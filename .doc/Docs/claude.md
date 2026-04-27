# Claude Backend 운영 가이드

## 사전 조건

- Python 3.10+
- Claude Agent SDK 설치

```bash
pip install -U claude-agent-sdk
```

## 인증 방식

Claude backend(`execution_backend=claudecode`)는 다음 중 하나로 인증합니다.

1) **Claude Code 인증**: `claude auth login`
2) **API 키**: `ANTHROPIC_API_KEY` 환경변수 설정

둘 중 하나만 되어 있으면 동작합니다.

## 기본 실행

```bash
python agent_cli.py --repo /path/to/repo
```

Interactive Shell에서:

```
/set execution_backend claudecode
/save
/start
```

## 스모크 테스트

전송/수신/파싱 경로를 빠르게 확인하려면:

```bash
python -m agent_runner.backends.claude_smoke_test --prompt "Hello"
```

로그에서 `assistant` 또는 `result` 메시지 출력이 보이면 정상입니다.

## 트러블슈팅

### 1) claude-agent-sdk import 실패

- `pip install -U claude-agent-sdk` 실행 여부 확인
- 가상환경 활성화 확인

### 2) 인증 오류

- Claude Code CLI를 사용하는 경우 `claude auth login` 실행
- API 키를 사용하는 경우 `ANTHROPIC_API_KEY` 설정 확인

### 3) 스트림/수신 에러

- SDK 버전 차이로 `receive_response()` 또는 `receive_messages()`가 없을 수 있습니다.
- 최신 버전으로 업그레이드 후 재시도하세요.

### 4) 응답이 비어 있음

- prompt가 너무 짧거나, tool 설정이 제한적일 수 있습니다.
- PM 단계에서는 구조화 출력(json_schema)이 활성화되므로 로그에 `result`가 보이는지 확인하세요.
