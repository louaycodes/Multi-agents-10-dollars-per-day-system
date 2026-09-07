# 🤖 Setup Guide — Autonomous Revenue Agent System

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | [python.org](https://python.org) |
| Docker | 24+ | [docker.com](https://docker.com) |
| Docker Compose | v2+ | Included with Docker Desktop |
| Git | any | [git-scm.com](https://git-scm.com) |

---

## Step 1 — Clone & Install

```bash
git clone <your-repo-url>
cd Multi-agents-10-dollars-per-day-system

# Create a virtual environment
python3.11 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## Step 2 — Configure Environment

```bash
cp .env.example .env
nano .env   # or open in your editor
```

### Minimum required settings for Day 1:

```env
# Database (Docker handles this — no change needed in dev)
DB_PASSWORD=dev_password

# LLM — choose one:
LLM_TYPE=ollama                         # Free local LLM (recommended)
OLLAMA_MODEL=mistral

# OR use OpenAI instead:
# LLM_TYPE=openai
# OPENAI_API_KEY=sk-your-key-here

# Mturk (sandbox by default — safe for testing)
MTURK_ACCESS_KEY=your_access_key
MTURK_SECRET_KEY=your_secret_key
MTURK_SANDBOX=true   # Keep true until you're ready for real HITs

# Reddit (register free app at reddit.com/prefs/apps)
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_secret
REDDIT_USERNAME=your_username
REDDIT_PASSWORD=your_password
```

### Optional (for live marketplace revenue):

```env
# Facebook Marketplace (requires approved Facebook App)
FACEBOOK_API_TOKEN=your_graph_api_token
FACEBOOK_PAGE_ID=your_page_id
FACEBOOK_MARKETPLACE_ENABLED=true

# Fiverr (API only — no Selenium)
FIVERR_API_TOKEN=your_token
FIVERR_ENABLED=true

# Affiliate (Amazon Associates)
AMAZON_AFFILIATE_ID=your_id
AMAZON_AFFILIATE_TAG=your-20
```

---

## Step 3 — Start Infrastructure

```bash
# Start PostgreSQL, Redis, and Ollama
docker-compose up -d postgres redis ollama

# Wait ~30 seconds for postgres to be ready, then verify:
docker-compose ps

# Expected output:
# agents_postgres   running (healthy)
# agents_redis      running (healthy)
# agents_ollama     running (healthy)
```

### Pull the LLM model (one-time):

```bash
docker exec agents_ollama ollama pull mistral
# This downloads ~4GB — takes 2-10 minutes depending on connection
```

### Verify Ollama is working:

```bash
curl http://localhost:11434/api/generate -d '{"model":"mistral","prompt":"Hello!","stream":false}'
```

---

## Step 4 — Initialize Database

The schema is auto-applied on first Postgres start via Docker volume mount.
To verify:

```bash
docker exec -it agents_postgres psql -U postgres -d agents_db \
  -c "\dt"
# Expected: 6 tables listed (daily_results, transactions, opportunities, alerts, performance_metrics, daily_plans)
```

To manually re-apply schema:

```bash
docker exec -i agents_postgres psql -U postgres -d agents_db \
  < database/schema.sql
```

---

## Step 5 — Test Run (Immediate)

Run the full morning cycle right now to verify everything works:

```bash
# Activate venv first
source venv/bin/activate

# Run all agents immediately (bypass scheduler)
python main.py --now
```

Expected log output:
```
[Manager WAKEUP] Reading yesterday's results from DB...
[Manager DONE] Daily plan: allocations={'reselling': 0.50, ...}
[Hunter WAKEUP] Starting parallel scraping...
[Hunter DONE] Found 8 opportunities
[Executor WAKEUP] Executing 8 opportunities...
[Executor DONE] 6/8 opportunities executed
[Tracker WAKEUP] Checking 6 active transactions...
[Tracker DONE] P&L: revenue=$0.00 (active transactions, no revenue yet)
```

### Test individual agents:

```bash
python main.py --agent manager
python main.py --agent hunter
python main.py --agent executor
python main.py --agent tracker
```

---

## Step 6 — Run Unit Tests

```bash
python -m pytest tests/ -v

# Expected output:
# tests/test_agents.py::TestManagerAgent::test_generate_daily_plan_with_llm PASSED
# tests/test_agents.py::TestHunterAgent::test_opportunity_scoring PASSED
# ... (20+ tests)
```

---

## Step 7 — Start the Scheduled System

```bash
# Option A: Local (development)
python main.py

# Option B: Docker (production)
docker-compose up -d
docker logs -f agents_app
```

The system will log:
```
AUTONOMOUS REVENUE AGENT SYSTEM STARTING
  Target: $10.00/day
  Timezone: UTC
Next scheduled events:
  Manager: Daily Planning          → 06:00 UTC
  Hunter: Opportunity Discovery    → 06:30 UTC
  Executor: Transaction Execution  → 07:00 UTC
  Tracker: P&L Monitoring          → every 15 min
  Optimizer: 14:00 P&L Check       → 14:00 UTC
  Manager+Tracker: Daily Close     → 20:00 UTC
```

---

## Monitoring

### View logs:

```bash
# All logs (real-time)
tail -f logs/daily.log

# Errors only
tail -f logs/errors.log

# Transaction events only
tail -f logs/transactions.log

# Docker logs
docker logs -f agents_app
```

### Check database:

```bash
# Latest daily results
docker exec -it agents_postgres psql -U postgres -d agents_db \
  -c "SELECT date, revenue_total, profit, target_met FROM daily_results ORDER BY date DESC LIMIT 7;"

# Today's transactions
docker exec -it agents_postgres psql -U postgres -d agents_db \
  -c "SELECT niche, platform, agent_action, status, revenue FROM transactions WHERE date::date = CURRENT_DATE;"

# Active alerts
docker exec -it agents_postgres psql -U postgres -d agents_db \
  -c "SELECT alert_type, severity, message FROM alerts WHERE resolved = FALSE ORDER BY created_at DESC;"
```

### System health:

```bash
docker stats                    # CPU + memory usage
docker-compose ps               # Service status
```

---

## VPS Deployment (Production)

```bash
# 1. Provision VPS (2GB RAM minimum recommended)
#    DigitalOcean Droplet $12/mo | Hetzner CX21 | Linode 2GB

# 2. Install Docker
curl -fsSL https://get.docker.com | bash
apt install docker-compose-plugin -y

# 3. Clone repo
git clone <your-repo> && cd Multi-agents-10-dollars-per-day-system

# 4. Configure production .env
cp .env.example .env
# Edit: set MTURK_SANDBOX=false, real FB tokens, etc.

# 5. Build and start
docker-compose build
docker-compose up -d

# 6. Monitor
docker logs -f agents_app
```

---

## Revenue Timeline (Realistic)

| Day | Expected Revenue | Notes |
|-----|-----------------|-------|
| Day 1 | $2-4 | Setup, listings active, no sales yet |
| Day 2 | $4-7 | Listings gain traction |
| Day 3 | $7-11 | Products selling, Quora answers getting views |
| Day 4+ | $10-15 | Stable daily revenue |
| Week 2 | $12-20/day | Optimized listings, proven niches scaled |

> **Mturk sandbox** must be switched to `MTURK_SANDBOX=false` in `.env` to earn real money from HITs.
> **Facebook Marketplace** requires a Graph API token from an approved Facebook Developer App.
