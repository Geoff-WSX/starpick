"""配置加载与启动强校验。

- 加载 config.yaml
- 从环境变量 / 本地 .env 读取 GITHUB_TOKEN（绝不从 config 文件读取密钥）
- 启动校验：分类非空、阈值范围、端口合法、token 格式
- 错误早失败：校验失败抛出 ConfigError，启动脚本可据此定位
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"
RUNTIME_TOKEN_FILE = BASE_DIR / ".runtime_token"   # 前端设置页写入（权限 600）

TOKEN_PATTERN = re.compile(r"(ghp_|github_pat_|gho_|ghu_)[A-Za-z0-9_]+")


def is_valid_token(token: str) -> bool:
    """Token 格式校验：GitHub 个人访问令牌前缀。"""
    return bool(token and TOKEN_PATTERN.fullmatch(token.strip()))


def set_runtime_token(cfg: Config, token: str) -> str:
    """保存并热生效 Token（写 .runtime_token，优先级高于环境变量）。"""
    token = token.strip()
    if not is_valid_token(token):
        raise ConfigError("Token 格式非法：应以 ghp_ / github_pat_ / gho_ / ghu_ 开头")
    cfg.runtime_token = token
    try:
        RUNTIME_TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
        try:
            RUNTIME_TOKEN_FILE.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        raise ConfigError(f"写入运行时 Token 文件失败: {exc}") from exc
    return token


def clear_runtime_token(cfg: Config) -> None:
    """清除运行时 Token（回退到环境变量配置，若存在）。"""
    cfg.runtime_token = ""
    try:
        RUNTIME_TOKEN_FILE.unlink(missing_ok=True)
    except OSError:
        pass


class ConfigError(Exception):
    """配置校验失败。message 中携带可定位的具体配置项。"""


def _load_env_file(path: Path) -> None:
    """加载 .env 文件（仅设置尚未存在的环境变量，避免覆盖真实环境变量）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_env_ref(value: Any) -> Any:
    """解析 ${ENV_NAME} 引用；未设置时原样保留字符串并在校验阶段报错。"""
    if isinstance(value, str):
        m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value.strip())
        if m:
            return os.environ.get(m.group(1), "")
    return value


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8000
    static_dir: Path = BASE_DIR.parent / "frontend" / "dist"

    interval_hours: int = 6
    discovery_created_hours: int = 72   # 发现轮回看窗口：只搜最近 N 小时内创建的新仓库
    full_sweep_hour: int = 4            # 每日全量关键词扫荡的时刻（0-23，配置时区）
    timezone: str = "Asia/Shanghai"
    misfire_grace_seconds: int = 3600
    catchup_threshold_rounds: int = 2
    update_interval_hours: int = 24   # 更新检查频率（定时增量，与采集轮次分开调度）

    token: str = ""
    runtime_token: str = ""   # 前端设置页保存的 Token（热生效，优先于环境变量）
    search_rate_per_minute: int = 10
    core_rate_per_hour: int = 60
    request_timeout: int = 15
    retry_max: int = 3
    mirror_enabled: bool = True
    mirror_prefixes: list[str] = field(
        default_factory=lambda: ["https://gh-proxy.com/", "https://ghproxy.net/"]
    )
    per_category_top: int = 5
    concurrency: int = 6
    update_batch_size: int = 30    # 每轮增量检查的项目数（最久未检查优先，滚动轮询）

    min_stars: int = 500
    max_inactive_days: int = 90
    zh_weight: float = 1.2
    require_zh: bool = False

    re_recommend_on_major_update: bool = True
    rounds_retention: int = 90
    skip_existing_days: int = 7   # 已采集且 README 完整的项目，N 天内跳过详情抓取（只拉新项目）

    categories: dict[str, list[str]] = field(default_factory=dict)

    # ---- 派生 ----
    @property
    def effective_token(self) -> str:
        """生效 Token：前端运行时配置优先，其次环境变量/.env。"""
        return self.runtime_token or self.token

    @property
    def has_token(self) -> bool:
        return bool(self.effective_token)

    @property
    def search_rate(self) -> int:
        # 有 token 时 GitHub 将搜索配额提升到 30/min，无需用户手动改
        return 30 if self.has_token else self.search_rate_per_minute

    @property
    def core_rate(self) -> int:
        return 5000 if self.has_token else self.core_rate_per_hour


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml 解析失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml 根节点必须是映射")

    _load_env_file(BASE_DIR / ".env")
    cfg = Config()
    # 运行时 Token（前端设置页保存）优先：重启后仍保留
    if RUNTIME_TOKEN_FILE.exists():
        saved = RUNTIME_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if is_valid_token(saved):
            cfg.runtime_token = saved

    def _get(section: str, key: str, default: Any) -> Any:
        sec = raw.get(section, {})
        return sec.get(key, default) if isinstance(sec, dict) else default

    # server
    cfg.host = str(_get("server", "host", cfg.host))
    cfg.port = int(_get("server", "port", cfg.port))
    static = _get("server", "static_dir", str(cfg.static_dir))
    # 相对路径以 backend/ 为基准解析（../frontend/dist → 项目根/frontend/dist）
    cfg.static_dir = (BASE_DIR / static).resolve() if not Path(static).is_absolute() else Path(static).resolve()

    # schedule
    cfg.interval_hours = int(_get("schedule", "interval_hours", cfg.interval_hours))
    cfg.discovery_created_hours = int(_get("schedule", "discovery_created_hours", cfg.discovery_created_hours))
    cfg.full_sweep_hour = int(_get("schedule", "full_sweep_hour", cfg.full_sweep_hour))
    cfg.timezone = str(_get("schedule", "timezone", cfg.timezone))
    cfg.misfire_grace_seconds = int(_get("schedule", "misfire_grace_seconds", cfg.misfire_grace_seconds))
    cfg.catchup_threshold_rounds = int(_get("schedule", "catchup_threshold_rounds", cfg.catchup_threshold_rounds))
    cfg.update_interval_hours = int(_get("schedule", "update_interval_hours", cfg.update_interval_hours))

    # github
    gh = raw.get("github", {}) if isinstance(raw.get("github", {}), dict) else {}
    token_env = str(gh.get("token_env", "GITHUB_TOKEN"))
    cfg.token = _resolve_env_ref(os.environ.get(token_env, ""))
    cfg.search_rate_per_minute = int(gh.get("search_rate_per_minute", cfg.search_rate_per_minute))
    cfg.core_rate_per_hour = int(gh.get("core_rate_per_hour", cfg.core_rate_per_hour))
    cfg.request_timeout = int(gh.get("request_timeout", cfg.request_timeout))
    cfg.retry_max = int(gh.get("retry_max", cfg.retry_max))
    cfg.mirror_enabled = bool(gh.get("mirror_enabled", cfg.mirror_enabled))
    # 兼容旧配置单值 mirror_prefix 与新配置 mirror_prefixes 列表
    raw_prefixes = gh.get("mirror_prefixes", gh.get("mirror_prefix", cfg.mirror_prefixes))
    if isinstance(raw_prefixes, str):
        cfg.mirror_prefixes = [raw_prefixes] if raw_prefixes.strip() else []
    elif isinstance(raw_prefixes, list):
        cfg.mirror_prefixes = [str(p).strip() for p in raw_prefixes if str(p).strip()]
    else:
        cfg.mirror_prefixes = []
    cfg.per_category_top = int(gh.get("per_category_top", cfg.per_category_top))
    cfg.concurrency = int(gh.get("concurrency", cfg.concurrency))
    cfg.update_batch_size = int(gh.get("update_batch_size", cfg.update_batch_size))

    # filter
    flt = raw.get("filter", {}) if isinstance(raw.get("filter", {}), dict) else {}
    cfg.min_stars = int(flt.get("min_stars", cfg.min_stars))
    cfg.max_inactive_days = int(flt.get("max_inactive_days", cfg.max_inactive_days))
    cfg.zh_weight = float(flt.get("zh_weight", cfg.zh_weight))
    cfg.require_zh = bool(flt.get("require_zh", cfg.require_zh))

    # dedup
    dd = raw.get("dedup", {}) if isinstance(raw.get("dedup", {}), dict) else {}
    cfg.re_recommend_on_major_update = bool(dd.get("re_recommend_on_major_update", cfg.re_recommend_on_major_update))
    cfg.rounds_retention = int(dd.get("rounds_retention", cfg.rounds_retention))
    cfg.skip_existing_days = int(dd.get("skip_existing_days", cfg.skip_existing_days))

    # categories
    cats = raw.get("categories", {})
    if isinstance(cats, dict):
        for name, kws in cats.items():
            if isinstance(kws, list):
                cfg.categories[str(name)] = [str(k) for k in kws if str(k).strip()]

    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    errors: list[str] = []
    if not cfg.categories:
        errors.append("categories: 至少需要 1 个分类")
    for name, kws in cfg.categories.items():
        if not kws:
            errors.append(f"categories.{name}: 关键词列表为空")
    if cfg.port < 1 or cfg.port > 65535:
        errors.append(f"server.port: {cfg.port} 超出合法端口范围 (1-65535)")
    if cfg.interval_hours < 1:
        errors.append("schedule.interval_hours: 必须 >= 1")
    if cfg.discovery_created_hours < 1 or cfg.discovery_created_hours > 720:
        errors.append("schedule.discovery_created_hours: 必须在 1-720 之间")
    if cfg.full_sweep_hour < 0 or cfg.full_sweep_hour > 23:
        errors.append("schedule.full_sweep_hour: 必须在 0-23 之间")
    if cfg.update_interval_hours < 1:
        errors.append("schedule.update_interval_hours: 必须 >= 1")
    if cfg.update_batch_size < 1:
        errors.append("github.update_batch_size: 必须 >= 1")
    if cfg.min_stars < 0:
        errors.append("filter.min_stars: 必须 >= 0")
    if cfg.max_inactive_days < 1:
        errors.append("filter.max_inactive_days: 必须 >= 1")
    if cfg.retry_max < 0:
        errors.append("github.retry_max: 必须 >= 0")
    if cfg.concurrency < 1 or cfg.concurrency > 32:
        errors.append("github.concurrency: 建议范围 1-32")
    if cfg.effective_token and not is_valid_token(cfg.effective_token):
        errors.append("github.token_env: 引用的 Token 格式非法（应以 ghp_/github_pat_ 等开头）")
    if errors:
        raise ConfigError("配置校验失败，请检查以下项：\n- " + "\n- ".join(errors))
