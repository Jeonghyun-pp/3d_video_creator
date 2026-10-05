# AI Technical Visualization Studio — Starter Pack

## 목표
건축에만 묶이지 않고, "평소 보이지 않는 구조와 시스템을 3D로 해부해 설명하는 숏폼 계정"을 만든다.

초기 포지셔닝:
- 건축/도시 40%
- 산업/인프라 30%
- 첨단기술/제품 30%

콘텐츠 문법:
Exterior → Cutaway → Exploded View → Flow/Mechanism → Key takeaway

비즈니스 모델:
Media → Portfolio → B2B Technical Storytelling Studio

즉, 조회수와 팔로워만 목표로 하지 않는다.
계정 자체를 제조·로봇·반도체·모빌리티·건설·인프라 기업의 기술 설명 영상 포트폴리오로 만든다.

---

## 현재 상태 — 레퍼런스급 릴 엔진 (2026-10-05)

`studio` CLI + Blender 5.2 + Astra 스킬(`.agents/skills/reel-production/`)로 건축 해부도 릴을 처음부터 만든다.
Blender는 구조·위치·카메라·글자·치수를, 생성모델은 질감·분위기를 맡고, 사람이 생성 전 추가 요청과 테이크 선택을 한다.

- 지나온 과정, 현재 지표, 남은 문제: **[docs/ENGINE_PROGRESS.md](docs/ENGINE_PROGRESS.md)**
- 단계별 측정값과 검증: [docs/BUILD_REPORT.md](docs/BUILD_REPORT.md) · 사용법: [docs/USING_THE_AGENT.md](docs/USING_THE_AGENT.md)
- 최근(F0–F11, F9): 지평선 고정과 하늘 확보, 2D 타이틀, 교차로·신호·가로수·보행자 거리 키트, 리빌 이음선 수정,
  화면 공간 화살표, 지면 단면(`section_push` + `section.stage`), 카메라 전용 하늘과 구간별 노출.
- 남은 우선순위: ① 역 내부 채우기(역사 키트) ② 단면 앞 머무름(`move.dwell`) ③ 창문·하늘 색 보정 ④ 거리 질감은 생성모델 프롬프트로.

새 컴퓨터: [docs/SETUP.md](docs/SETUP.md) — `scripts/bootstrap.sh` 한 번이면 같은 장면이 빌드된다. 에이전트 진입점은 [AGENTS.md](AGENTS.md).

```sh
scripts/bootstrap.sh
.venv/bin/python -m studio project from-example --example samsung_cutaway --project projects/samsung_cutaway
.venv/bin/python -m studio --help
.venv/bin/python -m unittest discover tests      # 단위 테스트
.venv/bin/python tests/run_smokes.py             # Blender 스모크 전체
```

---

## 권장 기술 스택

### 리서치/기획
- ChatGPT / Claude
- Google / 공식 자료 / 특허 / 기업 자료

### 3D
- Blender
- Blender Python
- BlenderKit / Sketchfab / CGTrader
- 보조 모델 생성: Meshy / Tripo 등

### AI Image / Video
- Midjourney / FLUX / Runway Image
- Runway / Veo 계열 Image-to-Video

### Voice / Edit
- ElevenLabs
- CapCut 또는 Premiere

### 자동화 / 개발
- Codex
- Python
- ffmpeg
- JSON 기반 scene specification

---

## Codex의 역할

Codex는 다음을 담당한다.

1. 프로젝트 폴더 구조 유지
2. scene.json 스키마 관리
3. Blender Python 스크립트 작성
4. 카메라/조명/재질/키프레임 자동화
5. 렌더링 batch 작업
6. ffmpeg 기반 영상 조합
7. 자막 및 narration timing 파일 생성
8. 반복 작업을 CLI로 자동화
9. 결과물 검증
10. 작업 로그와 실패 원인 기록

