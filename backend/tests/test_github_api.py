"""GitHub API 限流器与 403 重试测试（网络部分用 mock session）。"""
import time
from unittest import mock

from app.config import Config
from app.github_api import (
    GitHubClient,
    GitHubError,
    RateLimiter,
    extract_variant_links,
    resolve_repo_path,
    variant_lang,
)


def test_rate_limiter_paces_second_call():
    limiter = RateLimiter(60)  # 60/min = 1 秒 1 个令牌
    t0 = time.monotonic()
    limiter.acquire()  # 立即通过
    limiter.acquire()  # 需等待约 1 秒
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.9


def test_rate_limiter_zero_rate_is_noop():
    limiter = RateLimiter(0)
    t0 = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    assert time.monotonic() - t0 < 0.5


def test_rate_limiter_high_rate_is_fast():
    limiter = RateLimiter(6000)  # 6000/min → 10ms 间隔
    t0 = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    assert time.monotonic() - t0 < 1.0


# ---------------- 403 rate limit 分级重试 ----------------
class _FakeResp:
    def __init__(self, status, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def json(self):
        import json as _json
        return _json.loads(self.text)


class _FakeSession:
    """按序返回预置响应；用尽后返回 200 空结果。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, method, url, timeout=15, **kwargs):
        self.calls += 1
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return _FakeResp(200, '{"ok": true}')


def _client_with_responses(responses):
    cfg = Config()
    cfg.retry_max = 3
    client = GitHubClient(cfg)
    client.session = _FakeSession(responses)
    return client


def test_403_rate_limit_waits_until_reset_and_retries():
    """403 + X-RateLimit-Reset 在合理等待范围内 → 等待后重试成功。"""
    future_reset = int(time.time()) + 2
    responses = [
        _FakeResp(403, "API rate limit exceeded", {"X-RateLimit-Reset": str(future_reset)}),
        _FakeResp(200, '{"items": [{"full_name": "org/app"}]}'),
    ]
    client = _client_with_responses(responses)
    t0 = time.monotonic()
    data = client.search_repositories("q", per_page=10)
    assert client.session.calls == 2
    assert data == [{"full_name": "org/app"}]
    assert time.monotonic() - t0 >= 1.5


def test_403_rate_limit_too_far_raises():
    """重置时间过远（>90s）→ 不等待，直接抛错。"""
    far_reset = int(time.time()) + 600
    client = _client_with_responses([
        _FakeResp(403, "API rate limit exceeded", {"X-RateLimit-Reset": str(far_reset)}),
    ])
    try:
        client.search_repositories("q")
        assert False, "应当抛出 GitHubError"
    except GitHubError as exc:
        assert exc.kind == "rate_limit"


def test_429_retry_after_backoff():
    """429 按 Retry-After 等待后重试。"""
    responses = [
        _FakeResp(429, "slow down", {"Retry-After": "1"}),
        _FakeResp(200, '{"items": []}'),
    ]
    client = _client_with_responses(responses)
    data = client.search_repositories("q")
    assert client.session.calls == 2
    assert data == []


# ---------------- release assets 字段（size 保留） ----------------
def test_get_latest_release_keeps_asset_size():
    """get_latest_release 的 assets 必须保留 size（前端下载弹窗按文件大小展示）。"""
    release_json = (
        '{"tag_name":"v1.2.3","published_at":"2026-01-01T00:00:00Z","body":"x",'
        '"assets":['
        '{"name":"app-arm64.dmg","browser_download_url":"https://x/1.dmg","size":123456789},'
        '{"name":"app.exe","browser_download_url":"https://x/2.exe","size":2048}'
        ']}'
    )
    client = _client_with_responses([_FakeResp(200, release_json)])
    rel = client.get_latest_release("a/b")
    assert rel is not None
    assets = rel["assets"]
    assert len(assets) == 2
    assert assets[0]["name"] == "app-arm64.dmg"
    assert assets[0]["url"] == "https://x/1.dmg"
    assert assets[0]["size"] == 123456789
    assert assets[1]["size"] == 2048


# ---------------- 多语言 README 变体：文件名识别 / 链接还原 / 链接优先探测 ----------------
def test_variant_lang_classification():
    """变体文件名 → 语言归一键；非语言后缀（dev/ai/template）不得误判为语言。"""
    cases = {
        "README.zh-CN.md": "zh",
        "README_CN.md": "zh",
        "README-cn.md": "zh",
        "README.cmn-Hans-CN.md": "zh",
        "README.zh-TW.md": "zh-Hant",
        "README_TW.md": "zh-Hant",
        "README_HK.md": "zh-Hant",
        "readme-ja.md": "ja",
        "README.en.md": "en",
        "README_EN.md": "en",
        "README.en-US.md": "en",
        "README.vi-VN.md": "vi",
        "README.md": None,
        "README": None,
        "README.dev.md": None,
        "README.ai.md": None,
        "README.template.md": None,
        "docs/notes.md": None,
    }
    for name, want in cases.items():
        assert variant_lang(name) == want, name


def test_resolve_repo_path_accepts_relative_and_github_urls():
    """链接还原：相对路径 / 子目录 / blob 与 raw 绝对链接（含大小写）都能还原。"""
    assert resolve_repo_path("./README_CN.md", "org/app") == "README_CN.md"
    assert resolve_repo_path("docs/readme/README_zh.md", "org/app") == "docs/readme/README_zh.md"
    assert resolve_repo_path("docs/../README_zh.md", "org/app") == "README_zh.md"
    assert resolve_repo_path("README_zh.md#说明", "org/app") == "README_zh.md"
    assert resolve_repo_path("/i18n/README.zh-cn.md", "org/app") == "i18n/README.zh-cn.md"
    assert resolve_repo_path(
        "https://github.com/org/app/blob/main/README-en.md", "org/app") == "README-en.md"
    assert resolve_repo_path(
        "https://github.com/Org/App/raw/HEAD/docs/README_zh.md", "org/app"
    ) == "docs/README_zh.md"
    assert resolve_repo_path(
        "https://raw.githubusercontent.com/org/app/main/README_CN.md", "org/app"
    ) == "README_CN.md"


def test_resolve_repo_path_rejects_external_and_escaping():
    """外链 / 其它仓库 / 越出仓库根 / 纯锚点一律拒绝。"""
    assert resolve_repo_path("https://example.com/README_CN.md", "org/app") is None
    assert resolve_repo_path("https://github.com/other/repo/blob/main/README_CN.md", "org/app") is None
    assert resolve_repo_path("../../secret.md", "org/app") is None
    assert resolve_repo_path("#readme", "org/app") is None
    assert resolve_repo_path("", "org/app") is None


def test_extract_variant_links_from_readme():
    """语言切换链接提取：按语言归类、保留子目录与大小写、去重、忽略非变体链接。"""
    readme = (
        "[English](README.md) · [简体中文](docs/readme/README_zh.md) · "
        "[繁體中文](docs/readme/README_zh-TW.md) · [日本語](README_ja.md) · "
        "[English readme](https://github.com/org/app/blob/main/README_en.md) · "
        "[别家中文](https://github.com/other/repo/blob/main/README_CN.md) · "
        "[使用文档](docs/guide.md) · [重复](docs/readme/README_zh.md)"
    )
    links = extract_variant_links(readme, "org/app")
    assert links["zh"] == ["docs/readme/README_zh.md"]  # 去重且保留子目录
    assert links["zh-Hant"] == ["docs/readme/README_zh-TW.md"]
    assert links["ja"] == ["README_ja.md"]
    assert links["en"] == ["README_en.md"]  # blob 绝对链接同样可还原
    assert "README.md" not in links.get("zh", [])  # 默认 README 不算变体


def _bundle_client(hits: set[str], calls: list, monkeypatch) -> GitHubClient:
    """构造离线客户端：只认 hits 里的路径，所有探测调用记入 calls。"""
    return _offline_client({p: f"# {p}\n内容" for p in hits}, calls, monkeypatch)


def _offline_client(contents: dict[str, str], calls: list, monkeypatch) -> GitHubClient:
    """构造离线客户端：路径命中 contents 时返回对应内容（可逐路径定制），探测记入 calls。"""
    cfg = Config()
    cfg.mirror_prefixes = ["https://mirror.invalid/"]
    client = GitHubClient(cfg)

    def fake_probe(full_name, filename):
        calls.append(filename)
        return contents.get(filename)

    monkeypatch.setattr(client, "_probe_readme_file", fake_probe)
    return client


def test_bundle_prefers_readme_declared_path(monkeypatch):
    """README 自报的链接路径优先于静态名单（子目录/大小写特异变体由此采到）。"""
    readme = "# App\n[简体中文](docs/readme/README_zh.md)\n"
    calls: list[str] = []
    client = _bundle_client({"docs/readme/README_zh.md", "README.zh-CN.md"}, calls, monkeypatch)

    bundle = client.get_readme_bundle("org/app", default_readme=readme)

    assert bundle["default"] == readme  # 传入原文时不再抓原文
    assert bundle["zh"] == "# docs/readme/README_zh.md\n内容"  # 链接路径命中优先
    assert "README.md" not in calls  # 未重复抓原文
    assert "docs/readme/README_zh.md" in calls


def test_bundle_falls_back_to_static_names(monkeypatch):
    """README 无可解析链接 → 回退静态名单扫描（老路径不变）。"""
    readme = "# App\nEnglish only readme.\n"
    calls: list[str] = []
    client = _bundle_client({"README_CN.md"}, calls, monkeypatch)

    bundle = client.get_readme_bundle("org/app", default_readme=readme)

    assert bundle["zh"] == "# README_CN.md\n内容"
    assert bundle["en"] is None
    assert set(GitHubClient._README_ZH_NAMES) <= set(calls)  # 静态名单确实扫过


def test_probe_readme_variant_uses_mirror_head_only():
    """按需/变体探测：只走镜像通道、只探 HEAD（不三倍扫分支）。"""
    cfg = Config()
    cfg.mirror_prefixes = ["https://mirror-1/", "https://mirror-2/"]
    client = GitHubClient(cfg)
    urls: list[str] = []

    class _Session:
        def get(self, url, timeout=6):
            urls.append(url)
            return _FakeResp(200, "# 中文说明")

    client.session = _Session()
    text = client.probe_readme_variant("org/app", "docs/readme/README_zh.md")
    assert text == "# 中文说明"
    assert urls == [
        "https://mirror-1/https://raw.githubusercontent.com/org/app/HEAD/docs/readme/README_zh.md"
    ]


def test_bundle_collects_all_declared_languages(monkeypatch):
    """README 声明的每种语言都采（不止中/英），各自按链接里的真实路径探测。"""
    readme = (
        "[简体中文](docs/readme/README_zh.md) · [日本語](docs/readme/README_ja.md) · "
        "[한국어](docs/readme/README_ko.md) · [Русский](docs/readme/README_ru.md)"
    )
    calls: list[str] = []
    client = _bundle_client(
        {"docs/readme/README_zh.md", "docs/readme/README_ja.md",
         "docs/readme/README_ko.md", "docs/readme/README_ru.md"},
        calls, monkeypatch,
    )

    bundle = client.get_readme_bundle("org/app", default_readme=readme)

    assert bundle["zh"] == "# docs/readme/README_zh.md\n内容"
    assert bundle["ja"] == "# docs/readme/README_ja.md\n内容"
    assert bundle["ko"] == "# docs/readme/README_ko.md\n内容"
    assert bundle["ru"] == "# docs/readme/README_ru.md\n内容"
    assert bundle["en"] is None  # 没声明的语言不硬造
    assert "docs/readme/README_ja.md" in calls


def test_bundle_second_round_picks_languages_from_variants(monkeypatch):
    """二轮补全：中文版比原文多列出的语种（如马拉雅拉姆语）也能采到。"""
    readme = "[简体中文](docs/README_zh.md)\n"
    calls: list[str] = []
    client = _offline_client({
        "docs/README_zh.md": "# 中文\n[മലയാളം](docs/README_ml.md)\n",
        "docs/README_ml.md": "# മലയാളം\n",
    }, calls, monkeypatch)

    bundle = client.get_readme_bundle("org/app", default_readme=readme)

    assert bundle["zh"].startswith("# 中文")
    assert bundle["ml"] == "# മലയാളം\n"  # 一轮没声明，从中文版正文里补到
    assert calls.count("docs/README_ml.md") == 1


def test_bundle_caps_language_count(monkeypatch):
    """语言清单异常长时按上限截断（防止单个项目打爆探测请求量）。"""
    from app.github_api import _OTHER_LANG_CODES, _VARIANT_MAX_LANGS

    codes = sorted(_OTHER_LANG_CODES)[: _VARIANT_MAX_LANGS + 5]  # 37 种真实语言代码
    readme = "".join(f" · [{c}](docs/README_{c}.md)" for c in codes)
    calls: list[str] = []
    client = _bundle_client(set(), calls, monkeypatch)

    client.get_readme_bundle("org/app", default_readme=readme)

    probed = [c for c in calls if c.startswith("docs/")]
    assert len(probed) == _VARIANT_MAX_LANGS  # 超出的 5 种不再探测
    assert set(probed) <= {f"docs/README_{c}.md" for c in codes}
