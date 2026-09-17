"""The canonical result contract: ChatResult and InferenceResult validation.

Every backend normalizes to these before HAVEN sees output, so the
contract is strict: text must survive whitespace normalization non-empty,
model_id is required, and the optional vendor extras (usage, raw) are
carried verbatim for audit, never reinterpreted.
"""

import pytest

from haven.models import ChatResult, InferenceResult
from haven.models.results import ChatResult as DirectChatResult
from haven.models.results import InferenceResult as DirectInferenceResult


def test_chat_result_normalizes_surrounding_whitespace():
    result = ChatResult(text="  hello there  ", model_id="model-a")
    assert result.text == "hello there"
    assert result.model_id == "model-a"
    assert result.usage is None
    assert result.raw is None


def test_chat_result_rejects_blank_text():
    with pytest.raises(ValueError, match="non-empty"):
        ChatResult(text="   ", model_id="model-a")


def test_chat_result_rejects_non_string_text():
    with pytest.raises(ValueError, match="must be a string"):
        ChatResult(text=None, model_id="model-a")  # type: ignore[arg-type]


def test_chat_result_rejects_blank_model_id():
    with pytest.raises(ValueError, match="model_id"):
        ChatResult(text="hello", model_id="  ")


def test_chat_result_rejects_non_dict_usage_and_raw():
    with pytest.raises(ValueError, match="usage"):
        ChatResult(text="hello", model_id="m", usage=[1, 2])
    with pytest.raises(ValueError, match="raw"):
        ChatResult(text="hello", model_id="m", raw="vendor envelope")


def test_chat_result_is_frozen():
    result = ChatResult(text="hello", model_id="m")
    with pytest.raises(Exception):
        result.text = "changed"


def test_chat_result_to_dict_carries_everything_verbatim():
    usage = {"completion_tokens": 3}
    raw = {"choices": [{"message": {"content": "hello"}}]}
    result = ChatResult(text="hello", model_id="m", usage=usage, raw=raw)
    assert result.to_dict() == {
        "text": "hello",
        "model_id": "m",
        "usage": usage,
        "raw": raw,
    }


def test_inference_result_wraps_outputs():
    result = InferenceResult(outputs={"embeddings": [[0.1, 0.2]]}, model_id="encoder")
    assert result.outputs == {"embeddings": [[0.1, 0.2]]}
    assert result.model_id == "encoder"
    assert result.raw is None


def test_inference_result_rejects_non_dict_outputs():
    with pytest.raises(ValueError, match="outputs"):
        InferenceResult(outputs=[[0.1]], model_id="encoder")  # type: ignore[arg-type]


def test_inference_result_rejects_blank_model_id():
    with pytest.raises(ValueError, match="model_id"):
        InferenceResult(outputs={}, model_id="")


def test_inference_result_is_frozen():
    result = InferenceResult(outputs={}, model_id="m")
    with pytest.raises(Exception):
        result.outputs = {"other": 1}


def test_inference_result_to_dict_shape():
    raw = {"logits": [1.0]}
    result = InferenceResult(outputs={"label": "cat"}, model_id="vision", raw=raw)
    assert result.to_dict() == {
        "outputs": {"label": "cat"},
        "model_id": "vision",
        "raw": raw,
    }


def test_results_are_importable_from_the_reference_backend():
    # backends re-export the canonical contract so plugin authors import
    # one place; both paths must be the same objects.
    from haven.models.backends.reference import ChatResult as RefChat
    from haven.models.backends.reference import InferenceResult as RefInf

    assert RefChat is DirectChatResult
    assert RefInf is DirectInferenceResult
