"""The backend registry ships EMPTY; resolve returns None (never raises)
so the manager can turn a miss into the explicit BACKEND_MISSING state.
"""

from haven.models import BackendRegistry


def test_registry_ships_empty():
    assert BackendRegistry().registered() == ()
    assert BackendRegistry().resolve("onnx") is None


def test_register_and_resolve_a_loader():
    registry = BackendRegistry()
    sentinel = object()
    registry.register("fake", sentinel)
    assert registry.resolve("fake") is sentinel
    assert registry.resolve("other") is None
    assert registry.registered() == ("fake",)


def test_register_rejects_blank_names():
    registry = BackendRegistry()
    try:
        registry.register("  ", object())
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
