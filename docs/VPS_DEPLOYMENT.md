# 🚀 VPS Deployment Guide — Agents System on Contabo

> **Guarantee**: The portfolio is **never touched**. Agents live in their own Docker network, volumes, ports, and directory.

---

## Port Isolation Map

| Service | Portfolio (DO NOT CHANGE) | Agents System (NEW) |
|---------|--------------------------|---------------------|
| PostgreSQL | `5432` (if any) | **`5433`** |
| Redis | `6379` (if any) | **`6380`** |
| Ollama | `11434` (if any) | **`11435`** |
| App/API | `3000` / `8000` (portfolio) | **`8001`** |
| Nginx | `80` / `443` | unchanged |

---

## Directory Structure on VPS

```
/home/louay/
├── portfolio/          ← EXISTING — never touched
│   ├── frontend/
│   ├── backend/
│   └── (docker-compose.yml or pm2 or systemd)
│
└── agents-system/      ← NEW — 100% isolated
    ├── .env            ← your API keys (git-ignored)
    ├── docker-compose.yml
    ├── main.py
    ├── agents/
    ├── utils/
    ├── database/
    ├── logs/
    ├── backups/
    └── docs/
```

---

## STEP 1 — Connect to VPS

```bash
ssh louay@<YOUR_CONTABO_IP>
# or with key: ssh -i ~/.ssh/id_rsa louay@<YOUR_CONTABO_IP>
```

---

## STEP 2 — Run Diagnostic (MANDATORY first step)

```bash
# Upload the diagnostic script
scp docs/vps_diagnose.sh louay@<VPS_IP>:~/vps_diagnose.sh

# On the VPS:
chmod +x vps_diagnose.sh
bash vps_diagnose.sh 2>&1 | tee vps_report.txt
cat vps_report.txt
```

**Review the output** to confirm:
- [ ] Which ports the portfolio uses
- [ ] Whether Nginx is running
- [ ] Which ports are free (5433, 6380, 11435, 8001)
- [ ] Available disk space (need 4GB+)

