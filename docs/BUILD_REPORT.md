# 제작 에이전트 빌드 결과

2026-10-02. 이 문서는 실제 구현·검증 결과를 기록한다. 설계서는 AGENT_BUILD_PLAN.md, 사용법은 USING_THE_AGENT.md, 시각 품질 개선 계획은 REFERENCE_QUALITY_ROADMAP.md다.

현재 코어·자산·미디어·타이밍 모듈이 구현됐으며 실제 Blender/FFmpeg 테스트를 통과했다. 서로 다른 두 프로젝트에서 재생 가능한 초안을 만들었다. **사용자의 컴퓨터 부하 우려에 따라 고해상도 렌더와 자동 후속 실행을 중단했다.** 1080×1920 원본은 540프레임 중 106프레임을 보존했고 최종 해상도 영상은 아직 완성되지 않았다. 전체 제작 에이전트의 첫 작동 버전이며, 임의의 소재에서 한 명령으로 레퍼런스급 결과를 보장하는 완성 서비스는 아니다.

## 결과물

- [18초 파빌리온 초안](../projects/harness_validation/final/rough_3796557e18e1f89b/scratch_candidate.mp4): 세 컷, 한국어 임시 음성·자막·움직이는 앵커 라벨. 540×960 편집본이며 소스는 360×640 프리뷰다.
- [4초 자산 재사용 검증](../projects/harness_validation/harness_transfer/final/rough_f6ee64615b8f410d/candidate.mp4): 준비한 동일 자산의 독립 인스턴스, 일반 explode/assemble, 서로 다른 두 카메라. 무음 의도이며 거친 검증용 영상이다.
- 편집 가능한 장면: `projects/harness_validation/shots/shot_01/versions/v0003/scene.blend`, `shot_02/versions/v0004/scene.blend`, `shot_03/versions/v0003/scene.blend`.
- 초기 화면과 개선 비교: `examples/pavilion_hero.png`, `examples/pavilion_hero_v2.png`; 최신 확정 장면의 대표 프레임은 각 shots/의 renders/ 아래.
- 실제 형상 보존 수정 증거: 각 컷 `versions/v0002/revision_check.json`.
- 1080×1920 후보 영상: 부하를 줄이기 위해 출력 중단. shot_01의 0–105프레임 보존, shot_02/03 대기 작업 취소. 완성 파일로 표시하지 않는다.

주제는 창작한 설명용 파빌리온이다. 특정 실물 건물의 정확한 내부 구조를 재현했다고 주장하지 않는다. 임시 음성 결과는 scratch로 표시하고 게시용 마스터로 승격하지 않는다.

## 구현된 연결

자연어 요청 → 제작 스킬/Astra → brief/shot/자산 계약 → 자산 검사·가져오기 → Blender 원본/수정 버전 → 렌더 작업 → 음성/타이밍 → 그래픽·편집 → QA/시각 검토 → 필요한 부분 수정.

| 모듈 | 실제 파일 | 동작 |
|---|---|---|
| 오케스트레이션 | .agents/skills/reel-production, .codex/, scripts/reel_agent.py | Astra 요청 실행기, Sol 구현·Luna 인벤토리·Astra 검토 역할, 제작 절차 |
| 프로젝트·실행 | studio/project.py, common.py, __main__.py | 스키마, 상태·재개, JSON CLI, 리뷰/마스터 검사 |
| 자산·레퍼런스 | studio/assets.py, references.py, blender_ops/asset_prepare.py, assets.py | 로컬 검색, Poly Haven API 구현, 종속 파일, 2단계 준비, 부품·앵커 매핑, 독립 인스턴스 가져오기 |
| 장면·수정 | studio/blender.py, blender_ops/build_scene.py, scene_tools.py | 불변 버전, 기준 장면 사본 수정, 카메라와 여섯 동작 |
| 렌더 | studio/jobs.py, blender_ops/render_frames.py | 백그라운드 job, 잠금, 프로필·샘플 선택, 해시 캐시, 취소·부분 복구, 코드 스냅샷 |
| 음성·동기화 | studio/audio.py, timing.py | Yuna scratch, WAV 입력, 선택적 ElevenLabs, cue→동작 프레임, 범위 오류 검출 |
| 그래픽·편집 | studio/edit.py | 움직이는 3D 앵커, 라벨 슬롯, 한국어 자막, 실제 해상도 편집, 음성/무음 구분 |
| 검수 | studio/qa.py + 제작 스킬 | 프레임·인코딩·음량 검사, contact sheet, 이미지 비교, 한계/수정 기록 |

새 장면은 author 스크립트로 만들고, 준비된 자산은 import_prepared_asset로 가져온다. 특정 소재마다 Astra가 제작 판단과 필요한 코드를 작성한다. `author_pavilion.py` 하나가 모든 건물을 자동 생성하는 구조가 아니다.

