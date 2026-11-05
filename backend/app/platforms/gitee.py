"""Gitee（码云）平台客户端。

实测约束（2026-11，见 AGENTS.md「多平台采集」）
==============================================
- **搜索必须 Token**：匿名调 `/search/repositories` 返回空数组 `[]`（HTTP 200，
  不是报错），带无效 Token 返 401。没有 Token 时该平台直接跳过（`build_platform_clients`
  不实例化），不要靠匿名搜索。
- **限流**：响应头 `X-RateLimit-Limit: 60`（匿名档），带 Token 更高。
- **仓库详情匿名可读**：`/repos/{owner}/{repo}` 无需 Token，字段齐全
  （`stargazers_count` / `pushed_at` / `default_branch` / `description`）。
- **没有 topics**：列表与详情的 `topics` 都是 null。分类匹配只能靠
  「名称 + 简介」，这会让召回率低于 GitHub（GitHub 有 topics 参与匹配）。
- **README 走 raw 直链**：`/{owner}/{repo}/raw/{默认分支}/README.md`，
  不占 API 配额；分支名必须从仓库详情取（Gitee 上 `3.0` 这类非 main/master
  分支名很常见，硬编码 main/master 会大量 404）。
- **Release assets 基本都是源码包**：Gitee 公开 API 返回的 release assets 只有
  自动生成的 `<tag>.zip` / `<tag>.tar.gz`，真正的二进制附件要登录态才有权限
  （`/releases/attachments` 匿名返 40001）。这些源码包会被 collector 的
  `_is_meta_asset` 过滤掉，所以 Gitee 项目多数最终落在「复制安装提示词」形态。
- **html_url 带 `.git` 后缀**（`https://gitee.com/a/b.git`），入库前要剥掉，
  否则前端链接点开是裸 git 地址。
"""
from __future__ import annotations

import logging
import re
import time

import requests

from .base import PlatformError

log = logging.getLogger("kazumi")

GITEE_API = "https://gitee.com/api/v5"
UA = "starpick/1.3 (+https://github.com/Geoff-WSX/starpick)"

# 搜索单页上限（Gitee 文档允许 100，取 50 与其它平台对齐，减少单次响应体积）
_PAGE_SIZE = 50
# 每个关键词最多翻几页（含 Token 后配额较高，翻 3 页足够拿够候选）
_MAX_PAGES_PER_KEYWORD = 3

# Gitee release 里自动生成的源码归档下载地址特征：
#   https://gitee.com/{owner}/{repo}/archive/refs/tags/v1.2.3.zip
# 它们不是安装包，展示成「下载版本」会让用户下一个源码压缩包。
_ARCHIVE_URL_PATTERN = re.compile(r"/archive/refs/")


def _is_source_archive(asset: dict) -> bool:
    """判断 Gitee 的 release 资产是否为自动生成的源码归档（而非真实安装包）。

    判据优先看 URL（`/archive/refs/` 是源码归档的固定路径），其次按
    `<tag>.zip` / `<tag>.tar.gz` 这种「名字就是版本号」的形态兜底。
    真实上传的二进制附件（如 `app-1.2.3-win64.exe`）不受影响。
    """
    url = (asset.get("browser_download_url") or "").lower()
    if _ARCHIVE_URL_PATTERN.search(url):
        return True
    name = (asset.get("name") or "").lower().strip()
    # 形如 v1.2.3.zip / 1.2.3.tar.gz：整名就是一个版本号
    return bool(re.fullmatch(r"v?\d[\w.\-]*\.(zip|tar\.gz|tgz|tar\.bz2)", name))


