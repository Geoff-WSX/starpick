"""多平台开源项目来源抽象层。

各平台客户端实现同一组最小接口，供 `Collector` 按来源循环调用：

- `search_repositories(keywords, min_stars, per_page, max_items)` → 候选列表
- `get_readme(full_name)` → README 原文（失败返回 None）
- `get_latest_release(full_name)` → release 摘要（无可发布内容返回 None）

返回结构统一为 GitHub 语义（`full_name` / `stars` / `topics` / `assets` 等），
让 `collector` 的下游流程（复检 / 打分 / 入库 / 安装包回填）无需感知平台差异。

`full_name` 在两个层面不同：
- **平台内部调用**（如 `get_readme`）用裸名 `owner/repo`；
- **入库主键**用带前缀名 `source:owner/repo`（非 GitHub 才加前缀），避免跨平台重名撞
  `projects.full_name` 的 UNIQUE 约束。转换由 `qualify` / `split_qualified` 负责。
"""
from __future__ import annotations

from ..config import Config
from .base import (
    SOURCE_LABELS,
    PlatformClient,
    PlatformError,
    bare_name,
    qualify,
    split_qualified,
)
from .codeberg import CodebergClient
from .gitee import GiteeClient

__all__ = [
    "SOURCE_LABELS",
    "PlatformClient",
    "PlatformError",
    "bare_name",
    "qualify",
    "split_qualified",
    "CodebergClient",
    "GiteeClient",
    "build_platform_clients",
    "missing_source_requirements",
]


def build_platform_clients(cfg: Config, github_client) -> dict[str, object]:
    """按 `cfg.enabled_sources` 组装平台客户端字典（source → client）。

    GitHub 始终复用采集器已构造的 client（它带着 Token 与限流器，不能重建）；
    其它平台按需实例化。未启用的平台、以及**缺少运行前提**的平台不出现在返回值里，
    采集循环据此跳过。

    缺少前提的典型是 Gitee：它的搜索接口匿名不可用（返回空数组而非报错），
    没有 Token 就实例化只会白跑一轮并静默得到 0 结果，所以直接不注册。
    前端通过 `missing_source_requirements()` 得知哪些平台为何不可用。
    """
    clients: dict[str, object] = {}
    for source in cfg.enabled_sources:
        if source == "github":
            clients["github"] = github_client
        elif source == "codeberg":
            clients["codeberg"] = CodebergClient(timeout=cfg.request_timeout)
        elif source == "gitee":
            # 无 Token 不实例化：Gitee 搜索必需 Token，否则静默返回空
            if cfg.gitee_token:
                clients["gitee"] = GiteeClient(cfg.gitee_token,
                                               timeout=cfg.request_timeout)
        # 未知平台已在 config.validate 阶段拒绝启动，这里不会走到
    return clients


def missing_source_requirements(cfg: Config) -> dict[str, str]:
    """启用但缺少运行前提的平台 → 原因（供前端提示用户去配置）。

    返回的键是「启用却没实例化」的平台。空字典表示所有启用平台都可用。
    """
    missing: dict[str, str] = {}
    for source in cfg.enabled_sources:
        if source == "gitee" and not cfg.gitee_token:
            missing["gitee"] = "gitee_token_missing"
    return missing
