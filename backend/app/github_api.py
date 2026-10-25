"""GitHub API 封装：搜索 / Release / README。

生产级要点：
- 限流令牌队列：搜索请求串行排队，守住配额
- 分级重试：429 按 Retry-After；5xx/超时指数退避（retry_max 上限）；持久错误不重试
- 镜像 failover：README/下载直链在直连失败时切换镜像前缀
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import requests

from .config import Config

log = logging.getLogger("kazumi.github")

API_BASE = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
# 分支候选：HEAD 自动解析默认分支（覆盖 main/master/develop/trunk 等任意命名），
# 后续候选兜底镜像/代理对 HEAD 支持不完整的情况
_README_BRANCHES = ("HEAD", "main", "master")
# 变体探测的分支候选：两路镜像均实测支持 HEAD（默认分支自动解析），
# 只探一个分支可把静态名单全扫的请求数压到三分之一
_PROBE_BRANCHES = ("HEAD",)


def _looks_like_error_page(text: str) -> bool:
    """镜像/网关 HTML 错误页识别（如 gh-proxy 回源失败时返回的 HTML 文档）。

    README 正文几乎不可能是完整 HTML 文档开头，以此为界避免把错误页当正文入库。
    """
    head = (text or "").lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")
UA = "starpick-good-project-finder/1.0"

# ---------------- 多语言 README 变体：文件名识别与链接还原 ----------------
# 语言代码表（小写、去分隔符后的紧凑形式）→ 归一键。前端 ProjectDetail.vue 的
# variantLang() 维护同一份表；后端是路径校验的权威方。
_ZH_CODES = {"zh", "cn", "chs", "hans", "cmn", "zhcn", "cnzh", "chinese"}
_ZH_HANT_CODES = {"zht", "tw", "hk", "hant", "tcn", "zhtw", "zhhk", "traditional", "fan"}
_EN_CODES = {"en", "eng", "english"}
# 常见其它语言（ISO 639-1 及常见写法）；README.dev.md / README.ai.md 之类
# 非语言后缀不在表内，不会被误判成语言变体
_OTHER_LANG_CODES = {
    "ar", "az", "be", "bg", "bn", "ca", "cs", "da", "de", "el", "es", "et", "eu",
    "fa", "fi", "fr", "gl", "he", "hi", "hr", "hu", "hy", "id", "is", "it", "ja",
    "jp", "ka", "kk", "km", "kn", "ko", "kr", "ky", "lo", "lt", "lv", "mk", "ml",
    "mn", "mr", "ms", "my", "ne", "nl", "no", "pa", "pl", "ps", "pt", "ro", "ru",
    "si", "sk", "sl", "sq", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "ua",
    "uk", "ur", "uz", "vi",
}
_VARIANT_NAME_RE = re.compile(r"^readme[._-]([a-z0-9._-]+)\.(?:md|markdown|mdx)$", re.I)
# 单项目变体探测的并发度与语言数上限（大型 i18n 仓库实测最多 40+ 种语言，
# 上限只为兜住异常长的链接清单，不影响正常采集）
_VARIANT_PROBE_WORKERS = 4
_VARIANT_MAX_LANGS = 64
_MD_LINK_RE = re.compile(r"\[[^\]]{0,80}\]\(([^)\s]+)\)")
_GITHUB_FILE_URL_RE = re.compile(
    r"^https?://(?:github\.com/([^/\s]+/[^/\s]+)/(?:blob|raw)"
    r"|raw\.githubusercontent\.com/([^/\s]+/[^/\s]+))"
    r"/[^/\s]+/(.+)$",
    re.I,
)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def variant_lang(basename: str) -> str | None:
    """README 变体文件名 → 语言归一键；非语言变体（README.md / README.dev.md）返回 None。

    "zh"=简体中文、"zh-Hant"=繁体中文、"en"=英文，其它语言返回代码原文（如 "ja"）。
    """
    m = _VARIANT_NAME_RE.match(basename or "")
    if not m:
        return None
    code = m.group(1).strip(".-_").lower().replace("_", "-")
    compact = code.replace("-", "")
    if compact in _ZH_CODES or code.startswith(("zh-cn", "zh-hans", "cmn-hans")):
        return "zh"
    if compact in _ZH_HANT_CODES or code.startswith(("zh-tw", "zh-hk", "zh-hant", "cmn-hant")):
        return "zh-Hant"
    if compact in _EN_CODES or code.startswith("en-"):
        return "en"
    if compact in _OTHER_LANG_CODES:
        return compact
    head = code.split("-")[0]
    if head in _OTHER_LANG_CODES:
        return head
    return None


def normalize_repo_path(raw: str) -> str | None:
    """仓库内相对路径归一化（去掉 ./ 与空段、解析 ..）；越出仓库根返回 None。"""
    parts: list[str] = []
    for seg in (raw or "").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts) or None


def resolve_repo_path(href: str, full_name: str) -> str | None:
    """README 中的链接 → 本仓库内的相对路径（外链/锚点/其它仓库返回 None）。

    支持相对路径与 GitHub blob / raw 绝对链接（含子目录）。绝对链接里写的路径
    大小写与仓库中真实文件一致，据此探测可避开"文件名大小写猜错"的漏采。
    """
    href = (href or "").strip().split("#")[0].split("?")[0]
    if not href or href.startswith("#"):
        return None
    if _SCHEME_RE.match(href):
        m = _GITHUB_FILE_URL_RE.match(href)
        if not m:
            return None
        repo = m.group(1) or m.group(2)
        if repo.lower() != (full_name or "").lower():
            return None
        return normalize_repo_path(m.group(3))
    return normalize_repo_path(href)


def extract_variant_links(readme: str, full_name: str) -> dict[str, list[str]]:
    """从 README 正文提取语言变体文件路径，按语言归类（保持出现顺序、去重）。

    项目自己的语言切换链接是最可靠的文件名来源：真实大小写与所在子目录如实
    记录，正好补上静态名单（固定根目录文件名、大小写敏感）的两类漏采。
    """
    found: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for href in _MD_LINK_RE.findall(readme or ""):
        rel = resolve_repo_path(href, full_name)
        if not rel:
            continue
        lang = variant_lang(rel.rsplit("/", 1)[-1])
        if not lang or (lang, rel) in seen:
            continue
        seen.add((lang, rel))
        found.setdefault(lang, []).append(rel)
    return found


@dataclass
class RateLimiter:
    """按分钟速率分发的令牌队列（令牌按固定间隔补充）。"""

    rate_per_minute: int
    _next_tick: float = field(default=0.0)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self) -> None:
        if self.rate_per_minute <= 0:
            return
        interval = 60.0 / self.rate_per_minute
        with self._lock:
            now = time.monotonic()
            if self._next_tick > now:
                wait = self._next_tick - now
            else:
                wait = 0.0
            self._next_tick = max(now, self._next_tick) + interval
        if wait:
            time.sleep(wait)


class GitHubError(Exception):
    def __init__(self, message: str, kind: str = "unknown", retryable: bool = False):
        super().__init__(message)
        self.kind = kind          # rate_limit / server / timeout / auth / not_found / other
        self.retryable = retryable


class GitHubClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": UA, "Accept": "application/vnd.github+json"}
        )
        self._token = cfg.effective_token
        if self._token:
            self.session.headers.update({"Authorization": f"Bearer {self._token}"})
        # README 多通道并行探测 + 采集并发时，默认连接池（10）会溢出并反复丢弃连接，
        # 调大池容量减少重建开销（64 仍会在回填+变体探测同时高峰时打满刷
        # "Connection pool is full" 告警，实测 128 后安静）
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=128, pool_maxsize=128, max_retries=0
        )
        self.session.mount("https://", adapter)
        # 搜索与 core 请求分开限流；core 速率按 小时配额折算为分钟
        self.search_limiter = RateLimiter(cfg.search_rate)
        self.core_limiter = RateLimiter(max(1, cfg.core_rate // 60))

    # ---------------- 运行时热生效：Token 变更无需重启 ----------------
    def apply_token(self, token: str) -> None:
        """热更新 GitHub Token 与对应限流速率（前端设置页保存后调用）。

        - 有 Token：写入 Authorization 头，限流提升为 搜索 30/min、core 5000/h
        - 空 Token：移除 Authorization 头，回退匿名限流
        """
        self._token = token
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            self.session.headers.pop("Authorization", None)
        # 重建限流器（新的 rate 由 cfg.has_token 派生，先已更新 runtime_token）
        self.search_limiter = RateLimiter(self.cfg.search_rate)
        self.core_limiter = RateLimiter(max(1, self.cfg.core_rate // 60))

    @property
    def has_auth(self) -> bool:
        return bool(self._token)

    # ---------------- 基础请求（分级重试） ----------------
    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", self.cfg.request_timeout)
        retries = 0
        while True:
            try:
                resp = self.session.request(method, url, timeout=timeout, **kwargs)
            except requests.Timeout as exc:
                if retries < self.cfg.retry_max:
                    retries += 1
                    self._backoff(retries)
                    continue
                raise GitHubError(f"请求超时: {url}", kind="timeout", retryable=True) from exc
            except requests.ConnectionError as exc:
                if retries < self.cfg.retry_max:
                    retries += 1
                    self._backoff(retries)
                    continue
                raise GitHubError(f"连接失败: {url}", kind="server", retryable=True) from exc

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5") or 5)
                if retries < self.cfg.retry_max:
                    retries += 1
                    log.warning("限流 429，等待 %ss 后重试 (%s)", retry_after, url)
                    time.sleep(min(retry_after, 30))
                    continue
                raise GitHubError(
                    f"限流 429（多次重试仍被限流）: {url}",
                    kind="rate_limit", retryable=True,
                )
            if resp.status_code in (500, 502, 503, 504):
                if retries < self.cfg.retry_max:
                    retries += 1
                    self._backoff(retries)
                    continue
                raise GitHubError(f"服务端错误 {resp.status_code}: {url}", kind="server", retryable=True)

            if resp.status_code == 401:
                raise GitHubError("认证失败（Token 无效或已失效）", kind="auth", retryable=False)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                # 配额上限：按 X-RateLimit-Reset 等待到重置时刻再重试（上限 90s）
                wait = 0
                reset = resp.headers.get("X-RateLimit-Reset")
                if reset:
                    try:
                        wait = int(reset) - int(time.time())
                    except (ValueError, TypeError):
                        wait = 0
                if 0 < wait <= 90 and retries < self.cfg.retry_max:
                    retries += 1
                    log.warning("触发配额上限，等待 %ss 至重置后重试 (%s)", wait, url)
                    time.sleep(wait)
                    continue
                raise GitHubError(
                    f"触发配额上限（403 rate limit，需等待 {max(wait, 0)}s）: {url}",
                    kind="rate_limit", retryable=True,
                )
            if resp.status_code == 404:
                raise GitHubError(f"资源不存在: {url}", kind="not_found", retryable=False)
            if resp.status_code >= 400:
                raise GitHubError(
                    f"请求失败 {resp.status_code}: {url} ({resp.text[:200]})",
                    kind="other", retryable=False,
                )
            return resp

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 10))

    # ---------------- 搜索 ----------------
    def search_repositories(
        self, query: str, per_page: int = 100, max_items: int = 200
    ) -> list[dict]:
        """按 star 倒序分页拉取搜索候选，到页尾或 max_items 上限停止。

        GitHub 搜索 API 单次最多返回 1000 条；无 Token 时每次翻页都消耗
        一次 search 配额，max_items 作为保护性上限（由每分类候选上限传入）。
        """
        items: list[dict] = []
        page = 1
        while True:
            self.search_limiter.acquire()
            resp = self._request(
                "GET",
                f"{API_BASE}/search/repositories",
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            batch = resp.json().get("items", [])
            if not batch:
                break
            items.extend(batch)
            if len(items) >= max_items or len(batch) < per_page:
                break
            page += 1
        return items[:max_items]

    # ---------------- Release ----------------
    def get_latest_release(self, full_name: str) -> dict | None:
        self.core_limiter.acquire()
        try:
            resp = self._request(
                "GET", f"{API_BASE}/repos/{full_name}/releases/latest"
            )
        except GitHubError as exc:
            if exc.kind in ("not_found",):
                return None
            raise
        data = resp.json()
        assets = [
            {
                "name": a.get("name", ""),
                "url": a.get("browser_download_url", ""),
                "size": int(a.get("size") or 0),
            }
            for a in data.get("assets", [])
        ]
        return {
            "tag_name": data.get("tag_name", ""),
            "published_at": data.get("published_at", ""),
            "body": data.get("body") or "",
            "assets": assets,
        }

    # ---------------- README ----------------
    # 项目自带多语言 README 的兜底文件名名单：仅在默认 README 里解析不出语言切换
    # 链接时使用（大小写敏感、只查仓库根，按实测出现频率排序）
    _README_ZH_NAMES = (
        "README.zh-CN.md", "README_CN.md", "README_zh.md", "README.zh.md",
        "README_cn.md", "README_zh-CN.md", "README-cn.md", "README_zh_cn.md",
    )
    _README_EN_NAMES = (
        "README_EN.md", "README.en.md", "README-EN.md",
        "README_en.md", "README.en-US.md", "README_en-US.md",
    )

    def get_readme_text(self, full_name: str) -> str | None:
        """抓取项目默认 README 原文（README.md），多通道 failover：

        1. raw.githubusercontent.com 直取（不占 API 配额）
        2. 国内镜像通道（gh-proxy.com / ghproxy.net 等，依次尝试）
        3. GitHub API /readme 兜底（自动解析默认分支与文件名变体，走 core 限流）

        分支候选依次尝试 main/master，避免额外调用 API 探测默认分支。
        """
        return self._fetch_readme_file(full_name, "README.md", use_api_fallback=True)

    def get_readme_bundle(
        self, full_name: str, default_readme: str | None = None
    ) -> dict[str, str | None]:
        """抓取项目自带的多语言 README：原文 + README 里声明的全部语言变体。

        返回 {"default": ..., "zh": ..., "en": ..., "<其它语言>": ...}；zh/en 恒在
        （没有则为 None），其它语言仅在 README 声明且抓到内容时出现。

        流程：a) 原文抓取与 static 名单扫描（zh/en 兜底）并行启动；b) 原文到手后按
        「README 自身写出的语言切换链接」并发探测每种语言（含子目录与 GitHub blob
        绝对链接，链接里的路径即仓库真实路径，优先级高于 static 名单）；c) 再从已
        取到的变体正文里补一轮新语言（中文版常比英文版多列出几个语种）。
        default_readme 传入库内已存原文时跳过原文抓取（变体重探场景）。
        """
        pool = ThreadPoolExecutor(max_workers=3)
        try:
            f_default = (
                pool.submit(self._fetch_readme_file, full_name, "README.md", True)
                if default_readme is None else None
            )
            f_static = {
                "zh": pool.submit(self._sweep_static_names, full_name, self._README_ZH_NAMES),
                "en": pool.submit(self._sweep_static_names, full_name, self._README_EN_NAMES),
            }
            default = f_default.result() if f_default else default_readme
            links = extract_variant_links(default or "", full_name)
            contents = self._probe_link_variants(full_name, links)
            if contents:
                extra: dict[str, list[str]] = {}
                for content in contents.values():
                    for lang, paths in extract_variant_links(content, full_name).items():
                        if lang not in links and lang not in contents and lang not in extra:
                            extra[lang] = paths
                if extra:
                    contents.update(self._probe_link_variants(full_name, extra))
            out: dict[str, str | None] = {"default": default}
            out.update(contents)
            for lang in ("zh", "en"):
                if lang not in out:
                    # 链接里没写中/英 → 回退 static 名单扫描（老仓库命名习惯）
                    out[lang] = f_static[lang].result()
            return out
        finally:
            # 不让未命中的静态扫描拖慢整体：链接已命中时后台线程自行收尾
            pool.shutdown(wait=False)

    def probe_readme_variant(self, full_name: str, path: str) -> str | None:
        """按需抓取单个 README 变体文件（详情页点语言切换但内容未预先采集时）。

        仅走镜像通道；path 必须是仓库内相对路径（由调用方校验为语言变体文件名）。
        """
        return self._probe_readme_file(full_name, path)

    def _probe_link_variants(
        self, full_name: str, links: dict[str, list[str]]
    ) -> dict[str, str]:
        """并发探测每种语言的自报链接：一种语言取第一个抓到的路径内容。

        语言数量按 _VARIANT_MAX_LANGS 截断（大型 i18n 仓库实测 40+ 种语言，
        截断只为兜住异常长的清单，不影响正常采集）。
        """
        langs = list(links)[:_VARIANT_MAX_LANGS]
        if not langs:
            return {}
        with ThreadPoolExecutor(max_workers=_VARIANT_PROBE_WORKERS) as pool:
            futs = {
                lang: pool.submit(self._probe_first_path, full_name, links[lang])
                for lang in langs
            }
            out: dict[str, str] = {}
            for lang, fut in futs.items():
                try:
                    content = fut.result()
                except Exception:  # 单语言失败不影响其它语言
                    continue
                if content:
                    out[lang] = content
        return out

    def _probe_first_path(self, full_name: str, paths: list[str]) -> str | None:
        for path in paths:
            content = self._probe_readme_file(full_name, path)
            if content:
                return content
        return None

    def _sweep_static_names(self, full_name: str, names: tuple[str, ...]) -> str | None:
        """按名单优先级并发扫描文件名（命中即按名单顺序取最靠前的那个）。"""
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(lambda n: (n, self._probe_readme_file(full_name, n)), names)
            )
        for _name, content in results:
            if content:
                return content
        return None

    def _probe_readme_file(self, full_name: str, filename: str) -> str | None:
        """轻量探测单个文件名（可含子目录路径）：仅走镜像通道（透明代理，404 即文件不存在）。

        raw 直连在当前网络常被重置、海外场景镜像 404 与 raw 一致，
        故变体探测不走 raw；分支只探 HEAD（两路镜像均支持，默认分支自动解析），
        避免静态名单全扫时 3 倍请求量。
        """
        raw_urls = [f"{RAW_BASE}/{full_name}/{b}/{filename}" for b in _PROBE_BRANCHES]
        urls: list[str] = []
        if self.cfg.mirror_enabled:
            for prefix in self.cfg.mirror_prefixes:
                urls += [prefix + u for u in raw_urls]
        for url in urls:
            try:
                resp = self.session.get(url, timeout=6)
            except Exception:
                continue
            if resp.status_code == 200 and not _looks_like_error_page(resp.text):
                return resp.text
        return None

    def _fetch_readme_file(self, full_name: str, filename: str,
                           use_api_fallback: bool) -> str | None:
        """按指定文件名抓取 README（raw 轻量 → 镜像 → API 兜底）。

        raw 直连用轻量抓取（短超时、不重试）：raw 是 CDN 无配额，
        在部分网络（如当前环境）连接会被重置，指数退避重试代价极高；
        镜像与 API 通道才走完整重试逻辑。
        """
        branches = _README_BRANCHES
        raw_urls = [f"{RAW_BASE}/{full_name}/{b}/{filename}" for b in branches]
        # 镜像优先（当前网络 raw 常被重置）：轻量抓取，短超时、不重试，
        # 镜像列表本身即 failover；避免偶发回源慢触发 15s 超时 + 指数退避
        urls: list[str] = []
        if self.cfg.mirror_enabled:
            for prefix in self.cfg.mirror_prefixes:
                urls += [prefix + u for u in raw_urls]
        for url in urls:
            try:
                resp = self.session.get(url, timeout=8)
            except Exception:
                continue
            if resp.status_code == 200 and not _looks_like_error_page(resp.text):
                return resp.text
        # raw 直连兜底（海外网络可用）：轻量抓取、短超时、不重试
        for url in raw_urls:
            try:
                resp = self.session.get(url, timeout=3)
            except Exception:
                continue
            if resp.status_code == 200 and not _looks_like_error_page(resp.text):
                return resp.text
        if use_api_fallback:
            try:
                self.core_limiter.acquire()
                resp = self._request(
                    "GET", f"{API_BASE}/repos/{full_name}/readme/{filename}",
                    headers={"Accept": "application/vnd.github.raw+json"},
                )
                return resp.text
            except GitHubError as exc:
                log.debug("README API 兜底失败 %s (%s)", full_name, exc.kind)
        return None
