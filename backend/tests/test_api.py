"""API 契约测试：FastAPI TestClient 验证各端点。"""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import main

    cfg = Config()
    cfg.categories = {"影视": ["追剧"]}
    cfg.static_dir = tmp_path / "no-dist"

    # 隔离 lifespan：不读真实 config、不碰真实 history.db、不触发真实补跑
    monkeypatch.setattr("app.main.load_config", lambda: cfg)
    db = Database(tmp_path / "api.db")
    monkeypatch.setattr("app.main.Database", lambda path: db)
    # lifespan 会用真实 Collector 覆盖 state.collector；替换为占位对象，
    # 保证 POST /api/run 不会真实触发网络采集
    monkeypatch.setattr("app.main.Collector", lambda cfg, db: None)
    db.upsert_project({
        "full_name": "org/app-a", "name": "app-a", "owner": "org",
        "description": "A tool for watching shows", "homepage": "", "stars": 5000,
        "language": "Python", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": "https://github.com/org/app-a", "topics": ["tool"],
        "platforms": ["Windows"], "system_requirements": ["内存: 8GB"],
        "download_url": "https://dl/app-a", "zh_friendly": 0,
        "category": "影视", "major_version": "2",
        "first_seen_at": _NOW, "last_recommended_at": _NOW,
        "last_round_id": 1,
        "readme": "# App-A\nAn awesome tool for watching shows.",
        # 采集入库的其它语言 README（中/英之外）由详情接口按语言键下发
        "readme_variants": json.dumps(
            {"ja": "# 日本語 README", "ko": "# 한국어 README"}, ensure_ascii=False
        ),
        "assets": [
            {"name": "app_windows.zip", "url": "https://dl/app-a/win", "size": 100, "platform": "Windows"},
            {"name": "app_android.apk", "url": "https://dl/app-a/android", "size": 90, "platform": "Android"},
        ],
    })
    db.upsert_project({
        "full_name": "org/app-zh", "name": "app-zh", "owner": "org",
        "description": "一个中文追剧工具", "homepage": "", "stars": 800,
        "language": "Vue", "pushed_at": "2026-09-10T00:00:00Z",
        "html_url": "https://github.com/org/app-zh", "topics": [],
        "platforms": [], "system_requirements": [],
        "download_url": "", "zh_friendly": 1,
        "category": "影视", "major_version": "",
        "first_seen_at": _NOW, "last_recommended_at": _NOW,
        "last_round_id": 1,
        "readme": "",
    })
    rid = db.create_round("2026-10-08 00:00:00")
    db.finish_round(rid, "success", {"影视": 2}, {"inserted": 2})
    # app-a 已探测过 release（有 assets）；app-zh 未探测 → 检测中态
    db.mark_assets_probed("org/app-a")

    main.state.cfg = cfg
    main.state.db = db
    main.state.collector = None
    main.state.scheduler = None
    with TestClient(main.app) as tc:
        yield tc
    db.close()
    main.state.cfg = None
    main.state.db = None


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_ok"] is True
    assert body["last_round"]["status"] == "success"


