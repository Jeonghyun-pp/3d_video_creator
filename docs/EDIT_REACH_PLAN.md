# 모든 값 조작 — 반영 계획 (2026-10-06)

원칙(사용자 결정): **샷의 모든 값은 사용자의 말로 바꿀 수 있고, 아무것도 읽지 않는 값은 쓸 수 없다.** 특정 프로젝트가 아니라 엔진·에이전트
규칙 전체에 적용한다. 근거: 스토리보드 노브가 거리·높이·각도만 덮어 slide `span`, macro_push `detail_fill`을 말로 못 바꿨고, crane·dive_through
노브는 무브가 읽지 않는 이름을 가리켜 "더 높게"가 조용히 무시됐다. 이미 끝난 것: 카메라 무브 params(`camera_moves_core.PARAMS` + 실행 기록 테스트 +
`validate_shot`), 스토리보드 `set`(d213de5).

조사(에이전트 4개, 읽기 전용) 결과 남은 빈틈은 네 종류다.

| 종류 | 예 | 결과 |
|---|---|---|
| 열린 객체(오타 통과) | `actions[].params`(drive는 스키마 분기조차 없음), `scene.section.options`, `scene.levels[]`, `scene.kits[].args.overrides`, 예제 `edits[]` | 쓴 값이 조용히 무시 |
| 조건부로 죽는 값(스키마는 받음) | orbit 계열 무브의 `whip_in_deg`·`clearance_m`·`look_target`·`arrive`, `lens_mm` 없는 `lens_end_mm`, rig 타입·타이밍 프로필별 키, 그래픽 종류별 키, 타이틀 `scale_curve`, 채움 항목 `layout`별 키, `camera.target_anchor`(아무도 안 읽음) | 쓴 값이 조용히 무시 |
| 읽는데 못 쓰는 값(스키마가 거부) | explode `rotation_radians`, assemble `order`, dust `ceiling_z` | 말로 못 바꿈 |
| 편집 경로가 좁음 | `set`이 새 항목 추가·삭제·없는 중간 객체(`framing`) 불가, `fill revise`는 add/remove만, 워크벤치는 카메라 키·리그만, 결정 사다리 경로 오류가 날 예외 | 말로 못 바꿈 |

## 설계: 하나의 메커니즘 "선언된 읽기(declared reads)"

목록을 늘리는 게 아니라 같은 틀 하나를 모든 곳에 적용한다.

1. **표** — 열린 객체·조건부 키마다 "이 맥락(무브 종류, 동작 종류, rig 타입, 프로필, 그래픽 종류…)에서 코드가 읽는 키와 기본값"을 한 표에.
   기본값의 출처는 표 하나(코드는 표에서 읽는다 — 카메라 무브에서 한 방식).
2. **기계 검사** — 테스트가 그 코드를 실제로 실행하며 읽는 키를 기록해 표와 양방향 비교(빠진 이름·안 읽는 이름 둘 다 실패). Blender 코드는 기존
   스모크 안에서 기록용 dict로 같은 비교.
3. **강제** — `validate_shot`(모든 빌드·수정·시안이 지나감)이 "맥락상 아무도 안 읽는 값"을 거부. 메시지는 읽는 키 목록을 준다.
4. **편집** — 하나의 편집 모듈(`studio/shot_edit.py`)이 표와 스키마(조건 분기까지)를 보고 경로 편집을 허용·거부. 스토리보드·워크벤치·채움이 같은 함수를 쓴다.

표가 새 코드보다 늦게 갱신되면 2번 테스트가 실패하므로 표가 낡을 수 없다(N+1: 새 무브·동작·키가 생겨도 규칙을 고칠 필요 없음, 표 한 줄 + 테스트가 강제).

## 단계

각 단계: 실패하는 테스트 먼저 → 수정 → 단위·관련 스모크. 단계 끝에 전체 검증(단위, 스모크, `rig_regression` 바이트 동일, `freeze_check`),
작은 커밋. 다른 세션 프로젝트(DDP·역)는 읽기만 하고, 새 검사를 켜기 전에 전 프로젝트를 읽기 전용으로 스캔해 걸리는 게 있으면 멈추고 보고.

### R0. 사전 스캔과 결정 (0.5시간)
- 스크립트(스크래치)로 examples·projects·테스트 픽스처 전체에서 R2–R5가 거부할 값을 센다. 조사 시점: 동작 params 미사용 키 0(테스트 픽스처의
  cutaway `mode` 1건), 무브 params 0. 조건부 키·rig·그래픽은 아직 안 셈.
- 결정 필요(아래 "사용자 결정" 1–3).

