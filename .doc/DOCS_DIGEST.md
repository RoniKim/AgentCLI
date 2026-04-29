# DOCS DIGEST

This digest is a compact index for AgentCLI PM/Dev/QA runs.
It is generated from the current `.doc/Docs` file inventory.

## Inventory
- .doc/Docs/ARCHITECTURE.md
- .doc/Docs/claude.md
- .doc/Docs/CONVENTIONS.md
- .doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md
- .doc/Docs/LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md
- .doc/Docs/TASK_STATUS_CLASSIFICATION_REVIEW.md
- .doc/Docs/UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md
- .doc/Docs/WEB_CONSOLE_TARGET.md

## .doc/Docs/ARCHITECTURE.md
- path: `.doc/Docs/ARCHITECTURE.md`
- decoded_as: `utf-8-sig`
- headings:
  - # AgentCLI Architecture Notes
  - ## Current Product
  - ## Web Console Target
  - ## Integration Direction

## .doc/Docs/claude.md
- path: `.doc/Docs/claude.md`
- decoded_as: `utf-8-sig`
- headings:
  - # Claude Backend 운영 가이드
  - ## 사전 조건
  - ## 인증 방식
  - ## 기본 실행
  - ## 스모크 테스트
  - ## 트러블슈팅
  - ### 1) claude-agent-sdk import 실패
  - ### 2) 인증 오류
  - ### 3) 스트림/수신 에러
  - ### 4) 응답이 비어 있음

## .doc/Docs/CONVENTIONS.md
- path: `.doc/Docs/CONVENTIONS.md`
- decoded_as: `utf-8-sig`
- headings:
  - # AgentCLI Implementation Conventions
  - ## General
  - ## Python
  - ## Web Console
  - ## AgentCLI PM/Dev/QA Behavior

## .doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md
- path: `.doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md`
- decoded_as: `utf-8-sig`
- headings:
  - # Windows 핸들/프로세스 누수로 인한 시스템 슬로우다운 문제
  - ## 1. 증상 (Observed Symptoms)
  - ### 환경 조건
  - ## 2. 진단 과정 — 기각된 가설들
  - ## 3. 근본 원인 가설 — Process Object → File Handle 연쇄 누수
  - ### Windows 커널 동작 원리
  - ### 연쇄 시나리오
  - ## 4. 코드 레벨 root cause 후보
  - ### 1순위: `process_guard._wait_for_parent_exit` 무기한 핸들 보유
  - ### 2순위: subprocess PIPE 핸들 누수
  - ### 3순위: Logger `FileHandler`의 무한 보유 + cleanup 미보장
  - ### 4순위 (안전성): PID 재사용으로 무관 프로세스 kill 위험
  - ## 5. 제안 수정 — 우선순위 순
  - ### Fix A (최우선): `_wait_for_parent_exit`를 timeout 기반 polling으로 변경
  - # PID 재사용 검증 (Fix D 통합)
  - ### Fix B: `_CodexAppServerClient.close()` 핸들 cleanup 강화
  - # PIPE 명시적 close — Windows에서 reader thread가 EOF 받게 보장
  - # daemon thread join — 1초 timeout
  - ### Fix C: Logger cleanup을 atexit에 등록
  - ### Fix D: PID 재사용 검증
  - ### Fix E (선택): Logger FileHandler를 non-inheritable로 명시
  - ## 6. 사용자 진단 명령
  - # 1. 좀비 Python 프로세스 카운트
  - # 2. error.log 파일 핸들 보유자 (Sysinternals handle.exe 필요)
  - # https://learn.microsoft.com/en-us/sysinternals/downloads/handle
  - # 3. 시스템 전체 kernel pool / handle 카운트
  - # 4. TIME_WAIT 소켓 (보조 가설 검증)
  - ## 7. 검증 절차
  - ## 8. 관련 파일 인덱스
  - ## 9. 추가 분석 자료
  - ## 10. 후속 조치

## .doc/Docs/LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md
- path: `.doc/Docs/LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md`
- decoded_as: `utf-8-sig`
- headings:
  - # Local PR Queue And Deferred Validation Plan
  - ## Problem
  - ## Target Flow
  - ## Backlog Sizing Guard
  - ## Local PR Packet
  - ## Status Model
  - ## Merge Policy
  - ## Shell And Web Commands
  - ## Implementation Order

## .doc/Docs/TASK_STATUS_CLASSIFICATION_REVIEW.md
- path: `.doc/Docs/TASK_STATUS_CLASSIFICATION_REVIEW.md`
- decoded_as: `utf-8-sig`
- headings:
  - # Task 결과 분류 통합 — 검증 결과와 후속 작업
  - ## 0. 구현 업데이트 (2026-04-29)
  - ## TL;DR
  - ## 1. 검증 결과 매트릭스
  - ## 2. 의도-실현 갭
  - ## 3. 다국어 매트릭스
  - ## 4. 후속 작업 (우선순위 + 추정 공수)
  - ### Tier 1 — 분류 의도를 실현하는 핵심 (Hot-fix, 1주 분량)
  - # branch 유지, base checkout 안 함, pending_review 큐에 등록
  - # 기존 abandon_task_branch / restore_checkpoint 흐름
  - # task_status.py classify_task_failure() 안:
  - ### Tier 2 — 운영 KPI / 안정성 보강 (Roadmap, 1주 분량)
  - ## {task.id}: {title}
  - # ...
  - ## 5. GOALS.md 추가 권장 (선택)
  - ### P0-N. Task Result Classification — Disposition Dispatch
  - ## 6. 검증 못한 항목
  - ## 7. 검토 메타정보

## .doc/Docs/UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md
- path: `.doc/Docs/UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md`
- decoded_as: `utf-8-sig`
- headings:
  - # 무인운영 신뢰성 — 검증 결과와 후속 작업
  - ## TL;DR
  - ## 1. 검증 결과 매트릭스
  - ## 2. 후속 작업 (우선순위 + 추정 공수)
  - ### Tier 1 — 무인운영 안정성 (필수)
  - ### Tier 2 — 운영 정책 결정
  - ### Tier 3 — 별개 트랙 (앞서 합의된 다른 우선순위)
  - ## 3. 폐기된 제안 (이전 분석 정정)
  - ### 3-1. "Codex thread_id 재활용" — 폐기
  - ### 3-2. "Anthropic prompt caching 적용" — Codex 메인 환경에서 N/A
  - ### 3-3. "Sleep-and-resume에 1주 투자" — 정정
  - ## 4. 부록: 검증 방법 재현
  - # Quota wait 흐름 — Codex
  - # Quota wait 흐름 — Claude
  - # Outer loop reason dispatch
  - # Resume capability
  - # STOP 파일 lifecycle
  - # HEARTBEAT
  - # Worktree merge atomicity
  - ## 5. 변경 이력
  - ## 관련 문서

## .doc/Docs/WEB_CONSOLE_TARGET.md
- path: `.doc/Docs/WEB_CONSOLE_TARGET.md`
- decoded_as: `utf-8-sig`
- headings:
  - # Web Console Design Target
  - ## Source Of Truth
  - ## Shell Structure
  - ## Visual System
  - ## Data Contract
  - ## FastAPI Server Target
  - ## Non-Goals For First Pass
