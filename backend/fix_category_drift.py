"""修复 5 个分类的词表漂移（2026-11 第二轮排查）。

背景：第一轮修好「游戏」的 emulator 歧义后，同类问题在别的分类还存在——
问题不在 emulator，而在**关键词的英文首要含义不等于中文分类意图**：

| 分类 | 词 | 实际含义 | 误收 |
|---|---|---|---|
| 加速工具 | `mirror` | 代码镜像仓库（"Mirror of https://..."） | FFmpeg/git/postgres/chromium 等 22 个 |
| 视频工具 | `downloader` | 任何下载器 | 漫画/小说/torrent 下载器 17 个 |
| 阅读 | `新闻` | 任何新闻聚合 | 股票分析、舆情监控 3 个 |
| 影视 | `anime` | 词形变体命中 `animation` | 动画引擎 anime.js |
| 创作 | `动画` | 中文子串命中「动画图解」 | 算法教程 hello-algo |

config.yaml 已加负关键词挡住新增采集。本脚本修存量：把主分类失去证据、
但描述有明确更好归属的项目迁走；给不出归属的**保持不动**（宁可留着看不顺眼，
也不要乱迁——第一轮实测「迁到首个命中分类」会把 streamlink/owncast 等影视工具
吸进「开发」，因为「开发」收了 cli/framework 这类通用词，全库 28% 都能命中）。

用法：
  .venv/bin/python -I fix_category_drift.py            # dry-run
  .venv/bin/python -I fix_category_drift.py --apply
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from app.collector import Collector, _match_categories  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import Database  # noqa: E402

APPLY = "--apply" in sys.argv

# 来源分类 → (描述特征, 目标分类)。只收录「描述明确说明它是什么」的特征。
ROUTES: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "加速工具": (
        # 代码镜像仓库 → 开发（FFmpeg/git/postgres/CMake/wireshark 等）
        (("mirror of", "source code mirror", "read-only mirror",
          "official mirror", "github mirror", "git-ified", "mirror repository"),
         "开发"),
        # 面向开发者的加速/测试工具（SpacetimeDB 等）
        (("database", "framework", "library", "compiler", "runtime"), "开发"),
        # 3D/图形创作工具的镜像（blender）
        (("3d", "render", "graphics", "animation"), "创作"),
    ),
    "视频工具": (
        # 漫画/小说阅读与下载属于「阅读」而非「视频工具」
        (("manga", "comic", "novel", "漫画", "小说"), "阅读"),
    ),
    "阅读": (
        # 股票/舆情分析器不是阅读器
        (("stock", "股票", "舆情", "trend monitor", "trading"), "AI工具"),
    ),
    "影视": (
        # 动画引擎库不是影视播放
        (("animation engine", "动画引擎"), "创作"),
    ),
    "创作": (
        # 算法教程不是创作工具
        (("algorithm", "数据结构"), "开发"),
    ),
}


def route(desc: str, rules: tuple[tuple[tuple[str, ...], str], ...]) -> str:
    d = desc.lower()
    for hints, target in rules:
        if any(h in d for h in hints):
            return target
    return ""


def main() -> None:
    cfg = load_config()
    db = Database("history.db")
    collector = Collector(cfg, db)
    patterns = collector._category_patterns()

    moves: list[tuple[str, str, str, str]] = []   # (full_name, 原, 新, 描述)
    untouched: list[tuple[str, str, str]] = []    # (full_name, 分类, 描述)
    for src, rules in ROUTES.items():
        rows = db._conn.execute(
            "SELECT full_name, name, description, topics, category "
            "FROM projects WHERE category=?", (src,)
        ).fetchall()
        for r in rows:
            item = {"name": r["name"], "description": r["description"] or "",
                    "topics": json.loads(r["topics"] or "[]")}
            if src in _match_categories(item, patterns):
                continue      # 仍有本分类证据 → 不动
            target = route(r["description"] or "", rules)
            if target:
                moves.append((r["full_name"], src, target, r["description"] or ""))
            else:
                untouched.append((r["full_name"], src, r["description"] or ""))

    print(f"主分类失去证据的项目：需迁移 {len(moves)} / 给不出归属保持不动 {len(untouched)}\n")
    print("迁移明细：")
    for name, src, target, desc in moves:
        print(f"  {name[:40]:40s} {src:6s} → {target:6s} {desc[:44]}")
    print(f"\n保持不动（请人工复核，脚本不处理）：")
    for name, src, desc in untouched:
        print(f"  {name[:40]:40s} {src:6s} {desc[:50]}")

    if not APPLY:
        print("\n[dry-run] 未写库。确认后加 --apply 执行。")
        db.close()
        return

    print("\n开始写库…")
    with db._lock:
        for name, _src, target, _desc in moves:
            db._conn.execute(
                "UPDATE projects SET category=?, categories=? WHERE full_name=?",
                (target, json.dumps([target], ensure_ascii=False), name),
            )
        db._conn.commit()
    print(f"完成：{len(moves)} 个项目已改归")
    db.close()


if __name__ == "__main__":
    main()
