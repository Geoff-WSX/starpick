"""采集流水线：防重入锁 → 搜索 → 二次抓取（并发）→ 筛选 → 排序 → 去重入库。

生产级要点：
- 防重入锁：rounds 表 running 状态 + 进程内线程锁，杜绝并发双轮
- 单轮失败不阻塞下一轮：整轮异常写入 rounds.error，返回失败摘要
- 中文友好仅加权；平台/要求缺失自动降级
- 关键词 OR 合并：每类一次搜索（减少配额消耗 + 提升召回）
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .db import Database
from .github_api import GitHubClient, GitHubError
from .requirements import detect_zh_friendly, extract_system_requirements, infer_platforms, major_version_of

log = logging.getLogger("kazumi.collector")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# GitHub 搜索 API 限制：单条查询最多 5 个 AND/OR/NOT 运算符 → 每组最多 6 个关键词
_MAX_OR_TERMS = 6
# 多语言变体重探窗口：中/英变体仍为空的项目，距上次变体探测超过 N 天再重试一次
# （捕获取链接式探测上线前的漏采与镜像瞬时失败；避免每次启动都全库重扫）
_VARIANT_RETRY_DAYS = 30
# 入库的变体正文按语言截断（个别 README 极大，避免库与详情接口被撑大）
_VARIANT_MAX_CHARS = 400_000
# 中/英变体有独立列（列表展示与中文友好判定在用），不进 readme_variants JSON
_CORE_README_LANGS = ("default", "zh", "en")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _variants_json(bundle: dict | None) -> str:
    """README 采集结果里的「其它语言」正文 → 入库 JSON（中/英在各自列，不重复存）。

    '{}' 表示这轮全语言采集跑过但只有中/英（后续不再重探）；空串留给「本次没采到
    结果」（探测抛错）的情况，让库里的旧值保留、下个窗口再试。
    """
    extras = {
        lang: (content or "")[:_VARIANT_MAX_CHARS]
        for lang, content in (bundle or {}).items()
        if lang not in _CORE_README_LANGS and (content or "").strip()
    }
    return json.dumps(extras, ensure_ascii=False)


def _row_to_cand(row) -> dict:
    """SQLite 行 → 可直接回传 upsert 的候选字典（JSON 列还原成 list，避免二次编码）。"""
    cand = dict(row)
    for key in ("topics", "platforms", "system_requirements", "assets", "categories"):
        raw = cand.get(key)
        if isinstance(raw, str) and raw:
            try:
                cand[key] = json.loads(raw)
            except json.JSONDecodeError:
                cand[key] = []
    return cand


def _is_meta_asset(name: str) -> bool:
    """判断 release 资产是否为校验/元数据文件（黑名单扩展名，白名单安装包不受影响）。"""
    low = (name or "").lower().strip()
    if not low:
        return True
    # 校验和文件：checksum 内容文件（含 .sha256 等后缀），以及常见命名模式
    if re.search(r"\.(sha1|sha256|sha512|md5|sig|asc|sum|blockmap)$", low):
        return True
    if re.search(r"\.(yml|yaml|json)$", low):
        return True
    # 无扩展名的校验文件（SHA256SUMS / SHA512SUMS / checksums 等）
    if "." not in low and re.search(r"(sum|checksum|hash|sha)", low):
        return True
    # latest.yml / *.sha256 这类带哈希指纹的附加文件
    if re.search(r"\.(txt|patch|diff)$", low) and re.search(
        r"(checksum|sha|sum|release-?notes|changelog|version|latest|update)", low
    ):
        return True
    return False


def _asset_platform(name: str) -> str:
    """按发布文件名推断目标平台（单资产级，用于版本选择器分组）。"""
    low = (name or "").lower()
    if any(k in low for k in ("windows", "win64", "win32", "win-", "win_", ".exe", ".msi", ".msix")):
        return "Windows"
    if any(k in low for k in ("macos", "darwin", "osx", "mac-", ".dmg", ".pkg")):
        return "macOS"
    if any(k in low for k in ("linux", "ubuntu", "debian", "appimage", ".deb", ".rpm", ".tar.gz", ".tgz")):
        return "Linux"
    if "android" in low or low.endswith(".apk") or ".aab" in low:
        return "Android"
    if "ios" in low or low.endswith(".ipa"):
        return "iOS"
    if any(k in low for k in ("docker", "container")):
        return "Docker"
    if "web" in low or low.endswith(".html"):
        return "Web"
    return ""


def build_category_queries(keywords: list[str], min_stars: int, pushed_cutoff: str) -> list[str]:
    """把分类关键词拆成若干次 OR 查询（每组 ≤6 关键词，规避 GitHub 5 运算符上限）。

    - 中文关键词加引号保证精确短语匹配（"追剧" 与 "番剧采集" 分词语义不同）
    - 英文关键词不加引号，容忍复数/派生形态（anime → animes）
    - 返回列表；调用方对每组结果合并后按 star 取 top，重叠候选天然去重
    """
    terms: list[str] = []
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        terms.append(f'"{kw}"' if _CJK_RE.search(kw) else kw)
    if not terms:
        return []
    queries: list[str] = []
    for i in range(0, len(terms), _MAX_OR_TERMS):
        group = terms[i : i + _MAX_OR_TERMS]
        q = f'{" OR ".join(group)} in:name,description'
        if min_stars and min_stars > 0:
            q += f" stars:>={min_stars}"
        if pushed_cutoff:
            q += f" pushed:>={pushed_cutoff}"
        queries.append(q)
    return queries


def _pushed_cutoff(max_inactive_days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_inactive_days)
    return cutoff.strftime("%Y-%m-%d")


def _local_today(timezone_name: str) -> str:
    """今日日期（按配置时区）：每日精选的 pick_date 口径。

    UTC 日期与本地日期差 8 小时（Asia/Shanghai），凌晨生成的精选若标 UTC
    日期会被记到"昨天"；时区字符串非法时退回 UTC，避免启动失败。
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone_name or "UTC")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _score(stars: int, pushed_at: str, zh: bool, zh_weight: float) -> float:
    """综合评分：热度（star 对数）+ 活跃加权 + 中文加分。"""
    score = 0.0
    if stars > 0:
        score += math.log10(stars + 1)
    try:
        pushed = datetime.strptime(pushed_at[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - pushed).days
        score += max(0.0, 1.0 - age_days / 365.0) * 0.5
    except (ValueError, TypeError):
        pass
    if zh:
        score += zh_weight
    return round(score, 3)


# ---------------- 采集进度（供 /api/collect/status 轮询展示） ----------------
class CollectProgress:
    """采集进度快照：阶段 / 分类进度 / 详情抓取 / 安装包回填。线程安全（内部锁）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.running = False
        self.phase = ""                 # searching / enriching / storing / backfilling / finished
        self.message = ""
        self.current_category = ""
        self.categories_done = 0
        self.categories_total = 0
        self.enriched_done = 0
        self.enriched_total = 0
        self.backfill_done = 0
        self.backfill_total = 0
        self.started_at = ""
        self.updated_at = ""

    def start(self, started_at: str, categories_total: int) -> None:
        with self._lock:
            self._reset()
            self.running = True
            self.phase = "searching"
            self.started_at = started_at
            self.categories_total = categories_total
            self._touch()

    def set(self, *, phase: str = "", message: str = "", current_category: str = "",
            categories_done: int = -1, categories_total: int = -1,
            enriched_done: int = -1, enriched_total: int = -1,
            backfill_done: int = -1, backfill_total: int = -1) -> None:
        with self._lock:
            if phase:
                self.phase = phase
            if message:
                self.message = message
            if current_category:
                self.current_category = current_category
            if categories_done >= 0:
                self.categories_done = categories_done
            if categories_total >= 0:
                self.categories_total = categories_total
            if enriched_done >= 0:
                self.enriched_done = enriched_done
            if enriched_total >= 0:
                self.enriched_total = enriched_total
            if backfill_done >= 0:
                self.backfill_done = backfill_done
            if backfill_total >= 0:
                self.backfill_total = backfill_total
            self._touch()

    def finish(self, ok: bool) -> None:
        with self._lock:
            self.running = False
            self.phase = "finished"
            self.message = "采集完成" if ok else "采集失败"
            self._touch()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "phase": self.phase,
                "message": self.message,
                "current_category": self.current_category,
                "categories_done": self.categories_done,
                "categories_total": self.categories_total,
                "enriched_done": self.enriched_done,
                "enriched_total": self.enriched_total,
                "backfill_done": self.backfill_done,
                "backfill_total": self.backfill_total,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
            }

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class UpdateProgress:
    """更新检查进度快照：done/total + updated/nochange/failed 统计。线程安全（内部锁）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.running = False
        self.phase = ""                 # checking / finished
        self.message = ""
        self.done = 0
        self.total = 0
        self.updated = 0
        self.nochange = 0
        self.failed = 0
        self.started_at = ""
        self.updated_at = ""

    def start(self, started_at: str) -> None:
        with self._lock:
            self._reset()
            self.running = True
            self.phase = "checking"
            self.started_at = started_at
            self._touch()

    def set(self, *, phase: str = "", message: str = "", done: int = -1, total: int = -1,
            updated: int = -1, nochange: int = -1, failed: int = -1) -> None:
        with self._lock:
            if phase:
                self.phase = phase
            if message:
                self.message = message
            if done >= 0:
                self.done = done
            if total >= 0:
                self.total = total
            if updated >= 0:
                self.updated = updated
            if nochange >= 0:
                self.nochange = nochange
            if failed >= 0:
                self.failed = failed
            self._touch()

    def finish(self, ok: bool) -> None:
        with self._lock:
            self.running = False
            self.phase = "finished"
            self.message = "检查完成" if ok else "检查失败"
            self._touch()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "phase": self.phase,
                "message": self.message,
                "done": self.done,
                "total": self.total,
                "updated": self.updated,
                "nochange": self.nochange,
                "failed": self.failed,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
            }

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Collector:
    def __init__(self, cfg: Config, db: Database, client: GitHubClient | None = None):
        self.cfg = cfg
        self.db = db
        self.client = client or GitHubClient(cfg)
        self._run_lock = threading.Lock()
        self._backfill_futures: set = set()
        self._backfill_all_lock = threading.Lock()
        self.progress = CollectProgress()
        self._update_lock = threading.Lock()
        self.update_progress = UpdateProgress()

    def apply_token(self, token: str) -> None:
        """热更新 GitHub Token（前端设置页保存后立即生效，无需重启）。"""
        self.client.apply_token(token)

    # ---------------- 防重入（检查 + 建轮次原子化） ----------------
    def _acquire_run(self, source: str = "manual") -> int | None:
        """在进程内锁中完成"检查 running + 创建轮次"，杜绝检查与创建之间的竞态窗口。

        返回新建轮次 id；已有 running 轮次时返回 None（跳过本轮）。
        崩溃/重启遗留的 running 行由启动时 db.clear_stale_running() 清理。
        """
        with self._run_lock:
            if self.db.has_running_round():
                log.warning("检测到采集任务运行中，跳过本轮（防重入）")
                return None
            started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return self.db.create_round(started_at, source)

    def is_running(self) -> bool:
        """是否有采集轮次正在执行（供优雅停机判断）。"""
        with self._run_lock:
            return self.db.has_running_round()

    # ---------------- 主流程 ----------------
    def collect_once(
        self,
        min_stars: int | None = None,
        max_inactive_days: int | None = None,
        per_category_top: int | None = None,
        categories: list[str] | None = None,
        custom_category: dict | None = None,
        source: str = "manual",
        discovery_only: bool = False,
    ) -> dict[str, Any]:
        round_id = self._acquire_run(source)
        if round_id is None:
            return {"status": "skipped", "reason": "已有采集任务运行中"}

        # 全轮耗时起点：从建轮次开始计时（覆盖分类搜索与限流排队等待）
        self._t0 = time.monotonic()
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.progress.start(started_at, 1 if discovery_only else len(self.cfg.categories))
        summary: dict[str, Any] = {"status": "failed", "categories": {}, "stats": {}}
        try:
            if discovery_only:
                summary = self._run_discovery(round_id, min_stars)
            else:
                summary = self._run(round_id, min_stars, max_inactive_days,
                                    per_category_top, categories, custom_category)
            stats = summary.get("stats", {})
            # 每日精选：全量轮必刷；发现轮仅在真的有新入库时刷（避免一天内反复换榜）
            if stats.get("mode") != "discovery" or stats.get("inserted", 0) > 0:
                try:
                    from app.picker import pick_daily
                    picks = pick_daily(self.db)
                    if picks:
                        self.db.save_daily_pick(_local_today(self.cfg.timezone), picks)
                except Exception as exc:
                    log.warning("每日精选生成失败: %s", exc)
            self.db.finish_round(
                round_id,
                summary.get("status", "failed"),
                summary.get("categories", {}),
                summary.get("stats", {}),
                summary.get("error", ""),
            )
        except Exception as exc:  # 兜底：任何异常都要落库，保证下一轮能跑
            log.exception("采集任务异常终止")
            self.db.finish_round(
                round_id, "failed", {}, {}, f"{type(exc).__name__}: {exc}"
            )
            summary = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self.progress.finish(ok=summary.get("status") == "success")
            self.db.prune_rounds(self.cfg.rounds_retention)
            # 大表定期清理（快照/通知无上限会持续膨胀）
            self.db.prune_health_snapshots()
            self.db.prune_notifications()
        return summary

    def _run(
        self,
        round_id: int,
        min_stars: int | None = None,
        max_inactive_days: int | None = None,
        per_category_top: int | None = None,
        categories: list[str] | None = None,
        custom_category: dict | None = None,
    ) -> dict[str, Any]:
        candidates: list[dict] = []
        categories_summary: dict[str, int] = {}
        # 本轮分类集合：默认全部内置分类；可叠加自定义分类、可只跑选中子集
        cats: dict[str, list[str]] = dict(self.cfg.categories)
        if custom_category and custom_category.get("name"):
            cname = str(custom_category["name"]).strip()
            ckeys = [str(k).strip() for k in (custom_category.get("keywords") or []) if str(k).strip()]
            if cname and ckeys:
                cats[cname] = ckeys
        if categories:
            cats = {k: v for k, v in cats.items() if k in categories}
        categories_total = len(cats)
        # 合并/过滤后的分类总数写回进度（collect_once 的 start 只登记内置数）
        self.progress.set(categories_total=categories_total)
        # 手动触发可覆盖过滤阈值（None=用配置默认值；0=不限）
        min_stars = self.cfg.min_stars if min_stars is None else min_stars
        per_cat = (
            self.cfg.per_category_top
            if per_category_top is None or per_category_top <= 0
            else per_category_top
        )
        max_days = (
            self.cfg.max_inactive_days
            if max_inactive_days is None
            else max_inactive_days
        )
        if max_days and max_days > 0:
            pushed_cutoff = _pushed_cutoff(max_days)
        else:
            pushed_cutoff = None  # 不限活跃时间：不拼 pushed 条件
        done = 0
        for category, keywords in cats.items():
            self.progress.set(
                message=f"正在搜索分类「{category}」",
                current_category=category,
            )
            queries = build_category_queries(keywords, min_stars, pushed_cutoff)
            if not queries:
                done += 1
                self.progress.set(categories_done=done)
                continue
            cat_hits: list[dict] = []
            for query in queries:
                try:
                    items = self.client.search_repositories(
                        query, per_page=100, max_items=per_cat
                    )
                except GitHubError as exc:
                    log.error("分类 %s 搜索失败: %s", category, exc)
                    continue
                for it in items:
                    full_name = it.get("full_name", "")
                    if not full_name:
                        continue
                    cat_hits.append(_to_candidate(it, category))
            # 每分类按 star 预排后全量纳入（受本轮 per_cat 上限约束）
            cat_hits.sort(key=lambda c: c["stars"], reverse=True)
            picked = cat_hits[:per_cat]
            candidates.extend(picked)
            categories_summary[category] = len(picked)
            log.info("分类 %s 候选 %d 个", category, len(picked))
            done += 1
            self.progress.set(
                categories_done=done,
                message=f"分类搜索完成 {done}/{categories_total}",
            )

        if not candidates:
            return {
                "status": "failed",
                "categories": categories_summary,
                "stats": {"candidates": 0, "inserted": 0, "updated": 0, "skipped": 0},
                "error": "本轮未搜索到任何候选（网络/限流导致）",
            }

        to_enrich, self._last_skipped_fresh = self._skip_fresh(candidates)
        enriched = self._enrich(to_enrich)       # 并发抓 README（快，不受 release 限流拖累）
        enriched = self._filter_stars(enriched, min_stars)  # 代码层热度兜底（用本轮生效阈值）
        self.progress.set(phase="storing", message="正在入库与去重…")
        inserted, updated, skipped, stored = self._store(enriched, round_id)
        self._start_backfill(enriched)           # release assets/直链 后台异步补全（含已入库项目）

        return {
            "status": "success",
            "categories": categories_summary,
            "stats": {
                "mode": "full",
                "candidates": len(candidates),
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped,
                "skipped_fresh": self._last_skipped_fresh,
                "duration_sec": round(time.monotonic() - self._t0, 1) if hasattr(self, "_t0") else 0,
                "filter_used": {
                    "min_stars": min_stars,
                    "max_inactive_days": max_days,
                    "per_category_max": per_cat,
                },
            },
        }

    # ---------------- 发现轮（轻量）：只搜近期创建的高星新仓库 ----------------
    def _category_patterns(self) -> list[tuple[str, list]]:
        """分类关键词匹配模式：内置分类（配置）+ 自定义分类（DB），保持配置顺序。

        语义对齐 GitHub 搜索查询构造（build_category_queries）：
        - 含中文关键词 → 引号短语 ≈ 子串匹配
        - 纯英文关键词 → 各词独立出现即命中（GitHub 隐式 AND），\\b 防止 rag⊂storage 类误配
        """
        cats: dict[str, list[str]] = {k: list(v) for k, v in self.cfg.categories.items()}
        for row in self.db.list_custom_categories():
            kws = [k.strip() for k in (row["keywords"] or "").split(",") if k.strip()]
            if kws:
                cats.setdefault(row["name"], kws)
        patterns: list[tuple[str, list]] = []
        for name, kws in cats.items():
            compiled = []
            for kw in kws:
                kw = (kw or "").strip()
                if not kw:
                    continue
                low = kw.lower()
                if _CJK_RE.search(kw):
                    compiled.append(("sub", [re.compile(re.escape(low))]))
                else:
                    compiled.append(("words", [
                        re.compile(r"\b" + re.escape(w) + r"\b") for w in low.split()
                    ]))
            if compiled:
                patterns.append((name, compiled))
        return patterns

    def _run_discovery(self, round_id: int, min_stars: int | None = None) -> dict[str, Any]:
        """发现轮：1~2 次搜索请求发现近期创建的高星新仓库，本地按分类关键词归类。

        对比全量扫荡（~35 次搜索、分钟级）：发现轮秒级完成，承担高频新库发现；
        全量扫荡每日一次，负责存量关键词召回与 star/活跃时间刷新（touch_seen）。
        """
        min_stars = self.cfg.min_stars if min_stars is None else min_stars
        created_since = (
            datetime.now(timezone.utc)
            - timedelta(hours=self.cfg.discovery_created_hours)
        ).strftime("%Y-%m-%d")
        query = f"created:>={created_since}"
        if min_stars > 0:
            query += f" stars:>={min_stars}"
        self.progress.set(
            phase="searching", current_category="全站新仓库", categories_total=1,
            message=f"发现轮：搜索最近 {self.cfg.discovery_created_hours}h 创建的高星仓库…",
        )
        try:
            items = self.client.search_repositories(query, per_page=100, max_items=200)
        except GitHubError as exc:
            return {
                "status": "failed", "categories": {},
                "stats": {"mode": "discovery", "candidates": 0},
                "error": f"发现轮搜索失败: {exc}",
            }
        patterns = self._category_patterns()
        candidates: list[dict] = []
        matched_names: set[str] = set()
        for it in items:
            if not it.get("full_name"):
                continue
            for cat in _match_categories(it, patterns):
                candidates.append(_to_candidate(it, cat))
                matched_names.add(it["full_name"])
        self.progress.set(
            categories_done=1,
            message=f"发现轮：{len(items)} 个新仓库，{len(matched_names)} 个命中分类关键词",
        )
        log.info("发现轮：新仓库 %d 个，命中分类 %d 个", len(items), len(matched_names))
        stats: dict[str, Any] = {
            "mode": "discovery",
            "candidates": len(candidates),
            "discovered": len(items),
            "matched": len(matched_names),
            "filter_used": {"min_stars": min_stars,
                            "discovery_created_hours": self.cfg.discovery_created_hours},
        }
        if not candidates:
            # 发现轮空结果是常态（近期恰好没有命中关键词的新仓库），区别于全量轮的空结果=失败
            stats.update({"inserted": 0, "updated": 0, "skipped": 0,
                          "duration_sec": round(time.monotonic() - self._t0, 1)})
            return {"status": "success", "categories": {}, "stats": stats}

        to_enrich, skipped_fresh = self._skip_fresh(candidates)
        enriched = self._enrich(to_enrich)
        enriched = self._filter_stars(enriched, min_stars)
        self.progress.set(phase="storing", message="正在入库与去重…")
        inserted, updated, skipped, _stored = self._store(enriched, round_id)
        self._start_backfill(enriched)
        stats.update({
            "inserted": inserted, "updated": updated, "skipped": skipped,
            "skipped_fresh": skipped_fresh,
            "duration_sec": round(time.monotonic() - self._t0, 1),
        })
        return {"status": "success", "categories": {}, "stats": stats}

    # ---------------- 跳过近期已采集（只拉新项目，减少重复耗时） ----------------
    def _skip_fresh(self, candidates: list[dict]) -> tuple[list[dict], int]:
        """已入库 + README 完整 + 近 skip_existing_days 天入库的项目跳过详情抓取。

        返回 (需要抓取的候选, 跳过数量)。skip_existing_days<=0 时不跳过（全量刷新）。
        """
        days = self.cfg.skip_existing_days
        if not days or days <= 0:
            return candidates, 0
        existing = self.db.get_existing_batch([c.get("full_name", "") for c in candidates])
        if not existing:
            return candidates, 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        to_enrich: list[dict] = []
        skipped = 0
        for c in candidates:
            row = existing.get(c.get("full_name", ""))
            if (
                row is not None
                and (row["readme_len"] or 0) > 50
                and row["last_recommended_at"]
                and row["last_recommended_at"] >= cutoff
            ):
                skipped += 1
            else:
                to_enrich.append(c)
        if skipped:
            log.info("跳过 %d 个近期已采集项目（%d 天内）", skipped, days)
            self.progress.set(
                message=f"跳过 {skipped} 个近期已采集项目（{days} 天内），只抓取新项目…"
            )
        return to_enrich, skipped

    # ---------------- 二次抓取（并发） ----------------
    def _enrich(self, candidates: list[dict]) -> list[dict]:
        self.progress.set(phase="enriching", enriched_total=len(candidates),
                          message="正在抓取项目详情…")
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.cfg.concurrency) as pool:
            futs = {pool.submit(self._enrich_one, c): c for c in candidates}
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception:
                    log.exception("二次抓取任务异常")
                self.progress.set(
                    enriched_done=len(results),
                    message=f"抓取项目详情 {len(results)}/{len(candidates)}",
                )
        return results

    def _enrich_one(self, cand: dict) -> dict:
        """抓 README 原文 + 项目自带多语言变体（快）；release 直链由 _backfill_one 后台补全。"""
        full_name = cand["full_name"]
        try:
            bundle = self.client.get_readme_bundle(full_name)
            readme = bundle.get("default")
            cand["readme_zh"] = bundle.get("zh") or ""
            cand["readme_en"] = bundle.get("en") or ""
            cand["readme_variants"] = _variants_json(bundle)
            cand["ml_probed"] = 1
            cand["ml_variants_at"] = _utc_now()
        except GitHubError as exc:
            log.warning("README 抓取失败 %s: %s", full_name, exc)
            readme = None
            cand["readme_zh"] = ""
            cand["readme_en"] = ""

        cand["readme"] = readme or ""
        cand["platforms"] = infer_platforms([], cand["readme"])
        cand["download_url"] = cand.get("html_url", "")
        cand["major_version"] = ""
        cand["assets"] = []

        cand["system_requirements"] = extract_system_requirements(cand["readme"])
        cand["zh_friendly"] = detect_zh_friendly(
            cand["readme"], cand.get("description", "")
        ) or bool(cand["readme_zh"])  # 项目自带中文 README 亦视为中文友好
        return cand

    # ---------------- release 后台补全（不阻塞入库；无 Token 时按 60s 排队） ----------------
    def _start_backfill(self, enriched: list[dict]) -> None:
        if not enriched:
            return
        self.progress.set(phase="backfilling", backfill_total=len(enriched),
                          message="后台探测安装包（受限流排队，可先行浏览）…")
        pool = ThreadPoolExecutor(max_workers=self.cfg.concurrency)
        for c in enriched:
            fut = pool.submit(self._backfill_one, c)
            self._backfill_futures.add(fut)
            # 完成即从集合移除，避免长期运行下 futures 无界累积（内存泄漏面）
            fut.add_done_callback(self._backfill_futures.discard)
        pool.shutdown(wait=False)




    def _backfill_one(self, cand: dict, track_progress: bool = True) -> None:
        """探测最新 release：补全多平台 assets、下载直链、大版本号后更新入库。

        - assets 为完整发布产物列表（前端按平台渲染版本选择，不再默认下载第一个文件）
        - 失败保持现状（download_url 兜底为 html_url），不阻塞采集
        - track_progress=False：存量全库回填（backfill_all）复用本函数时不扰动本轮进度
        """
        try:
            release = self.client.get_latest_release(cand["full_name"])
        except GitHubError as exc:
            log.warning("release 补全失败 %s: %s", cand["full_name"], exc)
            return  # 失败不落探测标记，下次启动/下一轮重试
        if not release:
            # 项目确无 release（404）：落探测标记，避免每次启动全库重复探测
            self.db.mark_assets_probed(cand["full_name"])
            return
        assets = release.get("assets", [])
        asset_list = _release_assets_to_list(release)
        cand["assets"] = asset_list
        cand["platforms"] = infer_platforms([a.get("name", "") for a in assets], cand.get("readme", ""))
        cand["download_url"] = next(
            (a["url"] for a in asset_list if a["url"]), ""
        ) or cand.get("html_url", "")
        cand["major_version"] = major_version_of(release.get("tag_name", ""))
        # 主分类保持入库时的值（跨界项目由 _store/add_category 管理分类，回填不改主分类）
        existing = self.db.project_exists(cand["full_name"])
        if existing is not None:
            cand["category"] = existing["category"]
        try:
            self.db.upsert_project(cand)
            self.db.mark_assets_probed(cand["full_name"])  # 探测成功（含仅有元数据产物）
        except Exception as exc:
            log.warning("release 补全入库失败 %s: %s", cand["full_name"], exc)
        finally:
            # 回填进度：线程安全累加（不精确到单个项目，仅供 UI 展示大致进度）
            if track_progress:
                with self.progress._lock:
                    self.progress.backfill_done += 1
                    self.progress.message = (
                        f"安装包探测 {self.progress.backfill_done}/"
                        f"{max(self.progress.backfill_total, 1)}（后台）"
                    )
                    self.progress._touch()

    def wait_backfill(self, timeout: float = 30.0) -> None:
        """等待全部后台补全完成（测试与优雅停机用）。"""
        for fut in list(self._backfill_futures):
            try:
                fut.result(timeout=timeout)
            except Exception:
                log.warning("release 补全任务异常", exc_info=True)
            self._backfill_futures.discard(fut)

    # ---------------- 全库回填：README 原文（存量空值补抓） ----------------
    def backfill_readme(self) -> None:
        """对 README 为空的存量项目补抓原文（多通道：raw → 镜像 → API 兜底）。

        - raw/镜像不占 API 配额，并发快；仅兜底通道受 core 限流
        - 防重入：与 assets 回填共用同一把锁；并发触发时阻塞等待对方完成再执行（最多 1 小时）
        """
        if not self._backfill_all_lock.acquire(timeout=3600):
            log.info("README 回填等待超时，跳过本次触发")
            return
        try:
            rows = self.db.list_projects_missing_readme()
            if not rows:
                log.info("README 回填：所有项目已有 README，无需回填")
                return
            log.info("README 回填启动：%d 个存量项目待补 README", len(rows))
            pool = ThreadPoolExecutor(max_workers=min(self.cfg.concurrency, 4))
            futs = []
            for row in rows:
                futs.append(pool.submit(self._backfill_readme_one, _row_to_cand(row)))
            pool.shutdown(wait=True)
            done = 0
            for fut in futs:
                try:
                    fut.result()
                    done += 1
                except Exception:
                    log.exception("README 回填任务异常")
            log.info("README 回填结束：完成 %d / %d", done, len(futs))
        finally:
            self._backfill_all_lock.release()

    def _backfill_readme_one(self, cand: dict) -> None:
        full_name = cand["full_name"]
        try:
            bundle = self.client.get_readme_bundle(full_name)
        except Exception as exc:  # 兜底：任何异常保持现状
            log.warning("README 回填失败 %s: %s", full_name, exc)
            return
        readme = bundle.get("default") or ""
        if not readme.strip() and not (bundle.get("zh") or "").strip() \
                and not (bundle.get("en") or "").strip():
            log.debug("README 回填无内容 %s", full_name)
            cand["ml_probed"] = 1  # 已探测（项目确无 README 内容），避免反复回填
            cand["ml_variants_at"] = _utc_now()
            cand["readme_variants"] = "{}"  # 全语言采集已跑过（确实没有任何 README）
            try:
                self.db.upsert_project(cand)
            except Exception as exc:
                log.warning("README 回填入库失败 %s: %s", full_name, exc)
            return
        cand["readme"] = readme
        cand["readme_zh"] = bundle.get("zh") or ""
        cand["readme_en"] = bundle.get("en") or ""
        cand["readme_variants"] = _variants_json(bundle)
        cand["ml_probed"] = 1
        cand["ml_variants_at"] = _utc_now()
        cand["platforms"] = infer_platforms([], cand.get("readme", ""))
        cand["system_requirements"] = extract_system_requirements(cand["readme"])
        cand["zh_friendly"] = detect_zh_friendly(
            cand["readme"], cand.get("description", "")
        ) or bool(cand["readme_zh"])
        try:
            self.db.upsert_project(cand)
        except Exception as exc:
            log.warning("README 回填入库失败 %s: %s", full_name, exc)

    # ---------------- 变体回填/重探：存量项目的语言变体补齐 ----------------
    def backfill_variants(self) -> None:
        """补齐存量项目的语言变体：a) 全语言采集没跑过的；b) 超出重探窗口的。

        - 只补变体：用库内已存原文解析语言切换链接（不再抓原文），按链接里的真实
          路径探测。a 类给全语言采集上线前的老数据补采其余语言；b 类每
          _VARIANT_RETRY_DAYS 把全库重扫一遍，兜住镜像瞬时失败与上游新加/改名的
          语言变体（入库取并集，重探不会丢已采到的语言）
        - a 类不做窗口限制：探测成功即写 readme_variants（无其它语言写 '{}'），
          失败保持空串下次启动再试
        - 防重入：与 README/release 回填共用同一把锁（同锁串行，避免镜像流量叠加）
        """
        if not self._backfill_all_lock.acquire(timeout=3600):
            log.info("变体重探等待超时，跳过本次触发")
            return
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=_VARIANT_RETRY_DAYS)
            ).strftime("%Y-%m-%d %H:%M:%S")
            seen: set[int] = set()
            rows = []
            for row in self.db.list_projects_missing_all_variants() + \
                    self.db.list_projects_retry_variants(cutoff):
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                rows.append(row)
            if not rows:
                log.info("变体重探：无需重探的项目")
                return
            log.info("变体重探启动：%d 个项目（待补全语言或超出重探窗口）", len(rows))
            pool = ThreadPoolExecutor(max_workers=min(self.cfg.concurrency, 4))
            futs = [pool.submit(self._backfill_variants_one, _row_to_cand(row)) for row in rows]
            pool.shutdown(wait=True)
            done = found = 0
            for fut in futs:
                try:
                    if fut.result():
                        found += 1
                    done += 1
                except Exception:
                    log.exception("变体重探任务异常")
            log.info("变体重探结束：完成 %d / %d，新采到变体 %d 个", done, len(futs), found)
        finally:
            self._backfill_all_lock.release()

    def _backfill_variants_one(self, cand: dict) -> bool:
        """重探单个项目的语言变体（含中/英之外的全部语言）；返回是否采到变体内容。"""
        full_name = cand["full_name"]
        try:
            bundle = self.client.get_readme_bundle(
                full_name, default_readme=cand.get("readme") or ""
            )
        except Exception as exc:  # 异常不写结果，留待下次启动/下个窗口重试
            log.warning("变体重探失败 %s: %s", full_name, exc)
            return False
        zh = bundle.get("zh") or ""
        en = bundle.get("en") or ""
        cand["readme_zh"] = zh
        cand["readme_en"] = en
        cand["readme_variants"] = _variants_json(bundle)
        cand["ml_variants_at"] = _utc_now()
        if zh and not cand.get("zh_friendly"):
            # 与采集路径一致：自带中文变体即视为中文友好
            cand["zh_friendly"] = 1
        try:
            self.db.upsert_project(cand)
        except Exception as exc:
            log.warning("变体重探入库失败 %s: %s", full_name, exc)
            return False
        return bool(zh or en or cand["readme_variants"] != "{}")

    # ---------------- 全库回填（存量项目 release 探测） ----------------
    def backfill_all(self) -> None:
        """对 assets 为空的存量项目逐个探测最新 release，补全多版本下载数据。

        - 仅补全"还没有安装包记录"的项目；已有 assets 的不重复请求（省配额）
        - 受 GitHub 限流客户端约束（无 Token 时约 60 次/小时，自动排队），不阻塞采集
        - 防重入：与 README 回填共用同一把锁；并发触发时阻塞等待对方完成再执行（最多 1 小时）
        """
        if not self._backfill_all_lock.acquire(timeout=3600):
            log.info("全库回填等待超时，跳过本次触发")
            return
        try:
            rows = self.db.list_projects_missing_assets()
            if not rows:
                log.info("全库回填：所有项目已有安装包数据，无需回填")
                return
            log.info("全库回填启动：%d 个存量项目待探测 release", len(rows))
            pool = ThreadPoolExecutor(max_workers=min(self.cfg.concurrency, 4))
            futs = []
            for row in rows:
                cand = dict(row)
                # SQLite 返回的 JSON 列还原成 list，避免 upsert 时二次编码
                for key in ("topics", "platforms", "system_requirements",
                            "assets", "categories"):
                    raw = cand.get(key)
                    if isinstance(raw, str) and raw:
                        try:
                            cand[key] = json.loads(raw)
                        except json.JSONDecodeError:
                            cand[key] = []
                futs.append(pool.submit(self._backfill_one, cand, False))
            pool.shutdown(wait=True)
            done = 0
            for fut in futs:
                try:
                    fut.result()
                    done += 1
                except Exception:
                    log.exception("全库回填任务异常")
            log.info("全库回填结束：完成 %d / %d", done, len(futs))
        finally:
            self._backfill_all_lock.release()

    # ---------------- 筛选 ----------------
    def _filter_stars(self, enriched: list[dict], min_stars: int) -> list[dict]:
        """代码层热度兜底：用本轮生效的 min_stars（手动覆盖优先），0=不限。"""
        out = []
        for c in enriched:
            if c.get("stars", 0) >= min_stars:
                out.append(c)
            else:
                log.debug("候选 %s stars=%s 低于门槛，过滤", c.get("full_name"), c.get("stars"))
        return out

    # ---------------- 入库（去重 + 重大更新再推荐） ----------------
    def _store(self, enriched: list[dict], round_id: int) -> tuple[int, int, int, list[dict]]:
        inserted = updated = skipped = 0
        stored: list[dict] = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # 排序：score 高优先；score 相同时按分类字典顺序（跨界项目主分类固定为最先命中的分类）
        cat_order = {name: i for i, name in enumerate(self.cfg.categories)}

        def _sort_key(x: dict) -> tuple[float, int]:
            return (-_score(
                x.get("stars", 0), x.get("pushed_at", ""),
                bool(x.get("zh_friendly")), self.cfg.zh_weight,
            ), cat_order.get(x["category"], 99))

        for c in sorted(enriched, key=_sort_key):
            c["score"] = _score(
                c.get("stars", 0), c.get("pushed_at", ""),
                bool(c.get("zh_friendly")), self.cfg.zh_weight,
            )
            existing = self.db.project_exists(c["full_name"])
            if existing is not None:
                # 已推荐过：若本轮命中新分类，则追加为附加分类（跨界项目多分类标注）
                old_cat = existing["category"]
                if c["category"] != old_cat and c["category"] not in self.db._json_list(
                    existing["categories"]
                ):
                    self.db.add_category(c["full_name"], c["category"])
                    log.info("跨界项目 %s 追加分类「%s」（原「%s」）",
                             c["full_name"], c["category"], old_cat)
                # 已推荐过：默认跳过；开启"再推荐"时按条件点亮
                if self.cfg.re_recommend_on_major_update:
                    # 注意：候选的 major_version 在 enrich 阶段为空（release 探测在
                    # 后台 _backfill_one 异步完成，不经过 _store），故"重大版本更新"
                    # 分支无法在此判定；当前实际生效的只有 star 翻倍再推荐。
                    stars_old = existing["stars"] or 0
                    stars_new = c.get("stars", 0)
                    if stars_new >= stars_old * 2:
                        log.info("star 翻倍再推荐 %s (stars %s->%s)",
                                 c["full_name"], stars_old, stars_new)
                        c["first_seen_at"] = existing["first_seen_at"]
                        c["last_recommended_at"] = now
                        c["last_round_id"] = round_id
                        c["category"] = existing["category"]  # 主分类稳定，只累积附加分类
                        self.db.upsert_project(c)
                        stored.append(c)
                        updated += 1
                        continue
                skipped += 1
                # 已入库且未达再推荐条件：刷新 star/推荐时间后跳过。
                # 必须推进 last_recommended_at，否则满 7 天后该项目每轮都被重复抓详情。
                self.db.touch_seen(c["full_name"], c.get("stars", 0),
                                   c.get("pushed_at", ""), now, round_id)
                continue
            c["first_seen_at"] = now
            c["last_recommended_at"] = now
            c["last_round_id"] = round_id
            if self.db.upsert_project(c):
                stored.append(c)
                inserted += 1
                # 新项目收录通知（幽灵防御由 db 层保证：项目必须真实存在）
                _row = self.db.project_exists(c["full_name"])
                if _row is not None:
                    self.db.create_notification(
                        "collect", _row["id"],
                        "新项目收录：" + c["name"],
                        (c.get("description") or "")[:120],
                    )
            else:
                updated += 1
        log.info("本轮入库：新增 %d / 更新 %d / 跳过 %d", inserted, updated, skipped)
        return inserted, updated, skipped, stored

    # ---------------- 更新检查（已采集项目是否有新 release） ----------------
    def _acquire_update(self, scope: str = "incremental") -> int | None:
        """防重入：进程内锁中完成"检查 running + 建轮次"（同采集防重入模式）。"""
        with self._update_lock:
            if self.db.has_running_update_round():
                log.warning("检测到更新检查运行中，跳过本轮（防重入）")
                return None
            started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return self.db.create_update_round(started_at, scope)

    def is_update_running(self) -> bool:
        with self._update_lock:
            return self.db.has_running_update_round()

    def check_updates(self, limit: int | None = None,
                     categories: list[str] | None = None) -> dict[str, Any]:
        """检查已采集项目是否有新 release，结果写 update_rounds 历史。

        limit=None → 手动全库（scope=full）；limit=N → 定时增量检查最久未检查的 N 条（scope=incremental）。
        categories 非空时只检查这些分类的项目（可多选）。
        单仓失败不中断整轮；整轮异常兜底落库，保证下一轮可跑。
        """
        scope = "full" if limit is None else "incremental"
        round_id = self._acquire_update(scope)
        if round_id is None:
            return {"status": "skipped", "reason": "已有更新检查运行中"}
        self._ut0 = time.monotonic()
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.update_progress.start(started_at)
        try:
            rows = self.db.list_projects_for_update(limit, categories)
            total = len(rows)
            self.update_progress.set(total=total, message=f"正在检查 {total} 个项目的 release 更新")
            updated = nochange = failed = 0
            for idx, row in enumerate(rows, 1):
                try:
                    rel = self.client.get_latest_release(row["full_name"])
                    now2 = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    if rel is None:
                        # 无 release（未发版仓库）视为无变化：同样落 last_check_at，
                        # 否则增量检查按「最久未检查优先」会永远卡在同一批无 release 项目上
                        nochange += 1
                        self.db.mark_checked(row["id"], row["latest_tag"] or "", 0, now2)
                        self.db.record_health_snapshot(row["id"], row["stars"], 0, "", now2)
                    else:
                        tag = (rel.get("tag_name") or "").strip()
                        old = (row["latest_tag"] or "").strip()
                        # 首次检查（库内没有旧 tag）只初始化 latest_tag：不计更新、不发通知，
                        # 否则每个项目的第一次检查都会被误报"有新版"，污染 has_update 与每日精选
                        is_new = bool(tag and old and tag != old)
                        self.db.mark_checked(
                            row["id"], tag or old, 1 if is_new else 0, now2,
                        )
                        updated += 1 if is_new else 0
                        nochange += 0 if is_new else 1
                        if is_new:
                            # 有新版本通知
                            self.db.create_notification(
                                "update", row["id"],
                                "有新版：" + row["full_name"].split("/")[-1] + " " + (tag or ""),
                                (rel.get("body") or "")[:120],
                            )
                        # 健康度快照 + 最新 release 说明（供详情页健康曲线 / 版本说明展示）
                        self.db.record_health_snapshot(
                            row["id"], row["stars"], 1 if is_new else 0, tag, now2
                        )
                        if rel.get("body"):
                            self.db.save_release_notes(row["id"], rel.get("body") or "")
                        # 顺带刷新 assets（含 size）：同一次 release 请求，零额外配额
                        asset_list = _release_assets_to_list(rel)
                        if asset_list:
                            platforms = infer_platforms(
                                [a.get("name", "") for a in asset_list], row.get("readme", "") or ""
                            )
                            self.db.update_project_assets(row["id"], asset_list, platforms)
                except Exception as exc:  # 单仓失败不影响整轮
                    failed += 1
                    log.warning("更新检查失败 %s: %s", row["full_name"], exc)
                self.update_progress.set(
                    done=idx, updated=updated, nochange=nochange, failed=failed,
                    message=f"正在检查 {idx}/{total}…（发现新版 {updated}）",
                )
            duration = round(time.monotonic() - self._ut0, 1)
            stats = {"total": total, "updated": updated, "nochange": nochange,
                     "failed": failed, "duration_sec": duration}
            self.db.finish_update_round(round_id, "success", stats, "")
            self.update_progress.finish(ok=True)
            return {"status": "success", **stats}
        except Exception as exc:  # 兜底：整轮异常也要落库
            log.exception("更新检查异常终止")
            self.db.finish_update_round(round_id, "failed", {}, f"{type(exc).__name__}: {exc}")
            self.update_progress.finish(ok=False)
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self.db.prune_update_rounds(self.cfg.rounds_retention)


