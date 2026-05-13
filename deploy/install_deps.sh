#!/bin/bash
# deploy/install_deps.sh
set -euo pipefail

echo "=== 安装 Redis ==="
apt-get update && apt-get install -y redis-server
systemctl enable redis-server
systemctl start redis-server
redis-cli ping

echo "=== 安装 MinIO ==="
wget -q https://dl.min.io/server/minio/release/linux-amd64/minio -O /usr/local/bin/minio
chmod +x /usr/local/bin/minio
useradd -r -s /bin/false minio || true
mkdir -p /data/minio
chown minio:minio /data/minio

# 创建 systemd 服务
cat > /etc/systemd/system/minio.service << 'EOS'
[Unit]
Description=MinIO
After=network.target

[Service]
User=minio
Group=minio
ExecStart=/usr/local/bin/minio server /data/minio --console-address ":9001"
Restart=always
Environment=MINIO_ROOT_USER=admin
Environment=MINIO_ROOT_PASSWORD=changeme123

[Install]
WantedBy=multi-user.target
EOS

systemctl daemon-reload
systemctl enable minio
systemctl start minio

echo "=== 创建 MinIO buckets ==="
sleep 3
mc alias set local http://localhost:9000 admin changeme123 2>/dev/null || true
mc mb local/rec-cs 2>/dev/null || true
mc mb local/rec-collection 2>/dev/null || true
mc mb local/rec-marketing 2>/dev/null || true

echo "=== 依赖服务安装完成 ==="
