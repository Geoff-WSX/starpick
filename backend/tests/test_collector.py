"""采集流水线测试：mock GitHubClient 跑完整一轮（含防重入/去重/筛选/入库）。"""
import json
from datetime import datetime, timezone

from app.collector import Collector, _row_to_cand
from app.config import Config
from app.db import Database
from app.github_api import GitHubError

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class FakeClient:
    """模拟 GitHub 搜索与二次抓取。"""

    def __init__(self, items=None):
        self.items = items or []
        self.search_calls = 0
        self.last_query = ""
        self.readme_calls = 0  # README 抓取计数（跳过逻辑验证用）

    def search_repositories(self, query, per_page=20, max_items=200):
        self.search_calls += 1
        self.last_query = query
        return self.items

    def get_latest_release(self, full_name):
        return {
            "tag_name": "v2.3.8",
            "published_at": "2026-10-01T00:00:00Z",
            "assets": [
                {"name": "app_windows.zip", "url": f"https://dl/{full_name}/win", "size": 1024},
                {"name": "app_macos.dmg", "url": f"https://dl/{full_name}/mac", "size": 2048},
                {"name": "app_android.apk", "url": f"https://dl/{full_name}/android", "size": 512},
            ],
        }

    def get_readme_text(self, full_name):
        return ("# Demo\n"
                "A useful open source tool with rich features.\n"
                "System Requirements: 8GB RAM, 64-bit OS.\n"
                "中文说明：支持多语言。")

    def get_readme_bundle(self, full_name, default_readme=None):
        self.readme_calls += 1
        return {"default": self.get_readme_text(full_name), "zh": None, "en": None}


def _single_cfg() -> Config:
    cfg = Config()
    cfg.categories = {"影视": ["追剧"]}
    cfg.per_category_top = 5
    cfg.concurrency = 2
    cfg.skip_existing_days = 0   # 默认测试全量刷新；跳过逻辑由专门用例验证
    return cfg


def _item(full_name: str, stars: int, pushed: str = "2026-09-01T00:00:00Z") -> dict:
    return {
        "full_name": full_name,
        "name": full_name.split("/")[1],
        "description": f"desc of {full_name}",
        "homepage": "",
        "stargazers_count": stars,
        "language": "Python",
        "pushed_at": pushed,
        "html_url": f"https://github.com/{full_name}",
        "topics": ["tool"],
    }


def test_collect_once_inserts_projects(tmp_path):
    db = Database(tmp_path / "test.db")
    items = [
        _item("org/app-a", 5000),
        _item("org/app-b", 3000),
    ]
    collector = Collector(_single_cfg(), db, FakeClient(items))
    summary = collector.collect_once()
    collector.wait_backfill()  # release 补全为异步后台任务，测试需同步等待
    assert summary["status"] == "success"
    assert summary["stats"]["inserted"] == 2
    assert db.count_projects() == 2
    # 采集进度：完成后 running=False、阶段 finished、分类进度正确
    snap = collector.progress.snapshot()
    assert snap["running"] is False
    assert snap["phase"] == "finished"
    assert snap["categories_total"] == 1
    assert snap["categories_done"] == 1
    assert snap["enriched_total"] == 2
    assert snap["enriched_done"] == 2
    assert snap["backfill_total"] == 2
    assert snap["backfill_done"] == 2  # wait_backfill 后回填完成
    row = db.project_exists("org/app-a")
    assert row is not None
    assert "Windows" in _json(row["platforms"])
    assert "macOS" in _json(row["platforms"])
    assert row["zh_friendly"] == 1
    assert row["lang"] == "en"  # 英文简介 → 语言标记 en
    assert row["download_url"] == "https://dl/org/app-a/win"  # release 补全写入直链
    # 多平台 assets 完整入库（版本选择下载的数据基础）
    assets = _json(row["assets"])
    assert len(assets) == 3
    assert assets[0]["platform"] == "Windows"
    assert assets[1]["platform"] == "macOS"
    assert assets[2]["platform"] == "Android"
    assert assets[2]["url"].endswith("android")


