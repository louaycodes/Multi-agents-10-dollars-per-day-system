"""
config/settings.py — Central configuration loader.

All settings are loaded from environment variables (via .env) using python-dotenv.
Import `settings` anywhere in the codebase for type-safe config access.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


def _get(key: str, default=None, cast=str, required=False):
    """Helper: get env var with optional type casting."""
    val = os.environ.get(key, default)
    if required and not val:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    if val is None:
        return None
    try:
        return cast(val)
    except (ValueError, TypeError):
        return default


# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
class DatabaseConfig:
    HOST: str = _get("DB_HOST", "localhost")
    PORT: int = _get("DB_PORT", 5432, int)
    NAME: str = _get("DB_NAME", "agents_db")
    USER: str = _get("DB_USER", "postgres")
    PASSWORD: str = _get("DB_PASSWORD", "")
    POOL_SIZE: int = _get("DB_POOL_SIZE", 5, int)
    MAX_OVERFLOW: int = _get("DB_MAX_OVERFLOW", 10, int)

    @classmethod
    def dsn(cls) -> str:
        return (
            f"host={cls.HOST} port={cls.PORT} "
            f"dbname={cls.NAME} user={cls.USER} "
            f"password={cls.PASSWORD}"
        )

    @classmethod
    def url(cls) -> str:
        return (
            f"postgresql://{cls.USER}:{cls.PASSWORD}"
            f"@{cls.HOST}:{cls.PORT}/{cls.NAME}"
        )


# ──────────────────────────────────────────────
# Redis
# ──────────────────────────────────────────────
class RedisConfig:
    HOST: str = _get("REDIS_HOST", "localhost")
    PORT: int = _get("REDIS_PORT", 6379, int)
    PASSWORD: str = _get("REDIS_PASSWORD", "")
    DB: int = _get("REDIS_DB", 0, int)

    @classmethod
    def url(cls) -> str:
        if cls.PASSWORD:
            return f"redis://:{cls.PASSWORD}@{cls.HOST}:{cls.PORT}/{cls.DB}"
        return f"redis://{cls.HOST}:{cls.PORT}/{cls.DB}"


# ──────────────────────────────────────────────
# LLM
# ──────────────────────────────────────────────
class LLMConfig:
    TYPE: str = _get("LLM_TYPE", "ollama")           # 'ollama' | 'openai'
    OLLAMA_BASE_URL: str = _get("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = _get("OLLAMA_MODEL", "mistral")
    OLLAMA_TIMEOUT: int = _get("OLLAMA_TIMEOUT", 60, int)
    OPENAI_API_KEY: str = _get("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = _get("OPENAI_MODEL", "gpt-3.5-turbo")
    OPENAI_MAX_TOKENS: int = _get("OPENAI_MAX_TOKENS", 2048, int)


# ──────────────────────────────────────────────
# Niche 1: Product Reselling
# ──────────────────────────────────────────────
class ResellingConfig:
    ALIEXPRESS_SCRAPE_MODE: bool = _get("ALIEXPRESS_SCRAPE_MODE", "true", lambda x: x.lower() == "true")
    ALIEXPRESS_API_KEY: str = _get("ALIEXPRESS_API_KEY", "")
    FACEBOOK_API_TOKEN: str = _get("FACEBOOK_API_TOKEN", "")
    FACEBOOK_PAGE_ID: str = _get("FACEBOOK_PAGE_ID", "")
    FACEBOOK_ENABLED: bool = _get("FACEBOOK_MARKETPLACE_ENABLED", "false", lambda x: x.lower() == "true")
    SHOPEE_SELLER_ID: str = _get("SHOPEE_SELLER_ID", "")
    SHOPEE_API_KEY: str = _get("SHOPEE_API_KEY", "")
    SHOPEE_ENABLED: bool = _get("SHOPEE_ENABLED", "false", lambda x: x.lower() == "true")
    BUDGET: float = _get("RESELLING_BUDGET", 20.0, float)
    MIN_PROFIT_MARGIN: float = _get("MIN_PROFIT_MARGIN", 0.60, float)


# ──────────────────────────────────────────────
# Niche 2: Micro-Gigs
# ──────────────────────────────────────────────
class GigsConfig:
    FIVERR_API_TOKEN: str = _get("FIVERR_API_TOKEN", "")
    FIVERR_ENABLED: bool = _get("FIVERR_ENABLED", "false", lambda x: x.lower() == "true")
    MTURK_ACCESS_KEY: str = _get("MTURK_ACCESS_KEY", "")
    MTURK_SECRET_KEY: str = _get("MTURK_SECRET_KEY", "")
    MTURK_REGION: str = _get("MTURK_REGION", "us-east-1")
    MTURK_SANDBOX: bool = _get("MTURK_SANDBOX", "true", lambda x: x.lower() == "true")
    CLICKWORKER_API_KEY: str = _get("CLICKWORKER_API_KEY", "")
    CLICKWORKER_ENABLED: bool = _get("CLICKWORKER_ENABLED", "false", lambda x: x.lower() == "true")


# ──────────────────────────────────────────────
# Niche 3: Affiliate & Content
# ──────────────────────────────────────────────
class AffiliateConfig:
    REDDIT_CLIENT_ID: str = _get("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET: str = _get("REDDIT_CLIENT_SECRET", "")
    REDDIT_USERNAME: str = _get("REDDIT_USERNAME", "")
    REDDIT_PASSWORD: str = _get("REDDIT_PASSWORD", "")
    REDDIT_USER_AGENT: str = _get("REDDIT_USER_AGENT", "MultiAgentSystem/1.0")
    AMAZON_AFFILIATE_ID: str = _get("AMAZON_AFFILIATE_ID", "")
    AMAZON_AFFILIATE_TAG: str = _get("AMAZON_AFFILIATE_TAG", "")
    UDEMY_AFFILIATE_KEY: str = _get("UDEMY_AFFILIATE_KEY", "")
    PAYOUT_PER_CLICK: float = _get("AFFILIATE_PAYOUT_PER_CLICK", 0.25, float)


# ──────────────────────────────────────────────
# System
# ──────────────────────────────────────────────
class SystemConfig:
    TIMEZONE: str = _get("TIMEZONE", "UTC")
    LOG_LEVEL: str = _get("LOG_LEVEL", "INFO")
    DAILY_TARGET: float = _get("DAILY_TARGET", 10.0, float)

    # Agent schedule
    MANAGER_HOUR: int = _get("MANAGER_HOUR", 6, int)
    MANAGER_MINUTE: int = _get("MANAGER_MINUTE", 0, int)
    HUNTER_HOUR: int = _get("HUNTER_HOUR", 6, int)
    HUNTER_MINUTE: int = _get("HUNTER_MINUTE", 30, int)
    EXECUTOR_HOUR: int = _get("EXECUTOR_HOUR", 7, int)
    EXECUTOR_MINUTE: int = _get("EXECUTOR_MINUTE", 0, int)
    TRACKER_INTERVAL_MINUTES: int = _get("TRACKER_INTERVAL_MINUTES", 15, int)
    OPTIMIZER_CHECK_HOUR: int = _get("OPTIMIZER_CHECK_HOUR", 14, int)
    OPTIMIZER_CHECK_MINUTE: int = _get("OPTIMIZER_CHECK_MINUTE", 0, int)
    DAILY_CLOSE_HOUR: int = _get("DAILY_CLOSE_HOUR", 20, int)
    DAILY_CLOSE_MINUTE: int = _get("DAILY_CLOSE_MINUTE", 0, int)

    # Scraping
    SCRAPER_MAX_THREADS: int = _get("SCRAPER_MAX_THREADS", 4, int)
    SCRAPER_REQUEST_DELAY: float = _get("SCRAPER_REQUEST_DELAY", 2.0, float)
    SCRAPER_TIMEOUT: int = _get("SCRAPER_TIMEOUT", 30, int)

    # Opportunities
    MAX_OPPORTUNITIES: int = _get("MAX_OPPORTUNITIES", 8, int)


# Namespace exports
DB = DatabaseConfig
REDIS = RedisConfig
LLM = LLMConfig
RESELLING = ResellingConfig
GIGS = GigsConfig
AFFILIATE = AffiliateConfig
SYSTEM = SystemConfig
