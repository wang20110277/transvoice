#!/bin/bash
# deploy/install_all.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "===== 智能外呼系统 一键安装 ====="

echo "[1/5] 安装依赖服务 (Redis + MinIO)..."
bash "${SCRIPT_DIR}/install_deps.sh"

echo "[2/5] 安装 FreeSWITCH..."
bash "${SCRIPT_DIR}/install_fs.sh"

echo "[3/5] 安装 UniMRCP..."
bash "${SCRIPT_DIR}/install_unimrcp.sh"

echo "[4/5] 初始化数据库..."
export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGDATABASE="${PGDATABASE:-callbot}"
psql -c "CREATE DATABASE callbot;" 2>/dev/null || true
psql -d callbot -f "${SCRIPT_DIR}/init_db.sql"

echo "[5/5] 验证安装..."
echo "--- FreeSWITCH ---"
fs_cli -x "show modules" 2>/dev/null | head -5 || echo "FreeSWITCH 未运行（需手动启动）"
echo "--- UniMRCP ---"
systemctl is-active unimrcp 2>/dev/null || echo "UniMRCP 未运行（需手动启动）"
echo "--- Redis ---"
redis-cli ping
echo "--- MinIO ---"
systemctl is-active minio 2>/dev/null || echo "MinIO 未运行"
echo "--- PostgreSQL ---"
psql -d callbot -c "\dt callbot.*" 2>/dev/null | head -15

echo "===== 安装完成 ====="