def test_collect_once_dedup_second_round(tmp_path):
    db = Database(tmp_path / "test.db")
    items = [_item("org/app-a", 5000)]
    collector = Collector(_single_cfg(), db, FakeClient(items))
    collector.collect_once()
    # 第二轮相同项目：默认不重复入库
    summary = collector.collect_once()
    assert summary["stats"]["inserted"] == 0
    assert summary["stats"]["skipped"] == 1
    assert db.count_projects() == 1


def test_collect_once_skips_fresh_existing(tmp_path):
    """近期已采集 + README 完整 → 跳过详情抓取（只拉新项目），并计数 skipped_fresh。"""
    db = Database(tmp_path / "test.db")
    items = [_item("org/app-a", 5000), _item("org/app-b", 3000)]
    client = FakeClient(items)
    cfg = _single_cfg()
    cfg.skip_existing_days = 7
    collector = Collector(cfg, db, client)
    collector.collect_once()
    client.readme_calls = 0  # 重置计数
    summary = collector.collect_once()
    assert summary["status"] == "success"
    assert summary["stats"]["skipped_fresh"] == 2   # 两项目均在 7 天内入库
    assert client.readme_calls == 0                  # 未再抓 README
    assert db.count_projects() == 2                  # 数据未丢
    snap = collector.progress.snapshot()
    assert snap["enriched_total"] == 0               # 详情阶段跳过全部
    assert snap["running"] is False


def test_collect_once_refreshes_stale_existing(tmp_path):
    """已存在但入库时间超出阈值 → 重新抓取详情（防止数据永久冻结）。"""
    db = Database(tmp_path / "test.db")
    items = [_item("org/app-a", 5000)]
    client = FakeClient(items)
    cfg = _single_cfg()
    cfg.skip_existing_days = 7
    collector = Collector(cfg, db, client)
    collector.collect_once()
    # 把入库时间改成 30 天前（模拟长期未刷新）
    db._conn.execute(
        "UPDATE projects SET last_recommended_at = "
        "datetime('now', '-30 days') WHERE full_name='org/app-a'"
    )
    db._conn.commit()
    client.readme_calls = 0
    summary = collector.collect_once()
    assert summary["stats"]["skipped_fresh"] == 0    # 过期项目重新抓取
    assert client.readme_calls == 1


def test_collect_once_skips_below_star_threshold(tmp_path):
    db = Database(tmp_path / "test.db")
    items = [_item("org/lowstar", 100)]
    collector = Collector(_single_cfg(), db, FakeClient(items))
    summary = collector.collect_once()
    assert summary["stats"]["inserted"] == 0


def test_collect_once_stars_double_recommends(tmp_path):
    cfg = _single_cfg()
    cfg.re_recommend_on_major_update = True
    db = Database(tmp_path / "test.db")
    first = FakeClient([_item("org/app-a", 5000)])
    Collector(cfg, db, first).collect_once()

    # 第二轮 stars 翻倍 → 允许再次推荐（更新而非跳过）
    second = FakeClient([_item("org/app-a", 20000)])
    collector = Collector(cfg, db, second)
    summary = collector.collect_once()
    collector.wait_backfill()
    assert summary["stats"]["updated"] == 1
    assert db.count_projects() == 1  # 仍是 1 条（更新而非新增）


def test_collect_once_antireentry_skips(tmp_path):
    """防重入：DB 中存在 running 轮次时，collect_once 直接跳过。"""
    db = Database(tmp_path / "test.db")
    db.create_round("2026-10-08 00:00:00")  # 模拟已有 running 轮
    collector = Collector(_single_cfg(), db, FakeClient([_item("org/app-a", 5000)]))
    summary = collector.collect_once()
    assert summary["status"] == "skipped"
    assert db.count_projects() == 0


def test_acquire_run_is_atomic_single_round(tmp_path):
    """防重入原子化：_acquire_run 在锁内完成"检查+创建"，返回轮次 id；未结束时重复调用返回 None。"""
    db = Database(tmp_path / "test.db")
    collector = Collector(_single_cfg(), db, FakeClient([]))
    rid = collector._acquire_run()
    assert rid is not None
    assert db.has_running_round()
    # 轮次未 finish，再次获取应被拒绝（检查与创建之间无竞态窗口）
    assert collector._acquire_run() is None
    assert db.list_rounds(10)[0]["id"] == rid


