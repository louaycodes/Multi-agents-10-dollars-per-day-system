"""
tests/test_agents.py — Unit tests for all 5 agents.

Mocks DB, LLM, and API clients to test agent logic in isolation.
"""

import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch, call


class TestManagerAgent(unittest.TestCase):

    def _make_yesterday(self, **overrides):
        base = {
            "date": date.today(),
            "revenue_total": 8.0,
            "cost_total": 0.5,
            "profit": 7.5,
            "target": 10.0,
            "target_met": False,
            "revenue_by_niche": {"reselling": 5.0, "gigs": 2.0, "affiliate": 0.5},
            "transactions_count": 8,
            "notes": "",
        }
        base.update(overrides)
        return base

    @patch("agents.manager.get_yesterday_results")
    @patch("agents.manager.get_historical_performance")
    @patch("agents.manager.get_performance_metrics")
    @patch("agents.manager.save_daily_plan")
    @patch("agents.manager.upsert_performance_metrics")
    @patch("agents.manager.publish")
    @patch("agents.manager.invoke_json")
    def test_generate_daily_plan_with_llm(
        self,
        mock_invoke,
        mock_publish,
        mock_upsert_perf,
        mock_save_plan,
        mock_get_perf,
        mock_get_hist,
        mock_get_yesterday,
    ):
        """Manager generates a valid plan using LLM output."""
        mock_get_yesterday.return_value = self._make_yesterday()
        mock_get_hist.return_value = []
        mock_get_perf.return_value = []
        mock_invoke.return_value = {
            "allocations": {"reselling": 0.60, "gigs": 0.30, "affiliate": 0.10},
            "priority_niches": ["reselling", "gigs", "affiliate"],
            "strategy": "Scale reselling which drove 67% of revenue",
            "expected_revenue": 11.0,
            "llm_reasoning": "Reselling dominated yesterday, scale it today.",
        }

        from agents.manager import ManagerAgent
        agent = ManagerAgent()
        plan = agent.generate_daily_plan()

        self.assertIn("allocations", plan)
        self.assertIn("priority_niches", plan)
        self.assertEqual(plan["target"], 10.0)
        self.assertAlmostEqual(
            sum(plan["allocations"].values()), 1.0, delta=0.05
        )
        mock_save_plan.assert_called_once()
        mock_publish.assert_called_once()

    @patch("agents.manager.get_yesterday_results")
    @patch("agents.manager.get_historical_performance")
    @patch("agents.manager.get_performance_metrics")
    @patch("agents.manager.save_daily_plan")
    @patch("agents.manager.upsert_performance_metrics")
    @patch("agents.manager.publish")
    @patch("agents.manager.invoke_json")
    def test_generate_daily_plan_fallback(
        self,
        mock_invoke,
        mock_publish,
        mock_upsert_perf,
        mock_save_plan,
        mock_get_perf,
        mock_get_hist,
        mock_get_yesterday,
    ):
        """Manager falls back to rule-based when LLM fails."""
        mock_get_yesterday.return_value = self._make_yesterday(revenue_total=0.0, profit=0.0)
        mock_get_hist.return_value = []
        mock_get_perf.return_value = []
        mock_invoke.return_value = None  # LLM fails

        from agents.manager import ManagerAgent
        agent = ManagerAgent()
        plan = agent.generate_daily_plan()

        self.assertIn("allocations", plan)
        alloc_sum = sum(plan["allocations"].values())
        self.assertAlmostEqual(alloc_sum, 1.0, delta=0.05)


