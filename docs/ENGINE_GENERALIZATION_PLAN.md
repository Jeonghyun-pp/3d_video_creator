# 범용 3D 해설 영상 엔진 계획 — 버그 수정 → 대화로 정렬 → 무엇이든 만들기

작성 2026-10-06, 기준 커밋 `e6de5f2`. 실행 계획(승인본): `~/.claude/plans/enchanted-stargazing-snail.md`.

**진행(10-06)**: 0–2단계 완료(`BUILD_REPORT.md` "범용화 1–2단계"). 결정: 승인 방식은 지금처럼(아래 1D의 서명 승인은 채택하지 않음, image3d·가격표·approve 순서만 수정), 게이트 기본값 explain-strict(look-first는 프로젝트 선택). 줄 번호는 이 커밋 기준이다.
근거: 코드 전량 정독(4개 영역) + 설계 정독(5개 영역). 아래 버그 1–16은 두 번째 정독에서 모두 재현 근거로 CONFIRMED.
크기: S < 1일, M 1–3일, L > 3일. 🔒 = 사용자 승인 필요(수정 금지 파일·정책·유료).

## 최종 목표

> 제3자가 고른 처음 보는 기술 주제를, 사용자와 대화(브리프 → 사실 → 대본 → 샷 리스트 → 샷별 스토리보드 → 룩)로 확정하고,
> 새 author 코드 없이 선언형 장면 데이터만으로 candidate 영상까지 만든다. 사용자가 승인한 스토리보드 프레임과
> 최종 렌더의 카메라·객체 배치가 (느슨한 허용 오차로) 일치한다.

원칙:
- 해설 컷(라벨·화살표가 붙는 컷)은 가리키는 것이 맞아야 하고, 나머지는 "잘 보이고 멋있으면" 된다 — 사람이 고른다.
- 게이트는 돈·승인·사실·데이터 무결성·깨진 출력만 강제한다. 취향(레퍼런스 1편에서 학습한 수치)은 경고다.
- 사람·물·천은 Blender 대역으로 정렬하고, 승인 뒤 생성모델이 바꾼다(사람이 테이크 선택).
- 일반 메커니즘으로 고친다(N+1 시험). 사례 나열 금지.

## 단계 개요

| 단계 | 목표 상태 | 크기 | 승인 |
|---|---|---|---|
| 0 | 공개 저장소의 출처 없는 실존 시설 주장 정리 | S | 🔒 문장 처리·히스토리 |
| 1 | 믿을 수 있는 기반: 수정 빌드 = 새 빌드, 게이트가 저장 장면을 판정, 돈·승인은 사람만, 출처 없는 사실 차단, 새 클론 E2E | ~2.5주 | 🔒 여러 건 |
| 2 | 게이트 정리: 엄격도 설정, 생성 테이크 사용자 선택, SKILL 축약 | ~3.5일 | 🔒 정책 |
| 3 | 결정 사다리(텍스트): 브리프·사실·대본·샷 리스트를 대화로 확정 | ~1.5주 | — |
| 4 | 선언형 장면: s01을 author 0줄로 재현 | ~2주 | — |
| 5 | 시각 스토리보드 핑퐁: 카메라·객체를 프레임으로 맞추고 계약으로 고정 | ~2주 | — |
| 6 | 범용성 확장: 기구학·흐름·자산·대역·복수 문법 (N+1 주제가 요구하는 것부터) | 주제별 | 🔒 범위 |
| 7 | N+1 실측: 제3자가 고른 주제로 끝까지 | L | 🔒 렌더·유료 |
| 병행 | 품질: 실사 질감 A/B, 카메라 리듬, 룩 보정 | — | 🔒 유료 |

순서 근거: 대화형 엔진에서 "이거 바꿔줘" = 수정 빌드이므로 1이 먼저. 2는 3–5의 규칙 문서(SKILL)를 다시 쓰기 전에.
스토리보드의 객체 편집은 장면이 데이터여야 하므로 4 다음에 5.

---

## 0단계 — 사실 주장 정리 (오늘)

- `examples/samsung_cutaway/project/shots/*/shot.json` 내레이션: 실존 시설 결함 주장("기둥 80개 중 50개…", "2026년 5월 국토부 발표…")이
  `sources.json` 빈 상태, `claim_ids: []`로 공개 저장소에 있다.
- 조치(🔒 사용자 결정): (a) 출처를 붙인다, (b) 해당 문장을 빼거나 일반 설명으로 바꾼다, (c) "설명용 가정" 표시 + 수치 제거.
- git 히스토리에 남은 문장을 지울지(히스토리 재작성)는 사용자 결정.

## 1단계 — 믿을 수 있는 기반

완료 조건(모두 테스트로 증명):
1. 모든 수정 범위(camera/scene/fill/graphics/simulate/look)에서 base 위 수정 빌드 결과 = 같은 입력 새 빌드 결과(digest).
2. 리그·fill·fidelity 판정이 look 이후 저장되는 장면을 측정.
3. 에이전트 권한으로는 승인 기록·유료 호출이 불가능.
4. 문장마다 출처 claim 또는 "설명용" 표시가 없으면 candidate·납품 거부.
5. 새 클론: bootstrap → from-example → build → layout 렌더 → (가짜 클립) generative 컷 포함 rough 편집 → qa collect.
+ 버그마다 재현 테스트 먼저, 단위·스모크 통과, `rig_regression` 바이트 동일(아니면 사유 기록).

### 1A. 작은 수정 (승인 불필요, 각 S, 독립 커밋)

