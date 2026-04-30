# Post-run backend parity and PR queue stabilization plan

> 작성일: 2026-04-30
> 상태: 후속 작업 노트. 현재 runner가 도는 동안 main worktree와 `.doc/GOALS.md`는 수정하지 않는다.
> 커밋 정책: 이 문서는 작업 메모이며 커밋하지 않는다.
> 근거: Claude 병렬 에이전트 보고서 + Codex 독립 코드 검증(HEAD `91bd283`, read-only 검토)
> 관련: `STABILITY_SECURITY_AUDIT_FOLLOWUP_20260430.md`가 이 문서의 F1/F2/F3/F4/F5를 A/R/L/E 안정성 감사 항목으로 확장한다.

---

## 0. 결론

현재 runner가 도는 중이면 즉시 수정하지 않는다.

이 이슈들은 대부분 "지금 실행 중인 Codex backend runner를 즉시 중단시켜야 하는 확정 장애"가 아니라, 다음 runner 사이클 또는 Claude backend/failover 운영 전에 닫아야 하는 안정화 작업이다. 따라서 현재 run 종료 후 아래 순서로 main을 수정하고, targeted pytest를 통과시킨 뒤 runner를 다시 돌린다.

우선순위:

1. F3: `branch_index.json` read-modify-write race 방지
2. F4: failure disposition/retry metadata 의미 정리
3. F1: Claude backend에 local PR queue 연결
4. F2: F1 포팅 과정에서 phantom completion parity guard 반영
5. F5: runner start approved roots empty fail-open을 fail-closed로 변경
6. 문서 동기화: `CLAUDE.md`, user-facing docs, digest/index

순서 보정:

- PR queue helper를 Codex/Claude 양쪽으로 확장하기 전에 `branch_index.json` writer를 먼저 직렬화한다.
- helper가 failure disposition 또는 retry metadata를 packet/report schema에 넣는다면 F4 schema 정리를 helper 추출보다 먼저 끝낸다.
- F4를 뒤로 미룰 경우, helper는 disposition 필드를 packet에 새로 주입하지 않는다.
- PR queue packet/index write-order 변경과 reconcile pass는 같은 patch set으로 묶는다. 둘을 나누면 crash recovery 중간 상태가 남는다.
- 이 문서의 Step 2-7 순서를 canonical 실행 순서로 삼는다. `STABILITY_SECURITY_AUDIT_FOLLOWUP_20260430.md`의 Day plan은 묶음 설명이며, 실제 커밋 순서는 이 문서를 따른다.

---

## 1. 검증된 사실

### F1. Claude backend PR queue 미연결

확인:

- `agent_runner/cycle.py`만 `queue_review_packet`을 import한다.
- 실제 호출은 `cycle.py:3184`, `cycle.py:4149`에만 있다.
- `agent_runner/backends/claudecode.py`에는 `queue_review_packet` 또는 `pr_queue` 호출이 없다.

영향:

- `--execution-backend claudecode` 사용자는 completed task가 local PR queue에 남지 않는다.
- Web Console의 worktree/PR review UX가 Codex backend 결과만 보게 된다.
- P0-L230/P0-L232류 "runner can create local PR review packet" 목표는 backend-agnostic이라고 보기 어렵다.

판정:

- High. Claude backend 또는 failover를 production으로 쓰기 전 차단 항목.

### F2. Phantom completion guard parity gap

확인:

- Codex backend는 `ref_has_new_commits()`와 `preserved_task_branch_has_new_commits`를 사용한다.
- Claude backend는 `claudecode.py:2539`에서 `has_new_commits(repo, task_head_before)`만 본다.

중요한 nuance:

- 현재 Claude backend는 Codex backend처럼 task branch를 PR packet으로 보존하는 흐름이 아니다.
- Claude backend는 worktree 내부에서 `merge_task_branch(repo, tb)`를 수행한 뒤, worktree HEAD 변경 여부를 보는 구조다.
- 그래서 "Claude + worktree = 모든 성공 task phantom"은 현재 코드에서 바로 확정되는 실패 모드는 아니다.
- 하지만 F1을 고치면서 Claude backend도 branch-preserve/PR queue 흐름으로 맞추면, 이 guard를 같이 반영하지 않으면 같은 phantom bug가 재발한다.

