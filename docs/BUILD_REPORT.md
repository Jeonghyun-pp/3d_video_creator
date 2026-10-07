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
.venv/bin/python -m studio job resume --project projects/harness_validation --job job_dd5e7ec81c27
.venv/bin/python -m studio job resume --project projects/harness_validation --job job_a7ac43087e1e
.venv/bin/python -m studio job resume --project projects/harness_validation --job job_969f9e0336b6
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

## 카메라 연출 엔진 (2026-10-05): 무브 · 속도 곡선 · 학습된 모션 스타일 · camera fit
목표는 레퍼런스를 복제하는 것이 아니라 "건축해부도 느낌"을 처음부터 만드는 것이다. 레퍼런스는 스타일 학습 데이터와 시험지로만 쓴다(프레임 미보관).

| 단계 | 구현 | 검증 |
|---|---|---|
| M1 속도 곡선 | `rig.timing`(burst_settle·ease_in_out·linear·points, monotone cubic), timed flythrough/orbit(`sweep_deg`), `scope: all`, flythrough `look_target` 가드, aim/lens 키 `ease` | 단위 16개. jet_canyon_rig 재빌드 v0010의 rig_hash와 프레임별 카메라 샘플이 v0009와 바이트 동일(core 파일은 변경) |
| M2 스타일 | `motion style learn/show/check`, 컷 검출, 24→30 pulldown 반복 프레임 제거, 샷 envelope 7특징 | archcutaway(17샷, 레퍼런스 1편 → `overfit_risk`) |
| M4 proxy | `flow_proxy`(레이캐스트 격자, 엣지 가중) | 블록아웃 17샷 렌더 MAD 대비 0.25 s 창 Spearman 0.835, 샷 0.718. 레벨은 ±50 % |
| M3 무브 | `camera.move`(waypoints 기본형 + dive_through/pass_between/descend_levels/push_in/crane/orbit_reveal, whip_in_deg, clearance 수리) → 빌드 시 rig로 컴파일, `camera_move_report.json`, `CAMERA_MOVE_FAILED` | 단위 11개(N+1: waypoints로 dive_through와 같은 경로 재현), smoke 9항목(개구부 통과, burst→settle, 막힌 경로는 가드로 거부, 없는 참조는 실패) |
| M4 fit | `camera fit [--apply]`: Blender probe 1회(진행도별 화면 흐름) → 호스트 Nelder-Mead로 타이밍 피팅, LEVEL_LOW/HIGH 힌트 | 단위 3개. 샷당 2~26 s |
| M5 블러 | `realism.target_blur_px`(라벨 앵커 2 px 상한 유지), 스타일 블러 기본값 | look_camera smoke 통과 |
| M6 스킬 | SKILL #6 확장 + 기계 검사 5b, `references/camera_rig.md` 무브 절(폴백 순서·금지), contract 명령 2개, `qa collect` 스타일 행(경고 전용) | test_skill_contract 통과 |

E2E (`projects/harness_validation/samsung_moves`, Cycles 16 spp 720×1280, 무료). 14샷은 move로, s05(틸트)·s08·s11(샷 내부 컷)은 기존 키 유지. fit 힌트에 따라 경로 기하를 2회 수정. 레퍼런스 대비 측정(pulldown 제거 후):

| 지표 | 기존 블록아웃 | 무브 엔진 | 계획 목표 |
|---|---|---|---|
| 샷별 움직임 비율 중앙값 | 0.289 | 0.469 | 0.8–1.25 ❌ |
| 비율 0.8–1.25 샷 수 | 2/17 | 3/17 | — |
| 1 s 창 패턴 Spearman(전체 합산) | 0.03 | 0.08 | ≥ 0.7 ❌ |
| 끝 정지 비율 중앙값 | 0.067 | 0.357 | 0.15–0.35 (약간 초과) |
| 스타일 범위 안 샷 | 2/17 | 8/17 | — |

- 개선: 머리 돌진 후 정지하는 형태는 재현됐다. 정지 비율이 0.07에서 0.36으로, 스타일 안 샷이 2개에서 8개로 늘었다.
- 미달 원인(측정값):
  - 스타일 이탈 10샷의 주원인은 `mean_mad` 레벨(10샷)과 `peak_t`(9샷)다.
  - fit이 예측한 레벨과 렌더의 비율은 중앙값 0.81, 범위 0.52~1.86이다. 보정의 ±50 %와 일치하고, 계통 편향은 확인되지 않았다.
  - 내용 밀도(엣지 수)가 레벨을 좌우하므로 타이밍만으로는 레벨을 맞출 수 없다.
- 관찰: s01은 돌진이 1.2 s 안에 도로를 통과해, 레퍼런스가 같은 시점에 유지하는 도시·타이틀 설정 컷이 사라졌다. 스타일 숫자는 의미(무엇을 오래 보여줄지)를 모른다.
- N+1 (`steel_joint_moves`, 레퍼런스 없음, push_in·orbit_reveal·crane 3샷):
  - 스타일 안 샷은 0/3이었다.
  - 기둥 하나와 빈 바닥뿐인 장면이라 레벨이 낮다(MAD 1.0~3.9).
  - orbit은 벽이 늦게 들어와 흐름이 끝으로 몰렸다(burst_share 0.14). probe는 이 흐름 분포를 예측했지만 fit 범위로는 바꿀 수 없었다.
- 회귀:
  - 단위 197개 통과.
  - smoke 18개 중 17개 통과. 실패한 `control_pass_smoke`는 기존 실패와 같다.
  - 렌더 지문 파일은 수정하지 않았다.
- 비교 영상(내부 전용): `samsung_reel_inputs/compare/compare_ref_old_moves.mp4`(레퍼런스 | 기존 | 무브).
- 다음 후보:
  - 설정 구간 유지를 위한 `hold_head_s`(의미 우선 정지).
  - 레벨을 경로 기하로 자동 조정하는 fit 변수(경로 배율).
  - 레퍼런스 2편 이상으로 스타일 재학습.

## 점진적 개구(reveal) + 생성모델용 구조 디테일 (2026-10-05, 후속)
**A. 도로가 서서히 뚫리는 연출 (무료)**
- **새 액션 `reveal`** (`studio/blender_ops/reveal.py`, build 전용)
  - 숨긴 boolean 커터를 액션 구간의 비율 `t`마다 위치·회전·크기로 키 잡아 구멍이 서서히 열린다.
  - `also_cut_overlapping`은 최종 커터 부피 안의 정적 닫힌 메시(차선 표시)까지 자르고, 움직이는 객체(차)는 건너뛴다.
  - `scene_tools.py`는 렌더 지문 대상이라 수정하지 않았다.
- **카메라 cue `cam-<mark>`**
  - `compile_move`가 timing 곡선으로 각 지점의 통과 프레임을 계산한다. 액션은 이를 음성 cue처럼 바인딩한다.
  - 그래서 카메라를 다시 피팅해도 개구가 도착 전에 끝난다. `studio_authored_animation`이 켜져 있어도 cue에 묶인 액션은 적용된다(저자 키 보존 확인).
- **`move.arrive` + 느린 머리 `timing.head_frac`**
  - 빌드는 위반을 보고만 하고, `camera fit`은 하드 페널티로 다룬다.
  - s01: 입구 통과 49 → 45 프레임(1.5 s 이후), early penalty 0. 이 샷의 스타일 리듬 특징은 의미 우선이라 범위를 벗어난다(의도된 상충).
- **프레임별 관통 가드 `passes_through_geometry`**(모든 리그, 기본 켜짐): 그 프레임의 형상에 대해 카메라 이동 선분을 ray cast한다.
  - 기존 rig smoke에서 실제 관통 2건을 새로 찾았다.
    - 반경 30 m orbit이 x=-70 벽을 통과했다. 이 경우는 이제 거부 테스트로 남겼다.
    - flythrough가 전투기와 겹쳤다. 테스트 카메라를 앞으로 옮겼다.
  - samsung_moves 14개 리그 샷과 jet은 위반 0이다.
- **s01 결과**(`compare/compare_s01_reveal.mp4`, 내부 전용)
  - 0.3 s에는 도로가 닫혀 있고 타이틀이 보인다. 0.9 s에 도로 가운데 구멍이 열린다. 1.4 s부터 카메라가 진입한다.
  - 레퍼런스와 다른 점: 레퍼런스는 높은 시점에서 단면을 내려다보고, 우리는 dive로 진입한다. 무브 선택의 문제다.
- **검증**: `tests/test_reveal_cues.py`(4), `tests/studio/reveal_smoke.py`(6항목: 닫힘→열림, cue 바인딩, 저자 키 보존, 늦은 개구 거부, fit의 arrive).

**B. 구조 디테일**
- **B1 측정 `qa_generative.richness`**
  - 지표: edge_fraction, fine_edge_fraction, coverage, distinct_tiles(추적 가능한 64 px 타일).
  - `control.json`에 저장된다. 하이브리드 구조 QA 기준을 control clay로 바꿨다(previs는 대체).
  - **결정적 발견: control pass의 clay 조명이 그림자를 드리워 실내가 검게 나왔다.** 무그림자로 고쳤다.

| control clay | s02 coverage / tiles | s03 coverage / tiles |
|---|---|---|
| 기존(그림자) | 0.15 / 5 | 0.02 / 4 |
| 수정 후, 상자 블록아웃 | 0.37 / 25.5 | 0.31 / 13.5 |
| 수정 후, exemplar 디테일 | 0.40 / 23 | 0.33 / 13 |
| 레퍼런스 렌더(질감 포함, 상한) | 0.10 / 44 | 0.08 / 32.5 |

- 레퍼런스는 강한 엣지가 상대적이라 coverage가 낮게 나온다. 비교에는 tiles와 fine edges(s03에서 레퍼런스의 1/3)를 쓴다.
- 디테일 추가는 엣지를 s02 +23 %, s03 +9 % 늘렸다. 하지만 반복 구조(계단·침목)는 추적 가능성을 늘리지 않았다.
- **B2 일반 메커니즘**(요소별 빌더 없음)
  - 경로 배열 `pattern: path`, 그룹 item(중첩)
  - 표 행의 `shape`(channel/angle/polygon) + KS D 3502 C/L, KS R 9106 50N 행. 모두 agent recall 값이며 사람 확인 필요.
  - 발광 재질(`emission_strength`, catalog `light_panel`)
  - fidelity count가 배열 사본 단위로 센다.
  - 경로 끝 부동소수점 버그를 고쳤다(계단 마지막 단이 중복되던 문제). 회귀 체크를 추가했다.
