#!/usr/bin/env python3
"""
SRT 화이트보드 애니메이션 - 통합 렌더러 (마스크 편성 + 스트리밍 필기)

선화 이미지 한 장과 같은 이름의 annotation.json을 화이트보드 손그림 영상으로
렌더링한다.
  - 편성은 마스크 모델을 따른다: 영역을 내러티브 순서대로 하나씩 그리고,
    각 영역은 자신의 허용 마스크(사각형 region − 이후 모든 영역 − 자신의
    protectedRegions) 안에서만 그릴 수 있다. 따라서 아직 차례가 오지 않은
    내용은 절대 미리 노출되지 않는다.
  - 그리는 방식은 스트리밍 모델을 따른다: 허용 마스크 안에서 펜이 골격/그리드
    경로를 따라 이동하며 연속으로 선을 긋고(ink 단계), 이어서 색을 입힌다
    (color 단계). 모든 영역이 하나의 영구 캔버스를 공유하므로 이미 그린 영역은
    화면에 그대로 남는다.

모든 드로잉 기본 연산은 stream_render.CanvasOps에서 가져온다. 전체 이미지를
그리는 렌더러도 같은 것을 쓴다. 이 모듈이 더하는 것은 영역별 편성과 장면
타임라인뿐이다. 마지막 줄에 OUTPUT=<경로>를 출력하므로 상위 호출자가 캡처할 수 있다.

사용법:
  <ENV_PY> render_stream_whiteboard.py <이미지> <주석.json> <출력.mp4> [손이미지.png]
  옵션은 --help 참고. --total-ms를 생략하면 주석의 sceneDurationMs를 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# 스트리밍 렌더러의 구성 요소를 그대로 재사용한다(같은 디렉터리)
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
import stream_render as sr  # noqa: E402
from text_render import render_subtitle  # noqa: E402

DEFAULT_HAND = _SCRIPT_DIR.parent / "assets" / "drawing-hand.png"

# 장면 끝에서 완성된 그림을 최소한 이만큼 보여준다.
TAIL_SECONDS = 0.5


# ──────────────────────────────────────────────────────────────
# 장면 타임라인
#
# 렌더러는 펜 한 자루이므로 영역은 반드시 순차적으로 그려진다.
# 프레임을 쓰면서 암묵적인 시계를 굴리는 대신 타임라인 전체를 미리 계획하는
# 이유는, 렌더링 길이를 sceneDurationMs와 정확히 일치시키기 위해서다.
# 장면이 쓸 수 있는 모든 프레임은 첫 프레임을 쓰기 전에 각 단계에 배정된다.
# ──────────────────────────────────────────────────────────────
@dataclass
class ElementSlot:
    """한 영역이 차지하는 타임라인 구간(단위: 프레임)."""

    element: dict
    later: list[dict] = field(repr=False)
    begin: int
    ink_frames: int
    color_frames: int

    @property
    def end(self) -> int:
        return self.begin + self.ink_frames + self.color_frames


@dataclass
class ScenePlan:
    slots: list[ElementSlot]
    total_frames: int
    scale: float          # 장면 예산에 맞추려고 적용한 시간 축소 배율
    honor_starts: bool    # 주석의 startMs를 그대로 쓸 수 있었는지


def order_elements(elements: list[dict]) -> list[dict]:
    """
    그리는 순서. 가림(occlusion) 순서이기도 하다.

    `startMs`보다 `sequence`가 우선한다. 프리뷰 스튜디오에서 모듈 순서를 바꾸면
    `sequence`(와 배열 순서)만 다시 쓰이고 `startMs`는 그대로 남기 때문에,
    여기서 `startMs`를 읽으면 사용자가 방금 승인한 순서를 조용히 무시하게 된다.
    `sequence`가 없는 요소가 하나라도 있으면 배열 순서로 물러난다.
    """
    indexed = list(enumerate(elements))
    if all(isinstance(e.get("sequence"), (int, float)) for e in elements):
        indexed.sort(key=lambda pair: (pair[1]["sequence"], pair[0]))
    return [element for _, element in indexed]


def _starts_are_consistent(ordered: list[dict]) -> bool:
    """그리는 순서를 따라 startMs가 단조 증가하는지, 즉 그대로 쓸 수 있는지."""
    starts = [e["reveal"]["startMs"] for e in ordered]
    return all(a <= b for a, b in zip(starts, starts[1:]))


def _lay_out(ordered: list[dict], cfg: sr.Config, scale: float,
             honor_starts: bool) -> tuple[list[ElementSlot], int]:
    """
    모든 영역을 프레임 축 위에 겹치지 않게 이어 붙인다.

    앞 영역이 아직 그리는 중이면 요청보다 늦게 시작할 수는 있지만 더 일찍
    시작하지는 않는다. 각 영역의 durationMs는 설정된 가중치에 따라 ink 단계와
    color 단계로 나뉜다.
    """
    fps = cfg.fps
    weight_sum = cfg.ink_weight + cfg.color_weight
    slots: list[ElementSlot] = []
    cursor = 0
    for idx, element in enumerate(ordered):
        reveal = element["reveal"]
        duration = max(2, round(reveal["durationMs"] * scale * fps / 1000))
        requested = round(reveal["startMs"] * fps / 1000) if honor_starts else 0
        begin = max(requested, cursor)
        ink_frames = max(1, round(duration * cfg.ink_weight / weight_sum))
        color_frames = max(1, duration - ink_frames)
        slots.append(ElementSlot(element, ordered[idx + 1:], begin, ink_frames, color_frames))
        cursor = begin + ink_frames + color_frames
    return slots, cursor


def plan_scene(elements: list[dict], cfg: sr.Config, total_ms: int) -> ScenePlan:
    """
    주석을 명시적인 프레임 예산으로 바꾼다.

    reveal 구간이 겹치는 영역들은 여기서 동시에 그려질 수 없으므로, 이어 붙이면
    장면 길이를 넘길 수 있다. 그럴 때는 뒤를 늘리는 대신 각 영역의 길이를 줄여
    예산에 맞춘다. sceneDurationMs는 자막 구간에서 오는 값이라, 한 장면이 길어지면
    합치고 난 뒤 그 뒤의 모든 장면이 내레이션과 어긋나기 때문이다.
    """
    ordered = order_elements(elements)
    honor_starts = _starts_are_consistent(ordered)
    target = max(0, round(total_ms * cfg.fps / 1000))
    tail = max(1, round(TAIL_SECONDS * cfg.fps))

    slots, cursor = _lay_out(ordered, cfg, 1.0, honor_starts)
    scale = 1.0
    if target > 0 and cursor + tail > target:
        low, high = 0.0, 1.0
        for _ in range(24):  # 예산 안에 들어가는 가장 큰 배율을 찾는다
            mid = (low + high) / 2
            _, fitted = _lay_out(ordered, cfg, mid, honor_starts)
            if fitted + tail <= target:
                low = mid
            else:
                high = mid
        if low > 0.0:
            scale = low
            slots, cursor = _lay_out(ordered, cfg, scale, honor_starts)

    return ScenePlan(slots, max(target, cursor + tail), scale, honor_starts)


# ──────────────────────────────────────────────────────────────
# 쓴 프레임 수를 세는 기록기
# ──────────────────────────────────────────────────────────────
class _CountingWriter:
    """
    cv2.VideoWriter를 감싸 쓴 프레임 수를 센다.

    모든 단계는 정해진 개수의 프레임을 내보내야 한다. 개수를 세어 두면 중간에
    빠져나온 단계(예: 빈 마스크)를 뒤에서 채울 수 있어, 영상이 조용히 짧아지는
    일을 막는다.
    """

    def __init__(self, writer: cv2.VideoWriter) -> None:
        self._writer = writer
        self.count = 0
        self.overlay: tuple[np.ndarray, np.ndarray] | None = None

    def write(self, frame: np.ndarray) -> None:
        if self.overlay is not None:
            text, alpha = self.overlay
            # 원본을 건드리지 않는다. _hold는 같은 스냅샷을 여러 번 넘긴다
            frame = (frame * (1.0 - alpha) + text * alpha).astype(np.uint8)
        self._writer.write(frame)
        self.count += 1

    def is_opened(self) -> bool:
        return self._writer.isOpened()

    def release(self) -> None:
        self._writer.release()


# ──────────────────────────────────────────────────────────────
# 공유 캔버스 위에 영역 단위로 스트리밍 필기
# ──────────────────────────────────────────────────────────────
def _scaled_rect(region: dict, sx: float, sy: float,
                 out_w: int, out_h: int) -> tuple[int, int, int, int]:
    """주석 캔버스 좌표 → 출력 픽셀 좌표. 프레임 밖은 잘라낸다."""
    x0 = max(0, min(out_w, int(round(region["x"] * sx))))
    y0 = max(0, min(out_h, int(round(region["y"] * sy))))
    x1 = max(0, min(out_w, int(round((region["x"] + region["width"]) * sx))))
    y1 = max(0, min(out_h, int(round((region["y"] + region["height"]) * sy))))
    return x0, y0, x1, y1


class RegionStreamRenderer(sr.CanvasOps):
    """장면 전체가 공유하는 상태를 들고, 영역을 하나씩 그린다."""

    def __init__(self, image_bgr: np.ndarray, annotation: dict, cfg: sr.Config,
                 hand_png: Path | None, bare_tip: bool, subtitles: bool = True) -> None:
        self.cfg = cfg
        self.ann = annotation
        self.subtitles = subtitles
        self.canvas_bgr = sr._hex_to_bgr(cfg.canvas_hex)

        h0, w0 = image_bgr.shape[:2]
        self.out_w, self.out_h = sr.compute_output_size(w0, h0, cfg)

        # 주석 캔버스 좌표를 렌더 프레임 좌표로 옮기는 배율
        self.sx = self.out_w / annotation["canvas"]["width"]
        self.sy = self.out_h / annotation["canvas"]["height"]

        self.color_img = cv2.resize(image_bgr, (self.out_w, self.out_h),
                                    interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(self.color_img, cv2.COLOR_BGR2GRAY)
        self.thresh_map = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 10
        )
        self.grid_blocks = sr._to_grid_blocks(self.thresh_map, cfg.grid_edge)
        self.active_all = sr._active_mask(self.thresh_map, cfg.grid_edge, cfg.ink_threshold)
        self.ink_pixels = self.thresh_map < cfg.ink_threshold
        self.ink_paint = np.repeat(self.thresh_map[:, :, None], 3, axis=2).astype(np.float32)

        # 원본 배경을 캔버스 색으로 덮어, 채색 단계에서 종이색이 튀지 않게 한다.
        if cfg.match_bg:
            self._match_original_background()

        # 영구 캔버스 한 장: 이미 그린 영역은 그대로 남는다
        self.drawn = np.empty((self.out_h, self.out_w, 3), dtype=np.float32)
        self.drawn[...] = self.canvas_bgr.astype(np.float32)

        self.tip: sr.TipOverlay | None = None
        if not bare_tip:
            hand = sr._load_hand(hand_png, cfg.target_hand_height) if hand_png else None
            anchor_x, anchor_y = cfg.tip_anchor_x, cfg.tip_anchor_y
            if hand is None:
                hand = sr._procedural_tip(cfg.target_hand_height)
                anchor_x, anchor_y = 0.5, 0.70
            self.tip = sr.TipOverlay(hand[0], hand[1],
                                     tip_anchor_x=anchor_x, tip_anchor_y=anchor_y)

    # ── 허용 마스크: 사각형 − 이후 영역 − protectedRegions ──
    def _allowed_mask(self, element: dict, later_elements: list[dict]) -> np.ndarray:
        mask = np.zeros((self.out_h, self.out_w), dtype=bool)
        x0, y0, x1, y1 = _scaled_rect(element["region"], self.sx, self.sy, self.out_w, self.out_h)
        mask[y0:y1, x0:x1] = True
        for later in later_elements:
            lx0, ly0, lx1, ly1 = _scaled_rect(later["region"], self.sx, self.sy,
                                              self.out_w, self.out_h)
            mask[ly0:ly1, lx0:lx1] = False
        for protected in element.get("reveal", {}).get("protectedRegions", []):
            px0, py0, px1, py1 = _scaled_rect(protected, self.sx, self.sy,
                                              self.out_w, self.out_h)
            mask[py0:py1, px0:px1] = False
        return mask

    # ── 영역 안에서의 펜 경로 ──
    def _grid_path(self, allowed: np.ndarray) -> list[tuple[int, int]]:
        """그리드 방식: 마스크 안의 잉크 셀을 묶어 하나의 순서 있는 경로로 만든다."""
        allowed_cell = sr._to_grid_blocks(allowed.astype(np.uint8),
                                          self.cfg.grid_edge).any(axis=(2, 3))
        active = self.active_all & allowed_cell
        if not active.any():
            return []
        return sr.flatten_streams(sr.cluster_ink_streams(active))

    def _ink_plan(self, allowed: np.ndarray):
        """
        한 영역의 펜 샘플을 만든다.

        (samples, pen_lifts, sample_cell, path)를 돌려준다. 골격 방식에서는 샘플이
        이미 픽셀 단위로 정확해 셀 단위 채움이 필요 없으므로 `path`가 비어 있다.
        """
        if self.cfg.ink_path_mode == "skeleton":
            strokes = sr.build_skeleton_strokes(self.ink_pixels & allowed, self.cfg)
            if strokes:
                samples: list[tuple[int, int]] = []
                pen_lifts: set[int] = set()
                for index, stroke in enumerate(strokes):
                    if index > 0:
                        pen_lifts.add(len(samples))  # 획과 획 사이에서 펜을 든다
                    samples.extend(stroke)
                return samples, pen_lifts, [], []

        path = self._grid_path(allowed)
        if not path:
            return [], set(), [], []
        samples, pen_lifts, sample_cell = self._build_stroke_samples(path)
        return samples, pen_lifts, sample_cell, path

    # ── ink 단계 ──
    def _lay_ink(self, writer: _CountingWriter, frames: int, samples, pen_lifts,
                 sample_cell, path, allowed: np.ndarray) -> None:
        """
        펜이 지나간 자리를 따라 원본 그림의 선을 드러낸다.

        그리드 방식에서는 펜이 지나가는 대로 `path`의 셀을 통째로 찍어 채워
        면이 있는 도형이 비지 않게 하고, 골격 방식에서는 `path`가 비어 있어
        선을 따라 드러내는 것만으로 충분하다.
        """
        if frames <= 0:
            return
        if not samples:
            self._hold(writer, frames, (self.out_w // 2, self.out_h // 2))
            return

        indices = sr.frame_progress_indices(len(samples), frames)
        pauses = self._pause_frame_indices(frames, len(path) or len(samples))
        cells_done = 0
        previous: int | None = None
        for frame_index, sample_index in enumerate(indices):
            if frame_index in pauses and previous is not None:
                writer.write(self._snapshot_with_tip(*samples[previous]))
                continue

            if previous is None:
                self._reveal_ink_segment(samples[sample_index], samples[sample_index], allowed)
            else:
                for step in range(previous + 1, sample_index + 1):
                    if step in pen_lifts:
                        continue
                    self._reveal_ink_segment(samples[step - 1], samples[step], allowed)

            if path:
                target_cell = sample_cell[sample_index]
                while cells_done <= target_cell and cells_done < len(path):
                    self._ink_stamp(path[cells_done], allowed)
                    cells_done += 1

            writer.write(self._snapshot_with_tip(*samples[sample_index]))
            previous = sample_index

        while cells_done < len(path):  # 반쯤 그리다 만 셀이 남지 않게 마무리
            self._ink_stamp(path[cells_done], allowed)
            cells_done += 1

    # ── color 단계: 경로를 따라 칠하거나, 윤곽을 인식해 쓸어내리거나 ──
    def _wash_brush(self, writer: _CountingWriter, frames: int,
                    centers: list[tuple[int, int]], allowed: np.ndarray) -> None:
        if frames <= 0:
            return
        if not centers:
            self._hold(writer, frames, (self.out_w // 2, self.out_h // 2))
            return

        disk = sr._feathered_disk(self.cfg.brush_radius)
        previous: int | None = None
        for center_index in sr.frame_progress_indices(len(centers), frames):
            start = center_index if previous is None else previous + 1
            for step in range(start, center_index + 1):
                self._color_stamp(*centers[step], disk, allowed)
            writer.write(self._snapshot_with_tip(*centers[center_index]))
            previous = center_index

    def _wash_contour(self, writer: _CountingWriter, frames: int, allowed: np.ndarray) -> None:
        """
        색을 위에서 아래로 쓸어내리되 윤곽선에서 붙잡아 둔다.

        저항장이 전진선을 뒤로 밀어내므로 색이 윤곽에서 잠시 머물다 넘어간다.
        곧게 쓸어내리는 대신 색이 선을 따라 번지는 것처럼 보이게 하는 장치다.
        """
        if frames <= 0:
            return
        cfg = self.cfg
        ys_all, xs_all = np.where(allowed)
        if ys_all.size == 0:
            # 완전히 가려진 영역: 그래도 이 프레임들은 이 영역 몫이므로 화면을 유지한다
            self._hold(writer, frames, (self.out_w // 2, self.out_h // 2))
            return

        top, bottom = int(ys_all.min()), int(ys_all.max())
        left, right = int(xs_all.min()), int(xs_all.max())
        region_h, region_w = bottom - top + 1, right - left + 1

        crop = (slice(top, bottom + 1), slice(left, right + 1))
        allowed_crop = allowed[crop]
        color_crop = self.color_img[crop].astype(np.float32)
        drawn_crop = self.drawn[crop]

        # 띠의 폭은 잘라낸 영역이 아니라 전체 캔버스를 기준으로 잡는다. 선 굵기는
        # 캔버스 단위의 상수이므로, 작은 영역이라고 더 거친 쓸어내림이 되면 안 된다.
        resistance = sr.build_resistance_field(
            (self.ink_pixels & allowed)[crop], cfg, self.out_w, self.out_h
        )
        wave = sr._build_wipe_wave(region_w)
        delay_px = int(np.clip(region_h * cfg.wipe_delay_ratio, 12, 52))
        rows = np.arange(region_h, dtype=np.float32)[:, None]
        sweep = region_h + 2 * delay_px
        lanes = max(1, cfg.wipe_blocks)

        for frame_index in range(frames):
            progress = 1.0 if frames == 1 else frame_index / (frames - 1)
            lead = sr._ease_in_out_sine(progress) * sweep - delay_px
            revealed = (rows <= lead + wave[None, :] - resistance * delay_px) & allowed_crop
            drawn_crop[revealed] = color_crop[revealed]

            # 펜은 색이 번지는 경계 위를 좌우로 오간다. 손으로 칠하는 모습처럼.
            lane = sr._ease_in_out_sine((frame_index / lanes * 2.0) % 1.0)
            forward = (int(frame_index // lanes) % 2 == 0)
            cursor_x = int(lane * region_w) if forward else int((1.0 - lane) * region_w)
            cursor_x = max(0, min(region_w - 1, cursor_x))
            column = np.where(revealed[:, cursor_x])[0]
            cursor_y = int(column[-1]) if column.size > 0 else 0
            writer.write(self._snapshot_with_tip(left + cursor_x, top + cursor_y))

        drawn_crop[allowed_crop] = color_crop[allowed_crop]  # 칠하다 만 곳을 남기지 않는다

    # ── 프레임 수 관리 ──
    def _hold(self, writer: _CountingWriter, frames: int, at: tuple[int, int]) -> None:
        """펜을 `at`에 세워 둔 채 현재 캔버스를 `frames`장 내보낸다."""
        if frames <= 0:
            return
        snapshot = self._snapshot_with_tip(*at)
        for _ in range(frames):
            writer.write(snapshot)

    def _hold_still(self, writer: _CountingWriter, until: int) -> None:
        """펜 없이 캔버스만으로 `until` 프레임까지 채운다."""
        if writer.count >= until:
            return
        snapshot = self.drawn.astype(np.uint8)
        while writer.count < until:
            writer.write(snapshot)

    def _subtitle_overlay(self, element: dict):
        """영역의 subtitle을 화면 하단 겹침 레이어로 만든다. 자막이 없으면 None."""
        if not self.subtitles:
            return None
        return render_subtitle(element.get("subtitle", ""), self.out_w, self.out_h,
                               tuple(int(c) for c in self.canvas_bgr))

    def _run_phase(self, writer: _CountingWriter, frames: int, draw) -> None:
        """
        한 단계를 실행하고, 정확히 `frames`장을 쓰게 만든다.

        중간에 빠져나온 단계를 그대로 두면 타임라인과 실제 파일이 어긋나고,
        그 뒤의 모든 영역이 그 오차를 그대로 물려받는다.
        """
        target = writer.count + frames
        draw()
        if writer.count < target:
            self._hold(writer, target - writer.count, (self.out_w // 2, self.out_h // 2))

    # ── 메인 렌더링 ──
    def render_to(self, raw_path: Path, plan: ScenePlan) -> Path:
        writer = _CountingWriter(cv2.VideoWriter(
            str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"),
            self.cfg.fps, (self.out_w, self.out_h),
        ))
        if not writer.is_opened():
            raise RuntimeError(f"비디오 라이터를 열 수 없습니다: {raw_path}")

        try:
            for slot in plan.slots:
                # 자막은 해당 영역을 그리기 시작할 때 바뀐다. 그리기 전 대기 구간에는
                # 이전 자막이 남아 있어, 문장이 끊기지 않고 다음 장면으로 이어진다.
                self._hold_still(writer, slot.begin)
                writer.overlay = self._subtitle_overlay(slot.element)
                allowed = self._allowed_mask(slot.element, slot.later)
                samples, pen_lifts, sample_cell, path = self._ink_plan(allowed)

                self._run_phase(writer, slot.ink_frames, lambda: self._lay_ink(
                    writer, slot.ink_frames, samples, pen_lifts, sample_cell, path, allowed))

                if self.cfg.color_fill == "contour-wipe":
                    self._run_phase(writer, slot.color_frames,
                                    lambda: self._wash_contour(writer, slot.color_frames, allowed))
                else:
                    centers = [self._cell_center(c) for c in path] if path else samples
                    self._run_phase(writer, slot.color_frames,
                                    lambda: self._wash_brush(writer, slot.color_frames,
                                                             centers, allowed))

            # 마무리: 완성된 그림을 그대로 보여준다
            self.drawn[...] = self.color_img.astype(np.float32)
            self._hold_still(writer, plan.total_frames)
        finally:
            writer.release()
        return raw_path


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────
def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="SRT 화이트보드 렌더러 (마스크 편성 + 스트리밍 필기)")
    p.add_argument("image", help="선화 이미지")
    p.add_argument("annotation", help="같은 이름의 annotation.json")
    p.add_argument("output", help="출력 MP4 경로")
    p.add_argument("hand", nargs="?", default=str(DEFAULT_HAND),
                   help="손 이미지 PNG (기본값: 내장 이미지)")
    p.add_argument("--total-ms", type=int, default=None,
                   help="장면 길이. 생략하면 주석의 sceneDurationMs를 쓴다")
    p.add_argument("--bare-tip", action="store_true", help="펜/손을 겹쳐 그리지 않는다")
    p.add_argument("--no-subtitles", dest="subtitles", action="store_false",
                   help="주석의 subtitle을 화면에 얹지 않는다(기본은 얹음)")
    p.add_argument("--ink-path", default="grid", choices=["grid", "skeleton"],
                   help="펜 경로: grid(기본) 또는 skeleton 골격 추적")
    p.add_argument("--color-fill", default="contour-wipe", choices=["contour-wipe", "brush"],
                   help="채색: contour-wipe(기본) 또는 경로를 따라 칠하는 brush")
    p.add_argument("--pause", default="heavy", choices=["heavy", "auto", "light", "off"],
                   help="ink 단계에서 펜이 얼마나 자주 멈추는지")
    p.add_argument("--fps", type=int, default=None)
    p.add_argument("--grid-edge", type=int, default=None)
    p.add_argument("--brush-radius", type=int, default=None)
    p.add_argument("--cap-long-edge", type=int, default=None,
                   help="긴 변의 픽셀 상한 (기본 1080)")
    p.add_argument("--wipe-decay", type=float, default=None,
                   help="윤곽이 색의 전진선을 붙잡는 정도 (기본 0.86)")
    p.add_argument("--wipe-delay-ratio", type=float, default=None,
                   help="윤곽에서 깎이는 영역 높이의 비율 (기본 0.04)")
    p.add_argument("--wipe-blocks", type=int, default=None,
                   help="contour-wipe에서 펜이 좌우로 오가는 횟수 (기본 18)")
    return p.parse_args(argv)


def _build_cfg(args) -> sr.Config:
    overrides = {
        "fps": args.fps,
        "grid_edge": args.grid_edge,
        "brush_radius": args.brush_radius,
        "cap_long_edge": args.cap_long_edge,
        "wipe_decay": args.wipe_decay,
        "wipe_delay_ratio": args.wipe_delay_ratio,
        "wipe_blocks": args.wipe_blocks,
    }
    settings = {k: v for k, v in overrides.items() if v is not None}
    settings["ink_path_mode"] = args.ink_path
    settings["color_fill"] = args.color_fill
    settings["pause_mode"] = args.pause
    return sr.Config(**settings)


def main(argv=None) -> int:
    args = _parse_args(argv)
    cfg = _build_cfg(args)

    print("=" * 56)
    print("SRT 화이트보드 렌더러 (마스크 편성 + 스트리밍 필기)")
    print("=" * 56)

    image_bgr = sr._imread_any(args.image)
    if image_bgr is None:
        print(f"[err] 이미지를 읽을 수 없습니다: {args.image}")
        return 1
    try:
        annotation = json.loads(Path(args.annotation).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[err] 주석을 읽을 수 없습니다: {e}")
        return 1
    if not annotation.get("elements"):
        print("[err] 주석에 elements가 없습니다")
        return 1

    total_ms = args.total_ms or annotation.get("sceneDurationMs")
    if not total_ms:
        last = max(e["reveal"]["startMs"] + e["reveal"]["durationMs"]
                   for e in annotation["elements"])
        total_ms = last + 1000

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_name(out_path.stem + "_raw.mp4")

    renderer = RegionStreamRenderer(image_bgr, annotation, cfg,
                                    Path(args.hand) if args.hand else None, args.bare_tip,
                                    subtitles=args.subtitles)
    plan = plan_scene(annotation["elements"], cfg, total_ms)

    print(f"  입력: {args.image}")
    print(f"  프레임: {renderer.out_w}x{renderer.out_h} @ {cfg.fps}fps")
    print(f"  영역 수: {len(plan.slots)}, 길이: {plan.total_frames / cfg.fps:.2f}s "
          f"(요청 {total_ms / 1000:.2f}s), "
          f"필기: {cfg.ink_path_mode}, 채색: {cfg.color_fill}, "
          f"자막: {'표시' if args.subtitles else '없음'}")
    if not plan.honor_starts:
        print("  [warn] startMs가 모듈 순서와 어긋나 영역을 이어 붙였습니다. "
              "프리뷰 스튜디오에서 장면을 다시 저장하면 시간이 갱신됩니다.")
    if plan.scale < 1.0:
        print(f"  [warn] reveal 구간이 겹쳐 각 영역 길이를 {plan.scale:.0%}로 줄였습니다. "
              "자막 구간과 장면 길이를 맞추기 위한 조정입니다.")

    renderer.render_to(raw_path, plan)
    final = sr.transcode_h264(raw_path, out_path)

    print(f"\n최종 영상: {final}  ({final.stat().st_size / (1024 * 1024):.2f} MB)")
    print("=" * 56)
    print(f"OUTPUT={final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
