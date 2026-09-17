"""ModelDescriptor is the normalized internal contract; validation rules
include the endpoint-vs-file source split.
"""

import pytest

from haven.models import (
    ModelDescriptor,
    ModelKind,
    ModelSource,
    descriptor_from_dict,
    descriptor_to_dict,
)


def _descriptor(**overrides) -> ModelDescriptor:
    defaults = dict(
        id="m1",
        kind=ModelKind.INTELLIGENCE,
        capabilities=frozenset({"chat"}),
        backend="fake",
        architecture="stub",
        version="1.0.0",
        source=ModelSource.LOCAL,
        path=None,
        files={"weights": "weights.bin"},
    )
    defaults.update(overrides)
    return ModelDescriptor(**defaults)


def test_kind_and_source_enums_are_closed_and_stable():
    assert ModelKind("speech") is ModelKind.SPEECH
    assert ModelKind.SPECIALIZED.value == "specialized"
    assert ModelSource.DOWNLOADED.value == "downloaded"
    assert ModelSource.LOCAL.value == "local"
    assert ModelSource.ENDPOINT.value == "endpoint"


def test_accepts_enums_as_plain_strings_too():
    descriptor = _descriptor(kind="vision", source="downloaded")
    assert descriptor.kind is ModelKind.VISION
    assert descriptor.source is ModelSource.DOWNLOADED


def test_defaults_are_empty_collections_not_shared_mutables():
    first = _descriptor(files={"a": "a.bin"}, sha256={"a.bin": "x"})
    second = _descriptor(files={"a": "a.bin"})
    first.files["injected"] = "nope"
    assert second.files == {"a": "a.bin"}
    assert second.languages == frozenset()
    assert second.device_support == frozenset()
    assert second.sha256 == {}
    assert second.verified is False
    assert second.license is None


@pytest.mark.parametrize(
    "field",
    ["id", "version", "backend", "architecture"],
)
def test_required_text_fields_reject_empty(field):
    with pytest.raises(ValueError):
        _descriptor(**{field: "  "})


def test_capabilities_must_be_non_empty():
    with pytest.raises(ValueError):
        _descriptor(capabilities=frozenset())
    with pytest.raises(ValueError):
        _descriptor(capabilities=frozenset({"", " "}))


def test_file_sources_require_a_files_map():
    with pytest.raises(ValueError, match="files"):
        _descriptor(source=ModelSource.DOWNLOADED, files={})
    with pytest.raises(ValueError, match="files"):
        _descriptor(source=ModelSource.LOCAL, files={})


def test_endpoint_requires_an_http_url_path():
    with pytest.raises(ValueError, match="http"):
        _descriptor(source=ModelSource.ENDPOINT, path=None, files={})
    with pytest.raises(ValueError, match="http"):
        _descriptor(source=ModelSource.ENDPOINT, path="ftp://example", files={})
    descriptor = _descriptor(
        source=ModelSource.ENDPOINT, path="https://api.example.com/v1/", files={}
    )
    assert descriptor.files == {}


def test_endpoint_rejects_file_lists():
    with pytest.raises(ValueError, match="no files"):
        _descriptor(source=ModelSource.ENDPOINT, path="https://x.example/", files={"w": "w.bin"})


def test_supports_all_is_subset_matching():
    descriptor = _descriptor(capabilities=frozenset({"chat", "summarizer"}))
    assert descriptor.supports_all(frozenset({"chat"}))
    assert descriptor.supports_all(frozenset())
    assert not descriptor.supports_all(frozenset({"planner"}))


def test_descriptor_dict_round_trip():
    descriptor = _descriptor(
        kind=ModelKind.SPEECH,
        capabilities=frozenset({"asr", "vad"}),
        source=ModelSource.DOWNLOADED,
        path="/models/speech/m1",
        languages=frozenset({"en", "es"}),
        device_support=frozenset({"cpu", "cuda"}),
        license="MIT",
        verified=True,
        files={"weights": "w.bin"},
        sha256={"w.bin": "abc"},
    )
    rebuilt = descriptor_from_dict(descriptor_to_dict(descriptor))
    assert rebuilt == descriptor