- **B3 exemplar 8종**(spec 데이터만, `building_elements` 프로젝트에서 fidelity 통과 후 promote)
  - stair, escalator(31단), glass_railing, beam_grid_ceiling, light_row, track, slab_opening
  - N+1 vent_duct는 코드 변경 없이 추가했다.
- **B4 무료**: `samsung_detail` s02/s03(같은 무브)
  - 에스컬레이터·난간·거더·조명·선로를 exemplar로 교체했다. 3,809 / 517 객체, 관통 0.
  - **B4 유료 A/B는 승인 대기, 호출 0.**
- **B5**
  - SKILL #7: 건축 요소는 exemplar로 만든다는 규칙과 근거 수치를 추가했다.
  - 기계 검사 5a(관통)·6b(control 구조)를 추가했다.
  - `building_elements.md`, `generative_safety.md`, `camera_rig.md`를 갱신했다.
  - `route lint` W5 경고: 잠정 하한 coverage 0.25, tiles 10(미보정).
- **회귀**
  - 단위 202개 통과.
  - smoke 19개 중 18개 통과. 실패 1개 `control_pass_smoke`는 기존 실패와 같다(평면 앵커).
  - look_materials smoke의 kind 수 고정값(9)은 "모든 kind 빌드 + 발광 여부"로 일반화했다.

## Blender 활용 보강 (2026-10-05, 지문 파일 갱신 전까지)
- 계획: 감사 3건과 전량 매핑 3건을 근거로 한다.
- 원칙: 공통 역할 태그 1개, 지문 파일은 마지막에 한 번만 갱신, 성능 변경은 결과 불변.
- 렌더 지문 파일(`render_frames`, `scene_tools`, `render_profile`, `jobs`)은 아직 무수정이다.

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| P0 회귀 장치 | `tests/run_smokes.py`(실행 방식은 AST로 판별), `tests/freeze_check.py`(지문·look·control·프로젝트 계약·jet 해시), `tests/rig_regression.py`(jet 재빌드 바이트 비교), `tests/build_bench.py` | 기존 실패 `control_pass_smoke`의 앵커를 평면 중심에서 세 면 모서리로 옮겼다(규칙은 그대로). control 선택을 지문 문자열 순서에서 생성 시각 순서로 바꿨다(`latest_control`) |
| P1 정확성 | 횡단보도 중복 제거, 생성기별 `random.Random`, VERTICAL 센서 맞춤, 날카로운 모서리 임계값 31°(상수 1개), 숨긴 물체 레이 통과, `guards.clip_auto`, workbench 키 있는 대상 경고 | 시드 변경 후에도 s04는 횡단보도 외 객체 위치가 전부 동일했다. 중복 횡단보도는 z-fighting으로 검게 보이던 문제였고 이제 흰색이다. flow proxy 보정값은 그대로다(0.835, k 1.478) |
| P2 역할 태그 | `scene_roles.py`(`studio_scene_role`: environment_shell, atmosphere, helper, clutter, light_fixture). look 바운드·clay·perfection·스케일, control 깊이 범위·렌더, 시야·관통·flow 레이, reveal에 적용. 엔진 helper는 자동 태그 | **조명 "버그"는 오진이었다.** 껍데기를 바운드에서 빼자 실내가 과노출됐다(EV 5.5–6.0, s02 스틸 비교). 그래서 껍데기는 바운드에 그대로 둔다(EV 3.35로 복원). `studio_keep_light`, `emissive_to_area`는 실험적 opt-in으로 남겼다 |
| P3 성능 | 배열 사본과 Samsung 키트가 메시를 공유한다(`mesh_data.unique_data` 복사 후 쓰기). bmesh 데이터 API, 클러터 병합, reveal MANIFOLD, 정적/동적 BVH 병합, `AnchorIndex` 일괄 샘플링, 키 일괄 삽입 | 빌드 시간: s04 65.9 s → 1.1 s, s01 101 s → 2.0 s, detail s02 21 s → 7.4 s. 메시 수: 1572 → 65. 스틸 동일. jet 샘플 바이트 동일. MANIFOLD는 캡 재질을 유지하고 평가 시간이 0.18 대 1.26 ms다. 메시 삭제 중복 버그와 트리 캐시 키 버그를 고쳤다 |
| P4 control v2 | clay 렌더에 법선·object index·Freestyle 선 패스를 함께 뽑는다(`kinds normal,lines,id`, opt-in). `richness_lines` 추가. 깊이는 기존 인코딩을 유지 | 원래 계획(단일 렌더로 통합)에서 바꿨다. 보정과 호환을 지키려고 기존 depth/clay를 유지했고, extras를 켜도 clay sha는 동일하다. 선 coverage 0.78 대 clay 0.40(detail s02). Freestyle 때문에 control 시간이 약 4배(7:52)라 opt-in으로 둔다. 무그림자 clay에서 IoU 보정을 다시 측정했다(grain 0.977, 5 % shift 0.435, 임계 0.5 유지) |
| P5 연출 | `shot.render.atmosphere`(Principled Volume `box` + spot 빛줄기, 역할 atmosphere), 블록아웃 Fast GI | 안개를 400 m 전체에 깔면 헤이즈만 생긴다. atrium box에 0.02로 두면 빛줄기가 보인다. Fast GI로 프레임 시간 절반(1.6 → 0.8 s), 과노출도 줄었다 |
| P6 미리보기 | 엔진 실측 | Workbench 0.02 s, EEVEE 0.46 s(headless 정상이나 조명 불일치), Cycles 1.6 s. layout 프로필에 Workbench를 넣는 건 P7(jobs.py)에서 한다 |
| P8 문서 | SKILL #4(역할 태그, 근거·대조), Phase 4 kinds, 기계 검사 6c(조명 EV), quick ref(대기, 미리보기), references `scene_roles.md`(신규)·look_photoreal·blender_craft·generative_safety·routing·camera_rig·building_elements·index, review toml, AGENT_BUILD_PLAN §4.6 | skill contract 통과 |
| P9 결합 | `tests/studio/combo_smoke.py`: 껍데기 + exemplar 계단(공유 메시) + reveal(MANIFOLD) + move + 실사 look(대기) + control v2 + 재빌드 결정론 | 통과. photoreal에서도 공유를 유지하도록 perfection의 매끈함 원래 상태를 메시 단위로 저장하게 바꿨다 |

- **회귀**: 단위 202, smoke 20/20 통과(처음으로 전부). jet v0010 샘플 바이트 동일. freeze_check 결과 지문 파일 무변경.
- **미반영(사유)**
  - motion_proxy numpy화: 이득이 probe 약 2배로 작아 보류했다.
  - EEVEE 기본 채택: 조명이 Cycles와 맞지 않는다.
  - 시뮬레이션: bake + pack 필수 조건만 문서화했다.
- **남은 단계 P7(지문 일괄 갱신, 실행 전 확인 필요)**
  - `blur_glossy`/`clamp_indirect`
  - layout 프로필 Workbench
  - `anchors_for_frame` 숨긴 물체 통과
  - cutaway MANIFOLD
  - 모든 렌더 캐시가 무효가 된다.

## 레퍼런스급 연출 보강 G0–G7 (2026-10-05): GN 배치 · 굽힌 시뮬레이션 · 설명 그래픽 · 마감
- 사용자 선택 1·2·3·5. 유료 호출 0. 렌더 지문 파일 무수정(freeze_check).

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| G0 사실 | `blender_facts_smoke.py` | 5.2 실측: GN 인스턴스는 호스트 bound_box·to_mesh에 안 잡힌다. `depsgraph.object_instances`로 보인다. ray_cast는 Object Info 인스턴스면 호스트, 컬렉션 인스턴스면 원본을 돌려준다. 시뮬레이션 존 PACKED와 리지드바디 메모리 캐시는 저장 후 임의 순서 프레임에서도 같다. Bullet은 모양이 바뀌는 충돌체(reveal로 뚫리는 도로)를 보지 못한다 |
| G1 인스턴스 인식 | `scene_geometry.py`(records/merged/instance_boxes/is_time_dependent). 카메라 클리어런스·관통, clip far, control 깊이 범위, look 바운드, perfection 정지 판정에 적용. 역할 scatter, scatter_source, graphic, simulated | jet 샘플 바이트 동일(객체별 트리 유지 + 인스턴스 트리만 추가) |
| G2 scatter | `scatter.py`(GN: 면 분포 또는 점 → 컬렉션 인스턴스, 고정 시드). Samsung 가로수·터널 링·침목·잔해, s03 작업자 무리 | s12 객체 344 → 48. 같은 인자면 같은 배치(digest) |
| G3 시뮬레이션 | `simulate` 액션(rigid_debris, dust), cue 바인딩, 빌드 때 bake, `SIMULATION_NOT_BAKED` 게이트. reveal로 뚫리는 충돌체는 오류로 거부 | s01: 파편 120개와 먼지 1500개가 도로 개구부로 쏟아진다. 빌드 2.0 → 8.9 s, .blend 16 MB(캐시 내장). 첫 시도(0.25–0.8 m)는 모션블러에 묻혀 거의 안 보였다. 0.6–1.6 m, 시작 −14프레임으로 키웠다 |
| G4 설명 그래픽 | `shot.graphics`(arrow/dimension/outline/highlight/draw_line) → GP v3, 본 렌더·control에서 숨김. `graphics render`(EEVEE, 투명, 메시 holdout) → edit에서 자막·라벨 아래 합성. 생성 클립은 anchors_2d 없으면 `GRAPHICS_UNSUPPORTED` | E2E에서 버그 2개 발견·수정: (1) role graphic인 작가 메시(3D 타이틀)가 레이어에 섞였다 → `studio_graphic_layer` 표시만 렌더. (2) GP 레이어가 조명을 받아 선이 거의 검게 나왔다 → unlit + sRGB→linear. smoke에 회귀 검사 추가. edit 합성 단위 테스트 추가 |
| G5 마감·베이크 | `explainer_finish`(블룸 1.1/0.28, 비네팅 0.24, soften 0.15)를 `style.look.compositor`로 지정. `look_bake.py`(opt-in `passes.bake`): 색·금속·거칠기는 emission 경유 정확 bake, 법선, LOD는 최근접 거리로 고정 | 베이크 s02: 40개, 화면 ΔE76 중앙값 0.0 / p95 1.07. 그러나 프레임 시간 13.45 → 12.68 s(−6 %), 빌드 +606 s. 기준(−30 %) 미달이라 **opt-in만** 둔다 |
| G6 문서 | SKILL #7(배경은 scatter), Phase 2(시뮬레이션·그래픽 규칙과 Forbidden), 기계 검사 4b–4d. references `scatter_simulation.md`·`explainer_graphics.md` 신규, scene_roles·look_photoreal·generative_safety·index 갱신. review toml, AGENT_BUILD_PLAN | skill contract 통과(`graphics render` 명령 포함) |
| G7 결합 | `combo2_smoke.py`: 군중 scatter + reveal + 파편(cue) + 그래픽(cue) + finish + control v2 + 재빌드 결정론 | 통과 |

