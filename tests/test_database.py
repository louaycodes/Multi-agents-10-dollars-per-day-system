"""
tests/test_database.py — Tests for database utility functions.

Uses mocked psycopg2 connections to test CRUD logic without a real DB.
"""

import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch, call, contextmanager


class TestDatabaseHelpers(unittest.TestCase):
    """Test database CRUD functions with mocked connections."""

    def _mock_cursor(self, fetchone_result=None, fetchall_result=None):
        """Create a mock cursor with configurable return values."""
        cursor = MagicMock()
        cursor.fetchone.return_value = fetchone_result
        cursor.fetchall.return_value = fetchall_result or []
        return cursor

    @patch("utils.database._pool")
    def test_get_yesterday_results_with_data(self, mock_pool):
        """Returns yesterday's data when it exists."""
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_cursor = self._mock_cursor(
            fetchone_result={
                "date": date.today(),
                "revenue_total": 8.0,
                "cost_total": 0.5,
                "profit": 7.5,
                "target": 10.0,
                "target_met": False,
                "revenue_by_niche": {"reselling": 5.0, "gigs": 2.0, "affiliate": 0.5},
                "transactions_count": 5,
                "notes": "",
            }
        )
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # Import after patching
        from utils.database import get_yesterday_results
        # We can't easily test the full flow without a real connection pool
        # so we test the fallback behavior
        with patch("utils.database.get_cursor") as mock_get_cursor:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = lambda s: mock_cursor
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_get_cursor.return_value = mock_ctx
            mock_cursor.fetchone.return_value = {
                "date": date.today(),
                "revenue_total": 8.0,
                "cost_total": 0.5,
                "profit": 7.5,
                "target": 10.0,
                "target_met": False,
                "revenue_by_niche": {"reselling": 5.0},
                "transactions_count": 5,
                "notes": "",
            }
            result = get_yesterday_results()
            self.assertEqual(result["revenue_total"], 8.0)
            self.assertIn("revenue_by_niche", result)

    @patch("utils.database.get_cursor")
    def test_get_yesterday_results_no_data(self, mock_get_cursor):
        """Returns baseline zeroed dict when no data exists."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cursor
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_cursor.return_value = mock_ctx

        from utils.database import get_yesterday_results
        result = get_yesterday_results()

        self.assertEqual(result["revenue_total"], 0.0)
        self.assertEqual(result["profit"], 0.0)
        self.assertIn("reselling", result["revenue_by_niche"])

    @patch("utils.database.get_cursor")
    def test_insert_transaction_returns_id(self, mock_get_cursor):
        """insert_transaction returns the new row ID."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 42}
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cursor
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_cursor.return_value = mock_ctx

        from utils.database import insert_transaction
        txn_id = insert_transaction(
            niche="reselling",
            platform="facebook_marketplace",
            agent_action="product_listed",
            resource_id="LISTING_123",
            cost=1.50,
            status="active",
        )
        self.assertEqual(txn_id, 42)

    @patch("utils.database.get_cursor")
    def test_get_current_pnl_calculation(self, mock_get_cursor):
        """P&L is calculated correctly from paid transactions."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"niche": "reselling", "status": "paid", "revenue": 5.0, "cost": 1.5, "date": "2024-01-15 10:00"},
            {"niche": "gigs", "status": "paid", "revenue": 3.0, "cost": 0.0, "date": "2024-01-15 11:00"},
            {"niche": "affiliate", "status": "active", "revenue": None, "cost": 0.0, "date": "2024-01-15 12:00"},
        ]
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cursor
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_cursor.return_value = mock_ctx

        from utils.database import get_current_pnl
        pnl = get_current_pnl()

        # Revenue from paid transactions only
        self.assertAlmostEqual(pnl["revenue_by_niche"]["reselling"], 5.0)
        self.assertAlmostEqual(pnl["revenue_by_niche"]["gigs"], 3.0)
        self.assertAlmostEqual(pnl["revenue_by_niche"]["affiliate"], 0.0)
        self.assertAlmostEqual(pnl["revenue_total"], 8.0)
        self.assertFalse(pnl["target_met"])  # 8 < 10

    @patch("utils.database.get_cursor")
    def test_upsert_daily_result(self, mock_get_cursor):
        """upsert_daily_result executes without error."""
        mock_cursor = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cursor
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_cursor.return_value = mock_ctx

        from utils.database import upsert_daily_result
        # Should not raise
        upsert_daily_result(
            result_date=date.today(),
            revenue_total=11.0,
            cost_total=1.0,
            revenue_by_niche={"reselling": 7.0, "gigs": 3.0, "affiliate": 1.0},
            transactions_count=12,
            notes="Good day",
        )
        mock_cursor.execute.assert_called_once()

    @patch("utils.database.get_cursor")
    def test_insert_alert(self, mock_get_cursor):
        """insert_alert saves and returns alert ID."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 7}
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cursor
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_get_cursor.return_value = mock_ctx

        from utils.database import insert_alert
        alert_id = insert_alert(
            alert_type="low_pnl",
            severity="high",
            message="P&L=$6, need $4 more",
            agent_triggered="tracker",
        )
        self.assertEqual(alert_id, 7)