Codex에게 "멋진 영상을 알아서 만들어"라고 시키지 않는다.
항상 정보 구조와 장면 명세를 먼저 고정한다.

---

## 초기 운영 전략

### Phase 1 — 포맷 검증
목표: 10편

AI Image → Image-to-Video → Voice → CapCut

Blender를 억지로 쓰지 않는다.
어떤 주제/훅/시각 문법이 반응하는지 먼저 확인한다.

### Phase 2 — 포맷 고정
목표: 추가 10~20편

반응 좋은 콘텐츠 유형을 기준으로 Blender template을 만든다.

우선 자동화:
- Isometric camera
- Cutaway
- Exploded view
- Labels
- Camera dolly
- Basic flow animation
- Render presets

### Phase 3 — Studio화
목표:
- CAD/제품 자료 입력
- storyboard 자동 생성
- Blender scene 자동 구성
- technical short 제작
- B2B 의뢰 대응

---

## 콘텐츠 선정 기준

좋은 소재는 아래 조건을 많이 만족해야 한다.

1. 대부분 본 적이 있다.
2. 내부가 어떻게 생겼는지는 잘 모른다.
3. 단면으로 보여주면 바로 이해된다.
4. 30초 안에 하나의 핵심 원리를 설명할 수 있다.
5. 기업 고객과 연결될 수 있다.

예시:
- 엘리베이터 아래에는 뭐가 있을까?
- 지하철역은 지하에서 어떻게 생겼을까?
- 데이터센터 안에서 데이터는 어떻게 움직일까?
- 자동화 물류센터에서는 택배가 어떻게 이동할까?
- 전기차 배터리팩 안에는 뭐가 있을까?
- 로봇팔 내부에서는 힘이 어떻게 전달될까?
- 반도체 팹에서는 웨이퍼가 어떻게 이동할까?

---

## 핵심 제작 원칙

### 1. 구조 정확성 > 영상 화려함
설명 콘텐츠이므로 geometry consistency가 중요하다.

### 2. Text-to-Video보다 Image-to-Video
기준 이미지를 고정하고 AI는 motion/texture에 사용한다.

### 3. 한 영상 = 핵심 메시지 1개
정보를 많이 넣지 않는다.

### 4. 첫 프레임에 답의 일부를 보여준다
궁금증을 만든다고 3초 동안 외관만 보여주지 않는다.

### 5. AI가 구조를 결정하지 않게 한다
구조는 human-reviewed source of truth에서 정의한다.

---

## 기본 제작 흐름

1. topic.md 작성
2. sources.md 작성
3. script.md 작성
4. scene.json 생성
5. Blender 또는 AI image로 keyframe 생성
6. Image-to-Video
7. narration 생성
8. edit
9. QA
10. publish
11. performance.csv에 결과 기록
12. 다음 콘텐츠에 학습 반영

---

## 실행 가능한 영상 제작 파이프라인

현재 구현은 `scripts/video_pipeline.py` 하나로 프로젝트 생성, 명세 검증, Blender 렌더, 자막 입히기, 영상·음성 조립, 최종 파일 검사를 실행한다. 생성된 모든 MP4에는 SHA-256과 출력 정보가 담긴 manifest가 붙는다. 같은 장면의 입력이 바뀌지 않으면 재렌더하지 않는다.

### 설치

Python 3.11 이상, Blender, FFmpeg, ffprobe, 한국어 글꼴이 필요하다. macOS 예시:

```sh
brew install --cask blender
brew install ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/video_pipeline.py doctor
```

macOS Blender가 실행되지 않거나 헤드리스 렌더가 불안정한 환경에서는 Docker 렌더러를 사용할 수 있다. Docker가 실행 중이어야 한다.

```sh
docker build -q -t technical-studio-blender:4.0 -f Dockerfile.blender .
export STUDIO_BLENDER="$PWD/scripts/blender_container.sh"
.venv/bin/python scripts/video_pipeline.py doctor
```

