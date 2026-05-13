#!/bin/bash
# deploy/install_fs.sh
set -euo pipefail

FS_VERSION="1.10.12"
FS_DIR="/usr/local/freeswitch"
CONF_DIR="${FS_DIR}/conf"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== 安装 FreeSWITCH 依赖 ==="
apt-get update && apt-get install -y \
  build-essential automake autoconf libtool wget git \
  libncurses5-dev libssl-dev libcurl4-openssl-dev \
  libjpeg-dev libsqlite3-dev libpcre3-dev libspeexdsp-dev \
  libldns-dev libedit-dev libtiff5-dev yasm uuid-dev

echo "=== 编译安装 FreeSWITCH ==="
cd /usr/local/src
git clone -b v${FS_VERSION} https://github.com/signalwire/freeswitch.git freeswitch-${FS_VERSION}
cd freeswitch-${FS_VERSION}
./bootstrap.sh -j
# 启用必要模块
sed -i 's|#mod_unimrcp|mod_unimrcp|' modules.conf
sed -i 's|#mod_event_socket|mod_event_socket|' modules.conf
./configure --prefix=${FS_DIR}
make -j$(nproc)
make install

echo "=== 部署配置文件 ==="
cp "${PROJECT_DIR}/freeswitch/modules.conf" "${CONF_DIR}/autoload_modules/modules.conf"
cp "${PROJECT_DIR}/freeswitch/vars.xml" "${CONF_DIR}/vars.xml"
cp "${PROJECT_DIR}/freeswitch/event_socket.conf.xml" "${CONF_DIR}/autoload_configs/event_socket.conf.xml"
cp "${PROJECT_DIR}/freeswitch/unimrcp.conf.xml" "${CONF_DIR}/autoload_configs/unimrcp.conf.xml"
cp "${PROJECT_DIR}/freeswitch/dialplan/public.xml" "${CONF_DIR}/dialplan/public.xml"

echo "=== 验证 ==="
${FS_DIR}/bin/fs_cli -x "show modules" | grep -E "mod_sofia|mod_unimrcp|mod_event_socket|mod_dptools"
echo "=== FreeSWITCH 安装完成 ==="
