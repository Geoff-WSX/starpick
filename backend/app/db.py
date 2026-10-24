"""SQLite 数据层：WAL 模式、schema 顺序迁移、projects/rounds 表。

生产级约束：
- WAL + busy_timeout：采集线程写、API 线程读并发安全
- schema_version 迁移：结构演进受控，迁移失败拒绝启动
- projects.full_name 唯一索引：防重复入库
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMAS: list[str] = [
    # v1：初始结构
    """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        owner TEXT NOT NULL,
        description TEXT DEFAULT '',
        homepage TEXT DEFAULT '',
        stars INTEGER NOT NULL DEFAULT 0,
        language TEXT DEFAULT '',
        pushed_at TEXT DEFAULT '',
        html_url TEXT NOT NULL,
        topics TEXT DEFAULT '[]',
        platforms TEXT DEFAULT '[]',
        system_requirements TEXT DEFAULT '[]',
        download_url TEXT DEFAULT '',
        zh_friendly INTEGER NOT NULL DEFAULT 0,
        category TEXT NOT NULL,
        major_version TEXT DEFAULT '',
        first_seen_at TEXT NOT NULL,
        last_recommended_at TEXT NOT NULL,
        last_round_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_projects_category_stars ON projects(category, stars DESC);
    CREATE INDEX IF NOT EXISTS idx_projects_pushed_at ON projects(pushed_at DESC);
    """,
    # v2：rounds 轮次表（含防重入运行状态）
    """
    CREATE TABLE IF NOT EXISTS rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running',
        categories_summary TEXT DEFAULT '{}',
        stats TEXT DEFAULT '{}',
        error TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_rounds_status ON rounds(status);
    CREATE INDEX IF NOT EXISTS idx_rounds_started_at ON rounds(started_at DESC);
    """,
    # v3：README 原文（详情页展示）
    """
    ALTER TABLE projects ADD COLUMN readme TEXT DEFAULT '';
    ALTER TABLE projects ADD COLUMN description_zh TEXT DEFAULT '';
    ALTER TABLE projects ADD COLUMN readme_zh TEXT DEFAULT '';
    ALTER TABLE projects ADD COLUMN readme_zh_at TEXT DEFAULT '';
    """,
    # v4：release assets 完整列表（多平台版本选择下载）
    """
    ALTER TABLE projects ADD COLUMN assets TEXT DEFAULT '[]';
    """,
    # v5：简介语言标记（zh=简介含中文 / en=英文及其他），供前端中英文筛选
    """
    ALTER TABLE projects ADD COLUMN lang TEXT DEFAULT 'en';
    """,
    # v6：项目自带多语言 README（zh/en 变体）+ 多语言探测标记（详情页语言切换）
    """
    ALTER TABLE projects ADD COLUMN readme_en TEXT DEFAULT '';
    ALTER TABLE projects ADD COLUMN ml_probed INTEGER DEFAULT 0;
    """,
    # v7：清理翻译功能遗留的死列（description_zh / readme_zh_at，均已闲置）
    """
    ALTER TABLE projects DROP COLUMN description_zh;
    ALTER TABLE projects DROP COLUMN readme_zh_at;
    """,
    # v8：多分类支持（跨界项目可标注多个分类；categories 为 JSON 数组，
    #     主分类保持 category 列兼容，附加分类追加进数组）
    """
    ALTER TABLE projects ADD COLUMN categories TEXT DEFAULT '[]';
    """,
    # v9：更新检查（latest_tag/has_update/last_check_at）+ 更新检查轮次表 update_rounds
    #     （与采集轮次 rounds 分开保存；scope=full 手动全库 / incremental 定时增量）
    """
    ALTER TABLE projects ADD COLUMN latest_tag TEXT DEFAULT '';
    ALTER TABLE projects ADD COLUMN has_update INTEGER DEFAULT 0;
    ALTER TABLE projects ADD COLUMN last_check_at TEXT DEFAULT '';
    CREATE TABLE update_rounds (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      started_at TEXT NOT NULL,
      finished_at TEXT DEFAULT '',
      status TEXT DEFAULT 'running',
      scope TEXT DEFAULT 'incremental',
      stats TEXT DEFAULT '{}',
      error TEXT DEFAULT ''
    );
    CREATE INDEX idx_update_rounds_started ON update_rounds(started_at DESC);
    """,
    # v10：收藏体系（favorites 收藏+分组；health_snapshots 健康度曲线快照；
    #      projects.release_notes 最新 release 说明，详情页展示）
    """
    ALTER TABLE projects ADD COLUMN release_notes TEXT DEFAULT '';
    CREATE TABLE favorites (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id INTEGER NOT NULL UNIQUE,
      group_name TEXT DEFAULT '默认分组',
      created_at TEXT NOT NULL,
      updated_at TEXT DEFAULT ''
    );
    CREATE TABLE health_snapshots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id INTEGER NOT NULL,
      stars INTEGER DEFAULT 0,
      has_update INTEGER DEFAULT 0,
      latest_tag TEXT DEFAULT '',
      checked_at TEXT NOT NULL
    );
    CREATE INDEX idx_health_snapshots_project ON health_snapshots(project_id, checked_at);
    """,
    # v11：通知中心 + 每日精选（notifications 站内通知；daily_picks 每日精选榜单）
    """
    CREATE TABLE notifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      type TEXT DEFAULT 'update',
      project_id INTEGER DEFAULT 0,
      title TEXT NOT NULL,
      message TEXT DEFAULT '',
      created_at TEXT NOT NULL,
      read_at TEXT DEFAULT ''
    );
    CREATE INDEX idx_notifications_created ON notifications(created_at DESC);
    CREATE TABLE daily_picks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id INTEGER NOT NULL,
      pick_date TEXT NOT NULL,
      rank INTEGER DEFAULT 0,
      score REAL DEFAULT 0,
      created_at TEXT NOT NULL,
      UNIQUE(pick_date, project_id)
    );
    """,
    # v12：自定义分类（用户自定义分类采集时持久化，供首页分类展示与后续按分类采集/更新）
    """
    CREATE TABLE IF NOT EXISTS custom_categories (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      keywords TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    );
    """,
    # v15：采集轮次来源标记（manual 手动 / scheduled 定时 / startup 启动补跑），
    # 便于区分"重启自动补跑"与用户手动触发的轮次
    """
    ALTER TABLE rounds ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
    """,
    # v16：安装包探测标记（assets_probed）：探测过的项目不再每次启动重复回填
    # （无 release 的仓库 assets 恒为空，旧查询每次启动都会重探，匿名限流下白耗配额）
    """
    ALTER TABLE projects ADD COLUMN assets_probed INTEGER DEFAULT 0;
    UPDATE projects SET assets_probed = 1
      WHERE assets IS NOT NULL AND assets != '' AND assets != '[]';
    """,
    # v17：轮次来源重标记——历史上所有定时/补跑轮次都是全量扫荡，
    # 轻/重轮拆分后 scheduled/startup 语义变为「发现轮」，旧数据改标为 full*
    """
    UPDATE rounds SET source='full' WHERE source='scheduled';
    UPDATE rounds SET source='full-startup' WHERE source='startup';
    """,
    # v18：多语言变体重探时间（README 自报链接式变体探测上线后，链接失效或镜像
    #      瞬时失败的项目可按窗口重探；空串=还没重探过，排序上小于任何时间戳）
    """
    ALTER TABLE projects ADD COLUMN ml_variants_at TEXT DEFAULT '';
    """,
    # v19：README 全部语言变体（JSON：语言键 → 正文，不含 default/zh/en 三种，
    #      它们仍在各自列里）；空串=还没做过「全语言采集」，'{}'=采过但确实只有中/英
    """
    ALTER TABLE projects ADD COLUMN readme_variants TEXT DEFAULT '';
    """,
    # 索引补齐：默认按星排序、新增筛选、更新检查池、未读通知数
    # （原有索引只覆盖"分类+星排序"，其余排序/筛选都退化为全表扫描 + 临时 B 树）
    """
    CREATE INDEX IF NOT EXISTS idx_projects_stars ON projects(stars DESC);
    CREATE INDEX IF NOT EXISTS idx_projects_first_seen ON projects(first_seen_at);
    CREATE INDEX IF NOT EXISTS idx_projects_last_check ON projects(last_check_at, id);
    CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read_at);
    """,
]

# 列表查询只取展示所需列，避免拖读 readme/readme_zh/readme_en 等大字段
# （assets 为版本选择器必需，列表页保留；详情接口才取全列）
_LIST_COLS = (
    "id, full_name, name, owner, description, homepage, stars, language, pushed_at, "
    "html_url, topics, platforms, system_requirements, download_url, zh_friendly, "
    "category, major_version, lang, assets, assets_probed, "
    "first_seen_at, last_recommended_at, categories"
)


def _merge_variant_jsons(old: str | None, new: str | None) -> str:
    """其它语言变体 JSON 新旧值取并集（同语言以新内容为准，旧值独有的语言保留）。

    采集时某个语言的镜像探测偶发失败只会少几个键，取并集可避免已采到的语言被抹掉；
    空串表示本轮没做全语言采集（保留旧值），'{}' 表示跑过但只有中/英（旧值优先）。
    """
    if not new:
        return old or ""
    if not old or old == "{}":
        return new
    try:
        old_data, new_data = json.loads(old), json.loads(new)
    except (TypeError, ValueError):
        return new
    if not isinstance(old_data, dict) or not isinstance(new_data, dict):
        return new
    merged = {k: v for k, v in old_data.items() if v}
    merged.update({k: v for k, v in new_data.items() if v})
    return json.dumps(merged, ensure_ascii=False)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---------------- 迁移 ----------------
    def _migrate(self) -> None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if cur.fetchone() is None:
                self._conn.execute(
                    "CREATE TABLE schema_version (version INTEGER NOT NULL)"
                )
                self._conn.execute("INSERT INTO schema_version (version) VALUES (0)")
                self._conn.commit()
            current = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            for version, ddl in enumerate(_SCHEMAS, start=1):
                if version > current:
                    try:
                        # 逐条执行（不用 executescript）：executescript 会隐式先 COMMIT，
                        # 中途失败无法真正回滚，且重跑会因列已存在而启动失败
                        for stmt in ddl.strip().split(";"):
                            stmt = stmt.strip()
                            if stmt:
                                self._conn.execute(stmt)
                        self._conn.execute(
                            "INSERT INTO schema_version (version) VALUES (?)", (version,)
                        )
                        self._conn.commit()
                    except sqlite3.Error as exc:
                        self._conn.rollback()
                        raise RuntimeError(
                            f"数据库迁移到 v{version} 失败，已回滚：{exc}"
                        ) from exc
            self._backfill_lang()
            self._backfill_categories()
            self._backfill_favorite_groups()

    # ---------------- favorites 数据对齐 ----------------
    def _backfill_favorite_groups(self) -> None:
        """收藏分组改为按项目主分类自动归类：把旧「默认分组」行对齐到项目主分类（幂等）。

        调用方 _migrate 已持有 self._lock，本方法不得再次加锁（Lock 不可重入）。
        """
        try:
            self._conn.execute(
                "UPDATE favorites SET group_name = COALESCE("
                "(SELECT category FROM projects WHERE projects.id = favorites.project_id), '未分组') "
                "WHERE group_name = '默认分组'"
            )
            self._conn.commit()
        except sqlite3.Error:
            # 早期库可能无 favorites 表：静默跳过，不阻塞启动
            pass

    # ---------------- rounds ----------------
    def create_round(self, started_at: str, source: str = "manual") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO rounds (started_at, status, source) VALUES (?, 'running', ?)",
                (started_at, source),
            )
            self._conn.commit()
            return cur.lastrowid

    def finish_round(self, round_id: int, status: str, categories_summary: dict,
                     stats: dict, error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE rounds SET finished_at=?, status=?, categories_summary=?, stats=?, error=? WHERE id=?",
                (
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    status,
                    json.dumps(categories_summary, ensure_ascii=False),
                    json.dumps(stats, ensure_ascii=False),
                    error,
                    round_id,
                ),
            )
            self._conn.commit()

    def has_running_round(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM rounds WHERE status='running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row is not None

    def clear_stale_running(self, note: str = "") -> int:
        """把遗留的 running 轮次标记为 failed（进程重启/崩溃后自愈）。

        采集轮次是进程内的：进程重启意味着所有 running 轮次必然已死，
        在启动时调用即可解除防重入锁的永久拦截。返回清理条数。
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE rounds SET finished_at=?, status='failed', error=? "
                "WHERE status='running'",
                (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), note or "进程重启，旧运行轮次作废"),
            )
            self._conn.commit()
            return cur.rowcount

    def last_round(self) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM rounds ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row

    def last_round_by_sources(self, sources: tuple[str, ...]) -> sqlite3.Row | None:
        """按来源集合查最近一轮（启动补跑判定用：区分发现轮与全量轮）。"""
        if not sources:
            return None
        marks = ",".join("?" * len(sources))
        with self._lock:
            return self._conn.execute(
                f"SELECT * FROM rounds WHERE source IN ({marks}) ORDER BY id DESC LIMIT 1",
                list(sources),
            ).fetchone()

    def list_rounds(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT id, started_at, finished_at, status, categories_summary, stats, error, source "
                "FROM rounds ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def prune_rounds(self, keep: int) -> int:
        """保留最近 N 轮，清理更早的 rounds。返回删除条数。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM rounds ORDER BY id DESC LIMIT -1 OFFSET ?", (keep,)
            ).fetchall()
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            cur = self._conn.execute(f"DELETE FROM rounds WHERE id IN ({placeholders})", ids)
            self._conn.commit()
            return cur.rowcount

    # ---------------- 更新检查（update_rounds 与采集轮次分开保存） ----------------
    def list_projects_for_update(self, limit: int | None = None,
                                 categories: list[str] | None = None) -> list[dict]:
        """项目列表（更新检查用）：按 last_check_at 升序（最久未检查优先）。

        limit=None 全库（手动）；limit=N 增量检查前 N 条（定时滚动）。
        categories 非空时只取这些分类（命中主分类 category 或附加分类 categories 数组任一）。
        """
        # readme：资产名未命中平台规则时兜底推断平台标签（缺它会让该兜底恒为空）
        sql = (
            "SELECT id, full_name, stars, latest_tag, has_update, last_check_at, readme "
            "FROM projects"
        )
        where = ""
        params: list = []
        if categories:
            conds: list[str] = []
            for cat in categories:
                if not cat:
                    continue
                conds.append("category=?")
                params.append(cat)
                # 附加分类 categories 存 JSON 数组字符串，命中任一元素即算该分类
                conds.append("categories LIKE ? ESCAPE '\\'")
                params.append(f'%"{self._escape_like(cat)}"%')
            if conds:
                where = " WHERE (" + " OR ".join(conds) + ")"
        sql += where
        sql += " ORDER BY last_check_at ASC, id ASC"
        if limit is not None and int(limit) > 0:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params)]

    def mark_checked(self, project_id: int, latest_tag: str,
                     has_update: int, checked_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET latest_tag=?, has_update=?, last_check_at=? WHERE id=?",
                (latest_tag, has_update, checked_at, project_id),
            )
            self._conn.commit()

    def update_project_assets(self, project_id: int, assets: list,
                              platforms: list[str]) -> None:
        """检查更新/回填时刷新 release assets（含 size）与平台推断结果。

        platforms 为空（资产名未命中平台规则）时保留旧值，避免更新检查
        把项目已有的平台标签清空。
        """
        with self._lock:
            payload = json.dumps(assets, ensure_ascii=False)
            plat = json.dumps(platforms, ensure_ascii=False)
            self._conn.execute(
                "UPDATE projects SET assets=?, "
                "platforms=CASE WHEN ?='[]' THEN platforms ELSE ? END WHERE id=?",
                (payload, plat, plat, project_id),
            )
            self._conn.commit()

    def create_update_round(self, started_at: str, scope: str = "incremental") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO update_rounds (started_at, status, scope) VALUES (?, 'running', ?)",
                (started_at, scope),
            )
            self._conn.commit()
            return cur.lastrowid

    def finish_update_round(self, round_id: int, status: str,
                            stats: dict, error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE update_rounds SET finished_at=?, status=?, stats=?, error=? WHERE id=?",
                (
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    status,
                    json.dumps(stats, ensure_ascii=False),
                    error,
                    round_id,
                ),
            )
            self._conn.commit()

    def has_running_update_round(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM update_rounds WHERE status='running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row is not None

    def clear_stale_update_running(self, note: str = "") -> int:
        """进程重启自愈：遗留 running 更新检查轮次标记 failed。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE update_rounds SET finished_at=?, status='failed', error=? "
                "WHERE status='running'",
                (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                 note or "进程重启，旧运行轮次作废"),
            )
            self._conn.commit()
            return cur.rowcount

    def list_update_rounds(self, limit: int = 30) -> list[dict]:
        if limit < 1:
            limit = 30
        if limit > 200:
            limit = 200
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, finished_at, status, scope, stats, error "
                "FROM update_rounds ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            out: list[dict] = []
            for r in rows:
                d = dict(r)
                try:
                    d["stats"] = json.loads(d.get("stats") or "{}")
                except (ValueError, TypeError):
                    d["stats"] = {}
                out.append(d)
            return out

    def prune_update_rounds(self, keep: int) -> int:
        if keep < 1:
            keep = 1
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM update_rounds ORDER BY id DESC LIMIT -1 OFFSET ?", (keep,)
            ).fetchall()
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            cur = self._conn.execute(
                f"DELETE FROM update_rounds WHERE id IN ({placeholders})", ids
            )
            self._conn.commit()
            return cur.rowcount

    def prune_health_snapshots(self, keep_days: int = 365) -> int:
        """清理超过保留期的健康度快照（随采集轮次定期清理，防表无限增长）。"""
        if keep_days < 1:
            keep_days = 365
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM health_snapshots WHERE checked_at < ?", (cutoff,)
            )
            self._conn.commit()
            return cur.rowcount

    def prune_notifications(self, keep: int = 500) -> int:
        """仅保留最近 N 条通知（防表无限增长）。"""
        if keep < 1:
            keep = 500
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM notifications WHERE id NOT IN "
                "(SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()
            return cur.rowcount

    # ---------------- projects ----------------
    @staticmethod
    def _detect_lang(description: str) -> str:
        """简介含 CJK 中文 → zh，否则 → en。"""
        return "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in description) else "en"

    def _backfill_lang(self) -> None:
        """幂等回填 lang：按当前 description 重算（存量迁移后立即生效）。

        仅在 _migrate 的持锁上下文中调用（threading.Lock 不可重入，勿自行加锁）。
        """
        rows = self._conn.execute(
            "SELECT id, description FROM projects WHERE 1=1"
        ).fetchall()
        for row in rows:
            lang = self._detect_lang(row["description"] or "")
            self._conn.execute(
                "UPDATE projects SET lang=? WHERE id=?", (lang, row["id"])
            )
        self._conn.commit()

    @staticmethod
    def _json_list(raw) -> list[str]:
        """JSON 列安全解析；已解析的 list 原样返回；非法/非数组兜底为空列表。"""
        if isinstance(raw, list):
            return raw
        try:
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _escape_like(s: str) -> str:
        """转义 LIKE 模式中的通配符（配合 ESCAPE '\\' 使用）。

        分类名/关键词是用户输入，含 % _ \\ 时不转义会被当作通配符，造成错配/漏配。
        """
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # ---------------- custom_categories（自定义分类） ----------------
    def list_custom_categories(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, keywords, created_at FROM custom_categories ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def add_custom_category(self, name: str, keywords: list[str]) -> dict:
        """新增自定义分类；同名已存在时更新关键词，返回最终记录。"""
        with self._lock:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            self._conn.execute(
                "INSERT INTO custom_categories (name, keywords, created_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET keywords=excluded.keywords",
                (name, ",".join(keywords), now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT name, keywords, created_at FROM custom_categories WHERE name=?",
                (name,),
            ).fetchone()
            return dict(row)

    def _backfill_categories(self) -> None:
        """幂等回填 categories：存量项目按当前主分类初始化（仅处理空数组）。"""
        rows = self._conn.execute(
            "SELECT id, category FROM projects WHERE categories IS NULL OR categories='[]'"
        ).fetchall()
        for row in rows:
            cats = [row["category"]]
            self._conn.execute(
                "UPDATE projects SET categories=? WHERE id=?",
                (json.dumps(cats, ensure_ascii=False), row["id"]),
            )
        self._conn.commit()

    # ---------------- 分类清除（用户主动清理误采/不再需要的分类存量） ----------------
    @staticmethod
    def _category_tag_blob(categories: str | None) -> str:
        """附加分类 JSON 数组 → `,A,B,` 标签串（分隔符统一为单逗号）。

        json.dumps 默认分隔符带空格（`"开发", "影视"`）， separators 剥离时
        空格残留会让 `%,影视,%` LIKE 匹配落空，所以除引号括号外还要剥空格。
        """
        return (
            "," + (categories or "").replace(" ", "").replace('"', "").replace("[", "").replace("]", "") + ","
        )

    def _category_hit_rows(self, names: list[str]) -> list[sqlite3.Row]:
        """按分类名找出命中项目（主分类或附加分类数组任一命中）。

        附加分类是 JSON 字符串（如 ["AI工具","开发"]），LIKE %名% 只做粗筛，
        精确命中在 Python 侧用标签串边界比对（`,名,`）——LIKE %名% 本身会
        误伤前缀同名分类（"音乐" vs "音乐与阅读"）。
        """
        hits: dict[int, sqlite3.Row] = {}
        for name in names:
            rows = self._conn.execute(
                "SELECT id, category, categories FROM projects WHERE category=?",
                (name,),
            ).fetchall()
            for r in rows:
                hits[r["id"]] = r
            extra = self._conn.execute(
                "SELECT id, category, categories FROM projects WHERE categories LIKE ?",
                (f"%{name}%",),
            ).fetchall()
            for r in extra:
                if f",{name.replace(' ', '')}," in self._category_tag_blob(r["categories"]):
                    hits[r["id"]] = r
        return list(hits.values())

    def category_project_counts(self, names: list[str]) -> dict[str, int]:
        """按分类名统计可删项目数（主分类或附加分类命中且未收藏）。"""
        if not names:
            return {}
        with self._lock:
            fav_ids = {r["project_id"] for r in self._conn.execute("SELECT project_id FROM favorites")}
            counts: dict[str, int] = {}
            for name in names:
                counts[name] = sum(
                    1
                    for r in self._conn.execute(
                        "SELECT id, category, categories FROM projects WHERE category=? OR categories LIKE ?",
                        (name, f"%{name}%"),
                    )
                    if r["category"] == name
                    or f",{name.replace(' ', '')}," in self._category_tag_blob(r["categories"])
                    if r["id"] not in fav_ids
                )
            return counts

    def delete_projects_by_categories(self, names: list[str]) -> dict:
        """删除命中分类的项目（主分类或附加分类任一命中），事务执行全删或全不删。

        - 收藏项目不连坐（只把命中分类从其附加字段中摘除）；
        - 被删项目在 health_snapshots / daily_picks / notifications 的行级联清理（无外键）；
        - 残留在其它项目附加字段里的命中分类引用一并摘除。

        不做删除前备份：目前没有恢复入口，备份无法回滚这次操作；
        日常备份仍由启动/每日 3:05 定时任务与 cleanup 端点之外的 backup() 维护。
        返回 {"deleted", "kept_favorites"}。
        """
        if not names:
            return {"deleted": 0, "kept_favorites": 0}
        with self._lock:
            fav_ids = {r["project_id"] for r in self._conn.execute("SELECT project_id FROM favorites")}
            hits = self._category_hit_rows(names)
            ids = [r["id"] for r in hits]
            delete_ids = [i for i in ids if i not in fav_ids]
            kept = len(ids) - len(delete_ids)
            if not delete_ids and kept == 0:
                return {"deleted": 0, "kept_favorites": 0}
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if delete_ids:
                    dp = ",".join("?" * len(delete_ids))
                    for table in ("health_snapshots", "daily_picks", "notifications"):
                        self._conn.execute(f"DELETE FROM {table} WHERE project_id IN ({dp})", delete_ids)
                    self._conn.execute(f"DELETE FROM projects WHERE id IN ({dp})", delete_ids)
                self._cleanup_category_refs(names)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            return {"deleted": len(delete_ids), "kept_favorites": kept}

    def _cleanup_category_refs(self, names: list[str]) -> None:
        """从所有项目附加分类数组中摘除命中分类（收藏保留项目的残引用也清）。

        主分类字段不动：它仍命中 names 里的分类（否则项目不会被 flag 出来），
        清掉筛选器里会出现"项目有分类但分类下没项目"的空档。
        """
        name_set = set(names)
        for name in names:
            rows = self._conn.execute(
                "SELECT id, categories FROM projects WHERE categories LIKE ?",
                (f"%{name}%",),
            ).fetchall()
            for row in rows:
                if f",{name.replace(' ', '')}," not in self._category_tag_blob(row["categories"]):
                    continue  # 粗筛命中（同名前缀），但不是完整分类名，跳过
                try:
                    cats = json.loads(row["categories"])
                except (TypeError, ValueError):
                    continue
                remaining = [c for c in cats if c not in name_set]
                if remaining != cats:
                    self._conn.execute(
                        "UPDATE projects SET categories=? WHERE id=?",
                        (json.dumps(remaining, ensure_ascii=False), row["id"]),
                    )

    def upsert_project(self, p: dict) -> bool:
        """按 full_name 去重更新；返回 True=新入库，False=更新已有。"""
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, readme, readme_variants FROM projects WHERE full_name=?",
                (p["full_name"],),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    """INSERT INTO projects
                       (full_name, name, owner, description, homepage, stars, language,
                        pushed_at, html_url, topics, platforms, system_requirements,
                        download_url, zh_friendly, category, major_version,
                        first_seen_at, last_recommended_at, last_round_id,
                        readme, assets, lang, readme_zh, readme_en, ml_probed, categories,
                        ml_variants_at, readme_variants)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p["full_name"], p["name"], p["owner"], p.get("description", ""),
                        p.get("homepage", ""), p["stars"], p.get("language", ""),
                        p.get("pushed_at", ""), p["html_url"],
                        json.dumps(p.get("topics", []), ensure_ascii=False),
                        json.dumps(p.get("platforms", []), ensure_ascii=False),
                        json.dumps(p.get("system_requirements", []), ensure_ascii=False),
                        p.get("download_url", ""), int(p.get("zh_friendly", 0)),
                        p["category"], p.get("major_version", ""),
                        p["first_seen_at"], p["last_recommended_at"], p.get("last_round_id"),
                        p.get("readme", ""),
                        json.dumps(
                            self._json_list(p.get("assets")) or [], ensure_ascii=False
                        ),
                        self._detect_lang(p.get("description", "")),
                        p.get("readme_zh", ""), p.get("readme_en", ""),
                        int(p.get("ml_probed", 0)),
                        json.dumps(
                            self._json_list(p.get("categories"))
                            or [p["category"]], ensure_ascii=False
                        ),
                        p.get("ml_variants_at", ""),
                        p.get("readme_variants", ""),
                    ),
                )
                self._conn.commit()
                return True
            # 其它语言变体取并集：镜像偶发失败/上游临时抖动不会抹掉已采到的语言
            variants = _merge_variant_jsons(
                existing["readme_variants"], p.get("readme_variants", "")
            )
            self._conn.execute(
                """UPDATE projects SET
                     description=?, homepage=?, stars=?, language=?, pushed_at=?,
                     topics=?,
                     platforms=COALESCE(NULLIF(?, '[]'), platforms),
                     system_requirements=COALESCE(NULLIF(?, '[]'), system_requirements),
                     download_url=?, zh_friendly=?, category=?, major_version=?,
                     assets=COALESCE(NULLIF(?, '[]'), assets), lang=?,
                     readme=COALESCE(NULLIF(?, ''), readme),
                     readme_zh=COALESCE(NULLIF(?, ''), readme_zh),
                     readme_en=COALESCE(NULLIF(?, ''), readme_en),
                     ml_probed=MAX(COALESCE(?, 0), COALESCE(ml_probed, 0)),
                     ml_variants_at=COALESCE(NULLIF(?, ''), ml_variants_at),
                     -- 已在上面与库内旧值取过并集（不丢已采集的语言）
                     readme_variants=?,
                     last_recommended_at=COALESCE(?, last_recommended_at),
                     last_round_id=COALESCE(?, last_round_id)
                   WHERE full_name=?""",
                (
                    p.get("description", ""), p.get("homepage", ""), p["stars"],
                    p.get("language", ""), p.get("pushed_at", ""),
                    json.dumps(p.get("topics", []), ensure_ascii=False),
                    json.dumps(p.get("platforms", []), ensure_ascii=False),
                    json.dumps(p.get("system_requirements", []), ensure_ascii=False),
                    p.get("download_url", ""), int(p.get("zh_friendly", 0)),
                    p["category"], p.get("major_version", ""),
                    json.dumps(
                        self._json_list(p.get("assets")) or [], ensure_ascii=False
                    ),
                    self._detect_lang(p.get("description", "")),
                    p.get("readme", ""),
                    p.get("readme_zh", ""), p.get("readme_en", ""),
                    int(p.get("ml_probed", 0)),
                    p.get("ml_variants_at", ""),
                    variants,
                    p.get("last_recommended_at"), p.get("last_round_id"),
                    p["full_name"],
                ),
            )
            self._conn.commit()
            return False

    def project_exists(self, full_name: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM projects WHERE full_name=?", (full_name,)
            ).fetchone()

    def touch_seen(self, full_name: str, stars: int, pushed_at: str,
                   last_recommended_at: str, round_id: int) -> None:
        """已入库项目本轮再次命中候选：轻量刷新热度字段与推荐时间（不碰 README 等大字段）。

        作用：a) 推进 last_recommended_at，保证下次 _skip_fresh 能跳过
        （否则项目满 skip_existing_days 后每轮都被重复抓详情）；
        b) 用本轮搜索结果同步 star/活跃时间（列表展示与健康快照的数据源）。
        """
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET stars=?, pushed_at=?, last_recommended_at=?, "
                "last_round_id=? WHERE full_name=?",
                (int(stars or 0), pushed_at or "", last_recommended_at,
                 round_id, full_name),
            )
            self._conn.commit()

    def get_existing_batch(self, full_names: list[str]) -> dict[str, sqlite3.Row]:
        """批量查已存在项目（采集跳过判定用）：full_name → 行（仅 readme 完整度与入库时间相关字段）。

        内部按 500 个分块，避免超出 SQLite 变量数上限。
        """
        out: dict[str, sqlite3.Row] = {}
        names = [n for n in full_names if n]
        if not names:
            return out
        for i in range(0, len(names), 500):
            chunk = names[i:i + 500]
            marks = ",".join("?" * len(chunk))
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT full_name, last_recommended_at, "
                    f"length(readme) AS readme_len FROM projects "
                    f"WHERE full_name IN ({marks})",
                    chunk,
                ).fetchall()
            for r in rows:
                out[r["full_name"]] = r
        return out

    def add_category(self, full_name: str, category: str) -> bool:
        """为已入库项目追加一个附加分类（跨界项目多分类标注）。返回是否新增。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT categories FROM projects WHERE full_name=?", (full_name,)
            ).fetchone()
            if row is None:
                return False
            cats = self._json_list(row["categories"])
            if not cats:
                cats = [category]
            elif category in cats:
                return False
            else:
                cats.append(category)
            self._conn.execute(
                "UPDATE projects SET categories=? WHERE full_name=?",
                (json.dumps(cats, ensure_ascii=False), full_name),
            )
            self._conn.commit()
            return True

    def count_projects(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

    def count_projects_with_assets(self) -> int:
        """已有安装包记录的项目数（assets 探测回填的变更信号，供前端自动刷新）。"""
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM projects "
                "WHERE assets IS NOT NULL AND assets != '' AND assets != '[]'"
            ).fetchone()[0]

    def count_projects_pending_probe(self) -> int:
        """尚未探测过 release 的项目数（顶栏「安装包检测 N/M」进度分母分子用）。"""
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM projects WHERE COALESCE(assets_probed, 0)=0"
            ).fetchone()[0]

    def search_projects(self, category: str | None = None, keyword: str = "",
                        lang: str = "", page: int = 1, page_size: int = 20,
                        sort: str = "stars", new_since: str = "") -> tuple[list[sqlite3.Row], int]:
        """按分类/语言/关键词查询，返回 (行列表, 总数)。keyword 对 name/description 做 LIKE 匹配。

        new_since 非空时仅返回该时间之后首次入库的项目（新增高亮筛选）。
        """
        clauses, params = [], []
        if category:
            # 主分类命中或附加分类命中（跨界项目可在任意标注分类下被筛出）
            clauses.append("(category=? OR categories LIKE ? ESCAPE '\\')")
            params.extend([category, f'%"{self._escape_like(category)}"%'])
        if lang in ("zh", "en"):
            clauses.append("lang=?")
            params.append(lang)
        if keyword:
            like = f"%{self._escape_like(keyword)}%"
            clauses.append("(name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            params.extend([like, like])
        if new_since:
            clauses.append("first_seen_at >= ?")
            params.append(new_since)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM projects {where}", params
            ).fetchone()[0]
            order = "stars DESC" if sort == "stars" else "pushed_at DESC"
            offset = (page - 1) * page_size
            rows = self._conn.execute(
                f"SELECT {_LIST_COLS} FROM projects {where} ORDER BY {order} LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
            return rows, total

    def get_project(self, project_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()

    def similar_projects(self, project_id: int, category: str, stars: int,
                         limit: int = 5) -> list[dict]:
        """相似项目推荐：同主分类 + star 数量级相近（0.2x~5x），按 star 排序。

        返回列表展示字段（同 _LIST_COLS）。"""
        with self._lock:
            lo = max(0, int(stars * 0.2))
            hi = int(stars * 5) + 1
            rows = self._conn.execute(
                f"SELECT {_LIST_COLS} FROM projects "
                "WHERE category=? AND stars BETWEEN ? AND ? AND id!=? "
                "ORDER BY stars DESC LIMIT ?",
                (category, lo, hi, project_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_projects_missing_assets(self) -> list[sqlite3.Row]:
        """返回「尚未探测过 release」的项目（供全库回填）。

        以 assets_probed 标记为准而非 assets 是否为空：无 release 的仓库
        探测结果就是空列表，若不落标记会每次启动被重复探测。
        """
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM projects "
                "WHERE (assets IS NULL OR assets='' OR assets='[]') "
                "AND COALESCE(assets_probed, 0)=0 ORDER BY id"
            ).fetchall()

    def mark_assets_probed(self, full_name: str) -> None:
        """标记项目已完成 release 探测（幂等；探测失败/限流时不要调用，留待重试）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET assets_probed=1 WHERE full_name=?", (full_name,)
            )
            self._conn.commit()

    def list_projects_missing_readme(self) -> list[sqlite3.Row]:
        """返回「尚未探测过多语言 README」的项目（供全库 README 回填）。

        以 ml_probed 为准：项目确无 README 内容时也会置 1（见 _backfill_readme_one），
        旧查询里的 readme='' 条件会让这类项目每次启动都被重复回填。
        """
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM projects "
                "WHERE ml_probed IS NULL OR ml_probed=0 ORDER BY id"
            ).fetchall()

    def list_projects_retry_variants(self, before_iso: str, limit: int = 3000) -> list[sqlite3.Row]:
        """返回「有 README 原文、且上次变体探测早于 before_iso」的项目。

        供变体重探：镜像瞬时失败的补救，以及上游 README 新加/改名的语言变体在
        窗口（_VARIANT_RETRY_DAYS）之后重新扫一遍。ml_variants_at 为空串（从未
        探测）时排序小于任何时间戳，天然命中；upsert 对变体取并集，重探不会丢
        已采到的语言。
        """
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM projects "
                "WHERE COALESCE(readme, '') != '' "
                "AND COALESCE(ml_variants_at, '') < ? "
                "ORDER BY id LIMIT ?",
                (before_iso, limit),
            ).fetchall()

    def list_projects_missing_all_variants(self, limit: int = 3000) -> list[sqlite3.Row]:
        """返回「有 README 原文、但还没做过全语言采集（readme_variants=''）」的项目。

        供启动时的全语言回填：v19 之前入库的项目只有中/英变体，需要补采其余语言。
        """
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM projects "
                "WHERE COALESCE(readme, '') != '' AND COALESCE(readme_variants, '') = '' "
                "ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()

    def count_has_update(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM projects WHERE has_update=1"
            ).fetchone()
            return int(row["c"] or 0)

    # ---------------- favorites（收藏 + 分组） ----------------
    def add_favorite(self, project_id: int, group_name: str = "") -> None:
        """收藏项目；分组默认按项目主分类自动归类（零输入），重复收藏时更新分组（幂等）。"""
        group_name = (group_name or "").strip()
        with self._lock:
            # 查询必须在锁内：连接被多线程共享，锁外 SELECT 可能与其他写事务交错
            if not group_name:
                row = self._conn.execute(
                    "SELECT category FROM projects WHERE id=?", (project_id,)
                ).fetchone()
                group_name = (row["category"] if row and row["category"] else "") or "未分组"
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            self._conn.execute(
                "INSERT INTO favorites (project_id, group_name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "group_name=excluded.group_name, updated_at=excluded.updated_at",
                (project_id, group_name, now, now),
            )
            self._conn.commit()

    def remove_favorite(self, project_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM favorites WHERE project_id=?", (project_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def is_favorite(self, project_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT project_id, group_name, created_at FROM favorites WHERE project_id=?",
                (project_id,),
            ).fetchone()

    def move_favorite(self, project_id: int, group_name: str) -> bool:
        group_name = (group_name or "").strip() or "默认分组"
        with self._lock:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            cur = self._conn.execute(
                "UPDATE favorites SET group_name=?, updated_at=? WHERE project_id=?",
                (group_name, now, project_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_favorites(self, group: str = "") -> list[sqlite3.Row]:
        """收藏列表（联查项目基础列）；group 为空 = 全部分组。"""
        sql = (
            "SELECT f.project_id, f.group_name, f.created_at, "
            "p.full_name, p.name, p.owner, p.description, p.stars, p.language, "
            "p.category, p.has_update, p.latest_tag, p.last_check_at "
            "FROM favorites f JOIN projects p ON p.id = f.project_id"
        )
        params: list = []
        if group:
            sql += " WHERE f.group_name=?"
            params.append(group)
        sql += " ORDER BY f.created_at DESC"
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def favorite_groups(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT group_name, COUNT(*) AS cnt FROM favorites "
                "GROUP BY group_name ORDER BY cnt DESC, group_name"
            ).fetchall()
            return [dict(r) for r in rows]

    def count_favorites(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM favorites").fetchone()
            return int(row["c"] or 0)

    # ---------------- health_snapshots（健康度曲线） ----------------
    def record_health_snapshot(self, project_id: int, stars: int, has_update: int,
                               latest_tag: str, checked_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO health_snapshots (project_id, stars, has_update, latest_tag, checked_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, int(stars or 0), 1 if has_update else 0,
                 latest_tag or "", checked_at),
            )
            self._conn.commit()

    def list_health_snapshots(self, project_id: int, limit: int = 60) -> list[dict]:
        if limit < 2:
            limit = 60
        if limit > 365:
            limit = 365
        with self._lock:
            rows = self._conn.execute(
                "SELECT stars, has_update, latest_tag, checked_at "
                "FROM health_snapshots WHERE project_id=? "
                "ORDER BY checked_at DESC LIMIT ?", (project_id, limit),
            ).fetchall()
            out = [dict(r) for r in rows]
            out.reverse()  # 升序（时间从早到晚），便于曲线绘制
            return out

    # ---------------- release 说明（最新 release 的正文） ----------------
    def save_release_notes(self, project_id: int, notes: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET release_notes=? WHERE id=?", (notes or "", project_id)
            )
            self._conn.commit()

    # ---------------- notifications（站内通知） ----------------
    def create_notification(self, ntype: str, project_id: int, title: str,
                            message: str = "", dedup_hours: int = 24) -> bool:
        """写入通知；返回 True=已写入，False=重复/幽灵（不写入）。
        - 幽灵防御：project_id>0 时项目必须真实存在，否则不写。
        - 去重：同 type+project_id+title 在 dedup_hours 内不重复写。
        """
        if project_id > 0:
            with self._lock:
                exists = self._conn.execute(
                    "SELECT id FROM projects WHERE id=?", (project_id,)
                ).fetchone()
            if exists is None:
                return False
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM notifications WHERE type=? AND project_id=? AND title=?",
                (ntype, project_id, title),
            ).fetchone()
            if cur is not None:
                try:
                    created = datetime.strptime(cur["id"] and self._conn.execute(
                        "SELECT created_at FROM notifications WHERE id=?", (cur["id"],)
                    ).fetchone()["created_at"], "%Y-%m-%d %H:%M:%S")
                    if (datetime.now(timezone.utc) - created).total_seconds() < dedup_hours * 3600:
                        return False
                except Exception:
                    return False
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            self._conn.execute(
                "INSERT INTO notifications (type, project_id, title, message, created_at) "
                "VALUES (?,?,?,?,?)",
                (ntype, project_id, title, message, now),
            )
            self._conn.commit()
            return True

    def list_notifications(self, limit: int = 50) -> list[dict]:
        if limit < 1:
            limit = 50
        if limit > 200:
            limit = 200
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, type, project_id, title, message, created_at, read_at "
                "FROM notifications ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def unread_notification_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM notifications WHERE read_at=''",
            ).fetchone()
            return int(row["c"] or 0)

    def mark_notifications_read(self, ids: list[int] | None = None) -> int:
        """标记已读；ids 为空列表/None = 全部已读。返回更新条数。"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            if not ids:
                cur = self._conn.execute(
                    "UPDATE notifications SET read_at=? WHERE read_at=''", (now,)
                )
            else:
                placeholders = ",".join("?" for _ in ids)
                cur = self._conn.execute(
                    f"UPDATE notifications SET read_at=? WHERE id IN ({placeholders}) AND read_at=''",
                    [now, *ids],
                )
            self._conn.commit()
            return cur.rowcount

    # ---------------- daily_picks（每日精选） ----------------
    def list_pickable(self, min_stars: int = 20) -> list[dict]:
        """每日精选候选池：id/category/stars/pushed_at/zh_friendly/has_update。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, category, stars, pushed_at, zh_friendly, has_update "
                "FROM projects WHERE stars >= ?", (min_stars,),
            ).fetchall()
            return [dict(r) for r in rows]

    def pick_shuffle(self, count: int = 15) -> list[dict]:
        """「换一批」候选：随机抽取（多取一些供分类去重），含列表展示字段。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, full_name, name, owner, description, category, stars, "
                "language, zh_friendly, has_update "
                "FROM projects WHERE stars >= 20 ORDER BY RANDOM() LIMIT ?",
                (count,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_daily_pick(self, pick_date: str,
                        picks: list[tuple[int, int, float]]) -> None:
        """覆盖写入某日精选榜单；picks=[(project_id, rank, score), ...]。"""
        with self._lock:
            self._conn.execute("DELETE FROM daily_picks WHERE pick_date=?", (pick_date,))
            for item in picks:
                # 兼容 dict（pick_daily 返回）与 tuple 两种形态
                if isinstance(item, dict):
                    project_id = item["project_id"]
                    rank = int(item.get("rank") or 0)
                    score = float(item.get("score") or 0.0)
                else:
                    project_id, rank, score = item
                if self._conn.execute(
                    "SELECT id FROM projects WHERE id=?", (project_id,)
                ).fetchone() is None:
                    continue  # 幽灵防御：只收真实存在的项目
                self._conn.execute(
                    "INSERT OR REPLACE INTO daily_picks "
                    "(project_id, pick_date, rank, score, created_at) VALUES (?,?,?,?,?)",
                    (project_id, pick_date, rank, score,
                     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
                )
            self._conn.commit()

    def get_latest_daily_pick(self) -> dict | None:
        """最新一日精选（联查项目信息）；无数据返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT pick_date FROM daily_picks ORDER BY pick_date DESC LIMIT 1",
            ).fetchone()
            if row is None:
                return None
            rows = self._conn.execute(
                "SELECT p.id, p.full_name, p.name, p.owner, p.description, p.stars, "
                "p.language, p.category, p.zh_friendly, d.rank, d.score, d.pick_date "
                "FROM daily_picks d JOIN projects p ON p.id = d.project_id "
                "WHERE d.pick_date=? ORDER BY d.rank ASC",
                (row["pick_date"],),
            ).fetchall()
            return {"pick_date": row["pick_date"], "items": [dict(r) for r in rows]}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- 备份 ----------------
    def backup(self, backup_dir: Path, keep: int = 7) -> Path:
        """用 sqlite backup API 备份到 backup_dir（WAL 下也保证一致性快照）。

        文件名 history-YYYYMMDD-HHMMSS.db；超过 keep 份时删除最旧。
        写入后校验快照行数与源库一致，不一致则删除并抛异常——空库/半库快照不得
        留在备份目录，否则轮转会把真实备份挤掉。
        返回备份文件路径；失败抛异常（由调用方记录日志，不影响服务）。
        """
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"history-{stamp}.db"
        # 目标连接使用常规模式（不继承源库 WAL），backup API 会做一致性快照
        target = sqlite3.connect(dest)
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()
        # 校验快照完整性：行数不一致 = 备的是空库/半库，删除并报错
        try:
            check = sqlite3.connect(f"file:{dest}?mode=ro&immutable=1", uri=True)
            try:
                copied = check.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            finally:
                check.close()
        except sqlite3.Error:
            copied = -1
        with self._lock:
            source = self._conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if copied != source:
            try:
                dest.unlink()
            except OSError:
                pass
            raise RuntimeError(
                f"备份校验失败（源库 {source} 行 / 快照 {copied} 行），已删除 {dest.name}"
            )
        # 保留最近 keep 份，删除更早的
        backups = sorted(backup_dir.glob("history-*.db"))
        for old in backups[:-keep] if len(backups) > keep else []:
            try:
                old.unlink()
            except OSError:
                pass
        return dest