| # | 위치 | 문제 | 수정 | 테스트 |
|---|---|---|---|---|
| 1 | `studio/blender_ops/build_scene.py:31` | 모듈 수준 `from workbench_tools import compare`가 10행 `preserve.compare`를 덮어씀 → 워크벤치 커밋 + preserve 제약 시 117행 TypeError | `import workbench_tools as wbt`; `wbt.snapshot`, `wbt.compare` | 새 `tests/test_blender_ops_imports.py`: blender_ops 전 파일 AST로 모듈 수준에서 한 이름이 두 출처로 바인딩되면 실패(일반 lint) |
| 3 | `studio/edit.py:371-372` | generative 컷 `scene_version=None` → `Path / None` | 후보 목록에 scene_version 있을 때만 versions 경로 추가 | `tests/test_generative_clip.py`: 가짜 클립 + `edit.build(rough)` 성공 |
| 4 | `studio/subjects.py:223-227` | 이전 루프 변수 `silhouette` 사용 → NameError/마지막 것만 검사 | `by_view = {s['view']: s}`로 view별 이미지 검사 | `tests/test_subject_spec.py`: 실루엣 없음 → lint 오류(예외 아님), 첫 이미지 누락 → 첫 이미지 이름 |
| 5 | `studio/fill.py:97` | narration(dict)을 list로 순회 → 내레이션 단어 누락 | dict/list 모두 처리 | `tests/test_fill_brief.py`: 내레이션 단어가 `_phrases`에 포함 |
| 6 | `studio/generative/fal_client.py:216-224` | `HTTPError`가 `URLError`로 잡혀 401/404/422가 `GENERATION_PENDING`(무료 재실행) 무한 반복 | `except HTTPError`를 먼저: 5xx → PENDING, 401/403 → `ASSET_ACCESS_REQUIRED`, 404/410 → `GENERATION_REQUEST_UNKNOWN`(reconcile), 기타 4xx → 새 `GENERATION_POLL_REJECTED` | `tests/test_generative_fal.py`: 401 → 재시도 불가·재POST 없음·원장 reserved 유지, 503 → PENDING |
| 15a | `studio/__main__.py:13-15,76-88` | 종료 코드 손 목록(`DECISION_ERRORS`) 누락 다수, 파이썬 버그도 INPUT_INVALID, 매 명령 뒤 `status_project` 실패가 성공한 명령을 실패로 보고 | 종료 코드 = `1 if exc.retryable else 2`, 예기치 않은 예외 `INTERNAL_ERROR` 3; status 갱신은 실패해도 경고만 | 코드별 종료 코드 = retryable 플래그; status 예외 시 ok + 경고 |
| 15b | `studio/workbench_mcp.py:72-93`, `blender_ops/workbench_server.py:72-88` | 잘못된 요청 하나로 서버 종료 | 요청 단위 `except Exception` → 오류 응답, 전송 오류 무시 | `[]`, `"x"`, 깨진 바이트 뒤 ping 응답 |
| 15c | `studio/titles.py:23`, `camera_fit.py:21`; `edit.py:17`, `qa.py:14`, `references.py:8` | blender_ops를 `sys.path[0]`에 삽입(동명 모듈 가림); `scripts.shot_qa` 네임스페이스 import(cwd 의존) | `common.load_ops_module(name)`(importlib, sys.path 불변); `scripts/shot_qa.py` 본체를 `studio/shot_qa.py`로 이동, 스크립트는 래퍼 | import 후 sys.path 불변; 다른 cwd에서 `python -m studio doctor` |
| 15d | `scripts/video_pipeline.py:162`, `tests/test_camera_render.py:15-18` | fc-match 무조건 호출; Blender 게이팅이 PATH 기준 | `shutil.which('fc-match')`일 때만; `common.blender_binary()`로 게이팅 | PATH에 fc-match 없이 test_pipeline 통과 |
| 15e | `studio/routing.py:247`, `284-295` | `plan --apply`가 `est_minutes` 변화로 매번 샷 수정; `approve --budget-usd`가 검사 전에 기록 | 결정 키(`mode, features, rule_id, generative, role`)만 비교; 모든 검사 후 한 번에 기록 | apply 두 번 → revision 불변; 실패한 approve → project.json 바이트 동일 |
| 15f | `studio/edit.py:403-420`, `graphics.py:29-36` | 편집 캐시가 그래픽 레이어·titles import 폐포 미포함 → 오래된 candidate 재사용 | 스냅샷에 graphics 매니페스트 sha, titles import 폐포 해시 | graphics.json 변경 → candidate_id 변경 |
| 13 | `studio/project.py:422-436` | from-example: `runs/` 없음(렌더 실패), validate 전 copytree(재시도 불가), 없는 파일 참조(`audio_manifest`, `prompts/s01.txt`) | `_new_run()` 공용화; 임시 디렉터리 → validate → rename; 스키마에 `format: project-path` 표시 + 워커로 누락 파일 탐지, 파생 캐시는 null, 필수 입력은 `missing_inputs`로 보고(모든 프로젝트 validate에 적용) | from-example → `runs/` 존재, 깨진 참조 없음, 실패 시 대상 없음 |
| 12 | `studio/repair.py:76-81`, `blender.py:53-56`, `workbench.py:299-314` | 자동 되돌리기가 shot.json 통째 교체(라벨·내레이션·route 승인 유실) + spec 파일이 버전보다 새로워 `FIDELITY_STALE` | `METADATA_FIELDS = {labels, titles, narration, route}` 한 곳 정의 → `revise_shot` 메타 범위와 `select_version` 병합이 공유; 되돌릴 때 버전의 spec 사본 복원, 거부된 spec은 `spec.rejected-<v>.json` | 라벨 유지, spec = v1 사본, STALE 없음 |
| 7a | `studio/blender_ops/look.py:21` | 라벨 있는 샷의 렌즈 왜곡이 경고로 강등(라벨이 앵커에서 벗어남 = 깨진 출력) | `ALWAYS_FATAL`에 `camera_lens_distortion` 추가 🔒(look_inputs, 1B-2와 묶음) | look 스모크 |

