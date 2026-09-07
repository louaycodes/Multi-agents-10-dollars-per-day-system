"""
config/constants.py — Hardcoded system-wide constants.

These values are NOT secrets and are safe to commit.
Tunable parameters that rarely change.
"""

# ──────────────────────────────────────────────
# Retry & Timeout
# ──────────────────────────────────────────────
MAX_RETRIES: int = 3
RETRY_BACKOFF_BASE: float = 2.0       # seconds; doubles each retry
RETRY_BACKOFF_MAX: float = 30.0       # cap at 30s
API_TIMEOUT_SECONDS: int = 30
LLM_TIMEOUT_SECONDS: int = 90         # LLM may be slow locally
SCRAPER_TIMEOUT_SECONDS: int = 20

# ──────────────────────────────────────────────
# Opportunity Scoring Weights
# Formula: score = (roi * feasibility * time_to_profit) / effort_hours
# ──────────────────────────────────────────────
SCORE_ROI_WEIGHT: float = 1.0
SCORE_FEASIBILITY_WEIGHT: float = 1.0
SCORE_TIME_TO_PROFIT_WEIGHT: float = 1.0   # 1.0=same day, 0.5=2-3 days
SCORE_EFFORT_DIVISOR: float = 1.0

# Risk filter: skip any opportunity with risk_level == HIGH
RISK_LEVELS_BLOCKED: list = ["high"]

# Min feasibility score to consider an opportunity
MIN_FEASIBILITY_SCORE: float = 5.0
# Min estimated ROI ($) to consider
MIN_ESTIMATED_ROI: float = 1.0

# ──────────────────────────────────────────────
# Budget Allocation Constraints
# Manager enforces these floors/ceilings per niche
# ──────────────────────────────────────────────
NICHE_MIN_ALLOCATION: float = 0.10    # At least 10% to any active niche
NICHE_MAX_ALLOCATION: float = 0.70    # No more than 70% to any single niche

# Default allocation if no history available (Day 1)
DEFAULT_ALLOCATIONS: dict = {
    "reselling": 0.50,
    "gigs": 0.35,
    "affiliate": 0.15,
}

# ──────────────────────────────────────────────
# Revenue Targets
# ──────────────────────────────────────────────
DAILY_TARGET_DEFAULT: float = 10.0
OPTIMIZER_TRIGGER_THRESHOLD: float = 10.0  # Alert if P&L < this at 14:00

# P&L thresholds for optimizer escalation severity
OPTIMIZER_CRITICAL_THRESHOLD: float = 5.0  # < 5$ → emergency pivot
OPTIMIZER_MODERATE_THRESHOLD: float = 8.0  # 5-8$ → moderate pivot
# >= 8$ → light touch

# ──────────────────────────────────────────────
# Niche-Specific Constants
# ──────────────────────────────────────────────

# Reselling
RESELLING_MAX_PRODUCTS_PER_DAY: int = 10
RESELLING_DEFAULT_MARKUP: float = 2.5     # 2.5x cost price
RESELLING_FLASH_SALE_DISCOUNT: float = 0.30  # 30% off for optimizer pivot
RESELLING_MIN_UNIT_MARGIN_USD: float = 1.0

# Gigs
MTURK_MAX_HITS_PER_DAY: int = 20
MTURK_MIN_APPROVAL_RATE: float = 0.95    # 95%
MTURK_MIN_PAY_PER_HOUR: float = 3.0
MTURK_PIVOT_ADDITIONAL_HITS: int = 10    # Extra HITs on optimizer trigger
FIVERR_MAX_ACTIVE_GIGS: int = 5

# Affiliate
AFFILIATE_MAX_POSTS_PER_DAY: int = 10
AFFILIATE_QUORA_ANSWER_MIN_LENGTH: int = 150  # words
AFFILIATE_REDDIT_COMMENT_MIN_LENGTH: int = 100
AFFILIATE_PIVOT_EXTRA_POSTS: int = 5

# ──────────────────────────────────────────────
# Scraper Sources
# ──────────────────────────────────────────────
REDDIT_SUBREDDITS: list = [
    "entrepreneur",
    "dropshipping",
    "freelance",
    "Flipping",
    "beermoney",
]

QUORA_TOPIC_URLS: list = [
    "https://www.quora.com/topic/Entrepreneurship",
    "https://www.quora.com/topic/Online-Money-Making",
    "https://www.quora.com/topic/Dropshipping",
]

TIKTOK_SCRAPE_URLS: list = [
    "https://www.tiktok.com/trending",
    "https://www.tiktok.com/tag/productreview",
    "https://www.tiktok.com/tag/tiktokmademebuyit",
]

ALIEXPRESS_BESTSELLER_URL: str = (
    "https://www.aliexpress.com/category/200000345/consumer-electronics.html"
    "?SortType=total_tranqnty_desc"
)

# ──────────────────────────────────────────────
# Message Queue Channels
# ──────────────────────────────────────────────
CHANNEL_MANAGER_TO_ALL: str = "manager:daily_plan"
CHANNEL_HUNTER_TO_EXECUTOR: str = "hunter:opportunities"
CHANNEL_EXECUTOR_TO_TRACKER: str = "executor:transactions"
CHANNEL_TRACKER_TO_OPTIMIZER: str = "tracker:alerts"
CHANNEL_OPTIMIZER_TO_EXECUTOR: str = "optimizer:pivot_actions"

# Queue message TTL (seconds) — discard stale messages
MESSAGE_TTL_SECONDS: int = 3600  # 1 hour

# ──────────────────────────────────────────────
# LLM Prompt Templates (filled at runtime)
# ──────────────────────────────────────────────
LLM_JSON_RETRY_PROMPT: str = (
    "Your previous response was not valid JSON. "
    "Return ONLY a valid JSON object with no markdown, no explanation, "
    "no code fences. Just the raw JSON object."
)

# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
TRANSACTION_STATUSES: list = ["pending", "active", "completed", "paid", "failed"]
NICHE_NAMES: list = ["reselling", "gigs", "affiliate"]
ALERT_TYPES: list = ["low_pnl", "api_error", "account_issue", "optimization_triggered"]
ALERT_SEVERITIES: list = ["low", "medium", "high"]
