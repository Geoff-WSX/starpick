"""桌面壳入口：内嵌 WebView 窗口 + 程序化 uvicorn + 单实例检测。

用法（开发模式）：
    python -m app.desktop              # 打开桌面窗口（已在运行则直接指向既有实例）
    python -m app.desktop --port 8100  # 指定端口
    python -m app.desktop --selftest   # 无窗口自检：起服务→探活→校验静态页→退出（CI/打包验收）

打包入口：backend/desktop_entry.py（PyInstaller / py2app 均以它为主脚本，
桌面壳与纯服务模式共用 app 包，行为差异只在本文件的窗口部分）。

设计要点：
- 服务仍在 127.0.0.1 本地端口跑（uvicorn 程序化启动，复用 app.main:app 的 lifespan），
  窗口只是它的浏览器；因此采集/调度/备份逻辑与浏览器模式完全一致。
- 冻结环境固定 loop=asyncio / http=h11 / ws=none：纯 Python 实现，避免
  打包 uvloop/httptools 的二进制依赖问题（本项目不用 WebSocket）。
- 端口被别的程序占用时自动顺延；端口上是本程序实例时不再起第二个服务，直接开窗。
- 外链：pywebview 默认 ALLOW_EXTERNAL_LINKS_IN_BROWSER=True，window.open /
  target=_blank 由系统默认浏览器接管，下载仍然走浏览器（产品决策：不内置下载器）。
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import paths

WINDOW_TITLE = "STARPICK 拾星"
START_TIMEOUT_SECONDS = 45
_PORT_TRIES = 12


def _window_title() -> str:
    """窗口标题带版本号（APP_VERSION 延迟导入，保持模块导入轻量）。"""
    try:
        from .main import APP_VERSION
        return f"{WINDOW_TITLE} v{APP_VERSION}"
    except Exception:
        return WINDOW_TITLE


def _ensure_stdio() -> None:
    """py2app 双击启动（无控制台）时 sys.stdout/stderr 为 None，补 devnull 防日志写入报错。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _urlopen_local(url: str, timeout: float):
    """访问回环地址的请求必须绕开代理。

    urllib 在 macOS 上会读系统代理设置（Clash/Surge/表哥云等常驻开启），
    本机 127.0.0.1 请求被转发给代理后失败——代理开着时桌面壳会误报"启动失败"。
    """
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(url, timeout=timeout)