### R1. 공통 편집 모듈 `studio/shot_edit.py` (반나절)
- `storyboard._set`, `_schema_at`, `_move_param`, `SHOT_CONTENT`를 옮기고 확장:
  - 연산 `set`(value/factor/delta), `add`(배열 끝 `/-` 또는 index, 새 항목 = 스키마 검증), `remove` — 결정 사다리 `decisions.apply_ops`와 같은 문법.
  - 없는 중간 객체: 스키마가 그 키를 선언하면 만든다(`/camera/move/framing/horizon_v` 가능), 아니면 거부.
  - `_schema_at`이 `allOf`/`if-then`/`oneOf`를 따라가 맥락(동작 type, rig type 등)에 맞는 분기에서 키를 찾는다 → 동작 params 선택 키 하나씩 추가 가능.
  - 열린 객체의 새 키는 R2–R5의 표가 선언할 때만.
- `storyboard.apply_ops`는 이 모듈 위의 단축어(closer/height/angle/lens/horizon/look_at/object.*/title.set). 스토리보드 docstring·테스트 이름에서
  "closed vocabulary" 표현 정리(`test_closed_vocabulary` → 모르는 op 거부 + 노브 없으면 `set` 안내).
- `decisions.apply_ops`의 잘못된 경로 → `StudioError`(지금 날 KeyError). `layout._edit`도 같은 경로.
- `storyboard.pick`이 스냅샷을 쓰기 전 `validate_shot`.
- 테스트: `tests/test_shot_edit.py` — 추가·삭제·중간 객체·조건 분기·거부 메시지, 기존 `test_storyboard` 유지.

### R2. 동작 params 표 (반나절~1일)
- 새 순수 모듈 `studio/blender_ops/action_params.py`: `ACTION_PARAMS = {type: {key: default|REQUIRED|DERIVED}}`, simulate는 `kind`별 하위 행,
  drive는 `drives[]` 중첩 행(`joint` R, `subject` D, `rpm`|`keys`, `profile`), reveal/cutaway `cutter_keys[]` 중첩 행. `unknown_action_params(action)`.
- **`scene_tools.py`는 수정 금지 파일(렌더 지문·제어 패스 복사본)** → 건드리지 않는다. 표는 별도 모듈, 검사는 호스트 `validate_shot`.
- 스키마 정합(코드가 읽는데 거부 → 허용, 아무도 안 읽는데 허용 → 제거): explode `rotation_radians`·assemble `order`·dust `ceiling_z` 허용,
  cutaway `mode`·flow `marker_style` 제거(`validate_shot` 필수 목록에서도 `mode` 제거, 픽스처 1건 수정), drive 분기 추가(`drives` 항목 닫힘),
  peel의 죽은 `axis` 경로는 그대로(스키마가 이미 막음). — 사용자 결정 2.
- 검사: 기존 Blender 스모크(`action_smoke_blender`, `reveal_smoke`, `sim_bake_smoke` 두 kind, `mechanism_smoke` rpm·keys)에서 params를 기록용 dict로
  감싸 실행 → 읽은 키 = 표 행. 순수 `kinematics_core.drive_value`는 단위 테스트로.
- `validate_shot`: `unknown_action_params` 거부. 문서 `mechanisms.md`에 drive `subject` 추가.

### R3. 카메라의 조건부 키 (반나절)
- 무브 상위 키: orbit 계열(orbit_reveal, turntable)은 `whip_in_deg`·`clearance_m`·`look_target`·`arrive` 거부(이미 `dwell`·`framing`은 거부 중),
  `lens_end_mm`는 `lens_mm` 필요. 표 `MOVE_KEYS_BY_KIND`(camera_moves_core, kind는 `plan()`이 정함).
- rig: `RIG_KEYS = {type: keys}`(flythrough/orbit/follow/procedural…), `TIMING_KEYS = {profile: keys}`(burst_settle, points, linear…), `points` 프로필에
  `points` 필수. 위치: 새 순수 모듈(`camera_rig_core`는 jet 회귀 대상이라 코드 무변경, 표만 별도) + 기록 테스트(`timing_curve`, `bake`는 순수).
- `camera.target_anchor` 구현(사용자 결정: 의미를 정해 구현, 원 설계 `AGENT_BUILD_PLAN.md:420`): 키 카메라에서 키에 `target`이 없으면 이 앵커를 매 프레임
  바라보는 회전을 빌드 때 키로 굽는다(움직이는 대상 추적). 새 모듈 `blender_ops/camera_aim.py`를 `build_scene`에서 `apply_camera` 뒤에 호출
  (`scene_tools.py`는 수정 금지 파일이라 손대지 않음). 스키마: `target_anchor`가 있으면 `keys[].target` 선택. 무브·리그 샷의 `target_anchor`는 읽을 곳이
  없으므로 거부(무브는 `look_target`, 리그는 `look_target`/`subject`). 스모크: 움직이는 부품을 따라가는 키 카메라.
