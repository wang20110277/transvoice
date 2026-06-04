#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FREESWITCH_INCLUDE_DIR="/Users/lindaw/freeswitch/include/freeswitch"
FREESWITCH_LIBRARY="/Users/lindaw/freeswitch/lib/libfreeswitch.dylib"
FREESWITCH_MOD_DIR="/Users/lindaw/freeswitch/mod"

rm -rf build
mkdir -p build
cd build

# 关键：
# 1. BOOST_ROOT 指向 brew boost
# 2. 关掉新的 BoostConfig 模式
# 3. 直接给头文件/库路径
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DFREESWITCH_INCLUDE_DIR="${FREESWITCH_INCLUDE_DIR}" \
  -DFREESWITCH_LIBRARY="${FREESWITCH_LIBRARY}" \
  -DBOOST_ROOT=/opt/homebrew \
  -DBoost_NO_BOOST_CMAKE=ON \
  -DBoost_INCLUDE_DIR=/opt/homebrew/include \
  -DBoost_LIBRARY_DIR=/opt/homebrew/lib \
  -DCMAKE_CXX_FLAGS="-Wno-dev"

make -j$(sysctl -n hw.ncpu)

cp -f mod_audio_fork.so "${FREESWITCH_MOD_DIR}/"
chmod 755 "${FREESWITCH_MOD_DIR}/mod_audio_fork.so"

echo "✅ Done"
