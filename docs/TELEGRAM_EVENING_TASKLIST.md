# Telegram 연동 저녁 검증 TaskList

이 문서는 `AgentCLI Telegram 하이브리드 모드` 기능을 실제 환경에서 검증하기 위한 실행 체크리스트입니다.

## 0) 테스트 전 기본 정보

- 테스트 일시: `2026-02-20` 저녁
- 테스트 대상 repo: `D:\000.Work\001.Private\000.API\agent_cli`
- 권장 쉘: PowerShell
- 권장 Python: conda 환경 Python

---

## 1) 사전 준비

- [ ] 터미널에서 repo 루트로 이동
```powershell
cd D:\000.Work\001.Private\000.API\agent_cli
```

- [ ] conda Python 확인
```powershell
conda run -n base python --version
```

- [ ] 의존성 설치
```powershell
conda run -n base pip install -r requirements.txt
```

- [ ] Telegram 라이브러리 설치 확인
```powershell
conda run -n base python -c "import telegram; print(telegram.__version__)"
```

- [ ] CLI 옵션 반영 확인 (`--telegram*` 옵션 존재)
```powershell
conda run -n base python agent_cli.py --help
```

---

## 2) 환경 변수/설정 준비

- [ ] Bot Token 환경 변수 등록
```powershell
$env:AGENTCLI_TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
```

- [ ] pairing code 포함 하이브리드 실행 (thread 모드)
```powershell
conda run -n base python agent_cli.py --telegram --repo "D:\000.Work\001.Private\000.API\agent_cli" --telegram-runner-mode thread --telegram-pairing-code "PAIR-1234"
```

- [ ] 하이브리드 시작 로그 확인
  - 기대: `Telegram control plane started in background` 로그 출력 + local shell 프롬프트 표시

---

## 3) Telegram 기능 검증 (P0)

### 3-1. 인증/페어링

- [ ] `/whoami` 실행
  - 기대: `chat_id` 숫자 출력

- [ ] `/status` 실행 (pairing 전)
  - 기대: 권한 없음 메시지

- [ ] `/pair PAIR-1234` 실행
  - 기대: `Pairing successful` 메시지

- [ ] `/status` 재실행
  - 기대: `[AgentCLI Status]`와 `running`, `run_dir`, `progress` 출력

### 3-2. 실행/중단/조회

- [ ] `/run_start --autopilot --continuous --iterations 1 --no-build` 실행
  - 기대: `Runner started` + `run_dir` 출력

- [ ] `/status` 실행
  - 기대: `running: true`

- [ ] `/detail 80` 실행
  - 기대: `cycle_summary.log`, `metrics.jsonl`, `run_summary.json` 묶음 상세 출력

- [ ] `/errors 80` 실행
  - 기대: 오류 계열 metrics 이벤트만 출력

- [ ] `/events task_end 80` 실행
  - 기대: `task_end` 이벤트 필터 결과 출력

- [ ] `/grep quota metrics.jsonl 80` 실행
  - 기대: `metrics.jsonl`에서 quota 포함 라인만 출력

- [ ] `/tail cycle_summary.log 50` 실행
  - 기대: tail 응답 (없으면 `(empty)`라도 정상 응답)

- [ ] `/runs` 실행
  - 기대: 최근 run 목록 응답

- [ ] `/run_stop` 실행 후 버튼으로 Confirm
  - 기대: stop 요청 메시지

- [ ] `/status` 재확인
  - 기대: `running: false` 또는 종료 진행 상태 반영

### 3-3. 자동 푸시 검증

- [ ] `/notify` 실행
  - 기대: `enabled_events`, `send_cycle_summary`, `poll_interval_seconds` 출력

- [ ] `/run_start --autopilot --continuous --iterations 1 --no-build` 실행 후 텔레그램 대기
  - 기대: 수동 명령 없이 `[AgentCLI Notify]` 메시지 수신
  - 포함 기대 이벤트: `run_start`, `task_done` 또는 `task_failed`, `run_stop`

- [ ] `cycle_summary.log`가 갱신되는 시점 대기
  - 기대: `[cycle] ...` 형태 요약 푸시 수신

- [ ] (선택) 10분 이상 정체 상황 유도 후 대기
  - 기대: `[stalled] ... idle=... threshold=600s` 알림 수신

---

## 4) subprocess 모드 검증 (P0)

- [ ] 기존 하이브리드 종료 후 subprocess 모드로 재실행
```powershell
conda run -n base python agent_cli.py --telegram --repo "D:\000.Work\001.Private\000.API\agent_cli" --telegram-runner-mode subprocess --telegram-pairing-code "PAIR-1234"
```

- [ ] `/run_start --autopilot --continuous --iterations 1 --no-build` 실행
  - 기대: `mode: subprocess` 표시

- [ ] `/detail 80` 실행
  - 기대: `telegram_runner_subprocess.log` 섹션까지 표시

- [ ] `/run_stop` + Confirm 실행
  - 기대: 정상 stop 처리

---

## 5) 보안/저장 경로 검증 (P0)

- [ ] config 저장 기본 경로 확인
  - 기대: `%USERPROFILE%\.agentcli\configs\...json`

- [ ] run 산출물 위치 확인
  - 기대: `<repo>\.AgentCLI\agent_runs\...`

- [ ] 민감정보 git 추적 여부 확인
```powershell
git status --short
```
  - 기대: `configs/*.json`, `databases/*.db`, `prompts/*`, `*.jsonl`, `*.log`가 새로 추적되지 않음

- [ ] 토큰 우선순위 확인
  - 조건: config에 `telegram.bot_token` 값이 있어도 `AGENTCLI_TELEGRAM_BOT_TOKEN` 사용
  - 기대: 서비스 정상 시작, token 관련 오류 없음

---

## 6) 권한 차단 시나리오 (권장)

- [ ] allowlist에 없는 다른 채팅/계정에서 `/status` 또는 `/run_start` 실행
  - 기대: `Access denied` 메시지

- [ ] 잘못된 pairing code로 `/pair WRONG` 실행
  - 기대: `Pairing failed: invalid code`

---

## 7) 완료 기준 (PASS)

- [ ] 3번 섹션 전 항목 통과
- [ ] 4번 섹션 전 항목 통과
- [ ] 5번 섹션 전 항목 통과
- [ ] 치명 오류(서비스 비기동, 인증 우회, 중단 불가) 없음

---

## 8) 이슈 기록 템플릿

```text
[이슈 제목]
- 재현 시간:
- 실행 명령:
- Telegram 입력:
- 기대 결과:
- 실제 결과:
- 로그/스크린샷:
- 재현율:
```
