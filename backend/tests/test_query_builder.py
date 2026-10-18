"""关键词 OR 合并查询构造测试。

GitHub 搜索 API 限制单条查询最多 5 个 AND/OR/NOT 运算符，
因此超过 6 个关键词必须拆分为多组查询。
"""
from app.collector import build_category_queries


def test_or_merges_all_keywords():
    qs = build_category_queries(["追剧", "番剧", "anime"], 500, "2026-07-10")
    assert len(qs) == 1
    q = qs[0]
    assert " OR " in q
    assert '"追剧"' in q
    assert '"番剧"' in q
    assert "anime" in q
    assert "in:name,description" in q
    assert "stars:>=500" in q
    assert "pushed:>=2026-07-10" in q


def test_chinese_keywords_quoted_english_not():
    qs = build_category_queries(["追剧"], 500, "x")
    assert '"追剧"' in qs[0]
    qs2 = build_category_queries(["anime"], 500, "x")
    assert '"anime"' not in qs2[0]  # 英文不加引号，容忍复数/派生


def test_blank_keywords_skipped():
    qs = build_category_queries(["", "  ", "追剧"], 500, "x")
    assert '"追剧"' in qs[0]
    assert '""' not in qs[0]


def test_all_blank_returns_empty():
    assert build_category_queries(["", "  "], 500, "x") == []


def test_more_than_six_keywords_split_into_groups():
    """10 个影视关键词必须拆成 2 组，且每组 OR 运算符 ≤5（规避 GitHub 422）。"""
    kws = ["追剧", "番剧", "anime", "动漫", "在线观看", "流媒体", "视频下载", "字幕工具", "TVBox", "IPTV"]
    qs = build_category_queries(kws, 500, "2026-07-10")
    assert len(qs) == 2
    for q in qs:
        or_count = q.count(" OR ")
        assert or_count <= 5, f"单组 OR 运算符 {or_count} 超过 GitHub 上限"
    assert '"番剧"' in qs[0]
    assert "IPTV" in qs[1]


def test_six_keywords_stay_in_one_group():
    kws = ["a", "b", "c", "d", "e", "f"]
    qs = build_category_queries(kws, 500, "x")
    assert len(qs) == 1
    assert qs[0].count(" OR ") == 5
