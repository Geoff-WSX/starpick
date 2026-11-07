"""每日精选评分（双模式）：
- 模式A（新版优先）：有更新检查标记的新版本项目优先
- 模式B（热度加权）：stars + 活跃度 + 中文友好加权

组合规则：先取模式A前 N/2，再用模式B补足；同分类最多 2 个（分类多样性）。

**去重窗口**（2026-11 修复）：候选池排除「最近 N 天已精选过」的项目，
否则候选池固定 + 评分固定 = 每天算出同一批 Top 5（实测连续三天完全相同）。
窗口逐级回退（30 天 → 14 天 → 7 天 → 不排除），保证池子小的时候仍能出榜。
"""
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# 去重窗口（天）。按顺序尝试，第一个「剩余候选 >= count * 3」的窗口胜出。
_EXCLUDE_WINDOWS = (30, 14, 7, 0)


def _hot_score(project) -> float:
    """热度分：stars 对数缩放 + 最近活跃 + 中文友好加分。"""
    stars = int(project["stars"] or 0)
    base = (1 + stars) ** 0.35  # 对数缩放，避免头部垄断
    try:
        pushed = datetime.fromisoformat(project["pushed_at"].replace("Z", "+00:00"))
        days = max((datetime.now(timezone.utc) - pushed).days, 0)
        freshness = max(1.0 - days / 365.0, 0.1)
    except Exception:
        freshness = 0.5
    zh = 0.15 if int(project.get("zh_friendly") or 0) else 0.0
    return round(base * freshness + zh, 4)


def _pick_candidates(db, pick_date: str, count: int) -> list[dict]:
    """按去重窗口取候选池：窗口越窄池子越大，取第一个够用的。"""
    for days in _EXCLUDE_WINDOWS:
        if days <= 0:
            return db.list_pickable(min_stars=20)
        since = (datetime.strptime(pick_date, "%Y-%m-%d")
                 - timedelta(days=days)).strftime("%Y-%m-%d")
        remaining = db.pickable_count(min_stars=20, exclude_picked_since=since)
        if remaining >= count * 3:
            return db.list_pickable(min_stars=20, exclude_picked_since=since)
    # 所有窗口都不够（池子极小）：退回不排除，至少能出榜
    return db.list_pickable(min_stars=20)


def pick_daily(db, pick_date: str | None = None, count: int = 5) -> list[dict]:
    """生成每日精选。返回 [{'project_id','score'}, ...]（已按 rank 排序）。
    pick_date 默认今天（UTC 日期）。count 钳制 3~20。
    """
    if count < 3:
        count = 5
    if count > 20:
        count = 20
    date_str = pick_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 候选池：有一定热度的项目（避免收录僵尸仓库），并排除近期已精选的
    cands = _pick_candidates(db, date_str, count)
    if not cands:
        return []
    for c in cands:
        c["zh_friendly"] = int(c.get("zh_friendly") or 0)
        c["has_update"] = int(c.get("has_update") or 0)

    # 模式A：有新版
    updated = [c for c in cands if c["has_update"]]
    # 模式B：热度
    for c in cands:
        c["score"] = _hot_score(c)

    picked: list[dict] = []
    seen_cat: dict[str, int] = {}

    def _can_add(c: dict) -> bool:
        cat = c["category"] or "其他"
        return seen_cat.get(cat, 0) < 2

    def _take(group: list[dict], need: int) -> None:
        for c in sorted(group, key=lambda x: x["score"], reverse=True):
            if need <= 0:
                break
            if c["id"] in {x["project_id"] for x in picked}:
                continue
            if _can_add(c):
                picked.append({"project_id": c["id"], "score": c["score"]})
                seen_cat[c["category"] or "其他"] = seen_cat.get(c["category"] or "其他", 0) + 1
                need -= 1

    half = max(count // 2, 1)
    _take(updated, half)           # 新版优先占一半
    _take(cands, count - len(picked))  # 热度补足（含已拿过的可重复评分，但去重保护）

    for i, item in enumerate(picked):
        item["rank"] = i + 1
    log.info("每日精选 %s：%d 个（候选 %d）", date_str, len(picked), len(cands))
    return picked
