"""存量项目重分类：按新分类关键词表重新打标（主分类 + 跨界多分类）。

用法：
  python3 tools_recategorize.py          # dry-run：只统计分布，不改库
  python3 tools_recategorize.py --apply  # 正式写库（需已备份 history.db）
"""
import io
import json
import re
import sqlite3
import sys

ROOT = "/Users/Zhuanz/Desktop/GitHubSearch/QuanWangSouSuo"
DB = f"{ROOT}/backend/history.db"
CFG = f"{ROOT}/backend/config.yaml"

# ---- 加载新分类关键词表 ----
import yaml  # noqa: E402
cfg = yaml.safe_load(io.open(CFG, encoding="utf-8"))
CATS = cfg["categories"]  # {分类: [关键词...]}

# 英文关键词用词边界匹配，中文/混合（yt-dlp、TVBox、B站下载）用子串
def _is_pure_en(kw: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9 .-]*", kw)) and not any("\u4e00" <= ch <= "\u9fff" for ch in kw)


def _kw_re(kw: str):
    # 品牌/专名（含大写、数字、连字符、点）：子串匹配（如 TVBox→TVBoxOSC、yt-dlp）
    if _is_pure_en(kw) and re.search(r"[A-Z0-9\-.]", kw):
        return re.compile(re.escape(kw), re.IGNORECASE)
    # 普通英文词（纯小写无符号）：词边界 + 可选复数
    if _is_pure_en(kw) and " " not in kw:
        return re.compile(rf"\b{re.escape(kw)}(?:s|es)?\b", re.IGNORECASE)
    return re.compile(re.escape(kw), re.IGNORECASE)


KEYWORDS = {cat: [(_kw_re(kw), kw) for kw in kws] for cat, kws in CATS.items()}

# 旧分类 → 兜底新分类（仅零命中时使用；按描述内特征再细分）
# 高频开源项目硬编码归属（修正泛词误伤与零命中盲区，仅收录确定无疑的）
WHITELIST = {
    "openttd/openttd": "游戏",
    "rommapp/romm": "游戏",
    "mitmproxy/mitmproxy": "开发",
    "rurban/smhasher": "开发",
    "skypjack/entt": "开发",
    "markdown-it/markdown-it": "开发",
    "solido/awesome-flutter": "开发",
    "clockworklabs/spacetimedb": "开发",
    "que-rb/que": "开发",
    "puckeditor/puck": "开发",
    "google/model-viewer": "创作",
    "mastodon/mastodon": "效率工具",
    "umami-software/umami": "效率工具",
    "stablyai/orca": "AI工具",
    "langgenius/dify": "AI工具",
    "anthropics/claude-code": "AI工具",
    "aaronfeng753/waifu2x-extension-gui": "AI工具",
    "upstash/context7": "AI工具",
    "spotiflacapp/spotiflac-mobile": "音乐",
    "mozilla/pdf.js": "阅读",
    "xiuming/cloudflarespeedtest": "加速工具",
}

FALLBACK = {
    "设计与创作": "设计",
    "音乐与阅读": "音乐",
    "影视": "影视",
    "游戏": "游戏",
    "开发": "开发",
    "AI工具": "AI工具",
    "效率工具": "效率工具",
}


def classify_for(project) -> list[str]:
    """整项目分类：先查白名单，再按关键词打分。"""
    fn = (project.get("full_name") or "").lower()
    if fn in WHITELIST:
        return [WHITELIST[fn]]
    name = project.get("name") or ""
    desc = project.get("description") or ""
    lang = project.get("language") or ""
    topics = project.get("topics") or []
    text = " ".join([name, project.get("full_name") or "", desc, lang, " ".join(topics)])
    return classify(text)


def classify(text: str) -> list[str]:
    """返回按得分降序的命中分类列表（得分相同按配置顺序）。"""
    hits: dict[str, int] = {}
    for cat, kws in KEYWORDS.items():
        score = 0
        for rx, kw in kws:
            if rx.search(text):
                score += 1
        if score:
            hits[cat] = score
    if not hits:
        return []
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], list(CATS).index(kv[0])))
    return [c for c, _ in ranked]


def main():
    apply = "--apply" in sys.argv
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT id, name, full_name, description, language, topics, category, categories FROM projects"
    ).fetchall()

    stats: dict[str, int] = {}
    fallback_used = 0
    multi = 0
    for pid, name, full_name, desc, lang, topics, old_cat, old_cats in rows:
        proj = {
            "name": name or "", "full_name": full_name or "", "description": desc or "",
            "language": lang or "", "topics": json.loads(topics) if topics else [],
        }
        hits = classify_for(proj)
        if not hits:
            fallback_used += 1
            new_main = FALLBACK.get(old_cat, old_cat)
            new_cats = [new_main]
        else:
            new_main = hits[0]
            new_cats = hits
        if len(new_cats) > 1:
            multi += 1
        stats[new_main] = stats.get(new_main, 0) + 1
        if apply:
            db.execute(
                "UPDATE projects SET category=?, categories=? WHERE id=?",
                (new_main, json.dumps(new_cats, ensure_ascii=False), pid),
            )
    if apply:
        db.commit()
    print("=== 新分类分布 ===")
    for c, n in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"{c}: {n}")
    print(f"总数 {sum(stats.values())} · 跨界多分类 {multi} · 零命中兜底 {fallback_used}")
    db.close()
    print("模式:", "APPLIED（已写库）" if apply else "DRY-RUN（未改动）")


if __name__ == "__main__":
    main()
