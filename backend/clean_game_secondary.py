"""清理「游戏」被污染的附加分类（2026-11）。

背景：旧关键词表里 `emulator` 是裸词，把大量**游戏开发**项目（godot/bevy/phaser/
MCSManager 等）写进了「游戏」的附加分类数组。虽然它们的主分类正确（游戏开发），
但附加分类会让 `?category=游戏` 筛出这些引擎/框架项目——首页点「游戏」第一屏
全是 godot、bevy，用户会认为分类坏了。

处理：对「主分类不是游戏」的项目，若新规则下**没有任何游戏关键词证据**，
就从它的 categories 数组里摘掉「游戏」（其它附加分类原样保留）。
有证据的跨界项目（如真游戏兼工具）不动。

用法：
  .venv/bin/python -I clean_game_secondary.py           # dry-run
  .venv/bin/python -I clean_game_secondary.py --apply
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from app.collector import Collector, _match_categories  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import Database  # noqa: E402

APPLY = "--apply" in sys.argv
TARGET = "游戏"


def main() -> None:
    cfg = load_config()
    db = Database("history.db")
    collector = Collector(cfg, db)
    patterns = collector._category_patterns()

    rows = db._conn.execute(
        "SELECT full_name, name, description, topics, category, categories "
        "FROM projects WHERE categories LIKE ? AND category != ?",
        (f"%{TARGET}%", TARGET),
    ).fetchall()

    to_clean: list[tuple[str, list[str], list[str]]] = []
    for r in rows:
        cats = json.loads(r["categories"] or "[]")
        if TARGET not in cats:
            continue
        item = {"name": r["name"], "description": r["description"] or "",
                "topics": json.loads(r["topics"] or "[]")}
        # 仍有游戏关键词证据 → 合法跨界，保留
        if TARGET in _match_categories(item, patterns):
            continue
        remaining = [c for c in cats if c != TARGET]
        # 摘空时兜底保留主分类，避免 categories 变空数组
        if not remaining:
            remaining = [r["category"]]
        to_clean.append((r["full_name"], cats, remaining))

    print(f"扫描到附加分类含「{TARGET}」且主分类非该类的项目: {len(rows)}")
    print(f"其中无关键词证据、需摘除「{TARGET}」的: {len(to_clean)}\n")
    for name, cats, remaining in to_clean[:15]:
        print(f"  {name[:40]:40s} {cats} → {remaining}")
    if len(to_clean) > 15:
        print(f"  …（共 {len(to_clean)} 条）")

    if not APPLY:
        print(f"\n[dry-run] 未写库。确认后加 --apply 执行。")
        db.close()
        return

    print(f"\n开始写库…")
    with db._lock:
        for name, _cats, remaining in to_clean:
            db._conn.execute(
                "UPDATE projects SET categories=? WHERE full_name=?",
                (json.dumps(remaining, ensure_ascii=False), name),
            )
        db._conn.commit()
    print(f"完成：{len(to_clean)} 个项目的附加分类已清理")
    db.close()


if __name__ == "__main__":
    main()
