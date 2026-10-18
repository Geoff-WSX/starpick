"""更新检查测试：check_updates 写 update_rounds 历史（与采集轮次分开保存）+ 端点契约。"""
import json

import pytest
from fastapi.testclient import TestClient

from app.collector import Collector
from app.config import Config
from app.db import Database


class FakeUpdateClient:
    """模拟 GitHub releases 接口：full_name -> {"tag_name": ...}；None 表示无 release。"""

    def __init__(self, releases=None):
        self.releases = releases or {}

    def get_latest_release(self, full_name):
        rel = self.releases.get(full_name)
        if rel is None:
            return None
        return {
            "tag_name": rel["tag_name"],
            "published_at": rel.get("published_at", ""),
            "assets": rel.get("assets", []),
        }


def _mk_collector(tmp_path):
    cfg = Config()
    cfg.categories = {"影视": ["追剧"]}
    db = Database(tmp_path / "u.db")
    client = FakeUpdateClient()
    return Collector(cfg, db, client=client), db, client


def _seed(db, full_name: str, tag: str = "v1.0"):
    db.upsert_project({
        "full_name": full_name, "name": full_name.split("/")[1], "owner": "org",
        "description": "desc", "homepage": "", "stars": 500,
        "language": "Python", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": f"https://github.com/{full_name}", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": "影视", "major_version": "",
        "first_seen_at": "2026-10-08 00:00:00", "last_recommended_at": "2026-10-08 00:00:00",
        "last_round_id": 1, "readme": "", "assets": [],
    })
    # latest_tag 不在 upsert 列集合内，用 SQL 直接置初值
    db._conn.execute("UPDATE projects SET latest_tag=? WHERE full_name=?", (tag, full_name))
    db._conn.commit()
    return db.project_exists(full_name)


def test_check_updates_writes_history(tmp_path):
    """全库检查结束写 update_rounds 历史；标记最新 tag 与 has_update。"""
    col, db, client = _mk_collector(tmp_path)
    row = _seed(db, "org/app", tag="v1.0")
    client.releases["org/app"] = {"tag_name": "v2.0"}
    summary = col.check_updates(limit=None)
    assert summary["status"] == "success"
    assert summary["total"] == 1
    assert summary["updated"] == 1
    assert summary["nochange"] == 0
    assert summary["failed"] == 0
    rows = db.list_update_rounds(10)
    assert len(rows) == 1
    r = rows[0]
    assert r["scope"] == "full"          # limit=None = 手动全库
    assert r["status"] == "success"
    assert r["finished_at"]
    assert r["stats"]["total"] == 1
    assert r["stats"]["updated"] == 1
    assert r["stats"]["duration_sec"] >= 0
    p = db.get_project(row["id"])
    assert p["latest_tag"] == "v2.0"
    assert p["has_update"] == 1
    db.close()


def test_check_updates_incremental_limit(tmp_path):
    """增量检查只查最久未检查的 N 条，scope=incremental；无 release 视为无变化。"""
    col, db, client = _mk_collector(tmp_path)
    _seed(db, "org/a", tag="v1.0")
    _seed(db, "org/b", tag="v1.0")
    client.releases["org/a"] = {"tag_name": "v1.1"}
    # org/b 无 release → nochange；limit=1 只查 1 条
    summary = col.check_updates(limit=1)
    assert summary["status"] == "success"
    assert summary["total"] == 1
    rows = db.list_update_rounds(10)
    assert rows[0]["scope"] == "incremental"
    assert rows[0]["stats"]["updated"] <= 1
    db.close()


@pytest.fixture()
def uclient(tmp_path, monkeypatch):
    from app import main

    cfg = Config()
    cfg.categories = {"影视": ["追剧"]}
    cfg.static_dir = tmp_path / "no-dist"
    monkeypatch.setattr("app.main.load_config", lambda: cfg)
    db = Database(tmp_path / "api.db")
    monkeypatch.setattr("app.main.Database", lambda path: db)
    monkeypatch.setattr("app.main.Collector", lambda cfg, db: None)
    with TestClient(main.app) as tc:
        yield tc, db
    db.close()
    main.state.cfg = None
    main.state.db = None