- 검증: jet 리그 바이트 동일(코드 경로 무변경), 기존 샷 스캔.

### R4. 장면 데이터 (반나절)
- `scene.section.options`: `section.stage` 시그니처를 ast로 읽는 표(kits와 같은 방식) → `layout.lint`.
- `scene.levels[]`: 스키마 닫기 `{level_id, z, rects, obstacles}`.
- `scene.kits[].args`: 지금 새 빌드에만 도는 kit 검사를 `validate_shot`에서도(수정 빌드가 우회 못 하게), `path` 필수, 중첩 `overrides`(프리셋 키)·
  `intersections[].cross` 표.
- `scene.materials`: 참조 무결성(primitive가 쓰는 재질 존재, 안 쓰는 재질 경고), `catalog_overrides` 키를 호스트에서 카탈로그로 검사(지금은 Blender
  안 photoreal일 때만).
- 예제 `edits[]`: 포인터의 부모가 고정된 spec에서 풀려야 함, 편집 후 `validate_schema(spec,'subject')`+lint, `drop_parts` id 존재 확인.
  빌더 params 자체의 표(빌더별)는 범위 밖으로 기록(모델링 쪽 큰 작업).
- 테스트: `test_layout` 확장, `layout_smoke`, `s01_parity_smoke`(바이트 동일 유지).

### R5. 그래픽·타이틀·채움의 조건부 키 (반나절)
- graphics: 종류·공간별 키 표(`head_m` world 화살표, `tick_m`/`text` dimension, `target` outline, `points_2d`·`shaft_frac`·`head_ratio`·`fade_frames`
  screen, `radius_m` world). titles: `scale_curve`는 `anim: recede`만. 채움 항목: `layout`별 `edge`/`at`/`density_per_100m2`/`facing`.
- 방식: 스키마 if/then(닫힌 객체라 표 대신 스키마가 자연스러움) + 기록 테스트(그래픽은 `graphics_smoke`, 채움은 순수 `env_fill_core`).
- `policy.gates`: 키를 `gates.SOFTENABLE`로 제한(테스트가 동기화 강제).
- 실행 중 결정(10-06): `narration.cues[]`(오디오 파이프라인이 쓰는 14개 필드 기록), `route.generative.selection.override`, `fill_brief.approval.parents`는 코드가 쓰는 기록이고 편집 경로(SHOT_CONTENT) 밖이라 닫지 않음 — 사용자 말로 바꾸는 값이 아님.

### R6. 채움 계획 편집 (2시간)
- `fill revise --ops ops.json --user-words "…"`: `shot_edit` 문법으로 `fill_brief` 안 어느 값이든(항목 개수·간격·위치·facing, 층 void/note, topic).
  `--add/--remove`는 단축어로 유지. 사용자 원문 기록(지금 `propose --brief` 재작성은 원문이 안 남음).
- 테스트: `test_fill_brief` 확장, `fill_smoke`.

### R7. 워크벤치 `set_shot_value` (1.5–2일, 가장 큼)
- 현재 구조: 세션은 `authored.blend`(무브·동작·그래픽·룩 적용 전)를 연다. 생성기는 두 번 돌리면 중복된다 → 세션도 빌드와 같은 코드로 "기준 상태에서 다시
  생성"해야 한다.
- (a) `build_scene.py`의 생성 단계(채움 → 카메라·동작 → drive → 무브·reveal → 리그 → 그래픽 → 룩 → 화면 그래픽)를 함수 `generate(job, output, …)`로
  분리, 빌드와 세션이 같은 함수를 호출(빌드 결과 동일: `revision_equivalence_smoke`, s01 parity, jet 회귀로 확인). 수정 금지 파일 여부 사전 확인.
- (b) 호스트: `set_shot_value {path, value|factor|delta|op}` → 세션의 이전 샷 편집을 접어 `shot_edit`로 검증 → 절대값으로 기록(순서 의존 없음) →
  Blender에 새 샷 내용 전달. `/scene` 변경이면 레이아웃부터 다시 짓고 비-샷 편집을 재생, 아니면 기준 체크포인트에서 `generate`.
  세션에 `motion_style`, `library_root`, 채움 입력 전달.
- (c) 커밋: 모든 샷 편집을 순서대로 접어 `shot_override`(리그도 `set /camera/rig`로 같은 줄에), `/scene`이 바뀌면 새 빌드, 아니면 `--base`;
  저자 스크립트 샷(`shot.scene` 없음)에 `/scene` 편집 거부; 세션 시작 시점 revision으로 충돌 검사; **생성 후 기대값**(샘플 프레임의 카메라 행렬·렌즈,
  그래픽 보고)을 빌드 끝에서 비교 → 다르면 `WORKBENCH_REPLAY_MISMATCH`.
