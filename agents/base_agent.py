"""
agents/base_agent.py — Abstract base class for all agents.

Provides:
  - retry_with_backoff(): Decorator/wrapper for retrying failed operations
  - log_action(): Structured action logging
  - save_alert(): Persist alerts to DB
  - graceful_run(): Wrapper that catches all exceptions to prevent agent crashes
"""

import logging
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional

from config.constants import MAX_RETRIES, RETRY_BACKOFF_BASE, RETRY_BACKOFF_MAX

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base class for all autonomous agents.

    Subclasses must implement: run()
    """

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"agents.{name}")
        self.start_time: Optional[datetime] = None
        self.run_count: int = 0

    @abstractmethod
    def run(self) -> Any:
        """Main agent execution logic. Override in subclasses."""
        ...

    def graceful_run(self) -> Optional[Any]:
        """
        Wraps run() with top-level exception handling.
        Ensures one agent crashing doesn't bring down the whole system.
        """
        self.start_time = datetime.utcnow()
        self.run_count += 1
        self.logger.info(
            "=== %s WAKEUP === (run #%d at %s UTC)",
            self.name.upper(),
            self.run_count,
            self.start_time.strftime("%H:%M:%S"),
        )
        try:
            result = self.run()
            elapsed = (datetime.utcnow() - self.start_time).total_seconds()
            self.logger.info("=== %s DONE === (%.1fs)", self.name.upper(), elapsed)
            return result
        except Exception as exc:
            elapsed = (datetime.utcnow() - self.start_time).total_seconds()
            self.logger.error(
                "=== %s CRASHED === (%.1fs) | %s\n%s",
                self.name.upper(),
                elapsed,
                exc,
                traceback.format_exc(),
            )
            # Save crash alert to DB (best-effort)
            try:
                self._save_crash_alert(str(exc))
            except Exception:
                pass
            return None

    def retry_with_backoff(
        self,
        func: Callable,
        *args,
        max_retries: int = MAX_RETRIES,
        operation_name: str = "",
        **kwargs,
    ) -> Optional[Any]:
        """
        Call func(*args, **kwargs) with exponential backoff retry.

        Returns:
            Function result, or None if all retries fail.
        """
        label = operation_name or getattr(func, "__name__", str(func))
        for attempt in range(max_retries):
            try:
                result = func(*args, **kwargs)
                if attempt > 0:
                    self.logger.info("'%s' succeeded on attempt %d/%d", label, attempt + 1, max_retries)
                return result
            except Exception as exc:
                wait = min(RETRY_BACKOFF_BASE ** attempt, RETRY_BACKOFF_MAX)
                if attempt < max_retries - 1:
                    self.logger.warning(
                        "'%s' attempt %d/%d failed: %s — retrying in %.1fs",
                        label, attempt + 1, max_retries, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    self.logger.error(
                        "'%s' failed after %d attempts: %s", label, max_retries, exc
                    )
        return None

    def log_action(
        self,
        action: str,
        niche: str = "",
        result: Any = None,
        error: str = "",
        extra: Optional[dict] = None,
    ) -> None:
        """
        Log a structured agent action.

        Args:
            action: Short verb describing the action (e.g., "product_listed")
            niche: Which niche this action belongs to
            result: Success/failure result
            error: Error message if failed
            extra: Additional key-value pairs to log
        """
        parts = [f"[{self.name}]", f"action={action}"]
        if niche:
            parts.append(f"niche={niche}")
        if result is not None:
            status = "OK" if result else "FAIL"
            parts.append(f"status={status}")
        if error:
            parts.append(f"error={error[:100]}")
        if extra:
            for k, v in extra.items():
                parts.append(f"{k}={v}")

        msg = " | ".join(parts)
        if error:
            self.logger.warning(msg)
        else:
            self.logger.info(msg)

    def save_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        action_taken: Optional[str] = None,
    ) -> Optional[int]:
        """
        Persist an alert to the database.
        Returns alert_id, or None if DB save fails.
        """
        try:
            from utils.database import insert_alert
            alert_id = insert_alert(
                alert_type=alert_type,
                severity=severity,
                message=message,
                agent_triggered=self.name,
                action_taken=action_taken,
            )
            return alert_id
        except Exception as exc:
            self.logger.error("Failed to save alert: %s", exc)
            return None

    def _save_crash_alert(self, error_message: str) -> None:
        """Save a crash alert to DB on unhandled exception."""
        self.save_alert(
            alert_type="api_error",
            severity="high",
            message=f"Agent {self.name} crashed: {error_message[:500]}",
            action_taken="System continuing with other agents",
        )


def retry(max_attempts: int = MAX_RETRIES, backoff_base: float = RETRY_BACKOFF_BASE):
    """
    Decorator: retry a function with exponential backoff.

    Usage:
        @retry(max_attempts=3)
        def my_api_call():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    wait = min(backoff_base ** attempt, RETRY_BACKOFF_MAX)
                    if attempt < max_attempts - 1:
                        logger.warning(
                            "@retry | %s attempt %d/%d failed: %s — retrying in %.1fs",
                            func.__name__, attempt + 1, max_attempts, exc, wait,
                        )
                        time.sleep(wait)
                    else:
                        logger.error("@retry | %s failed after %d attempts: %s", func.__name__, max_attempts, exc)
                        raise
        return wrapper
    return decorator