class TestHunterAgent(unittest.TestCase):

    def test_opportunity_scoring(self):
        """Opportunities are scored and ranked correctly."""
        from agents.hunter import HunterAgent
        agent = HunterAgent.__new__(HunterAgent)  # Skip __init__

        opps = [
            {"estimated_roi": 5.0, "feasibility_score": 8.0, "time_to_profit": 1.0, "effort_hours": 1.0},
            {"estimated_roi": 2.0, "feasibility_score": 9.0, "time_to_profit": 1.0, "effort_hours": 0.5},
            {"estimated_roi": 1.0, "feasibility_score": 6.0, "time_to_profit": 0.5, "effort_hours": 2.0},
        ]

        scored = agent._score_and_rank(opps)
        # First should have highest composite score
        self.assertGreater(scored[0]["composite_score"], scored[-1]["composite_score"])

    def test_filter_high_risk(self):
        """High-risk opportunities are filtered out."""
        from agents.hunter import HunterAgent
        agent = HunterAgent.__new__(HunterAgent)

        opps = [
            {"niche": "reselling", "risk_level": "low", "feasibility_score": 7.0, "estimated_roi": 3.0},
            {"niche": "gigs", "risk_level": "high", "feasibility_score": 9.0, "estimated_roi": 50.0},  # Should be filtered
            {"niche": "affiliate", "risk_level": "medium", "feasibility_score": 6.0, "estimated_roi": 2.0},
        ]

        filtered = agent._filter_opportunities(opps)
        self.assertEqual(len(filtered), 2)
        self.assertFalse(any(o.get("risk_level") == "high" for o in filtered))

    def test_filter_low_feasibility(self):
        """Low feasibility opportunities are filtered out."""
        from agents.hunter import HunterAgent
        agent = HunterAgent.__new__(HunterAgent)

        opps = [
            {"niche": "reselling", "risk_level": "low", "feasibility_score": 3.0, "estimated_roi": 5.0},
            {"niche": "gigs", "risk_level": "low", "feasibility_score": 7.0, "estimated_roi": 2.0},
        ]
        filtered = agent._filter_opportunities(opps)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["niche"], "gigs")

    def test_priority_boost(self):
        """Manager's high-allocation niche gets composite score boost."""
        from agents.hunter import HunterAgent
        agent = HunterAgent.__new__(HunterAgent)

        opps = [
            {"niche": "reselling", "composite_score": 10.0},
            {"niche": "gigs", "composite_score": 10.0},
            {"niche": "affiliate", "composite_score": 10.0},
        ]
        allocations = {"reselling": 0.60, "gigs": 0.30, "affiliate": 0.10}
        boosted = agent._apply_priority_boost(opps, allocations)

        reselling_score = next(o["composite_score"] for o in boosted if o["niche"] == "reselling")
        affiliate_score = next(o["composite_score"] for o in boosted if o["niche"] == "affiliate")
        self.assertGreater(reselling_score, affiliate_score)


class TestTrackerAgent(unittest.TestCase):

    @patch("agents.tracker.get_current_pnl")
    @patch("agents.tracker.insert_alert")
    @patch("agents.tracker.publish")
    def test_optimizer_triggered_when_below_target(
        self, mock_publish, mock_alert, mock_pnl
    ):
        """Optimizer is triggered when P&L < target at 14:00."""
        mock_pnl.return_value = {
            "profit": 6.5,
            "target": 10.0,
            "target_met": False,
            "revenue_total": 6.5,
            "cost_total": 0.0,
            "revenue_by_niche": {},
        }
        mock_alert.return_value = 42

        from agents.tracker import TrackerAgent
        agent = TrackerAgent.__new__(TrackerAgent)
        agent.name = "tracker"
        agent.logger = __import__("logging").getLogger("test")

        with patch.object(agent, "retry_with_backoff", side_effect=lambda f, *a, **kw: f(*a, **kw)):
            with patch.object(agent, "save_alert"):
                triggered = agent.check_optimizer_trigger()

        self.assertTrue(triggered)
        mock_publish.assert_called_once()

    @patch("agents.tracker.get_current_pnl")
    def test_optimizer_not_triggered_when_target_met(self, mock_pnl):
        """Optimizer is NOT triggered when P&L >= target."""
        mock_pnl.return_value = {
            "profit": 11.0,
            "target": 10.0,
            "target_met": True,
            "revenue_total": 11.0,
            "cost_total": 0.0,
            "revenue_by_niche": {},
        }
        from agents.tracker import TrackerAgent
        agent = TrackerAgent.__new__(TrackerAgent)
        agent.name = "tracker"
        agent.logger = __import__("logging").getLogger("test")

        triggered = agent.check_optimizer_trigger()
        self.assertFalse(triggered)


