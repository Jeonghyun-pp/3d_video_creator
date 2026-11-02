# 공개 체크아웃 검증

2026-11-03 기준. 아래 결과는 명시한 경로의 기능 검증이며, 팀 생산성이나 제작 시간 단축을 측정한 결과는 아니다. 설치와 실행 절차는 [SETUP.md](SETUP.md), 과거 제작 성과는 [BUILD_REPORT.md](BUILD_REPORT.md)를 참조한다.

## 실제 Blender 검증

macOS, Blender **5.2.2 LTS**(2026-09-15 빌드)에서 저장소 루트를 작업 디렉터리로 두고 가상환경 Python으로 실행했다. 설치한 가상환경을 `.venv`로 사용할 때의 명령은 다음과 같다.

```sh
.venv/bin/python tests/studio/workbench_shot_smoke.py
.venv/bin/python -m unittest tests.test_build_lock -v
```

`workbench_shot_smoke.py`는 exit 0으로 끝났으며, 실제 로컬 Blender의 빌드·프리뷰·재생성 경로에서 다음 8항목을 확인했다.

1. 편집 전 프리뷰가 빌드된 장면 상태를 보여 준다.
2. 경로로 지정한 카메라 고도와 동작 값을 변경하고 프리뷰한다.
3. 저장한 variant를 복원하면 shot 데이터가 돌아온다.
4. 존재하지 않는 joint 편집을 거부하고 데이터를 유지한다. 프리뷰 픽셀 차이도 기존 테스트 기준(회색조 평균 1.0 미만)을 만족한다.
5. 읽히지 않는 파라미터와 충돌하는 수동 카메라 키를 거부한다.
6. commit 재실행이 세션 상태와 일치한다. generator 이후에도 카메라 고도 40과 drive 값 720이 유지된다.
7. 장면 편집을 새 빌드로 commit한다.
8. 의도적으로 틀린 generator 이후 기대값은 `WORKBENCH_REPLAY_MISMATCH`로 거부한다.

이 검증은 임시 프로젝트를 생성·삭제한다. 테스트의 synthetic decision fixture는 제작 승인 기록이 아니며, 비공개 제작 프로젝트나 유료 서비스를 사용하지 않았다. 개발 과정에서 카메라·동작을 탐색한 뒤 선택한 상태를 확정할 때, 거부된 변경이 남지 않고 확정 빌드가 세션을 재현하는지를 확인한다. 최종 영상의 미학적 품질 판정까지 포함하지 않는다.

`tests.test_build_lock`는 **2개 통과**했다. 실제 자식 프로세스의 `fcntl` 잠금 가시성, 예약 버전의 구분, 실패한 빌드 예약 정리를 확인한다. Blender 명령은 의도적으로 실패하는 stub이므로 이 결과를 추가 Blender 통합 성공으로 세지 않는다.

## 공개 CI checks의 범위

[Host contracts 워크플로](../.github/workflows/host-checks.yml)는 Ubuntu/Python 3.12에서 다음 **67개** 검사를 실행한다. 최신 원격 실행 결과는 [Actions](https://github.com/Jeonghyun-pp/3d_video_creator/actions/workflows/host-checks.yml)에서 커밋별로 확인할 수 있다.

2026-11-03에는 공개 대상 파일만 복사한 새 디렉터리에서 Python 3.12 가상환경을 만들고 의존성을 설치했다. `pip check`, 최초 CI 대상 55개 검사(host 38 + sheet 10 + composite 7), 두 CLI 도움말이 모두 통과했다. 기존 가상환경이나 비공개 제작 파일을 재사용하지 않은 macOS 실행 결과이며, 원격 CI 결과와 구분한다.

첫 원격 실행에서는 승인 테스트 4개가 이미지 fixture 준비 단계에서 실패했다. 새 파일의 수정 시각이 요청 시각보다 늦지 않은 상황을 강제로 만들자 같은 실패가 재현되었다. FakeCodex가 생성 완료 시각을 명시하도록 수정하고 foundation 12개를 CI에 추가했다. 실제 이미지의 freshness 검사는 완화하지 않았고, 오래된 이미지를 명명한 이벤트를 계속 거절하는 회귀도 포함했다. 원격 실패 로그에는 파일 시각이 없어 해당 실행의 정확한 시각 차이를 단정하지 않는다.

```sh
python -m pip install -r requirements.txt
python -m unittest tests.test_shot_edit tests.test_timings tests.test_workbench tests.test_registry_sync tests.test_content_keys tests.test_read_limits -v
python -m studio --help
python -m studio2 --help
# FFmpeg/ffprobe 설치 후:
python -m unittest tests.test_studio2_foundation tests.test_studio2_sheet tests.test_studio2_video_composite -v
```

- Host 계약 38개: 편집·타이밍·replay·MCP 경계를 임시 fixture와 mock으로 검사한다.
- 이미지·프로젝트 foundation 12개: 생성·캐시·실패 처리·fixture 시각·프로젝트 초기화를 검사한다.
- 승인 시트 10개: 승인 대상과 생성 결과의 연결을 검사한다.
- 영상 합성 7개: FFmpeg와 로컬 합성 미디어로 영상 연결을 검사한다.

이 범위는 개발 중 계약 회귀와 로컬 미디어 연결을 점검하는 데 쓰인다. Blender, 유료 API, RIFE를 실행하는 검사가 아니며, CLI 도움말 두 명령은 67개 테스트 수에 포함하지 않는다.

## 검증 한계

검사 시점의 전체 테스트 파일 372개를 모두 실행한 결과가 아니다. 별도 Blender smoke 전체, RIFE 프레임 보간, CAD·클라우드의 종단 간 실행, 유료 모델의 가용성·품질 및 최종 릴의 시각·청취 품질은 이번 검증 범위에 포함하지 않는다. 선택적 도구가 없어 건너뛴 검사를 기능 성공으로 해석하지 않는다.