- (d) 곁 빈틈: `CAMERA_KEYS_OVERRIDDEN_BY_RIG`를 `camera.move`에도, `set_camera_keys` 후 `/actions` 편집(동작이 건너뛰어짐) 거부, MCP 도구 설명을
  `TOOLS`에서 생성(지금 `set_camera_rig` 누락).
- (e) 승인 관계: 워크벤치 편집은 에이전트 탐색이라 원문 불필요, 승인된 스토리보드와 달라지면 기존 `STORYBOARD_DRIFT`가 잡음 — 사용자 결정 4.
- 테스트: 단위(편집 접기·restore 후·절대값 기록·MCP 목록), 스모크(스토리보드 장면에서 `/camera/move/params/*` → 프리뷰 → 변형 → 커밋 → 재빌드 카메라
  = 세션, `/scene/primitives/0/at` 새 빌드, 기대값 변조 실패, 키+동작 거부).

### R8. 에이전트 규칙·문서 (반나절)
- 스킬 작성 키트(`~/.claude/skill-rule-authoring-kit.md`) 준수, 끝에 Part E 점검.
- SKILL.md CRITICAL #3 확장(새 블록 금지 — 테스트가 CRITICAL 1개 강제, 6개 유지):
  "결정은 사용자 원문으로만, **그리고 그 말은 모든 값에 닿는다** — 맞는 단축 명령이 없으면 경로 `set`(낮은 등급의 우회가 아님), 아무도 안 읽는 값은
  거부되고 오류가 읽는 목록을 준다. Why(10-06 사례). Instead of '그건 못 바꿔요', `set`하고 무엇이 바뀌었는지 말한다."
- Phase D 문장: "말 → 어느 값이든 편집(단축 명령, 아니면 경로 `set`; 워크벤치는 `set_shot_value`)". 기계 검사 4b행에 "읽지 않는 값 편집 →
  `INPUT_INVALID`(읽는 목록)" (❌, SOFTENABLE 아님 → 계약 테스트 통과).
- references: `index.md`(typed edits 문구), `storyboard.md`(추가·삭제·중간 객체, 노브 없으면 `set` 안내), `workbench.md`(Change 단계에 `set_shot_value`,
  같은 경로 문법), `fill_brief.md`(`--ops`), `camera_rig.md`(조건부 키), `mechanisms.md`(drive `subject`), `rule_rationale.md`에 "10-06 이후 사례"
  절(기존 원문 보존).
- `scripts/reel_agent.py` 프롬프트에 한 줄(테스트가 요구하는 `route plan`/`route approve`/`no text` 유지), CLI 도움말(`--ops`에 `set` 문법).
- `BUILD_REPORT`(측정), `ENGINE_PROGRESS`, `ENGINE_GENERALIZATION_PLAN`(5단계 "편집 어휘(허용 목록)"·미구현 `set_camera_move`는 이 계획으로 대체 표시).

### R9. 실측 (2시간)
- 전 프로젝트 읽기 전용 스캔: 새 검사에 걸리는 기존 데이터 0 확인(아니면 보고).
- robot_joint에서 말로만: 카메라(스토리보드 `set`), 동작("더 빨리 돌려" → `/actions/0/params/drives/0/keys/1/value`), 장면(감속기 위치), 타이틀 추가·삭제,
  워크벤치에서 같은 값 수정 → 커밋 → 시트. 오타·안 읽는 값은 거부 메시지 확인.

## 순서와 규모
R0 → R1(모든 편집의 토대) → R2 → R3 → R4 → R5 → R6 → R7(R1·R2 필요) → R8 → R9. 합계 약 5–6일. R1–R6은 엔진 안쪽이라 각각 독립 커밋,
R7은 생성 단계 분리를 먼저 별도 커밋(빌드 결과 동일 증명) 후 도구.

## 사용자 결정 (2026-10-06 답변)
1. `camera.target_anchor` → 의미를 정해 구현(R3).
2. 스키마-코드 불일치 → 코드가 읽는 값 허용, 아무도 안 읽는 값 제거.
3. 다른 세션 프로젝트가 새 검사에 걸리면 → 오류 유지, 그 프로젝트는 수정하지 않고 보고.
4. 범위 → R0–R9 전부, 워크벤치 편집에 사용자 원문 불필요(승인된 스토리보드와 달라지면 `STORYBOARD_DRIFT`).
## 범위 밖(기록)
- 빌더별 params 표(subject spec `builders[].params`) — 모델링 쪽 큰 작업.
- 생성 프롬프트(`prompt_spec`)는 이미 닫힌 스키마 + JSON 편집이라 이번엔 문서만.
- 결정 사다리 본문은 이미 경로 편집 + 닫힌 스키마(오류 형식만 R1에서 정리).