### 1B. 수정 빌드 정확성 (핵심)

1. **authored 체크포인트 (버그 7, L, 🔒 정책)** — 생성기(fill·graphics·simulate)가 base에서 중복·실패하는 문제를 기능별 패치가 아니라 구조로 해결한다.
   - 문제: `fill_brief._source`(`fill_brief.py:59-65`) → `build_subject(replace=False)` → `assemble.py:552` "already built";
     `graphics._object`(`graphics.py:60-63`)·`build_screen` → `.001` 중복·같은 studio_id; `simulate.py:96,172` 파편·호스트 중복;
     `scene['studio_environment']` 누적; `camera_rig.py:96` `animation_data_clear`가 look의 two-point 키를 지운 뒤 look이 건너뛰어짐.
   - 수정: `build_scene.py`에서 author 실행·replay 확인 직후, fill/move/rig/look 전에 `authored.blend`(pack) 저장.
     `blender._build_shot`은 base의 `authored.blend`를 열고 `authored_sha256`을 `dependencies.json`에 기록.
     생성기 체인은 항상 authored 상태에서 다시 돌므로 수정 = 새 빌드가 구조적으로 성립(아직 없는 생성기 포함).
     preserve 비교는 패치 직후로 이동(생성기 결과가 아니라 패치가 바꾼 것을 검사). 워크벤치도 `authored.blend`를 연다(`workbench.py:62`).
     빌드 끝 불변식: 생성 접두어에 `.NNN` 이름·중복 studio_id 없음.
   - 기존 버전(authored.blend 없음)은 `--base` 거부 `BASE_NOT_REVISABLE`(복구: 새 빌드). 다른 세션의 DDP/역 프로젝트에 영향 → 🔒.
   - 비용: 버전당 디스크 약 2배.
   - 테스트: `tests/studio/revision_equivalence_smoke.py`(렌더 없음) — move·reveal·simulate·graphics·fill·photoreal 룩을 가진 샷:
     새 빌드 v1 → base v1 + 빈 패치 v2 → 새 빌드 v3; scene 상태 sha·인벤토리(이름·개수, `.001` 없음)·fill/graphics/simulation 보고서·리그 샘플 일치.
2. **look 건너뛰기 제거 (버그 8, M, 🔒 look_inputs)** — `look.py:207-210` 삭제(체크포인트가 있으면 이유가 사라짐).
   `MODULES` 리터럴(`look.py:19`)을 `look_modules()`(look.py의 blender_ops 내 import 폐포를 AST로 계산)로 교체 → preserve·geom_checks·scene_tools 자동 포함.
   `tests/freeze_check.py:32-33`의 `eval()`도 이 함수 호출로 변경.
3. **게이트를 look 뒤로 (버그 9, M, 🔒 동작 변경)** — `camera_rig.bake_camera_rig`를 `bake(job)`과 `verify(job)`(`camera_rig.py:254-345` 가드 루프)로 분리.
   새 순서: 리그 bake → fill apply → look → 화면 그래픽 → `camera_rig.verify` → `fill_brief.check` → fidelity 측정 → preserve/인벤토리.
   framing 가드는 측정 피치 대신 먼 수평점 투영으로 horizon_v 계산(two-point 후에도 불변). 리그 샘플은 bake 값을 유지해 jet 회귀 불변, 판정은 `final_guards`.
   기존 샷 일부가 새로 가드에 걸릴 수 있음.
4. **cue 재계산 (버그 10, M, 1B-1 이후)** — `camera_moves.py:234-257`: 수리 뒤 수리된 경로로 `mark_progress`·cue 재계산,
   달라지면 `on_cues` 재실행 + 수리 반복(최대 3회 고정점, 수렴 안 하면 `CAMERA_MOVE: cue drift`). 보고서에 `camera_cues_pre_repair` 유지.
   순수 함수 `core.cues_for(...)` 단위 테스트 + 장애물로 수리가 일어나는 스모크(cue 프레임 = 실제 최근접 프레임 ±1).
5. **모션 스타일 블러 (버그 2, S, 🔒 동작 변경)** — `build_scene.py:73-75`의 주입을 분기 밖으로(`camera_moves_core.realism_with_style`),
   `blender._motion_style`(`blender.py:31-36`)이 move 없이도 `camera.motion_style`을 반환(qa와 일치). archcutaway move 샷 블러 목표 6→14 px.

### 1C. 렌더 작업 (🔒 render_fingerprint — P7과 한 번에, 모든 렌더 캐시 1회 무효화)

- **CPU 폴백 지문 (버그 11)** — `jobs.py:339-346`: 폴백 시 새 설정으로 지문 재계산 → `renders/<fp2>`, `fallback_from` 기록;
  엔진/장치 오류 패턴일 때만 폴백(스크립트 버그는 그대로 실패).
- **렌더 게이트 일원화** — `fidelity.guard_render`(CLI 래퍼, `fidelity.py:434-450`) 대신 `render_gates(path, shot, version, profile)`
  (route·fill·fidelity·turnaround)를 `submit_render`와 `resume_job`(`jobs.py:261-277`, 지금은 게이트 없음) 둘 다에서 호출.
- **워커 분리** — `run_worker`와 헬퍼를 `studio/render_worker.py`로 옮기고 그 파일만 지문에 넣음 → 이후 게이트 수정이 캐시를 깨지 않음.
- 테스트: `tests/test_studio_render_settings.py`(가짜 `_run_process`로 폴백 → 지문 변경, 원래 설정 재요청 → 폴백 작업 + 경고), Python API 경로의 FIDELITY_STALE, resume 거부.

