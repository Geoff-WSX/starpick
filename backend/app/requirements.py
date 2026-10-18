"""二次标注：平台推断 / 系统要求提取 / 中文友好检测。

规则说明（启发式）：
- 平台推断：优先 release 资产扩展名映射，README 关键词兜底
- 系统要求：仅提取 README 中结构清晰的段落（内存/磁盘/GPU/运行时/Docker 等），
  匹配不到则返回空列表（遵循"无要求不标注"）
- 中文友好：CJK 字符占比 + 中文关键词双重判定，结果仅作排序加权
"""
from __future__ import annotations

import re

# 资产扩展名 → 平台（按优先级排列，识别到的第一个命中的平台）
_ASSET_PLATFORMS: list[tuple[str, str]] = [
    (r"\.(exe|msi|msix|zip)$", "Windows"),
    (r"\.dmg$|\.pkg$", "macOS"),
    (r"\.(apk|aab)$", "Android"),
    (r"\.ipa$", "iOS"),
    (r"\.(deb|rpm|AppImage|tar\.gz|tgz)$", "Linux"),
]

_README_PLATFORM_HINTS: list[tuple[str, str]] = [
    ("windows", "Windows"),
    ("macos|mac os|mac_os|darwin", "macOS"),
    ("linux", "Linux"),
    ("android", "Android"),
    ("ios|iphone|ipad", "iOS"),
    ("docker", "Docker"),
    ("web", "Web"),
]

_SYS_REQ_PATTERNS: list[tuple[str, str, str]] = [
    # (匹配正则, 标签, 提取正则)
    (r"内存|RAM|Memory", "内存", r"([0-9]+(?:\.[0-9]+)?\s*(?:GB|MB|GB\+|MB\+|GiB|MiB))"),
    (r"磁盘|Disk|存储空间|Storage", "磁盘空间", r"([0-9]+(?:\.[0-9]+)?\s*(?:GB|MB|GiB|MiB|TB))"),
    (r"显卡|GPU|图形", "GPU/显卡", r"([A-Za-z0-9][A-Za-z0-9\s\-]{1,30}(?:GPU|显卡|显存|VRAM))"),
    (r"显存|VRAM|Video Memory", "显存", r"([0-9]+(?:\.[0-9]+)?\s*(?:GB|MB)\s*(?:VRAM|显存)?)"),
    (r"Python\s*3", "Python 版本", r"(Python\s*3\.\d+)"),
    (r"Node\.?js|Node", "Node.js", r"(Node\.?js\s*(?:>=|≥|v)?\s*\d+(?:\.\d+)?)"),
    (r"Docker", "Docker", r"(Docker\s*(?:>=|≥)?\s*v?\d+(?:\.\d+)*)"),
    (r"操作系统|OS\s*要求|System Requirements|系统要求|Requirements", "系统", r"([A-Za-z0-9][^\n]{0,60}(?:Windows|macOS|Linux|系统|操作系统)[^\n]{0,60})"),
]

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ZH_KEYWORDS = (
    "中文", "简体中文", "繁体中文", "汉化", "简体",
    "zh-cn", "zh_CN", "中文说明", "中文文档",
    "chinese version", "chinese readme", "chinese documentation", "chinese 说明",
)


def infer_platforms(asset_names: list[str], readme_text: str | None = None) -> list[str]:
    """返回去重后的平台标签列表。优先资产扩展名，README 关键词兜底。"""
    platforms: list[str] = []
    for name in asset_names:
        for pattern, platform in _ASSET_PLATFORMS:
            if re.search(pattern, name, re.IGNORECASE):
                if platform not in platforms:
                    platforms.append(platform)
                break
    if not platforms and readme_text:
        for pattern, platform in _README_PLATFORM_HINTS:
            if re.search(pattern, readme_text, re.IGNORECASE):
                if platform not in platforms:
                    platforms.append(platform)
    return platforms


def extract_system_requirements(readme_text: str | None) -> list[str]:
    """从 README 提取系统/运行要求。无匹配返回空列表（无要求不标注）。"""
    if not readme_text:
        return []
    text = readme_text[:20000]  # 只扫前 2 万字符，控制成本
    found: list[str] = []
    for pattern, label, extract in _SYS_REQ_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            m = re.search(extract, text, re.IGNORECASE)
            if m:
                item = m.group(1).strip()
                if item and item not in found:
                    found.append(f"{label}: {item}")
    return found[:6]  # 单项目最多标注 6 条，避免卡片过载


def detect_zh_friendly(readme_text: str | None, description: str = "") -> bool:
    """中文友好判定：CJK 占比 > 0.5% 或出现中文关键词。启发式，允许误判。"""
    if readme_text:
        sample = readme_text[:20000]
        if sample:
            cjk_count = len(_CJK_RE.findall(sample))
            if cjk_count / max(len(sample), 1) > 0.005:
                return True
    text = (description or "") + (readme_text or "")[:20000]
    return any(kw.lower() in text.lower() for kw in _ZH_KEYWORDS)


def major_version_of(tag_name: str) -> str:
    """从 tag 提取 major 版本（如 v2.3.8 → 2），用于"重大更新再推荐"判断。"""
    m = re.search(r"(\d+)(?:\.|$)", tag_name or "")
    return m.group(1) if m else ""
