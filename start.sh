#!/bin/bash
# STARPICK 拾星 · 一键启动（macOS 版）
cd "$(dirname "$0")"

echo "============================================"
echo "   STARPICK 拾星 - 一键启动 (macOS)"
echo "============================================"
echo

# ---------- 1. 检查 Python 3.10+ ----------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未找到 python3，请安装 Python 3.10 或更高版本。"
    exit 1
fi

# ---------- 2. 检查/创建虚拟环境并安装后端依赖 ----------
if [ ! -d "backend/.venv" ]; then
    echo "[安装] 创建 Python 虚拟环境..."
    python3 -m venv backend/.venv || { echo "[错误] venv 创建失败"; exit 1; }
fi
if ! backend/.venv/bin/python -c "import fastapi, uvicorn, apscheduler, requests, yaml" >/dev/null 2>&1; then
    echo "[安装] 检测到后端依赖缺失，正在安装（首次约 1-2 分钟）..."
    backend/.venv/bin/python -m pip install -q -r backend/requirements.txt || { echo "[错误] 依赖安装失败"; exit 1; }
fi

# ---------- 3. 检查/构建前端 ----------
if [ ! -f "frontend/dist/index.html" ]; then
    echo "[检查] 未找到前端构建产物，开始构建..."
    command -v npm >/dev/null 2>&1 || { echo "[错误] 需要 Node.js 18+ 构建前端"; exit 1; }
    (cd frontend && npm install --no-audit --no-fund && npm run build) || { echo "[错误] 前端构建失败"; exit 1; }
fi

# ---------- 4. 检查 .env（可选） ----------
if [ ! -f "backend/.env" ] && [ ! -f "backend/.runtime_token" ]; then
    echo "[提示] 未配置 GITHUB_TOKEN（backend/.env 不存在，且未通过前端设置页保存 Token）。"
    echo "        未认证模式仍可运行，但限流较严（搜索 10 次/分钟）。"
    echo "        方式一：启动后点击顶栏【设置】粘贴 Token，保存即热生效（推荐，无需重启）。"
    echo "        方式二：在 backend/.env 中配置 GITHUB_TOKEN 后重启，见 README.md。"
    echo
fi

# ---------- 5. 端口占用检查 ----------
if lsof -i :8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[提示] 检测到端口 8000 已有服务在运行：http://127.0.0.1:8000"
    echo "        无需重复启动，直接打开浏览器使用即可。"
    echo "        如需重启服务，请先关闭旧实例再运行本脚本："
    echo "        pkill -f 'uvicorn app.main:app'"
    echo
    sleep 1
    open "http://127.0.0.1:8000"
    exit 0
fi

# ---------- 5.5 日志轮转：保留最近 3 份 uvicorn.log（nohup 后台启动场景） ----------
if [ -f "backend/uvicorn.log" ]; then
    for i in 3 2 1; do
        prev=$((i - 1))
        [ -f "backend/uvicorn.log.$prev" ] && mv -f "backend/uvicorn.log.$prev" "backend/uvicorn.log.$i"
    done
    mv -f "backend/uvicorn.log" "backend/uvicorn.log.1"
    echo "[日志] 已轮转 uvicorn.log（保留最近 3 份）"
fi

# ---------- 6. 启动 ----------
echo "[启动] 服务地址: http://127.0.0.1:8000"
echo "        浏览器将自动打开，Ctrl+C 停止服务。"
echo
sleep 1
open "http://127.0.0.1:8000"
cd backend
exec ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
