"""测试全局隔离。

lifespan 启动时会调用 _backup_db()（生产备份目录 backend/data/backup）。测试进程
用的是临时库，若直接写生产备份目录，轮转会把真实备份挤掉（历史事故：7 份空快照
删掉了 49MB 的真备份）。这里统一把备份目录重定向到每个用例自己的 tmp_path。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_backup_dir(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "BACKUP_DIR", tmp_path / "backup")