- **회귀**: smoke 26/26, 단위 204 통과. jet 바이트 동일. freeze_check는 렌더 지문 파일 무변경(control·look 입력은 의도된 변경).
- **E2E**(samsung_moves, Cycles review, GPU)
  - 비교 영상 `projects/harness_validation/samsung_reel_inputs/compare/compare_s01_gfx_sim.mp4`(레퍼런스 | 이전 v0007 | 신규 v0011 + 그래픽), `compare_s03_crowd_outline.mp4`.
  - s01: 하강 화살표, 개구부로 떨어지는 파편·먼지, 내부의 층고 치수선(노랑)이 보인다.
  - s03: 기둥 사이로 작업자 무리, 점검 기둥의 외곽선과 화살표. 외곽선이 가늘다(0.03 m). 앞 기둥에 가려 한쪽 윤곽만 보인다.
- **남은 것**: 그래픽 숫자(치수값) 라벨은 아직 2D 라벨로 직접 지정해야 한다. 기존 P7(지문 일괄 갱신)과 유료 하이브리드 A/B는 사용자 결정 대기.

### 후속: 무료 보정 3건 (같은 날)
- **치수값 자동 표시**: dimension에 `text: "auto"`를 주면 측정 길이("12.9 m")를 그래픽 레이어의 중점 옆에 찍는다(스타일 폰트, 폭의 4 %, 어두운 외곽선). 중점이 화면 밖이거나 형상 뒤면 숨긴다. s01 첫 배치는 중점이 기둥 `st.col3.1.7` 뒤라 한 번도 안 보였다. 그래서 개방 공간(x 4.2)으로 옮겼고, 77–92프레임에 표시된다. smoke에 검사를 추가했다.
- **s03 외곽선**: 같은 열의 앞 기둥에 가리던 `hall.col.1.6` 대신 `hall.col.0.6`를 두께 0.08 m로 그렸다. 기둥 전체 윤곽이 보인다.
- **색감 차이 원인**: v0007 대 v0011의 렌더 관련 설정 차이는 `cycles.use_fast_gi`(P5에서 Samsung 블록아웃에 켬) 하나뿐이었다. 같은 장면을 켜고 끈 렌더로 확인했다. Fast GI는 간접광을 황혼 하늘색으로 근사해 화면 전체가 차갑고 어두워진다. 대신 프레임 시간이 3.8 s 대 6.1–9.0 s다. G 작업(시뮬·그래픽)은 색에 영향이 없다. 블록아웃 색은 최종 색이 아니라는 주석을 samsung_lib에 남겼다.
- 회귀: smoke 26/26(blender_smoke는 GPU 렌더 대기와 겹쳐 한 번 시간 초과, 렌더 후 재실행 통과), 단위 204, jet 바이트 동일.

## 유료 하이브리드 A/B (2026-10-05, 사용자 승인 "7달러 상한으로 해봐")
- **설계**
  - 같은 카메라와 프롬프트(룩 단어만, 텍스트 금지)로 거친 블록아웃(`samsung_ab_coarse`)과 exemplar 디테일(`samsung_ab_detail`)을 비교한다. 대상은 s02와 s03이다.
  - Wan VACE: depth control 입력, 4클립.
  - Seedance 2.5: 더 나은 쪽(detail)에 clay previs 입력, 2클립.
- **비용**
  - 확정 청구 $3.29: Wan 4건 $1.32, Seedance s02 $1.97.
  - 미확인 2건: Wan coarse s02 take1 $0.29(fal 쪽 "Error processing request", 청구 여부는 대시보드로 확인해야 함), Seedance s03 $2.19(POST 중 SSL EOF로 영수증 없음, 엔진이 재전송을 막음).
  - 최대 합계 $5.77로 상한 $7 이내다.
- **구조 QA**(control clay 대비 edge IoU 중앙값, 게이트 0.5): 6개 모두 불합격.

  | 샷 | Wan coarse | Wan detail | Seedance detail |
  |---|---|---|---|
  | s02 | 0.160 | 0.121 | 0.065 |
  | s03 | 0.229 | 0.172 | 미확인 |

- **눈으로 본 결과**
  - **s02**: detail이 낫다. 에스컬레이터와 유리 난간, 선로가 제자리에 생긴다. coarse는 없는 대각 부재를 지어낸다.
    - Wan: 극적인 조명과 실사감이 좋지만 층 높이와 위치가 흐른다.
    - Seedance: 배치 의미를 가장 잘 지키지만 거의 회색 단색이다. 4초를 생성해 2.9초에 맞추므로 타이밍도 어긋난다.
  - **s03**: 두 쪽 다 매우 실사적이다. 기둥과 보 정렬도 잘 맞는다(edge 오버레이로 확인). IoU가 낮은 주원인은 모델이 추가한 천장 루버와 바닥 원호선이다. acceptance의 "추가 금지"에 해당하므로 게이트 판정이 맞다.
- **결론**
  - 블록아웃 디테일은 IoU를 올리지 못했다. 각자 자기 clay 대비라 디테일 쪽 edge가 더 많은 탓도 있다. 대신 생성 결과의 의미(부재 종류)를 맞게 만든다.
  - 지금 조건(depth만 쓰는 Wan, reference 모드 Seedance)으로는 구조 게이트를 통과하는 하이브리드가 나오지 않는다. 라벨과 그래픽을 얹을 수 있는 컷(anchors_2d)도 생기지 않는다.
  - W5 richness 하한은 모든 조건이 불합격이라 이 데이터로는 보정할 수 없다. 임시값을 유지한다.
- **다음 후보**(유료, 미실행)
  - Wan의 depth+canny 결합 입력, 또는 구조 유지력이 더 높은 모델.
  - Seedance는 출력 길이(4 s 이상)에 맞춰 샷을 늘린다.
  - 실사감은 하이브리드 대신 Blender photoreal look을 쓰고, 생성은 분위기 컷에만 쓴다.
- **비교 영상**(레퍼런스 미포함): `samsung_reel_inputs/compare/ab_wan_s02.mp4`, `ab_wan_s03.mp4`(clay coarse | Wan coarse | clay detail | Wan detail), `ab_s02_clay_wan_seedance.mp4`.

## 생성모델 연동 개편 H0–H9 (2026-10-05): 컷 역할 · 지표 분리 · 생성 전 사람 검토 · 구조화 프롬프트 · 유료 호출 안정성
- 계획: 에이전트 3개의 코드 전량 매핑. 유료 호출 0. 렌더 지문·control·look 입력 무변경(control 재사용 fingerprint 동일 확인).

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| H0 측정 | 보존율(previs 윤곽이 결과에 남은 비율)·추가율(결과에만 있는 윤곽)을 합성 세트와 A/B 클립으로 측정 | **어떤 임계값도 분리 못 함.** s03 Wan 보존 0.29(저해상 0.40), 같은 clay를 5 % 옮긴 것 0.55(0.67). 허용오차 2–12 px, 저해상 비교 모두 같은 결론. 그래서 mood는 수치 게이트 없이 경고만 두고, 사람의 테이크 선택이 게이트다. 이전에 "s03은 정렬이 맞는다"고 본 눈대중은 지표로 뒷받침되지 않는다 |
| H2 지표 | `qa_generative._overlap` → iou·preservation·extra(프레임별, 중앙값·최소·최대) 기록, 게이트는 기존 IoU+앵커 그대로 | 합성 테스트: restyle 보존 ↑ 추가 0, 이동 보존 ↓, 격자선 추가는 보존 1.0·추가 ↑ |
| H1 역할 | `route.role` explain\|mood, `generative/policy.py`(policy_for·judge) 하나로 생성·선택·편집·lint가 판정. mood+라벨/그래픽 `ROUTE_ROLE_CONFLICT`. 불합격 explain 테이크는 편집에서 제외(Blender 폴백 + reject_route 경고), 생성 시 flicker·morph·text도 실행해 기록 | **구조 불합격 클립이 그대로 쓰이던 빈틈을 막았다.** A/B 5개 클립: explain 사용 불가, mood 사용 가능(경고 1개씩) |
| H6 안정성 | 요청 디렉터리 잠금(`GENERATION_IN_PROGRESS`), 무료 GET 재시도(5회, 2–30 s), FAILED/결과 5xx → `remote_failed` + ledger `unknown`(지출로 계산), POST 중 네트워크 오류 → UNKNOWN, `generate reconcile`(사용자 원문으로 정리) | 이번 A/B의 실제 사고 3종(결과 500, 폴링 중 연결 끊김, POST 중 SSL EOF)을 테스트로 재현 |
| H3 HITL | `generate review` 시트(Blender 프레임 3장, 참고 이미지, 역할·모델·비용, 프롬프트 항목과 최종 프롬프트). `request_fingerprint`(엔드포인트·프롬프트·입력 해시·seed·take·길이·trim·retime·패딩·어댑터 인자) = 생성 캐시 키 = 승인 묶음. `route approve --review --user-words`(에이전트 래퍼 문구 거부, `--agent-note` 분리, `--shot all`). 승인 후 변경 → `ROUTE_APPROVAL_STALE`. 수정 이력은 `--after --user-words`로 시트에 남음 | 승인 후 seed 한 값 변경만으로 생성 거부 확인. 실제 samsung_ab_detail 시트 생성 |
| H4 프롬프트 | `prompt_spec`(look·keep·add·forbid·mapping_overrides). subject spec 없이도 `subjects_index.json`(빌드가 기록: spec으로 만든 모든 부재의 정체·특징·빌더, 화면에 나온 프레임)에서 "형태 = 부재" 문장 자동 생성, 화면 밖 부재 제외. lint W7(120단어), E6(add=keep) | 결합 smoke: 화면 안 계단만 들어가고 카메라 뒤 에스컬레이터는 빠짐 |
| H5 입력 | Seedance 4초 미만 입력은 마지막 프레임 유지로 패딩(꼬리는 retime이 버림), 참고 이미지 출처 게이트(`REFERENCE_NOT_CLEARED`: reference/internal 경로·style.reference_paths 해시 차단, 프로젝트 renders·generated·stills 또는 cleared 자산만), W8 참고 이미지 없음, `generate still` | 2.93 s → 4.0 s 패딩, 출력 88프레임. 예상 비용이 입력 4 s 기준으로 바르게 올라감($1.97 → $2.27) |
| H7 선택 | `generate select --user-words --additions present:…,absent:…`, 불합격 거부, 판정 없는 add 항목 경고 | smoke에서 absent 추가물 경고 확인 |
| H8 문서 | HARD-GATE 1(시트·원문·reconcile), CRITICAL #2 보강, #11 컷 역할, Phase 4 순서, 기계 검사 7a–7d, routing.md 역할 표, generative_safety 순서·템플릿·사후 절차, review/implementation toml | skill contract 통과(신규 명령 3개 포함), Part E 점검 |

