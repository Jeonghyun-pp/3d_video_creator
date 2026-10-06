# 아스트라의 Blender 접근, 룩 판정 일원화, 수정 금지 기준선 (2026-10-06)

사용자 결정: "1,2,3 전부 계획 세우고 진행해".

## 1. Blender 접근 — studio 브로커 (근본 해결)
원인(재현): codex 샌드박스(seatbelt)가 GPU(IOKit `AGXDeviceUserClient`, `IOSurfaceRootUserClient`)를 막음 → Blender가 Metal
장치 이름을 못 읽고 시작 단계에서 세그폴트(exit 139). codex에는 GPU를 여는 설정이 없음. 화면 측정·프리뷰는 GPU가 필요.
설계: Blender는 샌드박스 밖 studio MCP 서버에서만 돈다. 그 서버 자체도 제한된다.
- `studio/broker.py`: studio CLI 명령(args)만 실행. `sandbox-exec` 프로파일 = 네트워크 차단, 쓰기는 저장소·임시 폴더만, GPU 허용
  (실측: Workbench 렌더 OK, 네트워크·밖 쓰기 차단). 환경은 `blender_env()`(키 없음) → 유료 호출은 구조적으로 불가.
- MCP 도구 `studio_run {args}` (workbench_mcp에 추가). `.codex/config.toml`: `default_tools_approval_mode = "approve"`는 이 서버에만.
- 샌드박스 안(`CODEX_SANDBOX` 설정)에서 Blender를 띄우려는 studio 명령은 크래시 전에 `BLENDER_NEEDS_BROKER`(같은 args로
  `studio_run`을 부르라는 안내)로 거부.
- 검증: 단위(명령 검증·프로파일), codex 샌드박스 안 CLI → 거부 코드, 브로커 경유 빌드 성공, P0 재실행(사이클로이드).

## 2. 룩 판정 일원화 (`look.py`, 수정 금지 파일 — 승인됨)
지금: 룩 실패는 `style.look.qa.fail_on`에 적힌 것만 오류, 나머지는 프로젝트 정책과 무관하게 경고. 바꿈: 룩 실패 코드(메시지 앞부분)를
gates.py 하나로 판정 — 취향 판정(`scale`, `camera_dof`, `camera_shake`, `camera_two_point`)만 `look_*`로 완화 가능, 나머지 오류.
`fail_on`은 스키마·코드에서 제거(사용처 0).
- 함께 고칠 버그: 스케일 감사가 `StudioSupport`(받침)를 이름의 "Stud"로 나사로 분류 → robot_joint 오탐. 레이아웃 받침에 `studio_dim_role: none`.
- 비용: look_inputs 해시가 바뀌어 기존 포토리얼 버전의 룩은 다음 빌드 때 다시 적용됨. 엄격 프로젝트에서 그동안 경고로 지나간
  스케일 문제(samsung_moves 등)는 빌드 오류가 됨 → 보고.

## 3. 수정 금지 기준선 기록
2번 이후 `studio freeze record --user-words "1,2,3 전부 계획 세우고 진행해"` (사용자 원문 그대로).
