#!/usr/bin/env python3
"""
스트리밍 필기 애니메이션 - 환경 준비 스크립트

하는 일:
  1. skill 디렉터리 안에 격리된 Python 가상 환경을 만든다(이미 있으면 재사용)
  2. 실행에 필요한 서드파티 라이브러리를 import할 수 있는지 확인한다
  3. 빠진 라이브러리를 자동으로 설치한다
  4. 마지막 줄에 ENV_PY=<인터프리터 경로>를 출력해 상위 호출자가 캡처하게 한다

사용법:
  python prepare_env.py          # 환경 생성 + 의존성 보충, ENV_PY 출력
  python prepare_env.py --check  # 확인만. 빠진 것이 있으면 0이 아닌 코드로 종료
"""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

# skill 루트 = 이 스크립트에서 두 단계 위
SKILL_ROOT = Path(__file__).resolve().parent.parent
VENV_ROOT = SKILL_ROOT / ".venv"

# import 이름 -> pip 설치 이름
DEPS: dict[str, str] = {
    "cv2": "opencv-python",
    "numpy": "numpy",
    "av": "av",  # PyAV: pip만으로 되는 H.264 인코딩. 시스템 ffmpeg가 없어도 된다
    "PIL": "Pillow",  # render_annotation_preview.py가 영역 번호 검사 이미지를 그릴 때 쓴다
}


def interpreter_path() -> Path:
    """가상 환경 안의 python 실행 파일 위치(플랫폼 공통)."""
    if sys.platform.startswith("win"):
        return VENV_ROOT / "Scripts" / "python.exe"
    return VENV_ROOT / "bin" / "python"


def ensure_venv(check_only: bool) -> Path:
    py = interpreter_path()
    if VENV_ROOT.exists() and py.exists():
        print(f"[ok] 기존 가상 환경 재사용: {VENV_ROOT}")
        return py

    if check_only:
        print(f"[err] 가상 환경이 아직 없습니다: {VENV_ROOT}")
        sys.exit(1)

    print(f"[..] 가상 환경 생성: {VENV_ROOT}")
    venv.create(str(VENV_ROOT), with_pip=True)
    print("[ok] 가상 환경 준비 완료")
    return py


def can_import(py: Path, import_name: str) -> bool:
    probe = subprocess.run(
        [str(py), "-c", f"import {import_name}"],
        capture_output=True,
    )
    return probe.returncode == 0


def install(py: Path, packages: list[str]) -> bool:
    if not packages:
        return True
    print(f"[..] 의존성 설치: {', '.join(packages)}")
    res = subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", *packages],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"[err] 설치 실패:\n{res.stderr}")
        return False
    print("[ok] 의존성 설치 완료")
    return True


def main() -> None:
    check_only = "--check" in sys.argv

    py = ensure_venv(check_only)

    missing: list[str] = []
    for import_name, pip_name in DEPS.items():
        if can_import(py, import_name):
            print(f"[ok] {pip_name}")
        else:
            print(f"[miss] {pip_name}")
            missing.append(pip_name)

    if missing:
        if check_only:
            print(f"\n의존성 {len(missing)}개 없음: {', '.join(missing)}")
            sys.exit(1)
        if not install(py, missing):
            sys.exit(1)

    # 마지막 줄: 호출자가 캡처하기로 약속된 출력
    print(f"\nENV_PY={py}")


if __name__ == "__main__":
    main()