def test_clear_stale_running_unblocks_collection(tmp_path):
    """僵尸轮次自愈：进程重启后遗留 running 被清理，采集恢复执行。"""
    db = Database(tmp_path / "test.db")
    db.create_round("2026-10-09 06:12:14")  # 模拟僵尸 running（进程已死）
    assert db.has_running_round()

    cleared = db.clear_stale_running()
    assert cleared == 1
    assert not db.has_running_round()
    last = db.last_round()
    assert last["status"] == "failed"  # 僵尸轮次被标记 failed（可审计）

    # 清理后采集恢复
    collector = Collector(_single_cfg(), db, FakeClient([_item("org/app-a", 5000)]))
    summary = collector.collect_once()
    assert summary["status"] == "success"
    assert db.count_projects() == 1


def test_meta_assets_filtered_from_picker(tmp_path):
    """资产过滤：release 里的校验/元数据文件不进入版本选择器，只保留安装包。"""
    db = Database(tmp_path / "test.db")

    class MetaClient(FakeClient):
        def get_latest_release(self, full_name):
            return {
                "tag_name": "v1.2.0",
                "published_at": "2026-10-01T00:00:00Z",
                "assets": [
                    {"name": "app_windows.zip", "url": "https://dl/win", "size": 100},
                    {"name": "app_macos.dmg", "url": "https://dl/mac", "size": 200},
                    {"name": "app-1.2.0.sha256", "url": "https://dl/sha", "size": 1},
                    {"name": "latest.yml", "url": "https://dl/yml", "size": 2},
                    {"name": "SHA256SUMS.txt", "url": "https://dl/sums", "size": 3},
                    {"name": "release-notes.txt", "url": "https://dl/notes", "size": 4},
                    {"name": "app-1.2.0.blockmap", "url": "https://dl/blockmap", "size": 5},
                    {"name": "SHA256SUMS", "url": "https://dl/sums2", "size": 6},
                    {"name": "checksums", "url": "https://dl/checksums", "size": 7},
                ],
            }

    collector = Collector(_single_cfg(), db, MetaClient([_item("org/app-a", 5000)]))
    collector.collect_once()
    collector.wait_backfill()
    assets = _json(db.project_exists("org/app-a")["assets"])
    names = [a["name"] for a in assets]
    assert "app_windows.zip" in names
    assert "app_macos.dmg" in names
    assert "app-1.2.0.sha256" not in names   # 校验和文件被过滤
    assert "latest.yml" not in names         # 元数据文件被过滤
    assert "SHA256SUMS.txt" not in names
    assert "release-notes.txt" not in names  # 说明文件被过滤
    assert "app-1.2.0.blockmap" not in names  # 增量更新元数据被过滤
    assert "SHA256SUMS" not in names          # 无扩展名校验文件被过滤
    assert "checksums" not in names
    assert len(assets) == 2


def test_collect_once_search_failure_marks_failed(tmp_path):
    class FailingClient(FakeClient):
        def search_repositories(self, query, per_page=20, max_items=200):
            raise GitHubError("模拟限流", kind="rate_limit", retryable=True)

    db = Database(tmp_path / "test.db")
    collector = Collector(_single_cfg(), db, FailingClient())
    summary = collector.collect_once()
    assert summary["status"] == "failed"
    last = db.last_round()
    assert last["status"] == "failed"
    assert last["error"]


def test_backfill_all_fills_missing_assets(tmp_path):
    """全库回填：assets 为空的存量项目被补全，已有 assets 的不重复探测。"""
    db = Database(tmp_path / "test.db")
    # 先跑一轮入库两个项目（FakeClient 的 release 会补全 assets → 不为空）
    collector = Collector(_single_cfg(), db, FakeClient([_item("org/app-a", 5000)]))
    collector.collect_once()
    collector.wait_backfill()
    assert len(_json(db.project_exists("org/app-a")["assets"])) == 3

    # 手工把 app-a 的 assets 清空，模拟"有项目但无安装包记录"的存量场景
    # （新语义以 assets_probed 探测标记为准，需一并重置；真实存量由 v16 迁移处理）
    with db._lock:
        db._conn.execute(
            "UPDATE projects SET assets='[]', assets_probed=0 WHERE full_name='org/app-a'"
        )
        db._conn.commit()
    missing = db.list_projects_missing_assets()
    assert [r["full_name"] for r in missing] == ["org/app-a"]

    collector.backfill_all()  # 同步执行（内部 pool.shutdown(wait=True)）
    row = db.project_exists("org/app-a")
    assets = _json(row["assets"])
    assert len(assets) == 3
    assert assets[0]["platform"] == "Windows"
    assert row["download_url"] == "https://dl/org/app-a/win"
    # 回填后不再出现在待回填清单
    assert db.list_projects_missing_assets() == []


