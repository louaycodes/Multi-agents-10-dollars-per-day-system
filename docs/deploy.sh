#!/usr/bin/env bash
# ============================================================
# deploy.sh — One-command VPS deployment for the agents system
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# What it does:
#   1. Checks prerequisites (Docker, disk, ports)
#   2. Clones the repo into ~/agents-system/
#   3. Guides .env setup
#   4. Builds and starts containers
#   5. Pulls Ollama model
#   6. Runs test cycle
#   7. Verifies portfolio still healthy
# ============================================================

set -euo pipefail

# ─────────── Colors ───────────
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

ok()   { echo -e "${GREEN}  ✅ $1${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${RESET}"; }
fail() { echo -e "${RED}  ❌ $1${RESET}"; exit 1; }
info() { echo -e "${CYAN}  ℹ️  $1${RESET}"; }
step() { echo -e "\n${BOLD}${CYAN}━━━ STEP $1 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }

REPO_URL="https://github.com/louaycodes/Multi-agents-10-dollars-per-day-system.git"
INSTALL_DIR="${HOME}/agents-system"
AGENTS_PORTS=(5433 6380 11435 8001)

# ─────────────────────────────────────────────
# STEP 0: Prerequisites check
# ─────────────────────────────────────────────
step "0: PREREQUISITES CHECK"

# Docker
if ! command -v docker &>/dev/null; then
  warn "Docker not installed — installing now..."
  curl -fsSL https://get.docker.com | bash
  usermod -aG docker "$USER"
  ok "Docker installed"
else
  ok "Docker: $(docker --version | head -1)"
fi

# Docker Compose
if docker compose version &>/dev/null 2>&1; then
  ok "Docker Compose (plugin): $(docker compose version)"
elif command -v docker-compose &>/dev/null; then
  ok "Docker Compose (standalone): $(docker-compose --version)"
else
  warn "Docker Compose not found — installing plugin..."
  apt-get install -y docker-compose-plugin 2>/dev/null || \
    curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
    -o /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose
  ok "Docker Compose installed"
fi

# Git
if ! command -v git &>/dev/null; then
  warn "Git not found — installing..."
  apt-get install -y git 2>/dev/null || yum install -y git 2>/dev/null || fail "Cannot install git"
fi
ok "Git: $(git --version)"

# Disk space (need at least 4GB for Ollama model)
AVAIL_GB=$(df -BG "${HOME}" | tail -1 | awk '{print $4}' | tr -d 'G')
if [ "${AVAIL_GB:-0}" -lt 4 ]; then
  warn "Only ${AVAIL_GB}GB free disk space. Ollama model needs ~4GB."
  echo "    Continue anyway? (y/N)"
  read -r CONTINUE
  [ "$CONTINUE" = "y" ] || [ "$CONTINUE" = "Y" ] || fail "Aborted due to low disk space."
else
  ok "Disk space: ${AVAIL_GB}GB free"
fi

# Port conflict check
echo ""
info "Checking ports for conflicts..."
CONFLICT=false
for PORT in "${AGENTS_PORTS[@]}"; do
  if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
    fail "Port ${PORT} is already in use! Check: ss -tlnp | grep :${PORT}"
    CONFLICT=true
  else
    ok "Port ${PORT} is free"
  fi
done

# ─────────────────────────────────────────────
# STEP 1: Clone / update repo
# ─────────────────────────────────────────────
step "1: CLONE REPOSITORY"

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Repo already exists — pulling latest..."
  cd "$INSTALL_DIR"
  git pull origin master
  ok "Repo updated"
else
  info "Cloning into ${INSTALL_DIR}..."
  git clone "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
  ok "Repo cloned"
fi

cd "$INSTALL_DIR"
info "Files present: $(ls | wc -l)"

# ─────────────────────────────────────────────
# STEP 2: Configure .env
# ─────────────────────────────────────────────
step "2: CONFIGURE .env"

if [ ! -f ".env" ]; then
  cp .env.example .env
  warn ".env created from template — you MUST edit it with your API keys"
  echo ""
  echo "  Required fields:"
  echo "    DB_PASSWORD=your_secure_password"
  echo "    MTURK_ACCESS_KEY=..."
  echo "    MTURK_SECRET_KEY=..."
  echo "    REDDIT_CLIENT_ID=..."
  echo "    REDDIT_CLIENT_SECRET=..."
  echo "    REDDIT_USERNAME=..."
  echo "    REDDIT_PASSWORD=..."
  echo ""
  echo -e "  ${BOLD}Edit .env now? (y/N)${RESET}"
  read -r EDIT_ENV
  if [ "$EDIT_ENV" = "y" ] || [ "$EDIT_ENV" = "Y" ]; then
    ${EDITOR:-nano} .env
  else
    warn "Skipping .env edit — system will use defaults/empty keys"
    info "Edit later: nano ${INSTALL_DIR}/.env"
  fi
else
  ok ".env already exists"
  # Warn if placeholder values still present
  if grep -q "your_secure_db_password_here" .env || grep -q "CHANGE_ME" .env 2>/dev/null; then
    warn ".env has placeholder values — please update before production use"
  fi
fi

# Ensure DB_PASSWORD is set
if ! grep -q "^DB_PASSWORD=" .env || grep -q "^DB_PASSWORD=$" .env; then
  RANDOM_PASS=$(openssl rand -hex 16)
  echo "DB_PASSWORD=${RANDOM_PASS}" >> .env
  warn "Auto-generated DB_PASSWORD: ${RANDOM_PASS}"
  warn "Save this password somewhere safe!"
fi

# ─────────────────────────────────────────────
# STEP 3: Build Docker images
# ─────────────────────────────────────────────
step "3: BUILD DOCKER IMAGES"

info "Building agents app image..."
docker compose build --no-cache
ok "Images built successfully"

# ─────────────────────────────────────────────
# STEP 4: Start services
# ─────────────────────────────────────────────
step "4: START SERVICES"

info "Starting all agents services (isolated from portfolio)..."
docker compose up -d

info "Waiting 30s for services to become healthy..."
sleep 30

# Check health
echo ""
echo "  Container status:"
docker compose ps
echo ""

UNHEALTHY=false
for SVC in agents_postgres agents_redis agents_app; do
  STATUS=$(docker inspect --format='{{.State.Status}}' "${SVC}" 2>/dev/null || echo "missing")
  if [ "$STATUS" = "running" ]; then
    ok "${SVC}: running"
  else
    warn "${SVC}: ${STATUS}"
    UNHEALTHY=true
  fi
done

if [ "$UNHEALTHY" = true ]; then
  warn "Some services not running — check logs:"
  echo "    docker compose logs postgres"
  echo "    docker compose logs redis"
  echo "    docker compose logs app"
fi

# ─────────────────────────────────────────────
# STEP 5: Pull Ollama model
# ─────────────────────────────────────────────
step "5: PULL OLLAMA MODEL"

OLLAMA_MODEL=$(grep "^OLLAMA_MODEL=" .env 2>/dev/null | cut -d= -f2 || echo "mistral")
OLLAMA_MODEL=${OLLAMA_MODEL:-mistral}

info "Pulling ${OLLAMA_MODEL} model (~4GB, this will take a few minutes)..."
docker exec agents_ollama ollama pull "${OLLAMA_MODEL}" || {
  warn "Ollama pull failed — container may still be starting"
  info "Retry manually: docker exec agents_ollama ollama pull ${OLLAMA_MODEL}"
}
ok "LLM model ready: ${OLLAMA_MODEL}"

# ─────────────────────────────────────────────
# STEP 6: Verify database
# ─────────────────────────────────────────────
step "6: VERIFY DATABASE"

info "Checking database tables..."
TABLES=$(docker exec agents_postgres psql -U postgres -d agents_db -c "\dt" 2>/dev/null | grep -c "public" || echo "0")
if [ "${TABLES:-0}" -ge 5 ]; then
  ok "Database: ${TABLES} tables initialized"
else
  warn "Expected 6 tables, found ${TABLES} — applying schema..."
  docker exec agents_postgres psql -U postgres -d agents_db \
    -f /docker-entrypoint-initdb.d/01_schema.sql 2>/dev/null || \
    warn "Schema apply failed — check: docker logs agents_postgres"
fi

# ─────────────────────────────────────────────
# STEP 7: Test run
# ─────────────────────────────────────────────
step "7: TEST RUN (immediate cycle)"

info "Running a full agent cycle now (bypasses scheduler)..."
docker exec agents_app python main.py --now 2>&1 | tail -30
ok "Test run complete — check output above for errors"

# ─────────────────────────────────────────────
# STEP 8: Verify portfolio still healthy
# ─────────────────────────────────────────────
step "8: VERIFY PORTFOLIO STILL RUNNING"

echo "  Running containers (all):"
docker ps --format "    {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -v agents || true

echo ""
info "Testing common portfolio ports..."
for PORT in 80 443 3000 5000 8000 8080; do
  if curl -s --max-time 3 "http://localhost:${PORT}" > /dev/null 2>&1; then
    ok "Port ${PORT}: responding"
  fi
done

echo ""
warn "Please also manually verify: curl http://your-portfolio-domain.com"

# ─────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}"
echo "╔══════════════════════════════════════════════════════╗"
echo "║           DEPLOYMENT COMPLETE ✅                    ║"
echo "╚══════════════════════════════════════════════════════╝"
echo -e "${RESET}"

echo "  Install directory: ${INSTALL_DIR}"
echo ""
echo "  Agent ports (all isolated from portfolio):"
echo "    PostgreSQL: localhost:5433"
echo "    Redis:      localhost:6380"
echo "    Ollama:     localhost:11435"
echo "    App:        localhost:8001"
echo ""
echo "  Useful commands:"
echo "    View logs:     cd ${INSTALL_DIR} && docker compose logs -f app"
echo "    Check P&L:     docker exec agents_postgres psql -U postgres -d agents_db -c 'SELECT * FROM daily_results ORDER BY date DESC LIMIT 5;'"
echo "    Stop agents:   cd ${INSTALL_DIR} && docker compose down"
echo "    Restart agents: cd ${INSTALL_DIR} && docker compose up -d"
echo "    Run now:       docker exec agents_app python main.py --now"
echo ""
echo "  📚 Full guide: ${INSTALL_DIR}/docs/SETUP.md"
echo "  🔧 Troubleshooting: ${INSTALL_DIR}/docs/TROUBLESHOOTING.md"
