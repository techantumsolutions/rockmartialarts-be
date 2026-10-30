import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.cart_models import CartValidationIssue
from utils.cart_validation import cart_addition_blocking_issues


def test_existing_enrollment_issue_does_not_block_another_cart_addition():
    issues = [
        CartValidationIssue(
            code="already_enrolled",
            message="This student is already enrolled in this course.",
            item_id="existing-item",
        ),
        CartValidationIssue(
            code="already_enrolled",
            message="This student is already enrolled in this course.",
            item_id="new-item",
        ),
    ]

    blocking = cart_addition_blocking_issues(issues, {"existing-item"})

    assert [issue.item_id for issue in blocking] == ["new-item"]


def test_cart_addition_still_blocks_other_validation_errors():
    issues = [
        CartValidationIssue(
            code="student_ownership",
            message="You can only add enrollments for student profiles on your account.",
            item_id="existing-item",
        ),
        CartValidationIssue(
            code="fee_drift",
            message="Pricing was refreshed.",
            item_id="new-item",
        ),
    ]

    blocking = cart_addition_blocking_issues(issues, {"existing-item"})

    assert [issue.code for issue in blocking] == ["student_ownership"]


if __name__ == "__main__":
    test_existing_enrollment_issue_does_not_block_another_cart_addition()
    test_cart_addition_still_blocks_other_validation_errors()
    print("ok")