class TestValidators(unittest.TestCase):
    """Test all validator functions."""

    def test_valid_daily_plan(self):
        from utils.validators import validate_daily_plan
        plan = {
            "target": 10.0,
            "allocations": {"reselling": 0.5, "gigs": 0.35, "affiliate": 0.15},
            "priority_niches": ["reselling", "gigs", "affiliate"],
            "strategy": "Scale reselling",
            "expected_revenue": 10.5,
        }
        valid, errors = validate_daily_plan(plan)
        self.assertTrue(valid, errors)

    def test_invalid_daily_plan_bad_sum(self):
        from utils.validators import validate_daily_plan
        plan = {
            "target": 10.0,
            "allocations": {"reselling": 0.5, "gigs": 0.5, "affiliate": 0.5},  # Sums to 1.5
            "priority_niches": ["reselling"],
        }
        valid, errors = validate_daily_plan(plan)
        self.assertFalse(valid)
        self.assertTrue(any("sum" in e.lower() for e in errors))

    def test_valid_opportunity(self):
        from utils.validators import validate_opportunity
        opp = {
            "niche": "reselling",
            "type": "product_listing",
            "title": "Anti-slip phone grip",
            "estimated_roi": 5.0,
            "feasibility_score": 8.0,
            "effort_hours": 0.5,
            "time_to_profit": 1.0,
            "competition": "low",
            "capital_required": 1.50,
            "risk_level": "low",
        }
        valid, errors = validate_opportunity(opp)
        self.assertTrue(valid, errors)

    def test_invalid_opportunity_bad_niche(self):
        from utils.validators import validate_opportunity
        opp = {
            "niche": "crypto_trading",  # Invalid
            "type": "product_listing",
            "title": "something",
            "estimated_roi": 5.0,
            "feasibility_score": 7.0,
        }
        valid, errors = validate_opportunity(opp)
        self.assertFalse(valid)

    def test_llm_json_extraction(self):
        from utils.validators import validate_llm_json
        raw = '```json\n{"allocations": {"reselling": 0.5}, "priority_niches": ["reselling"]}\n```'
        valid, parsed, errors = validate_llm_json(raw, ["allocations", "priority_niches"])
        self.assertTrue(valid)
        self.assertIsNotNone(parsed)
        self.assertIn("allocations", parsed)

    def test_llm_json_extraction_no_code_fence(self):
        from utils.validators import validate_llm_json
        raw = '{"allocations": {"gigs": 0.5}, "priority_niches": ["gigs"]}'
        valid, parsed, errors = validate_llm_json(raw, ["allocations"])
        self.assertTrue(valid)

    def test_llm_json_extraction_invalid(self):
        from utils.validators import validate_llm_json
        raw = "Sorry, I cannot help with that."
        valid, parsed, errors = validate_llm_json(raw, ["allocations"])
        self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()
