#!/usr/bin/env python3
"""
SRT 파싱 + 스토리보드 제안

.srt 자막을 구조화된 자막 큐로 파싱하고, "장면당 25-35초 내레이션" 기준으로
자막을 묶어 장면을 나눈다. 각 장면의 시작/종료 시각, 전체 길이(→ sceneDurationMs),
텍스트를 함께 돌려준다.

용도: srt-whiteboard-animation 워크플로우 1단계의 입력 자료. 내러티브 이벤트를
읽어내고, 삽화 전략을 세우고, 각 이미지의 주석에 넣을 sceneDurationMs를 정한다.

사용법:
  python parse_srt.py <자막.srt> [--target-sec 30] [--min-sec 25] [--max-sec 35]

출력: JSON(stdout)
  cues    자막 큐: {index, startMs, endMs, durMs, text}
  scenes  제안 장면: {sceneIndex, startMs, endMs, sceneDurationMs, cueRange, text}
stderr에는 사람이 읽기 좋은 요약을 출력한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 밀리초는 없을 수 있다. 일부 도구는 00:00:03 --> 00:00:07 형태로 내보낸다.
_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})(?:[,.](\d{1,3}))?")


def _to_ms(h: str, m: str, s: str, ms: str | None) -> int:
    millis = int(ms.ljust(3, "0")) if ms else 0
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + millis


def parse_srt(text: str) -> list[dict]:
    """SRT 텍스트를 자막 큐 목록으로 파싱한다. 빈 줄, BOM, 쉼표/마침표 밀리초 구분자를 허용한다."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[dict] = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        # 타임코드가 있는 줄을 찾는다
        time_line_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if time_line_idx is None:
            continue
        times = _TIME.findall(lines[time_line_idx])
        if len(times) < 2:
            continue
        start = _to_ms(*times[0])
        end = _to_ms(*times[1])
        body = " ".join(lines[time_line_idx + 1:]).strip()
        cues.append({
            "index": len(cues) + 1,
            "startMs": start,
            "endMs": end,
            "durMs": max(0, end - start),
            "text": body,
        })
    return cues


def group_scenes(cues: list[dict], target_sec: float, min_sec: float, max_sec: float) -> list[dict]:
    """
    목표 길이에 맞춰 연속된 자막을 장면으로 묶는다. 누적 길이가 target에 가까워지면
    장면을 끊되, min보다 짧지 않고 max보다 길지 않게 한다(max를 넘으면 강제로 끊는다).
    """
    scenes: list[dict] = []
    bucket: list[dict] = []
    target_ms, min_ms, max_ms = target_sec * 1000, min_sec * 1000, max_sec * 1000

    def flush() -> None:
        if not bucket:
            return
        start = bucket[0]["startMs"]
        end = bucket[-1]["endMs"]
        scenes.append({
            "sceneIndex": len(scenes) + 1,
            "startMs": start,
            "endMs": end,
            "sceneDurationMs": max(0, end - start),
            "cueRange": [bucket[0]["index"], bucket[-1]["index"]],
            "text": " ".join(c["text"] for c in bucket).strip(),
        })
        bucket.clear()

    for cue in cues:
        # 이 큐를 현재 장면에 넣으면 max를 넘는 경우, 먼저 장면을 끊는다(초장문 장면 방지)
        if bucket:
            span_with = cue["endMs"] - bucket[0]["startMs"]
            if span_with > max_ms:
                flush()
        bucket.append(cue)
        span = bucket[-1]["endMs"] - bucket[0]["startMs"]
        # 목표에 도달했고 min보다 짧지 않으면 장면을 끊는다
        if span >= target_ms and span >= min_ms:
            flush()
    flush()
    return scenes


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="SRT 파싱 + 스토리보드 제안")
    p.add_argument("srt", help="자막 파일 경로 (.srt)")
    p.add_argument("--target-sec", type=float, default=30.0, help="장면당 목표 내레이션 초 (기본 30)")
    p.add_argument("--min-sec", type=float, default=25.0, help="장면당 최소 초 (기본 25)")
    p.add_argument("--max-sec", type=float, default=35.0, help="장면당 최대 초 (기본 35)")
    args = p.parse_args(argv)

    try:
        raw = Path(args.srt).read_text(encoding="utf-8-sig")
    except OSError as e:
        print(f"[err] 자막을 읽을 수 없습니다: {e}", file=sys.stderr)
        return 1

    cues = parse_srt(raw)
    if not cues:
        print("[err] 자막 큐를 하나도 파싱하지 못했습니다. SRT 형식을 확인하세요", file=sys.stderr)
        return 1
    scenes = group_scenes(cues, args.target_sec, args.min_sec, args.max_sec)

    total_ms = cues[-1]["endMs"] - cues[0]["startMs"]
    print(f"자막 큐: {len(cues)}개  전체 길이: {total_ms/1000:.1f}s  제안 장면: {len(scenes)}개",
          file=sys.stderr)
    for s in scenes:
        print(f"  장면{s['sceneIndex']:>2}  {s['startMs']/1000:6.1f}-{s['endMs']/1000:6.1f}s "
              f"({s['sceneDurationMs']/1000:4.1f}s, 자막{s['cueRange'][0]}-{s['cueRange'][1]}): "
              f"{s['text'][:40]}", file=sys.stderr)

    json.dump({"cues": cues, "scenes": scenes}, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
