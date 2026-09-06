# SRT 화이트보드 애니메이션 Skill

SRT 자막을 내러티브 순서에 따라 그려지는 화이트보드 손그림 영상으로 변환하는 Skill입니다. **영역별 마스크 오케스트레이션**과 **스트리밍 필기 드로잉**을 결합했습니다: 각 요소가 자막을 따라 순서대로 등장하고, 펜촉이 영역 안에서 연속적으로 선을 그린 뒤 점진적으로 채색하며, 최종적으로 MP4로 내보냅니다.

지식 해설, 스토리 내레이션, 강의 자막, 숏폼 대본 등을 따뜻한 미색 종이 배경의 손그림 애니메이션으로 만들기에 적합합니다.

## 예시

**장면: 원숭이 산의 바나나 쟁탈전** —— 자막의 내러티브 순서에 따라 바위산과 아기 원숭이, 바나나를 빼앗는 큰 원숭이, 구경하는 아이들을 차례로 그립니다.

![원숭이 산의 바나나 쟁탈전: SRT 화이트보드 애니메이션 데모](examples/scene-01-monkey-mountain-stream.gif)

원본 선화: [PNG 보기](examples/scene-01-monkey-mountain.png)

## 핵심 기능

- SRT 자막을 파싱하고 권장 시간(25–35초) 단위로 장면 분할
- 스토리보드와 삽화 전략을 먼저 출력하여 각 장면이 하나의 핵심 의미만 표현하도록 보장
- 화면 좌표가 아닌 자막 이벤트를 기준으로 요소의 의미적 드로잉 순서 구성
- `annotation.json`으로 영역, 타이밍, 자막 연결, 겹침 보호 영역 관리
- 각 영역은 연속적인 스트리밍 필기 방식: 먼저 `ink`로 선화를 그리고, `color`로 채색
- 브라우저 프리뷰 스튜디오에서 영역, 순서, 타이밍, 자막 연결 조정 지원
- 장면별 개별 렌더링과 다중 장면 병합으로 완성된 MP4 출력

## 작동 방식

이 Skill의 핵심은 "자막 주도, 단계별 확인"입니다. 각 단계가 끝날 때마다 확인을 기다려, 스토리보드·선화·주석이 확정되기 전에 렌더링 비용을 낭비하지 않습니다:

1. SRT를 파싱하여 스토리보드와 삽화 전략을 출력합니다.
2. 확인 후 통일된 스타일의 선화를 생성합니다.
3. 선화 확인 후, 자막과 원본 이미지를 바탕으로 주석을 생성하고 프리뷰 스튜디오에 로드합니다.
4. 주석 확인 후, 영역 분할·방향 검사 이미지를 생성합니다.
5. 프리뷰 스튜디오에서 영역, 내러티브 순서, 타이밍, 자막 연결을 조정하고 저장합니다.
6. 최종 주석 확인 후, 장면별로 MP4를 렌더링합니다.
7. 다중 장면 프로젝트는 각 장면의 결과물을 확인한 뒤 병합합니다.

## 비주얼 가이드라인

- 따뜻한 미색 종이 배경: `#F5EBD7` 권장
- 짙은 회색 스케치 선, 빨강·주황·파랑은 소량의 개념적 포인트로만 사용
- 미니멀한 손그림, 깨끗한 배경과 충분한 여백
- 장면 내 텍스트, 라벨, 사진 질감, 3D 효과, 복잡한 텍스처 사용 금지

## 설치 및 환경

Skill에는 독립적인 Python 가상 환경 준비 스크립트가 포함되어 있습니다. 처음 실행 시:

```bash
python scripts/prepare_env.py --check
python scripts/prepare_env.py
```

성공하면 첫 번째 명령이 `ENV_PY=<경로>`를 출력합니다. 이후 렌더링에는 이 인터프리터를 사용해 의존성 격리를 유지하세요.

## 프로젝트 에셋 구조

```text
assets/whiteboard/<프로젝트명>/
├── scene-01-<이름>.png
├── scene-01-<이름>.annotation.json
├── scene-01-<이름>-whiteboard.mp4
└── scene-01-<이름>-preview.mp4
```

이미지와 주석 파일은 반드시 같은 이름이어야 합니다. 예: `scene-01-demo.png` ↔ `scene-01-demo.annotation.json`

## 주석 포맷

각 요소는 원본 이미지의 정수 픽셀 좌표를 사용하며, `sequence`, `subtitle`, `narrativeRole`을 통해 자막 이벤트와 연결됩니다. 영역은 "장면 설정 → 핵심 인물/사물 → 동작 또는 변화 → 반응/결과" 순으로 정렬해야 합니다.