### 1D. 돈과 승인은 사람만 (버그 14, L, 🔒 설계)

현재: `asset image3d`는 `--allow-paid --max-usd`만으로 유료 호출(`assets.py:647-659`, `image3d.py:25-44`, 예산 검사 없음);
`check_user_words`(`review.py:79-86`)는 접두어만 거름; 예산이 에이전트가 쓸 수 있는 project.json에 있음; `reel_agent.py:51`이 `FAL_KEY` 포함 환경 전달;
평문 키 `~/.config/fal/api_key`; 에이전트는 JSON을 직접 쓸 수 있으므로 CLI 검사만으로는 무의미.

- **권장: 서명된 승인 + 같은 암호로 잠근 키**
  - bootstrap이 암호로 보호된 승인 키(`ssh-keygen` ed25519)를 만든다. 공개키는 `.studio/allowed_signers`.
  - 사람 전용 명령(route approve·예산, fill approve, asset approve, generate select, reconcile, review record, deliver, 모든 유료 실행)은
    정규화한 페이로드(종류, 프로젝트, 샷, 요청 지문/candidate 해시, 금액, 사용자 원문, 시각)를 `ssh-keygen -Y sign`으로 서명(암호 입력).
  - 소비 지점(`assert_route('generate')`, `paid_call`, `_require_turnaround`, `fill.require_approved`, `deliver`, select)은 `ssh-keygen -Y verify`로 검증.
    JSON에 직접 쓴 승인은 `ROUTE_APPROVAL_UNSIGNED`. 예산은 서명 기록에서만 읽음.
  - fal 키는 같은 암호로 암호화 저장, 사람이 실행한 명령 안에서만 복호화. `reel_agent.py`는 `*_KEY`를 환경에서 제거. 평문 키 경로 제거.
  - 사용자는 대화 중 `! .venv/bin/python -m studio route approve ...`처럼 직접 실행한다.
  - 대안(가벼운 안): 서명 없이 키 분리 + 에이전트 권한 설정에서 승인 명령 deny. 우회가 쉬우므로 보조 수단으로만.
  - 확인 필요: 설치된 Codex의 workspace-write 샌드박스에 네트워크가 없는지, `--approve-for-me`가 네트워크를 열지 않는지.
- **가격표 하나** — `fal_client.PRICING`만 남기고 `routing.MODELS`는 `{operations, endpoint}`만; `routing.estimate` = `fal_client.estimate_usd × attempts`.
- **image3d** — 리뷰 시트·서명 승인·예산 경로로 편입; 요청 디렉터리 키에 endpoint·인자 포함(`image3d.py:34`).
- **짧은 한국어 승인** — "좋아"(2자)가 `minLength 4`로 거부됨 → 길이 대신 시트 결속 + 서명으로 판단.
- 테스트: 직접 쓴 승인 → UNSIGNED, 테스트 서명 → 통과, 원문 변조 → 실패, 서명 없는 image3d → `paid_call` 미호출, 가격 일치, dry-run 환경에 키 없음.
  테스트·E2E는 `STUDIO_ALLOWED_SIGNERS`로 테스트 키 사용.

### 1E. 사실 강제 (버그 16, M, 🔒 정책)

- 새 `studio/facts.py`. 진실은 `sources.json` 하나: `sources[{source_id, title, url|citation, retrieved_at, excerpt}]`, `claims[{claim_id, text, source_ids≥1}]`.
- `shot.narration.sentence_claims`: 문장 분리(`[.!?。？！…]+` + 공백/끝)에 맞춰 각 문장이 `{claim_ids}` 또는 `{illustrative: 이유}`.
- `facts check` 문제: `FACTS_UNSOURCED`, `FACTS_MISALIGNED`(문장 수 변경 → 재연결), `FACTS_UNKNOWN_CLAIM`, `FACTS_SOURCE_MISSING`,
  `FACTS_ILLUSTRATIVE_NUMBER`(설명용 문장에 숫자 = 사실 주장).
- 게이트: candidate 편집 거부(rough는 경고), 편집 스냅샷에 sources sha, deliver 재검사, `review record facts_approved`는 검사 통과 + 서명 필요.
- 3단계의 facts·script 계층이 이 검사기를 그대로 쓴다(중복 구현 금지).
- 기존 DDP/역 프로젝트는 연결 전까지 candidate 불가(자동 마이그레이션 없음) → 🔒.

### 1F. E2E 스모크

- 최소 예제(Blender 컷 1 + 순수 generative 컷 1 + 프롬프트 파일): bootstrap → from-example → build → layout 렌더 →
  가짜 클립(`paid_call` 패치 + 테스트 서명) → rough 편집 → qa collect. (samsung s01은 사람의 fill 승인이 필요하므로 의도적으로 제외)

### 1단계 커밋 순서

1A 표 순서대로(각각 독립) → 1B-1 체크포인트 → 1B-2 look → 1B-3 게이트 순서 → 1B-4 cue → 1B-5 블러 → 1C(P7과 묶음) → 1E 사실 → 1D 서명 승인 → 1F E2E.

---

## 2단계 — 게이트 정리 (LLM 창의성 확보)

인벤토리 결과: look·motion·composition 스타일은 이미 경고. 실제로 창작을 막는 것은 fill 게이트, 카메라 구도 가드(`subject_margin`, `look_target_hidden`, `framing`),
화면 화살표 가독성 수치, 설명용 피사체의 fidelity, 수리 예산·자동 되돌리기, 해설 컷 생성 테이크 전면 금지, SKILL.md의 절대 문구.

