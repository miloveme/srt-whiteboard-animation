#!/usr/bin/env python3
"""
텍스트 그리기 도우미 (Pillow)

플랫폼별로 한글·CJK 글리프가 있는 폰트를 찾아 주고, 화면 하단에 얹을 자막
이미지를 만든다. 자막 검사 이미지(render_annotation_preview.py)와 영상
렌더러(render_stream_whiteboard.py)가 같은 폰트 탐색을 공유한다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 한글이 있는 폰트를 먼저 찾고, 전부 없으면 Pillow 내장 비트맵 폰트로 물러난다
_FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/AppleGothic.ttf",
    # Windows
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def find_font(size: int) -> ImageFont.ImageFont:
    """쓸 수 있는 폰트를 크기에 맞춰 연다. 없으면 내장 폰트로 물러난다(예외를 던지지 않는다)."""
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


def _wrap(text: str, font, draw: ImageDraw.ImageDraw, max_width: int) -> list[str]:
    """어절 단위로 줄을 나눈다. 한 어절이 너무 길면 글자 단위로 자른다."""
    def width_of(s: str) -> int:
        box = draw.textbbox((0, 0), s, font=font)
        return box[2] - box[0]

    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if width_of(candidate) <= max_width or not line:
            line = candidate
            continue
        lines.append(line)
        line = word
        while width_of(line) > max_width and len(line) > 1:
            cut = len(line) - 1
            while cut > 1 and width_of(line[:cut]) > max_width:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    return lines


def render_subtitle(text: str, frame_w: int, frame_h: int,
                    paper_bgr: tuple[int, int, int],
                    ink_bgr: tuple[int, int, int] = (50, 54, 58)) -> tuple[np.ndarray, np.ndarray] | None:
    """
    자막 한 줄을 프레임 크기의 겹침 레이어로 만든다.

    글자 주위에 종이색 테두리를 둘러 그림 위에 놓여도 읽히게 하되, 상자를 두지
    않아 화이트보드 특유의 여백감을 해치지 않는다.
    반환값은 (BGR 이미지, 0~1 알파). 그릴 내용이 없으면 None.
    """
    text = (text or "").strip()
    if not text:
        return None

    size = max(14, int(round(frame_h * 0.055)))
    font = find_font(size)
    stroke = max(2, size // 11)
    margin_x = int(frame_w * 0.07)
    max_width = frame_w - margin_x * 2

    layer = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    lines = _wrap(text, font, draw, max_width)

    line_h = int(round(size * 1.35))
    bottom = frame_h - int(frame_h * 0.06)
    top = bottom - line_h * len(lines)
    ink_rgb = (ink_bgr[2], ink_bgr[1], ink_bgr[0], 255)
    paper_rgb = (paper_bgr[2], paper_bgr[1], paper_bgr[0], 235)

    for i, line in enumerate(lines):
        box = draw.textbbox((0, 0), line, font=font)
        x = (frame_w - (box[2] - box[0])) // 2 - box[0]
        draw.text((x, top + i * line_h), line, font=font, fill=ink_rgb,
                  stroke_width=stroke, stroke_fill=paper_rgb)

    rgba = np.array(layer)
    bgr = rgba[:, :, [2, 1, 0]].astype(np.uint8)
    alpha = (rgba[:, :, 3].astype(np.float32) / 255.0)[:, :, None]
    return bgr, alpha
