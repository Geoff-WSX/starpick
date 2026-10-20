"""app/desktop.py 桌面壳测试：探活识别、端口顺延、无窗口自检全链路。

不加载 webview：selftest 路径可在无 GUI 环境（CI）跑通；窗口部分由打包验收覆盖。
"""
from __future__ import annotations

import socket

from app import desktop
from app import main as main_mod
from app.config import Config


def test_health_ok_false_on_free_port():
    assert desktop._health_ok(8397, timeout=0.5) is False


def test_find_free_port_returns_preferred_when_free():
    assert desktop._find_free_port(8396) == 8396


def test_find_free_port_skips_occupied():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy = sock.getsockname()[1]
        picked = desktop._find_free_port(busy, tries=3)
    assert picked is not None and picked != busy


def test_selftest_ok(monkeypatch, tmp_path, capsys):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app.js").write_text(
        "console.log('starpick');\n" + "// padding\n" * 60, encoding="utf-8")
    (dist / "index.html").write_text(
        '<!doctype html><html><head>'
        '<script type="module" src="/assets/app.js"></script>'
        "</head><body>STARPICK</body></html>",
        encoding="utf-8",
    )

    def fake_load_config(path=None):
        cfg = Config()
        cfg.host = "127.0.0.1"
        cfg.static_dir = dist
        return cfg

    monkeypatch.setattr("app.config.load_config", fake_load_config)
    # main.py 在导入时就绑定了 load_config，lifespan 用的是那个引用，必须一并替换
    monkeypatch.setattr(main_mod, "load_config", fake_load_config)
    # 自检不跑启动补采（避免测试联网），其余链路（静态托管/API/DB）真实执行
    monkeypatch.setattr(main_mod, "_startup_catchup", lambda: None)
    data = tmp_path / "data"
    monkeypatch.setenv("STARPICK_DATA_DIR", str(data))

    rc = desktop._run_selftest(8395)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "[selftest] OK" in out
    assert (data / "history.db").exists()


def test_find_free_port_exhausted():
    socks = []
    try:
        base = None
        for _ in range(4):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            socks.append(s)
        base = socks[0].getsockname()[1]
        # 若临时端口恰好不连续则跳过（极端情况，不影响主逻辑）
        ports = {s.getsockname()[1] for s in socks}
        if all(p in ports for p in range(base, base + 4)):
            assert desktop._find_free_port(base, tries=4) is None
    finally:
        for s in socks:
            s.close()


def test_desktop_entry_importable():
    import desktop_entry

    assert callable(desktop_entry.main)