- **엄격도 설정** — 새 `studio/gates.py`: `SEVERITY = {code: {'explain-strict': 'error', 'look-first': 'warn'}}`에 **완화 가능한 코드만** 등록.
  목록에 없는 코드는 항상 error(새 하드 게이트는 이 파일 수정 없이 하드 = N+1).
  - 완화 가능: `FILL_LEVEL_EMPTY`, `FILL_SUBJECT_HIDDEN`, `FILL_OFF_BRIEF`, fill lint 수치(identity ≤ 2종, ambient ≤ 4/100 m²), `subject_margin`, `look_target_hidden`,
    `framing`(horizon ±0.02, 레퍼런스 1편), `GRAPHIC_ILLEGIBLE`(s01 한 샷 기준 수치), `REPAIR_BUDGET_EXHAUSTED`, 설명용 피사체의 실루엣·치수·비율, 설명용 피사체의 요청 추적,
    layout 렌더의 `FILL_BRIEF_UNAPPROVED`.
  - 항상 하드: 유료·승인·라이선스·자격증명, 실존 피사체 fidelity(출처 2개, 실루엣), 버전·replay·preserve, 카메라 관통·clip, `MOVE_PATH_LOOP`, reveal inside-out,
    `SIMULATION_NOT_BAKED`, `TITLE_OUT_OF_SAFE`, `graphic_in_frame`, 생성 픽셀 위 라벨(anchors_2d 없이), 납품 기술 QA, 사실 검사.
  - `project.schema.json`에 `policy: {strictness, gates, explain_generated}`. 필드 없음 = `explain-strict`(기존 프로젝트·픽스처 동작 불변), `project init`은 `look-first`.
  - 배선: `blender.py`가 해석한 `gate_severity`를 job에 실어 `camera_rig.py:362`, `fill_brief.py:204-211`, `graphics.py:300`, `look.py:254`(fail_on 일반화)가 공통 사용;
    호스트 `fill.lint`, `subjects.lint_spec`, `fidelity.build_report`, `repair.check_budget`.
- **해설 컷 생성 테이크 사용자 선택** — `generative/policy.py:26-46`: hybrid explain 컷에 라벨·그래픽이 없고 사용자가 원문으로 고르면
  구조 게이트 실패를 경고로(`human_pick_allowed`, `pickable`). `clip.py:383-392` select, `clip.py:218`, `edit.py:234-258`(편집 시 오버레이 재판정 →
  나중에 라벨을 붙이면 자동 무효화), `shot.schema` selection `override`, route lint `W11_explain_overlays_need_structure`.
  계속 금지: anchors_2d 없는 생성 픽셀 위 라벨·화살표, mood + 라벨, hybrid에 첫 프레임 모델, 에이전트 대리 선택.
- **별도 수정** — 자동 되돌리기는 통과→실패일 때만(`repair.py:117`); `SINGLE_VARIANT` 삭제(`workbench.py:320`); W7 120→200단어(`clip.py:240`);
  `MAPPING_WORDS` 잘라낸 항목 보고(`clip.py:~289`); `generative_safety.md:5` "절대 완화 안 함" → "사용자 원문으로만 완화".
- **SKILL.md 144 → ~80줄** — HARD-GATE 2개 유지(테스트 요구). CRITICAL 13개 → "하드 규칙" 8개(라우팅, 생성 픽셀 텍스트 금지, 승인은 사용자 원문,
  실존 피사체 수치, 불변 버전, 깨진 출력 불변식, 라이선스·자격증명, 실패 보고) + "기본 작법(한 줄 사유로 바꿀 수 있음)".
  일화·Bad/Good 쌍·수치는 references로. 기계 검사표 30행 → 하드 게이트 코드 표 + "보고서를 읽고 눈으로 판단" 한 줄.
- 테스트: 새 `tests/test_gates.py`(기본 거부, 목록 코드만 완화, policy 없음 = strict); `test_skill_contract`에 "SKILL 하드 게이트 표의 코드는 studio에서 raise되고 SEVERITY에 없음";
  `test_generative_policy`(선택 경로·라벨 추가 시 무효); `test_repair`, `test_subject_spec`, fill/graphics 스모크에 look-first 경고 단언.

---

## 3단계 — 결정 사다리 (텍스트)

목표: 비싼 작업 전에 브리프·사실·대본·샷 리스트를 사용자와 핑퐁으로 확정. fill brief에서 검증한 패턴의 일반화.

- **공용 `studio/decisions.py`** — 계층은 코드 복사가 아니라 레지스트리 데이터:
  `brief → facts → script → shotlist → (shot) fill, storyboard → look`. 계층별 훅 `lint / sheet / apply_ops / materialize`.
  - 봉투: `{layer, scope_id, status, body, parents{layer: body_sha}, sheet{rev, path}, approval{user_words, body_sha, parents, sheet_rev}, history[...]}`.
  - stale은 **계산만 하고 기록하지 않음**(본문 해시 ≠ 승인 해시, 부모 승인 해시 변경, 부모 미승인 → 재귀). 부모가 바뀌면 자손이 자동 stale.
  - `drift`: 승인 내용을 실제 계약 필드에 투영한 값과 현재 필드가 다르면(`DECISION_DRIFT`, 손으로 고친 것 탐지).
  - `approve`는 마지막으로 보여준 시트 rev만 승인(`binding_for` 방식). 서명은 1D 경로.
  - `check_user_words`를 이 모듈로 이동, review.py는 재수출.
  - fill은 접근자로 편입: 저장은 `shot.fill_brief` 그대로(`build_scene.py:39-41`이 읽음), fill.py CLI 유지, 내부는 decisions 위임.
    부모 없는 기존 fill 승인은 "unbound" 경고(기존 테스트 그대로 통과).