컨테이너는 소프트웨어 EEVEE 렌더를 사용한다. 빠른 구조 확인이 필요하면 `STUDIO_RENDER_ENGINE=BLENDER_WORKBENCH`로 실행할 수 있다.

### 프로젝트와 입력 파일

```sh
.venv/bin/python scripts/video_pipeline.py new elevator
.venv/bin/python scripts/video_pipeline.py validate projects/elevator
```

`projects/<slug>/blender/source.blend`에는 `scene.json`의 객체 ID와 이름이 같은 Blender collection이 있어야 한다. 각 장면은 기본적으로 이 파일에서 렌더한다. 기존 이미지나 영상으로 컷을 만들려면 해당 장면에 `"source": "image"` 또는 `"source": "video"`와 프로젝트 내부의 `"media": "assets/file.png"` 경로를 추가한다. 영상 입력은 그 장면 길이 이상이어야 한다.

`scene.json`, `source.blend`, 원본 이미지·음성은 다시 제작할 수 있도록 버전 관리 또는 별도 백업에 보관한다. `renders/`와 `final/`은 재생성 가능한 출력이다.

`scene.json`은 [scene.schema.json](templates/scene.schema.json)을 따르며, 장면은 공백이나 겹침 없이 전체 길이를 채워야 한다. `sources.md`에 공식 자료를 기록하고, 내레이션을 확정한 뒤 `audio/narration.wav` 또는 각 장면 ID와 이름이 같은 `audio/scene_01.wav` 등을 모두 넣는다. 장면별 음성은 장면 길이보다 길면 오류로 처리하며, 짧으면 침묵을 채워 정확한 길이로 결합한다. 최종본 생성 전에는 검토자가 아래 파일을 작성한다.

```json
{"facts_approved": true, "script_approved": true}
```

위 내용은 `review.json`에 저장한다. 최종본 생성기는 출처 URL, 승인, 음성 파일이 없으면 중단한다. **이 승인은 자료를 검토한 사람이 해야 한다.**

### 렌더와 영상 조립

```sh
.venv/bin/python scripts/video_pipeline.py build projects/elevator --preview
.venv/bin/python scripts/video_pipeline.py build projects/elevator --review
.venv/bin/python scripts/video_pipeline.py build projects/elevator
```

빠른 미리보기는 360×640·12fps로 `final/preview.mp4`에 저장된다. 움직임과 선명도 판정용 검토본은 720×1280·`scene.json` fps로 `final/review.mp4`에 저장된다. 둘 다 미승인 표시가 들어가고 음성이 없으면 무음 트랙을 만든다. 최종본은 1080×1920·`scene.json` fps로 `final/master.mp4`에 저장된다. 각 결과에는 contact sheet, `subtitles/narration.srt`, `final/*.manifest.json`, 작업별 로그가 남는다. 장면을 강제로 다시 만들려면 `--force`를 붙인다.

최종본을 다시 만들었을 때 파일 내용이 바뀌면 이전 `master.mp4`와 manifest를 `final/history/`에 보관한다.

예제 지하철역의 **설명용 3D 모델**은 다음 명령으로 만든다. 실제 역 설계 자료를 검증한 모델이 아니므로 게시용으로 사용하지 않는다.

```sh
.venv/bin/python scripts/video_pipeline.py prepare-example
.venv/bin/python scripts/video_pipeline.py build projects/example_subway_station --preview
```

예제 모델이 이미 있으면 `prepare-example`은 덮어쓰지 않는다. 다시 만들 때만 `--force`를 붙인다.

### 검증

```sh
.venv/bin/python -m unittest discover -s tests -v
```

테스트는 잘못된 장면 시간·객체·파일 경로 차단, 미승인 최종본 차단, 이미지 컷에서 자막·영상·오디오 조립과 재실행을 확인한다. 최종 시각 품질, 구조 정확성, 게시 여부는 사람이 검토한다.