판정:

- Medium now, High when F1 parity implementation starts.

### F3. `branch_index.json` read-modify-write race

확인:

- `pr_queue.py:619-621`: `load_branch_index()` -> `_upsert_index_entry()` -> `_write_branch_index()`
- `pr_queue.py:1279-1293`: validation update 후 동일 패턴
- `_write_branch_index()`는 `atomic_write_json()`만 호출한다.
- atomic write는 torn write를 막지만, concurrent read-modify-write lost update는 막지 않는다.

영향:

- runner thread와 Web validation/action이 같은 repo index를 동시에 갱신하면 entry가 손실될 수 있다.
- 현재 Web에서 `validate_review_packet()` 연결이 아직 넓지 않아 즉시 재현 가능성은 제한적이지만, PR queue UI/validation action을 붙이기 전에 반드시 막아야 한다.

판정:

- Medium-high. PR queue를 운영 기능으로 확장하기 전 필수.

### F4. `build_failure_entry()` retry metadata 의미 충돌

확인:

- `failure_policy.py:177`의 `build_failure_entry()`는 attempt/max_attempts/dev_auto_escalate/dev_escalate_on을 받지 않는다.
- 내부에서 `decide_failure_disposition()`을 기본값으로 호출하므로, action은 `regression_failed`가 될 수 있다.
- 동시에 `auto_retry_allowed`는 status 기반 eligibility이므로 true일 수 있다.

영향:

- UI/리포트가 `auto_retry_allowed=true`를 "지금 retry가 실행된다"로 해석하면 혼란이 생긴다.
- 실제 retry 분기는 cycle.py에서 `decide_failure_disposition()`을 직접 호출하는 곳이 담당하므로 즉시 행동 버그라기보다 schema 의미 버그다.

판정:

- Medium-low. 필드 이름/계약 정리가 필요하다.

### F5. Approved roots empty fail-open

확인:

- `remote/controller.py:324-326`

```python
def _runner_start_path_within(path_text: str, roots: list[Path]) -> tuple[bool, Path | None]:
    if not roots:
        return True, None
```

영향:

- Web route는 `web.py:8077`에서 approved roots를 넘기므로 현 Web 테스트는 통과한다.
- 하지만 `RunnerController.start()` 직접 경로는 approved roots를 넘기지 않는다.
- root resolver가 실패하거나 새 호출자가 roots를 누락하면 path containment guard가 무력화된다.

판정:

- Medium. security guard는 기본 fail-closed가 맞다.

### 문서 drift

확인:

- 실제 라인 수:
  - `cycle.py`: 4257
  - `web.py`: 10126
  - `backends/claudecode.py`: 3343
  - `reporting.py`: 1908
  - `pr_queue.py`: 1333
  - `task_status.py`: 279
  - `failure_policy.py`: 219
- `CLAUDE.md` Key Modules에는 `pr_queue.py`, `task_status.py`, `failure_policy.py`가 없다.
- `docs/PIPELINE.md`, `docs/CONFIG_REFERENCE_KO.md`, `docs/MASTER_INDEX.md`에는 PR queue/task status/failure policy 키워드가 없다.

판정:

- Low for runtime, but important before the next PM/Dev prompt cycle.

---

## 2. 구현 설계

### T1. Shared PR queue helper 추출

목표:

- Codex backend와 Claude backend가 같은 helper로 review packet을 만든다.
- PR packet schema, validation artifact collection, changed files, merge preflight metadata가 backend마다 갈라지지 않게 한다.

위치:

- 신규 또는 기존 확장: `agent_runner/pipeline/shared_runtime.py`
- 호출자:
  - `agent_runner/cycle.py`
  - `agent_runner/backends/claudecode.py`
- 기존 저수준 writer:
  - `agent_runner/pr_queue.py::queue_review_packet`