- **회귀**: 단위 218 통과, jet 바이트 동일, freeze_check 렌더 지문 무변경, contracts 변경은 harness_validation만, smoke 27개(hitl 신규).
  - **간헐 실패 2건(미해결)**: 전체 실행에서 한 번씩 `sim_bake_smoke`, `blender_smoke`(렌더 워커가 6/6 프레임 후 interrupted로 보고)가 실패했다. 둘 다 단독 재실행 시 반복 통과했다. 원인은 찾지 못했고, blender_smoke 쪽은 jobs.py(지문 파일, 미수정) 경로다.
- **기존 승인 영향**: 이전에 승인된 하이브리드/생성 샷(otis, jet, samsung_ab)은 시트 묶음이 없어 다음 생성 때 `ROUTE_APPROVAL_STALE`이 난다. 의도된 동작이다(시트를 보여주고 다시 승인).

## 환경 키트 + 역할별 생성 입력 + 룩 밀도 측정 E0–E9 (2026-10-05, 모든 제작 공통)
- 계획: 에이전트 3개의 코드 매핑. 유료 호출 0. 렌더 지문·control·look 입력·fidelity CODE_FILES 무변경(freeze_check 목록이 이전과 동일).

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| E0 룩 측정 | `studio/look_style.py`(`look style learn/show/check`). 밝은 점/MP, 휘도 p50·p95, 채도, 어두운 영역 디테일을 8프레임 사분위로 계산하고 sha256 출처만 저장한다. `qa collect`·생성 manifest에 경고만 연결 | 레퍼런스 0–3.1 s: 점 750/MP, p50 64, p95 172, 채도 0.23, 어둠 디테일 0.075. 박스 도시 s01은 채도 0.10·어둠 디테일 0.016·p95 129로 낮았다. 점 수는 오히려 많았다(2473, 차선·타이틀) |
| E1 재질 | spec 재질에 `emission_color_srgb`, `shader {window_grid, emissive}`, `scene_role` 추가. `env_materials.window_grid`: 월드 좌표 격자와 셀 해시로 켜짐, 색온도, 밝기 ±40 %를 정하고 건물마다 다르게 | lit_ratio 0.3 → 측정 0.356, 0.7 → 0.738. 재렌더는 바이트 동일 |
| E2 키트 | `tower_block`, `streetlight`, `car`, `sign_panel`, `rooftop_unit`, `lane_dash` spec 데이터(`environment_kits_inputs`) → fidelity 통과 → promote(라이브러리 18종) | 6종 모두 통과. 차·가로등 치수는 에이전트 기억이므로 사람 확인 필요 |
| E3 채우기 | `env_fill_core`(순수 기하, 단위 테스트)와 `env_fill.along/blocks/on_top/on_front/traffic`. scatter 점 속성 `rot_z/scale/scale_xyz/source_index`와 `weights` 추가(하위 호환). 원본의 역할은 `studio_source_role`로 보존 | 기존 scatter digest 동일(gn_scatter, combo2). 차량은 개구부를 지나지 않음 |
| E4 조합 | `env_kits.street`(도로, 차선, 인도, 가로등, 가로수, 필지 건물, 옥상, 간판, 교통)와 `presets.json`(urban_dense, suburban, 낮 외벽). `environment_report.json` | 600 m 10차로: 0.2 s 빌드. 건물 124, 가로등 168, 간판 136, 차 144(인스턴스) |
| E5 Samsung | `samsung_lib.city`를 street 래퍼로 교체(44 m 도로, 구멍 벽, 중앙선, 횡단보도만 남김). s01, s04 재빌드 | 게이트 0, 관통 0. 객체 s01 1,264 → 796, s04 1,143 → 676. 빌드 s01 8.5 s(시뮬 포함), s04 1.3 s |
| N+1 | `env_kits_smoke`: 곡선 2차로 교외 주간 거리를 코드 수정 없이 생성 | 통과. 낮에는 창이 발광하지 않음 |
| E6 입력 | `generate inputs`(explain: control clay+depth, mood: 룩 렌더 review/final). W9(mood에 clay), W10(낡은 입력). 시트에 입력 표. W5는 explain에만 | `samsung_mood_s01` 데모: 입력 자동 선택 → 프롬프트 → 시트($2.27). 생성은 안 함 |
| E7 밀도 경고 | `style.look.look_style`, `shot.render.look_style` → qa collect 행, 생성 manifest `qa.look_style`, policy 경고 | 단위 테스트(점 개수를 아는 합성 영상, ±3) |
| E8 문서 | SKILL #7(키트·근거 수치·Bad/Good), #11(mood 입력은 룩 렌더), Phase 4 순서, 기계 검사 4e·7e, `environment_kits.md` 신규, scatter/generative/building/index 갱신, review toml | skill contract 통과 |

- **s01 결과**(레퍼런스 스타일 대비, 항공 구간 0–1.4 s)

  | 지표 | 이전 | 이후(키트 + photoreal_night) | 판정 |
  |---|---|---|---|
  | 채도 | 0.10 | 0.21 | 범위 안으로 들어옴 |
  | 어둠 디테일 | 0.016 | 0.059 | 범위 안으로 들어옴 |
  | p50 | 67.5 | 76.5 | 범위 안 유지 |
  | p95 | 129 | 251 | 여전히 범위 밖(이전 129 미달 → 이후 251 초과) |
  | 밝은 점/MP | 2473 | 1572 | 여전히 범위 밖(초과) |

  - p95는 노출을 −1.7 EV 낮춰도 232에 머문다. 원인은 흰 3D 타이틀과 횡단보도다(키트 문제 아님). 창 발광을 1.2로 낮춰도 자동 노출이 되돌려 효과가 없었고, 채도와 점이 떨어졌다. 그래서 2.6으로 되돌렸다.
  - 비교 영상: `samsung_reel_inputs/compare/compare_s01_citykit.mp4`(레퍼런스 | 이전 블록아웃 | 키트 + photoreal_night + 그래픽).
  - 남은 차이
    - 실내 구간이 야간 프리셋 하나로는 어둡다(한 샷에 외부와 실내가 섞임).
    - 하늘 위쪽에 야간 HDRI의 녹색이 보인다.
    - 파편이 낙하 전 도로 위에 더미로 보인다.
- **회귀**: 단위 228, smoke 28/28(이번 전체 실행은 간헐 실패 없음), jet 바이트 동일.

## F0–F11 s01 피드백 5건 수정 (2026-10-05, 유료 호출 없음, frozen 파일 무변경)

순서: 리서치 → 검증(사본 실험) → 코드 매핑(에이전트 3) → 계획(`~/.claude/plans/vast-questing-naur.md`) → 구현. 모든 수정은 엔진 기능 + 게이트로 넣었고, Samsung은 그 기능을 쓰는 첫 사례다.

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| F0 경로 | centripetal Catmull-Rom, 불변식 `MOVE_PATH_LOOP`(웨이포인트 사이 역행 금지), dive `inside`를 슬래브 아래 ≥ 1.5 m로 | v0018의 입구 루프(y 72.05→71.74, z +0.78/−1.50, f46→47 shift 점프)가 재현 경로에서 역행 0 |
| F1 프레이밍 | `move.framing {horizon_v, hold_until_cue, blend_frames}`. 리그가 피치로 지평선을 고정하고, two-point(frozen)는 그 피치를 shift로 바꾼다. 게이트 `framing`(유지 중 0.02 초과) | 닫힌 식과 투영 오차 1e-6. s01 지평선 0.19 → 0.40 |
| F2 구도 스타일 | `composition_style.py`(`composition style learn/show/check`, vp_v·sky_share·skyline_c, 해시만 저장), qa collect 행(`render.composition_span_s`) | 레퍼런스 0–1.4 s: vp 0.41, 하늘 0.23, 스카이라인 0.33(검증 값과 일치) |
| F3 하늘 | `street(sightline=...)`: 시작 카메라에서 보이는 lot 높이를 스카이라인 목표 아래로 제한(닫힌 식). 원경 `skyline` 행 | s01 lot 15개 제한. 하늘 비율 0.063 → 0.206(스타일 범위 안) |
| F4 타이틀 | `shot.titles`(2D, recede PCHIP, alpha 중심 앵커, premultiplied 부분 박스 리사이즈), `TITLE_OUT_OF_SAFE`, qa `title_safe_area`. 리그 게이트 `graphic_in_frame`(3D 텍스트가 프레임 경계에 2프레임 넘게 걸리면 실패) | 앵커 지터 < 0.35 px, 크기 단조 감소. s01 3D 타이틀 제거 |
| F5 리빌 | `_strip` 면 감김 수정(inside-out였음), signed-volume 게이트, 엣지 0 메시 `_manifold` 공허 통과 수정, GN 호스트에는 cap 슬롯 생략, 불리언은 첫 컷터 키까지 꺼진 상태로 키 | reveal smoke: 시작 전 구멍 0, inside-out 거부. s01 리빌 대상 16개 모두 부피 > 0 |
| F6 화면 화살표 | `graphics[].space: "screen"`(카메라 부모 GP, 프레임마다 view_frame, LINEAR 페이드, fan 헤드), `GRAPHIC_ILLEGIBLE`(길이, 머리 비, FOE 거리, 흐름 각) | graphics smoke: 페이드 알파 단조, 비행선 위 화살표 거부. s01 화살표 FOE 302 px, 흐름 57° |
| F7 단면 | `section.py`(흙 strata, 절단면 poché, 층별 천장등 + LED 열), 무브 `section_push`, `front_cutter`. 레퍼런스는 구멍 다이브가 아니라 역 앞 지면을 단면으로 자르는 연출임을 프레임으로 확인 | section smoke 통과. s01: 1.6 s에 흙 속 5개 층, 2.3 s 단면 근접, 3.0 s 내부 |
| F8 거리 디테일 | 차로 3.4 m(나머지는 갓길 + 경계선), 이중 황색선, 교차로(2밴드 횡단보도, 정지선, 5 m 화살표, near-side 4구 신호등, 보행 신호등), 가로등 팔 4.6 m + 등마다 광폭 스팟, 아스팔트 0.34, 겨울 가로수 6종 8 m, 보행자 4포즈(기둥 0.6 m 이격), 버스·택시·정류장, `along(avoid)`, `bare` 구간, 창 색 white_mix. exemplar 16종 spec 데이터 → fidelity 통과 → promote | env_kits smoke 18항목 통과. s01: 교차로 2, 신호 8, 보행 신호 16, 사람 125, 가로수 166, 스팟 54 |
| F10 먼지 | dust 입자가 `ceiling_z`(기본 영역 상단)를 넘으면 삭제 | sim smoke: 상단 ≤ 1.04 m |
| F11 문서 | SKILL #6(section_push), #12(Readable frame), Phase 2 타이틀, 기계 검사 4f·4g·4h. references: camera_rig, explainer_graphics, environment_kits 갱신, `titles.md`, `section_staging.md` 신규, index | skill contract 통과 |