```json
{
  "sceneId": "scene-01",
  "canvas": { "width": 1672, "height": 941 },
  "storyBasis": "아기 원숭이가 원숭이 산에서 바나나를 들고 있고, 큰 원숭이가 바나나를 빼앗으며, 아이들이 옆에서 구경한다.",
  "sceneDurationMs": 9000,
  "elements": [
    {
      "id": "rockery",
      "label": "원숭이 산 배경",
      "sequence": 1,
      "narrativeRole": "이야기의 장면 설정",
      "subtitle": "아기 원숭이가 원숭이 산 꼭대기에 앉아 바나나를 들고 있다.",
      "type": "structure",
      "region": { "x": 20, "y": 120, "width": 540, "height": 780 },
      "reveal": {
        "direction": "top_to_bottom",
        "startMs": 300,
        "durationMs": 2600,
        "maskPaddingPx": 22,
        "protectedRegions": []
      },
      "handPath": { "start": [290, 130], "end": [290, 890], "easing": "easeInOut" }
    }
  ]
}
```

`direction`과 `handPath`는 프리뷰 스튜디오의 사각형 프록시 표시에만 사용됩니다. 최종 영상의 실제 필기 경로는 스트리밍 렌더러가 자동 생성합니다. 서로 겹치는 객체는 앞선 요소의 `protectedRegions`에 나중에 표시할 영역을 지정하여 이후 콘텐츠가 미리 노출되지 않도록 하세요.

## 자주 쓰는 명령어

자막 파싱 및 권장 스토리보드 생성:

```bash
python scripts/parse_srt.py <자막.srt> --target-sec 30 --min-sec 25 --max-sec 35
```

영역 검사 이미지 생성:

```bash
python scripts/render_annotation_preview.py <이미지 경로> <주석 경로> <검사 이미지 출력 경로>
```

`assets/preview.html`을 열고 "폴더 열기"로 장면 디렉터리를 로드하면 영역, 순서, 타이밍, 자막 연결을 편집할 수 있습니다.

단일 장면 렌더링:

```bash
<ENV_PY> scripts/render_stream_whiteboard.py <이미지 경로> <주석 경로> <출력.mp4> assets/drawing-hand.png \
  --ink-path grid --color-fill contour-wipe
```

다중 장면 병합:

```bash
<ENV_PY> scripts/merge_scenes.py --inputs 장면1.mp4 장면2.mp4 장면3.mp4 --output final.mp4
```

## 품질 체크리스트

- 첫 프레임은 미리 노출된 선이 없는 깨끗한 미색 종이 배경이어야 함
- `canvas`가 원본 이미지 크기와 일치하고, 모든 영역이 캔버스 안의 정수 픽셀 좌표여야 함
- `sequence`, `startMs`가 자막의 내러티브 순서와 일치해야 함
- 중간 프레임에서 아직 시작하지 않은 영역과 보호 영역이 미리 나타나지 않아야 함
- 펜촉이 현재 진행 중인 필기 궤적에 밀착해야 함. 선화가 선명한 경우 `--ink-path skeleton` 선택 가능
- 각 장면 종료 후 완성된 화면을 최소 0.5초 유지. 다중 장면 병합 순서는 자막 스토리보드와 일치해야 함

## 저장소 구성

```text
srt-whiteboard-animation/
├── SKILL.md                         # 전체 워크플로우와 제약 조건
├── assets/
│   ├── drawing-hand.png              # 손 이미지 에셋
│   ├── preview.html                  # 로컬 편집용 프리뷰 스튜디오
├── examples/                         # README 예시 에셋
├── scripts/
│   ├── parse_srt.py                  # 자막 파싱 및 스토리보드 제안
│   ├── render_annotation_preview.py  # 주석 검사 이미지
│   ├── render_stream_whiteboard.py   # 스트리밍 필기 MP4 렌더러
│   ├── merge_scenes.py               # 다중 장면 병합
│   └── prepare_env.py                # 의존성 환경 준비
└── agents/openai.yaml                # Codex 메타데이터
```

## 기여

Issue나 Pull Request를 환영합니다. 드로잉 로직과 관련된 변경 사항은 실제 자막, 주석, 결과물을 사용해 마스크 보호, 타이밍, 최종 화면을 검증해야 합니다.

## 라이선스

이 프로젝트는 MIT License로 공개되어 있습니다. 자세한 내용은 [LICENSE](LICENSE)를 참고하세요.

## 만든 사람

물고기 기르기를 좋아하는 아저씨 / AI Builder / AI 팀으로 1인 회사를 만들어가는 중.

더우인(抖音)·빌리빌리(B站)·위챗 공식계정: 江哥是老登啊
