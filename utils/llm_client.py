"""
utils/llm_client.py — Unified LLM interface.

Priority order:
  1. Ollama (local, free, mistral model)
  2. OpenAI (if OPENAI_API_KEY is set)
  3. Rule-based fallback (deterministic JSON generation)

All public methods return plain Python objects (str or dict).
"""

import json
import logging
import re
import time
from typing import Any, Dict, Optional

from config.settings import LLM
from config.constants import LLM_JSON_RETRY_PROMPT, MAX_RETRIES, RETRY_BACKOFF_BASE

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Backend detection
# ──────────────────────────────────────────────
_ollama_available = False
_openai_available = False


def _check_ollama() -> bool:
    """Test if Ollama is running and has the required model."""
    global _ollama_available
    try:
        import requests
        resp = requests.get(f"{LLM.OLLAMA_BASE_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            if any(LLM.OLLAMA_MODEL in m for m in models):
                _ollama_available = True
                logger.info("Ollama available with model '%s'", LLM.OLLAMA_MODEL)
                return True
            else:
                logger.warning(
                    "Ollama running but model '%s' not found. "
                    "Run: ollama pull %s", LLM.OLLAMA_MODEL, LLM.OLLAMA_MODEL
                )
    except Exception as exc:
        logger.warning("Ollama not reachable: %s", exc)
    _ollama_available = False
    return False


def _check_openai() -> bool:
    """Test if OpenAI API key is configured."""
    global _openai_available
    if LLM.OPENAI_API_KEY:
        _openai_available = True
        logger.info("OpenAI fallback available (model=%s)", LLM.OPENAI_MODEL)
        return True
    return False


def init_llm() -> str:
    """
    Detect available LLM backends. Returns active backend name.
    Call once at startup.
    """
    if LLM.TYPE == "ollama" or not LLM.OPENAI_API_KEY:
        if _check_ollama():
            return "ollama"
    if _check_openai():
        return "openai"
    logger.warning("No LLM backend available — using rule-based fallback")
    return "fallback"


# ──────────────────────────────────────────────
# Ollama backend
# ──────────────────────────────────────────────
def _invoke_ollama(prompt: str) -> str:
    """Send a prompt to Ollama and return raw text response."""
    import requests
    payload = {
        "model": LLM.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1024},
    }
    resp = requests.post(
        f"{LLM.OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=LLM.OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


# ──────────────────────────────────────────────
# OpenAI backend
# ──────────────────────────────────────────────
def _invoke_openai(prompt: str) -> str:
    """Send a prompt to OpenAI and return raw text response."""
    from openai import OpenAI
    client = OpenAI(api_key=LLM.OPENAI_API_KEY)
    completion = client.chat.completions.create(
        model=LLM.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "You are a precise JSON-returning AI assistant. Always return valid JSON when asked."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=LLM.OPENAI_MAX_TOKENS,
        temperature=0.3,
    )
    return completion.choices[0].message.content or ""


# ──────────────────────────────────────────────
# Public interface
# ──────────────────────────────────────────────
def invoke(prompt: str) -> str:
    """
    Call the best available LLM backend with retry logic.

    Returns raw string response.
    """
    backends = []
    if _ollama_available:
        backends.append(("ollama", _invoke_ollama))
    if _openai_available:
        backends.append(("openai", _invoke_openai))

    for backend_name, backend_fn in backends:
        for attempt in range(MAX_RETRIES):
            try:
                response = backend_fn(prompt)
                logger.debug("LLM [%s] responded (%d chars)", backend_name, len(response))
                return response
            except Exception as exc:
                wait = min(RETRY_BACKOFF_BASE ** attempt, 30.0)
                logger.warning(
                    "LLM [%s] attempt %d/%d failed: %s — retrying in %.1fs",
                    backend_name, attempt + 1, MAX_RETRIES, exc, wait,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait)

    logger.error("All LLM backends failed — returning empty string")
    return ""


def invoke_json(prompt: str, expected_keys: Optional[list] = None) -> Optional[Dict[str, Any]]:
    """
    Call LLM and parse JSON from the response.
    Retries up to MAX_RETRIES times if response is not valid JSON.

    Args:
        prompt: The user prompt
        expected_keys: Optional list of keys to validate in the response

    Returns:
        Parsed dict, or None if all retries fail.
    """
    current_prompt = prompt
    for attempt in range(MAX_RETRIES):
        raw = invoke(current_prompt)
        parsed = _extract_json(raw)
        if parsed is not None:
            if expected_keys:
                missing = [k for k in expected_keys if k not in parsed]
                if missing:
                    logger.warning("LLM JSON missing keys %s, retrying...", missing)
                    current_prompt = (
                        f"{LLM_JSON_RETRY_PROMPT}\n\n"
                        f"Required keys: {expected_keys}\n\n"
                        f"Original request:\n{prompt}"
                    )
                    continue
            return parsed
        logger.warning("LLM JSON parse failed (attempt %d/%d), retrying...", attempt + 1, MAX_RETRIES)
        current_prompt = f"{LLM_JSON_RETRY_PROMPT}\n\nOriginal request:\n{prompt}"

    logger.error("invoke_json: all retries exhausted, returning None")
    return None


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Attempt to extract a JSON object from raw LLM output.
    Handles markdown code fences and leading/trailing text.
    """
    if not text:
        return None

    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


# ──────────────────────────────────────────────
# Rule-based fallback generators
# (used when no LLM available)
# ──────────────────────────────────────────────

def generate_manager_plan_fallback(
    yesterday: Dict[str, Any],
    performance_metrics: list,
) -> Dict[str, Any]:
    """
    Rule-based daily plan if LLM is unavailable.
    Allocates proportional to last 7d niche revenue.
    """
    from config.constants import DEFAULT_ALLOCATIONS, NICHE_MIN_ALLOCATION, NICHE_MAX_ALLOCATION

    # Calculate revenue per niche from history
    niche_revenue: Dict[str, float] = {"reselling": 0.0, "gigs": 0.0, "affiliate": 0.0}
    for pm in performance_metrics:
        niche = pm.get("niche")
        if niche in niche_revenue:
            niche_revenue[niche] = pm.get("avg_revenue_7d", 0.0)

    total = sum(niche_revenue.values())
    if total == 0:
        allocations = DEFAULT_ALLOCATIONS.copy()
    else:
        allocations = {
            niche: max(NICHE_MIN_ALLOCATION, min(NICHE_MAX_ALLOCATION, rev / total))
            for niche, rev in niche_revenue.items()
        }
        # Normalize to sum 1.0
        alloc_sum = sum(allocations.values())
        allocations = {k: round(v / alloc_sum, 2) for k, v in allocations.items()}

    priority = sorted(allocations, key=lambda n: allocations[n], reverse=True)
    return {
        "date": str(time.strftime("%Y-%m-%d")),
        "target": 10.0,
        "allocations": allocations,
        "priority_niches": priority,
        "strategy": "Rule-based allocation (LLM unavailable) — proportional to 7d avg revenue",
        "expected_revenue": 10.5,
        "llm_reasoning": "Fallback: proportional allocation",
    }


def generate_article_content(topic: str, keywords: list) -> str:
    """Generate a plain-text article for affiliate posts (fallback)."""
    kw_str = ", ".join(keywords[:5])
    return (
        f"# {topic}\n\n"
        f"If you're interested in {topic}, you're in the right place.\n\n"
        f"## Why {topic} Matters\n\n"
        f"Many people search for {kw_str} every day because this topic "
        f"provides real value and practical solutions.\n\n"
        f"## Getting Started\n\n"
        f"The best way to approach {topic} is to start with the basics "
        f"and build up your knowledge step by step.\n\n"
        f"## Conclusion\n\n"
        f"Whether you're a beginner or advanced, {topic} offers opportunities "
        f"for everyone. Take the first step today.\n\n"
        f"*Relevant topics: {kw_str}*\n"
    )