- **s01 결과**: `samsung_moves` s01 v0024(`section_push`). 비교 영상은 `samsung_reel_inputs/compare/compare_s01_fix.mp4`(레퍼런스 | v0018 | 이번).

  | 지표 | 레퍼런스 | v0018 | 이번 |
  |---|---|---|---|
  | 하늘 비율(0–1.4 s) | 0.22–0.23 | 0.063 | 0.206 |
  | 지평선 v(리그 보고) | 0.39–0.41 | 0.19 | 0.40 |
  | 중앙 스카이라인 | 0.33 | 0.016 | 0.17 |
  | p95 | 범위 안 | 159 | 219.5(초과) |
  | 채도 | 0.21–0.24 | 0.32 | 0.36(초과) |

  - 중앙 스카이라인이 낮은 원인은 HDRI 하늘에 비친 건물·나무 실루엣이다.
  - p95와 채도 초과의 원인은 창 과노출이다. 창 발광을 2.6에서 1.4로 낮춰도 미터가 EV를 1.45에서 1.8로 되올렸다.
  - 둘 다 F9(look_inputs frozen 배치: 카메라 전용 하늘, 컴포지터 노출 세그먼트)를 승인받아야 고칠 수 있다.
- **남은 것**
  - F9 승인 대기.
  - `camera_moves_smoke`에 얇은 개구부 케이스를 추가하지 않았다(단위 테스트로 대체).
  - 렌더 run 누적 상한에 걸려 `samsung_moves` `render_wall_minutes`를 120에서 240으로 올렸다(harness 프로젝트).
- **회귀**: 단위 246 통과. smoke reveal·graphics·env_kits·section·sim_bake·camera_moves·combo·combo2·hitl·look_bake 통과. jet 리그 바이트 동일. frozen 파일 23개 해시 무변경.

### F9 룩 배치 (사용자 승인 "승인할게 진행해", look_inputs frozen 변경)
- `look_lighting`:
  - `camera_sky`(프리셋 데이터): 카메라에만 노을 그라데이션이 보이고, HDRI는 조명만 맡는다. 강도는 level × 2^−EV로 노출과 무관하게 일정하다.
  - `meter_highlight`: 피사체 상위 0.5 %가 노출 후 `max_linear` 2.0을 넘지 않게 EV에 상한을 둔다.
  - `exposure_keys`: 키 프레임마다 따로 계량한 뒤, 차이를 `look_camera` 컴포지터 Exposure 노드에 Bezier 키로 건다. control·graphics 패스는 컴포지팅이 꺼져 있어 영향받지 않는다.
- `look.py`:
  - `inputs_hash`에 `exposure_keys`를 넣었다.
  - `build_scene`이 `camera_cues`를 job에 넘긴다.
  - 스키마에 `shot.render.exposure_keys`를 추가했다.
- **s01 v0026**
  - 기준 EV 1.7. 단면 구간 −0.45, 내부 −1.45 EV.
  - 하늘의 HDRI 건물이 사라지고 노을 그라데이션으로 바뀌었다.
  - 내부 과노출이 해소됐다.
- **지표**(0–1.4 s)

  | 지표 | 값 | 판정 |
  |---|---|---|
  | vp_v | 0.39 | 범위 안 |
  | skyline_c | 0.31 | 범위 안 |
  | sky_share | 0.29 | 레퍼런스 0.23보다 높음 |
  | p50 | 49–54 | 범위 안 |
  | 어둠 디테일 | — | 범위 안 |
  | p95 | 214–219 | 초과(창) |
  | 채도 | 0.42–0.55 | 초과(파란 하늘 그라데이션이 원인) |

- **다음 보정 후보**(데이터만, 코드 아님)
  - 하늘 stop 채도 낮추기.
  - 창 `max_linear` 또는 발광 낮추기.
- **회귀**: 단위 테스트 전부 통과, look 스모크 4/4 통과.

## 10-06 밤샘: 공통 엔진 (A 재현성, B 채움 계획, C 머무름, D 보정값, E 생성 준비; 렌더 없음)

| 블록 | 내용 | 측정·검증 |
|---|---|---|
| A | bootstrap, SETUP, AGENTS.md, Pretendard OFL 폰트, `font_file`, 에셋 메타데이터 상대경로(`read/write_manifest`), exemplar·DXF 경로 저장소 기준, author 동반 모듈·사이드카 해시, `examples/samsung_cutaway`, `project from-example`, `examples/kits`(템플릿), `tests/fixtures`, freeze_check baseline 없을 때 안내 | 새 클론: 테스트 통과, s01 빌드가 v0026과 동일. 단위 246 → 258, jet 동일 |
| B | `fill_brief` 스키마·CLI·게이트, `level_layout`, exemplar 3종(fidelity 통과·승격), Samsung 층 선언·기둥 양보·detail 사이드카, SKILL #13·검사 4i·`fill_brief.md` | fill 스모크 8/8(N+1 지하주차장), s01 v0028–v0030 게이트 통과(미승인 경고) |
| C | `timing.dwell` 시간 왜곡, `move.dwell` 해석, fit·probe 처리, `motion style learn --range`, dwell 측정 | 단위, jet 동일. 레퍼런스 0–3.1 s: burst_share 0.21, peak_t 0.8, dwell 0 → s01 미적용. camera fit(burst_settle만) 목적함수 85, LEVEL_LOW 0.29 → 미적용 |
| D | `night_city` max_linear 0.6, 하늘 stop 채도 −35 % (F9 승인 파일, 데이터만) | 계량 EV 1.45 / −0.5 / −1.6 (렌더 측정은 아침) |
| E | s01 경로 제안 + prompt_spec, 형상 매핑 brief 순위·60단어 예산 | 606 → 154단어, route lint: E4(견적 없음), W3(승인 대기), W5(control 없음), W7(길이), W8(룩 레퍼런스 없음) |

- **계획과 다른 점**
  - 채움 계획 승인은 렌더 프로필을 구분하지 않고 모든 렌더 전에 요구한다. 렌더 게이트 함수가 프로필을 모르고, `jobs.py`는 수정 금지라서다.
  - dwell은 레퍼런스 측정에서 근거가 나오지 않아 s01에 넣지 않았다.
- **수정 금지 파일**: 바뀐 것은 F9로 승인한 look 4개(`look.py`, `look_camera.py`, `look_lighting.py`, `lighting_presets.json`)뿐이다. render_fingerprint·control·fidelity는 그대로다.

## 10-06 범용화 1–2단계: 믿을 수 있는 기반 + 게이트 정리 (계획 `docs/ENGINE_GENERALIZATION_PLAN.md`)

사용자 결정: samsung 내레이션은 설명용으로(수치 제거), 수정 빌드는 authored 체크포인트(수정 금지 look·jobs는 P7과 묶음, 렌더 캐시 1회 무효화),
승인 방식은 지금처럼(원문 검사), 새 프로젝트 게이트 기본값은 explain-strict.

| 블록 | 내용 | 검증 |
|---|---|---|
| 0 사실 | samsung 내레이션·화면 글자에서 실존 시설 결함·발표·일정 주장 제거, 문장마다 illustrative 표시 | `facts check` 문제 0 |
| 1A 작은 버그 13건 | build_scene `compare` 덮어쓰기, generative 편집 크래시, subject lint 루프 변수, fill 내레이션, fal 폴링 HTTP 오분류, CLI 종료 코드·상태 갱신, MCP·워크벤치 서버 견고화, sys.path 오염·`shot_qa` 패키지화, fc-match·Blender 게이팅, route plan 결정 키·approve 순서, 편집 캐시(그래픽·titles 폐포), from-example(run·원자적·누락 파일), 되돌리기(메타데이터·spec 복원) | 버그마다 실패하는 테스트 먼저 |
| 1B 유료 경로 | `asset image3d`를 리뷰 시트·원문·예산 경로로, 가격표를 `fal_client.PRICING` 하나로 | 승인 없는 image3d는 호출 0, 가격 일치 테스트 |
| 1C 수정 빌드 | `authored.blend` 체크포인트(수정 = 새 빌드), look 항상 적용·코드 폐포 해시·렌즈 왜곡+라벨 치명, 게이트를 look 뒤로(리그 최종 판정·fill·fidelity), cue 고정점(수리 후 재계산, simulate 재적용 안전), 모션 스타일 블러 | `revision_equivalence_smoke`(새 빌드/base+빈 패치/새 빌드 동일, 옛 코드는 그래픽 중복으로 실패), cue 스모크(옛 코드 실패), jet 리그 바이트 동일 |
| 1D 렌더(P7) | 워커 분리(`render_worker.py`만 지문), 제출·재개 공통 게이트, CPU 폴백 별도 지문, layout = Workbench, 라벨 가림 레이 규칙, cutaway MANIFOLD, 광경로 보고 | 단위 테스트, 스모크 31/31 |
| 1E 사실 | `studio/facts.py`, `narration.sentence_claims`, candidate·납품·facts 승인 게이트 | 단위 테스트 |
| 1F E2E | `examples/e2e_minimal` + `e2e_smoke`(from-example → 빌드 → layout 렌더 → 생성 컷(가짜 응답) → rough 편집 → QA) | 새 클론: bootstrap 0, 단위 OK, E2E OK |
| 2 게이트 | `studio/gates.py` 완화 목록(기본 거부), `policy.strictness`(기본 explain-strict), 해설 컷 사용자 선택(`explain_generated`), 통과 빌드 유지, 매핑 누락 보고, SINGLE_VARIANT 삭제, SKILL 13→6 룰(원문은 `rule_rationale.md`) | `test_gates`, fill 스모크 look-first 경고 |