def _health_ok(port: int, timeout: float = 1.5) -> bool:
    """端口上是否已有本程序实例（按 /health 响应特征识别，避免误判其他占用程序）。"""
    import json

    try:
        with _urlopen_local(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and "db_ok" in data and "version" in data


def _find_free_port(preferred: int, tries: int = _PORT_TRIES) -> int | None:
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def _start_server(cfg, port: int):
    """程序化启动 uvicorn（复用 app.main:app 的完整 lifespan），返回 (server, thread)。"""
    import uvicorn

    from .main import app

    config = uvicorn.Config(
        app,
        host=cfg.host,
        port=port,
        log_config=None,   # 不覆盖日志配置：uvicorn 日志传播到 kazumi 的 root handlers
        access_log=False,  # 桌面壳不需要逐请求访问日志
        loop="asyncio",
        http="h11",
        ws="none",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return server, thread


def _wait_healthy(thread: threading.Thread, port: int,
                  timeout: float = START_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_ok(port, timeout=1.0):
            return True
        if not thread.is_alive():
            return False  # 服务线程已退出（配置错误/端口异常），不必等满超时
        time.sleep(0.25)
    return False


def _log_tail(lines: int = 25) -> str:
    from .main import LOG_DIR

    try:
        content = (LOG_DIR / "kazumi.log").read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


_ERROR_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ background:#0b1020; color:#e8ecf8; font-family:-apple-system,"Segoe UI",sans-serif;
         padding:48px; line-height:1.6; }}
  h1 {{ color:#ff6b3d; font-size:20px; }}
  pre {{ background:#141a2e; padding:16px; border-radius:8px; overflow:auto;
        font-size:12px; max-height:280px; white-space:pre-wrap; }}
  .hint {{ color:#9aa4c0; font-size:13px; }}
</style></head>
<body>
  <h1>{title}</h1>
  <p>{message}</p>
  <p class="hint">诊断日志（完整文件见下方路径）：</p>
  <pre>{tail}</pre>
  <p class="hint">{log_path}</p>
</body></html>"""


def _show_error_window(title: str, message: str) -> None:
    """启动失败时给一个可见窗口（.app 双击运行没有终端，错误不能只进日志）。"""
    from html import escape

    from .main import LOG_DIR

    try:
        import webview

        html_doc = _ERROR_HTML.format(
            title=escape(title),
            message=escape(message),
            tail=escape(_log_tail()),
            log_path=escape(str(LOG_DIR / "kazumi.log")),
        )
        webview.create_window(title, html=html_doc, width=780, height=600)
        webview.start()
    except Exception as exc:  # 连窗口都开不出来时兜底到标准错误
        print(f"[FATAL] {title}: {message} ({exc})", file=sys.stderr)


def _open_path(path: Path) -> None:
    """用系统文件管理器/默认程序打开路径（菜单项用，失败静默不影响主功能）。"""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: PTH123
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


class _JsApi:
    """注入页面的原生桥（window.pywebview.api.*）。

    前端外链必须走这里：pywebview 6.x 的 macOS 窗口里 window.open('_blank')
    会静默失效（新 WebView 创建回调只转发 LinkActivated 导航，window.open
    不属于该类型），用户点击下载毫无反应——见 frontend/src/api.ts openExternal()。
    """

    def open_external(self, url: str) -> None:
        import webbrowser

        if not url.startswith(("http://", "https://")):
            return
        webbrowser.open(url, 2, True)   # new=2 新标签；下载/分享等外链交给系统默认浏览器


def _open_window(url: str) -> None:
    """打开内嵌窗口（阻塞直到窗口关闭）；private_mode=False 让主题等 localStorage 持久化。"""
    import webview
    from webview.menu import Menu, MenuAction

    from .main import LOG_DIR

    def _reload() -> None:
        if webview.windows:
            webview.windows[0].load_url(url)

    # 默认 App/Edit 菜单（含 Cmd+C/V）由 pywebview 提供（SHOW_DEFAULT_MENUS=True），
    # 这里只补自定义菜单并汉化默认项
    menu = [
        Menu("页面", [MenuAction("重新加载页面", _reload)]),
        Menu("打开", [
            MenuAction("数据目录", lambda: _open_path(paths.data_dir())),
            MenuAction("日志目录", lambda: _open_path(LOG_DIR)),
        ]),
    ]
    localization = {
        "cocoa.menu.about": f"关于 {_window_title()}",
        "cocoa.menu.services": "服务",
        "cocoa.menu.view": "显示",
        "cocoa.menu.edit": "编辑",
        "cocoa.menu.hide": "隐藏",
        "cocoa.menu.hideOthers": "隐藏其他",
        "cocoa.menu.showAll": "全部显示",
        "cocoa.menu.quit": "退出",
        "cocoa.menu.fullscreen": "进入全屏",
        "cocoa.menu.cut": "剪切",
        "cocoa.menu.copy": "复制",
        "cocoa.menu.paste": "粘贴",
        "cocoa.menu.selectAll": "全选",
    }
    storage_dir = paths.data_dir() / "webview"
    storage_dir.mkdir(parents=True, exist_ok=True)
    webview.create_window(
        _window_title(), url, width=1280, height=860, min_size=(980, 640),
        text_select=True,   # 允许选中/复制 README 文字（默认 False）
        js_api=_JsApi(),    # 前端 openExternal() 走此桥（window.open 在窗口内静默失效）
    )
    webview.start(menu=menu, localization=localization,
                  private_mode=False, storage_path=str(storage_dir))


def _run_selftest(preferred_port: int) -> int:
    """无窗口自检：独立数据目录起服务 → /health → 首页与 assets → API → 退出。

    打包产物验收与 CI 冒烟用；数据目录隔离见 _run_selftest_inner。
    设置 STARPICK_SELFTEST_WEBVIEW=1 时额外校验 webview GUI 栈可导入；
    注意：必须在导入 app.main 之前确定数据目录——LOG_DIR 等模块级常量在导入时求值，
    否则冻结模式下自检会写到真实用户数据目录。
    """
    import contextlib
    import io
    import json

    # Windows 的窗口程序（console=False）stdout 指向 NUL，CI 里看不到任何输出；
    # 设置 STARPICK_SELFTEST_REPORT 时把全部输出同时写进这个文件供 CI 读取
    report_path = os.environ.get("STARPICK_SELFTEST_REPORT")
    report_fp = open(report_path, "w", encoding="utf-8") if report_path else None

    @contextlib.contextmanager
    def _tee():
        if report_fp is None:
            yield
            return
        new_out = sys.stdout, sys.stderr

        class _Tee(io.TextIOBase):
            def write(self, s: str) -> int:
                return report_fp.write(s)

            def flush(self) -> None:
                report_fp.flush()

        sys.stdout = sys.stderr = _Tee()
        try:
            yield
        finally:
            sys.stdout, sys.stderr = new_out
            report_fp.close()

    if not os.environ.get("STARPICK_DATA_DIR"):
        # 自检绝不碰真实用户数据：未显式指定时用一次性临时目录
        os.environ["STARPICK_DATA_DIR"] = tempfile.mkdtemp(prefix="starpick-selftest-")
    os.environ.setdefault("STARPICK_SELFTEST", "1")  # 冒烟标记（采集本就全手动，无定时/补跑可关）

    with _tee():
        return _run_selftest_inner(preferred_port)


def _run_selftest_inner(preferred_port: int) -> int:
    import json

    print(f"[selftest] data_dir = {paths.data_dir()}")

    from .config import ConfigError, load_config
    from .main import APP_VERSION, setup_logging

    setup_logging()
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"[selftest] FAIL 配置校验: {exc}")
        return 1
    port = _find_free_port(preferred_port)
    if port is None:
        print(f"[selftest] FAIL 端口 {preferred_port} 起 {_PORT_TRIES} 个均被占用")
        return 1

    failures: list[str] = []
    server, thread = _start_server(cfg, port)
    base = f"http://127.0.0.1:{port}"

    def _get(path: str, timeout: float = 5.0):
        with _urlopen_local(base + path, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()

    if not _wait_healthy(thread, port):
        failures.append(f"服务未在 {START_TIMEOUT_SECONDS}s 内就绪（/health 无响应）")
    else:
        status, _, body = _get("/health")
        health = json.loads(body)
        if status != 200 or not health.get("db_ok"):
            failures.append(f"/health 异常: {status} {body[:200]!r}")
        if not paths.default_db_path().exists():
            failures.append(f"数据库未在数据目录创建: {paths.default_db_path()}")
        status, ctype, body = _get("/")
        html = body.decode("utf-8", "replace")
        if status != 200 or "text/html" not in ctype:
            failures.append(f"首页异常: {status} {ctype}")
        match = re.search(r"/assets/[^\"']+\.js", html)
        if not match:
            failures.append("首页未引用 /assets/*.js（前端构建产物缺失？）")
        else:
            status, _, body = _get(match.group(0))
            if status != 200 or len(body) < 500:
                failures.append(f"静态资源异常: {match.group(0)} -> {status}, {len(body)}B")
        status, _, _ = _get("/api/projects?page=1&page_size=1")
        if status != 200:
            failures.append(f"/api/projects 异常: {status}")

    if os.environ.get("STARPICK_SELFTEST_WEBVIEW") == "1":
        # 打包验收：提前暴露 GUI 依赖缺失（缺 PyObjC/pythonnet 等），否则用户双击才黑窗
        wv_err = _check_webview_import()
        if wv_err:
            failures.append(wv_err)

    server.should_exit = True
    thread.join(timeout=30)
    if thread.is_alive():
        print("[selftest] 警告：服务线程 30s 内未完全退出（采集收尾超时）")

    if failures:
        for item in failures:
            print(f"[selftest] FAIL {item}")
        print("[selftest] 日志尾部:")
        print(_log_tail(15))
        return 1
    print(f"[selftest] OK version={APP_VERSION} frozen={paths.is_frozen()} port={port}")
    return 0


def _check_webview_import() -> str | None:
    """冻结包里导入 webview 并确认能定位系统 WebView，返回错误描述（None=通过）。

    纯服务自检覆盖不到 GUI 栈；打包漏了依赖（常见：macOS 缺 PyObjC、
    Windows 缺 pythonnet）时用户双击才会黑窗，这里提前暴露。
    设置 STARPICK_SELFTEST_WEBVIEW=1 时启用（纯服务冒烟不需要）。
    """
    backend = {"darwin": "cocoa", "win32": "edgechromium"}.get(sys.platform, "none")
    try:
        import webview  # noqa: F401
        if backend != "none":
            import importlib
            importlib.import_module(f"webview.platforms.{backend}")
    except Exception as exc:
        return f"webview/{backend or sys.platform} 导入失败: {type(exc).__name__}: {exc}"
    return None


def main(argv: list[str] | None = None) -> int:
    _ensure_stdio()
    parser = argparse.ArgumentParser(prog="starpick", description="STARPICK 拾星桌面壳")
    parser.add_argument("--port", type=int, default=None,
                        help="监听端口（默认取 config.yaml 的 server.port）")
    parser.add_argument("--selftest", action="store_true",
                        help="无窗口自检：起服务→探活→校验静态页后退出（CI/打包验收用）")
    args = parser.parse_args(argv)

    if args.selftest:
        # selftest 自行决定数据目录后才导入 app.main（见 _run_selftest 说明）
        return _run_selftest(args.port or 8000)

    from .config import ConfigError, load_config
    from .main import APP_VERSION, setup_logging

    setup_logging()
    log = logging.getLogger("kazumi")
    log.info("STARPICK 桌面壳启动（v%s，frozen=%s，data_dir=%s）",
             APP_VERSION, paths.is_frozen(), paths.data_dir())

    try:
        cfg = load_config()
    except ConfigError as exc:
        log.error("配置校验失败，拒绝启动：%s", exc)
        _show_error_window("STARPICK 拾星 · 配置校验失败", str(exc))
        return 1

    preferred = args.port or cfg.port
    server = thread = None
    started = False
    if _health_ok(preferred):
        port = preferred
        log.info("检测到既有 STARPICK 实例（127.0.0.1:%d），直接打开窗口", port)
    else:
        free = _find_free_port(preferred)
        if free is None:
            msg = f"端口 {preferred} 起 {_PORT_TRIES} 个端口均被占用，无法启动。"
            log.error(msg)
            _show_error_window("STARPICK 拾星 · 无法启动", msg)
            return 1
        if free != preferred:
            log.warning("端口 %d 被占用，改用 %d", preferred, free)
        port = free
        server, thread = _start_server(cfg, port)
        if not _wait_healthy(thread, port):
            log.error("服务未在 %ds 内就绪，启动失败", START_TIMEOUT_SECONDS)
            _show_error_window(
                "STARPICK 拾星 · 启动失败",
                f"内嵌服务未在 {START_TIMEOUT_SECONDS}s 内就绪（端口 {port}）。",
            )
            server.should_exit = True
            thread.join(timeout=5)
            return 1
        started = True
        log.info("内嵌服务已就绪：http://127.0.0.1:%d", port)

    try:
        _open_window(f"http://127.0.0.1:{port}")
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log.exception("打开桌面窗口失败：%s", exc)
        if started and server is not None:
            server.should_exit = True
            thread.join(timeout=10)
        return 1

    if started and server is not None:
        log.info("窗口已关闭，停止内嵌服务")
        server.should_exit = True
        thread.join(timeout=15)
    return 0


if __name__ == "__main__":
    sys.exit(main())
