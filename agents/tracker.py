"""
agents/tracker.py — Tracker Agent (every 15 min, 07:00-20:00)

Responsibilities:
  1. Poll all platform APIs for revenue updates every 15 min
  2. Update transaction statuses (pending → paid)
  3. Recalculate daily P&L
  4. At 14:00: Check if P&L >= $10, trigger Optimizer if not
  5. At 20:00: Generate final daily report
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from config.constants import (
    CHANNEL_TRACKER_TO_OPTIMIZER,
    CHANNEL_EXECUTOR_TO_TRACKER,
    OPTIMIZER_TRIGGER_THRESHOLD,
    OPTIMIZER_CRITICAL_THRESHOLD,
    OPTIMIZER_MODERATE_THRESHOLD,
)
from config.settings import SYSTEM

logger = logging.getLogger(__name__)


class TrackerAgent(BaseAgent):
    """
    Real-time P&L monitor. Runs every 15 minutes.
    """

    def __init__(self):
        super().__init__("tracker")
        from utils.api_clients import ProductResellClient, GigClient, AffiliateClient
        self.resell_client = ProductResellClient()
        self.gig_client = GigClient()
        self.affiliate_client = AffiliateClient()

    def run(self) -> Dict[str, Any]:
        """Standard run() — called every 15 minutes by scheduler."""
        return self.update_pnl()

    # ──────────────────────────────────────────
    # Core monitoring
    # ──────────────────────────────────────────

    def update_pnl(self) -> Dict[str, Any]:
        """
        Poll all platforms, update transaction statuses, recalculate P&L.

        Returns:
            Current P&L snapshot dict
        """
        from utils.database import get_today_transactions, update_transaction_status, get_current_pnl

        self.logger.info("=== TRACKER POLLING ===")

        # Get all active transactions to check
        transactions = get_today_transactions()
        active_txns = [t for t in transactions if t.get("status") in ("active", "pending")]

        self.logger.info("Checking %d active transactions...", len(active_txns))

        revenue_updates = 0
        for txn in active_txns:
            result = self._check_transaction_revenue(txn)
            if result and result.get("revenue_received"):
                revenue_updates += 1

        # Recalculate P&L
        pnl = get_current_pnl()

        self.logger.info(
            "P&L Update: revenue=%.2f cost=%.2f profit=%.2f target=%.2f "
            "target_met=%s (updated %d transactions)",
            pnl["revenue_total"],
            pnl["cost_total"],
            pnl["profit"],
            pnl["target"],
            pnl["target_met"],
            revenue_updates,
        )

        if pnl.get("alert"):
            self.logger.warning("P&L ALERT: %s", pnl["alert"])

        return pnl

    def _check_transaction_revenue(self, txn: Dict[str, Any]) -> Optional[Dict]:
        """
        Check if a specific transaction has received payment.
        Returns update result or None.
        """
        from utils.database import update_transaction_status

        niche = txn.get("niche", "")
        platform = txn.get("platform", "")
        resource_id = txn.get("resource_id", "")
        txn_id = txn.get("id")

        try:
            if niche == "reselling" and platform == "facebook_marketplace" and resource_id:
                result = self.resell_client.check_facebook_orders(resource_id)
                revenue = result.get("data", {}).get("revenue", 0.0)
                if revenue > 0:
                    update_transaction_status(txn_id=txn_id, status="paid", revenue=revenue)
                    self.logger.info(
                        "TRANSACTION PAID | id=%d platform=%s revenue=%.2f",
                        txn_id, platform, revenue,
                    )
                    return {"revenue_received": True, "revenue": revenue}

            elif niche == "gigs" and platform == "mturk" and resource_id:
                result = self.gig_client.get_mturk_assignment_status(resource_id)
                data = result.get("data", {})
                status = data.get("status", "")
                reward = data.get("reward", 0.0)
                if status == "Approved":
                    update_transaction_status(txn_id=txn_id, status="paid", revenue=reward)
                    self.logger.info(
                        "TRANSACTION PAID | id=%d platform=mturk reward=%.2f",
                        txn_id, reward,
                    )
                    return {"revenue_received": True, "revenue": reward}

            elif niche == "gigs" and platform == "fiverr" and resource_id:
                result = self.gig_client.get_fiverr_orders(resource_id)
                revenue = result.get("data", {}).get("revenue", 0.0)
                if revenue > 0:
                    update_transaction_status(txn_id=txn_id, status="paid", revenue=revenue)
                    return {"revenue_received": True, "revenue": revenue}

            elif niche == "affiliate":
                # Affiliate revenue is click-based and estimated
                # Update clicks from platform if available
                pass

        except Exception as exc:
            self.logger.debug("_check_transaction_revenue error for txn %s: %s", txn_id, exc)

        return None

    # ──────────────────────────────────────────
    # 14:00 — Optimizer trigger check
    # ──────────────────────────────────────────

    def check_optimizer_trigger(self) -> bool:
        """
        Called at 14:00. Returns True if Optimizer was triggered.
        """
        from utils.database import get_current_pnl, insert_alert
        from utils.message_queue import publish

        pnl = get_current_pnl()
        profit = pnl.get("profit", 0.0)
        target = pnl.get("target", SYSTEM.DAILY_TARGET)

        self.logger.info(
            "14:00 CHECK | profit=%.2f target=%.2f gap=%.2f",
            profit, target, target - profit,
        )

        if profit >= target:
            self.logger.info("✅ Target REACHED ($%.2f >= $%.2f) — no optimizer needed", profit, target)
            return False

        gap = target - profit
        severity = "high" if profit < OPTIMIZER_CRITICAL_THRESHOLD else "medium"

        alert_message = (
            f"P&L at 14:00 = ${profit:.2f} (target: ${target:.2f}, gap: ${gap:.2f}). "
            f"Optimizer intervention required."
        )
        self.logger.warning("⚠️  %s", alert_message)

        # Save alert to DB
        alert_id = insert_alert(
            alert_type="low_pnl",
            severity=severity,
            message=alert_message,
            agent_triggered="tracker",
            action_taken="Triggering optimizer agent",
        )

        # Publish to Optimizer
        publish(CHANNEL_TRACKER_TO_OPTIMIZER, {
            "alert_id": alert_id,
            "alert_time": datetime.utcnow().isoformat(),
            "current_pnl": pnl,
            "gap": gap,
            "severity": severity,
        })
        self.logger.info("Optimizer alert published (alert_id=%s)", alert_id)
        return True

    # ──────────────────────────────────────────
    # 20:00 — Final daily report
    # ──────────────────────────────────────────

    def generate_daily_report(self) -> Dict[str, Any]:
        """
        Generate final daily report at 20:00.
        Saves to DB and returns report dict.
        """
        from utils.database import (
            get_current_pnl,
            get_today_transactions,
            upsert_daily_result,
        )

        # Final poll before closing
        self.update_pnl()

        pnl = get_current_pnl()
        transactions = get_today_transactions()

        # Analyze by niche
        niche_stats: Dict[str, Dict] = {
            "reselling": {"revenue": 0.0, "count": 0, "paid": 0},
            "gigs": {"revenue": 0.0, "count": 0, "paid": 0},
            "affiliate": {"revenue": 0.0, "count": 0, "paid": 0},
        }
        best_transaction = None
        max_revenue = 0.0

        for txn in transactions:
            niche = txn.get("niche", "")
            revenue = float(txn.get("revenue") or 0)
            status = txn.get("status", "")

            if niche in niche_stats:
                niche_stats[niche]["count"] += 1
                if status == "paid":
                    niche_stats[niche]["revenue"] += revenue
                    niche_stats[niche]["paid"] += 1

            if revenue > max_revenue:
                max_revenue = revenue
                best_transaction = txn

        # Determine successful/failed niches
        successful_niches = [n for n, s in niche_stats.items() if s["revenue"] > 0]
        failed_niches = [n for n, s in niche_stats.items() if s["revenue"] == 0 and s["count"] > 0]

        # Recommendation for tomorrow
        best_niche = max(niche_stats, key=lambda n: niche_stats[n]["revenue"])
        worst_niche = min(niche_stats, key=lambda n: niche_stats[n]["revenue"])

        report = {
            "date": str(date.today()),
            "revenue": pnl["revenue_total"],
            "cost": pnl["cost_total"],
            "profit": pnl["profit"],
            "target": pnl["target"],
            "target_met": pnl["target_met"],
            "transactions_count": len(transactions),
            "paid_transactions": len([t for t in transactions if t.get("status") == "paid"]),
            "revenue_by_niche": pnl["revenue_by_niche"],
            "niche_stats": niche_stats,
            "successful_niches": successful_niches,
            "failed_niches": failed_niches,
            "best_transaction": {
                "id": best_transaction.get("id") if best_transaction else None,
                "niche": best_transaction.get("niche") if best_transaction else None,
                "revenue": max_revenue,
            },
            "recommendation_tomorrow": (
                f"Scale {best_niche} (${niche_stats[best_niche]['revenue']:.2f} today). "
                f"Review {worst_niche} strategy."
            ),
        }

        # Save final P&L to daily_results table
        self.retry_with_backoff(
            upsert_daily_result,
            result_date=date.today(),
            revenue_total=pnl["revenue_total"],
            cost_total=pnl["cost_total"],
            revenue_by_niche=pnl["revenue_by_niche"],
            transactions_count=len(transactions),
            notes=f"EOD report. Target met: {pnl['target_met']}. Best niche: {best_niche}.",
            operation_name="save_daily_result",
        )

        self.logger.info(
            "=== DAILY REPORT ===\n"
            "  Date: %s\n"
            "  Revenue: $%.2f | Cost: $%.2f | Profit: $%.2f\n"
            "  Target: $%.2f | Target Met: %s\n"
            "  Transactions: %d total, %d paid\n"
            "  Successful niches: %s\n"
            "  Failed niches: %s\n"
            "  Best: %s ($%.2f)\n"
            "  Recommendation: %s",
            report["date"],
            report["revenue"], report["cost"], report["profit"],
            report["target"], report["target_met"],
            report["transactions_count"], report["paid_transactions"],
            report["successful_niches"],
            report["failed_niches"],
            report["best_transaction"],
            max_revenue,
            report["recommendation_tomorrow"],
        )

        self.log_action(
            "daily_report_generated",
            extra={
                "profit": f"${report['profit']:.2f}",
                "target_met": report["target_met"],
            },
        )
        return report

    def get_current_pnl(self) -> Dict[str, Any]:
        """Public helper for other agents to get current P&L."""
        from utils.database import get_current_pnl
        return get_current_pnl()