- **덤으로 찾은 것**: 작성자 옆 `author.py`가 수정 패치를 덮어써서 수정 빌드가 원래 작성자를 다시 돌리고 있었다(preserve_smoke가 사실상 검사를 안 하던 원인). workbench_smoke는 픽스처에 .blend가 없어 실패 상태였다. factory_import_smoke는 상대경로 매니페스트를 cwd 기준으로 열고 있었다.
- **해석해서 적용한 것**: P7의 `blur_glossy`/`clamp` 값은 기록이 없어 바꾸지 않고 보고·경고만 한다.
- **freeze baseline**: 10-05 10:41 기록 이후 `542ad46`에서 control 그룹이 이미 바뀌어 있었다(이번 작업 무관). 1C·1D 승인분 반영 후 다시 기록했다.
- 회귀: 단위 테스트 전부 통과, 스모크 31/31, jet v0010 샘플 바이트 동일.

## 10-06 범용화 3–5단계: 결정 사다리 · 선언형 장면 · 스토리보드 핑퐁

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| 3 결정 사다리 | `studio/decisions.py`: brief → facts → script → shotlist → look, 계층마다 제안 → 사용자 원문 → 최신 시트만 승인, 부모 해시 결속(stale 계산), 계약 파일로 투영(drift 탐지), 게이트(build/render/generate/final voice/candidate/deliver). 첫 제안 때 `ladder.json` 생성(기존 프로젝트 영향 없음), `decide adopt`. fill brief는 shotlist에 결속 | `test_decisions` 7건(부모 변경 → 자식 stale, 손 편집 → drift, 숫자 주장 출처 2개) |
| 4 선언형 장면 | `shot.scene`($defs/scene) + `studio/layout.py`(병합·repeat/mirror/level_by_z 전개·fill 양보·exemplar 고정·lint) + `blender_ops/layout.py`(world·재질·volume·street 키트·인스턴스·primitive·조명·층·단면·bind). `--script` 선택, `author_lines` 기록, scene 수정은 데이터로 새 빌드 | **s01을 작성자 코드 0줄로 재현: 3983 객체가 형상·재질·조명 기준 일치, 카메라 샘플·거리 digest 동일**(`s01_parity_smoke`), `layout_smoke` |
| 5 스토리보드 | `studio/storyboard.py` + `storyboard_render.py`: Workbench 시트(프레임·캡션·탑뷰 경로, 1.5 s), 사용자 말 → 닫힌 편집 어휘(무브별 거리·높이·각도 노브, 렌즈, 지평선, 객체, 타이틀) → 데이터 재빌드 → 전/후 시트, 승인 = 측정 프레임 계약, look/final·hybrid 생성에서 `STORYBOARD_DRIFT` | `storyboard_smoke`(30 m 이동한 카메라를 거부, 승인 버전 통과), `test_storyboard` |

- **이번에 하지 않은 것**: 인스턴스 사이 relations(데이터에서 좌표 대신 관계로 배치), exemplar knobs(편집은 지금 JSON 포인터 데이터), s16 세트 재사용 검증. 6단계 N+1 주제가 정해지면 그 주제가 요구하는 순서로 한다.
- 회귀: 단위 306, 스모크 전체(아래 실행 결과), jet 샘플 동일.

## 10-06 범용화 6단계 (N+1: 로봇팔 관절 감속기) — 기구학 · 물체 무브 · 스토리보드 시안

| 묶음 | 내용 | 측정·검증 |
|---|---|---|
| 기어 수학 | `gear_core.py`: 인벌류트 외치·내치 윤곽(모듈·잇수·압력각에서 계산), 유성 배치(행성 중심·위상, 링 위상). 맞물릴 수 없는 조합 거부(링 ≠ 선+2·유성, 등간격 불가, 유성 < 17잇) | `test_kinematics_core`: 18/27/72×3을 선기어 0–40°로 돌리며 2D 다각형 겹침 0, 13잇 유성은 링과 충돌(그래서 하한 17) |
| 기구학 | spec `joints`·`couplings`(gear/internal_gear/belt/rack/planetary — 종류별 행 표, 미지 종류·이중 구동·순환 거부), 관절 피벗, `drive` 액션 → 프레임별 LINEAR 키 | `mechanism_smoke`: 선기어 360° → 캐리어 72.0°, 유성 −192°, 저장된 장면 중간 프레임도 비율 유지 |
| 간섭 게이트 | 상대운동하는 모든 부품 쌍(서로 다른 피벗 — 연동 쌍만이 아님)을 13프레임에서 BVH 검사, 표면을 법선 방향으로 크기의 0.05 % 안쪽으로(점 중심 축소는 링처럼 속이 빈 부품을 망가뜨림). `MECHANISM_INTERFERENCE` | 20쌍 통과; 유성 하나 반 톱니 틀림 → 거부; 막힌 링 테두리(연동에 없는 부품) → 거부 |
| 생성기 | `subject planetary --module --sun --planet --ring --planets` → spec 전체(lint 통과), `instances[].subject`로 프로젝트 spec을 장면에 배치 | `test_kinematics_core.MechanismSpecTest` |
| 물체 무브 | `turntable`·`slide`·`macro_push`: 거리 = 대상 상자 + 렌즈로 맞춤(`fill`, `distance_scale`), 기본값 표 `DEFAULTS`를 스토리보드 노브가 공유 | 0.16 m와 64 m 상자에서 경로 길이 비 = 400(동일 구도), 0.16 m 감속기를 ~0.5 m에서 잡음 |
| 스토리보드 시안 | `storyboard variants`(2–4개, 각각 카메라·장면·액션·타이틀·그래픽 자유 교체, 같은 게이트 통과, 한 장에 쌓은 시트) → `pick --user-words`(버전 재사용) → 일반 시트 → 승인. 숫자만 다른 시안은 `TAKES_ONE_IDEA` 경고 | `storyboard_variants_smoke`: 시안 3개 ≈ 5 s, 픽 전 승인·수정 거부, 픽 후 원래 시안 닫힘 |
| 버그 | 5단계 노브 표가 무브가 읽지 않는 이름을 가리킴(crane `to_height_m`→`to_h`, dive_through `start_height_m`→`above_m`) — "더 높게"가 조용히 무시될 뻔. 노브마다 계획기가 읽는지 검사하는 테스트 추가 | `test_every_knob_is_a_param_its_move_reads`(옛 이름으로 되돌리면 실패 확인) |

- **모든 값 조작(사용자 지적 후 수정)**: 노브 3개(거리·높이·각도)만 말로 바뀌던 구조 → `set` 편집(경로로 샷 내용 어느 값이든, 배수·증감·절대값).
  무브가 읽는 파라미터는 `camera_moves_core.PARAMS` 한 표(기본값 포함, 흩어진 숫자 28개를 옮김 — 11개 무브 계획 결과 바이트 동일),
  테스트가 모든 계획기를 실행해 읽는 이름 = 표를 양방향 검사(이름 빠짐·안 읽는 이름 둘 다 잡음 확인), `validate_shot`이 안 읽는 params 거부
  (기존 53개 샷 영향 0). macro_push "더 가까이"가 끝 거리에도 적용. 탑뷰 지도가 세로 화면에서 좁게 찍히던 버그(ortho 맞춤) 수정.
  실측: robot_joint 시안을 말로만 수정 — A 0.75배 가까이, B 끝 `detail_fill` 0.6→0.4, C 지나가는 폭 0.8→2.0 (시트 v02).
  남은 점: C는 계속 대상을 바라보며 지나가 프레임 차이가 작다 — 값이 아니라 연출 아이디어의 문제.
- 하지 않은 것: K1(운동 범위 claim `over`), G0 문법 파일, S0 스타일 n≥3, 4절 링크·캠·나사 연동, 하모닉·사이클로이드 감속기.
- 사실 메모: 산업용 로봇팔 관절에는 하모닉(파동기어)·사이클로이드(RV) 감속기가 흔하고 유성기어는 일부 기종에 쓰인다 —
  7단계 사실 계층에서 출처로 확인하고, 주제 문장을 그에 맞출 것.

## 10-06 모든 값 조작 (계획 `docs/EDIT_REACH_PLAN.md`, R0–R8)

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| R0 | 전 프로젝트 읽기 전용 스캔 | 새 검사에 걸리는 기존 데이터 0 (샷 127개) |
| R1 | `studio/shot_edit.py`: set/add/remove(value·factor·delta), 스키마 조건 분기 추적, 선언된 중간 객체 생성; 스토리보드 단축어가 그 위로; 결정 사다리 경로 오류 → `INPUT_INVALID`; pick 재검증 | `test_shot_edit` |
| R2 | 동작 params 표 9종(+simulate kind, drives/keys/cutter_keys 항목); 스키마 정합(읽는데 거부 3, 허용하는데 안 읽음 2, drive 분기 신설) | Blender 기록 스모크 13행 일치(표에서 키 하나 빼면 실패 확인), 스키마=표 단위 테스트 |
| R3 | `camera_keys.py`: rig 타입·타이밍 프로필·무브 종류별 키; `target_anchor` 구현(키 카메라의 대상, 시야 이탈 시 `CAMERA_ANCHOR_OUT_OF_VIEW`); 사용자 `timing.distance_m` 덮어쓰기 수정 | `bake`/`timing_curve` 기록 테스트 + camera_rig.py 스캔, anchor 스모크 |
| R4 | `scene_unread`: 키트 인자·프리셋 덮어쓰기·단면 옵션(시그니처에서 읽음)·카탈로그 덮어쓰기; 없는 재질 참조 거부(조용히 회색이던 것); 레벨 스키마 닫음; 예제 편집을 subject 스키마로 검사 | s01 parity 동일 |
| R5 | `content_keys.py`: 그래픽(공간·종류)·타이틀(anim)·채움 항목(layout); `policy.gates` 키 제한 | 그래픽 Blender 기록 스모크 6행, 타이틀·채움 기록 테스트; 프레임 0 화면 화살표 충돌 버그 발견·수정 |
| R6 | `fill revise --ops` | 간격·메모 변경, 결정 기록·안 읽는 키 거부 |
| R7 | 생성 체인 함수화(`generate.py`, 옛/새 코드 3장면 바이트 동일) → 워크벤치 `set_shot_value`(세션 = 원본 + 편집 + 샷 → 같은 생성), 커밋은 생성 뒤 카메라까지 비교, 장면 변경은 새 빌드 | `workbench_shot_smoke` 6검사(≈30 s), 기존 workbench/hitl 스모크 |
| R8 | SKILL #3 확장(말은 모든 값에 닿는다), Phase D·기계 검사 4b, references(storyboard·workbench·fill_brief·camera_rig·mechanisms·index·rule_rationale), 에이전트 프롬프트 | `test_skill_contract`, `test_reel_agent` |

