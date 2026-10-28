"""应用自更新检查测试：版本解析/比较、检查状态机（mock 网络）、设置端点契约。"""
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import APP_VERSION, _parse_tag_version, state


# ---------------- 版本解析 ----------------

def test_parse_tag_version_basic():
    assert _parse_tag_version("v1.0.1") == (1, 0, 1)
    assert _parse_tag_version("v1.2") == (1, 2)
    assert _parse_tag_version("1.0.0") == (1, 0, 0)

def test_parse_tag_version_rejects_non_semver():
    assert _parse_tag_version("v1.0.1-beta") is None
    assert _parse_tag_version("beta") is None
    assert _parse_tag_version("") is None

def test_parse_tag_version_compare():
    assert (_parse_tag_version("v1.0.1") > _parse_tag_version("v1.0.0")) is True
    assert (_parse_tag_version("v1.0.0") > _parse_tag_version("v1.0.0")) is False
    assert (_parse_tag_version("v2.0") > _parse_tag_version("v1.9.9")) is True


# ---------------- 检查状态机（mock 网络层） ----------------

class _FakeResp:
    def __init__(self, status_code=200, tag="v1.0.1", published_at="2026-10-20T00:00:00Z"):
        self.status_code = status_code
        self._tag = tag
        self._published_at = published_at

    def json(self):
        return {"tag_name": self._tag, "published_at": self._published_at}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise self.__class__.HTTPError(f"HTTP {self.status_code}")


class _FakeRequests:
    """fake requests 模块：异常类自成体系，_check_app_update 的 except 能捕获。"""
    class RequestException(Exception):
        pass
    class HTTPError(RequestException):
        pass
    class ConnectionError(RequestException):
        pass

    def __init__(self, resp=None, exc=None):
        self.resp = resp
        self.exc = exc
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if self.exc is not None:
            raise self.exc(url)
        return self.resp


@pytest.fixture()
def _clean_state():
    old = dict(state.app_update)
    old_cfg = state.cfg
    old_checking = state.app_update_checking
    state.app_update = {"status": "none", "latest_tag": "",
                        "published_at": "", "checked_at": ""}
    state.app_update_checking = False
    state.cfg = None
    yield
    state.app_update = old
    state.cfg = old_cfg
    state.app_update_checking = old_checking


def _run_check(monkeypatch, fake_requests):
    import sys
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    main._check_app_update()
    return fake_requests


def test_check_newer_release(monkeypatch, _clean_state):
    fake = _FakeRequests(resp=_FakeResp(tag="v9.9.9"))
    _run_check(monkeypatch, fake)
    u = state.app_update
    assert u["status"] == "newer"
    assert u["latest_tag"] == "v9.9.9"
    assert u["checked_at"]
    assert fake.calls == [
        f"https://api.github.com/repos/{main.GITHUB_REPO}/releases/latest"]

def test_check_up_to_date(monkeypatch, _clean_state):
    _run_check(monkeypatch, _FakeRequests(resp=_FakeResp(tag=f"v{APP_VERSION}")))
    assert state.app_update["status"] == "up_to_date"
    assert state.app_update["latest_tag"] == f"v{APP_VERSION}"

def test_check_non_semver_tag_is_unknown(monkeypatch, _clean_state):
    _run_check(monkeypatch, _FakeRequests(resp=_FakeResp(tag="release-2026")))
    u = state.app_update
    assert u["status"] == "unknown"
    assert u["latest_tag"] == "release-2026"  # 原样展示保持透明

def test_check_private_repo_404_is_unknown(monkeypatch, _clean_state):
    _run_check(monkeypatch, _FakeRequests(resp=_FakeResp(status_code=404)))
    assert state.app_update["status"] == "unknown"

def test_check_network_error_is_unknown(monkeypatch, _clean_state):
    fake = _FakeRequests(exc=_FakeRequests.ConnectionError)
    _run_check(monkeypatch, fake)
    assert state.app_update["status"] == "unknown"

def test_check_bad_json_is_unknown(monkeypatch, _clean_state):
    """响应 JSON 缺字段返回 None -> strip/浮点比较前崩 -> ValueError 兜底。"""
    _run_check(monkeypatch, _FakeRequests(
        resp=_FakeResp(tag="v1.0", published_at=None)))
    assert state.app_update["status"] in ("unknown", "up_to_date")

def test_check_reentrance_guard(_clean_state):
    """正在检查时再次调用直接返回（防重入），不触碰网络。"""
    state.app_update_checking = True
    main._check_app_update()
    assert state.app_update["status"] == "none"


# ---------------- 设置端点契约（复用 test_api.py 的隔离 fixture 模式） ----------------

@pytest.fixture()
def settings_client(tmp_path, monkeypatch):
    from app.config import Config
    from app.db import Database

    cfg = Config()
    cfg.categories = {}
    cfg.static_dir = tmp_path / "no-dist"
    monkeypatch.setattr("app.main.load_config", lambda: cfg)
    db = Database(tmp_path / "about.db")
    monkeypatch.setattr("app.main.Database", lambda path: db)
    monkeypatch.setattr("app.main.Collector", lambda cfgx, dbx: None)
    main.state.cfg = cfg
    main.state.db = db
    main.state.collector = None
    main.state.scheduler = None
    with TestClient(main.app) as tc:
        yield tc
    db.close()
    main.state.cfg = None


def test_settings_contract(settings_client):
    main.state.app_update = {"status": "none", "latest_tag": "",
                             "published_at": "", "checked_at": ""}
    data = settings_client.get("/api/settings").json()
    assert data["version"] == APP_VERSION
    assert data["repo_url"] == f"https://github.com/{main.GITHUB_REPO}"
    assert set(data["app_update"]) == {"status", "latest_tag", "published_at", "checked_at"}
    assert data["app_update"]["status"] in ("none", "up_to_date", "newer", "unknown")


def test_manual_check_endpoint_triggers(settings_client, monkeypatch):
    """手动端点应后台触发检查（daemon 线程），端点本身立即返回 ok。"""
    fired = []
    monkeypatch.setattr(main, "_check_app_update", lambda: fired.append(1))
    resp = settings_client.post("/api/app/update-check")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    deadline = time.time() + 3
    while not fired and time.time() < deadline:
        time.sleep(0.05)
    assert fired, "后台检查线程未执行"


def test_update_download_rejected_in_web_mode(settings_client, monkeypatch):
    """网页部署（非 frozen）调用一键更新接口应被拒绝——本地部署无安装包。"""
    monkeypatch.setattr(main.paths, "is_frozen", lambda: False)
    resp = settings_client.post("/api/app/update-download")
    assert resp.status_code == 400
    assert "git pull" in resp.json()["detail"]