def test_backfill_all_skips_when_nothing_missing(tmp_path):
    """全库回填：无待回填项目时直接返回，不触发任何 release 请求。"""
    db = Database(tmp_path / "test.db")
    client = FakeClient([_item("org/app-a", 5000)])
    collector = Collector(_single_cfg(), db, client)
    collector.collect_once()
    collector.wait_backfill()
    assert db.list_projects_missing_assets() == []
    collector.backfill_all()  # 应静默返回
    assert db.count_projects() == 1


def test_backfill_readme_fills_missing(tmp_path):
    """README 全库回填：readme 为空的存量项目被补抓原文。"""
    db = Database(tmp_path / "test.db")

    class ReadmeFailClient(FakeClient):
        """模拟早期 README 抓取失败（raw 不可达）入库的项目。"""

        def get_readme_text(self, full_name):
            raise GitHubError("连接失败", kind="server", retryable=True)

    collector = Collector(_single_cfg(), db, ReadmeFailClient([_item("org/app-a", 5000)]))
    collector.collect_once()
    collector.wait_backfill()
    assert db.project_exists("org/app-a")["readme"] == ""  # README 为空

    # 换回正常客户端跑 README 回填（模拟镜像通道恢复）
    collector2 = Collector(_single_cfg(), db, FakeClient([]))
    collector2.backfill_readme()
    row = db.project_exists("org/app-a")
    assert "中文说明" in row["readme"]
    assert row["zh_friendly"] == 1  # README 含中文 → 中文友好标记
    assert db.list_projects_missing_readme() == []


def test_backfill_readme_skips_when_all_filled(tmp_path):
    """README 回填：无空值项目时静默返回。"""
    db = Database(tmp_path / "test.db")
    collector = Collector(_single_cfg(), db, FakeClient([_item("org/app-a", 5000)]))
    collector.collect_once()
    collector.wait_backfill()
    assert db.list_projects_missing_readme() == []
    collector.backfill_readme()  # 应静默返回
    assert db.count_projects() == 1


def test_multilang_readme_zh_stored_and_friendly(tmp_path):
    """多语言 README：项目自带 README.zh-CN 时，中文版入库且项目标记中文友好。"""
    db = Database(tmp_path / "test.db")

    class BilingualClient(FakeClient):
        def get_readme_bundle(self, full_name, default_readme=None):
            return {
                "default": "# App\nAn English readme without Chinese.",
                "zh": "# 应用\n这是一个中文说明文档。",
                "en": None,
            }

    collector = Collector(_single_cfg(), db, BilingualClient([_item("org/app-a", 5000)]))
    collector.collect_once()
    collector.wait_backfill()
    row = db.project_exists("org/app-a")
    assert "English readme" in row["readme"]
    assert "中文说明文档" in row["readme_zh"]
    assert row["zh_friendly"] == 1  # 自带中文 README → 中文友好
    assert row["ml_probed"] == 1
    # 详情页 API 应返回两个版本
    from app.main import _project_detail_out
    out = _project_detail_out(row)
    assert out.readme_zh.startswith("# 应用")


class VariantClient(FakeClient):
    """模拟镜像变体通道：按 README 自报链接返回中文变体。"""

    def __init__(self, items=None, zh="# 应用\n这是一个中文说明文档。"):
        super().__init__(items)
        self.zh = zh
        self.bundle_calls = []  # (full_name, default_readme) 记录重探调用

    def get_readme_bundle(self, full_name, default_readme=None):
        self.bundle_calls.append((full_name, default_readme))
        return {"default": default_readme, "zh": self.zh, "en": None}


