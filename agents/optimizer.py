"""
agents/optimizer.py — Optimizer Agent (activated on-demand at 14:00 if P&L < $10)

Responsibilities:
  1. Activated ONLY when Tracker publishes low P&L alert
  2. Analyze why P&L is below target (by niche)
  3. Use LLM to generate specific pivot strategy
  4. Publish pivot actions to Executor
  5. Track post-pivot performance

Decision logic:
  - P&L < $5 (critical): emergency — flash sale + Mturk boost
  - P&L $5-$8 (moderate): targeted — optimize weakest niche
  - P&L $8-$10 (light): gentle — 5 more affiliate posts
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.constants import (
    CHANNEL_TRACKER_TO_OPTIMIZER,
    CHANNEL_OPTIMIZER_TO_EXECUTOR,
    OPTIMIZER_CRITICAL_THRESHOLD,
    OPTIMIZER_MODERATE_THRESHOLD,
    RESELLING_FLASH_SALE_DISCOUNT,
    MTURK_PIVOT_ADDITIONAL_HITS,
    AFFILIATE_PIVOT_EXTRA_POSTS,
)
from config.settings import SYSTEM

logger = logging.getLogger(__name__)


class OptimizerAgent(BaseAgent):
    """
    Emergency pivot strategist. Only runs when P&L < target at 14:00.
    """

    def __init__(self):
        super().__init__("optimizer")

    def run(self) -> Optional[Dict[str, Any]]:
        """
        Check for Tracker alert and generate pivot plan if needed.
        Returns optimization_plan or None if no alert.
        """
        from utils.message_queue import get_message

        alert = get_message(CHANNEL_TRACKER_TO_OPTIMIZER, timeout=2.0)
        if not alert:
            self.logger.debug("No optimizer alert in queue — system on track")
            return None

        return self.generate_pivot(alert)

    # ──────────────────────────────────────────
    # Pivot generation
    # ──────────────────────────────────────────

    def generate_pivot(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze the P&L situation and generate a concrete pivot plan.

        Args:
            alert: Alert dict from Tracker with current_pnl and gap

        Returns:
            optimization_plan dict
        """
        from utils.database import get_today_transactions, resolve_alert
        from utils.message_queue import publish

        current_pnl = alert.get("current_pnl", {})
        gap = float(alert.get("gap", 0))
        alert_id = alert.get("alert_id")
        severity = alert.get("severity", "medium")

        profit = current_pnl.get("profit", 0.0)
        revenue_by_niche = current_pnl.get("revenue_by_niche", {})

        self.logger.warning(
            "=== OPTIMIZER ACTIVATED === profit=%.2f gap=%.2f severity=%s",
            profit, gap, severity,
        )

        # Get transaction details for analysis
        transactions = get_today_transactions()
        niche_analysis = self._analyze_niches(transactions, revenue_by_niche)

        # Try LLM pivot first, then rule-based
        actions = self._generate_pivot_with_llm(
            profit=profit,
            gap=gap,
            niche_analysis=niche_analysis,
            revenue_by_niche=revenue_by_niche,
        )

        if not actions:
            actions = self._generate_pivot_rule_based(profit, gap, niche_analysis)

        expected_additional = self._estimate_additional_revenue(actions)
        confidence = min(0.9, expected_additional / max(gap, 0.01))

        optimization_plan = {
            "alert_time": alert.get("alert_time", datetime.utcnow().isoformat()),
            "current_pnl": profit,
            "gap": round(gap, 2),
            "severity": severity,
            "analysis": niche_analysis,
            "actions": actions,
            "expected_additional_revenue": round(expected_additional, 2),
            "confidence": round(confidence, 2),
            "generated_at": datetime.utcnow().isoformat(),
        }

        self.logger.info(
            "Pivot plan: %d actions, expected +$%.2f (confidence=%.0f%%)",
            len(actions), expected_additional, confidence * 100,
        )
        for action in actions:
            self.logger.info("  → %s", action)

        # Publish to Executor
        publish(CHANNEL_OPTIMIZER_TO_EXECUTOR, optimization_plan)
        self.logger.info("Pivot plan published to Executor")

        # Resolve the original alert
        if alert_id:
            try:
                resolve_alert(
                    alert_id,
                    action_taken=f"Optimizer generated {len(actions)} pivot actions, expected +${expected_additional:.2f}",
                )
            except Exception:
                pass

        # Save optimizer activation alert
        self.save_alert(
            alert_type="optimization_triggered",
            severity=severity,
            message=f"Optimizer activated: P&L=${profit:.2f}, gap=${gap:.2f}, {len(actions)} actions generated",
            action_taken=json.dumps(actions[:3]),
        )

        self.log_action("pivot_generated", extra={"gap": f"${gap:.2f}", "actions": len(actions)})
        return optimization_plan

    def _analyze_niches(
        self,
        transactions: List[Dict],
        revenue_by_niche: Dict[str, float],
    ) -> Dict[str, Any]:
        """Analyze which niches are underperforming and why."""
        analysis = {}

        # Count transactions and revenue by niche and status
        for niche in ["reselling", "gigs", "affiliate"]:
            niche_txns = [t for t in transactions if t.get("niche") == niche]
            paid_txns = [t for t in niche_txns if t.get("status") == "paid"]
            failed_txns = [t for t in niche_txns if t.get("status") == "failed"]
            active_txns = [t for t in niche_txns if t.get("status") in ("active", "pending")]

            revenue = float(revenue_by_niche.get(niche, 0))

            if len(niche_txns) == 0:
                status = "no_activity"
                diagnosis = "No transactions executed for this niche"
            elif len(failed_txns) > len(paid_txns):
                status = "high_failure_rate"
                diagnosis = f"{len(failed_txns)}/{len(niche_txns)} transactions failed"
            elif len(active_txns) > 0 and revenue == 0:
                status = "active_no_revenue"
                diagnosis = f"{len(active_txns)} active transactions but no revenue yet"
            elif revenue > 0:
                status = "performing"
                diagnosis = f"${revenue:.2f} earned from {len(paid_txns)} paid transactions"
            else:
                status = "underperforming"
                diagnosis = "Transactions executed but no revenue received"

            analysis[niche] = {
                "status": status,
                "diagnosis": diagnosis,
                "revenue": revenue,
                "total_transactions": len(niche_txns),
                "paid": len(paid_txns),
                "failed": len(failed_txns),
                "active": len(active_txns),
            }

        return analysis

    def _generate_pivot_with_llm(
        self,
        profit: float,
        gap: float,
        niche_analysis: Dict,
        revenue_by_niche: Dict,
    ) -> Optional[List[Dict]]:
        """Use LLM to generate specific pivot actions."""
        from utils.llm_client import invoke_json

        analysis_str = "\n".join([
            f"  {niche}: {data['diagnosis']} (status={data['status']}, revenue=${data['revenue']:.2f})"
            for niche, data in niche_analysis.items()
        ])

        prompt = f"""You are an emergency revenue optimizer for an autonomous income system.

SITUATION AT 14:00:
- Current P&L: ${profit:.2f}
- Target: ${SYSTEM.DAILY_TARGET:.2f}
- Gap to close: ${gap:.2f} needed in 5 hours
- Revenue by niche: {revenue_by_niche}

NICHE ANALYSIS:
{analysis_str}

AVAILABLE PIVOT ACTIONS:
1. flash_sale_reselling: Reduce product listing prices by 30% to trigger sales
2. list_more_products: List 3 more products from AliExpress
3. accept_mturk_hits: Accept N more Mturk HITs (each pays $0.10-$2.00)
4. post_quora_answers: Post N more affiliate Quora answers
5. post_reddit_comments: Post N more affiliate Reddit comments
6. reduce_fiverr_price: Reduce Fiverr gig prices to attract orders

TASK: Generate the minimum set of actions to earn ${gap:.2f} more by 20:00.
Consider: reselling = fastest revenue (same day), affiliate = slowest (1-2 days).

Return ONLY this JSON array (2-4 actions max):
[
  {{"action": "accept_mturk_hits", "count": 10, "expected_revenue": 2.50, "reason": "fastest revenue source"}},
  {{"action": "flash_sale_reselling", "discount": 0.30, "expected_revenue": 2.00, "reason": "incentivize immediate sales"}}
]"""

        result = invoke_json(prompt)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "actions" in result:
            return result["actions"]
        return None

    def _generate_pivot_rule_based(
        self,
        profit: float,
        gap: float,
        niche_analysis: Dict,
    ) -> List[Dict[str, Any]]:
        """
        Rule-based pivot decision tree.
        Used when LLM is unavailable.
        """
        actions = []

        # Determine worst-performing niche
        worst_niche = min(
            niche_analysis,
            key=lambda n: niche_analysis[n]["revenue"],
        )
        best_niche = max(
            niche_analysis,
            key=lambda n: niche_analysis[n]["revenue"],
        )

        if profit < OPTIMIZER_CRITICAL_THRESHOLD:
            # Emergency: throw everything at it
            self.logger.info("CRITICAL pivot: P&L=$%.2f — emergency measures", profit)
            actions = [
                {
                    "action": "flash_sale_reselling",
                    "discount": RESELLING_FLASH_SALE_DISCOUNT,
                    "expected_revenue": gap * 0.4,
                    "reason": "Emergency price reduction to trigger immediate sales",
                },
                {
                    "action": "accept_mturk_hits",
                    "count": MTURK_PIVOT_ADDITIONAL_HITS,
                    "expected_revenue": gap * 0.4,
                    "reason": "Mturk HITs = reliable same-day income",
                },
                {
                    "action": "post_quora_answers",
                    "count": AFFILIATE_PIVOT_EXTRA_POSTS,
                    "expected_revenue": gap * 0.15,
                    "reason": "Affiliate posts = passive additional income",
                },
            ]

        elif profit < OPTIMIZER_MODERATE_THRESHOLD:
            # Moderate: focus on fastest niches
            self.logger.info("MODERATE pivot: P&L=$%.2f — targeted boost", profit)
            if niche_analysis.get("reselling", {}).get("status") == "active_no_revenue":
                actions.append({
                    "action": "flash_sale_reselling",
                    "discount": 0.20,
                    "expected_revenue": gap * 0.5,
                    "reason": f"Reselling listings active but no sales — reduce price to convert",
                })
            actions.append({
                "action": "accept_mturk_hits",
                "count": 8,
                "expected_revenue": gap * 0.4,
                "reason": "Supplement with reliable Mturk income",
            })
            actions.append({
                "action": "list_more_products",
                "count": 3,
                "expected_revenue": gap * 0.2,
                "reason": "More listings = more opportunities",
            })

        else:
            # Light touch: just need a small boost
            self.logger.info("LIGHT pivot: P&L=$%.2f — small boost needed", profit)
            actions = [
                {
                    "action": "accept_mturk_hits",
                    "count": 5,
                    "expected_revenue": gap * 0.5,
                    "reason": "Small Mturk boost to close the gap",
                },
                {
                    "action": "post_quora_answers",
                    "count": 3,
                    "expected_revenue": gap * 0.3,
                    "reason": "Quick affiliate posts",
                },
            ]

        return actions

    def _estimate_additional_revenue(self, actions: List[Dict]) -> float:
        """Sum up expected_revenue from all pivot actions."""
        total = 0.0
        for action in actions:
            total += float(action.get("expected_revenue", 0))
        return total
