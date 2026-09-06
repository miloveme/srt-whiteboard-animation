#!/usr/bin/env python3
"""
다중 장면 병합: 각 장면의 화이트보드 애니메이션 MP4를 순서대로 이어 붙인다.

크기가 모두 같으면 시스템 ffmpeg로 무손실 병합한다(-c copy, 재인코딩 없음).
크기가 다르면 filter_complex로 등비 축소 + 여백 채움 후 병합한다. 크기가 다른
입력에 -c copy를 쓰면 재생이 깨진 파일이 만들어지므로 반드시 피해야 한다.
ffmpeg가 없으면 PyAV로 프레임 단위 재인코딩하며, 마찬가지로 여백을 채우고
타임스탬프를 다시 매긴다. 원본 장면 파일은 그대로 남는다.

사용법:
  <ENV_PY> merge_scenes.py --inputs a.mp4 b.mp4 c.mp4 --output final.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _probe_size(path: Path) -> tuple[int, int] | None:
    """ffprobe로 첫 번째 비디오 스트림의 가로/세로를 읽는다. 실패하면 None."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    res = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        return None
    try:
        stream = json.loads(res.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None


def _concat_list_file(inputs: list[Path]) -> Path:
    """ffmpeg concat 목록 파일을 쓴다. 경로의 작은따옴표는 concat 문법에 맞게 이스케이프한다."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in inputs:
            escaped = p.resolve().as_posix().replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")
        return Path(f.name)


def _ffmpeg_concat_copy(ffmpeg: str, inputs: list[Path], output: Path) -> bool:
    """무손실 병합. 모든 입력의 크기가 같을 때만 쓸 수 있다."""
    list_path = _concat_list_file(inputs)
    try:
        res = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", str(output)],
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            print(f"  ffmpeg 무손실 병합 완료: {output}")
            return True
        print(f"  [warn] ffmpeg -c copy 실패: {res.stderr.strip()[:200]}")
        return False
    finally:
        list_path.unlink(missing_ok=True)


def _ffmpeg_concat_scaled(ffmpeg: str, inputs: list[Path], output: Path,
                          width: int, height: int) -> bool:
    """크기가 다른 경우의 병합: 각 장면을 등비 축소하고 여백을 채워 같은 화면으로 맞춘 뒤 잇는다."""
    cmd: list[str] = [ffmpeg, "-y", "-loglevel", "error"]
    for p in inputs:
        cmd += ["-i", str(p)]
    chains = "".join(
        f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0xF6F1E3,setsar=1[v{i}];"
        for i in range(len(inputs))
    )
    joined = "".join(f"[v{i}]" for i in range(len(inputs)))
    cmd += [
        "-filter_complex", f"{chains}{joined}concat=n={len(inputs)}:v=1:a=0[out]",
        "-map", "[out]", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        str(output),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"  ffmpeg 여백 채움 병합 완료 ({width}x{height}): {output}")
        return True
    print(f"  [warn] ffmpeg 여백 채움 병합 실패: {res.stderr.strip()[:200]}")
    return False


def _letterbox(img, width: int, height: int):
    """등비 축소 + 가운데 정렬 + 종이색 여백. 크기가 다른 장면이 늘어나 찌그러지는 것을 막는다."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    if (w, h) == (width, height):
        return img
    scale = min(width / w, height / h)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.full((height, width, 3), (227, 241, 246), dtype=np.uint8)  # 렌더러 캔버스색 #F6F1E3의 BGR
    top, left = (height - new_h) // 2, (width - new_w) // 2
    canvas[top:top + new_h, left:left + new_w] = resized
    return canvas


def _pyav_concat(inputs: list[Path], output: Path) -> bool:
    """
    PyAV 대체 경로: 프레임 단위로 디코딩 → 첫 장면 크기에 맞춰 여백 채움 →
    PTS를 새로 매겨 인코딩한다. 각 장면의 PTS가 0에서 다시 시작하므로,
    다시 매기지 않으면 타임스탬프가 단조 증가하지 않아 프레임이 버려지거나
    재생 길이가 첫 장면만큼으로 줄어든다.
    """
    try:
        import av
    except ImportError:
        return False
    from fractions import Fraction

    with av.open(str(inputs[0])) as first:
        vs = first.streams.video[0]
        width, height = vs.codec_context.width, vs.codec_context.height
        rate = vs.average_rate or Fraction(30, 1)

    time_base = Fraction(1, int(round(float(rate))))
    out = av.open(str(output), mode="w")
    ostream = out.add_stream("h264", rate=rate)
    ostream.width, ostream.height = width, height
    ostream.pix_fmt = "yuv420p"
    ostream.time_base = time_base
    ostream.options = {"crf": "24", "preset": "medium"}

    index = 0
    try:
        for p in inputs:
            with av.open(str(p)) as cont:
                for frame in cont.decode(video=0):
                    if frame.width != width or frame.height != height:
                        try:
                            img = _letterbox(frame.to_ndarray(format="bgr24"), width, height)
                            frame = av.VideoFrame.from_ndarray(img, format="bgr24")
                        except ImportError:
                            frame = frame.reformat(width=width, height=height)
                    # 장면을 넘어가도 이어서 번호를 매겨 타임스탬프를 단조 증가시킨다
                    frame.pts = index
                    frame.time_base = time_base
                    index += 1
                    for pkt in ostream.encode(frame):
                        out.mux(pkt)
        for pkt in ostream.encode(None):
            out.mux(pkt)
    finally:
        out.close()
    print(f"  PyAV 병합 완료 ({width}x{height}, {index} 프레임): {output}")
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="여러 장면의 화이트보드 애니메이션 MP4를 순서대로 병합")
    p.add_argument("--inputs", nargs="+", required=True, help="재생 순서대로 나열한 MP4 목록")
    p.add_argument("--output", required=True, help="병합 결과 경로")
    args = p.parse_args(argv)

    inputs = [Path(x) for x in args.inputs]
    missing = [str(x) for x in inputs if not x.exists()]
    if missing:
        print(f"[err] 입력 파일이 없습니다: {', '.join(missing)}", file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None:
        sizes = [_probe_size(p) for p in inputs]
        known = [s for s in sizes if s is not None]
        uniform = len(known) == len(inputs) and len(set(known)) == 1
        if uniform and _ffmpeg_concat_copy(ffmpeg, inputs, output):
            print(f"OUTPUT={output.resolve()}")
            return 0
        if not uniform:
            print(f"  [warn] 장면 크기가 다르거나 확인할 수 없습니다 {sizes}. 여백 채움 병합으로 전환합니다")
        width, height = known[0] if known else (1080, 1920)
        if _ffmpeg_concat_scaled(ffmpeg, inputs, output, width, height):
            print(f"OUTPUT={output.resolve()}")
            return 0

    if _pyav_concat(inputs, output):
        print(f"OUTPUT={output.resolve()}")
        return 0
    print("[err] 병합 실패: 시스템에 ffmpeg가 없고 PyAV도 쓸 수 없습니다", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