def _to_repo(raw: dict) -> dict:
    """Gitee 仓库对象 → 统一 GitHub 语义字典（collector 下游只认这套键）。"""
    full_name = raw.get("full_name") or ""
    namespace = raw.get("namespace") or {}
    owner = namespace.get("path") or full_name.split("/")[0]
    html_url = (raw.get("html_url") or "").rstrip("/")
    # Gitee 的 html_url 常带 .git 后缀，剥掉才是可浏览的仓库页
    if html_url.endswith(".git"):
        html_url = html_url[:-4]
    return {
        "full_name": full_name,
        "name": raw.get("name") or full_name.split("/")[-1],
        "owner": owner,
        "description": raw.get("description") or "",
        "homepage": raw.get("homepage") or "",
        "stars": int(raw.get("stargazers_count") or 0),
        # Gitee 的 pushed_at 语义与 GitHub 一致（最后推送时间）
        "pushed_at": raw.get("pushed_at") or raw.get("updated_at") or "",
        "language": raw.get("language") or "",
        "html_url": html_url,
        # Gitee 无 topics 字段：留空数组，让 _match_categories 只用名称+简介
        "topics": [],
        "_default_branch": raw.get("default_branch") or "",
    }


class GiteeClient:
    """Gitee 搜索/README/release 客户端（搜索需 Token，其余匿名）。"""

    source = "gitee"

    def __init__(self, token: str, timeout: int = 20) -> None:
        self.token = (token or "").strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept": "application/json"})
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=16, pool_maxsize=16, max_retries=0
        )
        self.session.mount("https://", adapter)
        # 软限流：无 token 时 Gitee 给 60 次/小时，带 token 配额高得多，
        # 按 4 次/秒节流（Gitee 对突发请求比较敏感，比 Codeberg 保守些）
        self._min_interval = 0.25
        self._last_call = 0.0
        # 仓库默认分支缓存（README 抓取要用，避免每次多打一次详情请求）
        self._branch_cache: dict[str, str] = {}

    # ---------------- 内部 ----------------
    def _throttle(self) -> None:
        delta = time.monotonic() - self._last_call
        if delta < self._min_interval:
            time.sleep(self._min_interval - delta)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: dict | None = None, *, raw: bool = False,
             auth: bool = True):
        """发请求；`auth=True` 时附 access_token（仅 API 端点需要，raw 直链不要）。"""
        self._throttle()
        url = path if path.startswith("http") else f"{GITEE_API}{path}"
        q = dict(params or {})
        if auth and self.token:
            q["access_token"] = self.token
        try:
            resp = self.session.get(url, params=q or None, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PlatformError(f"Gitee 请求失败: {exc}", kind="network",
                                retryable=True) from exc
        if resp.status_code == 404:
            raise PlatformError(f"Gitee 404: {path}", kind="not_found")
        if resp.status_code == 401:
            # Token 失效：明确报出来，避免被当成「没数据」静默跳过
            raise PlatformError("Gitee Token 无效或已过期", kind="auth")
        if resp.status_code == 403:
            raise PlatformError("Gitee 拒绝访问（可能是限流或权限不足）", kind="rate_limit",
                                retryable=True)
        if resp.status_code == 429:
            raise PlatformError("Gitee 限流", kind="rate_limit", retryable=True)
        if resp.status_code >= 500:
            raise PlatformError(f"Gitee {resp.status_code}", kind="server",
                                retryable=True)
        if resp.status_code >= 400:
            raise PlatformError(f"Gitee {resp.status_code}: {path}", kind="client")
        return resp.text if raw else resp.json()

    # ---------------- 契约实现 ----------------
    def search_repositories(
        self, keywords: list[str], min_stars: int = 0,
        per_page: int = _PAGE_SIZE, max_items: int = 100,
    ) -> list[dict]:
        """按关键词搜索（Gitee 支持中文关键词，且 `sort=stars_count` 生效）。

        与 Codeberg 不同，Gitee 的搜索是按相关性/星数排序的**真搜索**，所以
        逐关键词搜 + 合并去重即可，不需要拉全站榜单。
        """
        if not self.token:
            return []
        page_size = min(per_page, _PAGE_SIZE)
        seen: dict[str, dict] = {}
        # 鉴权失败（Token 无效/过期）要往上报：静默返回空会让用户以为
        # 「Gitee 上没这类项目」，实际是 Token 问题。只报第一次就停，避免刷屏。
        first_error: PlatformError | None = None
        for kw in keywords:
            kw = (kw or "").strip()
            if not kw:
                continue
            for page in range(1, _MAX_PAGES_PER_KEYWORD + 1):
                try:
                    data = self._get(
                        "/search/repositories",
                        {
                            "q": kw,
                            "sort": "stars_count",
                            "order": "desc",
                            "per_page": page_size,
                            "page": page,
                        },
                    )
                except PlatformError as exc:
                    log.warning("Gitee 搜索失败 kw=%s page=%s: %s", kw, page, exc)
                    if first_error is None:
                        first_error = exc
                    break
                if not isinstance(data, list) or not data:
                    break
                for raw in data:
                    repo = _to_repo(raw)
                    if not repo["full_name"]:
                        continue
                    if repo["stars"] < min_stars:
                        continue
                    seen.setdefault(repo["full_name"], repo)
                if len(data) < page_size:
                    break
            if len(seen) >= max_items:
                break
        # 一条都没搜到、又有鉴权错误 → 抛出去让采集轮次显示失败原因
        if not seen and first_error is not None and first_error.kind == "auth":
            raise first_error
        return list(seen.values())[:max_items]

    def _default_branch(self, bare_name: str) -> str:
        """取仓库默认分支（带缓存）；失败回退 main。"""
        if bare_name in self._branch_cache:
            return self._branch_cache[bare_name]
        branch = "main"
        try:
            info = self._get(f"/repos/{bare_name}", auth=False)
            branch = (info.get("default_branch") or "main").strip() or "main"
        except PlatformError as exc:
            log.debug("Gitee 取默认分支失败 %s: %s", bare_name, exc)
        self._branch_cache[bare_name] = branch
        return branch

    def get_readme(self, bare_name: str) -> str | None:
        """取 README 原文。

        分支名从仓库详情取（Gitee 上 `3.0` 这类非 main 分支很常见）。
        raw 直链不占 API 配额，但分支必须先查到，否则必然 404。
        """
        branch = self._default_branch(bare_name)
        for filename in ("README.md", "readme.md", "README.rst"):
            url = f"https://gitee.com/{bare_name}/raw/{branch}/{filename}"
            try:
                text = self._get(url, raw=True, auth=False)
                if text and text.strip():
                    return text
            except PlatformError as exc:
                if exc.kind == "not_found":
                    continue
                log.warning("Gitee README 抓取失败 %s/%s: %s", bare_name, filename, exc)
                return None
        return None

    def get_latest_release(self, bare_name: str) -> dict | None:
        """取最新 release（**匿名请求**，不带 token）。

        Gitee 的 release 接口匿名可读，而带上一个失效 token 反而返 401
        （实测：匿名 200 / 假 token 401）。这里刻意不认证，既避免 token 过期连带
        打断安装包探测，也少一次可能失败的鉴权。

        Gitee 的公开 release assets 只有自动生成的 `<tag>.zip` / `.tar.gz`，
        真二进制附件要登录态（匿名 `/releases/attachments` 返 40001）。
        因此这里照常返回，但下游 `_is_meta_asset` 会把源码包过滤掉 ——
        多数 Gitee 项目最终表现为「无安装包」，这是平台现状而非 bug。
        """
        try:
            data = self._get(f"/repos/{bare_name}/releases", {"per_page": 10},
                             auth=False)
        except PlatformError as exc:
            if exc.kind == "not_found":
                return None
            raise
        if not isinstance(data, list) or not data:
            return None
        stable = [r for r in data if not r.get("prerelease")]
        chosen = stable[0] if stable else data[0]
        raw_assets = chosen.get("assets") or []
        assets = [
            {
                "name": a.get("name", ""),
                "url": a.get("browser_download_url", ""),
                "size": int(a.get("size") or 0),
            }
            for a in raw_assets
            if a.get("browser_download_url") and not _is_source_archive(a)
        ]
        return {
            "tag_name": chosen.get("tag_name", ""),
            "published_at": chosen.get("created_at") or "",
            "body": chosen.get("body") or "",
            "assets": assets,
        }
