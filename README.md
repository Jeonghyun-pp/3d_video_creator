# AI Technical Visualization Studio

LLM 에이전트가 기술 설명 영상을 만들고 수정할 때 사용하는 Python CLI와 MCP 도구입니다. 장면 명세, 편집 이력, 검사 결과를 파일로 남기고, 수정한 상태를 다시 생성해 비교합니다. 개인 제작 환경에서 개발했으며 팀 도입률이나 개발 시간 절감률은 측정하지 않았습니다.

| 경로 | 하는 일 | 진입점 |
|---|---|---|
| `studio` | Blender 장면·카메라·기구 생성, Workbench 편집, 상태 복원과 재현 검사. 이야기·검토·편집 공통 도구도 제공 | [사용법](docs/USING_THE_AGENT.md), [v1 Skill](.agents/skills/reel-production/SKILL.md) |
| `studio2` | 생성 이미지로 컷을 구성하고 표식·움직임·영상 검사 연결. 이야기와 편집은 `studio`를 재사용 | [v2 Skill](.agents/skills/reel-production-v2/SKILL.md) |
| `scripts/video_pipeline.py` | 초기 `scene.json` 기반 렌더·자막·조립 경로 | [Legacy 사용법](docs/LEGACY_PIPELINE.md) |

## 처음 확인하기: 모델 호출·Blender 없이

Python 3.12 이상이 필요합니다. 저장소 루트에서 실행합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest tests.test_shot_edit tests.test_timings tests.test_workbench tests.test_registry_sync tests.test_content_keys tests.test_read_limits -v
.venv/bin/python -m studio --help
.venv/bin/python -m studio2 --help
```

38개 테스트가 `OK`로 끝나는 것이 기대 결과입니다. 스키마와 코드가 읽는 값이 일치하는지, 선언한 값만 편집할 수 있는지, 실패도 시간 기록에 남는지, 체크포인트 복원 후 올바른 연산만 재생하는지, MCP 내부 호출 경계가 유지되는지 확인합니다. 모델·Blender 호출은 mock으로 대체하므로 영상 품질이나 실제 렌더 성공을 증명하지 않습니다. 이 범위를 [CI](.github/workflows/host-checks.yml)에서도 실행합니다. CI의 별도 단계는 FFmpeg와 합성 미디어로 승인 sheet·영상 합성 회귀를 검사합니다.

[설치와 실제 Blender 검증](docs/SETUP.md)은 별도 단계입니다. `scripts/bootstrap.sh`는 RIFE 바이너리 다운로드와 MCP 설정까지 포함하는 전체 제작 환경 설치이므로 위 첫 확인에는 필요하지 않습니다.

## 구현을 따라 읽기

- [명세 편집](studio/shot_edit.py): JSON pointer 기반 set/add/remove, 선언되지 않거나 읽히지 않는 값 거부.
- [Workbench](studio/workbench.py): 편집·체크포인트·복원·커밋과 재현 검사. [공개 Blender smoke](tests/studio/workbench_shot_smoke.py)는 임시 감속기 프로젝트로 편집·복원·실패 복구를 확인합니다.
- [명령 시간 기록](studio/timings.py): 명령과 하위 작업의 시간·실패를 연결해 기록합니다.
- [공개 체크아웃 검증](docs/VERIFICATION.md): 새 환경의 검사 결과와 실제 Blender 편집·복원·commit 재현 검증, 미검증 범위.
- [공개 예제](examples/): 파빌리온, 삼성역 단면, kit 입력과 author 코드. [테스트 fixture](tests/fixtures/)도 공개되어 있습니다.
- [초기 기획과 로컬 실험 이력](docs/HISTORICAL_CONTEXT.md)은 과거 기록으로 보존했습니다.
- [단계별 검증 기록](docs/BUILD_REPORT.md), [개발 진행 기록](docs/ENGINE_PROGRESS.md): 당시의 환경·범위·남은 문제를 함께 읽어야 합니다.

`projects/`의 제작본·참조 영상과 `library/contrib/`의 로컬 확장은 공개 저장소에 없습니다. 예제 입력을 복사하는 것과 제작 승인을 받는 것은 별도입니다. 실제 제작에는 사실·이야기·world·shotlist 등 필요한 결정을 준비해야 하며, 과거 예제의 검증을 새 프로젝트의 승인으로 재사용하지 않습니다. [제작 절차](docs/USING_THE_AGENT.md)를 따르세요.

## 환경과 범위

Blender 기준 버전은 5.2.2입니다. 렌더·영상 조립에는 Blender와 FFmpeg가 필요하고, GPU·음성 지원은 운영체제에 따라 다릅니다. Python 의존성, 선택 CAD 환경, 생성 모델·봉인된 실행 엔진 설정은 [SETUP](docs/SETUP.md)에 구분했습니다. 생성 영상의 현재 선택 모델은 [video_models.json](library/video_models.json)의 `paid_video`가 기준입니다.

에이전트 진입점은 [AGENTS.md](AGENTS.md)입니다. 검사 통과는 그 검사가 측정한 범위에 한정됩니다. 표본 프레임 검사를 연속 운동의 정확성으로, 테스트 실행 시간을 팀 생산성 개선으로 해석하지 않습니다.
