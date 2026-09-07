"""
utils/validators.py — Input validation for all agent data structures.

Uses Pydantic v2 for schema validation.
All validators return (is_valid: bool, errors: list[str]).
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Simple field validators (no Pydantic dependency needed)
# ──────────────────────────────────────────────

def _check_required_keys(data: dict, required: list) -> List[str]:
    return [f"Missing required key: '{k}'" for k in required if k not in data]


def _check_type(data: dict, key: str, expected_type: type) -> List[str]:
    if key in data and not isinstance(data[key], expected_type):
        return [f"Key '{key}' must be {expected_type.__name__}, got {type(data[key]).__name__}"]
    return []


def _check_range(data: dict, key: str, min_val: float, max_val: float) -> List[str]:
    if key in data:
        try:
            val = float(data[key])
            if not (min_val <= val <= max_val):
                return [f"Key '{key}' = {val} is out of range [{min_val}, {max_val}]"]
        except (ValueError, TypeError):
            return [f"Key '{key}' is not numeric"]
    return []


def _check_enum(data: dict, key: str, allowed: list) -> List[str]:
    if key in data and data[key] not in allowed:
        return [f"Key '{key}' = '{data[key]}' not in allowed values {allowed}"]
    return []


# ──────────────────────────────────────────────
# Schema: Daily Plan (from Manager)
# ──────────────────────────────────────────────

VALID_NICHES = ["reselling", "gigs", "affiliate"]

def validate_daily_plan(plan: Any) -> Tuple[bool, List[str]]:
    """
    Validate the structure of a daily plan dict.

    Expected:
    {
        "date": "2024-01-15",
        "target": 10.0,
        "allocations": {"reselling": 0.5, "gigs": 0.35, "affiliate": 0.15},
        "priority_niches": ["reselling", "gigs", "affiliate"],
        "strategy": "...",
        "expected_revenue": 10.5
    }
    """
    errors = []

    if not isinstance(plan, dict):
        return False, ["Daily plan must be a dict"]

    errors += _check_required_keys(plan, ["target", "allocations", "priority_niches"])
    errors += _check_type(plan, "allocations", dict)
    errors += _check_type(plan, "priority_niches", list)

    if not errors:
        # Check allocations sum to ~1.0
        alloc = plan.get("allocations", {})
        alloc_sum = sum(float(v) for v in alloc.values())
        if not (0.95 <= alloc_sum <= 1.05):
            errors.append(f"Allocations sum to {alloc_sum:.2f}, expected ~1.0")

        # Check all niches valid
        for niche in plan.get("priority_niches", []):
            if niche not in VALID_NICHES:
                errors.append(f"Invalid niche in priority_niches: '{niche}'")

        # Check target is positive
        errors += _check_range(plan, "target", 0.01, 10000.0)

    return len(errors) == 0, errors


# ──────────────────────────────────────────────
# Schema: Opportunity (from Hunter)
# ──────────────────────────────────────────────

VALID_OPPORTUNITY_TYPES = [
    "product_listing", "mturk_hit", "quora_answer",
    "reddit_post", "devto_article", "fiverr_gig_update"
]
VALID_RISK_LEVELS = ["low", "medium", "high"]
VALID_COMPETITION = ["low", "medium", "high"]

def validate_opportunity(opp: Any) -> Tuple[bool, List[str]]:
    """
    Validate a single opportunity dict from Hunter.

    Expected:
    {
        "niche": "reselling",
        "type": "product_listing",
        "title": "Anti-slip phone grip",
        "estimated_roi": 5.0,
        "feasibility_score": 8.0,
        "effort_hours": 0.5,
        "time_to_profit": 1.0,
        "competition": "low",
        "capital_required": 1.50,
        "risk_level": "low"
    }
    """
    errors = []
    if not isinstance(opp, dict):
        return False, ["Opportunity must be a dict"]

    errors += _check_required_keys(opp, ["niche", "type", "title", "estimated_roi", "feasibility_score"])
    errors += _check_enum(opp, "niche", VALID_NICHES)
    errors += _check_enum(opp, "risk_level", VALID_RISK_LEVELS)
    errors += _check_enum(opp, "competition", VALID_COMPETITION)
    errors += _check_range(opp, "feasibility_score", 0.0, 10.0)
    errors += _check_range(opp, "time_to_profit", 0.0, 1.0)
    errors += _check_range(opp, "effort_hours", 0.0, 100.0)

    if not opp.get("title"):
        errors.append("Opportunity 'title' must not be empty")

    return len(errors) == 0, errors


def validate_opportunities_list(opps: Any) -> Tuple[bool, List[str]]:
    """Validate a list of opportunities."""
    if not isinstance(opps, list):
        return False, ["Expected a list of opportunities"]
    all_errors = []
    for i, opp in enumerate(opps):
        valid, errors = validate_opportunity(opp)
        if not valid:
            all_errors.extend([f"[opp {i}] {e}" for e in errors])
    return len(all_errors) == 0, all_errors


# ──────────────────────────────────────────────
# Schema: Transaction (from Executor)
# ──────────────────────────────────────────────

VALID_TRANSACTION_STATUSES = ["pending", "active", "completed", "paid", "failed"]
VALID_PLATFORMS = [
    "facebook_marketplace", "shopee", "mturk", "fiverr",
    "quora", "reddit", "devto", "manual", "mturk_mock"
]

def validate_transaction(txn: Any) -> Tuple[bool, List[str]]:
    """
    Validate a transaction dict before DB insert.

    Expected:
    {
        "niche": "reselling",
        "platform": "facebook_marketplace",
        "agent_action": "product_listed",
        "status": "active"
    }
    """
    errors = []
    if not isinstance(txn, dict):
        return False, ["Transaction must be a dict"]

    errors += _check_required_keys(txn, ["niche", "platform", "agent_action", "status"])
    errors += _check_enum(txn, "niche", VALID_NICHES)
    errors += _check_enum(txn, "status", VALID_TRANSACTION_STATUSES)

    return len(errors) == 0, errors


# ──────────────────────────────────────────────
# Schema: P&L snapshot (from Tracker)
# ──────────────────────────────────────────────

def validate_pnl_snapshot(pnl: Any) -> Tuple[bool, List[str]]:
    """Validate a real-time P&L snapshot dict."""
    errors = []
    if not isinstance(pnl, dict):
        return False, ["P&L snapshot must be a dict"]

    errors += _check_required_keys(pnl, ["revenue_total", "cost_total", "profit", "target"])
    errors += _check_range(pnl, "revenue_total", 0.0, 100000.0)
    errors += _check_range(pnl, "cost_total", 0.0, 100000.0)

    return len(errors) == 0, errors


# ──────────────────────────────────────────────
# Schema: Optimizer plan
# ──────────────────────────────────────────────

VALID_PIVOT_ACTIONS = [
    "flash_sale_reselling",
    "list_more_products",
    "reduce_fiverr_price",
    "accept_mturk_hits",
    "post_quora_answers",
    "post_reddit_comments",
    "publish_devto_article",
]

def validate_optimization_plan(plan: Any) -> Tuple[bool, List[str]]:
    """
    Validate an optimization plan from Optimizer agent.

    Expected:
    {
        "current_pnl": 7.5,
        "gap": 2.5,
        "actions": [{"action": "accept_mturk_hits", "count": 10}],
        "expected_additional_revenue": 3.0,
        "confidence": 0.75
    }
    """
    errors = []
    if not isinstance(plan, dict):
        return False, ["Optimization plan must be a dict"]

    errors += _check_required_keys(plan, ["current_pnl", "gap", "actions"])
    errors += _check_range(plan, "confidence", 0.0, 1.0)
    errors += _check_range(plan, "current_pnl", 0.0, 10000.0)

    if not isinstance(plan.get("actions"), list):
        errors.append("'actions' must be a list")
    elif len(plan["actions"]) == 0:
        errors.append("'actions' must not be empty")

    return len(errors) == 0, errors


# ──────────────────────────────────────────────
# LLM response validation
# ──────────────────────────────────────────────

def validate_llm_json(raw: str, expected_keys: List[str]) -> Tuple[bool, Optional[Dict], List[str]]:
    """
    Parse and validate a raw LLM string response as JSON.

    Returns:
        (is_valid, parsed_dict_or_None, errors_list)
    """
    import re
    if not raw:
        return False, None, ["Empty LLM response"]

    # Strip code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract first JSON block
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return False, None, [f"Could not parse JSON from LLM response: {raw[:200]}"]
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError as exc:
            return False, None, [f"JSON parse error: {exc}"]

    missing = [k for k in expected_keys if k not in parsed]
    if missing:
        return False, parsed, [f"Missing keys in LLM JSON: {missing}"]

    return True, parsed, []
