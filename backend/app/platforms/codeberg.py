"""Codeberg（Forgejo/Gitea 系）平台客户端。

实测约束（2026-11，见 AGENTS.md「多平台采集」）
==============================================
- **不按分类关键词搜**：实测按分类关键词只能捞到 1~12 星的课程/练习仓库，而全站
  真正的高星项目（zig 6993 ★、forgejo 5768 ★、Gadgetbridge 2016 ★）与 STARPICK
  的 12 个分类关键词几乎不重合。改为「全站高星榜 + 本地关键词归类」（见
  `search_repositories` 的说明）。
- **搜索是短语匹配**：`q=video` 有结果、`q=video player` 返回 0 条。
- **中文关键词无结果**：`q=视频` 恒返回 0（Codeberg 主体是欧洲项目）。
- **`limit` 上限 50**：传 100/500 都只返 50，翻页有效。
- **限流宽松**：`ratelimit-policy: baseline;q=2000;w=600`（2000 次/10 分钟），
  远好于 GitHub 匿名搜索的 10 次/分钟，正常采集不会撞到。
- README 走 `/api/v1/repos/{owner}/{repo}/raw/README.md`（自动用默认分支，不占配额）。

README 只取默认 README.md 一种文件名：Codeberg 上中英双语变体极少，
`/raw/` 端点对不存在的文件返 404，全量扫名单会白耗 2000 次配额。
"""
from __future__ import annotations

import logging
import time

import requests

from .base import PlatformError

log = logging.getLogger("kazumi")

CODEBERG_API = "https://codeberg.org/api/v1"
UA = "starpick/1.3 (+https://github.com/Geoff-WSX/starpick)"

# 榜单单页硬上限（服务端限制，传更大也只返 50）
_PAGE_SIZE = 50
# 全站榜单最多翻几页（50 × 20 = 1000 条上限）。Codeberg 星数分布很陡：前 1000 条
# 已覆盖 ≥5 星的项目，再往下全是长尾，对推荐无意义。
_MAX_PAGES_TOTAL = 20


def _to_repo(raw: dict) -> dict:
    """Codeberg 仓库对象 → 统一 GitHub 语义字典（collector 下游只认这套键）。"""
    full_name = raw.get("full_name") or ""
    owner = (raw.get("owner") or {}).get("login") or full_name.split("/")[0]
    return {
        "full_name": full_name,
        "name": raw.get("name") or full_name.split("/")[-1],
        "owner": owner,
        "description": raw.get("description") or "",
        "homepage": raw.get("website") or "",
        "stars": int(raw.get("stars_count") or 0),
        # Codeberg 的 updated_at 是仓库活动时间，语义最接近 GitHub 的 pushed_at
        "pushed_at": raw.get("updated_at") or "",
        "language": raw.get("language") or "",
        "html_url": raw.get("html_url") or "",
        "topics": raw.get("topics") or [],
        # 存档/镜像仓库不作为推荐候选（镜像站在 Codeberg 上很常见且无意义）
        "_archived": bool(raw.get("archived")),
        "_mirror": bool(raw.get("mirror")),
        "_empty": bool(raw.get("empty")),
    }


