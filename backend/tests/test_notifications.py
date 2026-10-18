"""通知中心 + 每日精选测试：幽灵防御/去重/已读语义/精选评分与端点。"""
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database


def _seed(db, full_name="org/app-a", stars=5000, cat="影视", has_update=0):
    db.upsert_project({
        "full_name": full_name, "name": full_name.split("/")[1], "owner": "org",
        "description": "A tool for watching shows", "homepage": "", "stars": stars,
        "language": "Python", "pushed_at": "2026-10-01T00:00:00Z",
        "html_url": f"https://github.com/{full_name}", "topics": ["tool"],
        "platforms": ["Windows"], "system_requirements": [],
        "download_url": "", "zh_friendly": 0,
        "category": cat, "major_version": "2",
        "first_seen_at": "2026-10-08 00:00:00", "last_recommended_at": "2026-10-08 00:00:00",
        "last_round_id": 1, "readme": "# App", "assets": [],
    })
    if has_update:
        db.mark_checked(db.project_exists(full_name)["id"], "v9.9", 1,
                        "2026-10-12 00:00:00")


@pytest.fixture()
def fclient(tmp_path, monkeypatch):
    from app import main

    cfg = Config()
    cfg.categories = {"影视": ["追剧"], "游戏": ["游戏"]}
    cfg.static_dir = tmp_path / "no-dist"
    monkeypatch.setattr("app.main.load_config", lambda: cfg)
    db = Database(tmp_path / "api.db")
    monkeypatch.setattr("app.main.Database", lambda path: db)
    monkeypatch.setattr("app.main.Collector", lambda cfg, db: None)
    _seed(db, "org/app-a", stars=5000, cat="影视")
    _seed(db, "org/app-b", stars=800, cat="游戏")
    _seed(db, "org/app-c", stars=3000, cat="影视", has_update=1)
    with TestClient(main.app) as tc:
        yield tc, db
    db.close()
    main.state.cfg = None
    main.state.db = None


# ---------------- 数据方法 ----------------
def test_create_notification_and_dedup(fclient):
    client, db = fclient
    pid = db.project_exists("org/app-a")["id"]
    assert db.create_notification("collect", pid, "新项目收录：app-a", "desc") is True
    # 24h 内同 type+project+title 去重
    assert db.create_notification("collect", pid, "新项目收录：app-a", "desc") is False
    # 幽灵防御：不存在项目不写
    assert db.create_notification("collect", 99999, "x", "y") is False
    assert db.unread_notification_count() == 1
    items = db.list_notifications(10)
    assert len(items) == 1
    assert items[0]["type"] == "collect"
    assert items[0]["read_at"] == ""


def test_mark_read_semantics(fclient):
    client, db = fclient
    pid = db.project_exists("org/app-a")["id"]
    db.create_notification("collect", pid, "a", "")
    db.create_notification("collect", pid, "b", "")
    db.create_notification("collect", pid, "c", "")
    # 指定 ids 已读
    items = db.list_notifications(10)
    assert db.mark_notifications_read([items[0]["id"]]) == 1
    assert db.unread_notification_count() == 2
    # 空列表 = 全部已读
    assert db.mark_notifications_read([]) == 2
    assert db.unread_notification_count() == 0


def test_daily_pick_save_and_ghost(fclient):
    client, db = fclient
    pid_a = db.project_exists("org/app-a")["id"]
    pid_b = db.project_exists("org/app-b")["id"]
    db.save_daily_pick("2026-10-13", [(pid_a, 1, 9.5), (99999, 2, 8.0), (pid_b, 3, 7.0)])
    data = db.get_latest_daily_pick()
    assert data["pick_date"] == "2026-10-13"
    # 幽灵项目被过滤，只剩 2 个
    assert len(data["items"]) == 2
    assert data["items"][0]["name"] == "app-a"
    # 覆盖写入（同日 replace）
    db.save_daily_pick("2026-10-13", [(pid_b, 1, 6.0)])
    data = db.get_latest_daily_pick()
    assert len(data["items"]) == 1


# ---------------- picker 评分 ----------------
def test_pick_daily_modes(fclient):
    from app.picker import pick_daily
    client, db = fclient
    picks = pick_daily(db, pick_date="2026-10-13", count=3)
    assert len(picks) == 3
    # 模式A：有新版的项目优先进入前 half
    ids = [x["project_id"] for x in picks]
    assert db.project_exists("org/app-c")["id"] in ids
    # rank 连续
    assert [x["rank"] for x in picks] == [1, 2, 3]
    # 分类去重：影视最多 2 个
    cats = []
    for item in picks:
        p = db.get_project(item["project_id"])
        cats.append(p["category"])
    assert cats.count("影视") <= 2


# ---------------- 端点 ----------------
def test_notifications_api(fclient):
    client, db = fclient
    pid = db.project_exists("org/app-a")["id"]
    db.create_notification("collect", pid, "新项目收录：app-a", "d")
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["unread_count"] == 1
    assert body["items"][0]["title"] == "新项目收录：app-a"
    # limit 超范围被 Query 校验拒绝（1-200）
    resp = client.get("/api/notifications?limit=500")
    assert resp.status_code == 422
    # 指定 id 已读
    nid = body["items"][0]["id"]
    resp = client.post("/api/notifications/mark-read", json={"ids": [nid]})
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    resp = client.get("/api/notifications")
    assert resp.json()["unread_count"] == 0
    # 空 ids = 全部已读（无未读则 updated=0）
    resp = client.post("/api/notifications/mark-read", json={"ids": []})
    assert resp.json()["updated"] == 0


def test_picks_api(fclient):
    client, db = fclient
    # 无精选时返回空
    resp = client.get("/api/picks/latest")
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    # 生成后返回
    from app.picker import pick_daily
    picks = pick_daily(db, pick_date="2026-10-13", count=3)
    db.save_daily_pick("2026-10-13", picks)
    resp = client.get("/api/picks/latest")
    body = resp.json()
    assert body["pick_date"] == "2026-10-13"
    assert len(body["items"]) == 3
    assert body["items"][0]["rank"] == 1
