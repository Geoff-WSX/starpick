"""平台推断 / 系统要求提取 / 中文检测测试。"""
from app.requirements import (
    detect_zh_friendly,
    extract_system_requirements,
    infer_platforms,
    major_version_of,
)


def test_infer_platforms_from_assets():
    assets = ["Kazumi_windows_2.3.8.zip", "Kazumi_macos_2.3.8.dmg",
              "Kazumi_linux_2.3.8.deb", "Kazumi_android_2.3.8.apk"]
    platforms = infer_platforms(assets)
    assert "Windows" in platforms
    assert "macOS" in platforms
    assert "Linux" in platforms
    assert "Android" in platforms


def test_infer_platforms_dedup():
    assets = ["a.exe", "b.msi", "c.zip"]
    assert infer_platforms(assets) == ["Windows"]


def test_infer_platforms_fallback_readme():
    readme = "This project supports Windows and macOS and Linux."
    platforms = infer_platforms([], readme)
    assert "Windows" in platforms
    assert "macOS" in platforms
    assert "Linux" in platforms


def test_infer_platforms_empty():
    assert infer_platforms([]) == []


def test_extract_system_requirements_ram():
    readme = """
# App
System Requirements:
- 8GB RAM
- Windows 10
"""
    reqs = extract_system_requirements(readme)
    assert any("内存" in r and "8" in r for r in reqs)


def test_extract_system_requirements_python():
    readme = "Requires Python 3.10 or newer and Docker 24."
    reqs = extract_system_requirements(readme)
    assert any("Python" in r for r in reqs)
    assert any("Docker" in r for r in reqs)


def test_extract_system_requirements_none():
    readme = "A simple tool. Just clone and run."
    assert extract_system_requirements(readme) == []


def test_extract_system_requirements_none_readme_missing():
    assert extract_system_requirements(None) == []


def test_detect_zh_friendly_cjk():
    readme = "这是一个完全中文的项目说明文档，用来测试中文识别功能是否正常工作。" * 5
    assert detect_zh_friendly(readme) is True


def test_detect_zh_friendly_keyword():
    readme = "English readme with 中文 support noted."
    assert detect_zh_friendly(readme) is True


def test_detect_zh_friendly_english():
    readme = "This is a pure English README without any Chinese characters here."
    assert detect_zh_friendly(readme) is False


def test_major_version_of():
    assert major_version_of("v2.3.8") == "2"
    assert major_version_of("1.0") == "1"
    assert major_version_of("") == ""


def test_infer_platforms_app_targz_is_macos():
    """Tauri/Electron 的 `.app.tar.gz` 是 macOS 自动更新包，不能判成 Linux。

    实测 24 个含此类资产的项目里 11 个被错标为 Linux（clash-verge-rev、
    readest、nuclear 等纯 macOS 软件出现在「Linux」筛选下）。
    """
    assert infer_platforms(["Clash.Verge_2.5.8_aarch64.app.tar.gz"]) == ["macOS"]
    assert infer_platforms(["Yaagl.app.tar.gz"]) == ["macOS"]
    # 真正的 Linux 压缩包仍然判 Linux
    assert infer_platforms(["tool-1.0-linux-x64.tar.gz"]) == ["Linux"]
    # 常规 tar.gz 不受影响
    assert infer_platforms(["source-1.0.tar.gz"]) == ["Linux"]
