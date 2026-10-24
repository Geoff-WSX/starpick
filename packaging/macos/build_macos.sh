#!/usr/bin/env bash
# macOS 一键打包：前端构建 → PyInstaller(.app) → 冻结自检 → dmg。
#
# 用法：packaging/macos/build_macos.sh [版本号]
# 产物：packaging/dist/StarPick.app 与 packaging/dist/StarPick-<版本>-<架构>.dmg
#
# 说明：
# - 打包用独立的干净 venv（packaging/.venv-build），不用开发用的 Anaconda venv
#   （PyInstaller 官方不支持 Anaconda Python）。
# - 未做代码签名/公证：本机自用不受影响；从网络分发时首次打开需右键 → 打开。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-1.0.0}"
ARCH="$(uname -m)"
BUILD_VENV="$ROOT/packaging/.venv-build"
APP="$ROOT/packaging/dist/StarPick.app"
DMG="$ROOT/packaging/dist/StarPick-${VERSION}-${ARCH}.dmg"

echo "==> 1/5 构建前端（frontend/dist）"
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
npm run build

echo "==> 2/5 准备打包环境：$BUILD_VENV"
if [ ! -x "$BUILD_VENV/bin/python" ]; then
  uv venv --python 3.10 "$BUILD_VENV"
  VIRTUAL_ENV="$BUILD_VENV" uv pip install \
    -r "$ROOT/backend/requirements.txt" \
    -r "$ROOT/backend/requirements-desktop.txt" \
    pyinstaller pillow
fi

echo "==> 3/5 PyInstaller 构建 StarPick.app"
cd "$ROOT"
VIRTUAL_ENV="$BUILD_VENV" "$BUILD_VENV/bin/pyinstaller" --noconfirm --clean \
  --distpath "$ROOT/packaging/dist" \
  --workpath "$ROOT/packaging/build" \
  "$ROOT/packaging/starpick.spec"

echo "==> 4/5 冻结包冷启动自检（--selftest，独立临时数据目录）"
SELFTEST_DATA="$(mktemp -d /tmp/starpick-frozen-selftest.XXXXXX)"
STARPICK_DATA_DIR="$SELFTEST_DATA" "$APP/Contents/MacOS/StarPick" --selftest

echo "==> 5/5 打 dmg（含 Applications 拖拽安装快捷方式）"
STAGE="$(mktemp -d /tmp/starpick-dmg-stage.XXXXXX)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "STARPICK $VERSION" -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG"

echo
echo "==> 完成"
echo "    App: $APP"
echo "    DMG: $DMG"
echo "    提示：从网络下载的未签名应用首次打开需「右键 → 打开」放行（或系统设置 → 隐私与安全性 → 仍要打开）"
