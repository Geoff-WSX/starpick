@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   GitHub 好项目发现器 - 一键启动
echo ============================================
echo.

REM ---------- 1. 检查 Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10 或更高版本并加入 PATH。
    echo        下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ---------- 2. 检查/安装后端依赖 ----------
python -c "import fastapi, uvicorn, apscheduler, requests, yaml" >nul 2>nul
if errorlevel 1 (
    echo [安装] 检测到后端依赖缺失，正在安装（首次运行约需 1-2 分钟）...
    python -m pip install -r backend\requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

REM ---------- 3. 检查/构建前端 ----------
if not exist "frontend\dist\index.html" (
    echo [检查] 未找到前端构建产物，开始构建...
    where npm >nul 2>nul
    if errorlevel 1 (
        echo [错误] 需要 Node.js 来构建前端。请安装 Node.js 18+ 后重试。
        echo        下载: https://nodejs.org/
        pause
        exit /b 1
    )
    cd frontend
    call npm install >nul 2>nul
    call npm run build
    if errorlevel 1 (
        echo [错误] 前端构建失败，请查看上方错误信息。
        cd ..
        pause
        exit /b 1
    )
    cd ..
)

REM ---------- 4. 检查 .env（可选） ----------
if not exist "backend\.env" (
    echo [提示] 未配置 GITHUB_TOKEN（backend\.env 不存在）。
    echo        未认证模式仍可运行，但限流较严（搜索 10 次/分钟）。
    echo        建议在 backend\.env 中配置 Token 后重启，见 README.md。
    echo.
)

REM ---------- 5. 端口占用检查 ----------
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [警告] 端口 8000 已被占用，可能已有实例在运行，请先关闭旧实例。
    pause
    exit /b 1
)

REM ---------- 6. 启动 ----------
echo [启动] 服务地址: http://127.0.0.1:8000
echo        浏览器将自动打开，关闭本窗口即停止服务。
echo.
start "" http://127.0.0.1:8000
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo [已停止] 服务已退出。
pause
