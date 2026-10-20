"""运行期路径解析：只读资源目录 vs 可写数据目录。

- 开发模式（源码直接运行）：所有路径与历史行为完全一致——数据库/备份/Token/.env
  在 backend/ 下，logs/ 在项目根，前端产物取 frontend/dist。
- 打包模式（PyInstaller / py2app，sys.frozen）：程序目录只读，可写数据
  （数据库/备份/日志/Token/.env/config.yaml）统一放平台用户数据目录，
  只读资源（前端产物、默认 config.yaml）随包分发。

环境变量 STARPICK_DATA_DIR 可显式覆盖数据目录：开发模式下只影响
数据库/备份/日志（config.yaml 与 .env 仍读 backend/，方便带真实配置做隔离测试）；
打包模式下连 config.yaml/.env/Token 一起切换（首次运行会把默认配置种到该目录）。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "StarPick"


def is_frozen() -> bool:
    """是否为打包（冻结）运行环境。"""
    return bool(getattr(sys, "frozen", False))


def _data_dir_override() -> str:
    return os.environ.get("STARPICK_DATA_DIR", "").strip()


_RESOURCE_MARKERS = ("frontend_dist", "config.yaml")


def _looks_like_resources(path: Path) -> bool:
    return any((path / marker).exists() for marker in _RESOURCE_MARKERS)


def resource_dir() -> Path:
    """只读资源根（前端产物 / 默认 config.yaml）。

    打包后按候选目录逐个探测标记文件：PyInstaller 的 macOS .app 把数据放
    Contents/Resources 而 sys._MEIPASS 指向 Contents/Frameworks；Windows onedir
    则统一在 _MEIPASS（_internal）。探测失败时退回 _MEIPASS / 可执行文件目录。
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        exe = Path(sys.executable).resolve()
        candidates: list[Path] = []
        if meipass:
            mp = Path(meipass)
            candidates += [mp, mp.parent / "Resources", mp.parent.parent / "Resources"]
        candidates += [
            exe.parent,
            exe.parent.parent / "Resources",
            exe.parent.parent.parent / "Resources",
        ]
        for candidate in candidates:
            if _looks_like_resources(candidate):
                return candidate
        return Path(meipass) if meipass else exe.parent
    return Path(__file__).resolve().parent.parent.parent  # 项目根


def backend_dir() -> Path:
    """开发模式下的 backend/ 目录；打包后不存在，退回资源根。"""
    if is_frozen():
        return resource_dir()
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """可写数据根（纯计算，不创建）。优先级：STARPICK_DATA_DIR > 打包用户目录 > backend/。"""
    override = _data_dir_override()
    if override:
        return Path(override).expanduser()
    if is_frozen():
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / APP_NAME
        if sys.platform.startswith("win"):
            base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            return Path(base) / APP_NAME
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(base) / APP_NAME
    return backend_dir()


def logs_dir() -> Path:
    """日志目录：打包后（或显式指定数据目录时）进数据目录；开发模式保持项目根 logs/。"""
    if is_frozen() or _data_dir_override():
        return data_dir() / "logs"
    return resource_dir() / "logs"


def default_db_path() -> Path:
    return data_dir() / "history.db"


def backup_dir() -> Path:
    """备份目录：开发模式保持 backend/data/backup（历史行为）。"""
    if is_frozen() or _data_dir_override():
        return data_dir() / "backup"
    return backend_dir() / "data" / "backup"


def runtime_token_path() -> Path:
    """Token 文件：打包后随数据目录；开发模式保持 backend/.runtime_token。"""
    if is_frozen():
        return data_dir() / ".runtime_token"
    return backend_dir() / ".runtime_token"


def env_file_path() -> Path:
    """.env：打包后随数据目录（用户可放 GITHUB_TOKEN）；开发模式保持 backend/.env。"""
    if is_frozen():
        return data_dir() / ".env"
    return backend_dir() / ".env"


def config_path() -> Path:
    """生效配置路径：打包后为数据目录下 config.yaml；开发模式为 backend/config.yaml。"""
    if is_frozen():
        return data_dir() / "config.yaml"
    return backend_dir() / "config.yaml"


def resource_config_path() -> Path:
    """随包分发的默认 config.yaml（打包时放在资源根）。"""
    return resource_dir() / "config.yaml"


def default_static_dir() -> Path:
    """前端构建产物：打包后取资源目录里的 frontend_dist；开发模式为 frontend/dist。"""
    if is_frozen():
        return resource_dir() / "frontend_dist"
    return resource_dir() / "frontend" / "dist"


def ensure_user_config() -> Path | None:
    """打包模式首次运行：把随包默认 config.yaml 种到数据目录（已存在则不动）。

    返回新复制的目标路径；开发模式或无需复制时返回 None。
    """
    if not is_frozen():
        return None
    target = config_path()
    if target.exists():
        return None
    src = resource_config_path()
    if not src.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    return target