- **계층 본문** — brief `{topic, audience, length_s, tone, language, key_message, subject_mode, references, must_include, must_avoid}` → `project.brief`;
  facts → `sources.json`(1E 검사기); script `{lines:[{line_id, text, claim_ids, kind: claim|framing}]}` → 길이 ±15 % lint;
  shotlist `{shots:[{shot_id, role, purpose, explains, line_ids, duration_s, route_features, subject_ids}]}` → 승인 시 `init_project` 루프(`project.py:79-106`)를
  `materialize_shots()`로 리팩터해 샷 생성(버전 있는 샷은 절대 삭제 안 하고 `removed_from_shotlist` 보고).
- **저장** — `projects/<p>/decisions/{ladder.json, <layer>.json, storyboard/<shot>.json, sheets/<layer>/rNN/, log.jsonl}`.
  `ladder.json` 없는 프로젝트 = 레거시(새 게이트 건너뜀, `decide adopt`로 기존 계약에서 초안 생성 후 사용자 승인).
- **게이트 표(한 곳)** — build: shotlist(스토리보드는 경고); render 전부: shotlist·fill·storyboard; look/final·still: + look, final은 storyboard 계약 통과;
  generate clip: storyboard·look + 기존 결속; 최종 음성: script; candidate·deliver: facts·script.
  새 코드 `DECISION_UNAPPROVED`, `DECISION_STALE`(어느 부모가 바뀌었는지 명시), `DECISION_DRIFT`, `STORYBOARD_DRIFT`.
- **CLI** — `studio decide status|propose|revise|approve|show|adopt`. 포인터 계층의 ops `{op: set|add|remove, path, value}`는 스키마 밖 경로 거부(기본 거부).
- **SKILL** — 0단계 앞에 "Phase D — 사용자와 결정"(D1 브리프 → D2 사실 → D3 대본 → D4 샷 리스트 → D5 스토리보드 → D6 룩), 하드 규칙 "승인 전 비싼 작업 금지".
  무인 실행은 제안·시트까지만 만들고 첫 미승인 계층에서 멈춤(렌더 없음). 목표 사용자 턴 10–15회, 한 계층 3회 수정 뒤 `DECISION_ROUNDS` 경고 → 질문으로 전환.
- 테스트: `tests/test_decisions.py`(해시, 왕복, 래퍼 원문 거부, 부모 변경 → 자식 stale + 부모 이름, 레거시 fill 승인 유효, 최신 아닌 시트 승인 거부, drift, 게이트 행렬, ladder 없는 프로젝트 비거부).

## 4단계 — 선언형 장면 (`shot.scene`)

목표: 장면을 데이터로. author 스크립트는 탈출구. 측정: s01을 author 0줄로 재현(장면 digest 일치), `dependencies.json`의 `author_lines → 0`.

- **s01 author가 하는 일** — 대부분 이미 데이터(street 키트, section.stage, reveal, titles, graphics, fill) 또는 작은 계약으로 표현 가능.
  새 엔진 기능이 필요한 것: 인스턴스 사이 관계(지금은 피사체 안에서만, `assemble.py:428-468`), 배열 항목으로 쓰는 `exemplar` 빌더(`assemble.py:158-161`),
  spec `knobs`·`levels`, 카메라 무브에서 sightline 계산, 범용 해석기. s01의 `L.camera` 키는 리그가 덮어쓰는 죽은 코드.
  숨은 의존성: exemplar를 최신 버전으로 읽고(`samsung_lib.py:136`, `env_fill.py:29`) `dependencies.json`에 기록 안 함 → 고정(`exemplar@vN`).
- **계약** — `shot.scene`(또는 `use: "sets/<id>.json"` + id 병합): `anchors`, `kits[]`(허용 목록 레지스트리), `instances[]`(subject 또는 `exemplar@vN`,
  `at`/`place` 관계, `knobs`, `edits[]`(drop_parts, 포인터 set), `materials.remap`, `visible`, `motion`), `levels`, `volumes`, `section`, `bind[]`, `lights[]`, `shell`,
  `materials`, `primitives[]`(최후 수단, 개수 상한 경고). 식·루프 없음(언어화 방지), 반복은 spec 배열.
- **엔진** — `studio/blender_ops/layout.py` `build(job)` → `layout_report.json`. 고정 순서: volumes → sightline → kits → instances(고정된 spec 스냅샷, knobs/edits,
  인스턴스 간 관계) → materials(`look_tags.json`, catalog) → primitives → lights/shell → levels → section → bind/visibility.
  `build_scene.py:24-27`에서 author 앞에 실행, author는 `script_path` 있을 때만. 호스트 `studio/layout.py` resolve/lint + `schemas/studio-v1/scene.schema.json`.
  lint: 참조 해석, 키트 인자 = 함수 시그니처(AST 교차 검사), 모든 action 대상이 bind/부품으로 존재, 카메라 무브 참조 존재, brief 층 ⊆ 선언 층(Blender 전용 검사를 호스트로), 고정 안 된 exemplar.
  `blender.py`: `--script` 선택, `dependencies.json`에 `layout_sha256`, `exemplar_specs`, `author_lines`.
- **수정** — revise에 `scene` 범위, 레이아웃 수정은 새 빌드(base 없음), `changes.json`의 `layout_diff`, preserve 토큰에 레이아웃 id.
  워크벤치 `set_layout_param(instance_id, pointer, value)`(id 기반 포인터), 커밋은 patch.py가 아니라 `shot.scene` 패치.