def test_list_projects(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    item = next(i for i in body["items"] if i["full_name"] == "org/app-a")
    assert item["platforms"] == ["Windows"]
    assert item["system_requirements"] == ["内存: 8GB"]
    assert len(item["assets"]) == 2
    assert item["assets"][0]["platform"] == "Windows"
    assert item["assets"][1]["platform"] == "Android"
    assert item["assets_probed"] is True
    assert item["lang"] == "en"
    zh_item = next(i for i in body["items"] if i["full_name"] == "org/app-zh")
    assert zh_item["lang"] == "zh"
    # 未探测过 release → 前端据此渲染「安装包检测中」态
    assert zh_item["assets_probed"] is False


def test_list_projects_zh_filter_and_sorts(client):
    """zh=1 只留中文友好；sort=new 按 first_seen_at 倒序（契约：合法排序值 200）。"""
    resp = client.get("/api/projects?zh=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["full_name"] == "org/app-zh"
    assert body["items"][0]["zh_friendly"] is True

    for sort_value in ("stars", "recent", "new"):
        r = client.get(f"/api/projects?sort={sort_value}")
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2
    # 非法排序值被 422 拦截
    assert client.get("/api/projects?sort=bogus").status_code == 422


def test_list_projects_filter_by_category(client):
    resp = client.get("/api/projects", params={"category": "影视"})
    assert resp.json()["total"] == 2
    resp = client.get("/api/projects", params={"category": "游戏"})
    assert resp.json()["total"] == 0


def test_list_projects_filter_by_lang(client):
    """中英文语言筛选：简介含中文 → zh，否则 → en。"""
    resp = client.get("/api/projects", params={"lang": "en"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["full_name"] == "org/app-a"
    resp = client.get("/api/projects", params={"lang": "zh"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["full_name"] == "org/app-zh"
    resp = client.get("/api/projects", params={"lang": "bad"})
    assert resp.status_code == 422


def test_list_projects_keyword(client):
    resp = client.get("/api/projects", params={"keyword": "watching"})
    assert resp.json()["total"] == 1
    resp = client.get("/api/projects", params={"keyword": "不存在词"})
    assert resp.json()["total"] == 0


def test_list_projects_slim_fields(client):
    """列表接口瘦身：不返回 readme 等大字段（详情接口才返回），降低列表页开销。"""
    resp = client.get("/api/projects")
    items = resp.json()["items"]
    assert items, "应至少有一个项目"
    for it in items:
        assert "readme" not in it
        assert "readme_zh" not in it
        assert "readme_en" not in it
    # 详情接口仍返回 README
    detail = client.get(f"/api/projects/{items[0]['id']}")
    assert "readme" in detail.json()


def test_project_detail(client):
    resp = client.get("/api/projects/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["full_name"] == "org/app-a"
    assert body["lang"] == "en"
    assert "An awesome tool" in body["readme"]
    assert "description_zh" not in body  # 机器翻译字段已下线（用户拒绝翻译功能）
    assert "readme_zh" in body            # 项目自带中文 README 变体（详情页语言切换）
    assert "readme_en" in body            # 项目自带英文 README 变体
    assert "mirror_prefixes" in body      # 镜像前缀：前端渲染 README 图片用
    assert client.get("/api/projects/999").status_code == 404


def test_project_detail_returns_other_language_readmes(client):
    """详情接口下发采集入库的其它语言 README（详情页切换语言直接用库内内容）。"""
    body = client.get("/api/projects/1").json()
    assert body["readme_variants"] == {
        "ja": "# 日本語 README", "ko": "# 한국어 README",
    }
    # 库里没有其它语言的项目返回空字典 → 前端回退到「按需拉取」
    assert client.get("/api/projects/2").json()["readme_variants"] == {}


def test_variants_of_tolerates_broken_values():
    """_variants_of 对缺列/空值/坏 JSON/非字典 JSON 都返回空字典，不拖垮详情接口。"""
    from app.main import _variants_of

    assert _variants_of({}) == {}
    assert _variants_of({"readme_variants": ""}) == {}
    assert _variants_of({"readme_variants": None}) == {}
    assert _variants_of({"readme_variants": "{坏 JSON"}) == {}
    assert _variants_of({"readme_variants": '["ja"]'}) == {}
    assert _variants_of({"readme_variants": '{"ja": "# 日本語", "ko": ""}'}) == {
        "ja": "# 日本語"  # 空内容不算语言版本
    }


class _StubVariantClient:
    """只实现变体探测的假客户端（记录调用），避免测试触网。"""

    def __init__(self, content="中文 README 内容"):
        self.content = content
        self.calls: list[tuple[str, str]] = []

    def probe_readme_variant(self, full_name, path):
        self.calls.append((full_name, path))
        return self.content


class _StubCollector:
    def __init__(self, client):
        self.client = client

    def is_running(self) -> bool:  # lifespan 停机等待用到
        return False


@pytest.fixture()
def variant_client(client, monkeypatch):
    """注入带假客户端的 collector（默认 fixture 里 collector 为 None）。"""
    from app import main

    stub = _StubVariantClient()
    monkeypatch.setattr(main.state, "collector", _StubCollector(stub))
    return stub


def test_readme_variant_endpoint_fetches_on_demand(client, variant_client):
    """按需拉取：README 写了但未预采的语言变体，点切换时才走镜像抓取。"""
    resp = client.get("/api/projects/1/readme-variant",
                      params={"path": "docs/readme/README_zh.md"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "docs/readme/README_zh.md"
    assert body["content"] == "中文 README 内容"
    assert body["truncated"] is False
    assert variant_client.calls == [("org/app-a", "docs/readme/README_zh.md")]


def test_readme_variant_endpoint_accepts_github_blob_url(client, variant_client):
    """README 里的绝对链接（GitHub blob）可直接传，服务端还原为仓库内路径。"""
    url = "https://github.com/org/app-a/blob/main/README_CN.md"
    resp = client.get("/api/projects/1/readme-variant", params={"path": url})
    assert resp.status_code == 200
    assert resp.json()["path"] == "README_CN.md"
    assert variant_client.calls == [("org/app-a", "README_CN.md")]


@pytest.mark.parametrize("bad", [
    "../backend/config.yaml",                                # 越出仓库根
    "README.md",                                             # 默认 README 不是语言变体
    "docs/guide.md",                                         # 非语言变体文件
    "README.dev.md",                                         # 非语言后缀
    "https://example.com/README_CN.md",                      # 站外
    "https://github.com/other/repo/blob/main/README_CN.md",  # 其它仓库
])
def test_readme_variant_endpoint_rejects_invalid_path(client, variant_client, bad):
    resp = client.get("/api/projects/1/readme-variant", params={"path": bad})
    assert resp.status_code == 400
    assert variant_client.calls == []  # 非法路径不触发抓取


def test_readme_variant_endpoint_404_when_unavailable(client, variant_client):
    """镜像拿不到（文件改名/删除）→ 404，前端展示回 GitHub 的兜底链接。"""
    variant_client.content = None
    resp = client.get("/api/projects/1/readme-variant", params={"path": "README_zh.md"})
    assert resp.status_code == 404


def test_readme_variant_endpoint_requires_ready_service(client):
    """collector 未就绪（服务未初始化）→ 503，且不触网。"""
    assert client.get("/api/projects/1/readme-variant",
                      params={"path": "README_zh.md"}).status_code == 503


def test_readme_variant_endpoint_404_for_missing_project(client, variant_client):
    assert client.get("/api/projects/999/readme-variant",
                      params={"path": "README_zh.md"}).status_code == 404
    assert variant_client.calls == []


def test_list_rounds(client):
    resp = client.get("/api/rounds")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["status"] == "success"


def test_stats(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_projects"] == 2
    assert body["rounds_by_status"]["success"] >= 1
    # assets_ready：安装包探测回填的变更信号（前端自动刷新据此广播）
    assert body["assets_ready"] == 1  # 夹具里 app-a 带 assets、app-zh 无
    # assets_pending：未探测项目数（顶栏「安装包检测 N/M」进度）
    assert body["assets_pending"] == 1  # app-zh 未探测


def test_run_returns_service_unavailable(client):
    # collector 被隔离为 None → 服务未就绪 → 503，且绝不应触发真实采集
    resp = client.post("/api/run")
    assert resp.status_code == 503


def test_invalid_sort_rejected(client):
    resp = client.get("/api/projects", params={"sort": "bad"})
    assert resp.status_code == 422


def test_collect_status_idle(client):
    """采集进度端点：无采集任务时返回空闲快照（默认值，不报错）。"""
    resp = client.get("/api/collect/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["phase"] == ""
    assert body["categories_total"] == 0


def test_list_projects_new_filter(client):
    """新增筛选：new=new 仅返回最近一轮开始时间之后首次入库的项目，且标记 is_new。"""
    from app import main

    # 造一个更早入库的旧项目（first_seen_at 早于最近轮次开始时间）
    main.state.db.upsert_project({
        "full_name": "org/app-old", "name": "app-old", "owner": "org",
        "description": "An old project", "homepage": "", "stars": 300,
        "language": "Go", "pushed_at": "2026-08-01T00:00:00Z",
        "html_url": "https://github.com/org/app-old", "topics": [],
        "platforms": [], "system_requirements": [],
        "download_url": "", "zh_friendly": 0,
        "category": "影视", "major_version": "",
        "first_seen_at": "2026-10-01 00:00:00",  # 早于最近轮次
        "last_recommended_at": "2026-10-01 00:00:00",
        "last_round_id": 0,
        "readme": "",
    })

    all_resp = client.get("/api/projects")
    assert all_resp.json()["total"] == 3

    resp = client.get("/api/projects", params={"new": "new"})
    body = resp.json()
    assert body["total"] == 2  # 只有最近一轮入库的 2 个项目
    names = {it["full_name"] for it in body["items"]}
    assert "org/app-old" not in names
    assert all(it["is_new"] for it in body["items"])
    assert body["new_since"]  # 返回口径时间

    # 普通列表：is_new 按同口径标记（旧项目 False）
    normal = client.get("/api/projects").json()
    old_item = next(it for it in normal["items"] if it["full_name"] == "org/app-old")
    assert old_item["is_new"] is False

    resp = client.get("/api/projects", params={"new": "bad"})
    assert resp.status_code == 422


def test_list_projects_returns_categories_and_secondary_filter(client):
    """多分类：列表返回 categories 字段；按附加分类筛选也能命中跨界项目。"""
    from app import main

    # 造一个跨界项目：主分类影视 + 附加分类效率工具
    main.state.db.upsert_project({
        "full_name": "org/cross", "name": "cross", "owner": "org",
        "description": "Cross tool", "homepage": "", "stars": 500,
        "language": "Go", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": "https://github.com/org/cross", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": "影视", "major_version": "",
        "first_seen_at": "2026-10-01 00:00:00",
        "last_recommended_at": "2026-10-01 00:00:00", "last_round_id": 0,
        "readme": "", "categories": ["影视", "效率工具"],
    })

    items = client.get("/api/projects").json()["items"]
    cross = next(it for it in items if it["full_name"] == "org/cross")
    assert cross["categories"] == ["影视", "效率工具"]

    # 按附加分类筛选：效率工具 应命中跨界项目
    eff = client.get("/api/projects", params={"category": "效率工具"}).json()
    assert any(it["full_name"] == "org/cross" for it in eff["items"])
    # 主分类筛选仍正常
    v = client.get("/api/projects", params={"category": "影视"}).json()
    assert any(it["full_name"] == "org/cross" for it in v["items"])


def test_list_projects_returns_mirror_prefixes(client):
    """下载加速：列表与详情均下发国内镜像前缀（前端拼「镜像前缀 + 原始URL」加速下载）。"""
    items = client.get("/api/projects").json()["items"]
    for it in items:
        assert it["mirror_prefixes"], "列表项应下发镜像前缀（Config 默认启用）"
        assert it["mirror_prefixes"][0].startswith("https://")
    detail = client.get(f"/api/projects/{items[0]['id']}").json()
    assert detail["mirror_prefixes"] == items[0]["mirror_prefixes"]

def test_run_with_payload(client):
    """POST /api/run 可携带过滤覆盖参数；collector 隔离时仍 503 且不触发真实采集。"""
    resp = client.post("/api/run", json={"min_stars": 100, "max_inactive_days": 30})
    assert resp.status_code == 503


def test_collect_params_endpoint(client):
    resp = client.get("/api/collect/params")
    assert resp.status_code == 200
    body = resp.json()
    assert body["min_stars"] >= 0
    assert body["max_inactive_days"] >= 0
    assert body["per_category_max"] >= 1


def test_settings_token_save_and_clear(client, tmp_path, monkeypatch):
    """设置页 Token：状态查询 / 格式校验 / 保存热生效（限流提升）/ 清除回退。"""
    import json as _json
    from app import config as cfgmod

    # 隔离运行时 Token 文件，避免写入真实 backend/.runtime_token
    monkeypatch.setattr(cfgmod, "RUNTIME_TOKEN_FILE", tmp_path / ".runtime_token")

    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["has_token"] is False

    # 非法格式
    r = client.post("/api/settings/token", json={"token": "not-a-gh-token"})
    assert r.status_code == 422

    # 保存并热生效：搜索限流应提升至 30/min
    r = client.post("/api/settings/token", json={"token": "ghp_testToken123456"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["search_rate_per_minute"] == 30

    # 查询只返回打码，不回传原文
    r = client.get("/api/settings")
    j = r.json()
    assert j["has_token"] is True
    assert "ghp_testToken123456" not in _json.dumps(j, ensure_ascii=False)
    assert j["masked_token"].startswith("ghp_")

    # 清除：回退匿名限流
    r = client.post("/api/settings/token/clear")
    assert r.status_code == 200
    assert r.json()["has_token"] is False
    assert r.json()["search_rate_per_minute"] == 10


def test_keyword_with_underscore_matches_name(client):
    """含下划线的关键词要能按 name 命中。

    回归：name 分支缺 ESCAPE，转义出的 \\_ 在无 ESCAPE 的 LIKE 里被当作字面反斜杠，
    导致带下划线的项目名搜不到（description 分支有 ESCAPE 所以看似正常）。
    """
    from app import main

    main.state.db.upsert_project({
        "full_name": "org/douyin_dl", "name": "Douyin_TikTok_DL", "owner": "org",
        "description": "a downloader", "homepage": "", "stars": 999,
        "language": "Python", "pushed_at": "2026-09-01T00:00:00Z",
        "html_url": "https://github.com/org/douyin_dl", "topics": [],
        "platforms": [], "system_requirements": [], "download_url": "",
        "zh_friendly": 0, "category": "影视", "major_version": "",
        "first_seen_at": _NOW, "last_recommended_at": _NOW,
        "last_round_id": 1, "readme": "# x",
    })
    resp = client.get("/api/projects", params={"keyword": "Douyin_TikTok"})
    assert resp.status_code == 200
    names = [i["full_name"] for i in resp.json()["items"]]
    assert "org/douyin_dl" in names