def _legacy_project(full_name="legacy/app", readme="# App\nAn English readme.", **extra):
    """构造一条「原文在库、中英变体为空」的存量项目（等价 v18 之前入库的行）。"""
    cand = {
        "full_name": full_name, "name": full_name.split("/")[1], "owner": "legacy",
        "description": "legacy project", "homepage": "", "stars": 100,
        "language": "Python", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": f"https://github.com/{full_name}", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": "影视", "major_version": "",
        "first_seen_at": _NOW, "last_recommended_at": _NOW, "readme": readme,
    }
    cand.update(extra)
    return cand


def test_list_projects_retry_variants_window(tmp_path):
    """重探窗口：命中「有原文 + 从未探测或超出窗口」的项目，不碰窗口内的与无原文的。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/never"))
    db.upsert_project(_legacy_project("legacy/stale", ml_variants_at="2026-08-01 00:00:00"))
    db.upsert_project(_legacy_project("legacy/fresh", ml_variants_at="2026-10-15 00:00:00"))
    db.upsert_project(_legacy_project("legacy/has-zh", readme_zh="# 中文说明"))
    db.upsert_project(_legacy_project("legacy/no-readme", readme=""))

    rows = db.list_projects_retry_variants("2026-10-01 00:00:00")
    assert [r["full_name"] for r in rows] == [
        "legacy/never", "legacy/stale", "legacy/has-zh",
    ]


def test_backfill_variants_one_uses_stored_readme_and_stamps(tmp_path):
    """单项目重探：用库内原文解析链接（不重抓原文），采到变体并盖重探时间戳。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app"))
    client = VariantClient([])
    collector = Collector(_single_cfg(), db, client)

    row = db.project_exists("legacy/app")
    assert collector._backfill_variants_one(_row_to_cand(row)) is True

    row = db.project_exists("legacy/app")
    assert "中文说明文档" in row["readme_zh"]
    assert row["ml_variants_at"]  # 已重探
    assert row["zh_friendly"] == 1  # 补到中文变体 → 中文友好
    # 传给客户端的是库内原文（用于解析语言切换链接），没有二次抓原文
    assert client.bundle_calls == [("legacy/app", "# App\nAn English readme.")]


def test_backfill_variants_repairs_legacy_rows(tmp_path):
    """启动重探：存量项目（变体为空）被整批补齐。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app"))
    client = VariantClient([])
    collector = Collector(_single_cfg(), db, client)

    collector.backfill_variants()

    row = db.project_exists("legacy/app")
    assert "中文说明文档" in row["readme_zh"]
    assert row["ml_variants_at"]


def test_backfill_variants_skips_fresh_rows(tmp_path):
    """窗口保护：刚采集过的项目（已盖时间戳）不会被每次启动重复探测。"""
    db = Database(tmp_path / "test.db")
    client = VariantClient([_item("org/app-a", 5000)])
    collector = Collector(_single_cfg(), db, client)
    collector.collect_once()
    collector.wait_backfill()
    collected = len(client.bundle_calls)

    collector.backfill_variants()

    assert len(client.bundle_calls) == collected


def test_backfill_variants_failure_keeps_retry_window(tmp_path):
    """镜像异常时保留空时间戳（不抛错），下个窗口自动重试。"""
    class BrokenClient(FakeClient):
        def get_readme_bundle(self, full_name, default_readme=None):
            raise GitHubError("镜像不可用")

    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app"))
    collector = Collector(_single_cfg(), db, BrokenClient([]))

    collector.backfill_variants()  # 不应抛出

    row = db.project_exists("legacy/app")
    assert row["ml_variants_at"] == ""
    assert row["readme_zh"] == ""


class AllLangClient(FakeClient):
    """模拟「README 声明什么语言就采什么」的客户端：中/英 + 其它语言一起返回。"""

    def __init__(self, items=None, extras=None):
        super().__init__(items)
        self.extras = extras if extras is not None else {
            "ja": "# 日本語の説明",
            "fr": "# Version française",
        }

    def get_readme_bundle(self, full_name, default_readme=None):
        out = {"default": default_readme, "zh": "# 应用\n中文说明。", "en": None}
        out.update(self.extras)
        return out


def test_variants_json_keeps_only_non_core_languages():
    """readme_variants JSON 只装中/英之外的语言（中/英各有专列，避免重复存储）。"""
    from app.collector import _variants_json

    raw = _variants_json({
        "default": "# App", "zh": "# 中文", "en": "# English",
        "ja": "# 日本語", "ko": "   ",  # 空白内容不算变体
    })
    assert json.loads(raw) == {"ja": "# 日本語"}
    assert _variants_json({"default": "# App", "zh": "# 中文", "en": None}) == "{}"
    assert _variants_json(None) == "{}"


def test_variants_json_truncates_huge_variants():
    """单个语言变体正文按上限截断，避免撑大数据库与详情接口。"""
    from app.collector import _VARIANT_MAX_CHARS, _variants_json

    data = json.loads(_variants_json({"ja": "あ" * (_VARIANT_MAX_CHARS + 100)}))
    assert len(data["ja"]) == _VARIANT_MAX_CHARS


def test_collect_stores_all_declared_languages(tmp_path):
    """采集路径：README 声明的全部语言入库（中/英专列 + 其余进 readme_variants）。"""
    db = Database(tmp_path / "test.db")
    collector = Collector(_single_cfg(), db, AllLangClient([_item("org/app-a", 5000)]))
    collector.collect_once()
    collector.wait_backfill()

    row = db.project_exists("org/app-a")
    assert "中文说明" in row["readme_zh"]
    variants = json.loads(row["readme_variants"])
    assert set(variants) == {"ja", "fr"}
    assert variants["ja"] == "# 日本語の説明"
    # 详情页接口按语言键下发其它语言版本
    from app.main import _project_detail_out
    assert _project_detail_out(row).readme_variants["fr"] == "# Version française"


def test_list_projects_missing_all_variants_selects_unmigrated_rows(tmp_path):
    """全语言回填范围：有原文但从未做过全语言采集的行（'{}'/'其它语言'均已完成）。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/pending"))
    db.upsert_project(_legacy_project("legacy/done-empty", readme_variants="{}"))
    db.upsert_project(_legacy_project("legacy/done-langs", readme_variants='{"ja": "# 日本語"}'))
    db.upsert_project(_legacy_project("legacy/no-readme", readme=""))

    rows = db.list_projects_missing_all_variants()
    assert [r["full_name"] for r in rows] == ["legacy/pending"]


