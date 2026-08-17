"""
Unit tests for submitLeaveRequest.

These tests mock every AWS call (DynamoDB, Step Functions) so they run in
under a second with no real AWS access needed — this is what runs in the
GitHub Actions "Test" stage before every deploy. A failing test here blocks
the deploy stage entirely.
"""
import json
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "submitLeaveRequest"))


def make_mock_tables(balance_item=None, existing_items=None):
    balances_table = MagicMock()
    balances_table.get_item.return_value = {"Item": balance_item} if balance_item else {}
    requests_table = MagicMock()
    requests_table.query.return_value = {"Items": existing_items or []}
    requests_table.put_item.return_value = {}
    return requests_table, balances_table


def test_insufficient_balance_is_auto_rejected():
    import lambda_function as fn
    requests_table, balances_table = make_mock_tables(
        balance_item={"employee_id": "EMP001", "leave_type_year": "casual#2026",
                      "allocated": 10, "used": 8, "remaining": 2}
    )
    with patch.object(fn, "requests_table", requests_table), \
         patch.object(fn, "balances_table", balances_table), \
         patch.object(fn, "sfn") as mock_sfn:
        event = {"body": json.dumps({
            "employee_id": "EMP001", "leave_type": "casual",
            "start_date": "2026-09-01", "end_date": "2026-09-05",
            "reason": "CI test"
        })}
        result = fn.lambda_handler(event, None)
        body = json.loads(result["body"])
        assert result["statusCode"] == 400
        assert body["status"] == "AUTO_REJECTED"
        assert "Insufficient balance" in body["reason"]
        mock_sfn.start_execution.assert_not_called()


def test_valid_request_starts_step_functions_execution():
    import lambda_function as fn
    requests_table, balances_table = make_mock_tables(
        balance_item={"employee_id": "EMP001", "leave_type_year": "casual#2026",
                      "allocated": 10, "used": 2, "remaining": 8}
    )
    with patch.object(fn, "requests_table", requests_table), \
         patch.object(fn, "balances_table", balances_table), \
         patch.object(fn, "sfn") as mock_sfn:
        event = {"body": json.dumps({
            "employee_id": "EMP001", "leave_type": "casual",
            "start_date": "2026-09-01", "end_date": "2026-09-02",
            "reason": "CI test"
        })}
        result = fn.lambda_handler(event, None)
        body = json.loads(result["body"])
        assert result["statusCode"] == 200
        assert body["status"] == "PENDING"
        assert "request_id" in body
        mock_sfn.start_execution.assert_called_once()


def test_unpaid_leave_bypasses_balance_check():
    import lambda_function as fn
    requests_table, balances_table = make_mock_tables()
    with patch.object(fn, "requests_table", requests_table), \
         patch.object(fn, "balances_table", balances_table), \
         patch.object(fn, "sfn") as mock_sfn:
        event = {"body": json.dumps({
            "employee_id": "EMP001", "leave_type": "unpaid",
            "start_date": "2026-09-01", "end_date": "2026-09-01",
            "reason": "CI test"
        })}
        result = fn.lambda_handler(event, None)
        assert result["statusCode"] == 200
        balances_table.get_item.assert_not_called()
        mock_sfn.start_execution.assert_called_once()