- 범위 밖으로 기록: subject spec 빌더별 params 표(모델링), 코드가 쓰는 기록(narration cues 등)은 열어 둠.
- R9 실측(robot_joint 복사본 `robot_joint_reach`, 원본은 사용자 시안 선택 대기라 손대지 않음): 워크벤치에서 말 → 편집만으로
  "더 높은 데서" elevation 25→40, "모터 더 빨리" 선기어 720→1080°, "조금 위로" 감속기 z 0→0.02 m, "제목 넣어줘" 타이틀 추가 →
  커밋 v0008(장면 변경이라 새 빌드, 생성 뒤 카메라 비교 통과). 오타(`elevaton_deg`)와 턴테이블의 `whip_in_deg`는 이유와 함께 거부.

## 10-06 아스트라 블렌더 자유 + 구조로 지키는 선 (계획 `docs/ASTRA_BLENDER_FREEDOM_PLAN.md`, P0–P5)

| 단계 | 내용 | 측정·검증 |
|---|---|---|
| P0 | 기준선 실측: 사이클로이드 감속기, `scripts/reel_agent.py --low-load`(기본 샌드박스) | 10분, 도구 호출 64, 빌드 1회 시도·0회 성공. **codex 샌드박스 안에서 Blender가 Metal 시작 단계에서 크래시**(`supports_barycentric_whitelist` 세그폴트), MCP 워크벤치는 승인 정책 never로 거부 → 장면을 한 번도 못 만듦. 아스트라는 29엽·30핀·e 3 mm를 출처와 함께 정하고 저자 스크립트 26줄까지 씀. 과거 실행 32건: 워크벤치 사용 0, 최악 25빌드 중 13번째 첫 성공. `--approve-for-me` 재실행은 이 세션의 자동 권한 판단이 막아 사용자 결정 대기 |
| P3 | 수정 금지 코드 빌드·렌더 시작 때 해시 검사(기준선은 저장소 밖 `~/.config/studio/frozen_code/`, 사용자 원문으로만 기록; 아직 미기록 → 경고), 워크벤치 내부 도구 숨김·실패한 exec도 커밋 차단·`--allow-exec`에 원문, 빌더 params 읽기 표(기존 스펙 165개 충돌 0), 레지스트리↔스키마 동기화 테스트 6종, 사다리 삭제 감지 | 단위 테스트 |
| P1 | 화면 측정: 빌드 끝 Workbench id 패스(실제 카메라, 256 px, 8–12프레임) | 0.16–0.2 s/빌드(작은 장면). 근거리 잘림·빈 화면·벽 뒤 핵심 부품(설명 실패/무드 경고) 스모크. 클립면 인식 가드·앵커·스토리보드 투영(projection 2), 조준 빈 오브젝트일 때 실제 대상 가드, revolve 막힘 경고(기존 87개 오경보 0) |
| P2 | 저자 스크립트 격리: 1단계 별도 Blender(레이아웃+저자, 샌드박스 강제) → `authored.raw.blend`만 전달 → 호스트가 입력 해시 재확인 → 2단계 신뢰 빌드 | 기존 저자 72개 lint 통과(레거시 2개 제외: `author_pavilion` 독립 저장, 옛 `shot_02` argv). jet 리그 219프레임 바이트 동일, 스모크 44/44. 격리 스모크 9항목(게이트 바꿔치기·job 변조 무효, 밖 쓰기·보호 파일·3D 텍스트 거부, 설정 변경 기록, .blend 링크 → 로컬·해시) |
| P4b | 표현 설정 데이터: grade·compositor ops·engine_settings·addons·scene.links | 렌더러가 덮는 값 표 = 렌더 코드 할당(소스 검사). 오프셋+측광 합성, 룩 노드 유지, 렌더 프로필 뒤 유지, 라벨+렌즈왜곡 거부 스모크. 선언 없으면 룩 상태 해시 동일 |
| P4 | contrib: mesh·profile·coupling·spec 항목, 계약 테스트(격리 인터프리터), 자동 승격(사용한 빌드 통과 후), 버전 고정·deprecate, `contrib_gate` 수정 금지 그룹 | 사이클로이드 디스크 프로파일로 빌드 → v001 승격(출처 기록) → 다른 프로젝트가 v001로 빌드. NaN·열린 메시·bpy·파일·미선언 인자·미고정 참조·해시 변경 거부 |
| P5 | SKILL #7(자유와 선), 워크벤치 먼저·프레임 측정 작법, 기계 검사 5b–5d, `references/blender_freedom.md`, 런처 프롬프트 | 단위 391, 스모크 47/47 |

- 임계값 근거: `FRAME_SUBJECT_SMALL` 2 %(윈치 픽스처 2.9 %는 읽히는 제품 샷), `FRAME_SUPPORT_DOMINANT`는 jet 협곡 추격(승인된 구도)에 걸려 **판정에서 뺌** — 받침 비율은 기록만.
- 하지 않은 것: 룩 `fail_on`의 gates.py 일원화(`look.py` 수정 금지 파일이라 승인 필요), 2단계 샌드박스 enforce 전환(현재 record), 워크벤치 서버 샌드박스·세션 안 contrib, 무브 종류 contrib, P6 재실측(P0 차단 해소 후).

## 10-06 Blender 접근(브로커)·룩 판정 일원화·기준선 (계획 `docs/ASTRA_BLENDER_ACCESS_PLAN.md`)

| 항목 | 내용 | 측정·검증 |
|---|---|---|
| 원인 | codex 샌드박스가 GPU(IOKit `AGXDeviceUserClient`, `IOSurfaceRootUserClient`)를 막아 Blender가 Metal 시작에서 세그폴트 | `codex sandbox --log-denials`로 재현(exit 139); codex에 GPU 허용 설정 없음 |
| 브로커 | MCP 도구 `studio_run` → `studio/broker.py`: 샌드박스 밖, 자체 macOS 프로파일(GPU 허용, 네트워크 차단, 키 없음, 쓰기는 저장소·임시만), freeze/contrib 제외. 샌드박스 안 CLI는 `BLENDER_NEEDS_BROKER`. MCP 서버 도구는 설정으로 승인 | 프로파일 실측: Workbench 렌더 OK·네트워크 차단·밖 쓰기 차단. `broker_smoke`: 샌드박스 안 거부 → studio_run 빌드 성공(측정 0.12 s) |
| 룩 일원화 | 룩 실패 = 오류, 정책이 `look_scale/look_camera_dof/look_camera_shake/look_camera_two_point`만 경고로 내림; `fail_on` 제거 | 그동안 경고로 지나간 실패는 249버전 중 scale 9건뿐 — 대부분 이름 오분류(`StudioSupport`·`StudioSim_debris`의 "Stud"→나사) → `Studio` 접두어 무시로 수정. 카메라 리그 픽스처(2 m 벽)는 정책으로 경고 |
| 기준선 | `studio freeze record --user-words "1,2,3 전부 계획 세우고 진행해"` | 검사 변경 0·경고 0 |
| P0 재실측 | 2차: studio_run·워크벤치 MCP 사용 성공, contrib 직접 작성 → 세션의 contrib 미해석·"CPU 제한" 해석으로 멈춤 → 둘 다 수정. 3차: 약 20분, MCP 호출 29, **v0005까지 빌드**, 8엽·9핀(RepRap 설계 출처), 감속비 −1/8 정확, 간섭 0, 화면 측정 실패 0(중간에 `KEY_PART_INVISIBLE`이 가려진 출력 플랜지를 잡아 단면 추가로 해결), 프리뷰 스틸 4장 | 1차(기본 샌드박스, 브로커 전): 빌드 0회 |
| 기존 불안정 | `revision_equivalence_smoke` 강체 잔해 위치가 같은 입력에서 가끔 다름 | 오늘 작업 전 커밋에서도 5회 중 2회 재현 — 기존 문제, 별도 과제 |
| 아스트라 보고 반영 | 부모·자식 관계의 핵심 부품 분류가 지정 순서에 따라 덮어써짐 → 부모 먼저 칠하도록 수정 | frame_probe_smoke |

## 10-07 엔진 단면 테스트 (요청 `projects/harness_validation/astra_metrics/engine/REQUEST.md`, 사진 기반 외관 + 리서치 내부)

| 항목 | 측정 |
|---|---|
| 조건 | 본체 gpt-6-astra/medium, 서브에이전트 3개 gpt-6.1-sol/high(명시 전달 동작) |
| 비용·시간 | 약 90분, 도구 호출 626, MCP 38, 토큰 6,470만(캐시 입력 98 %) |
| 결과 | 메시 부품 434, contrib 8개 자동 승격, 렌더 정확히 3장. 운동(크랭크-슬라이더·캠 2:1·밸브 리프트·점화 순서) 계산과 1e-8 m 수준 일치, 치수 138개 출처 |
| 미달 | 외관 비대칭 주조·성형 커버·곡선 러너(사진과 차이 큼), 단면의 냉각수 통로·포트, 연속 간섭 증명 |
| 엔진 과제 | 부품별 연속 가시 구간, 연속 간섭, 구워진 애니메이션 관절 감사, 캠 팔로워·체인 물림, 실패 버전 워크벤치 수리, 비대칭 주조 형상 어휘 |
| 렌더 경로 확인 중 발견·수정 | 샌드박스 안 `ps` 실행 불가로 작업 상태 조회 실패 → 워커 PID + heartbeat로 판정(5e8da4e) |
| 기존 불안정 조사 | 강체 잔해: 순수 Blender 장면 8/8 동일, 빌드 내 굽기 직전 입력 전체 정밀도 동일인데 결과가 가끔 다름(부하 중 1/8, 무부하 0/24) → 스레드 순서 의존 추정. 해법: 입력 해시로 시뮬레이션 결과 재사용 |

## 10-07 선 보강 (테스트 후)

| 항목 | 내용 | 검증 |
|---|---|---|
| C-8 | 기록 주기의 유일한 기록은 빌드 중 `__pycache__` 쓰기 → 빌드 프로세스가 바이트코드를 쓰지 않음, 2단계 감시 강제 | contrib_smoke: 깨끗한 빌드에 감시 기록 0, jet 바이트 동일 |
| C-9 | 감시가 에이전트 코드 폴더(프로젝트·라이브러리 contrib)를 판정, 워크벤치 세션에도 설치 | workbench·contrib·workbench_shot 스모크 |
| C-10 | 부모·자식 핵심 부품이 지정 순서와 무관하게 자기 분류 유지 | frame_probe_smoke |
| C-11 | 잔해 시뮬레이션을 입력 키당 한 번만 계산, 궤적을 키프레임으로 저장·재사용 | revision_equivalence 3회 연속 통과, 궤적 1회 계산 확인 |
| 워커 생존 | 종료된 자식 워커(좀비)를 살아 있다고 보던 문제 → waitpid | blender_smoke(취소·재개) |
| 보류(승인 필요) | `contrib_loader.py`(수정 금지): 해시 확인한 바이트를 그대로 실행하는 방식(제안 diff는 세션 임시 폴더) | — |

