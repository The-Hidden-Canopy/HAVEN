"""Allow-list regression tests for the external world projection."""

from haven.external_agents.projections import WorldReadProjection


def test_world_projection_keeps_only_observational_fields():
    state = {
        "revision": 184,
        "observed_at": "2026-09-26T12:00:00+00:00",
        "rooms": [
            {
                "id": "office",
                "name": "Office",
                "devices": [
                    {
                        "id": "lamp",
                        "role": "light",
                        "is_on": True,
                        "status": "fresh",
                        "confidence": 1.0,
                        "service": "turn_on",
                        "raw_state": {"secret": "drop-me"},
                    }
                ],
                "people": ["Gerron"],
                "camera": {"id": "office_cam", "online": True, "stream_url": "drop-me"},
                "internal_note": "drop-me",
            }
        ],
        "people": [{"id": "gerron", "name": "Gerron", "room": "office", "email": "drop-me"}],
        "contexts": [{"id": "quiet", "label": "Quiet", "active": True, "secret": "drop-me"}],
        "memory": [{"content": "drop-me"}],
        "activity": [{"summary": "drop-me"}],
        "conversation": [{"text": "drop-me"}],
        "automations": [{"rule_id": "drop-me"}],
        "system": {"providers": ["drop-me"]},
        "voice": {"state": "drop-me"},
    }

    projection = WorldReadProjection.from_state(state).to_dict()

    assert set(projection) == {
        "revision",
        "observed_at",
        "rooms",
        "presence",
        "contexts",
        "devices",
        "freshness",
    }
    assert projection["rooms"] == [
        {
            "id": "office",
            "name": "Office",
            "devices": [
                {
                    "id": "lamp",
                    "role": "light",
                    "is_on": True,
                    "status": "fresh",
                    "confidence": 1.0,
                }
            ],
            "people": ["Gerron"],
            "camera": {"id": "office_cam", "online": True},
        }
    ]
    assert projection["presence"] == [{"id": "gerron", "name": "Gerron", "room": "office"}]
    assert projection["contexts"] == [{"id": "quiet", "label": "Quiet", "active": True}]
    assert projection["devices"] == [
        {"id": "lamp", "role": "light", "is_on": True, "status": "fresh", "confidence": 1.0}
    ]


def test_new_internal_state_fields_stay_out_by_default():
    projection = WorldReadProjection.from_state(
        {
            "rooms": [],
            "future_internal_field": {"secret": "drop-me"},
        }
    ).to_dict()

    assert set(projection) == {
        "revision",
        "observed_at",
        "rooms",
        "presence",
        "contexts",
        "devices",
        "freshness",
    }
