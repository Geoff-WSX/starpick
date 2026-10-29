# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：macOS .app 与 Windows exe 共用一份。

构建（产物与中间目录统一放 packaging/ 下）：
    macOS:   packaging/macos/build_macos.sh
    Windows: .github/workflows/build-windows.yml（CI）或 packaging/windows/build_windows.ps1

资源布局（与 backend/app/paths.py 冻结分支的探测逻辑对应）：
    frontend_dist/   前端构建产物 → paths.default_static_dir()
    config.yaml      默认配置     → paths.ensure_user_config() 首次运行种到用户数据目录
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # 仓库根
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"
CONFIG_YAML = BACKEND / "config.yaml"
ICON = ROOT / "packaging" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")

APP_NAME = "StarPick"
VERSION = "1.3"

if not (FRONTEND_DIST / "index.html").is_file():
    raise SystemExit("缺少前端构建产物 frontend/dist/index.html，请先在 frontend/ 下执行 npm run build")
if not CONFIG_YAML.is_file():
    raise SystemExit(f"缺少默认配置：{CONFIG_YAML}")

datas = [
    (str(FRONTEND_DIST), "frontend_dist"),   # SPA 静态页
    (str(CONFIG_YAML), "."),                 # 随包默认配置
]
datas += collect_data_files("tzdata")        # zoneinfo 时区数据（Windows 无系统时区库）

# uvicorn 走运行时动态导入（loop/http/ws 协议按名选型），单靠静态分析会漏
hiddenimports = collect_submodules("uvicorn") + [
    "apscheduler.triggers.interval",
    "apscheduler.triggers.cron",
    "apscheduler.executors.pool",
    "tzdata",
]

excludes = [
    # desktop.py 固定 loop=asyncio / http=h11：不打包未用的原生加速库
    "uvloop", "httptools",
    # 其他 GUI 后端与常用重依赖：pywebview 在 macOS/Windows 走系统 WebView
    "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "gi", "cefpython3",
    "numpy", "pandas", "matplotlib", "IPython", "PIL", "pytest",
]

a = Analysis(
    [str(BACKEND / "desktop_entry.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 应用：不弹控制台（macOS 无终端窗口 / Windows 无黑框）
    disable_windowed_traceback=False,
    argv_emulation=False,   # 不吞命令行参数（--selftest 依赖 sys.argv 原样传入）
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ICON),
        bundle_identifier="com.geoffwsx.starpick",
        version=VERSION,
        info_plist={
            "CFBundleName": "StarPick",
            "CFBundleDisplayName": "STARPICK",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSAllowsLocalNetworking": True,   # 允许 WKWebView 访问 127.0.0.1
            "NSHumanReadableCopyright": "MIT License",
        },
    )
