"""
utils/database.py — PostgreSQL connection pool and all CRUD helpers.

Uses a thread-safe connection pool via psycopg2.pool.
All public functions are safe to call from multiple agent threads.
"""

import json
import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2 import pool

from config.settings import DB

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Connection Pool (singleton)
# ──────────────────────────────────────────────
_pool: Optional[pool.ThreadedConnectionPool] = None


def init_db() -> None:
    """Initialize the connection pool. Call once at startup."""
    global _pool
    if _pool is not None:
        return
    try:
        _pool = pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=DB.POOL_SIZE + DB.MAX_OVERFLOW,
            dsn=DB.dsn(),
        )
        logger.info("PostgreSQL connection pool initialized (min=2, max=%d)", DB.POOL_SIZE + DB.MAX_OVERFLOW)
    except psycopg2.OperationalError as exc:
        logger.critical("Cannot connect to PostgreSQL: %s", exc)
        raise


def close_db() -> None:
    """Close all connections in the pool. Call at system shutdown."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed.")


@contextmanager
def get_cursor(dict_cursor: bool = True):
    """
    Context manager providing a cursor with auto-commit/rollback.

    Usage:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
    """
    if _pool is None:
        init_db()
    conn = _pool.getconn()
    try:
        cursor_factory = psycopg2.extras.RealDictCursor if dict_cursor else None
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error("DB transaction rolled back: %s", exc)
        raise
    finally:
        _pool.putconn(conn)


# ──────────────────────────────────────────────
# daily_results CRUD
# ──────────────────────────────────────────────

def get_yesterday_results() -> Dict[str, Any]:
    """
    Returns yesterday's daily_results row as a dict.
    Returns a zeroed baseline dict if no row exists.
    """
    yesterday = date.today() - timedelta(days=1)
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT date, revenue_total, cost_total, profit, target,
                       target_met, revenue_by_niche, transactions_count, notes
                FROM daily_results
                WHERE date = %s
                """,
                (yesterday,),
            )
            row = cur.fetchone()
            if row:
                result = dict(row)
                result["revenue_by_niche"] = result.get("revenue_by_niche") or {}
                return result
    except Exception as exc:
        logger.error("get_yesterday_results error: %s", exc)

    # Baseline fallback
    return {
        "date": yesterday,
        "revenue_total": 0.0,
        "cost_total": 0.0,
        "profit": 0.0,
        "target": 10.0,
        "target_met": False,
        "revenue_by_niche": {"reselling": 0.0, "gigs": 0.0, "affiliate": 0.0},
        "transactions_count": 0,
        "notes": "No data for yesterday (baseline)",
    }


