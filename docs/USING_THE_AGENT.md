# 제작 에이전트 사용

실행 기준 디렉터리는 `ai_technical_visualization_starter/`. Python은 `../.venv/bin/python`. 실제 완료 상태와 영상 링크는 `BUILD_REPORT.md`를 본다. `AGENT_BUILD_PLAN.md`는 설계 기준이다.

## 대화로 요청하기

이 작업 폴더를 연 Codex/ChatGPT Work에서 `reel-production` 절차로 요청한다. 프로젝트 스킬은 `.agents/skills/reel-production/SKILL.md`다.

예:

- “harness_validation을 보고 같은 형식으로 새 소재의 18초 릴스를 만들어. 실제 자료와 설명용 구조를 구분해.”
- “shot_01의 패널 분리 순서를 반대로 하고 시작을 1초 늦춰. 건물과 재질은 유지해.”
- “shot_02 카메라를 반대쪽에서 촬영해. 연결판을 화면 중심에 둬.”
- “자막만 짧게 고쳐. 기존 3D 프레임은 재사용해.”
- “마지막 작업을 재개하고 레퍼런스와 다른 점 세 가지를 우선순위대로 고쳐.”

Astra가 의도를 컷/수정 계약으로 바꾸고 도구를 호출한다. Python CLI는 장면을 판단하는 모델이 아니라 실행·저장·검사를 담당한다. 호출한 모델의 실제 정체성을 지침 문구로 바꿀 수는 없다.

## Astra 터미널 실행기

```bash
../.venv/bin/python scripts/reel_agent.py --project projects/harness_validation '기존 형상을 유지하고 패널 분리 순서를 반대로 수정해'
```

실제 실행은 `codex exec -m gpt-6-astra`를 사용한다. `.codex/config.toml`과 `.codex/agents/`에 Astra 제작/검토, Sol 구현, Luna 인벤토리 역할을 설정했다. `--dry-run`으로 명령만 확인할 수 있다.

2026-10-02 재검증에서 로그인과 모델 응답, 독립 실행기의 실제 정지 화면 제작이 성공했다. 이전 `Failed to synchronize managed preferences` 오류는 재현되지 않았으며 전역 설정을 변경해 해결한 것은 아니다. 기본 workspace sandbox에서는 Blender가 Metal 초기화 SIGSEGV로 종료됐고, 아래 공식 자동 승인 검토 모드에서는 실행됐다.

(정정 2026-10-04) 렌더 장치: 실행기는 기본으로 `STUDIO_RENDER_DEVICE=GPU`(Metal)를 하위 프로세스에 넘긴다. 컴퓨터 부하를 줄이려면 `--low-load`(CPU) 또는 `--device CPU`를 쓴다. `--dry-run`이 실제 env를 보여준다.

```bash
../.venv/bin/python scripts/reel_agent.py --approve-for-me --project projects/harness_validation/one_command_validation/valve_preview/inspection '밸브 이동 거리만 15cm로 수정해. 카메라와 다른 부품은 유지하고 마지막 프레임 한 장만 CPU 2스레드로 렌더해.'
```

