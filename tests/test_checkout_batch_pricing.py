"""Unit tests for renewal/checkout batch fee resolution helpers."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controllers.payment_controller import (
    _batch_identity,
    _fee_from_batch_for_keys,
    _resolve_branch_batch,
)


ONE_MONTH = "47c25d66-5fb5-4017-8681-48b986f0ebb0"


def test_batch_identity_prefers_batch_id():
    assert _batch_identity({"batch_id": "batch-1", "id": "other"}, 0) == "batch-1"
    assert _batch_identity({}, 2) == "__index:2__"


def test_fee_from_batch_uses_admin_fee_per_duration_not_legacy_batch_fee():
    batch = {
        "batch_id": "batch-regular",
        "batch_fee": 1500,
        "fee_per_duration": {ONE_MONTH: 2000},
    }
    assert _fee_from_batch_for_keys(batch, [ONE_MONTH]) == 2000.0


def test_fee_from_batch_falls_back_to_legacy_batch_fee():
    batch = {"batch_id": "legacy", "batch_fee": 3000}
    assert _fee_from_batch_for_keys(batch, [ONE_MONTH]) == 3000.0


def test_resolve_branch_batch_matches_id():
    batches = [{"batch_id": "batch-a"}, {"id": "batch-b"}]
    assert _resolve_branch_batch(batches, "batch-b")["id"] == "batch-b"
    assert _resolve_branch_batch(batches, "") is None


if __name__ == "__main__":
    test_batch_identity_prefers_batch_id()
    test_fee_from_batch_uses_admin_fee_per_duration_not_legacy_batch_fee()
    test_fee_from_batch_falls_back_to_legacy_batch_fee()
    test_resolve_branch_batch_matches_id()
    print("ok")
