"""
agents/manager.py — Manager Agent (06:00 + 20:00 daily)

Responsibilities:
  1. 06:00 — Read yesterday's DB results
  2. Analyze niche performance vs target
  3. Use LLM to generate allocation strategy
  4. Save daily plan to DB + message queue
  5. Update performance_metrics table
  6. 20:00 — Daily close: store final recommendations

Fallback: Rule-based allocation if LLM unavailable.
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.constants import (
    CHANNEL_MANAGER_TO_ALL,
    DEFAULT_ALLOCATIONS,
    NICHE_MIN_ALLOCATION,
    NICHE_MAX_ALLOCATION,
    NICHE_NAMES,
)
from config.settings import SYSTEM

logger = logging.getLogger(__name__)


class ManagerAgent(BaseAgent):
    """
    Chief orchestrator. Generates daily plan at 06:00 and closes at 20:00.
    """

    def __init__(self):
        super().__init__("manager")

    def run(self) -> Dict[str, Any]:
        """Standard run() — used by scheduler at 06:00."""
        return self.generate_daily_plan()

    # ──────────────────────────────────────────
    # 06:00 — Morning planning
    # ──────────────────────────────────────────

    def generate_daily_plan(self) -> Dict[str, Any]:
        """
        Generate today's allocation plan based on yesterday's performance.
        Saves to DB and publishes to message queue.

        Returns:
            daily_plan dict
        """
        from utils.database import (
            get_yesterday_results,
            get_historical_performance,
            get_performance_metrics,
            save_daily_plan,
            upsert_performance_metrics,
        )
        from utils.message_queue import publish

        self.logger.info("Reading yesterday's results from DB...")
        yesterday = get_yesterday_results()
        history = get_historical_performance(days=7)
        perf_metrics = get_performance_metrics()

        self.logger.info(
            "Yesterday: revenue=%.2f cost=%.2f profit=%.2f niches=%s",
            yesterday.get("revenue_total", 0),
            yesterday.get("cost_total", 0),
            yesterday.get("profit", 0),
            yesterday.get("revenue_by_niche", {}),
        )

        # Try LLM-based plan first
        plan = self._generate_plan_with_llm(yesterday, history, perf_metrics)

        # Validate and fallback if needed
        from utils.validators import validate_daily_plan
        is_valid, errors = validate_daily_plan(plan)
        if not is_valid:
            self.logger.warning("LLM plan invalid (%s) — using rule-based fallback", errors)
            plan = self._generate_plan_rule_based(yesterday, perf_metrics)

        # Ensure target is set
        plan["target"] = SYSTEM.DAILY_TARGET
        plan["date"] = str(date.today())

        self.logger.info(
            "Daily plan: allocations=%s priority=%s expected=%.2f",
            plan.get("allocations"),
            plan.get("priority_niches"),
            plan.get("expected_revenue", SYSTEM.DAILY_TARGET),
        )

        # Save to DB
        self.retry_with_backoff(save_daily_plan, plan, operation_name="save_daily_plan")

        # Update performance metrics from history
        self._update_performance_metrics(history, perf_metrics)

        # Publish to message queue for all agents
        publish(CHANNEL_MANAGER_TO_ALL, plan)
        self.logger.info("Daily plan published to channel '%s'", CHANNEL_MANAGER_TO_ALL)

        self.log_action("daily_plan_generated", extra={"allocations": str(plan.get("allocations"))})
        return plan

    def _generate_plan_with_llm(
        self,
        yesterday: Dict,
        history: List[Dict],
        perf_metrics: List[Dict],
    ) -> Optional[Dict]:
        """Use LLM to generate a data-driven allocation plan."""
        from utils.llm_client import invoke_json

        # Build concise context for LLM
        niche_breakdown = yesterday.get("revenue_by_niche", {})
        history_summary = ""
        for row in history[:5]:
            nb = row.get("revenue_by_niche", {})
            history_summary += (
                f"  {row.get('date')}: profit=${row.get('profit', 0):.2f} "
                f"reselling=${nb.get('reselling', 0):.2f} "
                f"gigs=${nb.get('gigs', 0):.2f} "
                f"affiliate=${nb.get('affiliate', 0):.2f}\n"
            )

        prompt = f"""You are a revenue allocation manager for an autonomous AI system.

YESTERDAY'S RESULTS:
- Total revenue: ${yesterday.get('revenue_total', 0):.2f}
- Total cost: ${yesterday.get('cost_total', 0):.2f}
- Profit: ${yesterday.get('profit', 0):.2f}
- Target: ${SYSTEM.DAILY_TARGET:.2f}
- Target met: {yesterday.get('target_met', False)}
- Revenue by niche:
  - Reselling: ${niche_breakdown.get('reselling', 0):.2f}
  - Gigs (Mturk/Fiverr): ${niche_breakdown.get('gigs', 0):.2f}
  - Affiliate: ${niche_breakdown.get('affiliate', 0):.2f}

LAST 7 DAYS HISTORY:
{history_summary if history_summary else '  No history yet (Day 1)'}