전체: 단위 397, 스모크 48/48, jet 219프레임 바이트 동일, 수정 금지 변경 0.

## 10-07 외관 모델링 (계획: 규칙 정리 R → 스펙 형상 연산 S → 사진 비교 V → 주조·성형 키트 K → 재측정 M)

| 단계 | 내용 | 검증 |
|---|---|---|
| R | 숫자 규칙을 "주장하는 숫자"로 한정, 외관은 어떤 경로든 자유·같은 판정, 워크벤치 exec 탐색용 개방(커밋 불가), 사진 `local_only`, 요청서 템플릿(기구·외관 트랙) | 단위 398 |
| S1 | 스펙 `ops`: bevel·boolean(manifold)·subdivide(crease)·solidify·remesh_voxel·displace(seed)·weld·shade — 부품·배열 항목·불리언 피연산자에 굽기, 앵커·미러·배열·워크벤치 재빌드가 결과를 봄; `OP_PARAMS` 표·린트(중첩 포함) | spec_ops_smoke: 보어 부피 1e-3, 결정성, 재빌드=새 빌드 |
| S2 | `fillet_core.round_corners` 한 규칙: loft/sweep `rounded_rect`·`points+fillet_r`, profile `{points, fillet_r}`·`{rounded_rect}`, revolve `fillet_m`; 맞지 않는 반경은 거부 | 단면 부피 1 % 이내 |
| S3 | contrib 메시 부품이 `smooth`/`sharp_angle_deg`(기본 평면) 사용, 엔트리에는 전달 안 함·선언하면 거부 | 단위 + 스모크 |
| V1–V4 | `view_match_core`(궤도 카메라 하나), `solve_pose`(대응점 6–20, 퇴화 입력 거부), `reference view add|show`, 워크벤치 `preview`에 궤도 카메라·`lit`(Cycles GPU) 패스, `anchors`, 호스트 도구 `reference_fit_camera`(점 → 점 허용치 안 실루엣 다듬기)·`reference_compare`(IoU·정렬 IoU·크기비·윤곽·부품 상자·시트). 피사체만 연 세션은 빈 장면 | photo_match_smoke: 카메라 0.001°, 스펙 수정이 드럼 상자에 드러남, 버전 생성 0 |
| V5 | spec `photo_views`(선택): 피사체 삼각형을 사진 카메라(피사체 루트 좌표)로 투영, 가려진 부분 제외(먼 것부터 그림), 사진 마스크와 비교; 종류 `photo`(실존 대상 오류·그 외 경고), 편차 `photo:<id>` | 단위 + 스모크(세션 subject_report) |
| K1 | `casting`: 멤버 SDF 합침, `fillet_m`(닫힘)·`round_m`(열림)·`offset_m`, `subtract` 코어, `cuts` 정밀 가공(manifold) | casting_smoke: 0.46 s, 닫힘, 가공면·보어 정확, 결정성, 2배 크기 비례 |
| K2 | `subd` 케이지(+crease), 호스트 닫힘·방향 린트(배열·그룹·주조 멤버·피연산자까지) | 스모크 + 단위 |
| K3 | sweep `path_smooth: catmull_rom`(점을 지나는 매끈한 경로)·`scale` 테이퍼 | 반경 10 → 5 mm 정확 |

fidelity 코드 변경으로 기존 fidelity 리포트는 낡음 처리(다시 빌드하면 갱신) — 의도된 동작.

## 10-07 자율성과 형상 완성도 (계획: 재량 헌장 → 위임 실행 → 금지문 정리 → 개선 동기 → 완성도 규칙·검사)

| 항목 | 내용 | 검증 |
|---|---|---|
| 재량 헌장 | SKILL "Decide yourself; ask only these": 묻는 것은 고정 목록(유료·승인·수정 금지 코드·실존 대상 큰 편차·범위 밖), 나머지는 스스로 정하고 `decide note`로 기록. 런처 프롬프트도 같음 | skill_contract(HARD-GATE 2개 유지) |
| 위임 실행 | `project.delegation`(사용자 원문만): `project delegate`, 런처 `--delegate`(→ `.studio/delegation.json`, 이 실행이 만드는 프로젝트가 한 번 가져감). 게이트 변경 없음(사다리는 원래 옵트인). `decide note/notes` → `decisions/agent_log.jsonl`(보고·측정만 읽음) | test_delegation |
| 금지문 정리 | 규칙 #7: 구조가 막는 금지 목록을 "빌드가 거부하는 코드 + 대안" 한 줄로 | 허용 표현 22 → 32 (제한 64 → 67, 새 규칙 #8 포함) |
| 개선 동기 | Phase 3: 합격은 바닥, 프리뷰가 무료인 동안 남은 차이 중 가장 큰 것을 고침 | — |
| #8 형상 완성도 | 디테일 4단 목록, 생략은 화면 크기로만(단순화 표), 대조 예시(프리즘 헤드 vs casting 헤드), 세그먼트 올려 통과 금지 | — |
| detail 검사 | 객체별 √(프레임 안 화면 bbox 면적 / 서로 다른 면 방향 수) > 40 → 실패, `plain: 이유`면 통과. 정책 `detail_placeholder`로만 경고화 | test_detail_check, detail_smoke(상자 대용품 105 실패, plain 통과, casting 5.6, 윈치 부품 27–36 통과) |
| 보정 (1차 엔진, 렌더 없이 저장 장면 측정) | 40 초과 = 헤드 커버 67–70, 흡기 러너 46–61, 딥스틱 59, 타이밍 커버 51–57, 개스킷 68–76, 플레넘 상자 41–44, 블록 41–42 — 모두 1차 평가가 지적한 부품. 30–40 = 알터네이터 33(지적됨)과 윈치의 정직한 원통 27–36이 섞임 → 검사 밖, 디테일 목록·검토자 몫 | 바깥 샷(s01, 엔진 약 400 px)은 0건: 먼 시점의 단순한 형태 언어는 사진 비교·검토자가 맡음 |
| 지표 선택 근거 | 면 수 → 실패(800면 프리즘이 통과), 최대 길이 → 실패(긴 매끈한 캠축이 걸림) → 면적 + 면 방향 수 | — |
| CONTRIB_PARAM_UNUSED | `contrib_probe`(격리 인터프리터, 해시 확인 바이트): 매니페스트 파라미터를 하나씩 흔들어 출력 불변이면 draft 거부·승격 버전 경고, 해시로 캐시 | 로컬 라이브러리 10개 중 `ported_cast`의 `rib`만 검출 |
| 측정 | `astra_run_metrics`: 결정 기록 수, 질문으로 끝난 실행, `--project`로 detail 실패·plain·사진 IoU | — |

## 10-07 엔진 재측정 (s01만) 과 드러난 결함 수정

| 항목 | 1차 (10-07 새벽) | 재측정 |
|---|---|---|
| 조건 | 아스트라 medium, 서브에이전트 high, 같은 사진 | 같음 + 새 템플릿·위임 기록(`--delegate`) |
| 범위 | s01–s03, 렌더 3장 | 사용자가 s01만 비교하기로 함(s02·s03 측정 안 함), 렌더 1장 |
| 외관 | 판·프리즘 상자, 각진 러너, 닫힌 원통 알터네이터 | casting·subd·매끈한 sweep: 성형 커버, 넓은 곡선 러너, 구리 보이는 알터네이터, 구멍 풀리 |
| 사진 비교 | 없음 | 첫 빌드 직후부터 반복, 자기 버전 반려 4회. IoU 0.834 (< 0.85, 미달) |
| 외관 체크리스트 | "미달" 한 줄 | 19항목 중 통과 8 / 불합격 11, 크롭 근거 |
| 디테일 | 없음 | 4단 목록 사전 작성, detail 검사 통과(최대 23), 시각적 완성도 미달 |
| 자율 결정 | 기록 없음 | `decide note` 14건 |
| 토큰 | 6,470만 | 7,870만 (s01 위주, 에이전트 4개, 97 % 캐시) |

실행 중 드러난 결함과 정식 수정:

| # | 결함 | 수정 | 검증 |
|---|---|---|---|
| F1 | 프리뷰가 노출·WB·커브·컴포지터를 남겨 id 색 포화, lit은 AgX와 다름 | `id_view` 표 하나(프레임 측정 공용), 값 전부 먼저 읽고 설정·표 순서 복원, lit은 장면 그대로 | preview_view_smoke: lit 0.6028 = 실제 렌더 0.6028 |
| F2 | 실패 빌드에 샷 스냅샷 없음 → 워크벤치 수리 불가 | 스냅샷은 입력이므로 빌드 전에 씀, 저자 스크립트 판정은 `author_job.json`(record 포함) | failed_repair_smoke: 실패 → 수정 → 커밋 → 새 버전 |
| F3 | init이 `key_parts` 등 샷 필드를 조용히 버림 | 샷 스키마 필드 전부 복사, 모르는 키 거부 | test_project_init |
| F4 | `/camera` 통째 교체가 보고만 되고 적용 안 됨 | 캐시 제거, 반환값을 content에서 | 수정 없이 실패·있으면 통과하는 테스트 |
| F5 | 빌드 종료마다 감사 훅 ValueError | 호출 프레임 없으면 저자 프레임 없음 | 빌드 로그 0건 |
| F6 | 외관 틈으로 내부가 보여도 통과 | `shot.concealed_parts` → `CONCEALED_PART_VISIBLE`(오류, 완화 불가) | frame_probe_smoke |
| F7 | 대응점 좌표를 눈대중, RMS 하나만 | `reference_grid`, 점별 오차·점 그림, 맞는 최대 점 집합으로 틀린 점 지목 / 전반 불일치 판정 | 실제 엔진 점 8개: "전반 불일치(모델 비례 차이)" 판정, 그림에서 알터네이터 풀리 42 px |
| F8 | 런처 기록에 토큰 없음, 손으로 만든 감시가 실패를 놓침 | `--log-dir`, 측정이 thread id로 서브에이전트까지 합산, `run_status.py` | test_run_tools, engine2 실데이터 |
| F9 | 외관 반복에 끝이 없음 | 요청서 샷당 외관 예산 칸, SKILL이 그 칸에서 멈추고 남은 차이 보고 | — |

범위 밖(다음 후보): 연속 구간 간섭 증명, 먼 시점의 형태 언어 판정, 커버 위 잔 디테일 밀도.