def get_historical_performance(days: int = 7) -> List[Dict[str, Any]]:
    """Returns last N days of daily_results rows, newest first."""
    since = date.today() - timedelta(days=days)
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT date, revenue_total, cost_total, profit, target_met,
                       revenue_by_niche
                FROM daily_results
                WHERE date >= %s
                ORDER BY date DESC
                """,
                (since,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_historical_performance error: %s", exc)
        return []


def upsert_daily_result(
    result_date: date,
    revenue_total: float,
    cost_total: float,
    revenue_by_niche: Dict[str, float],
    transactions_count: int,
    notes: str = "",
    target: float = 10.0,
) -> None:
    """Insert or update a daily_results row."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO daily_results
                    (date, revenue_total, cost_total, target, revenue_by_niche,
                     transactions_count, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (date) DO UPDATE SET
                    revenue_total = EXCLUDED.revenue_total,
                    cost_total = EXCLUDED.cost_total,
                    target = EXCLUDED.target,
                    revenue_by_niche = EXCLUDED.revenue_by_niche,
                    transactions_count = EXCLUDED.transactions_count,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
                """,
                (
                    result_date,
                    revenue_total,
                    cost_total,
                    target,
                    json.dumps(revenue_by_niche),
                    transactions_count,
                    notes,
                ),
            )
        logger.info("Upserted daily_result for %s: revenue=%.2f profit=%.2f",
                    result_date, revenue_total, revenue_total - cost_total)
    except Exception as exc:
        logger.error("upsert_daily_result error: %s", exc)
        raise


# ──────────────────────────────────────────────
# daily_plans CRUD
# ──────────────────────────────────────────────

def save_daily_plan(plan: Dict[str, Any]) -> None:
    """Persist Manager's daily plan."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO daily_plans (date, target, allocations, priority_niches, strategy_notes, llm_reasoning)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (date) DO UPDATE SET
                    allocations = EXCLUDED.allocations,
                    priority_niches = EXCLUDED.priority_niches,
                    strategy_notes = EXCLUDED.strategy_notes,
                    llm_reasoning = EXCLUDED.llm_reasoning
                """,
                (
                    date.today(),
                    plan.get("target", 10.0),
                    json.dumps(plan.get("allocations", {})),
                    json.dumps(plan.get("priority_niches", [])),
                    plan.get("strategy", ""),
                    plan.get("llm_reasoning", ""),
                ),
            )
        logger.info("Daily plan saved for %s", date.today())
    except Exception as exc:
        logger.error("save_daily_plan error: %s", exc)
        raise


def get_daily_plan(plan_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Retrieve today's (or specified date's) daily plan."""
    plan_date = plan_date or date.today()
    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM daily_plans WHERE date = %s", (plan_date,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("get_daily_plan error: %s", exc)
        return None


# ──────────────────────────────────────────────
# transactions CRUD
# ──────────────────────────────────────────────

def insert_transaction(
    niche: str,
    platform: str,
    agent_action: str,
    resource_id: str = "",
    revenue: Optional[float] = None,
    cost: float = 0.0,
    status: str = "pending",
    metadata: Optional[Dict] = None,
) -> int:
    """Insert a new transaction row. Returns the new transaction ID."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions
                    (niche, platform, agent_action, resource_id, revenue, cost,
                     status, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    niche,
                    platform,
                    agent_action,
                    resource_id,
                    revenue,
                    cost,
                    status,
                    json.dumps(metadata or {}),
                ),
            )
            txn_id = cur.fetchone()["id"]
        logger.info(
            "TRANSACTION INSERT | id=%d niche=%s platform=%s action=%s status=%s revenue=%s",
            txn_id, niche, platform, agent_action, status, revenue,
        )
        return txn_id
    except Exception as exc:
        logger.error("insert_transaction error: %s", exc)
        raise


def update_transaction_status(
    txn_id: int,
    status: str,
    revenue: Optional[float] = None,
    error_msg: Optional[str] = None,
    fulfillment_status: Optional[str] = None,
    affiliate_clicks: Optional[int] = None,
) -> None:
    """Update a transaction's status, revenue, and optional fields."""
    try:
        with get_cursor() as cur:
            fields = ["status = %s", "updated_at = NOW()"]
            values: list = [status]

            if revenue is not None:
                fields.append("revenue = %s")
                values.append(revenue)
                if status == "paid":
                    fields.append("revenue_received_at = NOW()")

            if error_msg is not None:
                fields.append("error_msg = %s")
                values.append(error_msg)

            if fulfillment_status is not None:
                fields.append("fulfillment_status = %s")
                values.append(fulfillment_status)

            if affiliate_clicks is not None:
                fields.append("affiliate_clicks = %s")
                values.append(affiliate_clicks)

            values.append(txn_id)
            cur.execute(
                f"UPDATE transactions SET {', '.join(fields)} WHERE id = %s",
                values,
            )
        logger.info("TRANSACTION UPDATE | id=%d status=%s revenue=%s", txn_id, status, revenue)
    except Exception as exc:
        logger.error("update_transaction_status error: %s", exc)
        raise


def get_today_transactions() -> List[Dict[str, Any]]:
    """Return all of today's transactions."""
    today = date.today()
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT * FROM transactions
                WHERE date::date = %s
                ORDER BY date DESC
                """,
                (today,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_today_transactions error: %s", exc)
        return []


def get_current_pnl() -> Dict[str, Any]:
    """
    Calculate real-time P&L from today's transactions.
    Returns a structured dict matching Tracker's output format.
    """
    transactions = get_today_transactions()
    revenue_by_niche: Dict[str, float] = {"reselling": 0.0, "gigs": 0.0, "affiliate": 0.0}
    cost_total = 0.0

    for txn in transactions:
        if txn.get("status") in ("paid", "completed") and txn.get("revenue"):
            niche = txn.get("niche", "gigs")
            revenue_by_niche[niche] = revenue_by_niche.get(niche, 0.0) + float(txn["revenue"])
        cost_total += float(txn.get("cost", 0.0))

    revenue_total = sum(revenue_by_niche.values())
    profit = revenue_total - cost_total

    from config.settings import SYSTEM
    target = SYSTEM.DAILY_TARGET

    return {
        "date": str(date.today()),
        "last_update": datetime.utcnow().isoformat(),
        "revenue_total": round(revenue_total, 2),
        "revenue_by_niche": {k: round(v, 2) for k, v in revenue_by_niche.items()},
        "cost_total": round(cost_total, 2),
        "profit": round(profit, 2),
        "target": target,
        "target_met": profit >= target,
        "transactions_count": len(transactions),
        "alert": None if profit >= target else f"P&L = ${profit:.2f}, ${target - profit:.2f} remaining",
    }


# ──────────────────────────────────────────────
# opportunities CRUD
# ──────────────────────────────────────────────

def insert_opportunity(opp: Dict[str, Any]) -> int:
    """Insert a scored opportunity from Hunter. Returns opportunity ID."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO opportunities
                    (date, niche, type, title, source_url, estimated_roi,
                     feasibility_score, effort_hours, time_to_profit,
                     competition_level, capital_required, risk_level, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    date.today(),
                    opp["niche"],
                    opp["type"],
                    opp["title"],
                    opp.get("source_url", ""),
                    opp.get("estimated_roi", 0.0),
                    opp.get("feasibility_score", 5.0),
                    opp.get("effort_hours", 1.0),
                    opp.get("time_to_profit", 1.0),
                    opp.get("competition", "medium"),
                    opp.get("capital_required", 0.0),
                    opp.get("risk_level", "low"),
                    opp.get("notes", ""),
                ),
            )
            return cur.fetchone()["id"]
    except Exception as exc:
        logger.error("insert_opportunity error: %s", exc)
        raise