THREE NICHES AVAILABLE:
1. reselling: List products from AliExpress on Facebook/Shopee. ROI 60-150%. Revenue arrives same day.
2. gigs: Mturk HITs + Fiverr gig management. Consistent $0.25-$2 per task. Revenue same day.
3. affiliate: Post answers on Reddit/Quora with affiliate links. Revenue 1-3 days delay.

CONSTRAINTS:
- Each niche allocation must be between {NICHE_MIN_ALLOCATION} and {NICHE_MAX_ALLOCATION}
- Allocations must sum to exactly 1.0
- Prioritize niches with proven revenue history
- If Day 1 (no history), use: reselling=0.50, gigs=0.35, affiliate=0.15

TASK: Decide today's resource allocation. Return ONLY this JSON:
{{
  "allocations": {{"reselling": 0.50, "gigs": 0.35, "affiliate": 0.15}},
  "priority_niches": ["reselling", "gigs", "affiliate"],
  "strategy": "one sentence explaining rationale",
  "expected_revenue": 10.5,
  "llm_reasoning": "detailed analysis in 2-3 sentences"
}}"""

        result = invoke_json(prompt, expected_keys=["allocations", "priority_niches"])
        return result

    def _generate_plan_rule_based(
        self,
        yesterday: Dict,
        perf_metrics: List[Dict],
    ) -> Dict[str, Any]:
        """Rule-based fallback: allocate proportionally to 7d average revenue."""
        from utils.llm_client import generate_manager_plan_fallback
        return generate_manager_plan_fallback(yesterday, perf_metrics)

    def _update_performance_metrics(
        self,
        history: List[Dict],
        current_metrics: List[Dict],
    ) -> None:
        """Recalculate and update rolling performance metrics for each niche."""
        from utils.database import upsert_performance_metrics

        for niche in NICHE_NAMES:
            revenues = [
                float(row.get("revenue_by_niche", {}).get(niche, 0))
                for row in history
            ]
            avg_7d = sum(revenues) / len(revenues) if revenues else 0.0
            successes = sum(1 for r in revenues if r > 0)
            success_rate = successes / len(revenues) if revenues else 0.0

            # Find best performer label from existing metrics
            best_performer = next(
                (m.get("best_performer", "") for m in current_metrics if m.get("niche") == niche),
                "",
            )

            self.retry_with_backoff(
                upsert_performance_metrics,
                niche=niche,
                avg_revenue_7d=avg_7d,
                avg_revenue_30d=avg_7d,  # Approximate with 7d until we have 30d of data
                success_rate_7d=success_rate,
                total_revenue=sum(revenues),
                best_performer=best_performer,
                recommendation=f"7d avg: ${avg_7d:.2f}, success rate: {success_rate:.0%}",
                operation_name=f"update_metrics_{niche}",
            )

    # ──────────────────────────────────────────
    # 20:00 — Evening close
    # ──────────────────────────────────────────

    def daily_close(self) -> Dict[str, Any]:
        """
        Run at 20:00 — analyze today's final results and prepare recommendations
        for tomorrow's Manager run.
        """
        from utils.database import get_current_pnl, upsert_daily_result
        from utils.llm_client import invoke_json

        pnl = get_current_pnl()
        self.logger.info(
            "Daily Close: revenue=%.2f cost=%.2f profit=%.2f target_met=%s",
            pnl["revenue_total"],
            pnl["cost_total"],
            pnl["profit"],
            pnl["target_met"],
        )

        # Generate end-of-day analysis with LLM
        prompt = f"""Analyze today's performance and generate tomorrow's recommendations.

TODAY'S RESULTS:
- Revenue: ${pnl['revenue_total']:.2f}
- Cost: ${pnl['cost_total']:.2f}
- Profit: ${pnl['profit']:.2f}
- Target: ${pnl['target']:.2f}
- Target met: {pnl['target_met']}
- By niche: {pnl['revenue_by_niche']}

Return ONLY this JSON:
{{
  "analysis": "what worked and what didn't in 2 sentences",
  "tomorrow_strategy": "specific recommendation for tomorrow",
  "scale_niche": "which niche to scale up",
  "reduce_niche": "which niche to reduce or skip"
}}"""

        analysis = invoke_json(prompt, expected_keys=["analysis", "tomorrow_strategy"])
        notes = json.dumps(analysis) if analysis else "LLM analysis unavailable"

        # Save final daily result
        self.retry_with_backoff(
            upsert_daily_result,
            result_date=date.today(),
            revenue_total=pnl["revenue_total"],
            cost_total=pnl["cost_total"],
            revenue_by_niche=pnl["revenue_by_niche"],
            transactions_count=pnl["transactions_count"],
            notes=notes,
            target=pnl["target"],
            operation_name="save_daily_result",
        )

        self.log_action(
            "daily_close",
            extra={
                "profit": f"${pnl['profit']:.2f}",
                "target_met": pnl["target_met"],
            },
        )
        return {"pnl": pnl, "analysis": analysis}