def test_backfill_variants_also_fills_never_collected_languages(tmp_path):
    """启动回填：v19 之前的存量项目（只有中/英）也会补采其它语言。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app"))
    client = AllLangClient([])
    collector = Collector(_single_cfg(), db, client)

    collector.backfill_variants()

    row = db.project_exists("legacy/app")
    assert set(json.loads(row["readme_variants"])) == {"ja", "fr"}
    assert row["ml_variants_at"]  # 已盖时间戳，不再进下个窗口
    assert db.list_projects_missing_all_variants() == []


def test_upsert_keeps_richer_variants_when_new_pass_sees_only_core(tmp_path):
    """再采集只看到中/英（'{}'）时保留库里已有的其它语言，避免镜像抖动丢内容。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app", readme_variants='{"ja": "# 日本語"}'))

    db.upsert_project(_legacy_project("legacy/app", readme_variants="{}"))

    row = db.project_exists("legacy/app")
    assert json.loads(row["readme_variants"]) == {"ja": "# 日本語"}


def test_upsert_overwrites_variants_with_richer_new_pass(tmp_path):
    """反向：新采集采到更多语言时正常覆盖（不是只写不改）。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app", readme_variants="{}"))

    db.upsert_project(_legacy_project("legacy/app", readme_variants='{"ja": "# 日本語"}'))

    row = db.project_exists("legacy/app")
    assert json.loads(row["readme_variants"]) == {"ja": "# 日本語"}


def test_upsert_merges_variants_union(tmp_path):
    """变体取并集：本轮少采到几个语言不抹掉库内旧值，同语言以新内容为准。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app", readme_variants=json.dumps({
        "ja": "# 日本語（旧）", "ko": "# 한국어",
    }, ensure_ascii=False)))

    db.upsert_project(_legacy_project("legacy/app", readme_variants=json.dumps({
        "ja": "# 日本語（新）", "ru": "# Русский",
    }, ensure_ascii=False)))

    row = db.project_exists("legacy/app")
    assert json.loads(row["readme_variants"]) == {
        "ja": "# 日本語（新）", "ko": "# 한국어", "ru": "# Русский",
    }


