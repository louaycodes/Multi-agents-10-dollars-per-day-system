#!/usr/bin/env bash
# ============================================================
# backup.sh — Agents database + logs backup script
#
# Usage:
#   chmod +x backup.sh
#   ./backup.sh              # Manual backup
#   # Or add to crontab for daily auto-backup:
#   # 0 2 * * * /home/louay/agents-system/docs/backup.sh >> /home/louay/agents-system/logs/backup.log 2>&1
# ============================================================

INSTALL_DIR="${HOME}/agents-system"
BACKUP_DIR="${INSTALL_DIR}/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup..."

# 1. Dump agents PostgreSQL database
echo "[$(date)] Backing up agents database..."
docker exec agents_postgres pg_dump -U postgres agents_db \
  > "${BACKUP_DIR}/agents_db_${DATE}.sql" && \
  echo "[$(date)] DB backup: ${BACKUP_DIR}/agents_db_${DATE}.sql" || \
  echo "[$(date)] ERROR: DB backup failed"

# 2. Compress logs
echo "[$(date)] Archiving logs..."
tar -czf "${BACKUP_DIR}/logs_${DATE}.tar.gz" -C "${INSTALL_DIR}" logs/ 2>/dev/null && \
  echo "[$(date)] Logs backup: ${BACKUP_DIR}/logs_${DATE}.tar.gz" || \
  echo "[$(date)] WARNING: logs archive failed"

# 3. Rotate old backups (keep last 7)
echo "[$(date)] Rotating old backups (keeping last 7)..."
ls -t "${BACKUP_DIR}"/agents_db_*.sql 2>/dev/null | tail -n +8 | xargs rm -f
ls -t "${BACKUP_DIR}"/logs_*.tar.gz 2>/dev/null | tail -n +8 | xargs rm -f

# 4. Show backup sizes
echo "[$(date)] Current backups:"
ls -lh "${BACKUP_DIR}" 2>/dev/null

echo "[$(date)] Backup complete."
