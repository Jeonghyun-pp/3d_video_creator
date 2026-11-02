이 문서는 초기 `scripts/video_pipeline.py` 경로를 설명합니다. 현재 개발 도구와 설치 진입점은 [README](../README.md)와 [SETUP](SETUP.md)입니다. 아래 명령은 저장소 루트에서 실행합니다.

# Legacy scene.json pipeline

초기 구현은 `scripts/video_pipeline.py` 하나로 프로젝트 생성, 명세 검증, Blender 렌더, 자막 입히기, 영상·음성 조립, 최종 파일 검사를 실행한다. 생성된 모든 MP4에는 SHA-256과 출력 정보가 담긴 manifest가 붙는다. 같은 장면의 입력이 바뀌지 않으면 재렌더하지 않는다.

### 설치

Python 3.12 이상, Blender, FFmpeg, ffprobe, 한국어 글꼴이 필요하다. macOS 예시:

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

`scene.json`은 [scene.schema.json](../templates/scene.schema.json)을 따르며, 장면은 공백이나 겹침 없이 전체 길이를 채워야 한다. `sources.md`에 공식 자료를 기록하고, 내레이션을 확정한 뒤 `audio/narration.wav` 또는 각 장면 ID와 이름이 같은 `audio/scene_01.wav` 등을 모두 넣는다. 장면별 음성은 장면 길이보다 길면 오류로 처리하며, 짧으면 침묵을 채워 정확한 길이로 결합한다. 최종본 생성 전에는 검토자가 아래 파일을 작성한다.

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