class TestOptimizerAgent(unittest.TestCase):

    def _make_analysis(self, reselling_revenue=3.0, gigs_revenue=2.0, affiliate_revenue=0.0):
        return {
            "reselling": {
                "status": "performing" if reselling_revenue > 0 else "active_no_revenue",
                "revenue": reselling_revenue,
                "diagnosis": "earning",
                "total_transactions": 3,
                "paid": 2,
                "failed": 0,
                "active": 1,
            },
            "gigs": {
                "status": "performing" if gigs_revenue > 0 else "active_no_revenue",
                "revenue": gigs_revenue,
                "diagnosis": "earning",
                "total_transactions": 5,
                "paid": 3,
                "failed": 1,
                "active": 1,
            },
            "affiliate": {
                "status": "active_no_revenue" if affiliate_revenue == 0 else "performing",
                "revenue": affiliate_revenue,
                "diagnosis": "no clicks yet",
                "total_transactions": 2,
                "paid": 0,
                "failed": 0,
                "active": 2,
            },
        }

    def test_critical_pivot_includes_flash_sale_and_mturk(self):
        """Critical situation (P&L < $5) triggers aggressive pivot."""
        from agents.optimizer import OptimizerAgent
        agent = OptimizerAgent.__new__(OptimizerAgent)
        agent.name = "optimizer"
        agent.logger = __import__("logging").getLogger("test")

        actions = agent._generate_pivot_rule_based(
            profit=3.0,
            gap=7.0,
            niche_analysis=self._make_analysis(0, 0, 0),
        )
        action_names = [a["action"] for a in actions]
        self.assertIn("flash_sale_reselling", action_names)
        self.assertIn("accept_mturk_hits", action_names)

    def test_light_pivot_for_small_gap(self):
        """Small gap (P&L = $9) triggers light pivot only."""
        from agents.optimizer import OptimizerAgent
        agent = OptimizerAgent.__new__(OptimizerAgent)
        agent.name = "optimizer"
        agent.logger = __import__("logging").getLogger("test")

        actions = agent._generate_pivot_rule_based(
            profit=9.0,
            gap=1.0,
            niche_analysis=self._make_analysis(5.0, 3.0, 1.0),
        )
        # Should be smaller/gentler actions
        total_expected = sum(a.get("expected_revenue", 0) for a in actions)
        self.assertGreater(total_expected, 0)

    def test_estimate_additional_revenue(self):
        """Revenue estimation sums expected values."""
        from agents.optimizer import OptimizerAgent
        agent = OptimizerAgent.__new__(OptimizerAgent)
        agent.name = "optimizer"
        agent.logger = __import__("logging").getLogger("test")

        actions = [
            {"action": "accept_mturk_hits", "expected_revenue": 2.5},
            {"action": "flash_sale_reselling", "expected_revenue": 1.5},
        ]
        estimated = agent._estimate_additional_revenue(actions)
        self.assertAlmostEqual(estimated, 4.0)


class TestBaseAgent(unittest.TestCase):

    def test_retry_with_backoff_succeeds(self):
        """retry_with_backoff returns result on success."""
        from agents.base_agent import BaseAgent

        class ConcreteAgent(BaseAgent):
            def run(self):
                return "ok"

        agent = ConcreteAgent("test")
        call_count = 0

        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("fail")
            return "success"

        with patch("agents.base_agent.time.sleep"):
            result = agent.retry_with_backoff(flaky_fn, max_retries=5)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)

    def test_retry_with_backoff_exhausted(self):
        """retry_with_backoff returns None when all retries fail."""
        from agents.base_agent import BaseAgent

        class ConcreteAgent(BaseAgent):
            def run(self):
                return None

        agent = ConcreteAgent("test")

        def always_fail():
            raise RuntimeError("always fails")

        with patch("agents.base_agent.time.sleep"):
            result = agent.retry_with_backoff(always_fail, max_retries=3)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
