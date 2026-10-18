"""备份体系测试：快照校验 + 轮转只保留最近 N 份。

背景：备份目录曾被测试进程写入的空快照占满，轮转把 49MB 的真备份删掉，
所以 backup() 现在会校验快照行数，不一致就删除并抛错。
"""
from app.db import Database


def _seed_one(db: Database, full_name: str = "org/app") -> None:
    db.upsert_project({
        "full_name": full_name, "name": full_name.split("/")[1], "owner": "org",
        "description": "desc", "homepage": "", "stars": 100,
        "language": "Python", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": f"https://github.com/{full_name}", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": "影视", "major_version": "",
        "first_seen_at": "2026-10-08 00:00:00", "last_recommended_at": "2026-10-08 00:00:00",
        "last_round_id": 1, "readme": "# x", "assets": [],
    })


def _project_count(path) -> int:
    import sqlite3

    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        conn.close()


def test_backup_writes_verified_snapshot(tmp_path):
    """备份产出的快照能独立打开，且行数与源库一致。"""
    db = Database(tmp_path / "d.db")
    _seed_one(db)
    _seed_one(db, "org/app2")
    dest = db.backup(tmp_path / "backup")
    assert dest.exists()
    assert _project_count(dest) == db.count_projects() == 2
    db.close()


def test_backup_rotation_keeps_newest(tmp_path):
    """轮转只保留最近 keep 份（按文件名排序），不碰其它前缀的文件。"""
    db = Database(tmp_path / "d.db")
    _seed_one(db)
    bdir = tmp_path / "backup"
    bdir.mkdir()
    for name in (
        "history-20200101-000001.db",
        "history-20200101-000002.db",
        "history-20200101-000003.db",
    ):
        (bdir / name).write_bytes(b"stub")
    # 手工备份文件（不同前缀）不参与轮转
    (bdir / "history.db.pre_manual").write_bytes(b"stub")

    dest = db.backup(bdir, keep=2)
    names = sorted(p.name for p in bdir.glob("history-*.db"))
    assert len(names) == 2
    assert dest.name in names
    assert names[0] == "history-20200101-000003.db"  # 最旧的两份被删
    assert (bdir / "history.db.pre_manual").exists()
    db.close()


def test_startup_skips_backup_when_db_empty(tmp_path, monkeypatch, caplog):
    """空库启动只记日志、不落快照文件（空快照会挤掉轮转里的真实备份）。

    走真实 lifespan（TestClient 上下文），验证 _backup_db 的空库跳过分支。
    """
    import logging

    from fastapi.testclient import TestClient

    from app import main
    from app.config import Config

    cfg = Config()
    cfg.categories = {"影视": ["追剧"]}
    cfg.static_dir = tmp_path / "no-dist"
    monkeypatch.setattr("app.main.load_config", lambda: cfg)
    monkeypatch.setattr("app.main.Database", lambda path: Database(tmp_path / "empty.db"))
    monkeypatch.setattr("app.main.Collector", lambda cfg, db: None)
    with caplog.at_level(logging.INFO, logger="kazumi"):
        with TestClient(main.app):
            pass
    # 与 test_api.client 夹具一致：清掉模块级 state，别让后续用例拿到已关闭的库
    main.state.cfg = None
    main.state.db = None
    assert any("跳过备份" in r.getMessage() for r in caplog.records)
    # conftest 已把 BACKUP_DIR 重定向到 tmp_path/backup：目录里不应出现任何快照
    assert not list((tmp_path / "backup").glob("history-*.db"))
