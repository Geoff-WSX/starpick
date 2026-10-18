#!/usr/bin/env python3
"""存量 assets 大小回填：为已采集项目的 release assets 补上 size（文件大小）。

背景：早期版本 get_latest_release 映射 assets 时丢弃了 size 字段，
导致下载弹窗无法显示文件大小。修复后新采集/检查更新的数据自带 size，
本脚本负责把存量数据补齐。

用法：
    ./.venv/bin/python tools_backfill_asset_sizes.py [--limit N] [--skip-existing]

- 遍历 assets 非空且仍有 size=0 资产的项目，逐个调 releases/latest 刷新（复用限流队列）
- --skip-existing：跳过所有资产 size>0 的项目（断点续跑，默认已开启）
- 匿名限流约 60 项目/小时；配置 GitHub Token 后（设置页保存即热生效）约 5000/小时
- 中途 Ctrl-C 可安全中断，已更新的项目不会重复处理
"""
import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

from app.collector import _release_assets_to_list, infer_platforms
from app.config import load_config
from app.db import Database
from app.github_api import GitHubClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill-size")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个项目（0=全部）")
    ap.add_argument("--skip-existing", action="store_true", default=True, help="跳过资产已含 size 的项目")
    args = ap.parse_args()

    cfg = load_config()
    db = Database(Path(__file__).resolve().parent / "history.db")
    client = GitHubClient(cfg)

    conn = sqlite3.connect(Path(__file__).resolve().parent / "history.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, full_name, assets FROM projects WHERE assets IS NOT NULL AND assets != '[]'"
    ).fetchall()
    todo = []
    for r in rows:
        try:
            assets = json.loads(r["assets"])
        except Exception:
            continue
        missing = any((a.get("size") or 0) <= 0 for a in assets)
        if missing:
            todo.append((r["id"], r["full_name"]))
    if args.limit and args.limit > 0:
        todo = todo[: args.limit]

    if not todo:
        log.info("没有需要回填 size 的项目（存量数据已完整）")
        return 0

    log.info("待回填 %d 个项目（匿名限流约 %d 分钟；配 Token 后约 %d 分钟）",
             len(todo), len(todo), max(1, len(todo) // 80))
    done = fail = skip = 0
    t0 = time.monotonic()
    try:
        for idx, (pid, full_name) in enumerate(todo, 1):
            try:
                rel = client.get_latest_release(full_name)
            except Exception as exc:
                fail += 1
                log.warning("[%d/%d] %s 失败: %s", idx, len(todo), full_name, exc)
                continue
            if not rel:
                skip += 1
                continue
            asset_list = _release_assets_to_list(rel)
            if asset_list:
                platforms = infer_platforms(
                    [a.get("name", "") for a in asset_list], ""
                )
                db.update_project_assets(pid, asset_list, platforms)
            done += 1
            if idx % 20 == 0 or idx == len(todo):
                log.info("[%d/%d] 完成 %d 失败 %d，已耗时 %.1f min",
                         idx, len(todo), done, fail, (time.monotonic() - t0) / 60)
    except KeyboardInterrupt:
        log.info("已中断（Ctrl-C）：%d 完成，可再次运行续跑", done)
        return 130

    log.info("回填结束：完成 %d，失败 %d，跳过 %d，耗时 %.1f min",
             done, fail, skip, (time.monotonic() - t0) / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