- **순서** — ① 동등성 하네스 `blender_ops/scene_digest.py`(이름 무관 형상 멀티셋 + id 맵 → 이후 엄격 이름) ② 스키마·lint·선택 스크립트 ③ `layout.py` v1
  ④ `samsung_station` spec + s01 동등성 ⑤ exemplar 배열·knobs ⑥ 인스턴스 간 관계 ⑦ revise/워크벤치 ⑧ 두 번째 샷(s16: 세트 재사용; 3D "2028년" 텍스트는 2D 타이틀로 바꿀지 사용자 결정).
- `samsung_lib` 이관: `run_length/girders/drop_parts` → knobs/edits, `station_box/column_hall/tunnel/office/excavation` → 프로젝트 spec → exemplar 승격,
  `worker` → `site_worker` exemplar, `city` → street 키트, `mat` → `look_tags.json`, 3D text·label_box·arrow → titles/graphics.
- 테스트: `tests/test_layout.py`(스키마, lint 오류별, 병합, 해시 안정성, layout_diff → 무효화 표, 키트 레지스트리, author_lines), `layout_smoke`, `s01_parity_smoke`.

## 5단계 — 시각 스토리보드 핑퐁

목표: 샷마다 카메라·객체를 화면으로 맞추고, 승인한 프레임을 느슨한 계약으로 고정.

- **본문** — `{camera{energy, move}, objects[{id, source, size_m, place{relation, ref}, role}], frames[{frame_id, t(0–1), caption, focus, visible}], titles, fill_ref}`.
  프레임은 샷 비율 `t`로 지정(음성 재타이밍에 안전).
- **시트(가벼움, 무인 실행 허용)** — `storyboard build`: 범용 author `storyboard_author.py`(spec 있으면 피사체, 없으면 exemplar, 그것도 없으면 라벨 붙은 대역 박스) →
  4단계 이후에는 `shot.scene`로 작성. 스토리보드 빌드는 수리 예산 면제(`blender.py:46`).
  `storyboard sheet`: 워크벤치 `preview`(Workbench 엔진, `workbench_tools.py:393`)로 프레임 3–5장 × (shaded, id) + 탑뷰 1장 ≈ 11회 렌더.
  **`render submit --profile layout`은 Cycles 16spp라 쓰지 않음.**
  새 READ 도구 `camera_state(frames)`, `storyboard_map(ids, focus_box)`, `preview`가 ortho 파라미터 반환. 탑뷰 지도: 카메라 경로, 프레임별 시야 쐐기 번호, id 라벨 박스, 범례.
  비교 시트(`--after rNN`): 프레임별 전/후, 지도에 이전 경로 회색 점선, 바뀐 내용 문장("f2: 카메라 2 m 낮춤, 35→50 mm; B2 천장 위 sprinkler_main 추가"), 화면 점유율 변화.
- **편집 어휘(허용 목록)** — `camera.closer/wider/height/angle/lens/move/energy/timing/horizon/look_at` → 무브 파라미터·`lens_keys`(새 SHOT_TOOL `set_camera_move`);
  `object.add/remove/move/scale` → 새 WRITE 도구 `place_object/remove_object`(4단계 이후 `set_layout_param`), 실존 피사체 크기 변경은 spec `deviations` + 사용자 원문;
  `fill.add/remove`, `title.set/remove`, `note`. 기록마다 "말씀하신 것 → 제가 한 것" + 전후 sha.
- **계약** — `decide approve --layer storyboard`: 선택 변형으로 워크벤치 커밋(replay 검증) → 프레임별 카메라 눈·방향·렌즈, focus 투영 박스, 보이는 id, 객체 월드 박스 기록.
  이후 빌드마다 `blender_ops/storyboard_check.py`(look 뒤, 렌더 없음) → `storyboard_report.json`. 허용 오차(잠정·미보정, 표류만 잡음):
  존재 id 집합 일치(유일한 정확 검사), focus 중심 0.15 이내·면적비 0.5–2, 시선 15°·눈 위치는 초점 거리의 25 %·렌즈 ±30 %, 객체 중심 max(0.5 m, 최대 치수 25 %), ±0.1 t 창에서 최적 매칭.
  빌드는 경고, look/final 렌더는 `STORYBOARD_DRIFT` 거부. 복구는 새 시트 → 사용자 원문 재승인(허용 오차 완화 금지).
- 테스트: `tests/test_storyboard.py`(미지 op 거부, op → 파라미터 결정성, `storyboard_core.compare`, 지도 투영), `test_workbench`(새 도구 replay),
  `storyboard_smoke`(Workbench 엔진·시간 상한, 카메라 op → 비교 시트, 승인 → 통과, 40° 회전 → DRIFT).

---

## 6단계 — 범용성 확장 (N+1 주제가 요구하는 것부터)

🔒 범위 결정 먼저: 기구학 포함 여부, 사람·유체는 "대역 + 생성 mood"로 할지.

