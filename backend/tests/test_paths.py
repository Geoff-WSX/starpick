"""app/paths.py 路径层测试：开发模式与打包模式的路径解析。

覆盖：
- 开发模式路径与历史布局逐项一致（backend/ 下数据，项目根 logs/）
- STARPICK_DATA_DIR 覆盖只切换运行时状态（库/备份/日志），配置与密钥仍在 backend/
- 冻结（打包）模式的 macOS / Windows 数据目录
- 随包默认 config.yaml 首次运行种植到数据目录，且不覆盖用户改动
- load_config 在冻结模式下读取种植后的配置
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import paths


def _as_frozen(monkeypatch, resource_dir: Path, platform: str = "darwin") -> None:
    """模拟冻结运行环境（PyInstaller 会在启动前设置 sys.frozen / sys._MEIPASS）。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(resource_dir), raising=False)
    monkeypatch.setattr(sys, "platform", platform)


def _isolate_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("STARPICK_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_dev_paths_match_legacy_layout(monkeypatch):
    monkeypatch.delenv("STARPICK_DATA_DIR", raising=False)
    assert not paths.is_frozen()
    backend = Path(paths.__file__).resolve().parent.parent  # backend/
    repo = backend.parent
    assert paths.data_dir() == backend
    assert paths.default_db_path() == backend / "history.db"
    assert paths.backup_dir() == backend / "data" / "backup"
    assert paths.runtime_token_path() == backend / ".runtime_token"
    assert paths.env_file_path() == backend / ".env"
    assert paths.config_path() == backend / "config.yaml"
    assert paths.default_static_dir() == repo / "frontend" / "dist"
    assert paths.logs_dir() == repo / "logs"


def test_data_dir_env_override_isolates_runtime_state(monkeypatch, tmp_path):
    override = tmp_path / "iso"
    monkeypatch.setenv("STARPICK_DATA_DIR", str(override))
    assert paths.data_dir() == override
    assert paths.default_db_path() == override / "history.db"
    assert paths.backup_dir() == override / "backup"
    assert paths.logs_dir() == override / "logs"
    # 开发模式下配置与密钥仍读 backend/（便于带真实配置做隔离实例测试）
    assert paths.config_path() == paths.backend_dir() / "config.yaml"
    assert paths.env_file_path() == paths.backend_dir() / ".env"


def test_frozen_macos_uses_application_support(monkeypatch, tmp_path):
    _as_frozen(monkeypatch, tmp_path / "res", "darwin")
    _isolate_home(monkeypatch, tmp_path)
    data = tmp_path / "home" / "Library" / "Application Support" / paths.APP_NAME
    assert paths.data_dir() == data
    assert paths.default_db_path() == data / "history.db"
    assert paths.backup_dir() == data / "backup"
    assert paths.logs_dir() == data / "logs"
    assert paths.runtime_token_path() == data / ".runtime_token"
    assert paths.env_file_path() == data / ".env"
    assert paths.config_path() == data / "config.yaml"


def test_frozen_windows_uses_appdata(monkeypatch, tmp_path):
    _as_frozen(monkeypatch, tmp_path / "res", "win32")
    monkeypatch.delenv("STARPICK_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert paths.data_dir() == tmp_path / "appdata" / paths.APP_NAME


def test_frozen_resource_paths_and_static(monkeypatch, tmp_path):
    res = tmp_path / "res"
    (res / "frontend_dist").mkdir(parents=True)
    _as_frozen(monkeypatch, res, "darwin")
    _isolate_home(monkeypatch, tmp_path)
    assert paths.resource_dir() == res
    assert paths.backend_dir() == res
    assert paths.resource_config_path() == res / "config.yaml"
    assert paths.default_static_dir() == res / "frontend_dist"


def test_frozen_env_override_wins(monkeypatch, tmp_path):
    _as_frozen(monkeypatch, tmp_path / "res", "darwin")
    override = tmp_path / "custom-data"
    monkeypatch.setenv("STARPICK_DATA_DIR", str(override))
    assert paths.data_dir() == override
    assert paths.config_path() == override / "config.yaml"
    assert paths.env_file_path() == override / ".env"


def test_ensure_user_config_dev_is_noop():
    assert paths.ensure_user_config() is None


def test_ensure_user_config_seeds_once(monkeypatch, tmp_path):
    res = tmp_path / "res"
    res.mkdir()
    (res / "config.yaml").write_text("server:\n  port: 8123\n", encoding="utf-8")
    _as_frozen(monkeypatch, res, "darwin")
    _isolate_home(monkeypatch, tmp_path)

    seeded = paths.ensure_user_config()
    assert seeded == paths.data_dir() / "config.yaml"
    assert seeded.read_text(encoding="utf-8") == "server:\n  port: 8123\n"
    # 二次调用不覆盖用户改过的配置
    seeded.write_text("server:\n  port: 9000\n", encoding="utf-8")
    assert paths.ensure_user_config() is None
    assert seeded.read_text(encoding="utf-8") == "server:\n  port: 9000\n"


def test_ensure_user_config_missing_resource_is_noop(monkeypatch, tmp_path):
    res = tmp_path / "res"
    res.mkdir()  # 资源里没有 config.yaml
    _as_frozen(monkeypatch, res, "darwin")
    _isolate_home(monkeypatch, tmp_path)
    assert paths.ensure_user_config() is None
    assert not paths.config_path().exists()


def test_load_config_seeds_default_in_frozen_mode(monkeypatch, tmp_path):
    res = tmp_path / "res"
    res.mkdir()
    (res / "config.yaml").write_text(
        "server:\n  port: 8123\ncategories:\n  影视:\n    - 追剧\n",
        encoding="utf-8",
    )
    _as_frozen(monkeypatch, res, "darwin")
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setattr("app.config.RUNTIME_TOKEN_FILE", tmp_path / "no-token")

    from app.config import load_config

    cfg = load_config()
    assert cfg.port == 8123
    assert cfg.categories == {"影视": ["追剧"]}
    assert paths.config_path().read_text(encoding="utf-8").startswith("server:")