DDP 레퍼런스 기반 대표 샷 실험과 네 작업 역할의 통과 기준은 [DDP 프로젝트 계획](projects/ddp_reference_pilot/EXECUTION_PLAN.md), 재생·재검증 명령은 [DDP 프로젝트 README](projects/ddp_reference_pilot/README.md)에 있다. 이 실험에는 이름으로 선택하는 Blender 카메라, 0.25초 프레임 비교, 자산 출처·승인 게이트가 포함된다. 현재 결과물은 내부 품질 검증용 프리뷰다.

여러 세션에서 DDP 작업을 이어갈 때는 [DDP 세션 인계 문서](projects/ddp_reference_pilot/SESSION_HANDOFF.md)를 먼저 읽는다. 현재 상태, 파일 관계, 실패 원인과 병렬 작업 경계를 기록했다.

### 이 작업 환경에서 확인한 사항

- Docker Blender 4.0.2로 예제 `.blend` 생성과 EEVEE 장면 렌더를 실행했다.
- FFmpeg 8.1.2로 28초 예제 미리보기와 1080×1920, 30fps의 임시 최종 출력 검사를 통과했다.
- macOS Blender 5.2.2는 현재 정상 실행된다. Apple M4의 Cycles Metal 경로에서 1080×1920 단일 프레임 출력도 확인했다. 첫 Metal 실행의 준비 시간은 길 수 있다.
- 2026-10-04: 컷별 route(blender/generative/hybrid), 실사 룩 프리셋, 카메라 리그(chase/orbit/flythrough), CC0·CAD·image-to-3D 자산, 유료 생성 게이트가 추가됐다. 렌더 기본 장치는 Metal GPU다. 사용법은 `docs/USING_THE_AGENT.md`.
- 예제의 3D 구조와 합성 음성은 제작 경로 검증용이다. `review.json`은 승인되지 않은 상태이며, 예제의 게시용 `master.mp4` 생성은 차단된다.

### 무료 자산으로 만든 지하철 영상 파일럿

`projects/dynamic_station_pilot`에는 기존 개념 모델을 개선한 독립 파일럿이 있다. `scripts/upgrade_dynamic_station.py`는 장면에 Poly Haven CC0 PBR 재질, 열차 외장·구조 단면 디테일, 컷별 카메라와 Cycles Metal 설정을 적용한다. `scripts/finish_pbr_station.py`는 CC0 지하철 입구 사진과 3D 컷을 편집한다. 사용한 자산의 출처와 해시는 `assets/cc0/manifest.json`에 있다.

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background projects/dynamic_station_pilot/blender/source.blend --python scripts/upgrade_dynamic_station.py -- projects/dynamic_station_pilot/blender/pbr_cutaway.blend
mkdir -p projects/dynamic_station_pilot/renders/pbr_frames
/Applications/Blender.app/Contents/MacOS/Blender --background projects/dynamic_station_pilot/blender/pbr_cutaway.blend --python-expr "import bpy; p=bpy.context.preferences.addons['cycles'].preferences; p.compute_device_type='METAL'; p.get_devices(); s=bpy.context.scene; s.cycles.device='GPU'; s.cycles.samples=24; s.render.resolution_x=720; s.render.resolution_y=1280; s.frame_start=49; s.frame_end=192; s.render.filepath='projects/dynamic_station_pilot/renders/pbr_frames/frame_######'; bpy.ops.render.render(animation=True)"
.venv/bin/python scripts/finish_pbr_station.py
```

두 번째 명령은 `renders/pbr_frames/frame_000049.png`부터 `frame_000192.png`까지 렌더된 뒤 실행한다. 산출물은 `final/pbr_pilot_720.mp4`와 1080×1920 업스케일본이다. 3D는 특정 실제 역의 설계도를 검증한 모델이 아니며, 첫 사진은 암스테르담 역을 촬영한 예시다. 유료 자산이나 외부 렌더 서비스는 사용하지 않았다.
