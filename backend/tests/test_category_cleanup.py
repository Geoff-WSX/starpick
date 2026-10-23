"""分类清除测试：多选删除存量项目（主分类+附加分类）、级联清表、收藏保留、采集互斥。"""
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database


def _seed(db, full_name, category, extra=None, stars=100):
    db.upsert_project({
        "full_name": full_name, "name": full_name.split("/")[1], "owner": "org",
        "description": "unused", "homepage": "", "stars": stars,
        "language": "Python", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": f"https://github.com/{full_name}", "topics": [],
        "platforms": [], "system_requirements": [],
        "download_url": "", "zh_friendly": 0,
        "category": category, "major_version": "",
        "first_seen_at": "2026-10-23 00:00:00", "last_recommended_at": "2026-10-23 00:00:00",
        "last_round_id": 1, "readme": "# x", "assets": [],
        **({"categories": extra} if extra else {}),
    })


@pytest.fixture()
def cclient(tmp_path, monkeypatch):
    """数据库层 + API 状态共用夹具（test_favorites.py 同款）。"""
    from app import main

    cfg = Config()
    cfg.categories = {"影视": ["追剧"], "游戏": ["模拟器"]}
    cfg.static_dir = tmp_path / "no-dist"
    monkeypatch.setattr("app.main.load_config", lambda: cfg)
    db = Database(tmp_path / "api.db")
    monkeypatch.setattr("app.main.Database", lambda path: db)
    monkeypatch.setattr("app.main.Collector", lambda cfg, db: None)
    _seed(db, "org/movie-1", "影视")
    _seed(db, "org/movie-2", "影视")
    # 跨界项目：主分类开发，附加分类含影视
    _seed(db, "org/cross-1", "开发", extra=["开发", "影视"])
    # 游戏类 + 收藏类
    _seed(db, "org/game-1", "游戏")
    _seed(db, "org/fav-1", "影视", stars=9000)
    db.add_favorite(db.project_exists("org/fav-1")["id"])
    with TestClient(main.app) as tc:
        yield tc, db
    db.close()
    main.state.cfg = None
    main.state.db = None


def test_preview_counts_exclude_favorites(cclient):
    """预览计数：主分类命中 + 排除收藏（fav-1 收藏了所以 不计入）。"""
    client, db = cclient
    resp = client.post("/api/categories/preview-cleanup", json={"names": ["影视", "游戏"]})
    assert resp.status_code == 200
    body = resp.json()
    # 影视可删：movie-1/movie-2 + cross-1（附加分类命中、未收藏）→ 3；游戏：1
    assert body["counts"]["影视"] == 3
    assert body["counts"]["游戏"] == 1
    assert body["running"] is False


def test_cleanup_deletes_category_projects(cclient):
    """清除多分类：主分类与附加分类（跨界项目）都命中。"""
    client, db = cclient
    resp = client.post("/api/categories/cleanup", json={"names": ["影视"]})
    assert resp.status_code == 200
    body = resp.json()
    # movie-1/movie-2/cross-1（附加分类命中）删除；fav-1 收藏保留
    assert body["deleted"] == 3
    assert body["kept_favorites"] == 1
    # 被删项目不存在
    assert db.project_exists("org/movie-1") is None
    assert db.project_exists("org/cross-1") is None
    # 收藏项目保留，但只在它自己的附加分类里不残留「影视」？主分类影视保留（主分类不动）
    row = db.project_exists("org/fav-1")
    assert row is not None