제안 API:

```python
@dataclass(frozen=True)
class ReviewPacketContext:
    source_repo: Path
    run_dir: Path
    run_id: str
    task_ids: list[str]
    base_ref: str
    head_ref: str
    branch: str
    created_at: str
    source_head_before: str
    source_head_after: str
    worktree_dir: str
    validation_status: str
    validation_records: Sequence[dict[str, Any]]
    goal_trace: Sequence[dict[str, Any]]
    changed_files: Sequence[str] | None = None
    merge_preflight: dict[str, Any] | None = None
    status: str = "pr_queued"


def queue_task_review_packet(ctx: ReviewPacketContext) -> dict[str, object]:
    ...
```

helper 내부 책임:

- `validation_artifacts` 추출:
  - 각 validation record의 `artifact_path`, `log_path`, `path`
- `qa_notes` 추출:
  - 각 validation record의 `summary`, `detail`
- `changed_files` fallback:
  - 명시 값이 없으면 `git_changed_files(source_repo, base_ref, head_ref)`
- `merge_preflight` 기본값:
  - `base_ref`
  - `head_ref`
  - `branch`
  - `source_head_before`
  - `source_head_after`
  - `source_main_mutated`
- 예외 handling은 helper 밖 호출자에서 backend별 metrics/event에 맞게 처리하거나, helper가 typed result를 반환한다.

Codex backend 수정:

- `cycle.py:3184`의 inline `queue_review_packet(...)` call을 helper 호출로 대체한다.
- `cycle.py:4149`의 whole-worktree pending merge packet도 helper를 쓰되, `task_ids`와 `validation_status`만 run-level로 넣는다.
- 기존 behavior를 바꾸지 않고 중복만 줄인다.

Claude backend 수정:

- `claudecode.py`의 worktree cleanup block 근처에 run-level PR packet 생성 추가.
- task branch 단위 packet까지 맞추려면 Codex와 동일하게 task completion block에서 packet을 만든다.
- 최소 1차 수정은 run-level packet부터 붙여도 된다. 단 P0-L230이 "each completed task"까지 요구한다면 task-level packet이 최종 목표다.
- Acceptance를 명시한다:
  - 1차 허용: Claude backend도 최소 run-level packet을 남기고 Web/CLI가 어느 backend 산출물인지 구분할 수 있다.
  - 최종 parity: completed task마다 task-level packet이 남고 Codex packet schema와 validation metadata가 맞는다.

주의:

- Claude backend가 아직 Codex와 동일한 task branch preserve 흐름이 아니므로, 한 번에 구조를 크게 바꾸지 않는다.
- 1차는 "Claude backend에서도 local PR queue에 run/task 결과가 남는다"를 목표로 한다.
- task branch preserve까지 바꾸는 경우 T5 phantom guard를 같은 PR에서 반영한다.

테스트:

- `tests/test_pr_queue.py`
  - helper가 validation artifacts와 QA notes를 기존 packet과 동일하게 만든다.
- 신규 또는 기존 Claude backend unit test
  - `backends/claudecode.py`가 `queue_task_review_packet()`을 호출하는 경로를 monkeypatch로 검증한다.
- regression:
  - 기존 `tests/test_worktree_isolation.py::test_generated_worktree_cleanup_preserves_pr_queue_and_source_head`

---

### T2. `branch_index.json` locking

목표:

- 같은 repo의 PR queue index 갱신을 process/thread 간 직렬화한다.
- atomic write는 유지하고, read-modify-write 전체를 critical section으로 감싼다.

위치:

- `agent_runner/pr_queue.py`

제안 구조:

```python
_BRANCH_INDEX_LOCKS_GUARD = threading.Lock()
_BRANCH_INDEX_LOCKS: dict[str, threading.RLock] = {}


def _branch_index_thread_lock(source_repo: Path) -> threading.RLock:
    key = source_repo.expanduser().resolve().as_posix().lower()
    with _BRANCH_INDEX_LOCKS_GUARD:
        lock = _BRANCH_INDEX_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _BRANCH_INDEX_LOCKS[key] = lock
        return lock
```

