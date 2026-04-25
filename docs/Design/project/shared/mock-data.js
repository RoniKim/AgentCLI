// Shared mock data for AgentCLI web visualization
// Based on the real AgentCLI CLI data shapes (BACKLOG, STATE, GOALS, metrics)

window.MOCK_SCHEMA_VERSION = 'v2-ko';
window.MOCK = (function() {
  const now = Date.now();
  const m = (offset) => new Date(now - offset * 60000);

  return {
    activeRun: {
      id: 'run_20260424_142311',
      repo: 'C:/Dev/BudgetBook',
      branch: 'feat/category-rules',
      backend: 'claudecode',
      startedAt: m(47),
      stage: 'Dev',
      stageIndex: 1, // 0=PM, 1=Dev, 2=QA
      iteration: 3,
      maxIterations: 5,
      progress: 0.62,
      budgetUsed: 0.41, // 41% of budget
      tokens: { in: 184203, out: 42918 },
      quota: { window: '5h', used: 0.34 },
      elapsedSec: 47 * 60 + 12,
    },

    stages: [
      { id: 'PM',  label: 'PM',  title: 'Backlog Planning',   status: 'done',    durationSec: 312, model: 'opus-4.5' },
      { id: 'Dev', label: 'Dev', title: 'Implementation',     status: 'running', durationSec: 1840, model: 'sonnet-4.5' },
      { id: 'QA',  label: 'QA',  title: 'Verification',       status: 'pending', durationSec: 0,   model: 'haiku-4.5' },
    ],

    backlog: [
      { id: 'T-017', title: 'Add rule-based category suggestion on transaction import', status: 'in_progress', priority: 'P0', tags: ['feature','category'], estimate: 'M', skill: 'categorize-transactions' },
      { id: 'T-018', title: 'Persist manual category overrides to SQLite', status: 'in_progress', priority: 'P0', tags: ['persistence'], estimate: 'S', skill: null },
      { id: 'T-019', title: 'QA: regression tests for existing rule engine', status: 'pending', priority: 'P0', tags: ['test','qa'], estimate: 'S', skill: null },
      { id: 'T-020', title: 'Budget over/under visual on dashboard', status: 'pending', priority: 'P1', tags: ['ui'], estimate: 'M', skill: null },
      { id: 'T-021', title: 'Export monthly report as PDF', status: 'pending', priority: 'P1', tags: ['export'], estimate: 'L', skill: 'pdf-export' },
      { id: 'T-015', title: 'Fix: duplicate rows after CSV re-import', status: 'done', priority: 'P0', tags: ['bug'], estimate: 'S', skill: null },
      { id: 'T-016', title: 'Refactor IngestService to async', status: 'done', priority: 'P1', tags: ['refactor'], estimate: 'M', skill: null },
      { id: 'T-014', title: 'Add CI smoke test', status: 'failed', priority: 'P1', tags: ['ci'], estimate: 'S', skill: null },
    ],

    goals: {
      p0: [
        { done: true,  text: 'Transaction import pipeline handles CSV/XLSX without dup rows' },
        { done: true,  text: 'Category rule engine replaces hardcoded keyword list' },
        { done: false, text: 'Manual category overrides survive re-import' },
        { done: false, text: 'Monthly budget view shows over/under per category' },
      ],
      p1: [
        { done: true,  text: 'IngestService is fully async' },
        { done: false, text: 'PDF export for monthly report' },
        { done: false, text: 'Dark mode on Settings screen' },
        { done: false, text: 'Keyboard-first nav (j/k, /, g+d)' },
      ],
    },

    runs: [
      { id: 'run_20260424_142311', startedAt: m(47),  status: 'running',  tasksDone: 2, tasksTotal: 5, branch: 'feat/category-rules',    durationSec: null },
      { id: 'run_20260424_083200', startedAt: m(368), status: 'success',  tasksDone: 4, tasksTotal: 4, branch: 'fix/ingest-dup',         durationSec: 2730 },
      { id: 'run_20260423_231100', startedAt: m(918), status: 'success',  tasksDone: 3, tasksTotal: 3, branch: 'refactor/ingest-async',  durationSec: 1880 },
      { id: 'run_20260423_140022', startedAt: m(1490),status: 'failed',   tasksDone: 1, tasksTotal: 3, branch: 'feat/pdf-export',        durationSec: 1210 },
      { id: 'run_20260422_212211', startedAt: m(2870),status: 'success',  tasksDone: 2, tasksTotal: 2, branch: 'chore/ci-smoke',         durationSec: 940 },
      { id: 'run_20260422_091500', startedAt: m(3610),status: 'stopped',  tasksDone: 0, tasksTotal: 3, branch: 'feat/dark-settings',     durationSec: 610 },
      { id: 'run_20260421_184400', startedAt: m(4420),status: 'success',  tasksDone: 5, tasksTotal: 5, branch: 'feat/rule-engine',       durationSec: 3240 },
    ],

    // Sparkline-friendly series
    metrics: {
      tokens24h: [320,410,680,540,720,880,1120,980,1240,1680,1440,1820,2040,1920,2280,2610,2480,2930,3120,2880,3240,3680,3540,4120],
      success24h: [1,1,0,1,1,1,1,0,1,1,1,1,0,1,1,1,1,1,0,1,1,1,1,1],
      // 0..1 budget over iterations
      budget: [0.05, 0.11, 0.18, 0.24, 0.31, 0.36, 0.41],
    },

    logs: [
      { t: '14:23:11', lvl: 'info',  stage: 'boot',  msg: 'AgentCLI v0.8.2 · backend=claudecode · repo=BudgetBook' },
      { t: '14:23:12', lvl: 'info',  stage: 'boot',  msg: 'Loaded config: ~/.agentcli/configs/budgetbook.json' },
      { t: '14:23:14', lvl: 'info',  stage: 'PM',    msg: 'PM stage started · model=opus-4.5' },
      { t: '14:23:18', lvl: 'debug', stage: 'PM',    msg: 'Reading GOALS.md (4 P0, 4 P1)' },
      { t: '14:24:02', lvl: 'info',  stage: 'PM',    msg: 'PM emitted 5 tasks (schema v2 OK)' },
      { t: '14:24:03', lvl: 'info',  stage: 'PM',    msg: 'BACKLOG.json written · 5 tasks' },
      { t: '14:24:05', lvl: 'info',  stage: 'Dev',   msg: 'Dev stage started · task=T-017' },
      { t: '14:26:41', lvl: 'debug', stage: 'Dev',   msg: 'skills match: categorize-transactions (0.82)' },
      { t: '14:31:07', lvl: 'info',  stage: 'Dev',   msg: 'edit: src/category/rules.py (+148 -22)' },
      { t: '14:33:20', lvl: 'info',  stage: 'Dev',   msg: 'edit: src/category/suggest.py (+82)' },
      { t: '14:36:55', lvl: 'info',  stage: 'Dev',   msg: 'T-017 complete · moving to T-018' },
      { t: '14:37:02', lvl: 'info',  stage: 'Dev',   msg: 'Dev stage · task=T-018' },
      { t: '14:44:18', lvl: 'warn',  stage: 'Dev',   msg: 'build: 2 type warnings in suggest.py (non-blocking)' },
      { t: '14:51:39', lvl: 'info',  stage: 'Dev',   msg: 'edit: src/db/overrides.sql (+34)' },
      { t: '15:02:44', lvl: 'info',  stage: 'Dev',   msg: 'checkpoint: c14b3ee · T-018 progress 70%' },
    ],

    notifications: [
      { t: m(2),   kind: 'task_done',   text: 'T-017 · Rule-based category suggestion · merged', run: 'run_20260424_142311' },
      { t: m(12),  kind: 'quota',       text: 'Claude quota window 5h · 34% used',                run: 'run_20260424_142311' },
      { t: m(47),  kind: 'run_start',   text: 'Run started · feat/category-rules',                run: 'run_20260424_142311' },
      { t: m(368), kind: 'run_stop',    text: 'Run finished · 4/4 tasks · 45m 30s',               run: 'run_20260424_083200' },
      { t: m(918), kind: 'task_failed', text: 'T-014 · CI smoke · retry escalated to opus',       run: 'run_20260423_231100' },
      { t: m(1490),kind: 'error',       text: 'Stage Dev crashed · BudgetExceeded',               run: 'run_20260423_140022' },
      { t: m(2870),kind: 'stalled',     text: 'No metrics for 10m — marked stalled',              run: 'run_20260422_212211' },
    ],

    config: {
      repo: 'C:/Dev/BudgetBook',
      execution_backend: 'claudecode',
      roles: ['PM','Dev','QA'],
      autopilot: true,
      continuous: true,
      iterations: 5,
      worktree_isolation: false,
      run_tests: true,
      budget: { max_usd: 8.0, max_iters: 5, max_continuations: 3 },
      claudecode: {
        dev_model: 'sonnet',
        dev_model_tier1: 'opus',
        qa_model: 'haiku',
        reporter_model: 'haiku',
      },
      telegram: { enabled: true, instance_name: 'home-pc-main' },
    },

    // Schema describing each config field — used by the drawer editor.
    // restart: does the runner need restart for this change to take effect
    // kind: ui control type
    configSchema: {
      'repo': { kind:'text', restart:true, desc:'Absolute path to the repo AgentCLI will operate on.', hint:'Windows paths use forward-slash. CLI: --repo PATH' },
      'execution_backend': { kind:'enum', options:['codex','claudecode'], restart:true, desc:'Which agent runtime to use for Dev & QA stages.', hint:'codex = OpenAI Codex CLI · claudecode = Anthropic Claude Code CLI' },
      'roles': { kind:'multienum', options:['PM','Dev','QA','Reporter'], restart:false, desc:'Which stages run in the pipeline. Order matters.', hint:'PM must come first. Reporter appends a summary after QA.' },
      'autopilot': { kind:'bool', restart:false, desc:'Skip interactive confirmation prompts.', hint:'When false, the CLI pauses between stages and waits for y/n.' },
      'continuous': { kind:'bool', restart:false, desc:'Automatically chain PM → Dev → QA without stopping.', hint:'Pair with autopilot=true for unattended runs.' },
      'iterations': { kind:'number', min:1, max:20, restart:false, desc:'Max pipeline iterations per run.', hint:'Each iteration = one full PM→Dev→QA cycle.' },
      'worktree_isolation': { kind:'bool', restart:true, desc:'Run inside a fresh git worktree to isolate changes.', hint:'Recommended for shared machines. Adds ~10s startup.' },
      'run_tests': { kind:'bool', restart:false, desc:'Execute test suite during QA stage.' },
      'budget.max_usd': { kind:'number', min:0.5, max:100, step:0.5, restart:false, desc:'Hard spend cap in USD. Runner stops when reached.' },
      'budget.max_iters': { kind:'number', min:1, max:20, restart:false, desc:'Safety cap; same as iterations unless explicitly different.' },
      'budget.max_continuations': { kind:'number', min:0, max:10, restart:false, desc:'Max times Dev can continue a capped response.' },
      'claudecode.dev_model': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'Default model for Dev stage.', hint:'Escalates to tier1 on repeated retries.' },
      'claudecode.dev_model_tier1': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'Escalation target when Dev retries.' },
      'claudecode.qa_model': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'Model for QA stage. Haiku is usually enough.' },
      'claudecode.reporter_model': { kind:'enum', options:['haiku','sonnet','opus'], restart:false, desc:'Model used to summarize the run.' },
      'telegram.enabled': { kind:'bool', restart:true, desc:'Mirror events to a Telegram chat.' },
      'telegram.instance_name': { kind:'text', restart:false, desc:'Friendly name surfaced in Telegram messages.', hint:'Useful when multiple runners share one chat.' },
    },
  };
})();
