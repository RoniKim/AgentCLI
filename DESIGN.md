# Design Notes (v2.0)

## 목표

- 비용/토큰 효율을 유지하면서도, **PM → Dev → QA** 자동 루프의 안정성을 높인다.
- 특히 PM 산출물(백로그)이 깨지거나 파싱 실패로 멈추는 문제를 **스키마 + 자동 리페어**로 줄인다.

## v2.0 핵심 변경

### 1) PM Structured Output (권위 소스)

PM은 최종 응답을 **JSON만** 반환하도록 프롬프트로 강제한다.
러너는 이를 `pydantic` 스키마(`agent_runner/schemas.py`)로 검증하고,
성공 시 `PM_OUTPUT_cycle_*.json`을 저장한 뒤 해당 JSON으로 `BACKLOG.json|md`를 **러너가 직접 생성**한다.

실패하면 `--pm-structured-retries` 만큼 **리페어 프롬프트**로 재시도한다.

### 2) Dev 프롬프트 강화 (apply_patch 중심)

Dev는 다음을 강하게 권장/강제한다.
- **플랜 장황 출력 금지**, 바로 구현/수정으로 진입
- 파일 수정은 **`apply_patch`**(또는 동등한 패치 도구) 중심
- 여러 파일을 만질 땐 **한 번에 묶어서** 수정(마이크로-커밋/산발 수정 방지)

### 3) Max-turns Continuation (선택)

SDK/모델이 max turns 예외를 내는 경우, 러너가 **continuation 프롬프트**로 이어서 재호출할 수 있다.
- PM: `--pm-max-turns-continuations`
- Dev: `--dev-max-turns-continuations`

### 4) 관측성(Observability)

- `run_dir/metrics.jsonl`: 단계별 이벤트를 JSONL로 기록
- `run_dir/tasks/*`: 태스크별 로그, 빌드/테스트 결과, 정책 스캔 결과

## 트레이드오프

- 완전한 'API 레벨 Strict Structured Outputs'는 OpenAI Responses API의 `response_format=json_schema`에 의존한다.
  2.0은 **SDK 버전 차이를 고려**해 먼저 로컬 스키마 검증 + 리페어를 기본으로 채택했다.
  (원하면 이후 `response_format` 기반으로 더 강하게 고정 가능)