class CodebergClient:
    """Codeberg 搜索/README/release 客户端；无 Token（公开 API，匿名即可用）。"""

    source = "codeberg"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept": "application/json"})
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=16, pool_maxsize=16, max_retries=0
        )
        self.session.mount("https://", adapter)
        # 软限流：Codeberg 给的是 2000/600s，留足余量按 8 次/秒节流，
        # 避免并发抓 README 时把自己打成 429。
        self._min_interval = 0.125
        self._last_call = 0.0

    # ---------------- 内部 ----------------
    def _throttle(self) -> None:
        delta = time.monotonic() - self._last_call
        if delta < self._min_interval:
            time.sleep(self._min_interval - delta)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: dict | None = None, *, raw: bool = False):
        self._throttle()
        url = path if path.startswith("http") else f"{CODEBERG_API}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PlatformError(f"Codeberg 请求失败: {exc}", kind="network",
                                retryable=True) from exc
        if resp.status_code == 404:
            raise PlatformError(f"Codeberg 404: {path}", kind="not_found")
        if resp.status_code == 429:
            raise PlatformError("Codeberg 限流", kind="rate_limit", retryable=True)
        if resp.status_code >= 500:
            raise PlatformError(f"Codeberg {resp.status_code}", kind="server",
                                retryable=True)
        if resp.status_code >= 400:
            raise PlatformError(f"Codeberg {resp.status_code}: {path}", kind="client")
        return resp.text if raw else resp.json()

    # ---------------- 契约实现 ----------------
    def search_repositories(
        self, keywords: list[str], min_stars: int = 0,
        per_page: int = _PAGE_SIZE, max_items: int = 100,
    ) -> list[dict]:
        """全站高星榜召回（不按分类关键词搜）。

        为什么不是「逐关键词搜索」：实测（2026-11）Codeberg 上按分类关键词搜到的
        几乎全是 1~12 星的课程/练习仓库，而全站真正的高星项目（zig 6993 ★、
        forgejo 5768 ★、Gadgetbridge 2016 ★）与 STARPICK 的分类关键词几乎不重合
        —— 它们靠「项目名 + 简介」被识别，而不是靠某个搜索词被召回。

        所以策略是：按 star 倒序翻页拉全站榜单，交给 collector 的
        `_match_categories` 做本地关键词归类，归不上的自然丢弃。这样只用少量请求
        就能拿到 Codeberg 上真正值得推荐的项目，也不会把低星凑数仓库带进来。

        `keywords` 参数保留是为了满足 PlatformClient 契约（collector 对平台无差别
        调用），本实现不用于检索——按分类拆词搜在 Codeberg 上是反效果。
        """
        page_size = min(per_page, _PAGE_SIZE)
        seen: dict[str, dict] = {}
        # 至少要拉够能让本地复检挑出入选分类的候选量；max_items 是全平台上限，
        # 但真实命中率低（大部分高星项目不属于 12 个消费级分类），故多翻几页。
        target = max(max_items * 4, 200)
        for page in range(1, _MAX_PAGES_TOTAL + 1):
            try:
                data = self._get(
                    "/repos/search",
                    {
                        "q": "",
                        "limit": page_size,
                        "page": page,
                        "sort": "stars",
                        "order": "desc",
                    },
                )
            except PlatformError as exc:
                log.warning("Codeberg 榜单拉取失败 page=%s: %s", page, exc)
                break
            batch = data.get("data") or []
            if not batch:
                break
            for raw in batch:
                repo = _to_repo(raw)
                # 已按 star 倒序，低于阈值说明后面都不用看了
                if repo["stars"] < min_stars:
                    return list(seen.values())[:max_items]
                if repo["_archived"] or repo["_empty"] or repo["_mirror"]:
                    continue
                seen.setdefault(repo["full_name"], repo)
            if len(batch) < page_size or len(seen) >= target:
                break
        return list(seen.values())[:max_items]

    def get_readme(self, bare_name: str) -> str | None:
        """取默认分支 README.md 原文。

        先试 README.md，再试 README.rst（老项目常见）、readme.md（小写）。
        `/raw/` 端点不占 API 配额，代价只是几次 404。
        """
        for filename in ("README.md", "readme.md", "README.rst"):
            try:
                text = self._get(
                    f"/repos/{bare_name}/raw/{filename}", raw=True
                )
                if text and text.strip():
                    return text
            except PlatformError as exc:
                if exc.kind == "not_found":
                    continue
                log.warning("Codeberg README 抓取失败 %s/%s: %s", bare_name, filename, exc)
                return None
        return None

    def get_latest_release(self, bare_name: str) -> dict | None:
        """取最新 release；从未发布过 release 返回 None。

        Codeberg 的 `/releases` 返回数组（无 `/latest` 端点），按 created_at 倒序，
        取第一条非 draft、非 prerelease 的。全部都是预发布时退回第一条，
        避免把「只发过 beta」的项目当成无安装包。
        """
        try:
            data = self._get(f"/repos/{bare_name}/releases", {"limit": 10})
        except PlatformError as exc:
            if exc.kind == "not_found":
                return None  # 仓库没有 release 功能/无发布
            raise
        if not isinstance(data, list) or not data:
            return None
        stable = [
            r for r in data
            if not r.get("draft") and not r.get("prerelease")
        ]
        chosen = stable[0] if stable else data[0]
        assets = [
            {
                "name": a.get("name", ""),
                "url": a.get("browser_download_url", ""),
                "size": int(a.get("size") or 0),
            }
            for a in (chosen.get("assets") or [])
            if a.get("browser_download_url")
        ]
        return {
            "tag_name": chosen.get("tag_name", ""),
            "published_at": chosen.get("published_at") or chosen.get("created_at") or "",
            "body": chosen.get("body") or "",
            "assets": assets,
        }
