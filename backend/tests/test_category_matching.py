"""分类关键词匹配与分类计数的回归测试。

覆盖两处 2026-11 的修复：
1. **负关键词机制**（`!` 前缀）：裸词 `emulator` 曾把 alacritty/ghostty/wezterm
   这类「终端模拟器」整批收进「游戏」分类（实测 140 个里 47 个误判，还顶掉 Top 榜）。
2. **category_counts 的 N+1 优化**：从「每分类 2 次查询」改为「两次全表扫描一次性
   归并」，性能 233ms → 24ms，语义必须逐项等价（含附加分类去重）。
"""
from __future__ import annotations

import json

import pytest

from app.db import Database


def _db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


def _insert(db: Database, full_name: str, category: str,
            categories: list[str] | None = None) -> None:
    """插入一条最小可用的项目行（只填计数需要的字段 + NOT NULL 列）。"""
    db.upsert_project({
        "full_name": full_name, "name": full_name.split("/")[-1],
        "owner": full_name.split("/")[0], "description": "",
        "homepage": "", "stars": 1000, "language": "Go",
        "pushed_at": "2026-10-01T00:00:00Z",
        "html_url": f"https://github.com/{full_name}", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": category, "major_version": "",
        "first_seen_at": "2026-10-01 00:00:00",
        "last_recommended_at": "2026-10-01 00:00:00",
        "readme": "", "categories": categories or [category],
    })


# ---------------- category_counts 语义 ----------------

def test_category_counts_primary_and_secondary(tmp_path):
    """主分类命中 + 附加分类命中都计入；同一项目不重复计数。"""
    db = _db(tmp_path)
    _insert(db, "a/one", "游戏")
    _insert(db, "b/two", "开发", ["开发", "游戏"])   # 跨界：主开发、附游戏
    _insert(db, "c/three", "音乐")

    counts = db.category_counts(["游戏", "开发", "音乐"])
    assert counts["游戏"] == 2      # a/one 主分类 + b/two 附加分类
    assert counts["开发"] == 1      # 只有 b/two（a/one 不在开发）
    assert counts["音乐"] == 1
    db.close()


def test_category_counts_prefix_name_not_confused(tmp_path):
    """前缀同名分类不得互相污染（音乐 vs 音乐与阅读）——标签串边界比对的意义。"""
    db = _db(tmp_path)
    _insert(db, "a/one", "音乐与阅读")
    _insert(db, "b/two", "音乐")

    counts = db.category_counts(["音乐", "音乐与阅读"])
    assert counts["音乐"] == 1
    assert counts["音乐与阅读"] == 1
    db.close()


def test_category_counts_missing_category_returns_zero(tmp_path):
    """没有项目的分类也要出现（值为 0），前端 chip 依赖完整键集合。"""
    db = _db(tmp_path)
    _insert(db, "a/one", "游戏")
    counts = db.category_counts(["游戏", "完全不存在的分类"])
    assert counts["游戏"] == 1
    assert counts["完全不存在的分类"] == 0
    db.close()


def test_category_counts_empty_names(tmp_path):
    db = _db(tmp_path)
    assert db.category_counts([]) == {}
    db.close()


def test_category_counts_matches_legacy_implementation(tmp_path):
    """与旧实现（逐分类 _category_hit_rows）逐项等价——这是优化安全性的核心断言。"""
    db = _db(tmp_path)
    _insert(db, "a/one", "游戏")
    _insert(db, "b/two", "开发", ["开发", "游戏", "AI工具"])
    _insert(db, "c/three", "音乐")
    _insert(db, "d/four", "游戏", ["游戏", "创作"])
    names = ["游戏", "开发", "音乐", "AI工具", "创作", "阅读"]

    new = db.category_counts(names)
    legacy = {n: len(db._category_hit_rows([n])) for n in names}
    assert new == legacy
    db.close()


# ---------------- 负关键词机制 ----------------

def _patterns_for(cfg_categories: dict[str, list[str]], tmp_path):
    """用指定分类词表构造 Collector 的编译模式（绕开 config.yaml）。"""
    from app.collector import Collector
    from app.config import Config

    cfg = Config()
    cfg.categories = cfg_categories
    db = _db(tmp_path)
    return Collector(cfg, db), db


def test_negative_keyword_excludes_terminal_emulator(tmp_path):
    """`!terminal` 应把终端模拟器挡在「游戏」外（核心回归）。"""
    from app.collector import _match_categories

    collector, db = _patterns_for({
        "游戏": ["emulator", "!terminal"],
    }, tmp_path)
    pats = collector._category_patterns()

    terminal = {"name": "alacritty", "description": "A terminal emulator", "topics": []}
    game = {"name": "pcsx2", "description": "PlayStation 2 emulator", "topics": []}

    assert _match_categories(terminal, pats) == []
    assert _match_categories(game, pats) == ["游戏"]
    db.close()


def test_negative_keyword_does_not_leak_into_query(tmp_path):
    """负关键词只用于本地复检，不能进 GitHub 查询串（NOT 计入 5 运算符上限）。"""
    from app.collector import build_category_queries

    queries = build_category_queries(["emulator", "!terminal"], 0, "")
    assert queries, "应至少生成一条查询"
    for q in queries:
        assert "terminal" not in q.lower()
        assert "emulator" in q.lower()


def test_negative_keyword_cjk(tmp_path):
    """中文负关键词走子串匹配（neg_sub 分支）。"""
    from app.collector import _match_categories

    collector, db = _patterns_for({
        "游戏": ["模拟器", "!终端"],
    }, tmp_path)
    pats = collector._category_patterns()

    term = {"name": "x", "description": "终端 模拟器", "topics": []}
    assert _match_categories(term, pats) == []
    game = {"name": "y", "description": "安卓 模拟器", "topics": []}
    assert _match_categories(game, pats) == ["游戏"]
    db.close()


def test_negative_keyword_multiword_requires_all(tmp_path):
    """多词负关键词是 AND 语义：`!aws local` 只有两词都出现才排除。

    这是负词精确性的关键——写成裸 `!aws` 会误杀 "Amazon Games launcher"
    （HeroicGamesLauncher 实测被误排除过）。
    """
    from app.collector import _match_categories

    collector, db = _patterns_for({
        "游戏": ["emulator", "!aws local"],
    }, tmp_path)
    pats = collector._category_patterns()

    # 两词都在 → 排除
    hit = {"name": "floci", "description": "The AWS Local Emulator", "topics": []}
    assert _match_categories(hit, pats) == []

    # 只有 aws、没有 local → 负词不成立，仍按 emulator 归入游戏
    keep = {"name": "heroic", "description": "A game emulator launcher for GOG and Amazon",
            "topics": []}
    assert _match_categories(keep, pats) == ["游戏"]
    db.close()