## 확인한 기능

- 같은 장면의 패널 분리 순서·시작 시점을 바꿔 새 버전을 생성했고 원래 메시 해시가 유지됐다.
- 전체 초안에서 연결부를 가리는 패널을 발견해 shot_02 카메라만 수정했다. v0004의 geometry/materials 보존 검사를 통과했고 수정한 전체 초안의 0.25초 간격 이미지에서 연결부가 드러남을 확인했다. 기술 QA만 통과한 이전 초안은 시각 검토에서 거절했다.
- 일반 동작: explode, peel, assemble, cutaway, flow, highlight. Blender에서 여섯 종류와 반복/삭제 시 복구를 검사했다.
- 실제 프레임 렌더, 정확한 개수의 동영상 생성, 누락된 한 프레임만 다시 렌더, 동일 요청 캐시 재사용, 취소/재개를 확인했다.
- 자막만 바꾸는 실제 CLI 검증에서 장면·원본 렌더·음성 해시는 유지되고 편집본만 달라졌다.
- GLB/FBX/OBJ/BLEND 모델을 실제 가져와 치수를 검증했다. 준비 자산 두 인스턴스의 객체·메시가 분리되고, 부품 동작과 앵커가 유지됐다. 회전과 비균일 축척이 있는 인스턴스에서도 1m 이동을 확인했다.
- 같은 자산 버전에 다른 파일/매핑을 덮어쓰면 거절한다. 이미 확정한 Blender 장면의 수정도 별도 버전으로 남는다.
- 실제 한국어 scratch 음성과 FFmpeg 편집·QA를 확인했다. 명시적 무음도 지원한다. 음성이 컷보다 길면 잘라 버리지 않고 오류를 반환한다.
- 바뀐 발화 cue와 기존 동작 시간이 다르면 timing resolve와 재렌더가 필요하다는 오류를 반환한다. 고정 시간 동작은 그대로 유지한다.
- 음성·화자·주변 발화가 달라지면 음성 캐시를 무효화한다. 프로젝트 revision을 올리지 않은 내용 변경도 이전 후보의 상태/마스터 승격 검사에서 검출한다.

증거: tests/test_studio_*.py, tests/studio/ 및 `projects/harness_validation/asset_integration/integration_summary.json`, `instances_integration.json`.

## 검증 및 환경 한계

신규 테스트 37개가 통과했다. 실제 Blender 렌더 smoke 6개, 여섯 동작과 반복/삭제 복구 smoke 9개, 형상·재질·카메라 보존 smoke 7개는 별도로 통과했다. 마지막 보완은 요청마다 preserve 목록을 명시적으로 교체·해제하는 기능이며, 가드 단계 회귀와 실제 Blender 보존 smoke를 다시 통과했다. 위 두 초안의 해상도·프레임 수·길이·인코딩·자막 영역 검사가 통과했다. 음성이 있는 18초 초안은 측정 -16.01 LUFS, -1.5 dBTP다. 이미지 시퀀스를 검토했으며 사람의 전체 재생·청취 승인으로 표시하지 않았다.

실측 예: 최초 파빌리온 장면 저장 30.165초, 저장된 연결부 장면의 카메라 수정·보존 검사 1.711초, 단순한 준비 자산 재사용 장면 저장 0.463/0.501초. 이는 Blender 실행·저장 시간이며 에이전트의 사고·스크립트 작성·영상 렌더 시간을 포함하지 않는다. 같은 장면의 재촬영이 처음 모델 제작보다 가벼운 이유를 보여주는 값이지, 새 소재의 완성 시간을 보장하는 수치가 아니다.

전체 테스트 검색을 처음 실행했을 때 기존 Workbench 카메라 테스트의 여섯 하위 사례가 SIGABRT로 실패했다. 기존 카메라 코드·테스트는 변경하지 않았다. 새 Cycles 경로의 성공과 이 환경 의존 실패를 구분한다.