file lock:

- `threading.RLock`만으로는 same-process race만 막는다.
- Web server가 별도 process로 실행되거나 Web validation/action이 활성화되면 cross-process lost update가 재현될 수 있다.
- 1차 구현부터 stdlib 기반 advisory lockfile을 함께 둔다.
- Windows는 `msvcrt.locking`, POSIX는 `fcntl.flock`을 사용하는 helper를 `agent_runner/utils.py` 또는 신규 `agent_runner/file_lock.py`에 둔다.
- thread lock은 같은 process 안의 reentrant critical section을 줄이고, file lock은 process 간 writer를 직렬화한다.
- lock path는 repo별 PR queue directory 아래에 둔다. 예: `.AgentCLI/pr_queue/branch_index.json.lock`.

제안 helper:

```python
@contextmanager
def advisory_file_lock(lock_path: Path, *, timeout_seconds: float = 30.0) -> Iterator[None]:
    ...
```

요구사항:

- lock file parent를 생성한다.
- timeout을 넘기면 typed exception 또는 structured error를 낸다.
- Windows/POSIX 분기를 helper 내부로 숨긴다.
- stale lock 삭제를 자동으로 하지 않는다. stale 판단/삭제는 preflight/doctor에서 별도 처리한다.
- lock file은 advisory이므로 모든 `branch_index.json` writer가 반드시 같은 helper를 사용해야 한다.

critical section:

```python
def _upsert_branch_index_locked(source_repo: Path, entry: dict[str, object]) -> dict[str, object]:
    with _branch_index_thread_lock(source_repo):
        with advisory_file_lock(_branch_index_lock_path(source_repo)):
            index = load_branch_index(source_repo)
            updated_index = _upsert_index_entry(index, entry)
            _write_branch_index(source_repo, updated_index)
            return updated_index
```

적용 지점:

- `queue_review_packet()`의 `load -> upsert -> write`
- `validate_review_packet_async()` 후반의 `load -> upsert -> write`

테스트:

- `tests/test_pr_queue.py`
  - `queue_review_packet()`를 여러 thread에서 동시에 호출하고 index entries가 모두 남는지 확인
  - 별도 Python subprocess 2개가 같은 `branch_index.json`에 entry를 추가해도 둘 다 남는지 확인
  - validation update와 queue insert가 interleaving되어도 기존 entry가 손실되지 않는지 확인
- monkeypatch로 `load_branch_index()` 뒤에 barrier를 걸어 lost update를 재현 가능한 테스트로 만든다.
- lock timeout 시 validation/queue action이 structured blocker를 남기고 index를 덮어쓰지 않는지 확인한다.

주의:

- lock scope에 validation 실행 자체를 넣지 않는다.
- lock scope는 index load/upsert/write만 포함한다.
- packet JSON write는 독립 파일이지만, E1 crash safety와 함께 순서를 재검토한다.
- 권장 순서:
  1. packet을 `branch_index_status=pending`으로 atomic write
  2. branch index를 locked upsert
  3. packet을 `branch_index_status=written`으로 atomic update
  4. preflight reconcile이 pending/orphan 상태를 복구

Implementation boundary:

- lock helper만 먼저 넣고 write-order/reconcile을 뒤로 미루지 않는다.
- `branch_index_status=pending`을 도입하는 patch는 같은 patch 안에서 reconcile을 제공한다.
- reconcile은 evidence를 삭제하지 않는다. missing packet/orphan index는 audit artifact 또는 stale marker로 남긴다.

---

### T3. Runner start path guard fail-closed

목표:

- approved roots가 비어 있으면 path containment 검증이 성공하지 않게 한다.
- 새 호출자가 roots를 빼먹어도 안전하게 실패한다.

위치:

- `agent_runner/remote/controller.py`

변경안:

```python
def _runner_start_path_within(path_text: str, roots: list[Path]) -> tuple[bool, Path | None]:
    try:
        candidate = Path(path_text).expanduser().resolve()
    except Exception:
        return False, None
    if not roots:
        return False, candidate
    for root in roots:
        ...
```

