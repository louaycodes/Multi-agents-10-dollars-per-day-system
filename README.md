# 🤖 Autonomous Multi-Agent Revenue System

> **Target: $10/day** — Fully autonomous, runs 24/7 without human intervention

A production-ready system of 5 specialized AI agents that collaborate daily to generate revenue through:
- 🛒 **Product Reselling** (AliExpress → Facebook Marketplace/Shopee) — 50% of revenue
- ⚙️ **Micro-Gigs** (Mturk HITs + Fiverr API optimization) — 35% of revenue  
- 🔗 **Affiliate Content** (Reddit + Quora + Dev.to with affiliate links) — 15% of revenue

---

## 🏗 Architecture

```
06:00  ┌─────────────────────────────────────────────────────┐
       │  MANAGER — Reads yesterday's DB, allocates budget,  │
       │  generates daily plan via LLM, publishes to queue   │
       └─────────────────┬───────────────────────────────────┘
                         │  daily_plan
06:30  ┌─────────────────▼───────────────────────────────────┐
       │  HUNTER — Scrapes TikTok/Reddit/AliExpress/Quora    │
       │  Scores opportunities, saves top 8 to DB + queue    │
       └─────────────────┬───────────────────────────────────┘
                         │  opportunities[]
07:00  ┌─────────────────▼───────────────────────────────────┐
       │  EXECUTOR — Dispatches to 3 niches:                 │
       │   Reselling: List products on marketplace           │
       │   Gigs: Accept Mturk HITs, optimize Fiverr gigs    │
       │   Affiliate: Post Reddit/Quora content              │
       └─────────────────┬───────────────────────────────────┘
                         │  transactions[]
09:00  ┌─────────────────▼───────────────────────────────────┐
-20:00 │  TRACKER — Every 15min: poll APIs, update P&L       │
       │  At 14:00: if P&L < $10 → trigger Optimizer        │
       └───────────────────────────────────────────┬─────────┘
                                                   │  alert
14:00  ┌───────────────────────────────────────────▼─────────┐
       │  OPTIMIZER (on-demand) — LLM pivot analysis:        │
       │   Critical (<$5): flash sale + Mturk boost          │
       │   Moderate ($5-8): optimize weakest niche           │
       │   Light ($8-9): 5 more affiliate posts              │
       └─────────────────────────────────────────────────────┘
20:00  → Final report → Manager daily close → Sleep until 06:00
```

---

## ⚡ Quick Start

```bash
# 1. Clone
git clone <repo-url> && cd Multi-agents-10-dollars-per-day-system

# 2. Install
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — add Mturk + Reddit credentials at minimum

# 4. Start infrastructure
docker-compose up -d postgres redis ollama
docker exec agents_ollama ollama pull mistral  # ~4GB, one-time

# 5. Test run (immediate)
python main.py --now

# 6. Start scheduled system
python main.py
```

See [docs/SETUP.md](docs/SETUP.md) for complete step-by-step instructions.

---

## 📁 Project Structure

```
autonomous-agents/
├── main.py                    # Entry point + APScheduler
├── requirements.txt           # Pinned dependencies
├── docker-compose.yml         # Postgres + Redis + Ollama + App
├── Dockerfile
├── .env.example               # Config template
│
├── config/
│   ├── settings.py            # Typed env var loader
│   ├── logger.py              # 3 log streams (daily/errors/transactions)
│   └── constants.py           # Scoring weights, limits, channels
│
├── database/
│   └── schema.sql             # 6 PostgreSQL tables with indexes
│
├── agents/
│   ├── base_agent.py          # retry_with_backoff, graceful_run, alerts
│   ├── manager.py             # Daily planning + evening close
│   ├── hunter.py              # Parallel scraping + opportunity scoring
│   ├── executor.py            # 3-niche transaction execution + pivots
│   ├── tracker.py             # Real-time P&L + optimizer trigger
│   └── optimizer.py          # Emergency pivot generation
│
├── utils/
│   ├── database.py            # PostgreSQL connection pool + CRUD
│   ├── message_queue.py       # Redis pub/sub + in-memory fallback
│   ├── llm_client.py          # Ollama → OpenAI → rule-based fallback
│   ├── scrapers.py            # Reddit/TikTok/AliExpress/Quora/Mturk
│   ├── api_clients.py         # Facebook/Reddit/Dev.to/Fiverr/Mturk APIs
│   └── validators.py          # Schema validation for all agent data
│
├── tests/
│   ├── test_agents.py         # 20+ unit tests for all 5 agents
│   ├── test_database.py       # DB CRUD + validator tests
│   └── test_scrapers.py       # Scraper fallback + parsing tests
│
├── docs/
│   ├── SETUP.md               # Installation guide
│   ├── API_REFERENCE.md       # All public methods documented
│   └── TROUBLESHOOTING.md     # Common issues + fixes
│
└── logs/
    ├── daily.log              # All activity (INFO+, rotating 10MB)
    ├── errors.log             # Errors only (ERROR+)
    └── transactions.log       # Transaction events only
```

---

## 🔑 Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| **Niches** | Reselling + Gigs + Affiliate | Realistic Day 1 revenue (unlike Medium/Fiverr-from-zero) |
| **LLM** | Ollama (mistral) → OpenAI → rule-based | Free-first, always has fallback |
| **Message Queue** | Redis → in-memory | Persistent when Redis up, never blocks when down |
| **No Selenium** | API-only for Fiverr | ToS compliant, no ban risk |
| **Mturk default** | Sandbox mode | Safe until you're ready for real HITs |
| **Scoring formula** | `(roi × feasibility × time_to_profit) / effort` | Rewards fast, easy, high-ROI opportunities |

---

## 📊 Revenue Timeline

| Period | Expected | Notes |
|--------|----------|-------|
| Day 1-2 | $2-6/day | Setup, first listings, no sales yet |
| Day 3-4 | $7-11/day | Listings gaining traction |
| Week 2 | $10-15/day | Target consistently met |
| Month 1 | ~$300 | Stable with top niches optimized |

---

## 🛠 Technology Stack

- **Python 3.11+** — Core language
- **CrewAI + LangChain** — Agent framework
- **APScheduler** — Lightweight cron scheduling
- **Ollama (mistral)** — Local LLM, free
- **PostgreSQL 14** — Persistent data store
- **Redis 7** — Message queue + cache
- **Docker + Compose** — One-command deployment
- **psycopg2** — PostgreSQL driver (connection pool)
- **boto3** — Amazon Mturk API
- **BeautifulSoup4 + requests** — Web scraping

---

## 🔒 Security

- ✅ Zero hardcoded secrets — all in `.env`
- ✅ `.env` never committed to git (add to `.gitignore`)
- ✅ Non-root Docker user
- ✅ Database connection pooling (not raw connections)
- ✅ Log rotation (10MB max per file, 7 backups)
- ✅ ToS-compliant: API-only (no Selenium automation)

---

## 📝 Testing

```bash
# All tests
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_agents.py -v

# Single test
python -m pytest tests/test_agents.py::TestManagerAgent::test_generate_daily_plan_with_llm -v
```

---

## 📚 Documentation

- [Setup Guide](docs/SETUP.md) — Installation + first run
- [API Reference](docs/API_REFERENCE.md) — All methods documented
- [Troubleshooting](docs/TROUBLESHOOTING.md) — Common issues + fixes