`--approve-for-me`는 sandbox 경계의 승인 요청을 자동 검토하는 Codex 공식 옵션이다. sandbox/승인 우회 옵션이 아니며, 거절된 작업은 중단해야 한다. 기본 동작은 계속 workspace-write다. [공식 설명](https://learn.chatgpt.com/docs/sandboxing/auto-review). 검증 범위와 실패/복구 이력은 BUILD_REPORT.md 및 `projects/harness_validation/one_command_validation/`에 있다. 실행기는 `gpt-6-astra`를 명시적으로 요청하지만 현재 대화 모델을 바꾸지는 않는다.

## 직접 사용할 핵심 명령

```bash
../.venv/bin/python -m studio doctor
../.venv/bin/python -m studio project status --project projects/harness_validation
../.venv/bin/python -m studio project validate --project projects/harness_validation
```

새 프로젝트:

```bash
../.venv/bin/python -m studio project init --id my_reel --brief examples/pavilion_brief.json
for shot in shot_01 shot_02 shot_03; do
  ../.venv/bin/python -m studio shot build --project projects/my_reel --shot "$shot" --script examples/author_pavilion.py
  ../.venv/bin/python -m studio render submit --project projects/my_reel --shot "$shot" --version v0001 --profile layout --samples 8
  ../.venv/bin/python -m studio audio build --project projects/my_reel --shot "$shot" --mode scratch
done
```

`examples/author_pavilion.py`는 특정 설명용 파빌리온 예시다. 모든 소재를 이 모델로 바꾸는 범용 생성기가 아니다. 새 소재에는 Astra가 실제 자산을 가져오거나 별도 author 스크립트를 작성한다.

위 brief는 세 컷이므로 세 컷 모두 만들어야 편집할 수 있다. render submit은 작업 ID를 반환하고 백그라운드에서 실행한다. 각 ID를 job status로 조회해 세 작업 모두 complete인 것을 확인한 뒤 `edit build --project projects/my_reel --profile rough`를 실행한다. 최종 candidate는 각 컷의 현재 버전을 final 프로필로 다시 렌더한 뒤 생성한다.

부분 수정:

```bash
../.venv/bin/python -m studio shot revise --project projects/my_reel --shot shot_01 --change change.json
```

change.json은 `base_revision`, `scope`, `targets`, `change`, `preserve`를 가진다. `base_revision`은 현재 shot.json의 정수 revision이며 `.blend`의 v0003과 다르다. labels/audio/edit 수정은 3D 버전을 유지한다. camera/motion은 저장된 장면에 패치해 새 버전을 만든다. 형상 변경에는 scope=scene, 렌더 스타일 변경에는 scope=style과 요청을 구현하는 `--script`가 필요하다. geometry는 scope 이름이 아니다.

`preserve`를 명시하면 그 요청의 보존 목록으로 교체하고, 생략하면 기존 목록을 유지한다. 명시적 `[]`는 보호 항목을 해제한다. 예를 들어 자막 수정에서 scene_version을 보존한 뒤 카메라를 수정하려면 `preserve: ["geometry", "materials"]`로 교체한다. geometry는 로컬 메시 형상을 보존하므로 부품 이동은 허용하고, materials는 재질을, camera는 카메라 설정·변환·애니메이션을 보존한다. 개별 객체 ID는 해당 객체의 형상·재질·변환·애니메이션을 함께 보호한다. 보호 해제는 사용자가 요청한 변경에 필요한 범위로 제한한다.

카메라 수정 예:

```json
{
  "base_revision": 5,
  "scope": "camera",
  "targets": ["camera"],
  "change": {
    "camera": {
      "projection": "perspective",
      "movement": "approach",
      "target_anchor": null,
      "keys": [
        {"frame": 0, "location": [22,-28,17], "target": [0,0,4], "lens_mm": 32},
        {"frame": 179, "location": [19,-25,16], "target": [0,0,4], "lens_mm": 32}
      ]
    }
  },
  "preserve": ["geometry", "materials"]
}
```

대표 프레임과 전체 렌더:

```bash
../.venv/bin/python -m studio render submit --project projects/my_reel --shot shot_01 --version v0001 --profile look --frames 0,90,179
../.venv/bin/python -m studio render submit --project projects/my_reel --shot shot_01 --version v0001 --profile final
../.venv/bin/python -m studio job status --project projects/my_reel --job JOB_ID
../.venv/bin/python -m studio job cancel --project projects/my_reel --job JOB_ID
../.venv/bin/python -m studio job resume --project projects/my_reel --job JOB_ID
```

취소·재개는 유효한 프레임을 보존한다. 완성된 캐시도 파일 무결성을 검사한다. 렌더 job은 장면 스냅샷과 실행 코드를 고정한다. 기본 CPU Cycles는 이번 환경에서 동작했다. EEVEE/Metal은 환경 오류가 있었으므로 GPU라는 이유만으로 속도를 약속하지 않는다. (정정 2026-10-04) 샌드박스 밖 Metal Cycles는 정상이며 기본 장치다(약 11배). `renderer_actual.json`의 device로 실제 장치를 확인한다.

## 컷별 route (2026-10-04)

각 컷은 `route`(blender | generative | hybrid)를 가진다. `route plan`이 선언된 feature로 경로·비용·시간을 제안하고, 유료 생성은 사용자의 말을 근거로 `route approve`가 기록된 뒤에만 `generate clip`이 실행된다. hybrid는 Blender가 완성한 모션 패스를 영상 입력 모델이 질감만 바꾼다. 빠른 카메라는 `camera.rig`, 실사 룩은 `render.look_preset`으로 지정한다. 상세: `.agents/skills/reel-production/references/`.

음성과 편집:

```bash
../.venv/bin/python -m studio audio build --project projects/my_reel --shot shot_01 --mode scratch
../.venv/bin/python -m studio timing resolve --project projects/my_reel --shot shot_01
../.venv/bin/python -m studio edit build --project projects/my_reel --profile rough
../.venv/bin/python -m studio edit build --project projects/my_reel --profile candidate
../.venv/bin/python -m studio qa collect --project projects/my_reel --candidate CANDIDATE_ID
```

cue에 묶인 동작은 timing resolve가 실제 발화 타이밍으로 새 장면 버전을 만들 수 있으므로 그 뒤 새 버전을 렌더한다. 고정 프레임 동작은 음성 변경으로 임의 이동하지 않는다. 영상보다 긴 음성을 잘라 맞추지 않고 오류로 반환한다.

candidate는 실제 최종 해상도 렌더가 필요하다. 저해상도 프리뷰를 확대해서 후보로 표시하지 않는다. 임시 음성을 쓰면 출력 이름이 scratch_candidate.mp4이고 needs_voice 상태다. 음성을 쓰지 않는 요청은 명시적 provider=none으로 구분한다. 최종 음성은 직접 제공 WAV 또는 설정된 ElevenLabs를 사용하며, 유료 호출은 --allow-paid가 있을 때만 수행한다.

## 자산과 레퍼런스

`asset search`, `asset fetch`, `asset prepare`를 순서대로 사용한다. 최초 prepare는 inventory와 여러 각도 이미지를 반환한다. Astra가 parts/anchors를 매핑한 뒤 두 번째 prepare를 실행한다. 준비 완료한 동일 버전에 다른 원본이나 매핑을 덮어쓰지 않는다.

Blender author 안에서는 `from assets import import_prepared_asset`로 준비한 자산을 가져온다. 인스턴스마다 ID와 데이터가 분리되며 부품·앵커가 유지된다. 실제 모델의 의미/정체성 검토는 Astra의 책임이다.

`reference prepare --project PATH --input VIDEO --range 2:5`는 출처 영상의 해당 구간을 실제 프레임으로 추출한다. 추출 완료는 분석 완료나 사실 확인을 뜻하지 않는다.

## 검증 실행

```bash
../.venv/bin/python -m unittest discover -s tests          # 전체 단위 테스트 (Blender 불필요, 일부는 CAD venv/API 색인 없으면 skip)
../.venv/bin/python tests/studio/blender_smoke.py          # 실제 렌더 포함
../.venv/bin/python tests/studio/workbench_smoke.py        # 상주 Blender 세션·커밋 replay
../.venv/bin/python tests/studio/assembly_smoke.py         # 관계 배치·결합 검사·결함 5종
blender -b --factory-startup --python tests/studio/modeling_smoke.py   # Blender 내부용 smoke (look_*, previs, action, import_pose 동일)
```

`blender_smoke.py`는 실제 Blender 렌더를 포함하므로 진행 중인 작업과 렌더 잠금을 공유한다. 첫 줄 docstring에 "Run inside Blender"가 있는 smoke는 `blender -b --factory-startup --python <file>`로 실행한다. 상세 실행 경로와 이번 결과는 BUILD_REPORT.md에 기록한다.