- **네이티브 Astra 실행기:** 최초 빌드의 `Failed to synchronize managed preferences`는 2026-10-02 재검증에서 재현되지 않았다. `gpt-6-astra`를 요청한 독립 CLI가 실제 장면·검증·정지 화면을 생성했다. 기본 workspace sandbox의 Blender SIGSEGV는 공식 `--approve-for-me` 승인 경로에서 해소됐다. 아래 추가 검증 기록에 첫 시도 실패와 복구 범위를 구분한다.
- **GPU:** EEVEE는 headless SIGABRT, Metal Cycles는 XPC_ERROR_CONNECTION_INVALID 컴파일 오류를 보였다. CPU Cycles는 실제 렌더에 성공했다. 실패 시 CPU 대체 경로를 구현했고 `job_7567012fde94`의 실제 실패→CPU 재시도→1080×1920 프레임 완료를 확인했다. 초기 개발 중 만들어진 job의 과거 실행분까지 코드 스냅샷으로 소급 보장하지 않는다. (정정 2026-10-04) Metal 실패는 Codex 샌드박스 안에서만 재현된다. 샌드박스 밖(`--approve-for-me` 실행기, 직접 실행)에서는 Metal Cycles가 정상이며 CPU 2스레드 대비 약 11배 빠르다(photoreal_research 01). 현재 렌더 기본 장치는 GPU이고 실패 시 CPU 재시도가 유지된다.
- **외부 다운로드:** 최초 빌드 때는 DNS 실패로 미검증이었다. 이후 사용자 요청의 2번 작업에서 Poly Haven 실제 검색→다운로드→Blender 가져오기→부품 매핑→재사용을 검증했다. 아래 추가 검증 기록을 참조한다. 다른 공급자와 특정 건물 전체 자산까지 검증했다는 뜻은 아니다.
- **최종 음성:** 유료 TTS를 호출하지 않았다. 현재 영상의 Yuna는 임시 음성이며 최종 품질 음성으로 표시하지 않는다.
- **시각 품질:** 레퍼런스급 미달. 대표 장면은 규칙적인 모델, 단순한 환경, 일정한 분리 동작 때문에 참조와 차이가 남는다. `reviews/`에 needs_work를 기록했다.
- **표현 범위:** peel은 강체 판 분리, flow는 단일 경로를 따르는 표식이다. 실제 물리 유동·천 변형으로 표현하지 않는다. 단면 동작은 기존 닫힌 메시의 Boolean이며 내부 구조를 저절로 만들어내지 않는다.

## 다음 사용

현재는 자동 재개하지 않는다. 중단 기록은 `projects/harness_validation/pause_report.json`이다. GPU 접근 문제를 해결하거나 충분한 유휴 시간을 확보한 뒤 사용자가 재개를 요청하면 다음 세 작업만 이어 진행한다. 이전 shot_02 v0003 작업은 카메라가 가리는 구버전이므로 재개하지 않는다. (정정 2026-10-04) GPU 접근 문제는 샌드박스 한정이었다.

```bash
../.venv/bin/python -m studio job resume --project projects/harness_validation --job job_dd5e7ec81c27
../.venv/bin/python -m studio job resume --project projects/harness_validation --job job_a7ac43087e1e
../.venv/bin/python -m studio job resume --project projects/harness_validation --job job_969f9e0336b6
```

세 작업이 complete가 된 후 `edit build --project projects/harness_validation --profile candidate`, `qa collect --project projects/harness_validation --candidate 반환된_ID`를 실행하고 이미지를 검토한다. 기존 106프레임은 무결성 검사 후 재사용한다. CPU 실측은 대략 프레임당 11–12초이므로 남은 434프레임만 약 80–90분이 걸릴 수 있다. 장면별 렌더 난이도와 컴퓨터 상태에 따라 달라지는 추정이며, 이번 중단 시점에 발열·하드웨어 손상을 측정한 것은 아니다.

현재 폴더 대화에서 “reel-production으로 harness_validation의 패널 분리 순서를 반대로 수정해”처럼 요청한다. 에이전트는 현재 revision과 원본을 읽고 필요한 명령/패치만 실행해야 한다. 직접 명령과 입력 예제는 USING_THE_AGENT.md에 있다.

다음 시각 개선의 첫 작업은 해상도 증가가 아니라 소재에 맞는 고품질 자산·내부 깊이·카메라와 분리 연출이다. REFERENCE_QUALITY_ROADMAP.md의 각 단계에는 수정 방법과 통과 증거를 명시했다.

## 추가 검증: 외부 3D 자산 확보 — 사용자 요청 2번만

실제 외부 자산의 검색, 다운로드, 재질 포함 가져오기, 부품 분리, 근접 적합성 판정, 준비 라이브러리 저장까지 완료했다. 대표 장면 미학 개선, 독립 실행기, 완성 릴스, 다른 소재 릴스 제작은 진행하지 않았다. 이전 고해상도 렌더도 재개하지 않았다.