호출부 보강:

- `normalize_runner_start_options()`에서 `config_path` 또는 `run_dir`가 명시됐는데 approved roots가 비면 명확한 error code를 낸다.
- 예:
  - `approved_run_roots_missing`
  - `approved_config_roots_missing`

주의:

- 현재 `build_runner_start_options_contract()` 내부 validation call은 roots를 넘기지 않는다.
- 해당 path는 contract generation용이므로 fail-closed로 바꾸면 side effect가 생길 수 있다.
- 방법:
  1. `_runner_start_path_within()`은 fail-closed로 바꾼다.
  2. `normalize_runner_start_options()`에서는 containment validation을 "path field present 또는 final override validation이고 approved roots가 제공된 경우"에만 수행한다.
  3. path field가 present인데 roots가 없으면 error를 낸다.
  4. contract generation처럼 validation_values가 내부 기본값이고 roots가 없는 경우에는 containment check를 생략한다.

테스트:

- `tests/test_stop_progress.py`
  - `normalize_runner_start_options(..., raw_options={"run_dir": outside}, approved_run_root=None)`가 error를 반환하는지
  - `config_path`도 동일
- `tests/test_web_console_safety.py`
  - 기존 outside root test 유지
  - Web route가 approved roots를 정상 전달해 기존 green 유지
- contract generation:
  - `tests/test_web_console_readonly.py` start options contract 관련 테스트 유지

---

### T4. Failure retry metadata schema 정리

목표:

- "retry 가능한 status인가"와 "이번 attempt에서 실제 retry action인가"를 분리한다.

현재 문제:

- `auto_retry_allowed`는 status eligibility에 가깝다.
- `disposition`은 attempt budget과 `dev_auto_escalate`를 반영하지 않은 기본 action일 수 있다.
- 같은 entry 안에 `auto_retry_allowed=true` + `disposition=regression_failed`가 동시에 나올 수 있다.

제안 schema:

```json
{
  "retry_eligible": true,
  "retryEligible": true,
  "retry_allowed_now": false,
  "retryAllowedNow": false,
  "retry_budget_consumed": false,
  "retryBudgetConsumed": false,
  "disposition": "regression_failed"
}
```

호환 전략:

- 기존 `auto_retry_allowed`는 당분간 유지하되 `retry_eligible` alias로 취급한다.
- UI/리포트 신규 표시는 `retry_allowed_now`를 우선 사용한다.
- `build_failure_entry()` 시그니처에 optional budget fields를 추가한다.

변경안:

```python
def build_failure_entry(
    *,
    task_id: str,
    reason: str,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    attempt: int = 0,
    max_attempts: int = 1,
    dev_auto_escalate: bool = False,
    dev_escalate_on: set[str] | Sequence[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    disposition = decide_failure_disposition(
        reason,
        task_status=status,
        validations=validations,
        detail=detail,
        attempt=attempt,
        max_attempts=max_attempts,
        dev_auto_escalate=dev_auto_escalate,
        dev_escalate_on=dev_escalate_on,
    )
```

호출부:

- `cycle.py`의 `_task_failure_entry()` closure가 attempt/max_attempts/dev_auto_escalate/dev_escalate_on을 넘긴다.
- `claudecode.py`도 동일.

테스트:

- `tests/test_failure_policy.py`
  - budget이 없으면 `retry_allowed_now=false`
  - budget이 있고 reason이 escalate_on이면 `retry_allowed_now=true`
  - `auto_retry_allowed`는 legacy alias로 true 유지 가능

---

### T5. Claude phantom completion parity guard

목표:

- F1 구현 중 Claude backend가 branch preserve/PR queue 구조를 갖게 될 때, preserved branch의 head를 기준으로 commit existence를 확인한다.

현재 Codex pattern:

