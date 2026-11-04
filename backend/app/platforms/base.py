"""平台抽象基类与 full_name 前缀编解码。

`full_name` 的两种形态
======================
- 裸名 `owner/repo`：平台客户端内部调用（README/release 接口要的就是这个）
- 限定名 `source:owner/repo`：入库主键。GitHub 项目**不加前缀**（保持与存量 1945 条
  数据一致，也保持 `github.com/owner/repo` 语义直观）；非 GitHub 一律加前缀。

这样 `projects.full_name` 的 UNIQUE 约束天然区分了跨平台同名项目
（GitHub 的 `forgejo/forgejo` 与 Codeberg 的 `forgejo/forgejo` 是两行）。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# 平台标识 → 展示名。前端 i18n 另行映射，这里给后端日志/接口兜底用。
SOURCE_LABELS: dict[str, str] = {
    "github": "GitHub",
    "codeberg": "Codeberg",
    "gitee": "Gitee",
    "gitlab": "GitLab",
}


def qualify(source: str, bare_name: str) -> str:
    """裸名 → 入库主键。GitHub 不加前缀（存量兼容），其余平台 `source:owner/repo`。"""
    if source == "github":
        return bare_name
    return f"{source}:{bare_name}"


def split_qualified(qualified: str) -> tuple[str, str]:
    """入库主键 → (source, 裸名)。

    兼容历史数据：不含 `:` 的一律视为 github 的裸名。
    注意 owner/repo 本身不含 `:`，所以首个 `:` 即分隔符。
    """
    if ":" in qualified:
        source, _, bare = qualified.partition(":")
        if source in SOURCE_LABELS:
            return source, bare
    return "github", qualified


def bare_name(qualified: str) -> str:
    """入库主键 → 裸名（拼 html_url / 调平台 API 时用）。"""
    return split_qualified(qualified)[1]


@runtime_checkable
class PlatformClient(Protocol):
    """各平台客户端的最小契约（collector 只依赖这些方法）。

    实现方可以带自己的限流器与重试策略，但失败一律抛 `PlatformError`，
    让 collector 的分类级 try/except 接住并继续下一个分类。
    """

    source: str
    """平台标识，与 `SOURCE_LABELS` 的键一致。"""

    def search_repositories(
        self, keywords: list[str], min_stars: int = 0,
        per_page: int = 50, max_items: int = 100,
    ) -> list[dict]:
        """按关键词召回候选，返回**统一 GitHub 语义**的仓库字典列表。

        每个字典至少含：`full_name`（裸名）、`name`、`owner`、`description`、
        `stars`、`language`、`pushed_at`、`html_url`、`topics`。

        `keywords` 是分类关键词列表而不是拼接好的查询串：各平台的查询语法差异
        （GitHub 支持 `stars:>=N` 限定、Codeberg 只做短语匹配）由实现方各自处理。
        """
        ...

    def get_readme(self, bare_name: str) -> str | None:
        """抓 README 原文；不存在或网络失败返回 None（不抛异常）。"""
        ...

    def get_latest_release(self, bare_name: str) -> dict | None:
        """最新 release 摘要；确认无 release 返回 None。

        返回 `{"tag_name", "published_at", "body", "assets": [{"name","url","size"}]}`，
        与 `GitHubClient.get_latest_release` 完全同构，便于 collector 复用装配逻辑。
        """
        ...


class PlatformError(Exception):
    """平台请求失败（限流/网络/5xx）；`kind` 供上层区分是否值得重试。"""

    def __init__(self, message: str, kind: str = "unknown", retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