> **If any agent port is already in use**: see [Changing Ports](#changing-ports) below.

---

## STEP 3 — One-Command Deployment

```bash
# On the VPS — one command does everything:
curl -fsSL https://raw.githubusercontent.com/louaycodes/Multi-agents-10-dollars-per-day-system/master/docs/deploy.sh | bash
```

**Or manually:**

```bash
# 1. Clone into isolated directory
cd ~
git clone https://github.com/louaycodes/Multi-agents-10-dollars-per-day-system.git agents-system
cd agents-system

# 2. Configure credentials
cp .env.example .env
nano .env   # Fill in your API keys

# 3. Build and start (isolated ports)
docker compose up -d --build

# 4. Wait for healthy status
sleep 30 && docker compose ps

# 5. Pull LLM model (one-time, ~4GB)
docker exec agents_ollama ollama pull mistral

# 6. Test immediately
docker exec agents_app python main.py --now
```

---

## STEP 4 — Configure `.env`

```bash
nano ~/agents-system/.env
```

**Minimum viable configuration** (replace with real values):

```env
# ── Database (managed by Docker — no change needed) ──────────
DB_HOST=postgres
DB_PORT=5432
DB_NAME=agents_db
DB_USER=postgres
DB_PASSWORD=change_this_to_a_strong_password

# ── LLM ──────────────────────────────────────────────────────
LLM_TYPE=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=mistral

# ── Niche: Gigs (Mturk) ──────────────────────────────────────
MTURK_ACCESS_KEY=your_aws_access_key
MTURK_SECRET_KEY=your_aws_secret_key
MTURK_SANDBOX=true            # Switch to false when ready for real HITs
MTURK_REGION=us-east-1

# ── Niche: Affiliate (Reddit) ─────────────────────────────────
REDDIT_CLIENT_ID=your_reddit_app_id
REDDIT_CLIENT_SECRET=your_reddit_secret
REDDIT_USERNAME=your_reddit_username
REDDIT_PASSWORD=your_reddit_password
REDDIT_USER_AGENT=MultiAgentSystem/1.0 by /u/your_username

# ── Niche: Reselling (Facebook Marketplace) ───────────────────
FACEBOOK_MARKETPLACE_ENABLED=false   # Enable after getting FB token
FACEBOOK_API_TOKEN=
FACEBOOK_PAGE_ID=

# ── System ────────────────────────────────────────────────────
TIMEZONE=UTC
LOG_LEVEL=INFO
DAILY_TARGET=10.0
```

> **Security**: `.env` is in `.gitignore` — it will NEVER be committed.

---

## STEP 5 — Verify Portfolio Still Running

```bash
# Check all Docker containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Test portfolio (adjust port/domain to yours)
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000   # backend
curl -s -o /dev/null -w "%{http_code}" http://localhost/        # frontend
curl -s -o /dev/null -w "%{http_code}" https://yourdomain.com  # public URL

# Verify agents containers are isolated (different network)
docker network inspect agents_network
docker network inspect agents_network | grep -i "containers" -A 20
```

Expected: **only** `agents_postgres`, `agents_redis`, `agents_ollama`, `agents_app` in `agents_network`. Portfolio containers in a different network.

---

## STEP 6 — Monitoring

### Real-time logs

```bash
cd ~/agents-system

# Follow all logs
docker compose logs -f

# App only
docker compose logs -f app

# Errors only
docker compose logs app | grep -i "error\|critical\|crashed"

# Transaction events
docker compose logs app | grep "TRANSACTION"
```

### Check P&L

```bash
# Today's revenue
docker exec agents_postgres psql -U postgres -d agents_db -c \
  "SELECT date, revenue_total, cost_total, profit, target_met
   FROM daily_results
   ORDER BY date DESC LIMIT 7;"

# Today's transactions
docker exec agents_postgres psql -U postgres -d agents_db -c \
  "SELECT niche, platform, agent_action, status, revenue, created_at
   FROM transactions
   WHERE created_at::date = CURRENT_DATE
   ORDER BY created_at DESC;"

# Active alerts
docker exec agents_postgres psql -U postgres -d agents_db -c \
  "SELECT alert_type, severity, message, created_at
   FROM alerts
   WHERE resolved = FALSE
   ORDER BY created_at DESC LIMIT 10;"
```

### System health

```bash
# Container resource usage
docker stats --no-stream

# Disk usage
df -h
docker system df

# Portfolio status check (weekly habit)
docker ps | grep -v agents
```

---

## ⚙️ Operations Cheat Sheet

```bash
# ── From ~/agents-system/ ─────────────────────────────────
# Start agents (portfolio unaffected)
docker compose up -d

# Stop agents (portfolio unaffected)
docker compose down

# Restart only the app (fastest)
docker compose restart app

# Force rebuild after code update
git pull && docker compose up -d --build

# Run a test cycle immediately
docker exec agents_app python main.py --now

# Run single agent
docker exec agents_app python main.py --agent manager
docker exec agents_app python main.py --agent hunter
docker exec agents_app python main.py --agent tracker

# Connect to agents database (NOT portfolio DB)
docker exec -it agents_postgres psql -U postgres -d agents_db

# Backup agents DB
bash docs/backup.sh
```

---

## Changing Ports (if conflict detected)

If the diagnostic found a port conflict, edit `docker-compose.yml` to change the external port:

```yaml
# Example: change agents Redis from 6380 to 6381
redis:
  ports:
    - "6381:6379"   # external:internal
```

Then restart: `docker compose up -d`

---

## Optional: Nginx URL Routing

If you want to expose the agents via a URL (e.g., `agents.yourdomain.com`):

```bash
# Copy template
sudo cp ~/agents-system/docs/nginx_agents.conf.template \
        /etc/nginx/sites-available/agents

# Edit domain name
sudo nano /etc/nginx/sites-available/agents
# Replace YOUR_DOMAIN.COM → yourdomain.com

# Enable
sudo ln -s /etc/nginx/sites-available/agents /etc/nginx/sites-enabled/agents

# Test config (CRITICAL — don't break portfolio Nginx)
sudo nginx -t

# If test passes, reload (zero downtime)
sudo nginx -s reload
```

---

## Daily Verification Checklist

```bash
# Run this every morning to confirm everything is healthy:
echo "=== $(date) ===" && \
docker compose -f ~/agents-system/docker-compose.yml ps && \
docker exec agents_postgres psql -U postgres -d agents_db -c \
  "SELECT date, profit, target_met FROM daily_results ORDER BY date DESC LIMIT 1;" && \
curl -s -o /dev/null -w "Portfolio HTTP: %{http_code}\n" http://localhost/
```

---

## Uninstall (if ever needed)

```bash
# Stop and remove agents containers, networks, volumes
cd ~/agents-system
docker compose down -v    # -v removes volumes (DATA DELETED)

# Remove agent images
docker rmi agents_app

# Remove directory
rm -rf ~/agents-system
```

> **Note**: This only affects agents. Portfolio remains completely unaffected.