- task branch를 preserved mode로 처리한 뒤:
  - `branch_head = git_rev_parse_ref(repo, tb.branch_name)`
  - `preserved_task_branch_has_new_commits = ref_has_new_commits(repo, tb.branch_name, task_head_before)`
- phantom check:
  - `if not (preserved_task_branch_has_new_commits or has_new_commits(repo, task_head_before)): ...`

Claude 적용 조건:

- Claude backend가 기존처럼 `merge_task_branch(repo, tb)`로 worktree HEAD를 움직이는 동안에는 immediate blocker가 아니다.
- Claude backend를 Codex와 동일하게 "source main을 건드리지 않고 task branch를 PR queue로 보존"하게 바꾸는 순간 필수다.

변경안:

- `claudecode.py` import:

```python
from ..gitops import ref_has_new_commits, git_rev_parse_ref
```

- task completion branch handling에 `preserved_task_branch_has_new_commits = False` 추가.
- preserved branch path에서 `ref_has_new_commits()` 계산.
- phantom completion check를 Codex와 동일 조건으로 변경.

테스트:

- `tests/test_pr_queue.py::test_preserved_task_branch_counts_commits_after_checkout_returns_to_base`와 같은 개념을 Claude backend helper/unit path에도 추가.
- 가능하면 branch handling helper를 shared로 빼서 backend별 중복 테스트를 줄인다.

---

### T6. 문서 동기화

목표:

- 다음 PM/Dev prompt가 stale module map을 읽지 않게 한다.
- user-facing docs에 새 운영 모델을 반영한다.

수정 대상:

- `CLAUDE.md`
  - Key Modules에 추가:
    - `pr_queue.py` local PR queue packet/index/validation
    - `task_status.py` task failure classification
    - `failure_policy.py` retry/preserve/stop disposition
  - stale line count 갱신:
    - `cycle.py ~4257`
    - `web.py ~10126`
    - `backends/claudecode.py ~3343`
    - `reporting.py ~1908`
- `docs/PIPELINE.md`
  - Local PR queue flow
  - `validation_pending/tests_skipped/no_tests_found`가 success가 아님을 명시
- `docs/CONFIG_REFERENCE_KO.md`
  - PR queue 관련 설정이 있으면 문서화
  - 없으면 현재는 "설정 없음, runtime artifact"라고 명시
- `docs/MASTER_INDEX.md`
  - `LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md`
  - `TASK_STATUS_CLASSIFICATION_REVIEW.md`
  - 이 후속 노트는 커밋하지 않을 계획이면 index에 넣지 않는다.

테스트:

- `python -m pytest -q tests/test_docs_validation.py`

---

## 3. 권장 작업 순서

### Step 1. runner 종료 확인

수정 시작 전:

```powershell
git status --short --branch
```

runner가 main worktree를 사용 중이면 종료 후 진행한다.

### Step 2. F3 cross-process lock부터 먼저 적용

이유:

- PR queue helper를 Claude까지 확장하기 전에 index writer를 안정화한다.
- 이후 테스트/수정 중 PR queue index가 더 많이 쓰일 수 있다.
- packet/index write-order 변경과 reconcile pass를 같은 patch set으로 포함한다.
- 이 단계 이후 runner를 재시작해도 pending packet, orphan index, missing packet reference가 진단 가능해야 한다.

검증:

```powershell
python -m pytest -q tests/test_pr_queue.py
```

### Step 3. F4 failure metadata contract 정리

이유:

- shared helper가 packet/report schema에 disposition을 넣기 전에 `retry_eligible`과 `retry_allowed_now` 의미를 확정한다.
- 이 순서를 지키면 helper 추출 후 packet schema 마이그레이션을 피할 수 있다.
- 만약 F4를 뒤로 미루면, Step 4 helper는 disposition/retry metadata를 새로 주입하지 않는다.

검증:

```powershell
python -m pytest -q tests/test_failure_policy.py tests/test_task_status_reporting.py tests/test_dependency_blocking_detail.py
```

### Step 4. F1 shared helper 추출 및 Codex call site 교체

이유:

