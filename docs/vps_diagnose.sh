#!/usr/bin/env bash
# ============================================================
# vps_diagnose.sh
# Run on the VPS to detect all running services, ports, and
# Docker resources BEFORE deploying the agents system.
#
# Usage:
#   chmod +x vps_diagnose.sh
#   bash vps_diagnose.sh
# ============================================================

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

divider() { echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }
header() { divider; echo -e "${BOLD}$1${RESET}"; }
ok() { echo -e "${GREEN}  ✅ $1${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${RESET}"; }
fail() { echo -e "${RED}  ❌ $1${RESET}"; }

echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║   VPS PRE-DEPLOYMENT DIAGNOSTIC TOOL    ║"
echo "║   Autonomous Multi-Agent System         ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${RESET}"

# ─────────────────────────────────────────────
# 1. System Resources
# ─────────────────────────────────────────────
header "1. SYSTEM RESOURCES"

echo "  OS:"
cat /etc/os-release | grep -E 'PRETTY_NAME|VERSION_ID' | sed 's/^/    /'

echo ""
echo "  CPU cores: $(nproc)"

echo ""
echo "  Memory (RAM):"
free -h | sed 's/^/    /'

echo ""
echo "  Disk space:"
df -h --output=source,size,used,avail,pcent,target | grep -v 'tmpfs\|udev\|loop' | sed 's/^/    /'

# Check if we have 4GB+ free
AVAIL_GB=$(df -BG / | tail -1 | awk '{print $4}' | tr -d 'G')
if [ "${AVAIL_GB:-0}" -ge 4 ]; then
  ok "Disk space OK (${AVAIL_GB}GB free, need 4GB for Ollama model)"
else
  warn "Low disk space (${AVAIL_GB}GB free) — Ollama model requires ~4GB"
fi

# ─────────────────────────────────────────────
# 2. Ports currently in use
# ─────────────────────────────────────────────
header "2. PORTS IN USE"

echo "  Listening ports:"
if command -v ss &>/dev/null; then
  ss -tlnp 2>/dev/null | grep LISTEN | awk '{print "    " $4, $6}' | sort
elif command -v netstat &>/dev/null; then
  netstat -tlnp 2>/dev/null | grep LISTEN | awk '{print "    " $4, $7}' | sort
else
  warn "ss/netstat not available — trying lsof"
  lsof -i -P -n 2>/dev/null | grep LISTEN | awk '{print "    " $1, $9}' | sort || true
fi

echo ""
echo "  Checking agents target ports:"
for PORT in 5433 6380 11435 8001; do
  if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
    fail "Port ${PORT} ALREADY IN USE — will need to change!"
  else
    ok "Port ${PORT} is FREE"
  fi
done

echo ""
echo "  Checking common conflict ports:"
for PORT in 5432 6379 11434 80 443 3000 5000 8000 8080; do
  STATUS=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | awk '{print $6}' | head -1)
  if [ -n "$STATUS" ]; then
    warn "Port ${PORT} in use: $STATUS"
  else
    ok "Port ${PORT} is free"
  fi
done

# ─────────────────────────────────────────────
# 3. Running Processes
# ─────────────────────────────────────────────
header "3. RUNNING SERVICES"

echo "  Systemd services:"
systemctl list-units --type=service --state=running 2>/dev/null \
  | grep -v '@\|getty\|dbus\|multiplex\|selinux\|network\|locale\|journald\|syslog\|cron\|rsyslog' \
  | head -30 | sed 's/^/    /' || warn "systemctl not available"

echo ""
echo "  PM2 processes (Node):"
if command -v pm2 &>/dev/null; then
  pm2 list 2>/dev/null | sed 's/^/    /' || warn "pm2 list failed"
else
  echo "    PM2 not installed"
fi

echo ""
echo "  Node processes:"
pgrep -a node 2>/dev/null | sed 's/^/    /' || echo "    None"

echo ""
echo "  Python processes:"
pgrep -a python 2>/dev/null | sed 's/^/    /' || pgrep -a python3 2>/dev/null | sed 's/^/    /' || echo "    None"

echo ""
echo "  Nginx status:"
if systemctl is-active nginx &>/dev/null; then
  ok "Nginx is running"
  echo "  Nginx config files found:"
  ls /etc/nginx/sites-enabled/ 2>/dev/null | sed 's/^/    /' || ls /etc/nginx/conf.d/ 2>/dev/null | sed 's/^/    /' || echo "    None found"
elif command -v nginx &>/dev/null; then
  warn "Nginx installed but NOT running"
else
  echo "    Nginx not installed"
fi

# ─────────────────────────────────────────────
# 4. Docker Status
# ─────────────────────────────────────────────
header "4. DOCKER STATUS"

if command -v docker &>/dev/null; then
  ok "Docker is installed: $(docker --version)"

  echo ""
  echo "  Running containers:"
  docker ps --format "    {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || warn "Docker not running or no permissions"

  echo ""
  echo "  All containers (including stopped):"
  docker ps -a --format "    {{.Names}}\t{{.Status}}" 2>/dev/null | head -20 || true

  echo ""
  echo "  Docker networks:"
  docker network ls 2>/dev/null | sed 's/^/    /' || true

  echo ""
  echo "  Docker volumes:"
  docker volume ls 2>/dev/null | sed 's/^/    /' || true

  echo ""
  echo "  Docker disk usage:"
  docker system df 2>/dev/null | sed 's/^/    /' || true

