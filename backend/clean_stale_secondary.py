"""清理全库失效的附加分类（2026-11）。

背景：附加分类（`categories` JSON 数组）是采集时「同一项目被多个分类关键词搜到」
累积出来的。旧关键词表很宽松——`开发` 收了 cli / framework / awesome / self-hosted
等通用词，全库 28% 的项目都能命中它——于是大量项目的附加分类里塞进了跟它无关的
标签。后果是筛选项虚高：点「游戏开发」会筛出 tensorflow、awesome-go。

2026-11 收紧词表（负关键词 + 多词短语相邻匹配）后，这些历史标注已经**失去证据**。
本脚本按当前词表重跑判定，摘掉没有证据的附加分类：

- 只处理**内置分类**（`config.yaml` 的 `categories` 键）。自定义分类（用户自建，
  `custom_categories` 表）一律不动——它们的判定逻辑不同，误删会丢用户配置。
- 主分类（`category` 列）**永不动**，只改 `categories` 数组。
- 摘完若数组为空，兜底写回主分类，避免 `categories='[]'` 这种半残状态。
- 仍有关键词证据的附加分类保留（真跨界项目，如既算 AI工具 又算 效率工具）。

用法：
  .venv/bin/python -I clean_stale_secondary.py            # dry-run
  .venv/bin/python -I clean_stale_secondary.py --apply    # 写库
"""
from __future__ import annotations

import json
import sys
from collections import Counter

sys.path.insert(0, ".")

from app.collector import Collector, _match_categories  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import Database  # noqa: E402

APPLY = "--apply" in sys.argv


def main() -> None:
    cfg = load_config()
    db = Database("history.db")
    collector = Collector(cfg, db)
    patterns = collector._category_patterns()
    builtin = set(cfg.categories)

    rows = db._conn.execute(
        "SELECT full_name, name, description, topics, category, categories "
        "FROM projects"
    ).fetchall()

    to_clean: list[tuple[str, list[str], list[str]]] = []
    stale_counter: Counter[str] = Counter()
    for r in rows:
        cats = json.loads(r["categories"] or "[]")
        if len(cats) <= 1:
            continue
        item = {"name": r["name"], "description": r["description"] or "",
                "topics": json.loads(r["topics"] or "[]")}
        # 有证据的分类 = 当前词表命中的 + 主分类本身；自定义分类无条件保留
        supported = set(_match_categories(item, patterns)) | {r["category"]}
        stale = [c for c in cats if c in builtin and c not in supported]
        if not stale:
            continue
        remaining = [c for c in cats if c not in stale]
        if not remaining:
            remaining = [r["category"]]
        to_clean.append((r["full_name"], cats, remaining))
        for c in stale:
            stale_counter[c] += 1

    print(f"全库 {len(rows)} 个项目，其中附加分类含失效项的: {len(to_clean)}\n")
    print("失效附加分类分布：")
    for name, n in stale_counter.most_common():
        print(f"  {name:10s} {n}")
    print()
    print("清理后仍是多分类的项目（保留，属真跨界）：")
    multi = [t for t in to_clean if len(t[2]) > 1]
    for name, cats, remaining in multi:
        print(f"  {name[:40]:40s} {cats} → {remaining}")
    print(f"  （共 {len(multi)} 个）\n")
    print("清理后回归单分类的项目（抽样 15 个）：")
    for name, cats, remaining in [t for t in to_clean if len(t[2]) <= 1][:15]:
        print(f"  {name[:40]:40s} {cats} → {remaining}")

    if not APPLY:
        print("\n[dry-run] 未写库。确认后加 --apply 执行。")
        db.close()
        return

    print("\n开始写库…")
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