def get_today_opportunities() -> List[Dict[str, Any]]:
    """Return today's opportunities ordered by composite_score desc."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT * FROM opportunities
                WHERE date = %s AND executed = FALSE
                ORDER BY composite_score DESC
                """,
                (date.today(),),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_today_opportunities error: %s", exc)
        return []


def mark_opportunity_executed(opp_id: int, txn_id: int) -> None:
    """Mark an opportunity as executed, linking to its transaction."""
    try:
        with get_cursor() as cur:
            cur.execute(
                "UPDATE opportunities SET executed = TRUE, execution_txn_id = %s WHERE id = %s",
                (txn_id, opp_id),
            )
    except Exception as exc:
        logger.error("mark_opportunity_executed error: %s", exc)


# ──────────────────────────────────────────────
# alerts CRUD
# ──────────────────────────────────────────────

def insert_alert(
    alert_type: str,
    severity: str,
    message: str,
    agent_triggered: str,
    action_taken: Optional[str] = None,
) -> int:
    """Log a system alert. Returns alert ID."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (alert_type, severity, message, agent_triggered, action_taken)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (alert_type, severity, message, agent_triggered, action_taken),
            )
            alert_id = cur.fetchone()["id"]
        logger.warning("ALERT [%s/%s] %s — agent: %s", severity.upper(), alert_type, message, agent_triggered)
        return alert_id
    except Exception as exc:
        logger.error("insert_alert error: %s", exc)
        raise


def resolve_alert(alert_id: int, action_taken: str) -> None:
    """Mark an alert as resolved."""
    try:
        with get_cursor() as cur:
            cur.execute(
                "UPDATE alerts SET resolved = TRUE, resolved_at = NOW(), action_taken = %s WHERE id = %s",
                (action_taken, alert_id),
            )
    except Exception as exc:
        logger.error("resolve_alert error: %s", exc)


# ──────────────────────────────────────────────
# performance_metrics CRUD
# ──────────────────────────────────────────────

def get_performance_metrics() -> List[Dict[str, Any]]:
    """Return the latest performance_metrics rows for each niche."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (niche)
                    niche, avg_revenue_7d, avg_revenue_30d, success_rate_7d,
                    total_revenue, best_performer, recommendation, updated_at
                FROM performance_metrics
                ORDER BY niche, date DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_performance_metrics error: %s", exc)
        return []


def upsert_performance_metrics(
    niche: str,
    avg_revenue_7d: float,
    avg_revenue_30d: float,
    success_rate_7d: float,
    total_revenue: float,
    best_performer: str = "",
    recommendation: str = "",
) -> None:
    """Insert or update performance metrics for a niche."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO performance_metrics
                    (date, niche, avg_revenue_7d, avg_revenue_30d, success_rate_7d,
                     total_revenue, best_performer, recommendation)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (date, niche) DO UPDATE SET
                    avg_revenue_7d = EXCLUDED.avg_revenue_7d,
                    avg_revenue_30d = EXCLUDED.avg_revenue_30d,
                    success_rate_7d = EXCLUDED.success_rate_7d,
                    total_revenue = EXCLUDED.total_revenue,
                    best_performer = EXCLUDED.best_performer,
                    recommendation = EXCLUDED.recommendation,
                    updated_at = NOW()
                """,
                (
                    date.today(),
                    niche,
                    avg_revenue_7d,
                    avg_revenue_30d,
                    success_rate_7d,
                    total_revenue,
                    best_performer,
                    recommendation,
                ),
            )
        logger.info("Performance metrics updated for niche=%s 7d_avg=%.2f", niche, avg_revenue_7d)
    except Exception as exc:
        logger.error("upsert_performance_metrics error: %s", exc)
        raise
