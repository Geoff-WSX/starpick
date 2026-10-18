"""收藏 + 健康度测试：收藏 CRUD/分组/快照端点 + 详情页收藏字段。"""
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database


def _seed(db, full_name="org/app-a", stars=5000):
    db.upsert_project({
        "full_name": full_name, "name": full_name.split("/")[1], "owner": "org",
        "description": "A tool for watching shows", "homepage": "", "stars": stars,
        "language": "Python", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": f"https://github.com/{full_name}", "topics": ["tool"],
        "platforms": ["Windows"], "system_requirements": [],
        "download_url": "", "zh_friendly": 0,
        "category": "影视", "major_version": "2",
        "first_seen_at": "2026-10-08 00:00:00", "last_recommended_at": "2026-10-08 00:00:00",
        "last_round_id": 1, "readme": "# App", "assets": [],
    })


@pytest.fixture()
def fclient(tmp_path, monkeypatch):
    from app import main

    cfg = Config()
    cfg.categories = {"影视": ["追剧"]}
    cfg.static_dir = tmp_path / "no-dist"
    monkeypatch.setattr("app.main.load_config", lambda: cfg)
    db = Database(tmp_path / "api.db")
    monkeypatch.setattr("app.main.Database", lambda path: db)
    monkeypatch.setattr("app.main.Collector", lambda cfg, db: None)
    _seed(db, "org/app-a", stars=5000)
    _seed(db, "org/app-b", stars=800)
    with TestClient(main.app) as tc:
        yield tc, db
    db.close()
    main.state.cfg = None
    main.state.db = None


def test_favorite_crud(fclient):
    """收藏（自动按主分类归组）→ 移动分组 → 列表/分组统计 → 取消收藏。"""
    client, db = fclient
    pid = db.project_exists("org/app-a")["id"]

    # 收藏：不传分组，自动归入项目主分类「影视」
    resp = client.post(f"/api/favorites/{pid}", json={"group_name": "追剧"})
    assert resp.status_code == 200
    assert resp.json()["group_name"] == "影视"

    # 重复收藏幂等
    resp = client.post(f"/api/favorites/{pid}", json={"group_name": "追剧"})
    assert resp.status_code == 200

    resp = client.get("/api/favorites")
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["group_name"] == "影视"
    assert body["groups"] == [{"group_name": "影视", "cnt": 1}]

    # 移动分组
    resp = client.patch(f"/api/favorites/{pid}", json={"group_name": "收藏夹"})
    assert resp.status_code == 200
    resp = client.get("/api/favorites?group=收藏夹")
    assert len(resp.json()["items"]) == 1
    resp = client.get("/api/favorites?group=影视")
    assert len(resp.json()["items"]) == 0

    # 详情页带收藏状态
    resp = client.get(f"/api/projects/{pid}")
    d = resp.json()
    assert d["is_favorite"] is True
    assert d["favorite_group"] == "收藏夹"

    # 取消收藏
    resp = client.delete(f"/api/favorites/{pid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    resp = client.get(f"/api/projects/{pid}")
    assert resp.json()["is_favorite"] is False
    resp = client.get("/api/favorites")
    assert len(resp.json()["items"]) == 0


def test_favorite_404_and_not_found(fclient):
    client, db = fclient
    # 不存在项目
    resp = client.post("/api/favorites/99999", json={"group_name": "x"})
    assert resp.status_code == 404
    # 未收藏时移动分组
    pid = db.project_exists("org/app-b")["id"]
    resp = client.patch(f"/api/favorites/{pid}", json={"group_name": "x"})
    assert resp.status_code == 404


def test_health_snapshots_endpoint(fclient):
    """快照落库 → 健康度曲线端点按时间升序返回。"""
    client, db = fclient
    pid = db.project_exists("org/app-a")["id"]
    db.record_health_snapshot(pid, 100, 0, "v1.0", "2026-10-11 00:00:00")
    db.record_health_snapshot(pid, 150, 1, "v2.0", "2026-10-12 00:00:00")
    resp = client.get(f"/api/projects/{pid}/health")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert items[0]["stars"] == 100      # 升序：最早在前
    assert items[1]["stars"] == 150
    assert items[1]["has_update"] == 1
    assert items[1]["latest_tag"] == "v2.0"
    # 不存在项目
    resp = client.get("/api/projects/99999/health")
    assert resp.status_code == 404


def test_release_notes_in_detail(fclient):
    """release 说明存入后详情页返回。"""
    client, db = fclient
    pid = db.project_exists("org/app-a")["id"]
    db.save_release_notes(pid, "## v2.0\n- 新增 XXX")
    resp = client.get(f"/api/projects/{pid}")
    assert resp.status_code == 200
    assert "v2.0" in resp.json()["release_notes"]
