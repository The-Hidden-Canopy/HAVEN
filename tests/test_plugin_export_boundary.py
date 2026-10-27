import json

import pytest

from haven.plugins.exports import authorized_receipt_projection
from test_unified_receipt import _device_receipt


def test_raw_private_receipt_and_enable_flag_are_not_export_consent():
    receipt = _device_receipt()
    with pytest.raises(PermissionError, match="explicit current export"):
        authorized_receipt_projection(receipt, principal_id="ada", scope_id="household-1",
                                      consumer_id="consumer", authorize=lambda *args: False)
    with pytest.raises(ValueError, match="raw dictionaries"):
        authorized_receipt_projection(receipt.to_dict(), principal_id="ada", scope_id="household-1",
                                      consumer_id="consumer", authorize=lambda *args: True)


def test_authorized_projection_is_allowlisted_and_contains_no_private_receipt_fields():
    receipt = _device_receipt()
    result = authorized_receipt_projection(receipt, principal_id="ada", scope_id="household-1",
                                          consumer_id="consumer", authorize=lambda *args: True)
    assert set(result) == {"schema", "outcome", "authority_status", "execution_attempted", "execution_success"}
    encoded = json.dumps(result)
    for private in ("household-1", "ada", "office", "brightness", "justification", "request-1", "event-1"):
        assert private not in encoded


def test_scope_mismatch_and_export_revocation_fail_each_call():
    allowed = {"current": True}
    receipt = _device_receipt()
    kwargs = dict(principal_id="ada", scope_id="household-1", consumer_id="consumer",
                  authorize=lambda *args: allowed["current"])
    assert authorized_receipt_projection(receipt, **kwargs)["outcome"] == "executed"
    allowed["current"] = False
    with pytest.raises(PermissionError):
        authorized_receipt_projection(receipt, **kwargs)
    with pytest.raises(PermissionError, match="outside"):
        authorized_receipt_projection(receipt, **dict(kwargs, scope_id="other"))
