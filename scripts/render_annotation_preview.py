#!/usr/bin/env python3
"""
주석 검사 이미지: annotation.json의 영역, 번호, 방향 화살표를 선화 위에 그린다.

용도: 워크플로우 4단계 — 영역 분할이 내러티브 순서와 맞는지, 모든 영역이 캔버스
안에 있는지, 겹치는 대상이 protectedRegions로 보호되어 있는지 확인한다.

사용법:
  <ENV_PY> render_annotation_preview.py <이미지> <주석json> <검사 이미지 출력>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 플랫폼별 폰트 후보. CJK 글리프가 있는 폰트를 먼저 찾고, 없으면 Pillow 내장 비트맵 폰트로 물러난다
_FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/arial.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _load_font(size: int) -> ImageFont.ImageFont:
    """후보 목록에서 쓸 수 있는 폰트를 찾는다. 전부 없으면 내장 비트맵 폰트로 물러난다(예외를 던지지 않는다)."""
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _text_center(draw: ImageDraw.ImageDraw, text: str, font, cx: float, cy: float) -> tuple[float, float]:
    """text를 (cx, cy)에 가운데 정렬하는 좌상단 좌표. 비트맵 폰트는 anchor를 지원하지 않아 직접 계산한다."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return cx - (right - left) / 2 - left, cy - (bottom - top) / 2 - top


def _default_hand_path(region: dict, direction: str) -> dict:
    """주석에 handPath가 없을 때 direction을 보고 region에서 방향 화살표를 만든다(검사 이미지 전용)."""
    x, y = region["x"], region["y"]
    w, h = region["width"], region["height"]
    mid_x, mid_y = x + w / 2, y + h / 2
    routes = {
        "top_to_bottom": ((mid_x, y), (mid_x, y + h)),
        "bottom_to_top": ((mid_x, y + h), (mid_x, y)),
        "left_to_right": ((x, mid_y), (x + w, mid_y)),
        "right_to_left": ((x + w, mid_y), (x, mid_y)),
    }
    start, end = routes.get(direction, routes["top_to_bottom"])
    return {"start": list(start), "end": list(end)}


def main(image_path: str, annotation_path: str, output_path: str) -> None:
    image = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(28)
    small_font = _load_font(18)
    colors = [(38, 103, 255, 225), (255, 105, 92, 225), (41, 167, 102, 225), (181, 100, 255, 225)]

    data = json.loads(Path(annotation_path).read_text(encoding="utf-8"))

    # 주석 좌표계(canvas)와 실제 이미지 크기가 다르면 비율로 환산해 박스가 어긋나지 않게 한다
    canvas = data.get("canvas") or {}
    cw = canvas.get("width") or image.width
    ch = canvas.get("height") or image.height
    sx, sy = image.width / cw, image.height / ch
    if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6:
        print(f"[warn] canvas({cw}x{ch})와 이미지({image.width}x{image.height}) 크기가 달라 비율로 환산했습니다")

    for index, element in enumerate(data["elements"], start=1):
        region = element["region"]
        x, y = region["x"] * sx, region["y"] * sy
        right, bottom = x + region["width"] * sx, y + region["height"] * sy
        color = colors[(index - 1) % len(colors)]
        fill = (*color[:3], 24)
        draw.rounded_rectangle((x, y, right, bottom), radius=12, outline=color, width=4, fill=fill)
        draw.ellipse((x + 8, y + 8, x + 44, y + 44), fill=color)
        draw.text(_text_center(draw, str(index), small_font, x + 26, y + 26),
                  str(index), font=small_font, fill="white")

        direction = element.get("reveal", {}).get("direction", "top_to_bottom")
        label = f"{index}. {element.get('label', element.get('id', ''))}  {direction}"
        draw.rounded_rectangle((x + 52, y + 8, min(right - 8, x + 52 + len(label) * 19), y + 46),
                               radius=6, fill=(255, 255, 255, 225))
        draw.text((x + 60, y + 12), label, font=small_font, fill=color)

        hand_path = element.get("handPath") or _default_hand_path(region, direction)
        start = (hand_path["start"][0] * sx, hand_path["start"][1] * sy)
        end = (hand_path["end"][0] * sx, hand_path["end"][1] * sy)
        draw.line((start, end), fill=color, width=4)
        draw.polygon((end, (end[0] - 13, end[1] - 7), (end[0] - 13, end[1] + 7)), fill=color)

    result = Image.alpha_composite(image, overlay).convert("RGB")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path, quality=95)
    print(f"OUTPUT={Path(output_path).resolve()}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(2)
    main(*sys.argv[1:4])