def _to_candidate(item: dict, category: str) -> dict:
    return {
        "full_name": item.get("full_name", ""),
        "name": item.get("name", ""),
        "owner": (item.get("full_name") or "/").split("/")[0],
        "description": item.get("description") or "",
        "homepage": item.get("homepage") or "",
        "stars": int(item.get("stargazers_count") or 0),
        "language": item.get("language") or "",
        "pushed_at": item.get("pushed_at") or "",
        "html_url": item.get("html_url") or "",
        "topics": item.get("topics") or [],
        "category": category,
    }


def _match_categories(item: dict, patterns: list[tuple[str, list]]) -> list[str]:
    """本地判定搜索结果命中哪些分类（按配置顺序返回全部命中，首个为主分类）。

    匹配文本 = 名称 + 简介 + topics（比 GitHub 查询的 in:name,description 略宽，
    topics 命中能提高发现轮的归类召回）。
    """
    text = " ".join([
        str(item.get("name") or ""),
        str(item.get("description") or ""),
        " ".join(str(t) for t in (item.get("topics") or [])),
    ]).lower()
    if not text.strip():
        return []
    hits: list[str] = []
    for name, compiled in patterns:
        for kind, pats in compiled:
            if kind == "sub":
                if pats[0].search(text):
                    hits.append(name)
                    break
            elif all(p.search(text) for p in pats):
                hits.append(name)
                break
    return hits


def _release_assets_to_list(release: dict) -> list[dict]:
    """release 对象 → 前端资产列表（name/url/size/platform），过滤元数据文件。"""
    asset_list: list[dict] = []
    for a in release.get("assets", []):
        name = a.get("name", "")
        url = a.get("url", "")
        if not name or not url:
            continue
        if _is_meta_asset(name):
            continue  # 过滤校验/元数据文件，只留可下载的安装包
        asset_list.append({
            "name": name,
            "url": url,
            "size": int(a.get("size") or 0),
            "platform": _asset_platform(name),
        })
    return asset_list