def test_cleanup_cascades_related_tables(cclient):
    """级联：health_snapshots / daily_picks / notifications 清理（无外键），收藏表不波及。"""
    client, db = cclient
    pid = db.project_exists("org/movie-1")["id"]
    fav_pid = db.project_exists("org/fav-1")["id"]
    with db._lock:
        db._conn.execute("INSERT INTO health_snapshots (project_id, stars, checked_at) VALUES (?,?,?)",
                         (pid, 100, "2026-10-23 00:00:00"))
        db._conn.execute("INSERT INTO health_snapshots (project_id, stars, checked_at) VALUES (?,?,?)",
                         (fav_pid, 9000, "2026-10-23 00:00:00"))
        db._conn.execute("INSERT INTO notifications (type, project_id, title, message, created_at) "
                         "VALUES ('new', ?, 't', 'm', '2026-10-23 00:00:00')", (pid,))
        db._conn.commit()
    with db._lock:
        db._conn.execute(
            "INSERT INTO daily_picks (pick_date, project_id, created_at) VALUES (?,?,?)",
            ("2026-10-23", pid, "2026-10-23 00:00:00"))
        db._conn.commit()

    resp = client.post("/api/categories/cleanup", json={"names": ["影视"]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3
    with db._lock:
        # 孤儿行清理
        assert db._conn.execute("SELECT COUNT(*) FROM health_snapshots WHERE project_id=?", (pid,)).fetchone()[0] == 0
        assert db._conn.execute("SELECT COUNT(*) FROM notifications WHERE project_id=?", (pid,)).fetchone()[0] == 0
        assert db._conn.execute("SELECT COUNT(*) FROM daily_picks WHERE project_id=?", (pid,)).fetchone()[0] == 0
        # 收藏项目的快照保留
        assert db._conn.execute("SELECT COUNT(*) FROM health_snapshots WHERE project_id=?", (fav_pid,)).fetchone()[0] == 1


def test_cleanup_keeps_favorites_and_strips_extra_refs(cclient):
    """收藏项目不删；跨界收藏项目在附加字段里的命中分类被摘除，主分类保留。"""
    client, db = cclient
    # 把 cross-1 也收藏 → 应保留且附加分类里的影视被摘除
    cross_id = db.project_exists("org/cross-1")["id"]
    db.add_favorite(cross_id)

    resp = client.post("/api/categories/cleanup", json={"names": ["影视"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 2          # 只剩 movie-1/movie-2 可删
    assert body["kept_favorites"] == 2   # fav-1 + cross-1
    refs = db.project_exists("org/cross-1")
    # 附加字段摘除影视后剩 ["开发"]（主分类不动）
    import json
    assert json.loads(refs["categories"]) == ["开发"]


def test_cleanup_rejects_while_collect_running(cclient):
    """采集运行中（rounds 表 running）→ 409 拒绝删除。"""
    client, db = cclient
    with db._lock:
        db._conn.execute(
            "INSERT INTO rounds (started_at, status, source) VALUES ('2026-10-23 00:00:00', 'running', 'manual')")
        db._conn.commit()
    resp = client.post("/api/categories/cleanup", json={"names": ["影视"]})
    assert resp.status_code == 409
    # 预览接口可用并提示 running
    resp = client.post("/api/categories/preview-cleanup", json={"names": ["影视"]})
    assert resp.json()["running"] is True


def test_cleanup_empty_and_unknown_category(cclient):
    """空清单/无效分类：不炸也删 0。"""
    client, db = cclient
    # 空清单被 Pydantic min_length 拦成 422（前端不会发空清单，仍作边界防御）
    resp = client.post("/api/categories/cleanup", json={"names": []})
    assert resp.status_code == 422
    resp = client.post("/api/categories/cleanup", json={"names": ["不存在的分类"]})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0, "kept_favorites": 0}


def test_similarity_prefix_not_hit(cclient):
    """LIKE 边界：「音乐」不得误伤「音乐与阅读」之类的同名前缀分类。（防 LIKE %名% 边界粗糙）"""
    client, db = cclient
    _seed(db, "org/misc-1", "音乐与阅读")
    resp = client.post("/api/categories/cleanup", json={"names": ["音乐"]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0
    assert db.project_exists("org/misc-1") is not None


def test_cleanup_does_not_write_backup_dir(cclient, tmp_path):
    """清除流程不新增备份快照（无恢复入口，备份无法回滚；日常备份由启动/定时任务维护）。

    夹具启动时 lifespan 的 _backup_db 会写一份快照到重定向后的 BACKUP_DIR，
    所以断言的是「清除前后快照数不变」，而不是目录为空。
    """
    client, db = cclient
    bdir = tmp_path / "backup"
    before = sorted(p.name for p in bdir.glob("history-*.db"))
    resp = client.post("/api/categories/cleanup", json={"names": ["游戏"]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    after = sorted(p.name for p in bdir.glob("history-*.db"))
    assert after == before
