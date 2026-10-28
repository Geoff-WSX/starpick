"""FastAPI 入口：API 路由 + APScheduler 定时采集 + 静态托管 + 健康检查。

运行：uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import paths
from .collector import Collector
from .config import (
    Config,
    ConfigError,
    clear_runtime_token,
    is_valid_token,
    load_config,
    set_runtime_token,
)
from .db import Database
from .github_api import resolve_repo_path, variant_lang

APP_VERSION = "1.2"
# 项目仓库地址（设置弹窗「关于」分组展示 + 更新检查）；仓库转公开后无需改代码，
# 无 token 用户即可匿名查到 Releases
GITHUB_REPO = "Geoff-WSX/starpick"
log = logging.getLogger("kazumi")

# 休眠看门狗参数：每 30s 比对挂钟与单调钟，差值超过 120s 判定为系统休眠过
SLEEP_WATCHDOG_INTERVAL_SECONDS = 30
SLEEP_GAP_THRESHOLD_SECONDS = 120
# 唤醒后等网络（WiFi/VPN）重连就绪再补跑，避免唤醒瞬间的请求全失败
WAKE_SETTLE_SECONDS = 30
# 按需抓取的 README 变体内容上限（字符）：超长 README 截断，避免响应体过大
_VARIANT_MAX_CHARS = 400_000


def _is_sleep_gap(wall_delta: float, mono_delta: float) -> bool:
    """挂钟推进明显快于单调钟 → 进程被系统休眠挂起过。

    macOS 的 time.monotonic() 不含睡眠时间，APScheduler 用 Event.wait（单调钟计时）
    等待下一个任务，因此休眠期间不会检查任务、唤醒后还要等"欠下的睡眠时长"才恢复。
    正常的进程抢占/GC 停顿会让两钟同步推进，不会误判。
    """
    return wall_delta - mono_delta > SLEEP_GAP_THRESHOLD_SECONDS

# 运行期路径（app/paths.py）：开发模式 = 项目根/logs；打包后 = 用户数据目录/logs
LOG_DIR = paths.logs_dir()
# 数据库自动备份目录（模块级常量：测试用 monkeypatch 重定向到临时目录，避免污染生产备份）
BACKUP_DIR = paths.backup_dir()


# ---------------- 日志初始化（轮转 + 结构化单行 JSON） ----------------
def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)

    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                payload["exc"] = "".join(
                    logging.Formatter().formatException(record.exc_info)
                )
            return json.dumps(payload, ensure_ascii=False)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "kazumi.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(console)


# ---------------- 全局状态 ----------------
class AppState:
    def __init__(self) -> None:
        self.cfg: Config | None = None
        self.db: Database | None = None
        self.collector: Collector | None = None
        self.scheduler: BackgroundScheduler | None = None
        # 应用自更新检查（内存态缓存，不入库）：结果 + 正在检查标志。
        # status: "none" 未查过 / "up_to_date" / "newer" / "unknown"（网络或解析失败）
        self.app_update: dict[str, Any] = {"status": "none", "latest_tag": "",
                                           "published_at": "", "checked_at": ""}
        self.app_update_lock = threading.Lock()
        self.app_update_checking = False


state = AppState()


# ---------------- 应用自更新检查 ----------------
def _parse_tag_version(tag: str) -> tuple[int, ...] | None:
    """把 "v1.2.3" 解析成数字元组用于比较；非语义化 tag 返回 None（不误判为可升级）。"""
    t = tag.strip().lstrip("vV")
    if not t or not all(part.isdigit() for part in t.split(".")):
        return None
    return tuple(int(part) for part in t.split("."))


def _check_app_update() -> None:
    """查本项目最新 Release 与 APP_VERSION 比较，结果写 state.app_update 缓存。

    独立请求、不走 GithubClient：采集限流器在匿名模式下全量扫荡期间会排队数分钟，
    手动「立即检查」不能被它卡住。token 仍取 effective_token——仓库私有期间本人
    token 能查到 Release，转公开后无 token 匿名即可，代码零改动。任何失败归入
    "unknown"，不影响服务运行。
    """
    if state.app_update_checking:
        return
    state.app_update_checking = True
    try:
        import requests

        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "StarPick-App"}
        if state.cfg is not None and state.cfg.has_token:
            headers["Authorization"] = f"Bearer {state.cfg.effective_token}"
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                headers=headers, timeout=8,
            )
            if resp.status_code == 404:
                # 私有仓库 + 无 token：查不到 Release，视为"暂无法检查"而非错误
                result = {"status": "unknown", "latest_tag": "",
                          "published_at": "", "checked_at": _now_iso()}
            else:
                resp.raise_for_status()
                data = resp.json()
                tag = (data.get("tag_name") or "").strip()
                latest = _parse_tag_version(tag)
                current = _parse_tag_version(APP_VERSION)
                if latest is None or current is None:
                    status = "unknown"
                else:
                    status = "newer" if latest > current else "up_to_date"
                result = {"status": status, "latest_tag": tag,
                          "published_at": data.get("published_at") or "",
                          "checked_at": _now_iso()}
        except (requests.RequestException, ValueError):
            result = {"status": "unknown", "latest_tag": "",
                      "published_at": "", "checked_at": _now_iso()}
        with state.app_update_lock:
            state.app_update = result
        if result["status"] == "newer":
            log.info("发现新版本：%s（当前 %s）", result["latest_tag"], APP_VERSION)
        else:
            log.info("应用更新检查完成：%s", result["status"])
    finally:
        state.app_update_checking = False


def _app_update_job() -> None:
    """定时自动检查应用更新（每 6 小时，与采集任务共用单 worker 串行——纯一次 HTTP
    请求，不会拖延采集）。"""
    _check_app_update()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _app_update_state_payload() -> dict[str, Any]:
    with state.app_update_lock:
        return dict(state.app_update)


def _collect_job(payload: RunRequest | None = None, source: str = "scheduled",
                 discovery_only: bool = False) -> None:
    if state.collector is None:
        return
    log.info("采集任务开始（来源: %s%s）", source, "，发现轮" if discovery_only else "")
    custom_category: dict | None = None
    if payload is not None and payload.custom_name and payload.custom_keywords:
        # 自定义分类：持久化到 custom_categories（首页分类自动出现），再并入本轮采集
        # 名称 strip 后为空则忽略（否则空白分类名会被写进库且无处清理）
        name = payload.custom_name.strip()
        kws = [k.strip() for k in payload.custom_keywords.split(",") if k.strip()]
        if name and kws:
            state.db.add_custom_category(name, kws)
            custom_category = {"name": name, "keywords": kws}
    if payload is None:
        summary = state.collector.collect_once(source=source,
                                               discovery_only=discovery_only)
    else:
        summary = state.collector.collect_once(
            payload.min_stars, payload.max_inactive_days,
            payload.per_category_top, payload.categories, custom_category,
            source=source, discovery_only=discovery_only,
        )
    log.info("采集任务结束: %s", summary.get("status"))


def _run_sleep_watchdog(scheduler: BackgroundScheduler, stop_event: threading.Event,
                        clock=None,
                        interval_seconds: int = SLEEP_WATCHDOG_INTERVAL_SECONDS,
                        settle_seconds: int = WAKE_SETTLE_SECONDS) -> None:
    """系统休眠看门狗（daemon 线程）：休眠唤醒后显式唤醒调度器，补跑错过的轮次。

    macOS 休眠时 time.monotonic() 冻结，APScheduler 的等待线程据此计时，所以睡眠期间
    连维护性 DarkWake 也不会检查任务，唤醒后还要再等"睡眠时长"的清醒时间才轮询——
    期间错过的轮次既不运行、next_run_time 也不推进（history.db 里必然看不到轮次）。
    采集/更新任务配了 misfire_grace_time=None + coalesce=True，唤醒后只会补跑一次。
    clock 可注入（返回 挂钟, 单调钟），供测试模拟休眠。
    """
    read_clock = clock or (lambda: (time.time(), time.monotonic()))
    last_wall, last_mono = read_clock()
    while not stop_event.wait(interval_seconds):
        wall_now, mono_now = read_clock()
        slept = (wall_now - last_wall) - (mono_now - last_mono)
        last_wall, last_mono = wall_now, mono_now
        if not _is_sleep_gap(slept, 0.0):
            continue
        log.info("检测到系统休眠 %.1f 小时，%d 秒后唤醒调度器补跑错过的轮次",
                 slept / 3600, settle_seconds)
        if stop_event.wait(settle_seconds):
            return
        try:
            scheduler.wakeup()
        except Exception as exc:  # 唤醒失败不影响服务本身，下一轮休眠还会再试
            log.error("休眠唤醒调度器失败: %s", exc)


def _mount_static() -> None:
    if state.cfg is None:
        return
    dist = state.cfg.static_dir
    if dist.exists() and (dist / "index.html").exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
        # SPA fallback：/project/1 等前端路由刷新时返回 index.html（API 路径除外）
        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            # dist 根目录的实体文件（favicon.svg 等）直接返回，避免被 fallback 吞成 index.html
            candidate = (dist / full_path).resolve()
            if (
                full_path
                and not full_path.endswith("/")
                and candidate.is_file()
                and candidate.is_relative_to(dist.resolve())
            ):
                return FileResponse(candidate)
            # index.html 每次重新请求（前端构建产物带 hash，改版后避免浏览器缓存旧 JS）
            return FileResponse(
                dist / "index.html",
                headers={"Cache-Control": "no-cache"},
            )

        log.info("前端静态托管（含 SPA fallback）：%s", dist)
    else:
        log.warning("未找到前端构建产物 %s，仅提供 API（请先执行前端 build）", dist)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    try:
        cfg = load_config()
    except ConfigError as exc:
        log.error("配置校验失败，拒绝启动：%s", exc)
        raise SystemExit(f"配置校验失败，拒绝启动：{exc}") from exc
    state.cfg = cfg
    state.db = Database(paths.default_db_path())
    # 启动自愈：进程重启意味着遗留 running 轮次必然已死，先解除防重入锁
    stale = state.db.clear_stale_running()
    if stale:
        log.warning("清理 %d 个遗留 running 轮次（进程重启/崩溃残留）", stale)
    stale_upd = state.db.clear_stale_update_running()
    if stale_upd:
        log.warning("清理 %d 个遗留 running 更新检查轮次（进程重启/崩溃残留）", stale_upd)
    state.collector = Collector(cfg, state.db)
    _mount_static()

    # 采集与项目更新检查只由用户手动触发（采集中心 / 更新历史弹窗），不再注册定时任务；
    # 调度器仅维护两个后台任务：应用自更新检查 + 数据库每日备份（均与项目采集无关）
    scheduler = BackgroundScheduler(
        timezone=cfg.timezone,
        executors={"default": {"type": "threadpool", "max_workers": 1}},
    )
    # 应用自更新检查：每 6 小时一次（发现新版仅提示，不自动下载安装）
    scheduler.add_job(
        _app_update_job,
        IntervalTrigger(hours=6),
        id="app-update-check",
        misfire_grace_time=None,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    state.scheduler = scheduler

    # 数据库自动备份：启动时备份一次 + 每天凌晨 3 点备份（保留最近 7 份）
    def _backup_db() -> None:
        if state.db is None:
            return
        try:
            if state.db.count_projects() == 0:
                log.info("数据库为空，跳过备份")
                return
            dest = state.db.backup(BACKUP_DIR)
            log.info("数据库已备份: %s", dest)
        except Exception as exc:  # 备份失败不影响服务运行
            log.error("数据库备份失败: %s", exc)
    try:
        scheduler.add_job(
            _backup_db,
            CronTrigger(hour=3, minute=5, timezone=cfg.timezone),
            id="db-backup",
            misfire_grace_time=cfg.misfire_grace_seconds,
            coalesce=True,
            max_instances=1,
        )
    except Exception as exc:
        log.error("注册定时备份任务失败: %s", exc)
    _backup_db()
    log.info("服务启动完成（采集与项目更新检查均为手动触发；数据库每日 3:05 自动备份、应用更新每 6h 自动检查）")

    # 休眠看门狗：系统休眠唤醒后主动唤醒调度器（备份/自更新任务错过的时间窗口恢复用）
    watchdog_stop = threading.Event()
    threading.Thread(target=_run_sleep_watchdog, args=(scheduler, watchdog_stop),
                     daemon=True, name="sleep-watchdog").start()

    # 存量项目全库回填（后台异步；README 快、release 受限流排队，均不阻塞启动与采集）
    if state.collector is not None:
        log.info("启动存量项目 README 与安装包全库回填（后台）")
        threading.Thread(
            target=state.collector.backfill_readme, daemon=True, name="backfill-readme"
        ).start()
        # 变体重探排在 README 回填之后（同锁串行）：只处理库内已有原文的项目
        threading.Thread(
            target=state.collector.backfill_variants, daemon=True, name="backfill-variants"
        ).start()
        threading.Thread(
            target=state.collector.backfill_all, daemon=True, name="backfill-all"
        ).start()
    yield
    watchdog_stop.set()
    scheduler.shutdown(wait=False)
    if state.collector is not None:
        # 优雅停机：最多等待 20s 让进行中的采集轮次收尾，再关闭数据库
        for _ in range(200):
            if not state.collector.is_running():
                break
            time.sleep(0.1)
    if state.db:
        state.db.close()


app = FastAPI(title="STARPICK", version=APP_VERSION, lifespan=lifespan)
# 详情接口可能下发几百 KB 的 README 与全部语言变体，gzip 后体积约降一个数量级
app.add_middleware(GZipMiddleware, minimum_size=1024)


# ---------------- 响应模型 ----------------
class ProjectOut(BaseModel):
    id: int
    full_name: str
    name: str
    owner: str
    description: str
    homepage: str
    stars: int
    language: str
    pushed_at: str
    html_url: str
    topics: list[str]
    platforms: list[str]
    system_requirements: list[str]
    download_url: str
    assets: list[dict[str, Any]]
    # release 是否已探测过：前端区分「没有安装包」与「还没检测到」（检测中态）
    assets_probed: bool = False
    zh_friendly: bool
    category: str
    lang: str
    first_seen_at: str
    last_recommended_at: str
    categories: list[str] = []
    mirror_prefixes: list[str] = []
    is_new: bool = False
    is_favorite: bool = False
    favorite_group: str = ""


class ProjectDetailOut(ProjectOut):
    readme: str
    readme_zh: str = ""
    readme_en: str = ""
    # 中/英之外的语言变体（语言键 → 正文），采集时按 README 声明全量入库
    readme_variants: dict[str, str] = {}
    release_notes: str = ""
    # mirror_prefixes 继承自 ProjectOut：前端渲染 README 图片与下载加速共用


class ReadmeVariantOut(BaseModel):
    """按需抓取的 README 语言变体（未预采集的语言在详情页站内查看）。"""
    path: str
    content: str
    truncated: bool = False


class RunRequest(BaseModel):
    """手动触发采集的过滤覆盖参数（可选；不传则用配置默认值，0=不限）。

    categories：只采集选中的分类子集（空/None=全部）；
    custom_name/custom_keywords：自定义分类采集（自动持久化到首页分类）。
    """

    min_stars: int | None = Field(default=None, ge=0, le=1_000_000)
    max_inactive_days: int | None = Field(default=None, ge=1, le=3650)
    # 每分类收录上限（None=用配置默认值；0=不限）
    per_category_top: int | None = Field(default=None, ge=0, le=1000)
    categories: list[str] | None = Field(default=None, max_length=50)
    custom_name: str | None = Field(default=None, max_length=30)
    custom_keywords: str | None = Field(default=None, max_length=1000)


class RunOut(BaseModel):
    status: str
    reason: str = ""


class CollectStatusOut(BaseModel):
    running: bool = False
    phase: str = ""
    message: str = ""
    current_category: str = ""
    categories_done: int = 0
    categories_total: int = 0
    enriched_done: int = 0
    enriched_total: int = 0
    backfill_done: int = 0
    backfill_total: int = 0
    started_at: str = ""
    updated_at: str = ""


class HealthOut(BaseModel):
    status: str
    version: str
    db_ok: bool
    last_round: dict[str, Any] | None
    has_token: bool = False   # 是否已配置 GitHub Token（决定采集速度与限流）


# ---------------- 路由 ----------------
@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    db_ok = False
    try:
        if state.db:
            state.db.count_projects()
            db_ok = True
    except Exception:
        db_ok = False
    last = state.db.last_round() if state.db else None
    # 时间戳统一为带时区 ISO（与其它端点一致，避免消费方把 UTC 墙钟误解为本地时间）
    if last:
        last = dict(last)
        last["started_at"] = _iso_utc(last.get("started_at") or "") or ""
        last["finished_at"] = _iso_utc(last.get("finished_at") or "") or ""
    return HealthOut(
        status="ok" if db_ok else "degraded",
        version=APP_VERSION,
        db_ok=db_ok,
        last_round=last,
        has_token=bool(state.cfg.has_token if state.cfg else False),
    )


NEW_WINDOW_HOURS = 72  # 「新增」筛选窗口：最近 N 小时内首次入库的项目


@app.get("/api/projects", response_model=dict[str, Any])
def list_projects(
    category: str | None = Query(default=None, max_length=50),
    keyword: str = Query(default="", max_length=50),
    lang: str = Query(default="", pattern="^(|zh|en)$"),
    page: int = Query(default=1, ge=1, le=1000),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="stars", pattern="^(stars|recent|new)$"),
    new: str = Query(default="", pattern="^(|new)$"),
    zh: str = Query(default="", pattern="^(|1)$"),
    platform: str = Query(default="", max_length=20),
) -> dict[str, Any]:
    # 新增角标/筛选共用口径：最近 NEW_WINDOW_HOURS 小时内首次入库的项目（不随空轮重置）。
    # 角标必须在普通列表也生效（new_since 非空即标记 is_new），否则 NEW 只在筛选后才可见；
    # new=new 时同一窗口再作为 SQL 过滤条件。
    badge_since = (
        datetime.now(timezone.utc) - timedelta(hours=NEW_WINDOW_HOURS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    filter_since = badge_since if new == "new" else ""
    rows, total = state.db.search_projects(
        category, keyword.strip(), lang, page, page_size, sort, filter_since,
        zh_friendly_only=(zh == "1"), platform=platform,
    )
    items = [_project_out(r, new_since=badge_since) for r in rows]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "new_since": filter_since,
        "items": items,
    }


@app.get("/api/collect/status", response_model=CollectStatusOut)
def collect_status() -> CollectStatusOut:
    """采集进度快照（前端轮询展示：阶段 / 分类 / 详情抓取 / 安装包回填）。"""
    snap = state.collector.progress.snapshot() if state.collector else {}
    return CollectStatusOut(**snap)


@app.get("/api/projects/{project_id}", response_model=ProjectDetailOut)
def project_detail(project_id: int) -> ProjectDetailOut:
    row = state.db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return _project_detail_out(row)


@app.get("/api/projects/{project_id}/similar", response_model=dict[str, Any])
def similar_projects(project_id: int, limit: int = Query(default=5, ge=1, le=10)) -> dict[str, Any]:
    """相似项目推荐：同分类 + star 数量级相近（供详情页继续探索）。"""
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    row = state.db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    items = state.db.similar_projects(project_id, row["category"], row["stars"], limit)
    # 与列表同一出口构造：JSON 列解析、is_favorite/mirror_prefixes 等字段齐全
    return {"items": [_project_out(r).model_dump() for r in items]}


@app.get("/api/projects/{project_id}/readme-variant", response_model=ReadmeVariantOut)
def project_readme_variant(
    project_id: int,
    path: str = Query(..., min_length=1, max_length=400,
                      description="仓库内相对路径（README 内语言切换链接还原后的路径）"),
) -> ReadmeVariantOut:
    """按需抓取项目自带 README 的语言变体（详情页点击未预采集的语言切换链接时）。

    仅接受本仓库内的 README 变体路径（相对路径或 GitHub blob 绝对链接均可还原），
    走镜像 failover、不占 GitHub API 配额；内容只回给前端即时渲染，不落库。
    """
    if state.db is None or state.collector is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    row = state.db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    rel = resolve_repo_path(path, row["full_name"])
    if rel is None or variant_lang(rel.rsplit("/", 1)[-1]) is None:
        raise HTTPException(status_code=400, detail="只支持本仓库的 README 语言变体路径")
    text = state.collector.client.probe_readme_variant(row["full_name"], rel)
    if not text:
        raise HTTPException(
            status_code=404, detail="该语言版本暂时无法通过镜像获取（可能已改名或删除）"
        )
    # 超大 README 截断，避免响应体过大；前端只读渲染，截断处标注
    truncated = len(text) > _VARIANT_MAX_CHARS
    return ReadmeVariantOut(path=rel, content=text[:_VARIANT_MAX_CHARS], truncated=truncated)


def _iso_utc(s: str) -> str | None:
    """把库内 UTC 墙钟 "YYYY-MM-DD HH:MM:SS" 转成带时区 ISO，供前端正确转本地时间。"""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).isoformat(timespec="seconds")
    except ValueError:
        return s


@app.get("/api/rounds", response_model=dict[str, Any])
def list_rounds(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
    rows = state.db.list_rounds(limit)
    items = []
    for r in rows:
        item = dict(r)
        item["started_at"] = _iso_utc(item.get("started_at") or "")
        item["finished_at"] = _iso_utc(item.get("finished_at") or "")
        raw = item.get("categories_summary") or "{}"
        try:
            item["categories_summary"] = json.loads(raw)
        except json.JSONDecodeError:
            item["categories_summary"] = {}
        raw_stats = item.get("stats") or "{}"
        try:
            item["stats"] = json.loads(raw_stats)
        except json.JSONDecodeError:
            item["stats"] = {}
        items.append(item)
    return {"items": items}


@app.post("/api/run", response_model=RunOut)
def trigger_run(payload: RunRequest = RunRequest()) -> RunOut:
    if state.collector is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪")
    threading.Thread(target=_collect_job, args=(payload,), kwargs={"source": "manual"}, daemon=True).start()
    return RunOut(status="accepted", reason="采集已触发，防重入锁会自动拦截并发轮次")




def _mask_token(token: str) -> str:
    """打码展示：ghp_***abcd（仅暴露前缀与末 4 位）。"""
    if not token:
        return ""
    if len(token) <= 8:
        return token[:3] + "***"
    return f"{token[:4]}…{token[-4:]}"


@app.get("/api/settings", response_model=dict[str, Any])
def get_settings() -> dict[str, Any]:
    """读取当前运行配置（Token 仅返回打码状态，不回传原文）。"""
    cfg = state.cfg
    return {
        "has_token": cfg.has_token,
        "masked_token": _mask_token(cfg.effective_token) if cfg.has_token else "",
        "search_rate_per_minute": cfg.search_rate,
        "core_rate_per_hour": cfg.core_rate,
        # 「关于」分组：应用版本 / 项目地址 / 自更新检查状态
        "version": APP_VERSION,
        "repo_url": f"https://github.com/{GITHUB_REPO}",
        "app_update": _app_update_state_payload(),
        # 部署形态：desktop=桌面安装包（更新引导下载新安装包）/ web=本地网页服务（拉代码重启）
        "deployment": "desktop" if paths.is_frozen() else "web",
    }


class TokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=255)


@app.post("/api/settings/token", response_model=dict[str, Any])
def save_token(body: TokenIn) -> dict[str, Any]:
    """保存并热生效 GitHub Token（无需重启，立即提升限流）。"""
    cfg = state.cfg
    token = body.token.strip()
    if not is_valid_token(token):
        raise HTTPException(status_code=422, detail="Token 格式非法：应以 ghp_ / github_pat_ / gho_ / ghu_ 开头")
    try:
        set_runtime_token(cfg, token)
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # 热生效：更新采集客户端的 Authorization 与限流速率
    if state.collector is not None:
        state.collector.apply_token(token)
    return {
        "ok": True,
        "has_token": cfg.has_token,
        "masked_token": _mask_token(cfg.effective_token),
        "search_rate_per_minute": cfg.search_rate,
        "core_rate_per_hour": cfg.core_rate,
    }


@app.post("/api/settings/token/clear", response_model=dict[str, Any])
def clear_token() -> dict[str, Any]:
    """清除运行时 Token，回退匿名模式（环境变量配置仍保留）。"""
    cfg = state.cfg
    clear_runtime_token(cfg)
    if state.collector is not None:
        state.collector.apply_token("")
    return {
        "ok": True,
        "has_token": cfg.has_token,
        "masked_token": _mask_token(cfg.effective_token) if cfg.has_token else "",
        "search_rate_per_minute": cfg.search_rate,
        "core_rate_per_hour": cfg.core_rate,
    }


@app.post("/api/app/update-check", response_model=dict[str, Any])
def trigger_app_update_check() -> dict[str, Any]:
    """手动触发应用更新检查（设置弹窗「关于」的「立即检查」按钮）。

    后台线程执行（端点立即返回，不阻塞——网络超时最长 8s，前端自行轮询
    /api/settings 拿结果）；同一时刻只允许一个检查在跑（幂等，重复点无害）。
    """
    threading.Thread(target=_check_app_update, daemon=True,
                     name="app-update-check").start()
    return {"ok": True, "app_update": _app_update_state_payload()}


@app.post("/api/app/update-download", response_model=dict[str, Any])
def app_update_download() -> dict[str, Any]:
    """返回最新 Windows 安装包直链（桌面壳「一键更新」用）。

    仅桌面壳（frozen）暴露；本地网页服务返回明确错误（网页部署用 git pull）。
    只查询资产直链，不下载不执行——下载与静默安装由 desktop.py 完成。
    """
    if not paths.is_frozen():
        raise HTTPException(status_code=400, detail="网页部署请用 git pull 更新，无安装包")
    import sys
    if sys.platform != "win32":
        raise HTTPException(status_code=501, detail="仅 Windows 桌面版支持一键自动更新")
    # 复用 _check_app_update 的独立请求路径查最新 Release（不走采集限流器）
    try:
        import requests
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "StarPick-App"}
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers=headers, timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        tag = (data.get("tag_name") or "").strip()
        latest = _parse_tag_version(tag)
        current = _parse_tag_version(APP_VERSION)
        if latest is None or current is None or latest <= current:
            raise HTTPException(status_code=409, detail="当前已是最新版本")
        asset = next(
            (a for a in data.get("assets", []) if a["name"].endswith("-setup.exe")),
            None,
        )
        if not asset:
            raise HTTPException(status_code=404, detail="最新版本未找到 Windows 安装包资产")
        return {
            "ok": True,
            "latest_tag": tag,
            "download_url": asset["browser_download_url"],
            "file_name": asset["name"],
        }
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"检查最新版本失败: {exc}") from exc


@app.get("/api/collect/params", response_model=dict[str, Any])
def collect_params() -> dict[str, Any]:
    """当前采集参数（供前端下拉框展示默认值）。"""
    return {
        "min_stars": state.cfg.min_stars,
        "max_inactive_days": state.cfg.max_inactive_days,
        "per_category_max": state.cfg.per_category_top,
        "categories": _all_categories(),
    }


@app.get("/api/categories", response_model=dict[str, Any])
def categories() -> dict[str, Any]:
    """全部分类（内置 + 自定义）与各分类项目总数，供首页筛选与采集中心分类选择。"""
    names = _all_categories()
    counts = state.db.category_counts(names) if state.db is not None else {}
    return {"categories": names, "counts": counts}


def _all_categories() -> list[str]:
    """内置分类（配置） + 自定义分类（DB），保持顺序去重。"""
    names = list(state.cfg.categories.keys()) if state.cfg else []
    if state.db is not None:
        for row in state.db.list_custom_categories():
            if row["name"] not in names:
                names.append(row["name"])
    return names


class CategoryCleanupCounts(BaseModel):
    """删除前预览：各分类可删项目数（收藏除外）。"""
    counts: dict[str, int] = Field(default_factory=dict)
    running: bool = False


class CategoryCleanupRequest(BaseModel):
    names: list[str] = Field(min_length=1, max_length=50)


class CategoryCleanupOut(BaseModel):
    deleted: int
    kept_favorites: int


@app.post("/api/categories/preview-cleanup", response_model=CategoryCleanupCounts)
def preview_category_cleanup(payload: CategoryCleanupRequest) -> CategoryCleanupCounts:
    """统计选中分类的可删项目数（收藏除外），打开确认弹窗时调用。"""
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪")
    names = [n.strip() for n in payload.names if n.strip()]
    counts = state.db.category_project_counts(names)
    return {"counts": counts, "running": state.db.has_running_round()}


@app.post("/api/categories/cleanup", response_model=CategoryCleanupOut)
def cleanup_categories(payload: CategoryCleanupRequest) -> CategoryCleanupOut:
    """删除选中分类的全部存量项目（收藏保留）。

    采集运行中拒绝执行：删除与采集同库写入，SQLite WAL 虽可并行读写，
    但用户已明确要求两者互斥；提示稍后重试。
    """
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪")
    names = [n.strip() for n in payload.names if n.strip()]
    if not names:
        raise HTTPException(status_code=400, detail="未选择要清除的分类")
    if state.db.has_running_round():
        raise HTTPException(status_code=409, detail="采集任务运行中，请等本轮结束后再清除")
    # 不做删除前备份：目前没有恢复入口，备份无法回滚这次操作；日常备份仍由启动/每日定时任务维护
    result = state.db.delete_projects_by_categories(names)
    log.info("分类清除：%s → 删除 %d 个、保留收藏 %d 个", names, result["deleted"], result["kept_favorites"])
    return CategoryCleanupOut(deleted=result["deleted"], kept_favorites=result["kept_favorites"])


@app.get("/api/stats", response_model=dict[str, Any])
def stats() -> dict[str, Any]:
    total = state.db.count_projects()
    rounds = state.db.list_rounds(30)
    by_category: dict[str, int] = {}
    for r in rounds:
        try:
            cs = json.loads(r["categories_summary"] or "{}")
        except json.JSONDecodeError:
            continue
        for cat, n in cs.items():
            by_category[cat] = by_category.get(cat, 0) + int(n or 0)
    return {
        "total_projects": total,
        # assets 探测回填只更新已有行、不改总数：前端自动刷新需要第二个变更信号
        "assets_ready": state.db.count_projects_with_assets(),
        # 尚未探测 release 的项目数：顶栏「安装包检测 N/M」进度（全部探测完归零隐藏）
        "assets_pending": state.db.count_projects_pending_probe(),
        "recent_rounds": len(rounds),
        "rounds_by_status": {
            status: sum(1 for r in rounds if r["status"] == status)
            for status in ("success", "failed", "running", "skipped")
        },
        "categories_summary": by_category,
    }


# ---------------- 辅助 ----------------
def _json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _project_out(row, new_since: str = "") -> ProjectOut:
    """列表/详情共用的 ProjectOut 构造；new_since 非空时按 first_seen_at 标记新增。

    列表页卡片收藏星标依赖 is_favorite：此处统一带收藏状态，
    不再只在详情接口填充（否则卡片刷新后总是显示未收藏）。
    """
    cats = _json_list(row["categories"]) if "categories" in row.keys() else []
    if not cats:
        cats = [row["category"]]
    fav = state.db.is_favorite(row["id"]) if state.db else None
    return ProjectOut(
        id=row["id"], full_name=row["full_name"], name=row["name"], owner=row["owner"],
        description=row["description"] or "",
        homepage=row["homepage"] or "", stars=row["stars"], language=row["language"] or "",
        pushed_at=row["pushed_at"] or "", html_url=row["html_url"],
        topics=_json_list(row["topics"]), platforms=_json_list(row["platforms"]),
        system_requirements=_json_list(row["system_requirements"]),
        download_url=row["download_url"] or "", assets=_json_list(row["assets"]),
        assets_probed=bool(row["assets_probed"]) if "assets_probed" in row.keys() else bool(row["assets"]),
        zh_friendly=bool(row["zh_friendly"]),
        category=row["category"], lang=row["lang"] or "en",
        first_seen_at=row["first_seen_at"],
        last_recommended_at=row["last_recommended_at"],
        categories=cats,
        mirror_prefixes=list(state.cfg.mirror_prefixes)
        if state.cfg and state.cfg.mirror_enabled else [],
        is_new=bool(new_since and (row["first_seen_at"] or "") >= new_since),
        is_favorite=fav is not None,
        favorite_group=fav["group_name"] if fav is not None else "",
    )


def _project_detail_out(row) -> ProjectDetailOut:
    base = _project_out(row)
    return ProjectDetailOut(
        **base.model_dump(),
        readme=row["readme"] or "",
        readme_zh=row["readme_zh"] or "",
        readme_en=row["readme_en"] or "",
        readme_variants=_variants_of(row),
        release_notes=row["release_notes"] or "",
    )


def _variants_of(row) -> dict[str, str]:
    """readme_variants 列（JSON）→ 语言键 → 正文；异常/空值返回空字典。"""
    raw = row["readme_variants"] if "readme_variants" in row.keys() else ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}


# ---------------- 更新检查（手动全库 / 定时增量；历史与采集轮次分开保存） ----------------
class UpdateStatusOut(BaseModel):
    running: bool
    phase: str = ""
    message: str = ""
    done: int = 0
    total: int = 0
    updated: int = 0
    nochange: int = 0
    failed: int = 0
    started_at: str = ""
    updated_at: str = ""


@app.post("/api/updates/run", response_model=dict[str, Any])
def run_updates(category: list[str] | None = Query(default=None, description="只检查这些分类（可多选）")) -> dict[str, Any]:
    """手动触发更新检查（后台异步；防重入：已有运行中则跳过）。

    category 为空 → 全库；指定一个或多个分类 → 只检查这些分类下的项目。
    """
    if state.collector is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    if state.collector.is_update_running():
        return {"status": "skipped", "reason": "已有更新检查运行中"}
    cats = [c for c in (category or []) if c]
    threading.Thread(
        target=lambda: state.collector.check_updates(limit=None, categories=cats or None), daemon=True,
        name="updates-full",
    ).start()
    return {"status": "started", "mode": "category" if cats else "full"}


@app.get("/api/updates/status", response_model=UpdateStatusOut)
def updates_status() -> UpdateStatusOut:
    if state.collector is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    return UpdateStatusOut(**state.collector.update_progress.snapshot())


@app.get("/api/updates/summary", response_model=dict[str, Any])
def updates_summary() -> dict[str, Any]:
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    rows = state.db.list_update_rounds(1)
    last = rows[0] if rows else None
    if last is not None:
        last["started_at"] = _iso_utc(last.get("started_at") or "") or ""
        last["finished_at"] = _iso_utc(last.get("finished_at") or "") or ""
    return {
        "total_projects": state.db.count_projects(),
        "projects_with_update": state.db.count_has_update(),
        "last_check": last,
    }


@app.get("/api/update-rounds", response_model=dict[str, Any])
def update_rounds(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """更新检查历史（与采集轮次历史分开保存）。"""
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    items = state.db.list_update_rounds(limit)
    for item in items:
        item["started_at"] = _iso_utc(item.get("started_at") or "") or ""
        item["finished_at"] = _iso_utc(item.get("finished_at") or "") or ""
    return {"items": items}


# ---------------- 收藏 + 分组 ----------------
class FavoriteIn(BaseModel):
    group_name: str = Field(default="默认分组", max_length=30)


class FavoriteOut(BaseModel):
    project_id: int
    group_name: str
    favorited_at: str
    full_name: str
    name: str
    owner: str
    description: str = ""
    stars: int = 0
    language: str = ""
    category: str = ""
    has_update: bool = False
    latest_tag: str = ""
    last_check_at: str = ""


@app.post("/api/favorites/{project_id}", response_model=dict[str, Any])
def add_favorite(project_id: int, payload: FavoriteIn | None = None) -> dict[str, Any]:
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    if state.db.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    # 分组自动按项目主分类归类（前端零输入）；payload.group_name 保留兼容但忽略
    state.db.add_favorite(project_id)
    row = state.db.get_project(project_id)
    group = (row["category"] if row and row["category"] else "") or "未分组"
    return {"status": "ok", "project_id": project_id, "group_name": group}


@app.delete("/api/favorites/{project_id}", response_model=dict[str, Any])
def remove_favorite(project_id: int) -> dict[str, Any]:
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    removed = state.db.remove_favorite(project_id)
    return {"status": "ok" if removed else "not_found", "project_id": project_id}


@app.patch("/api/favorites/{project_id}", response_model=dict[str, Any])
def move_favorite(project_id: int, payload: FavoriteIn) -> dict[str, Any]:
    """移动收藏到其他分组。"""
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    moved = state.db.move_favorite(project_id, payload.group_name)
    if not moved:
        raise HTTPException(status_code=404, detail="未收藏该项目")
    return {"status": "ok", "project_id": project_id, "group_name": payload.group_name}


@app.get("/api/favorites", response_model=dict[str, Any])
def list_favorites(group: str = "") -> dict[str, Any]:
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    rows = state.db.list_favorites(group)
    items = [
        FavoriteOut(
            project_id=r["project_id"], group_name=r["group_name"],
            favorited_at=_iso_utc(r["created_at"] or "") or "",
            full_name=r["full_name"], name=r["name"],
            owner=r["owner"], description=r["description"] or "", stars=r["stars"],
            language=r["language"] or "", category=r["category"],
            has_update=bool(r["has_update"]), latest_tag=r["latest_tag"] or "",
            last_check_at=_iso_utc(r["last_check_at"] or "") or "",
        ).model_dump()
        for r in rows
    ]
    return {"items": items, "groups": state.db.favorite_groups()}


# ---------------- 健康度曲线（更新检查快照） ----------------
@app.get("/api/projects/{project_id}/health", response_model=dict[str, Any])
def project_health(project_id: int, limit: int = Query(60, ge=2, le=365)) -> dict[str, Any]:
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    if state.db.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    items = state.db.list_health_snapshots(project_id, limit)
    for item in items:
        item["checked_at"] = _iso_utc(item.get("checked_at") or "") or ""
    return {"items": items}


# ---------------- 通知中心 ----------------
class MarkReadIn(BaseModel):
    """已读标记请求；ids 为空列表 = 全部标记已读。"""
    ids: list[int] = Field(default_factory=list, max_length=200)


@app.get("/api/notifications", response_model=dict[str, Any])
def list_notifications(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """通知列表 + 未读数。limit 钳制 1-200。"""
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    items = state.db.list_notifications(limit)
    # 时间戳统一带 UTC 偏移下发（前端 new Date() 才能正确转本地时区）
    for item in items:
        item["created_at"] = _iso_utc(item.get("created_at") or "") or ""
        item["read_at"] = _iso_utc(item.get("read_at") or "") or ""
    return {
        "items": items,
        "unread_count": state.db.unread_notification_count(),
    }


@app.post("/api/notifications/mark-read", response_model=dict[str, Any])
def mark_read(payload: MarkReadIn | None = None) -> dict[str, Any]:
    """标记已读；ids 为空 = 全部已读。"""
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    ids = payload.ids if payload else []
    updated = state.db.mark_notifications_read(ids)
    return {"status": "ok", "updated": updated}


# ---------------- 每日精选 ----------------
@app.get("/api/picks/latest", response_model=dict[str, Any])
def latest_daily_pick() -> dict[str, Any]:
    """最新一日精选榜单（联查项目信息）。"""
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    data = state.db.get_latest_daily_pick()
    if data is None:
        return {"pick_date": "", "items": []}
    return data


@app.get("/api/picks/shuffle", response_model=dict[str, Any])
def shuffle_pick() -> dict[str, Any]:
    """「换一批」：从候选池随机抽取 5 个（仅本次展示，不落库）。

    pick_date 沿用最新一日精选的日期（换一批只替换展示项目，日期不变）；
    尚无精选数据时为空字符串。
    """
    if state.db is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    latest = state.db.get_latest_daily_pick()
    pick_date = latest["pick_date"] if latest else ""
    cands = state.db.pick_shuffle(count=15)
    seen_cat: dict[str, int] = {}
    picked: list[dict] = []
    for c in cands:
        cat = c.get("category") or "其他"
        if seen_cat.get(cat, 0) >= 2:
            continue
        seen_cat[cat] = seen_cat.get(cat, 0) + 1
        c["rank"] = len(picked) + 1
        c["score"] = 0
        picked.append(c)
        if len(picked) >= 5:
            break
    return {"pick_date": pick_date, "items": picked}
