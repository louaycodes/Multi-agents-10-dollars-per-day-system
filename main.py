"""
main.py — Entry point for the Autonomous Revenue Agent System.

Initializes all 5 agents and schedules them with APScheduler:
  06:00 → Manager (daily planning)
  06:30 → Hunter (opportunity discovery)
  07:00 → Executor (transaction execution)
  Every 15 min → Tracker (real-time P&L monitoring)
  14:00 → Optimizer trigger check
  20:00 → Manager daily close + Tracker final report

Run: python main.py
Stop: Ctrl+C (graceful shutdown)
"""

import sys
import time
import signal
import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.logger import setup_logging, get_logger
from config.settings import SYSTEM

# Initialize logging FIRST (before any other imports that use logging)
setup_logging(level=SYSTEM.LOG_LEVEL)
logger = get_logger(__name__)


class AutonomousAgentSystem:
    """
    Central orchestrator for all 5 autonomous agents.
    """

    def __init__(self):
        logger.info("Initializing Autonomous Agent System...")

        # Initialize DB and message queue
        from utils.database import init_db
        from utils.message_queue import init_queue
        from utils.llm_client import init_llm

        init_db()
        logger.info("Database initialized")

        init_queue()
        logger.info("Message queue initialized")

        llm_backend = init_llm()
        logger.info("LLM backend: %s", llm_backend)

        # Initialize agents (lazy import to avoid circular deps)
        from agents.manager import ManagerAgent
        from agents.hunter import HunterAgent
        from agents.executor import ExecutorAgent
        from agents.tracker import TrackerAgent
        from agents.optimizer import OptimizerAgent

        self.manager = ManagerAgent()
        self.hunter = HunterAgent()
        self.executor = ExecutorAgent()
        self.tracker = TrackerAgent()
        self.optimizer = OptimizerAgent()

        # Scheduler
        tz = pytz.timezone(SYSTEM.TIMEZONE)
        self.scheduler = BackgroundScheduler(
            timezone=tz,
            job_defaults={
                "misfire_grace_time": 300,    # Allow 5min late start
                "coalesce": True,              # Don't run same job twice if missed
                "max_instances": 1,            # One instance per job at a time
            },
        )

        self._setup_signal_handlers()
        logger.info("All agents initialized successfully")

    # ──────────────────────────────────────────
    # Scheduler setup
    # ──────────────────────────────────────────

    def _setup_jobs(self) -> None:
        """Register all scheduled jobs."""
        tz = pytz.timezone(SYSTEM.TIMEZONE)

        # 06:00 — Manager planning
        self.scheduler.add_job(
            self._run_manager_morning,
            CronTrigger(hour=SYSTEM.MANAGER_HOUR, minute=SYSTEM.MANAGER_MINUTE, timezone=tz),
            id="manager_morning",
            name="Manager: Daily Planning",
        )

        # 06:30 — Hunter opportunity discovery
        self.scheduler.add_job(
            self._run_hunter,
            CronTrigger(hour=SYSTEM.HUNTER_HOUR, minute=SYSTEM.HUNTER_MINUTE, timezone=tz),
            id="hunter",
            name="Hunter: Opportunity Discovery",
        )

        # 07:00 — Executor transaction execution
        self.scheduler.add_job(
            self._run_executor,
            CronTrigger(hour=SYSTEM.EXECUTOR_HOUR, minute=SYSTEM.EXECUTOR_MINUTE, timezone=tz),
            id="executor_morning",
            name="Executor: Transaction Execution",
        )

        # Every 15 min — Tracker monitoring
        self.scheduler.add_job(
            self._run_tracker,
            IntervalTrigger(minutes=SYSTEM.TRACKER_INTERVAL_MINUTES, timezone=tz),
            id="tracker",
            name="Tracker: P&L Monitoring",
        )

        # 14:00 — Optimizer trigger check
        self.scheduler.add_job(
            self._check_optimizer,
            CronTrigger(
                hour=SYSTEM.OPTIMIZER_CHECK_HOUR,
                minute=SYSTEM.OPTIMIZER_CHECK_MINUTE,
                timezone=tz,
            ),
            id="optimizer_check",
            name="Optimizer: 14:00 P&L Check",
        )

        # 20:00 — Daily close
        self.scheduler.add_job(
            self._run_daily_close,
            CronTrigger(
                hour=SYSTEM.DAILY_CLOSE_HOUR,
                minute=SYSTEM.DAILY_CLOSE_MINUTE,
                timezone=tz,
            ),
            id="daily_close",
            name="Manager+Tracker: Daily Close",
        )

        jobs = self.scheduler.get_jobs()
        for job in jobs:
            logger.info("Scheduled: %-40s | next run: %s", job.name, job.next_run_time)

    # ──────────────────────────────────────────
    # Job wrappers
    # ──────────────────────────────────────────

    def _run_manager_morning(self) -> None:
        """06:00 — Daily planning."""
        self.manager.graceful_run()

    def _run_hunter(self) -> None:
        """06:30 — Opportunity discovery."""
        self.hunter.graceful_run()

    def _run_executor(self) -> None:
        """07:00 — Execute transactions from Hunter."""
        self.executor.graceful_run()

        # Also check for any pivot actions from previous day's Optimizer
        self._check_pivot_queue()

    def _run_tracker(self) -> None:
        """Every 15 min — Update P&L."""
        self.tracker.graceful_run()

    def _check_optimizer(self) -> None:
        """14:00 — Check P&L and trigger Optimizer if needed."""
        logger.info("=== 14:00 P&L CHECK ===")
        triggered = self.tracker.check_optimizer_trigger()

        if triggered:
            logger.warning("Optimizer triggered — generating pivot plan")
            self.optimizer.graceful_run()
            # Re-run Executor with pivot actions
            self._execute_pivot()

    def _run_daily_close(self) -> None:
        """20:00 — Final daily report and close."""
        logger.info("=== DAILY CLOSE ===")
        self.tracker.generate_daily_report()
        self.manager.daily_close()

    def _check_pivot_queue(self) -> None:
        """Check if Optimizer has published pivot actions to execute."""
        from utils.message_queue import get_message
        from config.constants import CHANNEL_OPTIMIZER_TO_EXECUTOR

        pivot = get_message(CHANNEL_OPTIMIZER_TO_EXECUTOR, timeout=0.5)
        if pivot:
            logger.info("Found pending pivot actions — executing...")
            self.executor.execute_pivot_actions(pivot)

    def _execute_pivot(self) -> None:
        """Immediately execute pivot actions after Optimizer runs."""
        from utils.message_queue import get_message
        from config.constants import CHANNEL_OPTIMIZER_TO_EXECUTOR
        import time as _time

        # Give Optimizer a moment to publish
        _time.sleep(2)
        pivot = get_message(CHANNEL_OPTIMIZER_TO_EXECUTOR, timeout=5.0)
        if pivot:
            logger.info("Executing pivot plan from Optimizer")
            self.executor.execute_pivot_actions(pivot)
        else:
            logger.warning("No pivot plan received from Optimizer")

    # ──────────────────────────────────────────
    # System lifecycle
    # ──────────────────────────────────────────

    def start(self) -> None:
        """Start the agent system and run until interrupted."""
        logger.info("=" * 60)
        logger.info("AUTONOMOUS REVENUE AGENT SYSTEM STARTING")
        logger.info("  Target: $%.2f/day", SYSTEM.DAILY_TARGET)
        logger.info("  Timezone: %s", SYSTEM.TIMEZONE)
        logger.info("  Log level: %s", SYSTEM.LOG_LEVEL)
        logger.info("=" * 60)

        self._setup_jobs()
        self.scheduler.start()

        logger.info("System running. Press Ctrl+C to stop.")
        logger.info("Next scheduled events:")

        for job in self.scheduler.get_jobs():
            if job.next_run_time:
                logger.info("  %-40s → %s", job.name, job.next_run_time.strftime("%H:%M %Z"))

        # Option: Run immediately for testing (uncomment to trigger morning cycle now)
        # self._run_immediately_for_testing()

        # Keep-alive loop
        try:
            while True:
                time.sleep(30)
                # Log heartbeat every hour
                now = datetime.utcnow()
                if now.minute == 0:
                    running_jobs = [j for j in self.scheduler.get_jobs() if j.next_run_time]
                    logger.info("♥ HEARTBEAT | UTC %s | %d jobs scheduled", now.strftime("%H:%M"), len(running_jobs))
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received")
            self.stop()

    def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down scheduler...")
        self.scheduler.shutdown(wait=True)
        from utils.database import close_db
        close_db()
        logger.info("System shutdown complete. Goodbye.")

    def _setup_signal_handlers(self) -> None:
        """Handle SIGTERM for Docker graceful shutdown."""
        def _handle_sigterm(signum, frame):
            logger.info("SIGTERM received — initiating graceful shutdown")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _handle_sigterm)

    def run_immediately(self) -> None:
        """
        Run the full morning cycle immediately (for testing).
        Bypasses scheduler timing.
        """
        logger.info("=== IMMEDIATE TEST RUN ===")
        logger.info("Running full cycle now...")

        logger.info("Step 1/5: Manager morning planning")
        self._run_manager_morning()

        logger.info("Step 2/5: Hunter opportunity discovery")
        self._run_hunter()

        logger.info("Step 3/5: Executor transaction execution")
        self._run_executor()

        logger.info("Step 4/5: Tracker P&L check")
        self._run_tracker()

        logger.info("Step 5/5: Optimizer check")
        self._check_optimizer()

        logger.info("=== IMMEDIATE RUN COMPLETE ===")
        pnl = self.tracker.get_current_pnl()
        logger.info("Current P&L: $%.2f (target: $%.2f)", pnl.get("profit", 0), pnl.get("target", 10))


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Autonomous Revenue Agent System")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Run the full morning cycle immediately (for testing)",
    )
    parser.add_argument(
        "--agent",
        choices=["manager", "hunter", "executor", "tracker", "optimizer"],
        help="Run a single agent immediately",
    )
    args = parser.parse_args()

    system = AutonomousAgentSystem()

    if args.now:
        system.run_immediately()
    elif args.agent:
        agent_map = {
            "manager": system._run_manager_morning,
            "hunter": system._run_hunter,
            "executor": system._run_executor,
            "tracker": system._run_tracker,
            "optimizer": system._check_optimizer,
        }
        logger.info("Running single agent: %s", args.agent)
        agent_map[args.agent]()
        logger.info("Done.")
    else:
        system.start()