def test_update_rounds_endpoint(uclient):
    """GET /api/update-rounds + /api/updates/summary 契约。"""
    client, db = uclient
    db.create_update_round("2026-10-13 08:00:00", "full")
    db.finish_update_round(
        1, "success", {"total": 3, "updated": 1, "nochange": 1,
                       "failed": 1, "duration_sec": 12.5}, ""
    )
    resp = client.get("/api/update-rounds")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["scope"] == "full"
    assert it["status"] == "success"
    assert it["stats"]["updated"] == 1
    assert it["stats"]["duration_sec"] == 12.5

    resp2 = client.get("/api/updates/summary")
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["last_check"]["stats"]["updated"] == 1
    assert body["total_projects"] >= 0
    db.close()


def test_no_release_counts_nochange_not_failed(tmp_path):
    """无 release 的仓库只计入 nochange，不得再误计 failed（回归：rel=None 时未 continue 导致双重计数）。"""
    col, db, client = _mk_collector(tmp_path)
    _seed(db, "org/no-release", tag="v1.0")
    # FakeUpdateClient 返回 None（该仓库无 release）
    summary = col.check_updates(limit=None)
    assert summary["status"] == "success"
    assert summary["total"] == 1
    assert summary["nochange"] == 1
    assert summary["failed"] == 0
    assert summary["updated"] == 0


def test_first_check_initializes_tag_without_false_update(tmp_path):
    """首次检查（库内无旧 tag）只初始化 latest_tag：不计更新、不发通知。

    回归：latest_tag 初值缺失时曾被判为"有新版"，首轮即给全库造出 has_update=1 与更新通知。
    """
    col, db, client = _mk_collector(tmp_path)
    row = _seed(db, "org/fresh", tag="")
    client.releases["org/fresh"] = {"tag_name": "v3.0"}
    summary = col.check_updates(limit=None)
    assert summary["status"] == "success"
    assert summary["updated"] == 0
    p = db.get_project(row["id"])
    assert p["latest_tag"] == "v3.0"
    assert p["has_update"] == 0
    assert db.unread_notification_count() == 0
    db.close()


def test_new_release_after_first_check_is_detected(tmp_path):
    """首次检查写入基线后，后续 tag 变化仍能正常判为有新版并发通知。"""
    col, db, client = _mk_collector(tmp_path)
    row = _seed(db, "org/bump", tag="")
    client.releases["org/bump"] = {"tag_name": "v1.0"}
    col.check_updates(limit=None)
    client.releases["org/bump"] = {"tag_name": "v1.1"}
    summary = col.check_updates(limit=None)
    assert summary["updated"] == 1
    assert db.get_project(row["id"])["has_update"] == 1
    assert db.unread_notification_count() == 1
    db.close()


def test_update_keeps_platforms_when_assets_unrecognized(tmp_path):
    """无法识别平台的资产不清空旧标签；识别到平台时正常刷新。

    回归：update_project_assets 曾无条件覆盖 platforms，日常更新检查持续丢失平台标签。
    """
    col, db, client = _mk_collector(tmp_path)
    keep = _seed(db, "org/keep", tag="v1.0")
    bump = _seed(db, "org/bump2", tag="v1.0")
    db.update_project_assets(keep["id"], [], ["Windows", "macOS"])
    db.update_project_assets(bump["id"], [], ["Windows"])
    client.releases["org/keep"] = {
        "tag_name": "v2.0",
        "assets": [{"name": "app-2.0.7z", "url": "https://dl/app.7z", "size": 10}],
    }
    client.releases["org/bump2"] = {
        "tag_name": "v2.0",
        "assets": [{"name": "app-2.0-linux.tar.gz", "url": "https://dl/app.tgz", "size": 20}],
    }
    summary = col.check_updates(limit=None)
    assert summary["failed"] == 0
    assert json.loads(db.get_project(keep["id"])["platforms"]) == ["Windows", "macOS"]
    assert json.loads(db.get_project(bump["id"])["platforms"]) == ["Linux"]
    db.close()