else
  fail "Docker is NOT installed"
  echo "  Install: curl -fsSL https://get.docker.com | bash"
fi

if command -v docker-compose &>/dev/null || docker compose version &>/dev/null 2>&1; then
  ok "Docker Compose available"
else
  warn "Docker Compose not available"
  echo "    Install: apt install docker-compose-plugin"
fi

# ─────────────────────────────────────────────
# 5. Nginx Config Inspection
# ─────────────────────────────────────────────
header "5. NGINX CONFIGURATION"

NGINX_FOUND=false
for NGINX_DIR in /etc/nginx/sites-enabled /etc/nginx/conf.d /etc/nginx/vhost.d; do
  if [ -d "$NGINX_DIR" ] && [ "$(ls -A $NGINX_DIR 2>/dev/null)" ]; then
    echo "  Files in $NGINX_DIR:"
    for f in "$NGINX_DIR"/*; do
      echo -e "\n    ${CYAN}── $f${RESET}"
      cat "$f" 2>/dev/null | grep -E 'server_name|listen|proxy_pass|root' | sed 's/^/      /' || true
    done
    NGINX_FOUND=true
  fi
done

if [ "$NGINX_FOUND" = false ]; then
  echo "  No Nginx config files found in standard locations"
fi

# ─────────────────────────────────────────────
# 6. Existing Portfolio Detection
# ─────────────────────────────────────────────
header "6. PORTFOLIO DETECTION"

echo "  Searching for portfolio directories:"
for DIR in /home/*/portfolio /home/*/app /home/*/project /var/www/* /root/portfolio /root/app; do
  if [ -d "$DIR" ]; then
    echo -e "    ${GREEN}Found:${RESET} $DIR"
    ls "$DIR" 2>/dev/null | head -10 | sed 's/^/      /'
    # Check for docker-compose
    if [ -f "$DIR/docker-compose.yml" ] || [ -f "$DIR/docker-compose.yaml" ]; then
      ok "Has docker-compose.yml"
      cat "$DIR/docker-compose.yml" 2>/dev/null | grep -E 'container_name:|ports:|image:' | sed 's/^/      /'
    fi
    # Check for package.json (Node)
    if [ -f "$DIR/package.json" ]; then
      ok "Node.js project detected"
    fi
    # Check for .env
    if [ -f "$DIR/.env" ]; then
      warn "Has .env file (NOT showing contents)"
    fi
  fi
done

echo ""
echo "  Home directory contents:"
ls -la /home/ 2>/dev/null | sed 's/^/    /'
ls -la /root/ 2>/dev/null | grep -v '\.$' | head -20 | sed 's/^/    /' || true

# ─────────────────────────────────────────────
# 7. Database Detection
# ─────────────────────────────────────────────
header "7. DATABASE DETECTION"

echo "  PostgreSQL:"
if systemctl is-active postgresql &>/dev/null; then
  ok "PostgreSQL running (systemd)"
  sudo -u postgres psql -c "\l" 2>/dev/null | sed 's/^/    /' || warn "Cannot list databases (no sudo or wrong user)"
elif docker ps 2>/dev/null | grep -qi postgres; then
  ok "PostgreSQL running in Docker"
  docker ps --format "    {{.Names}}: {{.Ports}}" 2>/dev/null | grep -i postgres || true
else
  echo "    PostgreSQL: not detected"
fi

echo ""
echo "  MySQL/MariaDB:"
if systemctl is-active mysql &>/dev/null || systemctl is-active mariadb &>/dev/null; then
  ok "MySQL/MariaDB running (systemd)"
else
  echo "    MySQL/MariaDB: not detected"
fi

echo ""
echo "  MongoDB:"
if systemctl is-active mongod &>/dev/null; then
  ok "MongoDB running (systemd)"
else
  echo "    MongoDB: not detected"
fi

# ─────────────────────────────────────────────
# 8. Firewall Status
# ─────────────────────────────────────────────
header "8. FIREWALL STATUS"

if command -v ufw &>/dev/null; then
  echo "  UFW status:"
  ufw status 2>/dev/null | sed 's/^/    /' || warn "Cannot read UFW status (try: sudo ufw status)"
else
  echo "  UFW not installed"
fi

if command -v iptables &>/dev/null; then
  echo ""
  echo "  iptables rules (INPUT chain, first 10):"
  iptables -L INPUT -n 2>/dev/null | head -15 | sed 's/^/    /' || warn "Cannot read iptables (need root)"
fi

# ─────────────────────────────────────────────
# 9. Summary & Recommendations
# ─────────────────────────────────────────────
header "9. DEPLOYMENT READINESS SUMMARY"

echo ""
echo -e "  ${BOLD}Copy-paste this info and send it to proceed:${RESET}"
echo ""
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │ PORTFOLIO BACKEND PORT: [see section 2 above]   │"
echo "  │ PORTFOLIO DB TYPE: [see section 7 above]        │"  
echo "  │ NGINX: [running/not running]                    │"
echo "  │ DOCKER: [installed/not installed]               │"
echo "  │ FREE DISK: [see section 1 above]                │"
echo "  │ FREE RAM: [see section 1 above]                 │"
echo "  └─────────────────────────────────────────────────┘"
echo ""
ok "Diagnostic complete. Share the output above."
