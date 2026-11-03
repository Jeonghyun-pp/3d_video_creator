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

[Host contracts 워크플로](../.github/workflows/host-checks.yml)는 Ubuntu/Python 3.12에서 아래 기본 **67개** 검사와 remote bootstrap·유료 호출 정책·smoke skip 회귀 **43개**, 합계 **110개**를 실행한다. 추가 회귀는 임시 파일과 가짜 서비스만 사용하며 Blender나 유료 API를 실행하지 않는다. 최신 원격 실행 결과는 [Actions](https://github.com/Jeonghyun-pp/3d_video_creator/actions/workflows/host-checks.yml)에서 커밋별로 확인할 수 있다.

2026-11-03에는 공개 대상 파일만 복사한 새 디렉터리에서 Python 3.12 가상환경을 만들고 의존성을 설치했다. `pip check`, 최초 CI 대상 55개 검사(host 38 + sheet 10 + composite 7), 두 CLI 도움말이 모두 통과했다. 기존 가상환경이나 비공개 제작 파일을 재사용하지 않은 macOS 실행 결과이며, 원격 CI 결과와 구분한다.

첫 원격 실행에서는 승인 테스트 4개가 이미지 fixture 준비 단계에서 실패했다. 새 파일의 수정 시각이 요청 시각보다 늦지 않은 상황을 강제로 만들자 같은 실패가 재현되었다. FakeCodex가 생성 완료 시각을 명시하도록 수정하고 foundation 12개를 CI에 추가했다. 실제 이미지의 freshness 검사는 완화하지 않았고, 오래된 이미지를 명명한 이벤트를 계속 거절하는 회귀도 포함했다. 원격 실패 로그에는 파일 시각이 없어 해당 실행의 정확한 시각 차이를 단정하지 않는다.

```sh
python -m pip install -r requirements.txt
python -m unittest tests.test_shot_edit tests.test_timings tests.test_workbench tests.test_registry_sync tests.test_content_keys tests.test_read_limits -v
python -m studio --help
python -m studio2 --help
python -m unittest tests.test_remote_gpu tests.test_generative_fal tests.test_smoke_runner -v
# FFmpeg/ffprobe 설치 후:
python -m unittest tests.test_studio2_foundation tests.test_studio2_sheet tests.test_studio2_video_composite -v
```

- Host 계약 38개: 편집·타이밍·replay·MCP 경계를 임시 fixture와 mock으로 검사한다.
- 이미지·프로젝트 foundation 12개: 생성·캐시·실패 처리·fixture 시각·프로젝트 초기화를 검사한다.
- 승인 시트 10개: 승인 대상과 생성 결과의 연결을 검사한다.
- 영상 합성 7개: FFmpeg와 로컬 합성 미디어로 영상 연결을 검사한다.

이 범위는 개발 중 계약 회귀와 로컬 미디어 연결을 점검하는 데 쓰인다. Blender, 유료 API, RIFE를 실행하는 검사가 아니며, CLI 도움말 두 명령은 67개 테스트 수에 포함하지 않는다.

## 확대 로컬 검증 (2026-11-03)

공개 대상 파일을 임시 디렉터리로 복사하고 HOME·설정·데이터를 분리했다. 외부 네트워크와 모델 CLI 호출을 막고 검토한 로컬 도구만 허용했다. 최종 작업 파일과 검증 사본의 SHA-256을 대조했다. 아래 결과는 macOS 로컬 실행이며 위 Linux CI와 범위가 다르다.

| 검사 | 결과와 범위 |
|---|---|
| 전체 unittest discovery | Python 3.14에서 1,801개 중 1,796개 통과, skip 5개, 실패 0. skip은 NumPy 검사 4개와 격리 HOME 조건의 OS sandbox 검사 1개이며 별도 적합 환경에서 모두 통과 |
| Standalone smoke | 110개 중 실제 로컬 도구로 실행 가능한 106개 통과. 실제 계정·사용자 sealed engine이 필요한 4개는 미실행 |
| 원격 장애 모사 | 가짜 API·SSH·프레임 writer와 실제 Python worker로 13개 시나리오 통과. 12작업 공유 서버, 동시 작업, worker 강제 종료, 전송·부팅 실패 등을 검사. 실제 클라우드 검증은 아님 |
| Blender | 5.2.2 LTS에서 빌드·렌더·카메라·기구·물리·편집·replay 검사. Jet rig의 hash와 219개 프레임 샘플이 기존 공개 기준과 일치 |
| RIFE·영상 | 실제 로컬 보간 바이너리와 FFmpeg로 관련 18개 회귀 통과. 외부 생성 API는 가짜 응답 사용 |
| CAD | 별도 Python 3.12 환경에서 실제 기하·DXF 10개 통과. CAD→Blender smoke에서 17개 anchor 오차 0과 반복 생성 hash 일치 확인 |

이 수치는 서로 중복되는 검사 범위를 포함하므로 합산한 전체 검증 수로 사용하지 않는다. CLI 339개 명령·그룹의 help 구성도 확인했지만 모든 handler와 인자 조합의 실행을 뜻하지 않는다.

검증 중 새 원격 worker 업로드에 `studio2`와 모델 registry가 빠지는 결함을 고쳤다. 업로드한 파일만 있는 새 디렉터리에서 `_worker --help`를 실행하는 회귀를 추가했다. 유료 요청은 자격증명 접근 전에 승인·예산을 확인하도록 순서를 바로잡았다. 공개 fixture를 준비하고, smoke의 skip과 실제 성공을 분리했으며, 장애 테스트가 실제로 worker를 종료했는지도 확인하도록 강화했다.

## 남은 검증 한계

과거 간헐 실패였던 `blender_smoke`와 `sim_bake_smoke`는 각각 세 번 통과했지만 원인 해결로 판정하지 않는다. 실제 유료 생성 모델의 가용성·품질·요금, 클라우드 GPU·SSH·OptiX, 사용자 sealed engine이 필요한 경로, 최종 릴의 시각·청취·사실 검토는 미검증이다. 전체 동적 suite의 Linux/Python 3.12 실행이나 모든 함수·분기의 exhaustive coverage도 주장하지 않는다. 선택적 도구가 없어 건너뛴 검사를 기능 성공으로 집계하지 않는다.
