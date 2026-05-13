#!/bin/bash
# deploy/install_unimrcp.sh
set -euo pipefail

UNIMRCP_DIR="/usr/local/unimrcp"
CONF_DIR="/etc/unimrcp"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== 安装 UniMRCP 依赖 ==="
apt-get update && apt-get install -y \
  build-essential automake autoconf libtool \
  libsofia-sip-ua-dev libssl-dev libcurl4-openssl-dev

echo "=== 编译安装 UniMRCP ==="
cd /usr/local/src
git clone https://github.com/unispeech/unimrcp.git
cd unimrcp
./bootstrap
./configure --prefix=${UNIMRCP_DIR}
make -j$(nproc)
make install

echo "=== 部署配置 ==="
mkdir -p ${CONF_DIR}
cp "${PROJECT_DIR}/freeswitch/unimrcp/unimrcpserver.xml" "${CONF_DIR}/unimrcpserver.xml"

echo "=== 验证 ==="
${UNIMRCP_DIR}/bin/unimrcpserver --version
echo "=== UniMRCP 安装完成 ==="