출처는 [Poly Haven의 Modular Industrial Pipes 01](https://polyhaven.com/a/modular_industrial_pipes_01), 제작자 Jorge Camacho. [자산 CC0 안내](https://polyhaven.com/license)와 [공개 API 사용 조건](https://polyhaven.com/our-api)을 확인했다. API를 사용하는 도구와 기록에는 Assets from Poly Haven 출처를 남겼다. 결제·계정 로그인·API 키 없이 확보했다.

| 검증 | 실제 결과 |
|---|---|
| 검색 | modular industrial pipes → 실제 후보 3개 확보. 모듈형 구조·독립 부품·PBR 재질을 기준으로 선택 |
| glTF 다운로드 | 2K, 8개 파일, 16,617,148바이트. 모델·버퍼·6개 텍스처를 함께 받아 체크섬 검증 |
| 편집 원본 다운로드 | 같은 자산의 .blend+2K 재질, 9개 파일, 28,849,810바이트 |
| 재질 | 실제 사용 재질 2개. 색상 sRGB, 노멀·금속/거칠기 Non-Color 확인. 이미지가 준비 .blend 안에 패킹됨. 누락 파일 0개 |
| 기본 부품 | 8개 배관 모듈을 ID로 매핑. 밸브 모듈만 0.3m 이동, 나머지 7개 불변, 시작 위치 복귀 검증 |
| 세부 분리 | glTF 밸브 연결 조각 293개, native .blend 사본 22개. 원본 3,066개 면을 유지한 채 분리. 손잡이/커버 조립체를 0.16m 이동한 정지 화면 확인 |
| 재사용 | 같은 다운로드 요청은 캐시 재사용, 추가 다운로드 0바이트. 원본 SHA-256 불변 |
| 코드 보완 | 실제 /search 응답의 slug 필드를 지원하고 /info에서 이름·태그를 보완. 관련 테스트 11개 통과, 그중 신규 실제 응답 형식 회귀 1개 |

**품질 판정:** 배관 모듈 배치와 부품 분리 설명용으로 사용 가능하다. 640×640 접사에서는 손잡이·플랜지의 각진 윤곽과 계기판 표현 한계가 보이므로 화면을 크게 채우는 최종 접사용으로는 미승인이다. 2K→4K로 텍스처를 높여도 각진 실루엣은 해결되지 않는다. 밸브 내부 작동 기구는 확인되지 않았으며, 외관 모델을 실제 작동 구조의 증거로 사용하지 않는다.

**확보 방법:** 필요한 촬영 거리와 움직일 부품 정의 → 원본 모델·라이선스·재질 파일 확인 → 가능한 경우 native .blend 확보 → 작은 다각도 검사 → 부품 사본 분리 및 의미 ID 매핑 → 원본/재질 보존 검사 → 고정 버전으로 라이브러리 저장. 세부 부품 분리가 필요한 경우 이번 결과에서는 glTF보다 .blend가 유리했다. 모든 .blend가 자동으로 더 좋다는 일반화는 하지 않는다. glTF에서 분리된 정점은 UV·노멀 경계 때문일 수 있으므로 단순 연결 조각 수를 물리 부품 수로 해석하지 않는다. 이 원리는 [Blender glTF 설명](https://docs.blender.org/manual/id/4.0/addons/import_export/scene_gltf2.html)의 정점 분리 설명과 부합하며, 실제 조각 수 차이는 로컬 검사에서 확인했다.

일반 설비·소품·재질은 Poly Haven을 바로 사용할 수 있는 1차 경로로 확보했다. 더 다양한 외관 모델은 [Blendkit](https://www.blendkit.com/docs/licenses/), 실제 제조 제품은 [BIMobject](https://www.bimobject.com/en-us/) 같은 공급자를 조사할 수 있다. 이 두 경로는 자료 조사만 했으며 계정 연결·다운로드·IFC 변환을 검증하지 않았다. 특정 건물의 외피·골조·내부가 모두 들어 있는 고품질 자산은 별도 소재별 검사가 필요하다.

재현 자료는 `projects/harness_validation/external_asset_validation/`에 있다.

- [근접 검사용 화면](../projects/harness_validation/external_asset_validation/valve_closeup.png), [뒷면](../projects/harness_validation/external_asset_validation/valve_back.png), [native 세부 분리 화면](../projects/harness_validation/external_asset_validation/native_valve_separated.png).
- `request.json`, `search_result.json`, `candidate.json`: 실제 검색·선정.
- `library/modular_industrial_pipes_01/v0001/asset.json`: glTF 기반 8부품 준비 자산.
- `library/modular_industrial_pipes_01/v0002/asset.json`: native 원본·재질·검사 결과 보존.
- `library/industrial_pipes_editable/v0001/asset.json`: 원본 면/재질을 유지해 세부 분리한 파생 자산. 일반 배관 7개+밸브 기하 조각 22개를 매핑. 29개가 모두 실제 기계 부품이라는 뜻은 아니다.
- `validation.json`, `native_separation.json`, `native_motion.json`: 이동·불변성·재질·토폴로지 검사.
- `validate_asset.py`, `inspect_native.py`, `render_separation.py`: 재현용 Blender 검사 스크립트.

검사 Blender는 CPU 2스레드로 제한했다. 384px 자산 검사 정지 화면과 640px 세부 검사 정지 화면만 만들었고 영상 프레임 연속 렌더는 하지 않았다. 이 작업으로 외부 자산 확보 경로의 실물 검증 공백을 해소했으며 레퍼런스급 품질 달성 판정은 별도로 남긴다.


## 추가 검증: 독립 실행기 제작 및 부분 수정 — 사용자 요청 3번

2026-10-02, `projects/harness_validation/one_command_validation/`에서 실행했다. 기존 DDP/station과 중단된 고해상도 작업은 재개하지 않았다. 독립 실행기는 명시적으로 `codex exec -m gpt-6-astra`를 요청했고 실행 로그를 보존했다. 이는 현재 대화 모델의 변경이나 서버 측 모델 정체성에 대한 독립 검증을 뜻하지 않는다.

- 기본 workspace-write 실행은 Blender Metal 초기화 SIGSEGV로 실패했다. 243.67초 후 부모 프로세스가 중단하고, 공식 `--approve-for-me` 자동 승인 검토 옵션을 실행기에 추가해 재시작했다. 전역 인증/관리 설정은 변경하지 않았으며 이전 managed-preferences 오류는 재현되지 않았다. (정정 2026-10-04) 이 SIGSEGV는 샌드박스 안의 Metal 초기화 문제이며, 샌드박스 밖 GPU 렌더는 정상이다.
- 승인 검토 모드의 재시도는 427.14초에 exit 0으로 완료됐다. 준비된 외부 배관 자산을 가져오고 pipe08만 월드 −Y 0.3m 이동하는 장면을 생성·검증·출력했다. 원본 23개 파일 SHA-256과 나머지 7개 부품의 30프레임 배치·형상·재질을 검증했다.
- 에이전트가 원본 위치가 사라지는 import 버그를 검출했다. 첫 장면 v0001은 보존하고 렌더하지 않았으며 프로젝트 내 복구로 v0002를 만들었다. 부모 세션은 공통 함수 `studio/blender_ops/assets.py`에서 모든 객체를 먼저 링크·평가한 뒤 행렬을 읽도록 수정했다. `tests/studio/import_pose_smoke.py`는 수정 전 실패, 수정 후 부모/자식 및 회전·비균일 축척 인스턴스 4개 비교에 통과했다. 실제 외부 배관도 별도 복구 코드 없이 8개 원본 행렬과 정확히 일치했고 원본 파일은 불변이었다.
- 별도 자연어 명령은 223.97초에 exit 0으로 완료됐고, 기존 v0002 snapshot의 motion 범위만 수정해 v0003을 만들었다. 이동 거리는 0.15000000298m. 나머지 7개 부품·카메라·조명·월드·재질·형상을 모든 30프레임에서 비교했고, 이전 파일 52개 해시를 보존했다. 부모도 기존 장면과 두 PNG의 해시를 독립 확인했다.
- 실제 렌더는 처음 2장(0·29), 수정 후 1장(29), 총 3장의 360×640·8 samples 정지 화면이다. CPU 2스레드를 설정했다. 연속 영상·음성·추가 다운로드·유료 API는 실행하지 않았다. 두 호출의 출력 이미지를 확인했다.
- 공통 수정 후 `test_studio_*.py` 38개 통과. 실행기의 기본/자동 검토 dry-run도 확인했다.

**판정:** 준비된 자산을 사용하는 제한된 장면에서 독립 실행기의 요청→제작→검증→이미지 검토, 그리고 새 요청→부분 수정→재검증→출력이 작동한다. 첫 시도의 환경 개입과 importer 수정이 있었으므로 처음부터 무개입으로 성공했다고 표현하지 않는다. 수정 요청은 기존 상태를 읽는 별도 실행이며 동일 대화 세션의 resume 기능 시험은 아니다. 모든 소재의 자동 제작이나 완성 릴스 품질 검증은 아니다. 낮은 해상도와 밸브/긴 배관 실루엣 겹침이 남아 있으며 레퍼런스급 대표 화면으로 승인하지 않는다. 원본 외관 모델의 내부 기구·제조 정확성도 미검증이다.

[첫 제작 및 수정 상세 보고서](../projects/harness_validation/one_command_validation/valve_preview/README.md), [최초 검증](../projects/harness_validation/one_command_validation/valve_preview/evidence.json), [수정 검증](../projects/harness_validation/one_command_validation/valve_preview/evidence_revision.json). 실행 시간·호출 모드·최종 출력은 부모의 `validation_summary.json`에 집계한다. 최초/재시도/수정 로그와 실패 기록은 덮어쓰지 않았다.


## 추가 검증: 레퍼런스 접사 품질 개선 — hero_quality

기존 참조 영상의 48.5초 외장재 연결부를 선정해 별도 `projects/harness_validation/hero_quality/`에서 설명용 모델과 대표 정지 화면을 제작했다. Luna는 레퍼런스·로컬 자산 조사와 독립 이미지 검토, Sol은 단일 장면 작성·검증·렌더를 맡았고 부모 세션이 비교와 수정 방향을 결정했다. 별도 Astra CLI를 중복 실행하지 않았다. 요청한 모델과 역할은 orchestration.json에 기록했다.

기존 배관 모델은 촬영 대상과 맞지 않아 사용하지 않았다. 분리된 판·채널·볼트·와셔·가스켓·외장재를 새로 구성했다. 콘크리트는 기존 로컬 Poly Haven Concrete Wall 004 사본을 검증·패킹했고, 무늬 철판 metal_plate는 표면 종류가 맞지 않아 제외했다. 출처는 [Concrete Wall 004](https://polyhaven.com/a/concrete_wall_004), [자산 라이선스](https://polyhaven.com/license). 기존 DDP/station 원본은 보존했다. 구조는 실측/시공 상세가 아닌 설명용이며 참조 속 치수나 작동 주장을 검증한 것이 아니다.

540×960·32 samples의 CPU 정지 화면 6개를 실제 비교했다. v1은 불린 절삭 도형의 면 방향 오류로 슬롯이 없었고 구도가 과도하게 잘렸다. v2에서 실제 구멍을 확인하고 CPU 2스레드 고정을 적용했다. v3 구도, v4 실제 콘크리트 재질과 금속 거칠기/색 변화, v5 고정부 가림과 재질 강도, v6 가로 슬롯·벽/외장재 단면·비스듬한 촬영 각도를 개선했다. 첫 렌더만 스레드 고정이 빠졌으며 이후 5개는 2스레드다. 렌더 시간은 각 약 7–16초, 전체 약 82초이며 모델 사고·스크립트 작성 시간은 제외한다. 작업 6개 모두 완료, 활성 렌더 없음. 정지 프레임 길이의 자동 clip 부속 파일은 움직임 검증 영상으로 세지 않는다.

**판정: needs_work.** 구성·구멍·판 두께·볼트 가시성·재질 구분은 개선됐지만, 금속 표면과 연결부-외장재 관계가 참조보다 단순하다. 레퍼런스급 승인, 3–5초 동작, 고해상도 검토까지 완료한 것으로 표시하지 않는다. 다음은 상세 자산/연결 관계 보완과 최종 대표 구도이며, 정지 화면 승인 뒤 짧은 동작으로 넘어간다.

[나란히 비교](../projects/harness_validation/hero_quality/comparison.jpg), [상세 기록](../projects/harness_validation/hero_quality/README.md), [부모 검증 및 판정](../projects/harness_validation/hero_quality/review.json). 실제 구멍 검증, 촬영 시 고정부 가림 확인, 텍스처 적합성 검사, 첫 렌더 전 자원 설정 확인과 정지 화면 게이트를 제작 스킬에 반영했다. 성공한 범위와 미승인 품질을 함께 기록하며 만능 레시피로 승격하지 않았다.


## 대표 화면 후속 보완: v0007–v0011 — 움직임은 계획만 유지

사용자 정정에 따라 곡선 이동/속도감 있는 4초 영상은 계획으로만 남겼다. 동작 프로젝트·장면·렌더는 생성하지 않았다. 기존 대표 화면 v0006 이후 형상, 빛, 표면 좌표를 보완해 v0011 정지 후보를 만들었다. Sol이 장면 구현, Luna가 독립 검토, 부모가 비교·수정 방향과 산출물 무결성 검사를 담당했다.

- v7: 중앙 채널 폭/길이와 구멍 배치를 조정하고, 왼쪽 고정부를 화면 안에 배치했다. 볼트의 장식용 캡을 제거하고 접힌 받침을 보완했다.
- v8: 앞쪽 넓은 덮개를 제거해 슬롯을 노출하고 배경 끝의 빈 공간을 없앴다. 조명을 바꾸고 재질 좌표를 객체 로컬 기준으로 고쳤다. 결과 금속이 너무 어두워져 최종 후보로 채택하지 않았다.
- v9: 중간 밝기·금속 거칠기·표면 패턴 크기를 조정했다. 실제 720×1280·64 samples 정지 화면도 출력해 540p 확대가 아님을 확인했다.
- v10: 외장재 접힌 단면·가스켓과 노출 나사산을 보완하고, 벽을 [Poly Haven Concrete Floor 01](https://polyhaven.com/a/concrete_floor_01) 기반 작은 입자로 시험했다. 파일은 기존 로컬 원본의 해시 일치 사본이고 원본은 변경하지 않았다. 첫 결과에 세로 늘어짐이 남았다.
- v11: 텍스처 교체보다 근본 원인인 UV 방향·배율 오류를 수정했다. 기존 큐브 전면 UV는 X가 V로, Z가 U로 향하고 각각 atlas의 1/4만 사용했다. 실제 반복률은 X 1.408회/m, Z 0.222회/m로 약 6.3배 비등방이었다. 명시적 평면 UV로 양쪽 1.25회/m(0.8m/타일)을 맞추고 검사했다. 세로 줄무늬가 제거된 뒤 720×1280·64 samples 원본 한 장을 추가 출력했다.

이번 추가 렌더는 540×960 다섯 장과 720×1280 두 장, 총 7장이다. 모두 CPU 2스레드이고 합계 렌더 실측 181.341초(모델 사고·스크립트 작성 제외)다. v11의 720p 렌더는 52.492초. 전체 hero_quality 작업 13개가 모두 완료됐으며 활성 렌더는 없다. 모든 저장 장면과 PNG 해시 및 재질 원본/사본 해시를 부모가 검증했다. 원본과 앞선 후보를 보존했다.

**시각 판정은 여전히 partial / needs_work다.** 좁은 채널, 실제 슬롯, 노출된 고정부, 외장재 단면, 늘어나지 않는 벽 표면은 개선됐다. 독립 검토에서는 금속/콘크리트의 미세 표면과 참조의 접힌 연결 형상까지 동급이라고 보기 어렵다고 판단했다. 기술 검사 통과를 레퍼런스급 승인으로 바꾸지 않았다. 부품명 설명 화면은 별도 사본이며 원본 형상/광택을 후처리로 바꾸지 않는다.

[이번 보완 전후 비교](../projects/harness_validation/hero_quality/comparison_refinement.jpg), [최신 검토·검증](../projects/harness_validation/hero_quality/review.json). `uv_audit_v0010.json`, `uv_audit_v0011.json`, `inspect_wall_uv.py`에 UV 오류 재현과 수정 검사를 남겼다. 실제 길이 기준 텍스처 축 검사와 움직일 객체의 로컬 재질 좌표 원칙을 제작 스킬에 반영했다. 향후 동작은 사용자 요청에 따라 별도 진행하며 이번에 자동 시작하지 않는다.


## 리서치 반영 업그레이드 (2026-10-04): 숫자는 코드가, 구조는 Astra가

계획: 리서치 3건(직접 제어·문헌·실무)의 결론 "LLM은 분해·관계에 강하고 정확한 숫자에 약하다"를 코드로 옮겼다. 렌더 지문 파일(`jobs.py`, `render_frames.py`, `scene_tools.py`, `render_profile.py`)은 수정하지 않았다(수정 시각 14:43 이전 그대로). 기존 렌더 캐시는 무효화되지 않는다. 유료 호출 0건.

| 단계 | 구현 | 실측 검증 |
|---|---|---|
| A 기반 | `remove_subject`/`build_subject(replace)`/`rebuild_parts`, 부품 앵커(배치된 면 기준), 빌드 시 lint(`SUBJECT_SPEC_INVALID`), spec 스냅샷+해시, `FIDELITY_STALE`, 리포트 summary | 재빌드 중복 0, 해시 동일, 부분 재빌드 시 mirror 추종, 이전 해시 없는 리포트도 stale 처리 |
| B 워크벤치 | 상주 Blender(Unix 소켓·토큰·allow-list), 타입 도구 15종, ID 프리뷰, checkpoint/restore, replay 커밋, Codex MCP `studio_workbench` | 기동 1.1 s, measure 왕복 0.1 ms, 4뷰 프리뷰 0.32 s(ID 미매칭 픽셀 0), 커밋 replay 일치, 조작한 기대값은 `WORKBENCH_REPLAY_MISMATCH`, exec 기본 거부 |
| C 수치 자동화 | datum/register 규칙, `subject trace`(loft 스테이션·날개 평면형, 가림 보고), `subject fit`(경계 Nelder-Mead, 출처 치수 고정, 관측 불가 파라미터 복원) | P-51D: 거친 추정 IoU 0.62 → trace 0.961 → fit 0.968 (수작업 spec 0.947), 258회 평가 47 s. 모델 기반 정합은 0.61 m 어긋남을 재현했고, 규칙 기반 정합이 이를 막았다 |
| D 결합 | `relations`(attach/align/through/on_surface/symmetric), `assembly_claims`(contact/no_interference/clearance/through/cover/no_floating), `geom_checks.py` | 정상 1건 통과, 심은 결함 5종이 각각 해당 claim만 실패(2.0 mm 들뜸, 5.0 mm 관입, 1.0 mm 구멍 간섭, 피복 35 mm, 이격 200 mm). winch를 관계로 바꾼 뒤 형상이 좌표판과 동일 |
| E 수정 루프 | `shot select`, `repair.py`(점수·자동 복귀·무개선 3회 차단·사용자 reset), `--diagnosis` | 단위 테스트: 악화 시 복귀, 예산 소진 차단, 통과 shot의 카메라 작업은 예산 미소모 |
| F 건축 | `profile`(KS D 3502/EN 10365 표, 정확한 다각형), `wall`(개구부, 불리언 없음), 격자 `array`, `dim_role`, DXF(ezdxf, CAD venv) | HEB300 단면적 +0.16 %, KS H-300 +0.08 %, 벽 체적 오차 0. N+1: 기둥 베이스(앵커 4, 결합 claim 6개 통과)+DXF 벽(IoU 0.9978), 빌더 코드 변경 없음 |
| G 예제·API | `subject promote/exemplars/init --from-exemplar`, Blender 5.2.2 API 색인(타입 4012, 연산자 2498), `api search/show`, doctor 항목 | 예제 4개 등록(winch, p51d, column_base, wall_a). `Mesh.use_auto_smooth` 조회 시 실제 대체 API 안내 |
| H 하이브리드 | 프롬프트 대응표, 방향 색(앞 빨강/뒤 파랑), 시간 구간 검은 placeholder, 참고 이미지 시간 구간, `generative_look.motion` | Cycles 픽셀로 앞/뒤 색과 구간 내 검정 확인, 프롬프트 lint 통과 |
| I 스킬·문서 | SKILL #8·#9, references `workbench.md`·`building_elements.md`, agent toml, 런처 프롬프트, review 스키마 키, 문서 불일치 정정 | `test_skill_contract`가 새 명령 19개의 존재를 검사 |

테스트: 단위 168개 통과(이전 144개). smoke 17개 중 16개 통과(신규: workbench, assembly, previs). 실패 1개 `control_pass_smoke`는 이번 변경과 무관하다. 15:20의 qa_generative 규칙("추적 불가 라벨은 미검증 = 실패")이 평평한 큐브 면에 앵커를 둔 이 smoke 장면을 거부한다. 관련 파일은 모두 이번 작업 이전 시각이다. 규칙을 완화하지 않았고 smoke 장면 수정이 필요하다.
회귀: jet_canyon_rig 재빌드 v0009가 v0008과 형상·실루엣·카메라 리그 요약 모두 동일했다. factory_cut 사본 재빌드는 객체 인벤토리가 동일했고 결과도 결정적이었다. 노출 0.05 EV 차이는 v0003이 `output_size` 수정 이전(가로 기본 해상도로 계량)에 빌드됐기 때문이다.
알려진 한계: IFC 가져오기 미구현. trace는 회전 없는 부품만 읽는다. 관계는 평행이동만 한다. 부분 재빌드는 관계를 전부 다시 푼다. KS D 3502 표 값은 agent recall이며 사람이 원문과 대조해야 한다. truss 브리프의 최소 이격을 1.5 m로 고쳤지만 기존 truss 버전은 1.0 m로 빌드돼 있다.

### 후속: 기록된 자유 (같은 날)
- `deviations`(spec): 검사 항목 단위로 의도적 변형을 선언한다(factor / min_iou / waive + reason, 실제 대상의 큰 변경은 user_evidence). 리포트는 원래 기대값을 함께 남기고 `stylized`, `unused_deviations`를 표시한다. 프롬프트는 영상 모델에 "의도된 변경, 유지"라고 알린다. 수정 루프는 선언 집합이 바뀌면 새 기준선으로 본다(`intent_changed`).
- 탐색: 워크벤치 `variant_save/variant_restore`, 다중 프레임 프리뷰, `workbench compare`(변형안 × 프레임 contact sheet), `set_camera_rig`(빌드와 같은 리그 굽기 + 가드 보고). `commit --chosen-variant --why`는 changes.json과 repair 원장에 기록되고, 탐색은 버전과 예산을 쓰지 않는다.
- 실측:
  - winch 리그 3안 비교에서 close 안은 커밋 전에 가드 실패 10건이 드러났다. wide를 선택해 shot.json 리그에 기록했고 stale 증가는 0이었다.
  - 분해도는 선언하면 통과했고, 미선언이면 claim 2개가 실패했다.
  - 벽을 20% 높인 경우, 미선언이면 치수와 실루엣(0.788)이 실패하고 자동 복귀했다. 선언하면 통과했고 현재 버전이 됐다.
- 이 과정에서 버그 2건을 고쳤다.
  - datum 실루엣이 도면 밖으로 나간 형상을 잘라 IoU를 부풀렸다(같은 벽이 0.977로 나옴). 모델과 도면을 모두 덮는 캔버스에서 계산하도록 고쳤다. 기존 P-51D(0.9679)와 벽(0.9978) 값은 변하지 않았다.
  - 리그 샷에서 카메라 키 변형안은 빌드 리그가 덮어쓴다. `set_camera_rig`과 `CAMERA_KEYS_OVERRIDDEN_BY_RIG` 경고를 추가했다.
