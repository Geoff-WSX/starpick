"""游戏分类误判修正（2026-11：emulator 歧义修复后的数据修复）。

**只动「游戏」分类**，不做全库自动重分类——原因是「开发」分类收了 cli/
self-hosted/framework 等通用词，全库 28% 的项目都能命中它，任何「迁到首个
命中分类」的自动逻辑都会把影视/设计/音乐的项目吸进「开发」（实测 dry-run
把 streamlink/owncast/mediapipe 4 个影视工具判成开发）。

处理规则（保守，宁可不改）：
1. `游戏` 分类里，新规则下仍有命中 → 不动（真游戏，关键词表已覆盖）；
2. 新规则下零命中，但描述含终端/AWS/CPU/配色等**确定非游戏**特征 → 改归到
   对应的合理分类（按特征路由，见 NOT_GAME_ROUTES）；
3. 新规则下零命中、又无明确特征 → **不动**（可能是关键词表盲区，如 wesnoth
   这类真游戏；删或乱迁都会造成数据损失）。

用法：
  .venv/bin/python -I fix_game_misclassification.py           # dry-run
  .venv/bin/python -I fix_game_misclassification.py --apply   # 写库
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from app.collector import Collector, _match_categories  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import Database  # noqa: E402

APPLY = "--apply" in sys.argv
SOURCE_CATEGORY = "游戏"

# 描述特征 → 更合适的分类。顺序即优先级（先匹配先命中）。
# 只收录「确定不是游戏」的特征，且都能给出明确去向。
NOT_GAME_ROUTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("terminal", "ssh client", "shell"), "开发"),
    (("aws", "serverless", "context protocol"), "开发"),
    (("x86 pc", "cpu emulator", "x86-based machines"), "开发"),
    (("color scheme", "theme for hugo", "normalize browsers"), "设计"),
    (("pre-trained model", "quantitative finance", "tts model"), "AI工具"),
)


def route_not_game(desc: str) -> str:
    """按描述特征给出误判项目的更合适分类；给不出返回空串（即保持不动）。"""
    d = desc.lower()
    for hints, target in NOT_GAME_ROUTES:
        if any(h in d for h in hints):
            return target
    return ""


def main() -> None:
    cfg = load_config()
    db = Database("history.db")
    collector = Collector(cfg, db)
    patterns = collector._category_patterns()

    rows = db._conn.execute(
        "SELECT full_name, name, description, topics FROM projects WHERE category=?",
        (SOURCE_CATEGORY,),
    ).fetchall()

    keep: list[str] = []
    reroute: list[tuple[str, str, str]] = []   # (full_name, 新分类, 描述)
    untouched: list[tuple[str, str]] = []      # 无明确特征 → 不动
    for r in rows:
        item = {"name": r["name"], "description": r["description"] or "",
                "topics": json.loads(r["topics"] or "[]")}
        hits = _match_categories(item, patterns)
        # ① 仍命中「游戏」→ 真游戏，保留
        if SOURCE_CATEGORY in hits:
            keep.append(r["full_name"])
            continue
        # ② 描述有「确定不是游戏」的特征 → 优先按特征路由
        #    （不能只看 hits 就走 keep：cmder 的 topics 里有 shell/ssh-agent，
        #     会命中「开发」，但它的 emulator 是控制台模拟器，应先按特征判）
        target = route_not_game(r["description"] or "")
        if target:
            reroute.append((r["full_name"], target, r["description"] or ""))
            continue
        # ③ 有其它分类命中 → 跟随（说明它确实属于别的分类）
        if hits:
            reroute.append((r["full_name"], hits[0], r["description"] or ""))
            continue
        # ④ 零命中且无特征 → 不动
        untouched.append((r["full_name"], r["description"] or ""))

    print(f"「{SOURCE_CATEGORY}」分类共 {len(rows)} 个项目：")
    print(f"  仍命中关键词（保留）: {len(keep)}")
    print(f"  确定非游戏 → 改归他类: {len(reroute)}")
    print(f"  零命中但无特征（保持不动）: {len(untouched)}\n")

    print(f"改归明细（{len(reroute)} 个）：")
    for name, target, desc in reroute:
        print(f"  {name[:44]:44s} → {target:8s} {desc[:52]}")
    print()
    print(f"保持不动的零命中项目（供人工复核，脚本不处理）：")
    for name, desc in untouched:
        print(f"  {name[:44]:44s} {desc[:56]}")

    if not APPLY:
        print("\n[dry-run] 未写库。确认无误后加 --apply 执行。")
        db.close()
        return

    print("\n开始写库…")
    with db._lock:
        for name, target, _desc in reroute:
            db._conn.execute(
                "UPDATE projects SET category=?, categories=? WHERE full_name=?",
                (target, json.dumps([target], ensure_ascii=False), name),
            )
        db._conn.commit()
    print(f"完成：{len(reroute)} 个项目已改归")
    print("\n分类计数（改归后）：")
    for r in db._conn.execute(
        "SELECT category, COUNT(*) n FROM projects GROUP BY category ORDER BY n DESC"
    ).fetchall():
        print(f"  {r['category']:12s} {r['n']:5d}")
    db.close()


if __name__ == "__main__":
    main()