| 묶음 | 내용 | 크기 |
|---|---|---|
| K0 기구학 | subject spec에 `joints`(revolute/prismatic/screw/fixed, 축, 한계, 출처)·`couplings`(gear/internal/belt/rack/screw/planetary/four_bar/slider_crank/cam — 종류별 행 표, 미지 종류 거부). 순수 `kinematics_core.py`(위상 정렬, 기어비 부호, Willis 식, 4절 닫힌 해, `MECHANISM_LOCKUP`). assemble이 관절 피벗 Empty 삽입, 프레임별 키 bake(드라이버 금지: `--disable-autoexec`). 새 action `drive`(채널 `mechanism:<joint>`, cue 바인딩) | L |
| K1 운동 범위 검증 | claim에 `over: {action_id, samples, include_limits}` → fidelity가 샘플 프레임마다 측정; 새 claim `gear_mesh`, `closure`, `within_limits`; `profile`에 인벌류트 치형(ISO 54 모듈 표) | M |
| G0 복수 문법 | `library/grammars/<id>.json`(beats: 역할·허용 무브·필수 액션·길이·스타일). 지금 문법을 `arch_cutaway`로 코드화(하드코딩 `centre_v 0.68`, `horizon_v 0.40` 이동) + `mechanism_explainer`. brief에 `grammar_id`(사용자 원문으로 결정), 샷 `beat_id`, `grammar lint`. 새 무브 `turntable`, `slide`, `macro_push`, DOF 키 | M |
| S0 스타일 n ≥ 3 | 레퍼런스별 동일 가중(가중 사분위), 레퍼런스 간 IQR, `overfit_risk = n<3 or 최대 가중>0.5`, leave-one-out 일관성, beat별 학습, 구도 특징 `street|object`(물체용: subject_fill, 중심, 여백) | M |
| F0 공정·흐름 | `flow` 일반화(GN 호스트 1개, 마커 dot/instance/streak, `pitch_m`, `path_ref`로 파이프 부품 경로, `stations`로 정지·형상 교체), 샷 `steps` 매크로, `simulate: stream`(유체 대역 입자), 무브 `scale_dive`(로그 거리 진행, 프레임별 near plane, 10³ 이상은 레벨 교체) | M–L |
| L0 라이선스·크레딧 | `studio/licenses.py` 기본 거부 표(CC0/PD cleared, CC-BY `cleared_attribution` + 크레딧 필수, BY-SA review_only, NC/ND/불명 차단), 편집 산출물에 `credits.md`·매니페스트 credits, 납품 시 크레딧 완전성, `ai_input:false` 자산은 생성 입력 거부. 이후 Sketchfab 어댑터(API·약관 확인 필요) | M |
| A0 STEP/IGES | CAD venv(build123d/OCP)로 가져오기 → 부품별 GLB + `assembly_tree.json`(동축 원통면 → 관절 후보). 라이선스는 원본 소속(review_only). `asset map propose`로 부품 매핑 제안 → 사람 승인 | L |
| P0 대역 | 절차형 `mannequin` exemplar(관절 체인, 공개 보행 각도 표를 cam coupling으로, 발 접지 claim), 위상 다른 4개 소스 scatter 군중, `simulate: surface`(Ocean)·`cloth`(셰이프 키 bake, 캐시 지속 여부 확인), scene role `proxy` + control `proxy_mask` → 구조 게이트가 대역 픽셀 무시, `generative_look.proxies` 프롬프트 매핑 | L |

## 7단계 — N+1 실측

- 주제는 만든 쪽이 아닌 사용자/다른 세션이 고른다(자기 선택 N+1은 약한 증거). 후보 예: "로봇팔 관절은 어떻게 움직일까"
  (6축 체인, 유성기어, 인벌류트 치형, 컨베이어 + carry, 마네킹 대역 mood 컷, `mechanism_explainer` 문법).
- 프로젝트는 `projects/harness_validation/<주제>/`. 렌더는 사용자 승인 뒤(무인 실행 중 무거운 렌더 금지).
- 합격: 3–5단계 대화로 확정, author 0줄, 스토리보드 계약 통과, over claim 통과·의도적 오류 주입 시 실패, 사실 검사 통과, 크레딧 완전, candidate 생성.
  실패한 지점이 다음 6단계 작업 목록이 된다.

## 병행 — 품질

- 실사 질감: (a) 자산·재질 투자 vs (b) 깊이·선 컨트롤 프레임 리스타일 + 시간 안정화 — 작은 유료 A/B로 결정 🔒.
  대역 → 생성 hybrid(마네킹 보행을 사람으로) A/B 🔒.
- `camera fit`의 `points` 곡선 탐색(뒤로 갈수록 가속하는 레퍼런스 리듬), 고해상도 하이라이트 계량(look 코드 🔒), 형상 매핑 문구 요약.
- 레거시 정리 🔒 범위: `scripts/video_pipeline.py` 계열, DDP·역 일회성 스크립트, Blender 4.0 Dockerfile, 가격 없는 Kling·Luma 어댑터.

## 사용자 결정 목록

1. 0단계: samsung 사실 문장 처리(출처/삭제/설명용), git 히스토리 재작성 여부.
2. 1B-1: base 수정에 `authored.blend` 필수(기존 버전은 새 빌드 필요, 디스크 2배) 또는 레거시 정리 대체 경로.
3. 1B-2·7a: look_inputs 수정(look 건너뛰기 제거, 모듈 폐포, 렌즈 왜곡 하드).
4. 1B-3: 게이트를 look 뒤에서 판정(기존 샷 일부가 새로 걸릴 수 있음).
5. 1B-5: move 샷 블러 목표 6→14 px.
6. 1C: render_fingerprint 수정 = P7과 함께 모든 렌더 캐시 1회 무효화, 워커 파일 분리 여부.
7. 1D: 서명 승인·암호화 키(사람 전용 명령은 사용자가 `!`로 직접 실행), Codex 샌드박스 네트워크 확인.
8. 1E: 출처 강제로 기존 프로젝트 candidate가 연결 전까지 막힘.
9. 2단계: 새 프로젝트 기본 `look-first`, 해설 컷 생성 테이크 사용자 선택 허용, SKILL 축약.
10. 6단계: 범위(기구학, 사람·유체 대역 + 생성).
11. 7단계: N+1 주제 선정자와 주제.
12. 기존 대기: s01 fill brief 승인, fal 미확인 결제 2건 reconcile.

## 진행 기록 위치

단계 완료 시 `docs/BUILD_REPORT.md`에 측정값, `docs/ENGINE_PROGRESS.md`에 상태를 갱신한다. 이 문서는 설계이며 보고서가 우선한다.