- 기존 Codex behavior가 바뀌지 않는지 먼저 확인한다.
- helper를 안정화한 뒤 Claude backend에 붙인다.

검증:

```powershell
python -m pytest -q tests/test_pr_queue.py tests/test_worktree_isolation.py
```

### Step 5. Claude backend PR queue 연결 + F2 guard 동시 반영

이유:

- backend parity 작업은 한 번에 끝내야 한다.
- branch preserve semantics를 바꾸는 경우 phantom guard가 같은 PR/commit에 있어야 한다.

검증:

```powershell
python -m pytest -q tests/test_pr_queue.py tests/test_worktree_isolation.py
python -m pytest -q tests/test_task_status.py tests/test_failure_policy.py tests/test_task_history_status.py tests/test_task_status_reporting.py
```

가능하면 claudecode path는 monkeypatch 기반 unit test를 추가한다.

### Step 6. F5 path guard fail-closed

이유:

- runner start contract path와 Web path 양쪽 영향이 있어, PR queue 작업과 분리해서 검증한다.

검증:

```powershell
python -m pytest -q tests/test_stop_progress.py tests/test_web_console_safety.py tests/test_web_console_readonly.py
```

### Step 7. 문서 동기화

검증:

```powershell
python -m pytest -q tests/test_docs_validation.py
```

### Step 8. 통합 검증

최소:

```powershell
python -m pytest -q tests/test_pr_queue.py tests/test_worktree_isolation.py tests/test_failure_policy.py tests/test_stop_progress.py tests/test_web_console_safety.py tests/test_docs_validation.py
```

가능하면 전체:

```powershell
python -m pytest -q
```

---

## 4. 커밋 분할 제안

현재 runner 종료 후 작업할 때 권장 커밋 단위:

1. `Stabilize PR queue branch index updates`
   - F3 cross-process advisory lock + thread lock + tests
2. `Clarify failure retry metadata`
   - F4 + tests
3. `Share local PR queue packet creation across backends`
   - helper 추출 + Codex call site 교체 + tests
4. `Queue Claude backend review packets safely`
   - Claude backend 연결 + F2 parity guard + tests
5. `Fail closed when runner start approved roots are unavailable`
   - F5 + tests
6. `Refresh backend parity and PR queue docs`
   - CLAUDE.md/docs 갱신

이 노트 자체는 커밋하지 않는다.

---

## 5. 최종 acceptance criteria

- Codex backend:
  - 기존 PR queue packet 생성 테스트가 모두 통과한다.
  - isolated worktree에서 source main이 mutating되지 않는다.
- Claude backend:
  - `--execution-backend claudecode` 경로에서도 PR queue packet이 생성된다.
  - worktree isolation 사용 시 phantom completion guard가 preserved branch 또는 current HEAD를 올바르게 본다.
- PR queue:
  - concurrent index update에서 entry 손실이 없다.
  - packet write와 validation update가 같은 `branch_index.json`을 안전하게 갱신한다.
- Runner start security:
  - approved roots가 없으면 explicit path override가 허용되지 않는다.
  - Web runner start는 approved roots를 정상 전달해 기존 UX를 유지한다.
- Failure policy:
  - `retry_eligible`과 `retry_allowed_now` 의미가 분리된다.
  - legacy `auto_retry_allowed`는 당분간 깨지지 않는다.
- Docs:
  - `CLAUDE.md` module map이 실제 주요 모듈과 맞는다.
  - user-facing docs가 PR queue/task status/failure policy를 설명한다.

---

## 6. 이번 검토에서 실행한 검증

```powershell
python -m pytest tests/test_pr_queue.py tests/test_worktree_isolation.py tests/test_failure_policy.py tests/test_web_console_safety.py -vv --tb=short
```

결과:

- 109 passed
- 27 subtests passed
- 실행 시간: 106.75s

주의:

- 첫 `-q` 실행은 `....`까지만 출력하고 exit 1로 끝났지만, 같은 범위를 `-vv --tb=short`로 재실행했을 때 전부 통과했다.
- 전체 test suite는 아직 실행하지 않았다.
