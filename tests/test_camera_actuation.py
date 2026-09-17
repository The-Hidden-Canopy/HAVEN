"""camera_to_device_manifest: a discovered camera becomes an actuatable device.

The last two tests run PTZ and privacy-shutter commands through the real
propose -> approve -> run_rule -> AuthorityEngine -> ExecutionProviderRegistry
path -- no camera-specific runtime code exists, because none is needed.
"""

from datetime import timedelta

import pytest

from haven.authority.policy import AuthorityEngine
from haven.cameras import CameraCapabilities, CameraManifest, camera_to_device_manifest
from haven.core.domain import ActionKind, ContextState, DeviceState, PresenceState, RuleDraft, WorldSnapshot
from haven.core.store import HavenStore
from haven.devices import ControlClass, DeviceRegistry
from haven.integrations.home_assistant import FixtureHomeAssistant
from haven.intelligence.gateway import ScriptedIntelligenceProvider
from haven.runtime import HavenRuntime
from test_vertical_slice import BASE_TIME, RoleTier, _principal


def _driveway_camera() -> CameraManifest:
    return CameraManifest(
        camera_id="driveway_cam",
        provider_id="onvif",
        room="driveway",
        capabilities=CameraCapabilities(live_stream=True, ptz=True, optical_zoom=True, privacy_shutter=True),
    )


def test_builds_a_capability_per_declared_hardware_feature():
    manifest = camera_to_device_manifest(
        _driveway_camera(),
        pan_tilt_service="onvif.ptz_move",
        zoom_service="onvif.zoom",
        privacy_shutter_service="onvif.set_privacy",
    )

    assert manifest.device_id == "driveway_cam"
    assert manifest.device_type == "camera"
    assert manifest.provider_id == "onvif"
    assert manifest.room == "driveway"
    assert manifest.capability_names == ("pan_tilt", "zoom", "privacy_shutter")
    assert manifest.capability("pan_tilt").control_class == ControlClass.LOW_RISK
    assert manifest.capability("privacy_shutter").control_class == ControlClass.GUARDED


def test_omits_capabilities_the_camera_does_not_declare():
    camera = CameraManifest(
        camera_id="bedroom_cam",
        provider_id="home_assistant",
        capabilities=CameraCapabilities(live_stream=True),  # no ptz/zoom/privacy_shutter
    )

    with pytest.raises(ValueError):
        camera_to_device_manifest(camera)


def test_declared_hardware_without_a_service_raises():
    with pytest.raises(ValueError):
        camera_to_device_manifest(_driveway_camera())  # ptz=True but no pan_tilt_service given


def _registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        camera_to_device_manifest(
            _driveway_camera(),
            pan_tilt_service="onvif.ptz_move",
            zoom_service="onvif.zoom",
            privacy_shutter_service="onvif.set_privacy",
        )
    )
    return registry


def _draft(principal, *, capability: str) -> RuleDraft:
    return RuleDraft(
        draft_id=f"draft-{capability}",
        household_id=principal.household_id,
        proposed_by=principal.actor_id,
        source_text=f"driveway camera {capability}",
        interpretation=f"Operate the driveway camera's {capability} when the resident is present.",
        trigger_person_id=principal.actor_id,
        trigger_room_id="driveway",
        action_kind=ActionKind.ACTIVATE_SCENE,
        target_device_id="driveway_cam",
        capability=capability,
    )


def _world(principal) -> WorldSnapshot:
    return WorldSnapshot(
        snapshot_id="camera-snapshot",
        household_id=principal.household_id,
        captured_at=BASE_TIME,
        valid_until=BASE_TIME + timedelta(minutes=10),
        presence=(
            PresenceState(
                person_id=principal.actor_id, room_id="driveway", present=True, observed_at=BASE_TIME, source="fixture"
            ),
        ),
        devices=(
            DeviceState(
                device_id="driveway_cam",
                kind="camera",
                room_id="driveway",
                is_on=True,
                brightness_pct=None,
                observed_at=BASE_TIME,
                source="fixture.onvif_state",
            ),
        ),
    )


def _approve(runtime, draft, *, resident, owner):
    rule = runtime.propose_draft(draft, principal=resident, now=BASE_TIME)
    runtime.approve_rule(
        rule.rule_id, principal=owner, justification="Owner approval for camera control.", now=BASE_TIME + timedelta(minutes=1)
    )
    return rule


def _runtime():
    resident = _principal()
    owner = _principal(actor_id="owner-1", role=RoleTier.OWNER)
    store = HavenStore(household_id=resident.household_id)
    adapter = FixtureHomeAssistant()
    runtime = HavenRuntime(
        store=store,
        intelligence_provider=ScriptedIntelligenceProvider(),
        home_assistant=adapter,
        authority=AuthorityEngine(device_registry=_registry()),
    )
    return runtime, store, adapter, resident, owner


def test_pan_tilt_is_low_risk_and_executes_immediately():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _draft(resident, capability="pan_tilt"), resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=_world(resident), justification="Pan the driveway camera left.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.outcome == "executed"
    assert adapter.commands[0].service == "onvif.ptz_move"


def test_privacy_shutter_is_guarded_and_requires_confirmation():
    runtime, store, adapter, resident, owner = _runtime()
    rule = _approve(runtime, _draft(resident, capability="privacy_shutter"), resident=resident, owner=owner)

    receipt = runtime.run_rule(
        rule.rule_id, principal=resident, world=_world(resident), justification="Disengage the privacy shutter.",
        now=BASE_TIME + timedelta(minutes=2),
    )

    assert receipt.decision.code.value == "confirmation_required"
    assert adapter.commands == ()