def test_upsert_keeps_variants_when_candidate_has_no_field(tmp_path):
    """不涉及变体的更新（如收藏/分组联动）不会清空已采到的语言。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project(_legacy_project("legacy/app", readme_variants='{"ja": "# 日本語"}'))

    db.upsert_project(_legacy_project("legacy/app"))  # 候选字典里没有 readme_variants

    row = db.project_exists("legacy/app")
    assert json.loads(row["readme_variants"]) == {"ja": "# 日本語"}


def _json(raw: str) -> list:
    import json
    return json.loads(raw or "[]")


def _multi_cfg() -> Config:
    """两个分类的配置：游戏先搜、效率工具后搜（用于验证跨分类合并）。"""
    cfg = Config()
    cfg.categories = {"游戏": ["游戏引擎"], "效率工具": ["game speed"]}
    cfg.per_category_top = 5
    cfg.concurrency = 2
    return cfg


def test_cross_category_project_gets_secondary_category(tmp_path):
    """跨界项目：同一项目在两个分类命中时，第二个分类作为附加分类入库。"""
    db = Database(tmp_path / "test.db")
    items = [_item("game1024/OpenSpeedy", 19206)]
    collector = Collector(_multi_cfg(), db, FakeClient(items))
    summary = collector.collect_once()
    collector.wait_backfill()
    assert summary["stats"]["inserted"] == 1  # 去重后只入库一条
    assert summary["stats"]["skipped"] == 1   # 第二个分类命中 → 跳过但追加分类
    row = db.project_exists("game1024/OpenSpeedy")
    assert row["category"] == "游戏"  # 主分类保持首个命中
    cats = _json(row["categories"])
    assert "游戏" in cats and "效率工具" in cats  # 附加分类已合并


def test_db_migration_backfills_categories(tmp_path):
    """v8 迁移：存量项目 categories 按主分类初始化回填。"""
    db = Database(tmp_path / "test.db")
    db.upsert_project({
        "full_name": "org/legacy", "name": "legacy", "owner": "org",
        "description": "legacy", "homepage": "", "stars": 300,
        "language": "Go", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": "https://github.com/org/legacy", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": "影视", "major_version": "",
        "first_seen_at": "2026-10-01 00:00:00",
        "last_recommended_at": "2026-10-01 00:00:00", "last_round_id": 1,
        "readme": "",
    })
    # 模拟旧数据：categories 为空数组，触发 _backfill_categories 幂等回填
    with db._lock:
        db._conn.execute(
            "UPDATE projects SET categories='[]' WHERE full_name='org/legacy'"
        )
        db._conn.commit()
    db2 = Database(tmp_path / "test.db")  # 重新打开触发迁移回填
    row = db2.project_exists("org/legacy")
    cats = _json(row["categories"])
    assert cats == ["影视"]


def test_upsert_assets_string_not_double_encoded(tmp_path):
    """防二次编码：upsert 传入 assets 为 JSON 字符串（回填路径场景）时不再二次编码。"""
    db = Database(tmp_path / "test.db")
    # 先正常入库（assets 为 list）
    db.upsert_project({
        "full_name": "org/app-a", "name": "app-a", "owner": "org",
        "description": "app", "homepage": "", "stars": 500,
        "language": "Go", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": "https://github.com/org/app-a", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": "影视", "major_version": "",
        "first_seen_at": "2026-10-01 00:00:00",
        "last_recommended_at": "2026-10-01 00:00:00", "last_round_id": 1,
        "readme": "",
        "assets": [{"name": "a.zip", "url": "https://github.com/org/app-a/releases/download/1/a.zip", "size": 1, "platform": "Windows"}],
    })
    # 模拟回填路径：从 DB 读出的 assets 字符串再次 upsert（历史 bug：会被二次编码）
    row = db.project_exists("org/app-a")
    db.upsert_project({**dict(row), "assets": row["assets"]})
    out = _json(db.project_exists("org/app-a")["assets"])
    assert isinstance(out, list) and len(out) == 1
    assert out[0]["name"] == "a.zip"
    assert db.project_exists("org/app-a")["assets"].startswith("[{")  # 未被引号包裹

def test_manual_filter_overrides_query(tmp_path):
    """手动触发可覆盖过滤阈值：查询串与轮次 stats 记录实际参数。"""
    import json

    db = Database(tmp_path / "test.db")
    client = FakeClient([_item("org/app-b", 300)])
    collector = Collector(_single_cfg(), db, client)
    collector.collect_once(min_stars=100, max_inactive_days=30)
    assert "stars:>=100" in client.last_query
    assert "pushed:>=" in client.last_query
    row = db.last_round()
    assert row is not None
    stats = json.loads(row["stats"])
    assert stats["filter_used"]["min_stars"] == 100
    assert stats["filter_used"]["max_inactive_days"] == 30


def test_manual_filter_zero_means_unlimited(tmp_path):
    """0 表示不限：查询串不携带对应过滤条件。"""
    import json

    db = Database(tmp_path / "test.db")
    client = FakeClient([_item("org/app-c", 300)])
    collector = Collector(_single_cfg(), db, client)
    collector.collect_once(min_stars=0, max_inactive_days=0)
    assert "stars:>=" not in client.last_query
    assert "pushed:>=" not in client.last_query
    row = db.last_round()
    assert row is not None
    stats = json.loads(row["stats"])
    assert stats["filter_used"]["min_stars"] == 0
    assert stats["filter_used"]["max_inactive_days"] == 0
    # 300 星项目低于配置默认门槛（500），但本轮 min_stars=0 不限 → 必须真正入库
    # （回归防护：代码层兜底曾用 cfg.min_stars 而非本轮生效值，导致低门槛静默失效）
    assert db.project_exists("org/app-c") is not None


def test_discovery_round_matches_categories_locally(tmp_path):
    """发现轮：单次 created:> 查询 + 本地分类关键词匹配；无关仓库不入库。"""
    import json

    db = Database(tmp_path / "test.db")
    client = FakeClient([
        {**_item("org/new-anime", 5000), "description": "一个追剧神器 anime streaming"},
        {**_item("org/random-thing", 5000), "description": "storage manager for blobs"},
    ])
    collector = Collector(_single_cfg(), db, client)
    summary = collector.collect_once(source="discovery", discovery_only=True)
    collector.wait_backfill()

    assert summary["status"] == "success"
    assert summary["stats"]["mode"] == "discovery"
    assert summary["stats"]["discovered"] == 2
    assert summary["stats"]["matched"] == 1
    # 单次查询：created 窗口 + star 门槛
    assert client.search_calls == 1
    assert "created:>=" in client.last_query
    assert "stars:>=500" in client.last_query
    # 命中「追剧」关键词 → 入库归入影视；未命中的不入库
    row = db.project_exists("org/new-anime")
    assert row is not None and row["category"] == "影视"
    assert db.project_exists("org/random-thing") is None
    # 轮次落库带 discovery 来源
    assert db.last_round()["source"] == "discovery"
    assert json.loads(db.last_round()["stats"])["mode"] == "discovery"


def test_discovery_round_empty_is_success(tmp_path):
    """发现轮空结果是常态（近期没有命中关键词的新仓库），必须算成功而非失败。"""
    db = Database(tmp_path / "test.db")
    collector = Collector(_single_cfg(), db, FakeClient([]))
    summary = collector.collect_once(source="discovery", discovery_only=True)
    assert summary["status"] == "success"
    assert summary["stats"]["inserted"] == 0
    assert db.last_round()["status"] == "success"


def test_discovery_word_boundary_no_false_match(tmp_path):
    """英文关键词按整词匹配：rag 不应误配 rags/storage，agent 命中 ai agent。"""
    db = Database(tmp_path / "test.db")
    cfg = _single_cfg()
    cfg.categories = {"AI工具": ["agent", "rag"]}
    client = FakeClient([
        {**_item("org/storage-mgr", 5000), "description": "storage manager with large rags"},
        {**_item("org/ai-agent-x", 5000), "description": "An AI agent framework"},
    ])
    collector = Collector(cfg, db, client)
    collector.collect_once(source="discovery", discovery_only=True)
    collector.wait_backfill()
    assert db.project_exists("org/storage-mgr") is None
    row = db.project_exists("org/ai-agent-x")
    assert row is not None and row["category"] == "AI工具"
