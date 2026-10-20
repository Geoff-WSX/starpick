"""生成 STARPICK 品牌图标：packaging/icon.icns（macOS）+ packaging/icon.ico（Windows）。

图形沿用前端 favicon 的品牌元素：深空蓝黑底 + 星橙五角星 + 圆弧托手，
另加星点与星辉，适配 Dock / 任务栏的深浅环境。
配色与前端主题变量一致（style.css：--bg #0a0f1e / --accent #f45d22 / --text #e8ecf5）。

运行（macOS，需 Pillow）：
    packaging/.venv-build/bin/python packaging/make_icons.py
Windows 上运行只产出 .ico（icns 仅 macOS 需要，用 iconutil）。
"""
from __future__ import annotations

import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent

BG_TOP = (10, 15, 30)        # #0a0f1e 深空蓝黑
BG_BOTTOM = (26, 36, 62)     # #1a243e 稍亮，做竖向渐变
STAR = (244, 93, 34)         # #f45d22 星橙
STAR_GLOW = (255, 138, 77)   # #ff8a4d 星辉
ARC = (147, 161, 189)        # #93a1bd 灰蓝托手
DOT = (232, 236, 245)        # #e8ecf5 星点

S = 1024                     # 主画布
MARGIN = 80                  # 圆角矩形留白（macOS 图标惯例）
RADIUS = 208                 # 圆角半径 ≈ 20%（对齐 Apple 圆角比例）


def _rounded_mask() -> Image.Image:
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [MARGIN, MARGIN, S - MARGIN, S - MARGIN], RADIUS, fill=255)
    return mask


def _gradient() -> Image.Image:
    strip = Image.new("RGB", (1, S))
    px = strip.load()
    for y in range(S):
        t = y / (S - 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
    return strip.resize((S, S))


def _star_points(cx: float, cy: float, outer: float, inner_ratio: float = 0.42,
                 rotation: float = -90.0) -> list[tuple[float, float]]:
    """五角星顶点（rotation=-90° 尖朝上；inner_ratio 与 favicon 的"胖星"一致）。"""
    pts = []
    for i in range(10):
        r = outer if i % 2 == 0 else outer * inner_ratio
        a = math.radians(rotation + i * 36)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def render_master() -> Image.Image:
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = _rounded_mask()
    base.paste(_gradient().convert("RGBA"), (0, 0), mask)

    # 星点：确定性随机，撒在上半部，透明度错落
    rng = random.Random(20261016)
    dots = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots)
    for _ in range(46):
        x = rng.uniform(MARGIN + 60, S - MARGIN - 60)
        y = rng.uniform(MARGIN + 40, S * 0.62)
        r = rng.uniform(1.6, 4.6)
        alpha = rng.randint(50, 170)
        dd.ellipse([x - r, y - r, x + r, y + r], fill=DOT + (alpha,))
    base.alpha_composite(Image.composite(dots, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    star_pts = _star_points(512, 396, 232)

    # 星辉：星形模糊放大
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(_star_points(512, 396, 262), fill=STAR_GLOW + (150,))
    glow = glow.filter(ImageFilter.GaussianBlur(64))
    base.alpha_composite(Image.composite(glow, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    # 圆弧托手（下方半圆，开口朝上，像掌托着星；细而收敛，不抢主星）
    arc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(arc).arc(
        [512 - 244, 626 - 244, 512 + 244, 626 + 244],
        start=0, end=180, fill=ARC + (225,), width=64)
    base.alpha_composite(Image.composite(arc, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    # 主星（清晰边缘，压在辉光与圆点之上）
    star = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(star).polygon(star_pts, fill=STAR + (255,))
    base.alpha_composite(Image.composite(star, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
    return base


ICONSET_GROUPS = {
    16: ["icon_16x16.png"],
    32: ["icon_16x16@2x.png", "icon_32x32.png"],
    64: ["icon_32x32@2x.png"],
    128: ["icon_128x128.png"],
    256: ["icon_128x128@2x.png", "icon_256x256.png"],
    512: ["icon_256x256@2x.png", "icon_512x512.png"],
    1024: ["icon_512x512@2x.png"],
}


def main() -> int:
    master = render_master()
    master.save(OUT / "icon-preview.png")
    master.resize((128, 128), Image.LANCZOS).save(OUT / "icon-preview-128.png")

    # Windows .ico（多尺寸内嵌）
    master.resize((256, 256), Image.LANCZOS).save(
        OUT / "icon.ico",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)],
    )
    print("ok icon.ico")

    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as td:
            iconset = Path(td) / "icon.iconset"
            iconset.mkdir()
            for size, names in ICONSET_GROUPS.items():
                img = master.resize((size, size), Image.LANCZOS)
                for name in names:
                    img.save(iconset / name)
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "icon.icns")],
                check=True,
            )
        print("ok icon.icns")
    else:
        print("skip icon.icns（仅 macOS 需要）")
    print("ok icon-preview.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
