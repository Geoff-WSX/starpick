"""每日精选评分（双模式）：
- 模式A（新版优先）：有更新检查标记的新版本项目优先
- 模式B（热度加权）：stars + 活跃度 + 中文友好加权

组合规则：先取模式A前 N/2，再用模式B补足；同分类最多 2 个（分类多样性）。
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


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


def pick_daily(db, pick_date: str | None = None, count: int = 5) -> list[dict]:
    """生成每日精选。返回 [{'project_id','score'}, ...]（已按 rank 排序）。
    pick_date 默认今天（UTC 日期）。count 钳制 3~20。
    """
    if count < 3:
        count = 5
    if count > 20:
        count = 20
    date_str = pick_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 候选池：有一定热度的项目（避免收录僵尸仓库）
    cands = db.list_pickable(min_stars=20)
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
