(function () {
  const ROOT = document.getElementById('app');
  if (!ROOT) {
    return;
  }

  const STORAGE = {
    view: 'agentcli.console.view.v1',
    goals: 'agentcli.console.goals.v1',
    config: 'agentcli.console.config.v1',
    worktree: 'agentcli.console.worktree.v1',
  };

  const VIEW_SHORTCUTS = {
    dashboard: 'g d',
    pipeline: 'g p',
    logs: 'g l',
    backlog: 'g b',
    goals: 'g g',
    config: 'g c',
    prompts: 'g t',
    history: 'g r',
    notifications: 'g n',
    worktree: 'g w',
    landing: 'g h',
    mobile: 'g m',
  };

  const VIEW_LABELS = {
    dashboard: 'Dashboard',
    pipeline: 'Pipeline',
    logs: 'Logs',
    backlog: 'Backlog',
    goals: 'Goals',
    config: 'Config',
    prompts: 'Prompts',
    history: 'Run History',
    notifications: 'Notifications',
    worktree: 'Worktree Review',
    landing: 'Landing preview',
    mobile: 'Mobile preview',
  };

  const RUNNER_CONTROL_CONFIRMATIONS = {
    start: 'START RUNNER',
    stop: 'STOP RUNNER',
    reload: 'RELOAD RUNNER',
    restart: 'RESTART RUNNER',
  };

  function nowMs() {
    return Date.now();
  }

  const START = nowMs();

  function minutesAgo(n) {
    return START - n * 60 * 1000;
  }

  function hoursAgo(n) {
    return START - n * 60 * 60 * 1000;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function readJSON(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  }

  function writeJSON(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Ignore storage failures in file:// or restricted environments.
    }
  }

  function escapeHTML(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function deepMerge(base, over) {
    if (over == null || typeof over !== 'object' || Array.isArray(over)) {
      return over == null ? base : over;
    }
    const out = Array.isArray(base) ? base.slice() : { ...(base || {}) };
    for (const key of Object.keys(over)) {
      const baseValue = out[key];
      const overValue = over[key];
      if (
        overValue &&
        typeof overValue === 'object' &&
        !Array.isArray(overValue) &&
        baseValue &&
        typeof baseValue === 'object' &&
        !Array.isArray(baseValue)
      ) {
        out[key] = deepMerge(baseValue, overValue);
      } else {
        out[key] = overValue;
      }
    }
    return out;
  }

  function getAt(obj, path) {
    return path.split('.').reduce((cur, key) => (cur == null ? undefined : cur[key]), obj);
  }

  function setAt(obj, path, value) {
    const parts = path.split('.');
    const out = clone(obj || {});
    let cur = out;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const part = parts[i];
      if (cur[part] == null || typeof cur[part] !== 'object') {
        cur[part] = {};
      }
      cur = cur[part];
    }
    cur[parts[parts.length - 1]] = value;
    return out;
  }

  function fmtDuration(sec) {
    if (sec == null || Number.isNaN(Number(sec))) return '--';
    const total = Math.max(0, Math.round(Number(sec)));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function fmtPercent(value) {
    if (value == null || value === '' || Number.isNaN(Number(value))) return '--';
    return `${Math.round(Math.max(0, Math.min(1, Number(value))) * 100)}%`;
  }

  function fmtMoney(value) {
    if (value == null || value === '' || Number.isNaN(Number(value))) return '--';
    return `$${Number(value).toFixed(2)}`;
  }

  function fmtRelative(ts) {
    if (!ts) return '--';
    const diff = (nowMs() - Number(ts || 0)) / 1000;
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function fmtClock(ts) {
    if (!ts) return '--';
    return new Date(ts).toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  function fmtTime(ts) {
    if (!ts) return '--';
    return new Date(ts).toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function fmtDateTime(ts) {
    if (ts == null || ts === '' || Number.isNaN(Number(ts))) return '--';
    return new Date(Number(ts) * 1000).toLocaleString('en-GB', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function fmtNumberShort(value) {
    if (value == null || value === '' || Number.isNaN(Number(value))) return '--';
    const n = Number(value);
    if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (Math.abs(n) >= 1_000) return `${Math.round(n / 1_000)}k`;
    return `${n}`;
  }

  function metricText(available, value, formatter, unavailableText = 'unavailable') {
    if (!available || value == null || value === '' || Number.isNaN(Number(value))) {
      return unavailableText;
    }
    return formatter ? formatter(value) : String(value);
  }

  function metricWidth(available, value) {
    if (!available || value == null || value === '' || Number.isNaN(Number(value))) {
      return '0%';
    }
    return progressWidth(value);
  }

  function normalizeListValues(values) {
    if (Array.isArray(values)) {
      return values.map((item) => toText(item, '').trim()).filter(Boolean);
    }
    if (typeof values === 'string') {
      return values.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
    }
    if (values == null) {
      return [];
    }
    return [toText(values, '').trim()].filter(Boolean);
  }

  function fmtList(values) {
    return normalizeListValues(values).join(', ');
  }

  function progressWidth(value) {
    const pct = Math.max(0, Math.min(100, Math.round((Number(value) || 0) * 100)));
    return `${pct}%`;
  }

  function isValidView(view) {
    return Object.prototype.hasOwnProperty.call(VIEW_LABELS, view);
  }

  function normalizeView(view) {
    return isValidView(view) ? view : 'dashboard';
  }

  function isEditableTarget(target) {
    return target && (target.matches('input, textarea, select, [contenteditable="true"]') || target.isContentEditable);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', 'readonly');
    ta.style.position = 'fixed';
    ta.style.left = '-10000px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
    } catch {
      // Ignore.
    }
    document.body.removeChild(ta);
    return Promise.resolve();
  }

  function buildSparkline(data, width = 180, height = 44, fill = 'rgba(126,227,138,0.12)', stroke = '#7ee38a') {
    if (!data || !data.length) {
      return '';
    }
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = (max - min) || 1;
    const step = width / Math.max(1, data.length - 1);
    const points = data.map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / range) * (height * 0.8) - height * 0.1;
      return [x, y];
    });
    const line = points
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point[0].toFixed(1)} ${point[1].toFixed(1)}`)
      .join(' ');
    const area = `${line} L ${width} ${height} L 0 ${height} Z`;
    return `
      <svg class="sparkline" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
        <path d="${area}" fill="${fill}"></path>
        <path d="${line}" fill="none" stroke="${stroke}" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path>
      </svg>
    `;
  }

  function statusClass(status) {
    if (status === 'running' || status === 'live') return 'status-chip status-chip--running';
    if (status === 'warn' || status === 'stopped' || status === 'manual') return 'status-chip status-chip--warn';
    if (status === 'error' || status === 'failed') return 'status-chip status-chip--err';
    return 'status-chip';
  }

  function runStatusLabel(status, finalReason = '') {
    const normalized = String(status || 'idle').toLowerCase();
    if (normalized === 'completed') return 'completed';
    if (normalized === 'success' || normalized === 'complete' || normalized === 'done') {
      return finalReason ? 'completed' : 'success';
    }
    if (normalized === 'running') return 'running';
    if (normalized === 'stopping' || normalized === 'stopped') return 'stopped';
    if (normalized === 'failed' || normalized === 'error') return 'failed';
    if (normalized === 'idle') return 'idle';
    return normalized || 'idle';
  }

  function runStatusTone(status, finalReason = '') {
    const normalized = String(status || 'idle').toLowerCase();
    if (normalized === 'success' || normalized === 'completed' || normalized === 'complete' || normalized === 'done') {
      return finalReason ? 'completed' : 'success';
    }
    if (normalized === 'running') return 'running';
    if (normalized === 'stopping' || normalized === 'stopped') return 'stopped';
    if (normalized === 'failed' || normalized === 'error') return 'failed';
    if (normalized === 'idle') return 'idle';
    return 'idle';
  }

  const RUN_STATUS_CLASS_NAMES = {
    idle: 'status-chip status-chip--idle',
    running: 'status-chip status-chip--running',
    completed: 'status-chip status-chip--completed',
    success: 'status-chip status-chip--success',
    stopped: 'status-chip status-chip--stopped',
    failed: 'status-chip status-chip--failed',
  };

  const RUN_BANNER_CLASS_NAMES = {
    idle: 'modal-banner section-banner section-banner--idle',
    running: 'modal-banner section-banner section-banner--running',
    completed: 'modal-banner section-banner section-banner--completed',
    success: 'modal-banner section-banner section-banner--success',
    stopped: 'modal-banner section-banner section-banner--stopped',
    failed: 'modal-banner section-banner section-banner--failed',
  };

  function runStatusClass(status, finalReason = '') {
    const tone = runStatusTone(status, finalReason);
    return RUN_STATUS_CLASS_NAMES[tone] || RUN_STATUS_CLASS_NAMES.idle;
  }

  function runBannerClass(status, finalReason = '') {
    const tone = runStatusTone(status, finalReason);
    return RUN_BANNER_CLASS_NAMES[tone] || RUN_BANNER_CLASS_NAMES.idle;
  }

  function severityClass(level) {
    if (level === 'warn') return 'log-row log-row--warn';
    if (level === 'err') return 'log-row log-row--err';
    if (level === 'debug') return 'log-row log-row--debug';
    return 'log-row log-row--info';
  }

  function priorityColor(priority) {
    if (priority === 'P0') return 'var(--err)';
    if (priority === 'P1') return 'var(--warn)';
    return 'var(--info)';
  }

  function kindColor(kind) {
    if (kind === 'task_done') return 'var(--accent)';
    if (kind === 'quota') return 'var(--warn)';
    if (kind === 'error' || kind === 'task_failed') return 'var(--err)';
    if (kind === 'stalled') return 'var(--warn)';
    return 'var(--info)';
  }

  function statusDotClass(status) {
    if (status === 'done' || status === 'success' || status === 'completed') return 'dot status-chip__dot';
    if (status === 'running') return 'dot dot--pulse';
    if (status === 'failed' || status === 'error') return 'dot status-chip__dot';
    if (status === 'stopped') return 'dot status-chip__dot';
    return 'dot status-chip__dot';
  }

  const MAX_LOG_ROWS = 120;
  const SNAPSHOT_POLL_MS = 15000;
  const STALE_AFTER_MS = 30000;
  const STAGE_INDEX = {
    idle: 0,
    pm: 0,
    dev: 1,
    qa: 2,
    security: 3,
    reporter: 4,
  };

  function toText(value, fallback = '') {
    if (value == null) {
      return fallback;
    }
    const text = String(value).trim();
    return text || fallback;
  }

  function toNumber(value, fallback = 0) {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
  }

  function toMaybeNumber(value) {
    if (value == null || value === '') return null;
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  function toArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function toObject(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function clampUnit(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) {
      return 0;
    }
    return Math.max(0, Math.min(1, num));
  }

  function runnerControlConfirmationPhrase(action) {
    const normalized = action === 'restart' ? 'reload' : action;
    return RUNNER_CONTROL_CONFIRMATIONS[normalized] || RUNNER_CONTROL_CONFIRMATIONS.reload;
  }

  function runnerControlActionLabel(action, busy = false) {
    const labels = {
      start: 'Start',
      stop: 'Stop',
      reload: 'Reload',
      restart: 'Restart',
    };
    const label = labels[action] || 'Run';
    if (!busy) {
      return label;
    }
    const busyLabels = {
      start: 'Starting...',
      stop: 'Stopping...',
      reload: 'Reloading...',
      restart: 'Restarting...',
    };
    return busyLabels[action] || 'Working...';
  }

  function runnerControlModalTitle(action) {
    const titles = {
      start: 'Confirm start',
      stop: 'Confirm stop',
      reload: 'Confirm reload',
      restart: 'Confirm restart',
    };
    return titles[action] || 'Confirm runner action';
  }

  function runnerControlActionSummary(action) {
    const summaries = {
      start: 'Start the runner with the current configuration snapshot.',
      stop: 'Stop the current runner and create a stop request for the controller.',
      reload: 'Stop the current runner, wait for it to settle, then start again.',
      restart: 'Restart the runner from the current snapshot.',
    };
    return summaries[action] || 'Confirm this runner control action.';
  }

  function createRunnerControlModel(overrides = {}) {
    const enabled = Boolean(overrides.enabled);
    const running = Boolean(overrides.running);
    const controllerAvailable = Boolean(overrides.controllerAvailable);
    const busy = Boolean(overrides.busy);
    const source = toText(overrides.source, 'default');
    const runStatus = toText(overrides.runStatus, running ? 'running' : 'idle');
    const message = toText(
      overrides.message,
      controllerAvailable
        ? enabled
          ? running
            ? 'Runner controls are enabled and the controller reports a running runner.'
            : 'Runner controls are enabled and the controller reports a stopped runner.'
          : 'Runner controls are disabled until the server opt-in is enabled.'
        : 'Runner controller is unavailable.'
    );
    return {
      enabled,
      source,
      controllerAvailable,
      busy,
      message,
      runStatus,
      status: {
        running,
        runnerMode: toText(overrides.runnerMode, 'unknown'),
        repo: toText(overrides.repo, ''),
        runDir: toText(overrides.runDir, ''),
        uptimeSeconds: toNumber(overrides.uptimeSeconds, 0),
        exitCode: overrides.exitCode == null ? null : overrides.exitCode,
        stopFile: toText(overrides.stopFile, 'STOP'),
        stopFileExists: Boolean(overrides.stopFileExists),
        done: toNumber(overrides.done, 0),
        failed: toNumber(overrides.failed, 0),
        warnings: toNumber(overrides.warnings, 0),
        reason: toText(overrides.reason, ''),
        lastEvent: toText(overrides.lastEvent, ''),
      },
      actions: {
        start: {
          enabled: Boolean(overrides.startEnabled),
          disabledReason: toText(overrides.startDisabledReason, ''),
          busy: false,
        },
        stop: {
          enabled: Boolean(overrides.stopEnabled),
          disabledReason: toText(overrides.stopDisabledReason, ''),
          busy: false,
        },
        reload: {
          enabled: Boolean(overrides.reloadEnabled),
          disabledReason: toText(overrides.reloadDisabledReason, ''),
          busy: false,
        },
        restart: {
          enabled: Boolean(overrides.restartEnabled),
          disabledReason: toText(overrides.restartDisabledReason, ''),
          busy: false,
        },
      },
      confirmation: {
        start: runnerControlConfirmationPhrase('start'),
        stop: runnerControlConfirmationPhrase('stop'),
        reload: runnerControlConfirmationPhrase('reload'),
        restart: runnerControlConfirmationPhrase('restart'),
      },
      lastAction: toText(overrides.lastAction, ''),
      lastMessage: toText(overrides.lastMessage, ''),
      lastError: toText(overrides.lastError, ''),
    };
  }

  function normalizeRunnerControlAction(action) {
    const raw = toObject(action);
    return {
      enabled: Boolean(raw.enabled),
      disabledReason: toText(raw.disabledReason || raw.disabled_reason, ''),
      busy: Boolean(raw.busy),
    };
  }

  function normalizeRunnerControl(control) {
    const raw = toObject(control);
    if (!Object.keys(raw).length) {
      return createRunnerControlModel({
        source: 'api',
        message: 'Runner control status is not available yet.',
        controllerAvailable: false,
        enabled: false,
        running: false,
        runStatus: 'loading',
        runnerMode: 'unknown',
      });
    }
    const status = toObject(raw.status);
    const actions = toObject(raw.actions);
    const confirmation = toObject(raw.confirmation);
    const message = toText(raw.message, '');
    const enabled = Boolean(raw.enabled);
    const controllerAvailable = Boolean(raw.controller_available || raw.controllerAvailable);
    const running = Boolean(status.running || raw.running);
    const busy = Boolean(raw.busy);
    return {
      enabled,
      source: toText(raw.source, 'api'),
      controllerAvailable,
      busy,
      message: message || (controllerAvailable ? (enabled ? (running ? 'Runner controls are enabled and the controller reports a running runner.' : 'Runner controls are enabled and the controller reports a stopped runner.') : 'Runner controls are disabled until the server opt-in is enabled.') : 'Runner controller is unavailable.'),
      runStatus: toText(raw.run_status || raw.runStatus || '', running ? 'running' : 'idle'),
      status: {
        running,
        runnerMode: toText(status.runner_mode || status.runnerMode, 'unknown'),
        repo: toText(status.repo, ''),
        runDir: toText(status.run_dir || status.runDir, ''),
        uptimeSeconds: toNumber(status.uptime_seconds || status.uptimeSeconds, 0),
        exitCode: status.exit_code == null ? null : status.exit_code,
        stopFile: toText(status.stop_file || status.stopFile, 'STOP'),
        stopFileExists: Boolean(status.stop_file_exists || status.stopFileExists),
        done: toNumber(status.done, 0),
        failed: toNumber(status.failed, 0),
        warnings: toNumber(status.warnings, 0),
        reason: toText(status.reason, ''),
        lastEvent: toText(status.last_event || status.lastEvent, ''),
      },
      actions: {
        start: normalizeRunnerControlAction(actions.start),
        stop: normalizeRunnerControlAction(actions.stop),
        reload: normalizeRunnerControlAction(actions.reload),
        restart: normalizeRunnerControlAction(actions.restart),
      },
      confirmation: {
        start: toText(confirmation.start, runnerControlConfirmationPhrase('start')),
        stop: toText(confirmation.stop, runnerControlConfirmationPhrase('stop')),
        reload: toText(confirmation.reload, runnerControlConfirmationPhrase('reload')),
        restart: toText(confirmation.restart, runnerControlConfirmationPhrase('restart')),
      },
      lastAction: toText(raw.last_action || raw.lastAction, ''),
      lastMessage: toText(raw.last_message || raw.lastMessage, ''),
      lastError: toText(raw.last_error || raw.lastError, ''),
    };
  }

  function normalizeRunStatus(rawStatus, hasRunData) {
    const status = toText(rawStatus, 'idle').toLowerCase();
    if (!hasRunData || status === 'no-run') {
      return 'idle';
    }
    if (status === 'completed' || status === 'complete' || status === 'done' || status === 'success') {
      return 'success';
    }
    if (status === 'finished' || status === 'stopping' || status === 'stop_requested') {
      return 'stopped';
    }
    if (status === 'error') {
      return 'failed';
    }
    return status || 'idle';
  }

  function normalizeStageStatus(rawStatus, fallback = 'pending') {
    const status = toText(rawStatus, '').trim().toLowerCase();
    if (!status) {
      return fallback;
    }
    const aliases = {
      complete: 'done',
      completed: 'done',
      done: 'done',
      ok: 'done',
      success: 'done',
      skip: 'skipped',
      skipped: 'skipped',
      stop: 'stopped',
      stopped: 'stopped',
      halted: 'stopped',
      cancelled: 'stopped',
      canceled: 'stopped',
      fail: 'failed',
      failed: 'failed',
      error: 'failed',
      running: 'running',
      active: 'running',
      in_progress: 'running',
      pending: 'pending',
      idle: 'pending',
    };
    return aliases[status] || fallback;
  }

  function compactText(value, maxChars = 180) {
    const text = toText(value, '').replace(/\s+/g, ' ').trim();
    if (!text) return '';
    if (text.length <= maxChars) return text;
    return `${text.slice(0, Math.max(0, maxChars - 3)).trimEnd()}...`;
  }

  function lifecycleStatusToneClass(status) {
    switch (normalizeStageStatus(status, 'pending')) {
      case 'done':
        return 'chip--accent';
      case 'running':
        return 'chip--warn';
      case 'failed':
        return 'chip--err';
      case 'stopped':
      case 'skipped':
        return 'chip--info';
      default:
        return 'chip--info';
    }
  }

  function normalizeBacklogStatus(rawStatus, fallback = 'pending') {
    const status = toText(rawStatus, '').trim().toLowerCase();
    if (!status) {
      return fallback;
    }
    const aliases = {
      complete: 'done',
      completed: 'done',
      done: 'done',
      ok: 'done',
      success: 'done',
      fail: 'failed',
      failed: 'failed',
      error: 'failed',
      running: 'in_progress',
      active: 'in_progress',
      in_progress: 'in_progress',
      pending: 'pending',
      idle: 'pending',
    };
    return aliases[status] || fallback;
  }

  function backlogStatusToneClass(status) {
    switch (normalizeBacklogStatus(status, 'pending')) {
      case 'done':
        return 'chip--accent';
      case 'in_progress':
        return 'chip--warn';
      case 'failed':
        return 'chip--err';
      default:
        return 'chip--info';
    }
  }

  function lifecycleStageCardClass(status) {
    const classes = ['stage-card'];
    switch (normalizeStageStatus(status, 'pending')) {
      case 'running':
        classes.push('stage-card--running');
        break;
      case 'failed':
        classes.push('stage-card--failed');
        break;
      case 'stopped':
        classes.push('stage-card--stopped');
        break;
      default:
        break;
    }
    return classes.join(' ');
  }

  function lifecycleStageIconClass(status) {
    switch (normalizeStageStatus(status, 'pending')) {
      case 'done':
        return 'stage-icon stage-icon--done';
      case 'running':
        return 'stage-icon stage-icon--running';
      case 'failed':
        return 'stage-icon stage-icon--failed';
      case 'stopped':
        return 'stage-icon stage-icon--stopped';
      default:
        return 'stage-icon stage-icon--pending';
    }
  }

  function lifecycleStageIconText(status) {
    switch (normalizeStageStatus(status, 'pending')) {
      case 'done':
        return 'OK';
      case 'running':
        return 'RUN';
      case 'failed':
        return 'ERR';
      case 'stopped':
        return 'STOP';
      case 'skipped':
        return 'SKIP';
      default:
        return 'WAIT';
    }
  }

  function buildSectionState(kind, rawStatus, message, source = 'api') {
    const status = toText(rawStatus, 'ready');
    return {
      kind,
      status,
      message: message || '',
      source,
    };
  }

  function fallbackSectionMessage(kind) {
    const messages = {
      activeRun: 'No active run is published yet.',
      stages: 'No lifecycle records were published yet.',
      backlog: 'No backlog artifacts were published yet.',
      goals: 'No goals were found in GOALS.md.',
      config: 'Config snapshot is incomplete.',
      prompts: 'Prompt inventory is empty.',
      logs: 'No log entries are available yet.',
      notifications: 'No notifications have been recorded yet.',
      metrics: 'No metrics snapshot is available yet.',
      history: 'Run history is empty.',
      worktree: 'No pending worktree merge is available.',
      runnerControl: 'Runner controls are unavailable in fallback mode.',
    };
    return messages[kind] || 'No data available yet.';
  }

  function normalizeLogLevel(level) {
    const value = toText(level, 'info').toLowerCase();
    if (['debug', 'info', 'warn', 'err'].includes(value)) {
      return value;
    }
    if (value === 'error') {
      return 'err';
    }
    return 'info';
  }

  function normalizeLogStage(stage) {
    return toText(stage, 'boot');
  }

  function normalizeLogEntry(entry) {
    const raw = toObject(entry);
    return {
      t: toText(raw.t || raw.ts, fmtClock(nowMs())),
      lvl: normalizeLogLevel(raw.lvl || raw.level),
      stage: normalizeLogStage(raw.stage || raw.component || raw.scope),
      msg: toText(raw.msg || raw.message || raw.text, ''),
    };
  }

  function normalizeNotification(entry) {
    const raw = toObject(entry);
    return {
      t: toNumber(raw.t || raw.ts || 0, 0),
      kind: toText(raw.kind || raw.type, 'info'),
      text: toText(raw.text || raw.message, ''),
      run: toText(raw.run || raw.run_id || '', ''),
    };
  }

  function normalizeBacklogItem(task) {
    const raw = toObject(task);
    const failure = toObject(raw.failure);
    return {
      id: toText(raw.id, 'task'),
      title: toText(raw.title, 'Untitled task'),
      status: normalizeBacklogStatus(raw.status, 'pending'),
      priority: toText(raw.priority, 'P1'),
      tags: toArray(raw.tags).map((tag) => toText(tag)).filter(Boolean),
      estimate: toText(raw.estimate, 'M'),
      skill: toText(raw.skill, ''),
      description: toText(raw.description || raw.prompt, ''),
      prompt: toText(raw.prompt, ''),
      files: toArray(raw.files).map((file) => toText(file)).filter(Boolean),
      dependsOn: toArray(raw.depends_on || raw.dependsOn || raw.dependencies).map((item) => toText(item)).filter(Boolean),
      fileScope: toText(raw.file_scope || raw.fileScope, ''),
      attempt: toMaybeNumber(raw.attempt),
      failure: {
        reason: toText(failure.reason || raw.failure_reason || raw.failureReason, ''),
        detail: toText(failure.detail || raw.failure_detail || raw.failureDetail, ''),
        cycle: toMaybeNumber(failure.cycle ?? raw.failure_cycle ?? raw.failureCycle),
        step: toMaybeNumber(failure.step ?? raw.failure_step ?? raw.failureStep),
        rc: toMaybeNumber(failure.rc ?? raw.failure_rc ?? raw.failureRc),
      },
      failureReason: toText(failure.reason || raw.failure_reason || raw.failureReason, ''),
      failureDetail: toText(failure.detail || raw.failure_detail || raw.failureDetail, ''),
      recentOutput: toText(raw.recent_output || raw.recentOutput, ''),
      cycle: toMaybeNumber(raw.cycle),
      step: toMaybeNumber(raw.step),
      taskTitle: toText(raw.task_title || raw.taskTitle, ''),
      model: toText(raw.model, ''),
      startedAt: toMaybeNumber(raw.started_at || raw.startedAt),
      endedAt: toMaybeNumber(raw.ended_at || raw.endedAt),
    };
  }

  function normalizeGoalBucket(bucket) {
    return toArray(bucket).map((goal) => ({
      done: Boolean(toObject(goal).done ?? toObject(goal).checked),
      checked: Boolean(toObject(goal).done ?? toObject(goal).checked),
      checkbox: toText(toObject(goal).checkbox, Boolean(toObject(goal).done ?? toObject(goal).checked) ? '[x]' : '[ ]'),
      text: toText(toObject(goal).text, ''),
      note: toText(toObject(goal).note, ''),
      lineNumber: toNumber(toObject(goal).lineNumber || toObject(goal).line_number || toObject(goal).line || 0, 0),
      line_number: toNumber(toObject(goal).lineNumber || toObject(goal).line_number || toObject(goal).line || 0, 0),
      line: toNumber(toObject(goal).line || toObject(goal).lineNumber || toObject(goal).line_number || 0, 0),
    }));
  }

  function normalizeGoalWarning(warning) {
    const raw = toObject(warning);
    return {
      lineNumber: toNumber(raw.lineNumber || raw.line_number || raw.line || 0, 0),
      line_number: toNumber(raw.lineNumber || raw.line_number || raw.line || 0, 0),
      line: toText(raw.line, ''),
      reason: toText(raw.reason, 'unsupported_line'),
      message: toText(raw.message, ''),
    };
  }

  function normalizePrompt(prompt) {
    const raw = toObject(prompt);
    return {
      id: toText(raw.id, 'prompt'),
      file: toText(raw.file, 'prompt.md'),
      scope: toText(raw.scope, 'PM'),
      source: toText(raw.source, ''),
      mode: toText(raw.mode, 'template'),
      updated: toText(raw.updated, 'unknown'),
      summary: toText(raw.summary, ''),
      preview: toText(raw.preview, ''),
      path: toText(raw.path, ''),
    };
  }

  function normalizeHistoryItem(run) {
    const raw = toObject(run);
    return {
      id: toText(raw.id, 'run'),
      startedAt: toNumber(raw.startedAt || 0, 0),
      status: toText(raw.status, 'idle'),
      tasksDone: toNumber(raw.tasksDone || 0, 0),
      tasksTotal: toNumber(raw.tasksTotal || 0, 0),
      branch: toText(raw.branch, 'HEAD'),
      durationSec: toNumber(raw.durationSec || 0, 0),
      stopReason: toText(raw.stopReason, ''),
      runDir: toText(raw.runDir, ''),
      lastCycle: toText(raw.lastCycle, ''),
    };
  }

  function normalizeChangedFile(file) {
    const raw = toObject(file);
    return {
      path: toText(raw.path, '(unknown)'),
      kind: toText(raw.kind, 'modified'),
      note: toText(raw.note, ''),
    };
  }

  function normalizeWorktreeState(worktree) {
    const raw = toObject(worktree);
    const changedFiles = toArray(raw.changedFiles).map(normalizeChangedFile);
    const checklist = toArray(raw.checklist).map((item) => toText(item)).filter(Boolean);
    const sourceRepo = toText(raw.sourceRepo || raw.source_repo, '');
    const sourceBranch = toText(raw.sourceBranch || raw.source_branch || raw.branch, 'HEAD');
    const baseRef = toText(raw.baseRef || raw.base_ref || raw.branch, '');
    const headRef = toText(raw.headRef || raw.head_ref, '');
    const worktreeDir = toText(raw.worktreeDir || raw.worktree_dir || raw.worktree, '');
    const patchPath = toText(raw.patchPath || raw.patch_path || raw.patch, '');
    const pendingFile = toText(raw.pendingFile || raw.pending_file, '');
    const runDir = toText(raw.runDir || raw.run_dir, '');
    const runnerRc = toNumber(raw.runnerRc ?? raw.runner_rc ?? raw.lastRc ?? raw.last_rc ?? 0, 0);
    const reviewRequiredValue = raw.reviewRequired ?? raw.review_required;
    const reviewRequired = Boolean(reviewRequiredValue ?? (raw.status === 'pending review' || raw.status === 'error'));
    const reviewRequiredMessage = toText(
      raw.reviewRequiredMessage || raw.review_required_message || raw.message || raw.summary,
      ''
    );
    return {
      status: toText(raw.status, 'none'),
      mode: toText(raw.mode, 'manual'),
      reviewRequired,
      reviewRequiredMessage,
      sourceRepo,
      sourceBranch,
      branch: sourceBranch,
      baseRef,
      headRef,
      worktreeDir,
      worktree: worktreeDir,
      patchPath,
      patch: patchPath,
      pendingFile,
      summary: toText(raw.summary, ''),
      risk: toText(raw.risk, ''),
      changedFiles,
      checklist,
      runDir,
      runnerRc,
      lastRc: runnerRc,
    };
  }

  function normalizeMetrics(metrics) {
    const raw = toObject(metrics);
    const tokens = toObject(raw.tokens);
    const tokensAvailable = Boolean(
      raw.tokens_available ||
        raw.tokensAvailable ||
        tokens.in != null ||
        tokens.input != null ||
        tokens.out != null ||
        tokens.output != null
    );
    const budgetAvailable = Boolean(raw.budget_available || raw.budgetAvailable || raw.budget_used != null || raw.budgetUsed != null);
    const quotaAvailable = Boolean(raw.quota_available || raw.quotaAvailable || raw.quota_used != null || raw.quotaUsed != null);
    return {
      tokens24h: toArray(raw.tokens24h).map((value) => toNumber(value, 0)),
      success24h: toArray(raw.success24h).map((value) => toNumber(value, 0)),
      budget: toArray(raw.budget).map((value) => clampUnit(value)),
      tokens: {
        in: tokensAvailable ? toMaybeNumber(tokens.in ?? tokens.input) ?? 0 : null,
        out: tokensAvailable ? toMaybeNumber(tokens.out ?? tokens.output) ?? 0 : null,
        available: tokensAvailable,
      },
      last_stage: toText(raw.last_stage, ''),
      quota_used: quotaAvailable ? toMaybeNumber(raw.quota_used ?? raw.quotaUsed) : null,
      budget_used: budgetAvailable ? toMaybeNumber(raw.budget_used ?? raw.budgetUsed) : null,
      tokensAvailable,
      budgetAvailable,
      quotaAvailable,
    };
  }

  function normalizeConfigData(config) {
    const raw = toObject(config);
    let data = toObject(raw.data);
    const schema = toObject(defaults.configSchema);
    for (const path of Object.keys(schema)) {
      const current = getAt(data, path);
      if (current === undefined) continue;
      data = setAt(data, path, normalizeConfigValue(current, schema[path]));
    }
    return {
      path: toText(raw.path, ''),
      source: toText(raw.source, ''),
      data,
      resolved_prompts_dir: toText(raw.resolved_prompts_dir, ''),
    };
  }

  function normalizeConfigValue(value, schema) {
    if (!schema) return value;
    if (schema.kind === 'multienum') return normalizeListValues(value);
    if (schema.kind === 'bool' && typeof value === 'string') {
      const normalized = value.trim().toLowerCase();
      if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
      if (['false', '0', 'no', 'off'].includes(normalized)) return false;
    }
    if (schema.kind === 'number' && value !== '' && value != null && !Number.isNaN(Number(value))) {
      return Number(value);
    }
    return value;
  }

  function adaptActiveRun(snapshot, context = {}) {
    const raw = toObject(snapshot);
    const repo = toObject(context.repo);
    const progress = toObject(context.progress);
    const metrics = normalizeMetrics(context.metrics);
    const config = toObject(context.config);
    const hasRunData = Boolean(raw.id || raw.status || raw.stage || raw.runDir || raw.run_dir || progress.latest_run_dir);
    const repoPath = toText(raw.repo || repo.path || config.repo || '', '');
    const repoLabel = toText(raw.repoLabel || repo.name || repoNameFromPath(repoPath) || 'agentcli', 'agentcli');
    const stage = toText(raw.stage || progress.current_stage || metrics.last_stage || 'idle', 'idle');
    const activeStatus = normalizeRunStatus(raw.status || progress.run_status || progress.runStatus, hasRunData);
    const selectedTask = toText(
      raw.task ||
        progress.current_task_id ||
        progress.selected_task_id ||
        toObject(progress.backlog).selected_id ||
        '',
      ''
    );
    const progressValue = toMaybeNumber(raw.progress ?? progress.progress ?? progress.progressValue);
    const progressAvailable = Boolean(
      raw.progressAvailable ||
        raw.progress_available ||
        progress.progress_available ||
        progress.progressAvailable ||
        (progressValue != null && progressValue > 0)
    );
    const progressRatio = progressAvailable ? progressValue : null;
    const budgetAvailable = Boolean(
      raw.budgetAvailable ||
        raw.budget_available ||
        metrics.budgetAvailable ||
        metrics.budget_available ||
        raw.budgetUsed != null ||
        raw.budget_used != null ||
        metrics.budget_used != null
    );
    const budgetUsed = budgetAvailable ? toMaybeNumber(raw.budgetUsed ?? raw.budget_used ?? metrics.budget_used ?? metrics.budgetUsed) : null;
    const quotaAvailable = Boolean(
      raw.quotaAvailable ||
        raw.quota_available ||
        metrics.quotaAvailable ||
        metrics.quota_available ||
        toObject(raw.quota).used != null ||
        metrics.quota_used != null
    );
    const quotaUsed = quotaAvailable ? toMaybeNumber(toObject(raw.quota).used ?? metrics.quota_used ?? metrics.quotaUsed) : null;
    const tokensAvailable = Boolean(
      raw.tokensAvailable ||
        raw.tokens_available ||
        metrics.tokensAvailable ||
        metrics.tokens_available ||
        toObject(raw.tokens).in != null ||
        toObject(raw.tokens).out != null ||
        metrics.tokens.in != null ||
        metrics.tokens.out != null
    );
    const tokens = toObject(raw.tokens);
    const tokenIn = tokensAvailable ? toMaybeNumber(tokens.in ?? tokens.input ?? metrics.tokens.in) : null;
    const tokenOut = tokensAvailable ? toMaybeNumber(tokens.out ?? tokens.output ?? metrics.tokens.out) : null;
    const runDir = toText(raw.runDir || raw.run_dir || progress.latest_run_dir || '', '');
    const attempt = toMaybeNumber(raw.attempt ?? raw.currentAttempt ?? progress.attempt ?? progress.current_attempt);
    const finalReason = toText(raw.finalReason || raw.final_reason || progress.final_reason || '');
    return {
      id: toText(raw.id || (runDir ? runDir.split(/[\\/]/).pop() : '') || (progress.latest_run_dir ? progress.latest_run_dir.split(/[\\/]/).pop() : ''), hasRunData ? '' : 'no-run'),
      repo: repoPath,
      repoLabel,
      branch: toText(raw.branch || repo.branch || context.branch || 'HEAD', 'HEAD'),
      backend: toText(raw.backend || config.execution_backend || 'codex', 'codex'),
      runDir,
      startedAt: toNumber(raw.startedAt ?? raw.started_at ?? 0, 0),
      stage,
      stageIndex: toNumber(raw.stageIndex || STAGE_INDEX[stage.toLowerCase()] || 0, 0),
      iteration: toNumber(raw.iteration ?? progress.iterations ?? 0, 0),
      maxIterations: toNumber(raw.maxIterations || config.iterations || 1, 1),
      progress: progressRatio,
      progressAvailable,
      attempt,
      worktreeMode: toText(raw.worktreeMode || raw.worktree_mode || progress.worktree_mode || progress.worktreeMode || '', ''),
      finalReason,
      budgetAvailable,
      budgetUsed,
      tokensAvailable,
      tokens: {
        in: tokenIn,
        out: tokenOut,
        available: tokensAvailable,
      },
      quotaAvailable,
      quota: {
        window: toText(toObject(raw.quota).window || metrics.quotaWindow || '5h', '5h'),
        used: quotaUsed,
        available: quotaAvailable,
      },
      elapsedSec: toNumber(raw.elapsedSec ?? raw.elapsed_seconds ?? 0, 0),
      status: activeStatus,
      task: selectedTask,
      taskTitle: toText(raw.taskTitle || raw.task_title || progress.current_task_title || '', ''),
    };
  }

  function adaptStages(stages, context = {}) {
    const items = toArray(stages)
      .map((stage) => {
        const raw = toObject(stage);
        const id = toText(raw.id || raw.label || raw.name, '');
        if (!id) return null;
        const status = normalizeStageStatus(raw.status || raw.state, 'pending');
        return {
          id,
          label: toText(raw.label || raw.id || raw.name, id),
          title: toText(raw.title || raw.taskTitle || raw.task_title || raw.name, toText(raw.label || raw.id || raw.name, id)),
          status,
          cycle: toMaybeNumber(raw.cycle),
          startedAt: toMaybeNumber(raw.startedAt || raw.started_at),
          endedAt: toMaybeNumber(raw.endedAt || raw.ended_at),
          durationSec: toMaybeNumber(raw.durationSec ?? raw.duration_seconds),
          model: toText(raw.model || raw.backend || '', ''),
          taskId: toText(raw.taskId || raw.task_id, ''),
          taskTitle: toText(raw.taskTitle || raw.task_title, ''),
          attempt: toMaybeNumber(raw.attempt || raw.currentAttempt),
          step: toMaybeNumber(raw.step),
          recentOutput: toText(raw.recentOutput || raw.recent_output, ''),
          reason: toText(raw.reason || raw.message, ''),
          rc: toMaybeNumber(raw.rc),
          isFallback: false,
        };
      })
      .filter(Boolean);
    const sectionStatus = !items.length ? 'empty' : items.length < 3 ? 'partial' : 'ready';
    return {
      items,
      state: buildSectionState('stages', sectionStatus, sectionStatus === 'ready' ? '' : sectionStatus === 'partial' ? 'Only some lifecycle records were published.' : fallbackSectionMessage('stages')),
    };
  }

  function adaptBacklog(backlog, context = {}) {
    const raw = toObject(backlog);
    const items = toArray(raw.items).map(normalizeBacklogItem);
    const counts = toObject(raw.counts);
    const selectedId = toText(raw.selected_id, '');
    const currentTaskId = toText(context.currentTaskId || '', '');
    const selectedTaskId = selectedId || (currentTaskId && items.some((task) => task.id === currentTaskId) ? currentTaskId : '');
    const status = items.length ? 'ready' : 'empty';
    return {
      items,
      counts: {
        pending: toNumber(counts.pending || items.filter((task) => task.status === 'pending').length, 0),
        in_progress: toNumber(counts.in_progress || items.filter((task) => task.status === 'in_progress').length, 0),
        done: toNumber(counts.done || items.filter((task) => task.status === 'done').length, 0),
        failed: toNumber(counts.failed || items.filter((task) => task.status === 'failed').length, 0),
      },
      selected_id: selectedTaskId,
      state: buildSectionState('backlog', status, items.length ? '' : fallbackSectionMessage('backlog')),
    };
  }

  function adaptGoals(goals, context = {}) {
    const raw = toObject(goals);
    const warnings = toArray(raw.warnings).map(normalizeGoalWarning);
    const items = {
      p0: normalizeGoalBucket(raw.items?.p0 || raw.p0 || []),
      p1: normalizeGoalBucket(raw.items?.p1 || raw.p1 || []),
    };
    const total = items.p0.length + items.p1.length;
    const done = items.p0.filter((goal) => goal.done).length + items.p1.filter((goal) => goal.done).length;
    const summary = {
      has_goals: Boolean(raw.completion?.has_goals ?? total),
      project_complete: Boolean(raw.completion?.project_complete),
      p0_total: toNumber(raw.summary?.p0_total || items.p0.length, items.p0.length),
      p0_done: toNumber(raw.summary?.p0_done || items.p0.filter((goal) => goal.done).length, 0),
      p1_total: toNumber(raw.summary?.p1_total || items.p1.length, items.p1.length),
      p1_done: toNumber(raw.summary?.p1_done || items.p1.filter((goal) => goal.done).length, 0),
      all_total: toNumber(raw.summary?.all_total || total, total),
      all_done: toNumber(raw.summary?.all_done || done, done),
      total: toNumber(raw.summary?.total || total, total),
      done: toNumber(raw.summary?.done || done, done),
      unchecked: toNumber(raw.summary?.unchecked || Math.max(0, total - done), Math.max(0, total - done)),
      warnings: toNumber(raw.summary?.warnings || warnings.length, warnings.length),
    };
    return {
      path: toText(raw.path, ''),
      exists: Boolean(raw.exists),
      mtime: raw.mtime == null ? null : Number(raw.mtime),
      size: raw.size == null ? null : Number(raw.size),
      raw_text: toText(raw.raw_text || raw.rawText, ''),
      completion: toObject(raw.completion),
      completion_level: toText(raw.completion_level || raw.completionLevel, ''),
      items,
      warnings,
      state: buildSectionState('goals', total ? 'ready' : 'empty', total ? '' : fallbackSectionMessage('goals')),
      summary,
    };
  }

  function adaptConfig(config, context = {}) {
    const raw = normalizeConfigData(config);
    const configData = deepMerge(clone(defaults.config), raw.data);
    return {
      path: raw.path,
      source: raw.source,
      data: configData,
      resolved_prompts_dir: raw.resolved_prompts_dir,
      state: buildSectionState('config', Object.keys(raw.data || {}).length ? 'ready' : 'empty', Object.keys(raw.data || {}).length ? '' : fallbackSectionMessage('config')),
    };
  }

  function adaptPrompts(prompts, context = {}) {
    const raw = toObject(prompts);
    const items = toArray(raw.items).map(normalizePrompt);
    return {
      dir: toText(raw.dir, ''),
      exists: Boolean(raw.exists),
      items,
      state: buildSectionState('prompts', items.length ? 'ready' : 'empty', items.length ? '' : fallbackSectionMessage('prompts')),
    };
  }

  function adaptLogs(logs, context = {}) {
    const raw = toObject(logs);
    const items = toArray(raw.entries).map(normalizeLogEntry).slice(-MAX_LOG_ROWS);
    return {
      entries: items,
      tail: toText(raw.tail, ''),
      files: toObject(raw.files),
      state: buildSectionState('logs', items.length ? 'ready' : 'empty', items.length ? '' : fallbackSectionMessage('logs')),
    };
  }

  function adaptNotifications(notifications, context = {}) {
    const items = toArray(notifications).map(normalizeNotification).slice(-MAX_LOG_ROWS);
    return {
      items,
      state: buildSectionState('notifications', items.length ? 'ready' : 'empty', items.length ? '' : fallbackSectionMessage('notifications')),
    };
  }

  function adaptMetrics(metrics, context = {}) {
    const data = normalizeMetrics(metrics);
    const hasData =
      data.tokensAvailable ||
      data.budgetAvailable ||
      data.quotaAvailable ||
      data.tokens24h.length > 0 ||
      data.success24h.length > 0 ||
      data.budget.length > 0 ||
      data.tokens.in != null ||
      data.tokens.out != null;
    return {
      ...data,
      state: buildSectionState('metrics', hasData ? 'ready' : 'empty', hasData ? '' : fallbackSectionMessage('metrics')),
    };
  }

  function adaptHistory(history, context = {}) {
    const raw = toObject(history);
    const items = toArray(raw.items).map(normalizeHistoryItem);
    return {
      items,
      summary: {
        runs: toNumber(toObject(raw.summary).runs || items.length, items.length),
        successes: toNumber(toObject(raw.summary).successes || items.filter((run) => run.status === 'success').length, 0),
        failures: toNumber(toObject(raw.summary).failures || items.filter((run) => run.status === 'failed').length, 0),
        stopped: toNumber(toObject(raw.summary).stopped || items.filter((run) => run.status === 'stopped').length, 0),
        tasksDone: toNumber(toObject(raw.summary).tasksDone || items.reduce((sum, run) => sum + run.tasksDone, 0), 0),
        tasksTotal: toNumber(toObject(raw.summary).tasksTotal || items.reduce((sum, run) => sum + run.tasksTotal, 0), 0),
      },
      state: buildSectionState('history', items.length ? 'ready' : 'empty', items.length ? '' : fallbackSectionMessage('history')),
    };
  }

  function adaptWorktree(worktree, context = {}) {
    const data = normalizeWorktreeState(worktree);
    const sectionStatus = data.status === 'error' ? 'error' : data.status && data.status !== 'none' ? 'ready' : 'empty';
    const sectionMessage =
      data.status === 'error'
        ? data.reviewRequiredMessage || data.summary || fallbackSectionMessage('worktree')
        : data.status === 'none'
          ? fallbackSectionMessage('worktree')
          : '';
    return {
      ...data,
      state: buildSectionState('worktree', sectionStatus, sectionMessage),
    };
  }

  function normalizeApiSnapshot(snapshot) {
    const raw = toObject(snapshot);
    const repo = toObject(raw.repo);
    const progress = toObject(raw.progress);
    const config = adaptConfig(raw.config, { progress, repo });
    const metrics = adaptMetrics(raw.metrics, { progress, repo, config: config.data });
    const runnerControl = normalizeRunnerControl(raw.runner_control || raw.runnerControl || raw.control);
    const activeRun = adaptActiveRun(raw.active_run, {
      repo,
      progress,
      metrics,
      config: config.data,
      branch: repo.branch || '',
      source: 'api',
    });
    const stages = adaptStages(raw.stages, { activeRun });
    const backlog = adaptBacklog(raw.backlog, { currentTaskId: progress.current_task_id || activeRun.task || '' });
    const goals = adaptGoals(raw.goals, { progress });
    const prompts = adaptPrompts(raw.prompts);
    const logs = adaptLogs(raw.logs);
    const notifications = adaptNotifications(raw.notifications);
    const history = adaptHistory(raw.history);
    const worktree = adaptWorktree(raw.worktree);

    return {
      ok: Boolean(raw.ok),
      sourceMode: 'api',
      snapshotStatus: 'ready',
      snapshotLabel: 'API snapshot',
      lastSnapshotAt: nowMs(),
      latestRunDir: toText(raw.latest_run_dir, ''),
      repo: {
        path: toText(repo.path, ''),
        name: toText(repo.name, repoNameFromPath(repo.path) || 'agentcli'),
        head: toText(repo.head, ''),
        branch: toText(repo.branch, 'HEAD'),
      },
      activeRun,
      stages: stages.items,
      backlog: backlog.items,
      backlogCounts: backlog.counts,
      backlogSelectedId: backlog.selected_id,
      goals: goals.items,
      goalsSnapshot: goals,
      goalsMeta: goals.summary,
      goalsPath: goals.path,
      goalsCompletion: goals.completion,
      logs: logs.entries,
      logTail: logs.tail,
      logFiles: logs.files,
      configDefault: clone(config.data),
      config: config.data,
      configMeta: {
        path: config.path,
        source: config.source,
        resolved_prompts_dir: config.resolved_prompts_dir,
      },
      prompts: prompts.items,
      promptsDir: prompts.dir,
      history: history.items,
      historySummary: history.summary,
      metrics,
      notifications: notifications.items,
      worktreeMerge: worktree,
      runnerControl,
      progress,
      sectionState: {
        activeRun: buildSectionState('activeRun', activeRun.status === 'idle' && !activeRun.task && !activeRun.startedAt ? 'empty' : 'ready', activeRun.status === 'idle' && !activeRun.task && !activeRun.startedAt ? fallbackSectionMessage('activeRun') : ''),
        stages: stages.state,
        backlog: backlog.state,
        goals: goals.state,
        config: config.state,
        prompts: prompts.state,
        logs: logs.state,
        notifications: notifications.state,
        metrics: metrics.state,
        history: history.state,
        worktree: worktree.state,
        runnerControl: buildSectionState('runnerControl', runnerControl.controllerAvailable ? (runnerControl.enabled ? 'ready' : 'disabled') : 'error', runnerControl.message || fallbackSectionMessage('runnerControl')),
      },
    };
  }

  function createBlankModel() {
    return {
      ok: false,
      sourceMode: 'loading',
      snapshotStatus: 'loading',
      snapshotLabel: 'Loading snapshot',
      lastSnapshotAt: 0,
      latestRunDir: '',
      repo: {
        path: '',
        name: 'agentcli',
        head: '',
        branch: 'HEAD',
      },
      activeRun: {
        id: 'no-run',
        repo: '',
        repoLabel: 'agentcli',
        branch: 'HEAD',
        backend: 'codex',
        startedAt: 0,
        stage: 'idle',
        stageIndex: 0,
        iteration: 0,
        maxIterations: 1,
        runDir: '',
        attempt: null,
        worktreeMode: '',
        finalReason: '',
        progressAvailable: false,
        progress: null,
        budgetAvailable: false,
        budgetUsed: null,
        tokensAvailable: false,
        tokens: { in: null, out: null, available: false },
        quotaAvailable: false,
        quota: { window: '5h', used: null, available: false },
        elapsedSec: 0,
        status: 'idle',
        task: '',
        taskTitle: '',
      },
      stages: [],
      backlog: [],
      backlogCounts: { pending: 0, in_progress: 0, done: 0, failed: 0 },
      backlogSelectedId: '',
      runnerControl: createRunnerControlModel({
        source: 'loading',
        message: 'Loading runner control status...',
        controllerAvailable: false,
        enabled: false,
        running: false,
        runStatus: 'loading',
        runnerMode: 'unknown',
      }),
      goals: { p0: [], p1: [] },
      goalsSnapshot: {
        path: '',
        exists: false,
        mtime: null,
        size: null,
        raw_text: '',
        items: { p0: [], p1: [] },
        completion: {},
        summary: {
          has_goals: false,
          project_complete: false,
          p0_total: 0,
          p0_done: 0,
          p1_total: 0,
          p1_done: 0,
          all_total: 0,
          all_done: 0,
          total: 0,
          done: 0,
          unchecked: 0,
          warnings: 0,
        },
        warnings: [],
        completion_level: 'all',
      },
      goalsMeta: { total: 0, done: 0 },
      goalsPath: '',
      goalsCompletion: {},
      goalsDirty: false,
      logs: [],
      logTail: '',
      logFiles: {},
      configDefault: {
        repo: '',
        execution_backend: 'codex',
        roles: ['PM', 'Dev', 'QA'],
        autopilot: true,
        continuous: true,
        iterations: 1,
        worktree_isolation: false,
        run_tests: true,
        budget: {
          max_usd: 8,
          max_iters: 5,
          max_continuations: 3,
        },
        claudecode: {
          dev_model: 'sonnet',
          dev_model_tier1: 'opus',
          qa_model: 'haiku',
          reporter_model: 'haiku',
        },
        telegram: {
          enabled: true,
          instance_name: 'home-pc-main',
        },
        prompts_dir: 'prompts/agentcli-fallback',
        worktree_merge_mode: 'manual',
      },
      config: {
        repo: '',
        execution_backend: 'codex',
        roles: ['PM', 'Dev', 'QA'],
        autopilot: true,
        continuous: true,
        iterations: 1,
        worktree_isolation: false,
        run_tests: true,
        budget: {
          max_usd: 8,
          max_iters: 5,
          max_continuations: 3,
        },
        claudecode: {
          dev_model: 'sonnet',
          dev_model_tier1: 'opus',
          qa_model: 'haiku',
          reporter_model: 'haiku',
        },
        telegram: {
          enabled: true,
          instance_name: 'home-pc-main',
        },
        prompts_dir: 'prompts/agentcli-fallback',
        worktree_merge_mode: 'manual',
      },
      configMeta: {
        path: '',
        source: '',
        resolved_prompts_dir: '',
      },
      configSchema: {
        repo: {
          kind: 'text',
          restart: true,
          desc: 'Absolute path to the repo AgentCLI will operate on.',
          hint: 'Use a local Windows path such as C:/Dev/AgentCLI.',
        },
        execution_backend: {
          kind: 'enum',
          options: ['codex', 'claudecode'],
          restart: true,
          desc: 'Backend used for Dev and QA stages.',
          hint: 'codex = OpenAI Codex CLI | claudecode = Anthropic Claude Code CLI.',
        },
        roles: {
          kind: 'multienum',
          options: ['PM', 'Dev', 'QA', 'Reporter'],
          restart: false,
          desc: 'Stages enabled in the pipeline.',
          hint: 'PM should stay first. Reporter appends a summary after QA.',
        },
        autopilot: {
          kind: 'bool',
          restart: false,
          desc: 'Skip interactive confirmation prompts.',
          hint: 'When off, the runner pauses between stages.',
        },
        continuous: {
          kind: 'bool',
          restart: false,
          desc: 'Run PM -> Dev -> QA without stopping.',
          hint: 'Pair with autopilot=true for unattended runs.',
        },
        iterations: {
          kind: 'number',
          min: 1,
          max: 20,
          restart: false,
          desc: 'Max number of run iterations.',
          hint: 'One iteration equals one full PM -> Dev -> QA cycle.',
        },
        worktree_isolation: {
          kind: 'bool',
          restart: true,
          desc: 'Run inside a fresh git worktree.',
          hint: 'Recommended for shared machines. Adds startup cost.',
        },
        run_tests: {
          kind: 'bool',
          restart: false,
          desc: 'Run the test suite during QA.',
          hint: 'Keeps verification inside the task loop.',
        },
        'budget.max_usd': {
          kind: 'number',
          min: 0.5,
          max: 100,
          step: 0.5,
          restart: false,
          desc: 'Hard spend cap in USD.',
          hint: 'Runner stops when the budget cap is reached.',
        },
        'budget.max_iters': {
          kind: 'number',
          min: 1,
          max: 20,
          restart: false,
          desc: 'Safety cap for iterations.',
          hint: 'Typically matches iterations unless explicitly different.',
        },
        'budget.max_continuations': {
          kind: 'number',
          min: 0,
          max: 10,
          restart: false,
          desc: 'Max times Dev can continue a capped response.',
          hint: 'Used when the model returns a partial response.',
        },
        'claudecode.dev_model': {
          kind: 'enum',
          options: ['haiku', 'sonnet', 'opus'],
          restart: false,
          desc: 'Default Dev model.',
          hint: 'Escalates when retries accumulate.',
        },
        'claudecode.dev_model_tier1': {
          kind: 'enum',
          options: ['haiku', 'sonnet', 'opus'],
          restart: false,
          desc: 'Tier 1 Dev model fallback.',
          hint: 'Used after repeated retries or capped output.',
        },
        'claudecode.qa_model': {
          kind: 'enum',
          options: ['haiku', 'sonnet', 'opus'],
          restart: false,
          desc: 'QA model.',
          hint: 'Haiku is usually enough for verification.',
        },
        'claudecode.reporter_model': {
          kind: 'enum',
          options: ['haiku', 'sonnet', 'opus'],
          restart: false,
          desc: 'Run summary model.',
          hint: 'Generates the closing report after QA.',
        },
        'telegram.enabled': {
          kind: 'bool',
          restart: true,
          desc: 'Mirror events to Telegram.',
          hint: 'Local notification bridge only.',
        },
        'telegram.instance_name': {
          kind: 'text',
          restart: false,
          desc: 'Friendly Telegram instance label.',
          hint: 'Useful when multiple runners share the same chat.',
        },
        prompts_dir: {
          kind: 'text',
          restart: true,
          desc: 'Prompt template directory.',
          hint: 'Default resolves to prompts/<repo-slug>-<hash>/.',
        },
        worktree_merge_mode: {
          kind: 'enum',
          options: ['manual', 'auto'],
          restart: true,
          desc: 'How worktree patches are handled.',
          hint: 'Manual mode requires review before applying.',
        },
      },
      prompts: [],
      worktreeMerge: {
        status: 'none',
        mode: 'manual',
        branch: 'HEAD',
        sourceRepo: '',
        sourceBranch: 'HEAD',
        baseRef: '',
        reviewRequired: false,
        reviewRequiredMessage: 'No pending worktree merge.',
        worktreeDir: '',
        worktree: '',
        patchPath: '',
        patch: '',
        pendingFile: '',
        summary: 'No pending worktree merge.',
        risk: 'No isolated worktree patch is pending review.',
        changedFiles: [],
        checklist: [
          'Inspect patch hunks',
          'Verify no secret leakage',
          'Approve merge only after review',
          'Discard only after archival copy',
        ],
        runDir: '',
        runnerRc: 0,
        headRef: '',
        lastRc: 0,
      },
      history: [],
      historySummary: { runs: 0, successes: 0, failures: 0, stopped: 0, tasksDone: 0, tasksTotal: 0 },
      metrics: {
        tokens24h: [],
        success24h: [],
        budget: [],
        tokens: { in: null, out: null, available: false },
        last_stage: '',
        quota_used: null,
        budget_used: null,
        tokensAvailable: false,
        budgetAvailable: false,
        quotaAvailable: false,
      },
      notifications: [],
      progress: {
        latest_run_dir: null,
        run_status: 'idle',
        tasks_done: 0,
        tasks_total: 0,
        tasks_failed: 0,
        progress: null,
        progress_available: false,
        current_task_id: '',
        current_task_title: '',
        attempt: null,
        worktree_mode: '',
        goals: { p0: [], p1: [] },
        backlog: { items: [], counts: {}, selected_id: '' },
        final_reason: '',
        final_rc: null,
        state: { done: [], failed: [], warnings: [] },
      },
      sectionState: {
        activeRun: buildSectionState('activeRun', 'loading', 'Loading read-only snapshot...','loading'),
        stages: buildSectionState('stages', 'loading', 'Loading read-only snapshot...','loading'),
        backlog: buildSectionState('backlog', 'loading', 'Loading read-only snapshot...','loading'),
        goals: buildSectionState('goals', 'loading', 'Loading read-only snapshot...','loading'),
        config: buildSectionState('config', 'loading', 'Loading read-only snapshot...','loading'),
        prompts: buildSectionState('prompts', 'loading', 'Loading read-only snapshot...','loading'),
        logs: buildSectionState('logs', 'loading', 'Loading read-only snapshot...','loading'),
        notifications: buildSectionState('notifications', 'loading', 'Loading read-only snapshot...','loading'),
        metrics: buildSectionState('metrics', 'loading', 'Loading read-only snapshot...','loading'),
        history: buildSectionState('history', 'loading', 'Loading read-only snapshot...','loading'),
        worktree: buildSectionState('worktree', 'loading', 'Loading read-only snapshot...','loading'),
        runnerControl: buildSectionState('runnerControl', 'loading', 'Loading runner control status...','loading'),
      },
    };
  }

  function createFallbackFixture() {
    const blank = createBlankModel();
    return {
      ok: true,
      sourceMode: 'fallback',
      snapshotStatus: 'fallback',
      snapshotLabel: 'Fallback data',
      lastSnapshotAt: nowMs(),
      latestRunDir: '',
      repo: {
        path: 'C:/Dev/AgentCLI',
        name: 'AgentCLI',
        head: 'offline',
        branch: 'main',
      },
      activeRun: {
        ...clone(blank.activeRun),
        repo: 'C:/Dev/AgentCLI',
        repoLabel: 'AgentCLI',
        branch: 'main',
      },
      runnerControl: createRunnerControlModel({
        source: 'fallback',
        message: 'Runner controls are unavailable in fallback mode.',
        controllerAvailable: false,
        enabled: false,
        running: false,
        runStatus: 'idle',
        runnerMode: 'unknown',
      }),
      stages: clone(blank.stages),
      backlog: clone(blank.backlog),
      backlogCounts: clone(blank.backlogCounts),
      backlogSelectedId: blank.backlogSelectedId,
      goals: {
        p0: [
          { done: false, text: 'Observe the current run in a browser without CLI shell access', note: '' },
        ],
        p1: [
          { done: false, text: 'Keep the browser useful when no run exists', note: '' },
        ],
      },
      goalsSnapshot: {
        path: '.doc/GOALS.md',
        exists: true,
        mtime: null,
        size: null,
        raw_text: '# Project Goals\n\n## P0\n- [ ] Observe the current run in a browser without CLI shell access\n\n## P1\n- [ ] Keep the browser useful when no run exists\n',
        items: {
          p0: [
            {
              done: false,
              checked: false,
              checkbox: '[ ]',
              text: 'Observe the current run in a browser without CLI shell access',
              note: '',
              lineNumber: 4,
              line: 4,
            },
          ],
          p1: [
            {
              done: false,
              checked: false,
              checkbox: '[ ]',
              text: 'Keep the browser useful when no run exists',
              note: '',
              lineNumber: 7,
              line: 7,
            },
          ],
        },
        completion: { has_goals: true, project_complete: false, p0_total: 1, p0_done: 0, p1_total: 1, p1_done: 0, all_total: 2, all_done: 0, unmet_p0: ['Observe the current run in a browser without CLI shell access'], unmet_p1: ['Keep the browser useful when no run exists'] },
        summary: {
          has_goals: true,
          project_complete: false,
          p0_total: 1,
          p0_done: 0,
          p1_total: 1,
          p1_done: 0,
          all_total: 2,
          all_done: 0,
          total: 2,
          done: 0,
          unchecked: 2,
          warnings: 0,
        },
        warnings: [],
        completion_level: 'all',
      },
      goalsMeta: { total: 2, done: 0 },
      goalsPath: '.doc/GOALS.md',
      goalsCompletion: { project_complete: false },
      goalsDirty: false,
      logs: [
        { t: fmtClock(minutesAgo(28)), lvl: 'info', stage: 'boot', msg: 'Fallback fixture loaded because the API was not reachable.' },
        { t: fmtClock(minutesAgo(12)), lvl: 'warn', stage: 'Dev', msg: 'Showing local fallback data for offline rendering.' },
      ],
      logTail: 'offline fallback fixture',
      logFiles: {
        cycle_summary: '.AgentCLI/agent_runs/offline-fallback/cycle_summary.log',
        run_log: '.AgentCLI/agent_runs/offline-fallback/logs/run.log',
        metrics: '.AgentCLI/agent_runs/offline-fallback/metrics.jsonl',
      },
      configDefault: createBlankModel().configDefault,
      config: createBlankModel().config,
      configMeta: {
        path: 'config/agentcli.json',
        source: 'fallback',
        resolved_prompts_dir: 'prompts/agentcli-fallback',
      },
      prompts: [
        {
          id: 'bootstrap',
          file: 'bootstrap_prompt.md',
          scope: 'PM',
          source: 'fallback',
          mode: 'template',
          updated: 'fallback',
          summary: 'Fallback bootstrap prompt preview.',
          preview: 'Read the repo, collect goals, and emit a small backlog before any code changes.',
          path: 'prompts/bootstrap_prompt.md',
        },
        {
          id: 'dev_task',
          file: 'dev_task_prompt.md',
          scope: 'Dev',
          source: 'fallback',
          mode: 'template',
          updated: 'fallback',
          summary: 'Fallback development prompt preview.',
          preview: 'Implement the assigned task and keep the scope narrow.',
          path: 'prompts/dev_task_prompt.md',
        },
      ],
      worktreeMerge: {
        status: 'none',
        mode: 'manual',
        branch: 'main',
        sourceRepo: 'C:/Dev/AgentCLI',
        sourceBranch: 'main',
        baseRef: '',
        reviewRequired: false,
        reviewRequiredMessage: 'No pending worktree merge.',
        worktreeDir: '',
        worktree: '',
        patchPath: '',
        patch: '',
        pendingFile: '',
        summary: 'No pending worktree merge.',
        risk: 'No isolated worktree patch is pending review.',
        changedFiles: [],
        checklist: [
          'Inspect patch hunks',
          'Verify no secret leakage',
          'Approve merge only after review',
          'Discard only after archival copy',
        ],
        runDir: '',
        runnerRc: 0,
        headRef: '',
        lastRc: 0,
      },
      history: [
        {
          id: 'run_offline_20260425_223000',
          startedAt: hoursAgo(7),
          status: 'success',
          tasksDone: 2,
          tasksTotal: 2,
          branch: 'main',
          durationSec: 980,
          stopReason: 'project_complete',
          runDir: 'offline-fallback',
          lastCycle: 'cycle=2 done=2/2',
        },
      ],
      historySummary: { runs: 1, successes: 1, failures: 0, stopped: 0, tasksDone: 2, tasksTotal: 2 },
      metrics: {
        tokens24h: [320, 480, 620, 720, 840],
        success24h: [1, 1, 1, 1, 1],
        budget: [0.12, 0.18, 0.24, 0.31, 0.34],
        tokens: { in: 18420, out: 6421, available: true },
        last_stage: 'Dev',
        quota_used: 0.22,
        budget_used: 0.34,
        tokensAvailable: true,
        budgetAvailable: true,
        quotaAvailable: true,
      },
      notifications: [
        { t: minutesAgo(28), kind: 'run_start', text: 'Fallback run loaded for offline rendering.', run: 'run_offline_20260426_000000' },
        { t: minutesAgo(12), kind: 'stalled', text: 'Offline fallback is not live data.', run: 'run_offline_20260426_000000' },
      ],
      progress: {
        ...clone(blank.progress),
        latest_run_dir: '',
        run_status: 'idle',
        tasks_done: 0,
        tasks_total: 0,
        tasks_failed: 0,
        progress: null,
        progress_available: false,
        current_task_id: '',
        current_task_title: '',
        attempt: null,
        worktree_mode: '',
        backlog: {
          items: [],
          counts: {},
          selected_id: '',
        },
        final_reason: '',
        final_rc: 0,
        state: { done: [], failed: [], warnings: [] },
      },
      sectionState: {
        activeRun: buildSectionState('activeRun', 'empty', fallbackSectionMessage('activeRun'), 'fallback'),
        stages: buildSectionState('stages', 'empty', fallbackSectionMessage('stages'), 'fallback'),
        backlog: buildSectionState('backlog', 'empty', fallbackSectionMessage('backlog'), 'fallback'),
        goals: buildSectionState('goals', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        config: buildSectionState('config', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        prompts: buildSectionState('prompts', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        logs: buildSectionState('logs', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        notifications: buildSectionState('notifications', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        metrics: buildSectionState('metrics', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        history: buildSectionState('history', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        worktree: buildSectionState('worktree', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
        runnerControl: buildSectionState('runnerControl', 'fallback', 'Using fallback data because the API is unavailable.', 'fallback'),
      },
    };
  }

  const ADAPTERS = {
    adaptActiveRun,
    adaptStages,
    adaptBacklog,
    adaptGoals,
    adaptConfig,
    adaptPrompts,
    adaptLogs,
    adaptNotifications,
    adaptMetrics,
    adaptHistory,
    adaptWorktree,
    normalizeSnapshot: normalizeApiSnapshot,
    createBlankModel,
    createFallbackFixture,
  };

  if (typeof globalThis !== 'undefined') {
    globalThis.__AGENTCLI_ADAPTERS__ = ADAPTERS;
  }

  function applySnapshotModel(model) {
    if (!model || typeof model !== 'object') {
      return false;
    }
    const next = clone(model);
    state.ok = Boolean(next.ok);
    state.sourceMode = toText(next.sourceMode, 'api');
    state.snapshotStatus = toText(next.snapshotStatus, 'ready');
    state.snapshotLabel = toText(next.snapshotLabel, 'API snapshot');
    state.lastSnapshotAt = toNumber(next.lastSnapshotAt || nowMs(), nowMs());
    state.latestRunDir = toText(next.latestRunDir, '');
    state.repo = toObject(next.repo);
    state.activeRun = toObject(next.activeRun);
    state.stages = toArray(next.stages);
    state.backlog = toArray(next.backlog);
    state.backlogCounts = toObject(next.backlogCounts);
    state.backlogSelectedId = toText(next.backlogSelectedId, '');
    state.goalsSnapshot = deepMerge(clone(defaults.goalsSnapshot), toObject(next.goalsSnapshot));
    state.goalsMeta = toObject(next.goalsMeta || state.goalsSnapshot.summary);
    state.goalsPath = toText(next.goalsPath || state.goalsSnapshot.path, '');
    state.goalsCompletion = toObject(next.goalsCompletion || state.goalsSnapshot.completion);
    if (!state.goalsDirty) {
      state.goals = deepMerge(clone(defaults.goals), toObject(next.goals));
    }
    state.logs = toArray(next.logs).slice(-MAX_LOG_ROWS);
    state.logTail = toText(next.logTail, '');
    state.logFiles = toObject(next.logFiles);
    state.configDefault = deepMerge(clone(next.configDefault || {}), null);
    state.config = deepMerge(clone(next.config || {}), readJSON(STORAGE.config, null));
    state.configMeta = toObject(next.configMeta);
    state.prompts = toArray(next.prompts);
    state.promptsDir = toText(toObject(next.config || {}).prompts_dir || next.promptsDir || '', '');
    state.worktreeMerge = toObject(next.worktreeMerge);
    state.runnerControl = normalizeRunnerControl(next.runnerControl);
    state.history = toArray(next.history);
    state.runs = state.history;
    state.historySummary = toObject(next.historySummary);
    state.metrics = toObject(next.metrics);
    state.notifications = toArray(next.notifications).slice(-MAX_LOG_ROWS);
    state.progress = toObject(next.progress);
    state.sectionState = toObject(next.sectionState);
    state.configSchema = clone(defaults.configSchema);
    state.serverMode = state.sourceMode === 'api';
    state.logsPaused = state.serverMode ? true : state.logsPaused;
    const nextBacklogSelection = toText(next.backlogSelectedId, '');
    state.backlogSelectedId = nextBacklogSelection;
    if (nextBacklogSelection) {
      state.backlogSelection = nextBacklogSelection;
    } else if (state.backlogSelection && !state.backlog.some((task) => task.id === state.backlogSelection)) {
      state.backlogSelection = '';
    }
    if (!state.historySelection && state.history.length) {
      state.historySelection = state.history[0].id;
    }
    if (!state.promptSelection && state.prompts.length) {
      state.promptSelection = state.prompts[0].id;
    }
    return true;
  }

  function applyServerSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') {
      return false;
    }
    return applySnapshotModel(normalizeApiSnapshot(snapshot));
  }

  function viewShell(view, title, subtitle, actions, body) {
    return `
      <section class="view view--${escapeHTML(view)}" data-view="${escapeHTML(view)}">
        <header class="view__header">
          <div class="view__title-block">
            <h1 class="view__title">${escapeHTML(title)}</h1>
            <div class="view__subtitle">${subtitle || ''}</div>
          </div>
          <div class="view__actions">${actions || ''}</div>
        </header>
        <div class="view__body">${body}</div>
      </section>
    `;
  }

  function panel(title, meta, body, className = '') {
    return `
      <section class="panel ${className}">
        <div class="panel__head">
          <h2 class="panel__title">${escapeHTML(title)}</h2>
          ${meta ? `<div class="panel__meta">${meta}</div>` : ''}
        </div>
        <div class="panel__body">${body}</div>
      </section>
    `;
  }

  function sectionNotice(sectionKey) {
    const section = state.sectionState?.[sectionKey];
    if (!section || section.status === 'ready') {
      return '';
    }
    const tone =
      section.status === 'error'
        ? 'err'
        : section.status === 'disabled' || section.status === 'stale' || section.status === 'loading' || section.status === 'fallback' || section.status === 'empty' || section.status === 'partial'
          ? 'warn'
          : 'info';
    const label =
      section.status === 'loading'
        ? 'Loading read-only snapshot'
        : section.status === 'disabled'
          ? 'Controls disabled'
        : section.status === 'fallback'
          ? 'Fallback data'
          : section.status === 'partial'
            ? 'Partial snapshot'
          : section.status === 'stale'
            ? 'Stale snapshot'
            : section.status === 'empty'
              ? 'Empty state'
              : section.status;
    const message = section.message || fallbackSectionMessage(sectionKey);
    return `
      <div class="modal-banner section-banner section-banner--${tone}">
        <span class="dot" style="background: currentColor;"></span>
        <div>
          <div class="section-banner__title">${escapeHTML(label)}</div>
          <div class="section-banner__copy">${escapeHTML(message)}</div>
        </div>
      </div>
    `;
  }

  function chip(label, className = '') {
    return `<span class="chip ${className}">${escapeHTML(label)}</span>`;
  }

  function button(label, action, extraClass = 'button--quiet', attrs = '') {
    return `<button type="button" class="button ${extraClass}" data-action="${escapeHTML(action)}" ${attrs}>${escapeHTML(label)}</button>`;
  }

  function navButton(item, active) {
    const activeClass = active ? ' nav-item--active' : '';
    const badge = item.badge ? `<span class="nav-badge">${escapeHTML(item.badge)}</span>` : '';
    return `
      <button type="button" class="nav-item${activeClass}" data-nav="${escapeHTML(item.view)}">
        <span class="nav-item__label">${escapeHTML(item.label)}</span>
        <span class="nav-item__meta">
          ${badge}
          <span>${escapeHTML(item.shortcut)}</span>
        </span>
      </button>
    `;
  }

  function metricCard(label, value, sub, accent = false) {
    const classes = ['stat-card__value'];
    if (accent) {
      classes.push('stat-card__value--accent');
    }
    if (value === 'unavailable') {
      classes.push('stat-card__value--unavailable');
    }
    return `
      <div class="stat-card">
        <div class="stat-card__label">${escapeHTML(label)}</div>
        <div class="${classes.join(' ')}">${escapeHTML(value)}</div>
        <div class="stat-card__sub">${sub || ''}</div>
      </div>
    `;
  }

  function kpiCard(label, value, sub, accent = false) {
    const classes = ['kpi-card__value'];
    if (accent) {
      classes.push('kpi-card__value--accent');
    }
    if (value === 'unavailable') {
      classes.push('kpi-card__value--unavailable');
    }
    return `
      <div class="kpi-card">
        <div class="kpi-card__label">${escapeHTML(label)}</div>
        <div class="${classes.join(' ')}">${escapeHTML(value)}</div>
        <div class="kpi-card__sub">${sub || ''}</div>
      </div>
    `;
  }

  function detailCard(label, value, valueClass = '') {
    return `
      <div class="runner-control__detail">
        <div class="runner-control__label">${escapeHTML(label)}</div>
        <div class="runner-control__value${valueClass ? ` ${valueClass}` : ''}">${escapeHTML(value)}</div>
      </div>
    `;
  }

  function renderTimelineConnector(nextStatus) {
    const normalized = normalizeStageStatus(nextStatus, 'pending');
    const cls = normalized === 'running' ? 'connector connector--running' : normalized === 'failed' ? 'connector connector--warn' : 'connector connector--done';
    return `<div class="${cls}"></div>`;
  }

  function renderLifecycleLane(stages, emptyMessage = 'No lifecycle records were published yet.') {
    if (!stages.length) {
      return `<div class="summary-note">${escapeHTML(emptyMessage)}</div>`;
    }
    return stages
      .map((stage, index) => `
        ${renderStageCard(stage)}
        ${index < stages.length - 1 ? renderTimelineConnector(stages[index + 1].status) : ''}
      `)
      .join('');
  }

  function renderStageCard(stage) {
    const status = normalizeStageStatus(stage.status, 'pending');
    const cardClass = lifecycleStageCardClass(status);
    const iconClass = lifecycleStageIconClass(status);
    const iconText = lifecycleStageIconText(status);
    const label = toText(stage.label, stage.id || 'Stage');
    const title = toText(stage.title || stage.taskTitle || stage.label, stage.label || 'Lifecycle stage');
    const model = toText(stage.model, '');
    const cycleText = stage.cycle != null ? `cycle ${stage.cycle}` : 'cycle unavailable';
    const taskIdText = stage.taskId ? `task ${stage.taskId}` : 'task unavailable';
    const attemptText = stage.attempt != null ? `attempt ${stage.attempt}` : 'attempt unavailable';
    const startedText = stage.startedAt ? `started ${fmtClock(stage.startedAt)}` : 'started unavailable';
    const endedText = stage.endedAt ? `ended ${fmtClock(stage.endedAt)}` : status === 'running' ? 'in progress' : 'ended unavailable';
    const durationText = stage.durationSec != null ? fmtDuration(stage.durationSec) : '--';
    const recentOutput = compactText(stage.recentOutput, 180) || 'Recent output unavailable.';
    return `
      <div class="${cardClass}">
        <div class="stage-card__head">
          <div class="${iconClass}">${iconText}</div>
          <div class="stage-card__title">
            <div class="stage-card__label">${escapeHTML(label)}</div>
            <div class="stage-card__meta">${escapeHTML(status)} | ${escapeHTML(cycleText)}</div>
          </div>
        </div>
        <div class="stage-card__body">
          <div>${escapeHTML(title)}</div>
          <div class="muted">${escapeHTML(model || 'model unavailable')} | ${escapeHTML(durationText)}</div>
          <div class="summary-note" style="margin-top:6px;">${escapeHTML([taskIdText, attemptText, startedText, endedText].join(' | '))}</div>
          <div class="summary-note" style="margin-top:6px;">${escapeHTML(recentOutput)}</div>
        </div>
      </div>
    `;
  }

  function renderTaskCard(task, bucketKey) {
    const isSelected = state.backlogSelection === task.id;
    const status = normalizeBacklogStatus(task.status, 'pending');
    const progress = status === 'in_progress' ? 0.62 : status === 'done' ? 1 : 0.1;
    const tags = (task.tags || []).map((tag) => chip(tag)).join('');
    const skill = task.skill ? chip(task.skill, 'chip--info') : '';
    const meta = [chip(status.replace(/_/g, ' '), backlogStatusToneClass(status)), chip(task.estimate), skill].filter(Boolean).join('');
    const dependencyText = task.dependsOn && task.dependsOn.length ? `Depends on ${task.dependsOn.join(', ')}` : 'Dependencies unavailable';
    const fileScopeText = task.fileScope || (task.files && task.files.length ? task.files.join(', ') : 'File scope unavailable');
    const failureReason = toText(task.failureReason || toObject(task.failure).reason, '');
    const failureDetail = toText(task.failureDetail || toObject(task.failure).detail, '');
    const recentOutput = compactText(task.recentOutput, 180) || 'Recent output unavailable.';
    return `
      <button type="button" class="task-card" data-backlog-select="${escapeHTML(task.id)}" aria-pressed="${isSelected ? 'true' : 'false'}">
        <div class="task-card__head">
          <span class="task-card__id">${escapeHTML(task.id)}</span>
          <span class="task-card__priority" style="color:${priorityColor(task.priority)}">${escapeHTML(task.priority)}</span>
        </div>
        <div class="task-card__title">${escapeHTML(task.title)}</div>
        <div class="task-card__meta">
          ${tags}
          ${meta}
        </div>
        <div class="summary-note" style="margin-top:8px;">${escapeHTML(compactText(dependencyText, 140) || 'Dependencies unavailable')}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(`File scope: ${fileScopeText}`, 140) || 'File scope unavailable')}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(task.attempt != null ? `Attempt ${task.attempt}` : 'Attempt unavailable')}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(failureReason ? `Failure: ${failureReason}${failureDetail ? ` | ${compactText(failureDetail, 120)}` : ''}` : 'Failure unavailable')}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(recentOutput)}</div>
        ${status === 'in_progress' ? `
          <div class="meter" style="margin-top:8px; width: 100%;"><div class="meter__fill meter__fill--warn" style="width:${progressWidth(progress)}"></div></div>
        ` : ''}
        ${isSelected ? `<div class="summary-note" style="margin-top:8px;">Selected for detail view</div>` : ''}
      </button>
    `;
  }

  function renderGoalItem(bucket, goal, index) {
    const done = Boolean(goal.done);
    const sourceLine = goal.lineNumber || goal.line || 0;
    const checkboxState = goal.checkbox || (done ? '[x]' : '[ ]');
    return `
      <div class="goal-item ${done ? 'goal-item--done' : ''}">
        <div class="goal-item__row">
          <button type="button" class="goal-item__check" data-goal-action="toggle" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}" aria-label="Toggle goal">
            ${done ? 'X' : ' '}
          </button>
          <div class="goal-item__body">
            <div class="goal-item__title ${done ? 'goal-item__title--done' : ''}">${escapeHTML(goal.text)}</div>
            ${goal.note ? `<div class="goal-item__note">${escapeHTML(goal.note)}</div>` : ''}
            ${sourceLine ? `<div class="summary-note" style="margin-top:4px;">Source line ${escapeHTML(sourceLine)} · ${escapeHTML(checkboxState)}</div>` : ''}
            <div class="goal-item__actions">
              <button type="button" class="button button--tiny button--quiet" data-goal-action="edit" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}">Edit</button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="move" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}">
                Move to ${bucket === 'p0' ? 'P1' : 'P0'}
              </button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="delete" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}">Delete</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderNotificationItem(item) {
    const color = kindColor(item.kind);
    const kindText = item.kind.replace(/_/g, '.').toUpperCase();
    return `
      <div class="notification-feed__item">
        <div class="notification-feed__kind">
          <span class="dot" style="color:${color}; background:${color}"></span>
          ${escapeHTML(kindText)}
        </div>
        <div class="notification-feed__age">${escapeHTML(fmtRelative(item.t))}</div>
        <div class="notification-feed__msg">${escapeHTML(item.text)}</div>
        <div class="notification-feed__run">${escapeHTML(item.run)}</div>
      </div>
    `;
  }

  function renderHistoryRow(run) {
    const selected = state.historySelection === run.id;
    const color =
      run.status === 'success'
        ? 'var(--accent)'
        : run.status === 'failed'
          ? 'var(--err)'
          : run.status === 'stopped'
            ? 'var(--warn)'
            : 'var(--info)';
    return `
      <button type="button" class="history-table__row ${selected ? 'config-row--active' : ''}" data-history-select="${escapeHTML(run.id)}">
        <span class="history-table__status" style="color:${color}">
          <span class="${run.status === 'running' ? 'dot dot--pulse' : 'dot'}" style="background:${color}"></span>
          ${escapeHTML(run.status.toUpperCase())}
        </span>
        <span>
          <span>${escapeHTML(run.branch)}</span>
          <div class="history-table__id">${escapeHTML(run.id)}</div>
        </span>
        <span>${escapeHTML(`${run.tasksDone}/${run.tasksTotal}`)}</span>
        <span>${escapeHTML(fmtDuration(run.durationSec))}</span>
        <span>${escapeHTML(fmtRelative(run.startedAt))}</span>
        <span style="text-align:right;"><span class="chip chip--accent">Open</span></span>
      </button>
    `;
  }

  function renderPromptCard(prompt) {
    const active = state.promptSelection === prompt.id;
    return `
      <button type="button" class="prompt-card ${active ? 'prompt-card--active' : ''}" data-prompt-select="${escapeHTML(prompt.id)}">
        <div class="prompt-card__head">
          <span class="badge badge--${prompt.mode === 'override' ? 'info' : 'dim'}">${escapeHTML(prompt.mode.toUpperCase())}</span>
          <div class="prompt-card__name">${escapeHTML(prompt.file)}</div>
        </div>
        <div class="prompt-card__meta">
          <span>${escapeHTML(prompt.scope)}</span>
          <span>${escapeHTML(prompt.source)}</span>
          <span>${escapeHTML(prompt.updated)}</span>
        </div>
        <div class="summary-note" style="margin-top:6px;">${escapeHTML(prompt.summary)}</div>
      </button>
    `;
  }

  function renderConfigValueSummary(path, schema, value) {
    if (!schema) return escapeHTML(JSON.stringify(value));
    if (schema.kind === 'bool') return escapeHTML(value === true ? 'enabled' : 'disabled');
    if (schema.kind === 'enum') return escapeHTML(value == null || value === '' ? '--' : String(value));
    if (schema.kind === 'multienum') return escapeHTML(fmtList(value || []) || '--');
    if (schema.kind === 'number') return escapeHTML(value == null || value === '' ? '--' : String(value));
    return escapeHTML(value == null || value === '' ? '--' : String(value));
  }

  function validateField(path, value, schema) {
    if (!schema) return null;
    if (schema.kind === 'bool') {
      return typeof value === 'boolean' ? null : 'must be a boolean';
    }
    if (schema.kind === 'number') {
      if (value === '' || value == null || Number.isNaN(Number(value))) return 'must be a number';
      const num = Number(value);
      if (schema.min != null && num < schema.min) return `must be >= ${schema.min}`;
      if (schema.max != null && num > schema.max) return `must be <= ${schema.max}`;
      return null;
    }
    if (schema.kind === 'text') {
      return String(value || '').trim() ? null : 'cannot be empty';
    }
    if (schema.kind === 'enum') {
      return schema.options.includes(value) ? null : `must be one of: ${schema.options.join(', ')}`;
    }
    if (schema.kind === 'multienum') {
      if (!Array.isArray(value) || value.length === 0) return 'pick at least one option';
      const invalid = value.filter((item) => !schema.options.includes(item));
      return invalid.length ? `invalid option(s): ${invalid.join(', ')}` : null;
    }
    return null;
  }

  function getConfigDiffs() {
    const diffs = [];
    for (const path of Object.keys(state.configSchema)) {
      const current = getAt(state.config, path);
      const base = getAt(state.configDefault, path);
      if (JSON.stringify(current) !== JSON.stringify(base)) {
        diffs.push({
          path,
          from: base,
          to: current,
          restart: Boolean(state.configSchema[path].restart),
        });
      }
    }
    return diffs;
  }

  function configGroups() {
    return [
      { title: 'Project', paths: ['repo', 'execution_backend', 'roles'] },
      { title: 'Runtime', paths: ['autopilot', 'continuous', 'iterations', 'worktree_isolation', 'run_tests'] },
      { title: 'Budget', paths: ['budget.max_usd', 'budget.max_iters', 'budget.max_continuations'] },
      { title: 'Models', paths: ['claudecode.dev_model', 'claudecode.dev_model_tier1', 'claudecode.qa_model', 'claudecode.reporter_model'] },
      { title: 'Telegram', paths: ['telegram.enabled', 'telegram.instance_name'] },
      { title: 'Prompts', paths: ['prompts_dir'] },
      { title: 'Worktree', paths: ['worktree_merge_mode'] },
    ];
  }

  function currentConfigSelection() {
    if (state.configSelection && state.configSchema[state.configSelection]) {
      return state.configSelection;
    }
    return configGroups().flatMap((group) => group.paths).find((path) => state.configSchema[path]) || '';
  }

  function currentPrompt() {
    if (!state.prompts.length) {
      return null;
    }
    return state.prompts.find((prompt) => prompt.id === state.promptSelection) || state.prompts[0];
  }

  function currentRun() {
    if (!state.runs.length) {
      return null;
    }
    return state.runs.find((run) => run.id === state.historySelection) || state.runs[0];
  }

  function currentBacklogTask() {
    if (!state.backlog.length) {
      return null;
    }
    if (!state.backlogSelection) {
      return null;
    }
    return state.backlog.find((task) => task.id === state.backlogSelection) || null;
  }

  function repoNameFromPath(value) {
    const text = String(value || '').trim().replace(/[\\/]+$/, '');
    if (!text) return '';
    const parts = text.split(/[\\/]+/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : text;
  }

  function quoteCommandArg(value) {
    return `"${String(value || '').replace(/"/g, '\\"')}"`;
  }

  function currentRepoPath() {
    return String(state.activeRun.repo || getAt(state.config, 'repo') || '').trim();
  }

  function currentRepoLabel() {
    return String(
      state.activeRun.repoLabel
      || state.repo.name
      || repoNameFromPath(currentRepoPath())
      || repoNameFromPath(state.activeRun.repo)
      || 'agentcli',
    ).trim();
  }

  function currentRunCommandSegments() {
    const repoPath = currentRepoPath();
    const rawIterations = Number(state.config.iterations || state.activeRun.maxIterations || 1);
    const iterations = Number.isFinite(rawIterations) && rawIterations > 0 ? Math.max(1, Math.round(rawIterations)) : 1;
    const headParts = ['agentcli', '--run-now'];
    if (repoPath) {
      headParts.push('--repo', quoteCommandArg(repoPath));
    }
    const tailParts = [];
    if (state.config.autopilot !== false) {
      tailParts.push('--autopilot');
    }
    if (state.config.continuous !== false) {
      tailParts.push('--continuous');
    }
    tailParts.push('--iterations', String(iterations));
    return {
      head: headParts.join(' '),
      tail: tailParts.join(' '),
    };
  }

  function currentRunCommand() {
    const segments = currentRunCommandSegments();
    return [segments.head, segments.tail].filter(Boolean).join(' ');
  }

  function currentRunCommandPreviewLines() {
    const segments = currentRunCommandSegments();
    if (!segments.tail) {
      return [segments.head];
    }
    return [`${segments.head} \\`, `  ${segments.tail}`];
  }

  function overlayRoot() {
    return document.getElementById('overlay-root');
  }

  function mainRoot() {
    return document.getElementById('main');
  }

  function topbarRoot() {
    return document.getElementById('topbar');
  }

  function sidebarRoot() {
    return document.getElementById('sidebar');
  }

  function activeRunStatusClass() {
    return runStatusClass(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason);
  }

  function runnerControlBusyAction() {
    if (state.stopSubmitting) {
      return state.stopAction;
    }
    if (state.runnerControl.busy) {
      return state.runnerControl.lastAction || 'busy';
    }
    return '';
  }

  function runnerControlActionState(action) {
    const actions = toObject(state.runnerControl.actions);
    return toObject(actions[action]);
  }

  function runnerControlActionEnabled(action) {
    if (!state.runnerControl.enabled || !state.runnerControl.controllerAvailable || state.runnerControl.busy) {
      return false;
    }
    const busyAction = runnerControlBusyAction();
    if (busyAction) {
      return false;
    }
    return Boolean(runnerControlActionState(action).enabled);
  }

  function runnerControlActionDisabledReason(action) {
    if (state.stopSubmitting || state.runnerControl.busy) {
      return 'A runner control request is already in flight.';
    }
    if (!state.runnerControl.enabled) {
      return state.runnerControl.message || 'Runner controls are disabled.';
    }
    if (!state.runnerControl.controllerAvailable) {
      return state.runnerControl.message || 'Runner controller is unavailable.';
    }
    const actionState = runnerControlActionState(action);
    return toText(actionState.disabledReason || actionState.disabled_reason || state.runnerControl.message, '');
  }

  function runnerControlButtonAttrs(action) {
    const enabled = runnerControlActionEnabled(action);
    const reason = runnerControlActionDisabledReason(action);
    const busy = runnerControlBusyAction() === action || state.runnerControl.busy;
    const attrs = [];
    if (!enabled) {
      attrs.push('disabled');
      if (reason) {
        attrs.push(`title="${escapeHTML(reason)}"`);
      }
    }
    if (busy) {
      attrs.push('aria-busy="true"');
    }
    return attrs.join(' ');
  }

  function runnerControlRequestPath(action) {
    const normalized = String(action || '').toLowerCase();
    if (normalized === 'start') return '/api/runner/start';
    if (normalized === 'stop') return '/api/runner/stop';
    if (normalized === 'restart') return '/api/runner/restart';
    return '/api/runner/reload';
  }

  function renderTopbar() {
    const elapsed = state.activeRun.startedAt ? fmtDuration((nowMs() - state.activeRun.startedAt) / 1000) : '--';
    const quotaPct = metricText(state.activeRun.quotaAvailable, state.activeRun.quota.used, fmtPercent);
    const budgetPct = metricText(state.activeRun.budgetAvailable, state.activeRun.budgetUsed, fmtPercent);
    const quotaWidth = metricWidth(state.activeRun.quotaAvailable, state.activeRun.quota.used);
    const budgetWidth = metricWidth(state.activeRun.budgetAvailable, state.activeRun.budgetUsed);
    const activeStatus = runStatusLabel(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason);
    const activeTone = runStatusTone(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason);
    const runLabel = state.activeRun.id || 'no-run';
    const snapshotLabel = state.snapshotLabel || (state.sourceMode === 'fallback' ? 'Fallback data' : 'API snapshot');
    const runnerBusyAction = runnerControlBusyAction();
    const runnerChipTone =
      runnerBusyAction
        ? 'status-chip--warn'
        : state.runnerControl.lastError
          ? 'status-chip--err'
        : !state.runnerControl.controllerAvailable || !state.runnerControl.enabled
          ? 'status-chip--warn'
          : state.runnerControl.status.running
            ? 'status-chip--running'
            : 'status-chip--warn';
    const snapshotTone =
      state.snapshotStatus === 'error'
        ? 'status-chip--err'
        : state.snapshotStatus === 'loading' || state.snapshotStatus === 'fallback' || state.snapshotStatus === 'stale'
          ? 'status-chip--warn'
          : 'status-chip--running';
    return `
      <div class="topbar__brand">
        <span class="brand-mark"></span>
        <div class="brand-copy">
          <div class="brand-title">agentcli</div>
          <div class="brand-subtitle">${escapeHTML(state.activeRun.repoLabel || state.repo.name || 'agentcli')} / ${escapeHTML(runLabel)}</div>
        </div>
      </div>
      <div class="topbar__status">
        <span class="${activeRunStatusClass()}">
          <span class="${activeTone === 'running' ? 'dot dot--pulse' : 'dot'}" style="color: currentColor; background: currentColor;"></span>
          ${escapeHTML(activeStatus)}
        </span>
        <span class="status-chip">${escapeHTML(state.activeRun.stage || 'idle')} | iter ${escapeHTML(`${state.activeRun.iteration}/${state.activeRun.maxIterations}`)}</span>
        <span class="status-chip">elapsed ${escapeHTML(elapsed)}</span>
      </div>
      <div class="topbar__actions">
        <span class="status-chip ${snapshotTone}">
          <span class="dot" style="color: currentColor; background: currentColor;"></span>
          ${escapeHTML(snapshotLabel)}
        </span>
        <span class="status-chip ${runnerChipTone}" title="${escapeHTML(state.runnerControl.message || '')}">
          <span class="dot" style="color: currentColor; background: currentColor;"></span>
          ${escapeHTML(runnerBusyAction ? runnerControlActionLabel(runnerBusyAction, true) : state.runnerControl.enabled ? 'controls on' : 'controls off')}
        </span>
        ${button('Refresh', 'refresh-status', 'button--quiet', 'aria-label="Refresh snapshot"')}
        ${button(runnerControlActionLabel('start', runnerBusyAction === 'start'), 'runner-start', 'button--primary', `aria-label="Start runner" ${runnerControlButtonAttrs('start')}`)}
        ${button(runnerControlActionLabel('stop', runnerBusyAction === 'stop'), 'runner-stop', 'button--danger', `aria-label="Stop runner" ${runnerControlButtonAttrs('stop')}`)}
        ${button(runnerControlActionLabel('reload', runnerBusyAction === 'reload'), 'runner-reload', 'button--quiet', `aria-label="Reload runner" ${runnerControlButtonAttrs('reload')}`)}
        ${button(`Command`, 'open-palette', 'button--ghost', 'aria-label="Open command palette"')}
        <span class="meter-chip ${state.activeRun.quotaAvailable ? '' : 'meter-chip--unavailable'}" title="Quota usage">
          quota ${escapeHTML(quotaPct)}
          <span class="meter ${state.activeRun.quotaAvailable ? '' : 'meter--unavailable'}"><span class="meter__fill ${state.activeRun.quotaAvailable ? 'meter__fill--info' : 'meter__fill--muted'}" style="width:${escapeHTML(quotaWidth)}"></span></span>
        </span>
        <span class="meter-chip ${state.activeRun.budgetAvailable ? '' : 'meter-chip--unavailable'}" title="Budget usage">
          budget ${escapeHTML(budgetPct)}
          <span class="meter ${state.activeRun.budgetAvailable ? '' : 'meter--unavailable'}"><span class="meter__fill ${state.activeRun.budgetAvailable ? 'meter__fill--warn' : 'meter__fill--muted'}" style="width:${escapeHTML(budgetWidth)}"></span></span>
        </span>
      </div>
    `;
  }

  function renderSidebar() {
    const repoLabel = state.activeRun.repoLabel || state.repo.name || 'agentcli';
    const branchLabel = state.activeRun.branch || state.repo.branch || 'HEAD';
    const quotaWindow = state.activeRun.quota.window || '5h';
    const quotaPct = metricText(state.activeRun.quotaAvailable, state.activeRun.quota.used, fmtPercent);
    const quotaWidth = metricWidth(state.activeRun.quotaAvailable, state.activeRun.quota.used);
    const liveLabel =
      state.snapshotStatus === 'loading'
        ? 'loading snapshot'
        : state.sourceMode === 'fallback'
          ? 'fallback data'
          : `${state.activeRun.backend} live`;
    const groups = [
      {
        title: 'Run',
        items: [
          { view: 'dashboard', label: 'Dashboard', shortcut: VIEW_SHORTCUTS.dashboard },
          { view: 'pipeline', label: 'Pipeline', shortcut: VIEW_SHORTCUTS.pipeline },
          { view: 'logs', label: 'Logs', shortcut: VIEW_SHORTCUTS.logs },
        ],
      },
      {
        title: 'Project',
        items: [
          { view: 'backlog', label: 'Backlog', shortcut: VIEW_SHORTCUTS.backlog },
          { view: 'goals', label: 'Goals', shortcut: VIEW_SHORTCUTS.goals },
          { view: 'config', label: 'Config', shortcut: VIEW_SHORTCUTS.config },
          { view: 'prompts', label: 'Prompts', shortcut: VIEW_SHORTCUTS.prompts },
          { view: 'worktree', label: 'Worktree Review', shortcut: VIEW_SHORTCUTS.worktree, badge: state.worktreeMerge.reviewRequired ? '!' : '' },
        ],
      },
      {
        title: 'History',
        items: [
          { view: 'history', label: 'Run History', shortcut: VIEW_SHORTCUTS.history },
          { view: 'notifications', label: 'Notifications', shortcut: VIEW_SHORTCUTS.notifications, badge: String(state.notifications.length) },
        ],
      },
      {
        title: 'Preview',
        items: [
          { view: 'landing', label: 'Landing preview', shortcut: VIEW_SHORTCUTS.landing },
          { view: 'mobile', label: 'Mobile preview', shortcut: VIEW_SHORTCUTS.mobile },
        ],
      },
    ];

    const groupsHTML = groups
      .map((group) => {
        const items = group.items.map((item) => navButton(item, state.activeView === item.view)).join('');
        return `
          <div class="nav-group">
            <div class="nav-group__title">${escapeHTML(group.title)}</div>
            ${items}
          </div>
        `;
      })
      .join('');

    return `
      <div class="sidebar__inner">
        ${groupsHTML}
        <div class="sidebar-card">
          <div class="sidebar-card__title">
            <span class="${runStatusTone(state.progress?.run_status || state.activeRun.status) === 'running' ? 'dot dot--pulse' : 'dot'}"></span>
            ${escapeHTML(liveLabel)}
          </div>
          <div>${escapeHTML(repoLabel)} | ${escapeHTML(branchLabel)}</div>
          <div class="sidebar-card__sub">${escapeHTML(quotaWindow)} quota | ${escapeHTML(quotaPct)} used</div>
          <div class="meter ${state.activeRun.quotaAvailable ? '' : 'meter--unavailable'}" style="margin-top:8px; width: 100%;">
            <div class="meter__fill ${state.activeRun.quotaAvailable ? 'meter__fill--info' : 'meter__fill--muted'}" style="width:${escapeHTML(quotaWidth)}"></div>
          </div>
        </div>
      </div>
    `;
  }

  function renderRunnerControlsPanel() {
    const control = state.runnerControl;
    const busyAction = runnerControlBusyAction();
    const messageTone =
      busyAction
        ? 'warn'
        : control.lastError
          ? 'err'
          : !control.controllerAvailable || !control.enabled
            ? 'warn'
            : control.status.running
              ? 'running'
              : 'info';
    const statusSummary = [
      control.enabled ? 'enabled' : 'disabled',
      control.busy ? 'busy' : '',
      control.status.runnerMode || 'unknown',
      control.runStatus || (control.status.running ? 'running' : 'idle'),
    ]
      .filter(Boolean)
      .join(' | ');
    const buttonRow = [
      button(runnerControlActionLabel('start', busyAction === 'start'), 'runner-start', 'button--primary', `aria-label="Start runner" ${runnerControlButtonAttrs('start')}`),
      button(runnerControlActionLabel('stop', busyAction === 'stop'), 'runner-stop', 'button--danger', `aria-label="Stop runner" ${runnerControlButtonAttrs('stop')}`),
      button(runnerControlActionLabel('reload', busyAction === 'reload'), 'runner-reload', 'button--quiet', `aria-label="Reload runner" ${runnerControlButtonAttrs('reload')}`),
    ].join('');
    const detailItems = [
      { label: 'Source', value: control.source || 'unknown' },
      { label: 'Controller', value: control.controllerAvailable ? 'available' : 'unavailable' },
      { label: 'Run mode', value: control.status.runnerMode || 'unknown' },
      { label: 'Run status', value: control.runStatus || (control.status.running ? 'running' : 'idle') },
    ];
    const detailHTML = detailItems
      .map(
        (item) => `
          <div class="runner-control__detail">
            <div class="runner-control__label">${escapeHTML(item.label)}</div>
            <div class="runner-control__value">${escapeHTML(item.value)}</div>
          </div>
        `
      )
      .join('');
    const message = control.message || 'Runner control status is not available yet.';
    return panel(
      'Runner controls',
      escapeHTML(statusSummary),
      `
        <div class="runner-control">
          <div class="modal-banner section-banner section-banner--${messageTone}">
            <span class="dot" style="background: currentColor;"></span>
            <div>
              <div class="section-banner__title">${escapeHTML(control.lastError ? 'Backend error' : busyAction ? 'Action in flight' : control.enabled ? 'Server opt-in enabled' : 'Controls disabled')}</div>
              <div class="section-banner__copy">${escapeHTML(message)}</div>
            </div>
          </div>
          <div class="runner-control__details">
            ${detailHTML}
          </div>
          <div class="runner-control__buttons">
            ${buttonRow}
          </div>
          <div class="summary-note">
            Confirmation phrases: start = ${escapeHTML(control.confirmation.start)}, stop = ${escapeHTML(control.confirmation.stop)}, reload = ${escapeHTML(control.confirmation.reload)}, restart = ${escapeHTML(control.confirmation.restart)}.
          </div>
        </div>
      `,
      'runner-control-panel'
    );
  }

  function renderDashboard() {
    const run = state.activeRun;
    const progress = state.progress || {};
    const budgetCap = toMaybeNumber(state.config?.budget?.max_usd);
    const taskId = progress.current_task_id || run.task || '';
    const taskTitle = progress.current_task_title || run.taskTitle || '';
    const attempt = progress.attempt ?? run.attempt;
    const attemptText = attempt == null ? 'unavailable' : String(attempt);
    const branchText = progress.branch || run.branch || state.repo.branch || 'HEAD';
    const worktreeModeText = progress.worktree_mode || run.worktreeMode || '';
    const runDirText = run.runDir || progress.latest_run_dir || state.latestRunDir || '';
    const finalReason = progress.final_reason || run.finalReason || '';
    const runStatus = progress.run_status || run.status;
    const runTone = runStatusTone(runStatus, finalReason);
    const runLabel = runStatusLabel(runStatus, finalReason);
    const runSummary = [
      `task ${taskId || 'unavailable'}`,
      taskTitle || 'task title unavailable',
      `attempt ${attemptText}`,
      `branch ${branchText}`,
      `worktree ${worktreeModeText || 'unavailable'}`,
      runDirText ? `run ${runDirText}` : 'run directory unavailable',
      finalReason ? `reason ${finalReason}` : null,
    ]
      .filter(Boolean)
      .join(' | ');
    const hasTokenTelemetry = Boolean(
      run.tokensAvailable ||
        run.tokens?.available ||
        run.tokens?.in != null ||
        run.tokens?.out != null
    );
    const tokenIn = hasTokenTelemetry ? run.tokens.in : null;
    const tokenOut = hasTokenTelemetry ? run.tokens.out : null;
    const tokenTotal = hasTokenTelemetry && tokenIn != null && tokenOut != null ? Number(tokenIn) + Number(tokenOut) : null;
    const budgetValue = run.budgetAvailable && run.budgetUsed != null && budgetCap != null ? run.budgetUsed * budgetCap : null;
    const doneTasks = state.backlog.filter((task) => task.status === 'done').length;
    const totalTasks = state.backlog.length;
    const p0Done = state.goals.p0.filter((goal) => goal.done).length;
    const p0Total = state.goals.p0.length;
    const selectedTask = currentBacklogTask();
    const latestLogs = state.logs.slice(-8);
    const recentNotifs = state.notifications.slice(0, 4);
    const tokenValueText = tokenTotal != null ? fmtNumberShort(tokenTotal) : 'unavailable';
    const tokenSubText = hasTokenTelemetry
      ? `in ${metricText(hasTokenTelemetry, tokenIn, fmtNumberShort)} | out ${metricText(hasTokenTelemetry, tokenOut, fmtNumberShort)}`
      : 'in unavailable | out unavailable';
    const budgetCardValue = budgetValue != null ? fmtMoney(budgetValue) : 'unavailable';
    const budgetCardSub = budgetCap != null
      ? `of ${fmtMoney(budgetCap)} | ${metricText(run.budgetAvailable, run.budgetUsed, fmtPercent)}`
      : `of unavailable | ${metricText(run.budgetAvailable, run.budgetUsed, fmtPercent)}`;

    const body = `
      <div class="view-grid view-grid--two">
        <div class="view-grid">
          ${sectionNotice('activeRun')}
          ${renderRunnerControlsPanel()}
          <div class="stat-grid stat-grid--four">
            ${metricCard('Stage', run.stage, `iter ${run.iteration}/${run.maxIterations}`, true)}
            ${metricCard('Tasks', `${doneTasks}/${totalTasks}`, `${totalTasks - doneTasks} remaining`)}
            ${metricCard('Tokens', tokenValueText, tokenSubText, false, tokenValueText === 'unavailable' ? 'unavailable' : '')}
            ${metricCard('Budget', budgetCardValue, budgetCardSub, false, budgetCardValue === 'unavailable' ? 'unavailable' : '')}
          </div>

          ${panel(
            'Pipeline snapshot',
            `active task ${escapeHTML(taskId || 'unavailable')} | ${escapeHTML(run.backend)}`,
            `
              <div class="pipeline">
                <div class="pipeline__row">
                  ${renderLifecycleLane(state.stages)}
                </div>
              </div>
            `
          )}

          ${panel(
            'Live logs',
            `${escapeHTML(state.logs.length)} lines | tail -f`,
            `
              ${sectionNotice('logs')}
              <div class="log-feed">
                <div class="log-feed__scroll">
                  ${latestLogs.length ? latestLogs.map((line) => renderLogRow(line)).join('') : '<div class="summary-note">No log entries yet.</div>'}
                </div>
              </div>
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            'Run facts',
            `${escapeHTML(taskId || 'unavailable')} | ${escapeHTML(taskTitle || 'task title unavailable')}`,
            `
              <div class="runner-control">
                <div class="${runBannerClass(runStatus, finalReason)}">
                  <span class="${statusDotClass(runStatus)}" style="background: currentColor;"></span>
                  <div>
                    <div class="section-banner__title">${escapeHTML(runLabel)}</div>
                    <div class="section-banner__copy">${escapeHTML(runSummary)}</div>
                  </div>
                </div>
                <div class="runner-control__details">
                  ${detailCard('Current task id', taskId || 'unavailable')}
                  ${detailCard('Current task title', taskTitle || 'unavailable')}
                  ${detailCard('Attempt', attemptText)}
                  ${detailCard('Branch', branchText)}
                  ${detailCard('Worktree mode', worktreeModeText || 'unavailable')}
                  ${detailCard('Run directory', runDirText || 'unavailable')}
                  ${finalReason ? detailCard('Final reason', finalReason, runTone === 'failed' ? 'err' : runTone === 'stopped' ? 'warn' : (runTone === 'completed' || runTone === 'success') ? 'accent' : 'muted') : ''}
                </div>
              </div>
            `
          )}

          ${panel(
            'Goals snapshot',
            `P0 ${p0Done}/${p0Total}`,
            `
              <div class="compact-list">
                ${sectionNotice('goals')}
                ${state.goals.p0.length ? state.goals.p0.slice(0, 4).map((goal) => `
                  <div class="compact-list__item">
                    <span class="compact-list__bullet" style="background:${goal.done ? 'var(--accent)' : 'var(--text-sub)'}"></span>
                    <div>
                      <div class="compact-list__body ${goal.done ? 'goal-item__title--done' : ''}">${escapeHTML(goal.text)}</div>
                      ${goal.note ? `<div class="compact-list__meta">${escapeHTML(goal.note)}</div>` : ''}
                    </div>
                  </div>
                `).join('') : '<div class="summary-note">No goals published yet.</div>'}
              </div>
            `
          )}

          ${panel(
            'Selected backlog item',
            selectedTask ? escapeHTML(selectedTask.id) : 'none',
            selectedTask
              ? `
                <div class="task-card" style="padding:12px 12px;">
                  <div class="task-card__head">
                    <span class="task-card__id">${escapeHTML(selectedTask.id)}</span>
                    <span class="task-card__priority" style="color:${priorityColor(selectedTask.priority)}">${escapeHTML(selectedTask.priority)}</span>
                  </div>
                  <div class="task-card__title">${escapeHTML(selectedTask.title)}</div>
                  <div class="task-card__meta">
                    ${chip(normalizeBacklogStatus(selectedTask.status, 'pending').replace(/_/g, ' '), backlogStatusToneClass(selectedTask.status))}
                    ${chip(selectedTask.estimate)}
                    ${selectedTask.skill ? chip(selectedTask.skill, 'chip--info') : ''}
                  </div>
                  <div class="summary-note" style="margin-top:8px;">${escapeHTML(selectedTask.dependsOn && selectedTask.dependsOn.length ? compactText(`Depends on ${selectedTask.dependsOn.join(', ')}`, 140) : 'Dependencies unavailable')}</div>
                  <div class="summary-note" style="margin-top:4px;">${escapeHTML(selectedTask.fileScope ? compactText(`File scope: ${selectedTask.fileScope}`, 140) : 'File scope unavailable')}</div>
                  <div class="summary-note" style="margin-top:4px;">${escapeHTML(selectedTask.attempt != null ? `Attempt ${selectedTask.attempt}` : 'Attempt unavailable')}</div>
                  <div class="summary-note" style="margin-top:4px;">${escapeHTML(selectedTask.failureReason ? `Failure: ${selectedTask.failureReason}${selectedTask.failureDetail ? ` | ${compactText(selectedTask.failureDetail, 120)}` : ''}` : 'Failure unavailable')}</div>
                </div>
              `
              : state.backlog.length ? `<div class="summary-note">No task selected.</div>` : `<div class="summary-note">No backlog artifacts were published yet.</div>`
          )}

          ${panel(
            'Notifications',
            `${recentNotifs.length} recent`,
            `
              <div class="compact-list">
                ${sectionNotice('notifications')}
                ${recentNotifs.length ? recentNotifs.map((item) => `
                  <div class="compact-list__item">
                    <span class="compact-list__bullet" style="background:${kindColor(item.kind)}"></span>
                    <div>
                      <div class="compact-list__body">${escapeHTML(item.text)}</div>
                      <div class="compact-list__meta">${escapeHTML(item.kind)} | ${escapeHTML(fmtRelative(item.t))}</div>
                    </div>
                  </div>
                `).join('') : '<div class="summary-note">No notifications yet.</div>'}
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'dashboard',
      'Dashboard',
      `${escapeHTML(taskId || 'unavailable')} | ${escapeHTML(taskTitle || 'task title unavailable')} | ${escapeHTML(branchText)} | ${escapeHTML(run.id)}`,
      `
        ${button('Open Pipeline', 'nav-pipeline', 'button--quiet')}
        ${button('Open Logs', 'nav-logs', 'button--quiet')}
        ${button('Worktree Review', 'nav-worktree', 'button--quiet')}
      `,
      body
    );
  }

  function renderLogRow(line) {
    const stageColor =
      line.stage === 'Dev'
        ? 'var(--accent)'
        : line.stage === 'PM'
          ? 'var(--info)'
          : line.stage === 'QA'
            ? 'var(--warn)'
            : 'var(--text-dim)';
    return `
      <div class="${severityClass(line.lvl)}">
        <div class="log-row__time">${escapeHTML(line.t)}</div>
        <div class="log-row__stage" style="color:${stageColor}">[${escapeHTML(line.stage)}]</div>
        <div class="log-row__level">${escapeHTML(line.lvl)}</div>
        <div class="log-row__msg">${escapeHTML(line.msg)}</div>
      </div>
    `;
  }

  function renderPipeline() {
    const hasTokenTelemetry = Boolean(
      state.activeRun.tokensAvailable ||
        state.activeRun.tokens?.available ||
        state.activeRun.tokens?.in != null ||
        state.activeRun.tokens?.out != null
    );
    const tokenIn = hasTokenTelemetry ? state.activeRun.tokens.in : null;
    const tokenOut = hasTokenTelemetry ? state.activeRun.tokens.out : null;
    const tokenTotal = hasTokenTelemetry && tokenIn != null && tokenOut != null ? Number(tokenIn) + Number(tokenOut) : null;
    const tokenInputText = metricText(hasTokenTelemetry, tokenIn, fmtNumberShort);
    const tokenOutputText = metricText(hasTokenTelemetry, tokenOut, fmtNumberShort);
    const tokenBudgetText = metricText(state.activeRun.budgetAvailable, state.activeRun.budgetUsed, fmtPercent);
    const tokenSparkline = state.metrics.tokens24h.length
      ? buildSparkline(state.metrics.tokens24h, 320, 44, 'rgba(126,227,138,0.12)', '#7ee38a')
      : '<div class="summary-note">Token telemetry unavailable.</div>';
    const stageSummary = renderLifecycleLane(state.stages);
    const outputs = state.stages.length
      ? state.stages.map((stage) => `
        <div class="task-card">
          <div class="task-card__head">
            <span class="task-card__id">${escapeHTML(stage.label)}</span>
            <span class="chip ${lifecycleStatusToneClass(stage.status)}">${escapeHTML(normalizeStageStatus(stage.status, 'pending').replace(/_/g, ' '))}</span>
          </div>
          <div class="task-card__title">${escapeHTML(stage.taskTitle || stage.title || 'Lifecycle record')}</div>
          <div class="task-card__meta">
            ${chip(stage.taskId || 'task unavailable', 'chip--info')}
            ${chip(stage.attempt != null ? `attempt ${stage.attempt}` : 'attempt unavailable', stage.attempt != null ? 'chip--accent' : 'chip--info')}
            ${chip(stage.cycle != null ? `cycle ${stage.cycle}` : 'cycle unavailable', 'chip--info')}
            ${stage.model ? chip(stage.model, 'chip--info') : ''}
          </div>
          <div class="summary-note" style="margin-top:8px;">${escapeHTML(stage.startedAt ? `Started ${fmtClock(stage.startedAt)}` : 'Started unavailable')} | ${escapeHTML(stage.endedAt ? `Ended ${fmtClock(stage.endedAt)}` : normalizeStageStatus(stage.status, 'pending') === 'running' ? 'In progress' : 'Ended unavailable')}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(stage.recentOutput, 220) || 'Recent output unavailable.')}</div>
        </div>
      `).join('')
      : ['<div class="summary-note">No lifecycle records were published yet.</div>'];

    const body = `
      <div class="view-grid view-grid--two">
        <div class="view-grid">
          ${panel(
            'Stage lane',
            `iter ${escapeHTML(`${state.activeRun.iteration}/${state.activeRun.maxIterations}`)} | current ${escapeHTML(state.activeRun.stage)}`,
            `
              ${sectionNotice('stages')}
              <div class="pipeline">
                <div class="pipeline__row">
                  ${stageSummary}
                </div>
              </div>
            `
          )}

          ${panel(
            'Current stage output',
            escapeHTML(state.activeRun.task || `${state.stages.length} lifecycle records`),
            `
              <div class="view-grid view-grid--three">
                ${outputs.join('')}
              </div>
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            'Stage guardrails',
            escapeHTML(state.activeRun.backend),
            `
                <div class="compact-list">
                  <div class="compact-list__item">
                    <span class="compact-list__bullet"></span>
                    <div class="compact-list__body">Read-only shell by default. Stop, merge, and discard are not auto-applied here.</div>
                  </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div class="compact-list__body">Current run uses manual stop confirmation and a local review workflow.</div>
                </div>
                  <div class="compact-list__item">
                    <span class="compact-list__bullet"></span>
                    <div class="compact-list__body">Dev stage: ${escapeHTML(state.activeRun.task || 'unavailable')} | budget ${escapeHTML(tokenBudgetText)}</div>
                  </div>
                </div>
              `
          )}

          ${panel(
            'Live tokens',
            '24h sparkline',
            `
              <div class="kpi-grid kpi-grid--three">
                ${kpiCard('Input', tokenInputText, hasTokenTelemetry ? 'tokens processed' : 'token telemetry unavailable', false, tokenInputText === 'unavailable' ? 'unavailable' : '')}
                ${kpiCard('Output', tokenOutputText, hasTokenTelemetry ? 'tokens generated' : 'token telemetry unavailable', false, tokenOutputText === 'unavailable' ? 'unavailable' : '')}
                ${kpiCard('Budget', tokenBudgetText, state.activeRun.budgetAvailable ? 'used this run' : 'budget telemetry unavailable', false, tokenBudgetText === 'unavailable' ? 'unavailable' : '')}
              </div>
              <div style="margin-top:12px;">${tokenSparkline}</div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'pipeline',
      'Pipeline',
      `Active stage ${escapeHTML(state.activeRun.stage)} | ${escapeHTML(state.activeRun.id)}`,
      `
        ${button('Open Logs', 'nav-logs', 'button--quiet')}
        ${button('Open Backlog', 'nav-backlog', 'button--quiet')}
      `,
      body
    );
  }

  function renderLogs() {
    const filters = ['all', 'info', 'warn', 'err', 'debug'];
    const filtered = state.logs.filter((line) => state.logFilter === 'all' || line.lvl === state.logFilter);
    const logsMode =
      state.snapshotStatus === 'loading'
        ? 'loading'
        : state.snapshotStatus === 'fallback'
          ? 'fallback'
          : state.serverMode
            ? 'snapshot'
            : state.logsPaused
              ? 'paused'
              : 'tail -f';
    const logsAction =
      state.snapshotStatus === 'loading'
        ? 'Loading'
        : state.serverMode
          ? 'Read-only snapshot'
          : state.logsPaused
            ? 'Resume live tail'
            : 'Pause live tail';
    const logsButtonClass =
      state.snapshotStatus === 'loading'
        ? 'button--quiet'
        : state.serverMode
          ? 'button--quiet'
          : state.logsPaused
            ? 'button--primary'
            : 'button--quiet';

    const body = `
      <div class="view-grid">
        ${panel(
          'Tail filter',
          logsMode,
          `
            ${sectionNotice('logs')}
            <div class="logs-toolbar">
              <div class="filters">
                ${filters
                  .map((filter) => `
                    <button type="button" class="filter-chip ${state.logFilter === filter ? 'filter-chip--active' : ''}" data-filter="${escapeHTML(filter)}">${escapeHTML(filter.toUpperCase())}</button>
                  `)
                  .join('')}
              </div>
              <div style="margin-left:auto; display:flex; gap:8px; align-items:center;">
                <span class="status-chip ${state.logsPaused ? 'status-chip--warn' : 'status-chip--running'}">
                  <span class="${state.logsPaused ? 'dot' : 'dot dot--pulse'}" style="color: currentColor; background: currentColor;"></span>
                  ${state.logsPaused ? 'paused' : 'live'}
                </span>
                ${button(state.logsPaused ? 'Resume' : 'Pause', 'toggle-logs', state.logsPaused ? 'button--primary' : 'button--quiet')}
              </div>
            </div>
          `
        )}

        ${panel(
          'cycle_summary.log',
          `${escapeHTML(filtered.length)} lines shown`,
          `
            <div class="log-feed">
              <div class="log-feed__scroll" data-log-scroll>
                ${filtered.length ? filtered.map((line) => renderLogRow(line)).join('') : '<div class="summary-note">No log entries match the current filter.</div>'}
                ${!state.logsPaused && !state.serverMode ? `
                  <div class="log-row" style="color: var(--accent);">
                    <div class="log-row__time">${escapeHTML(fmtClock(nowMs()))}</div>
                    <div class="log-row__stage" style="color: var(--accent);">[${escapeHTML(state.activeRun.stage)}]</div>
                    <div class="log-row__level">live</div>
                    <div class="log-row__msg">waiting for next event...</div>
                  </div>
                ` : ''}
              </div>
            </div>
          `
        )}
      </div>
    `;

    return viewShell(
      'logs',
      'Logs',
      `cycle_summary.log | ${escapeHTML(logsMode)}`,
      `
        ${button(logsAction, 'toggle-logs', logsButtonClass)}
        ${button('Open Dashboard', 'nav-dashboard', 'button--quiet')}
      `,
      body
    );
  }

  function renderBacklog() {
    const buckets = [
      { key: 'pending', label: 'Pending' },
      { key: 'in_progress', label: 'In progress' },
      { key: 'done', label: 'Done' },
      { key: 'failed', label: 'Failed' },
    ];
    const selected = currentBacklogTask();
    const totals = buckets.map((bucket) => {
      const tasks = state.backlog.filter((task) => task.status === bucket.key);
      return { ...bucket, tasks };
    });

    const board = `
      <div class="board-grid board-grid--four">
        ${totals
          .map((bucket) => `
            <section class="column">
              <div class="column__head">
                <span class="chip ${bucket.key === 'done' ? 'chip--accent' : bucket.key === 'in_progress' ? 'chip--warn' : bucket.key === 'failed' ? 'chip--err' : 'chip--info'}">${escapeHTML(bucket.label)}</span>
                <span class="column__count">${escapeHTML(bucket.tasks.length)}</span>
              </div>
              <div class="column__body">
                ${bucket.tasks.length ? bucket.tasks.map((task) => renderTaskCard(task, bucket.key)).join('') : `<div class="summary-note">${escapeHTML(state.backlog.length ? 'No tasks in this bucket.' : 'No backlog artifacts were published yet.')}</div>`}
              </div>
            </section>
          `)
          .join('')}
      </div>
    `;

    const detail = selected
      ? `
        <div class="task-card">
          <div class="task-card__head">
            <span class="task-card__id">${escapeHTML(selected.id)}</span>
            <span class="task-card__priority" style="color:${priorityColor(selected.priority)}">${escapeHTML(selected.priority)}</span>
          </div>
          <div class="task-card__title">${escapeHTML(selected.title)}</div>
          <div class="task-card__meta">
            ${chip(normalizeBacklogStatus(selected.status, 'pending').replace(/_/g, ' '), backlogStatusToneClass(selected.status))}
            ${chip(selected.estimate)}
            ${selected.skill ? chip(selected.skill, 'chip--info') : ''}
          </div>
          <div class="summary-note" style="margin-top:10px;">${escapeHTML(selected.dependsOn && selected.dependsOn.length ? compactText(`Depends on ${selected.dependsOn.join(', ')}`, 140) : 'Dependencies unavailable')}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.fileScope ? compactText(`File scope: ${selected.fileScope}`, 140) : 'File scope unavailable')}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.attempt != null ? `Attempt ${selected.attempt}` : 'Attempt unavailable')}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.cycle != null ? `Cycle ${selected.cycle}` : 'Cycle unavailable')}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.step != null ? `Step ${selected.step}` : 'Step unavailable')}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.failureReason ? `Failure: ${selected.failureReason}${selected.failureDetail ? ` | ${compactText(selected.failureDetail, 120)}` : ''}` : 'Failure unavailable')}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(selected.recentOutput, 220) || 'Recent output unavailable.')}</div>
        </div>
      `
      : state.backlog.length ? `<div class="summary-note">No task selected.</div>` : `<div class="summary-note">No backlog artifacts were published yet.</div>`;

    const body = `
      <div class="view-grid view-grid--two">
        <div class="view-grid">
          ${sectionNotice('backlog')}
          ${panel(
            'Work queue',
            `${escapeHTML(state.backlog.length)} tasks`,
            board
          )}
        </div>
        <div class="view-grid">
          ${panel(
            'Backlog summary',
            escapeHTML(selected ? selected.id : 'none'),
            `
              <div class="kpi-grid kpi-grid--four">
                ${kpiCard('Pending', String(state.backlog.filter((task) => task.status === 'pending').length), 'queued')}
                ${kpiCard('Active', String(state.backlog.filter((task) => task.status === 'in_progress').length), 'in progress', true)}
                ${kpiCard('Done', String(state.backlog.filter((task) => task.status === 'done').length), 'completed')}
                ${kpiCard('Failed', String(state.backlog.filter((task) => task.status === 'failed').length), 'needs attention')}
              </div>
              <div style="margin-top:12px;">${detail}</div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'backlog',
      'Backlog',
      `Current task ${escapeHTML(state.activeRun.task || 'none')} | selected ${escapeHTML(selected ? selected.id : 'none')}`,
      `
        ${button('Open Goals', 'nav-goals', 'button--quiet')}
        ${button('Open Pipeline', 'nav-pipeline', 'button--quiet')}
      `,
      body
    );
  }

  function renderGoals() {
    const goalSnapshot = toObject(state.goalsSnapshot);
    const goalSummary = toObject(goalSnapshot.summary);
    const goalWarnings = toArray(goalSnapshot.warnings);
    const goalFilePath = toText(goalSnapshot.path || state.goalsPath || '.doc/GOALS.md', '.doc/GOALS.md');
    const goalFileExists = Boolean(goalSnapshot.exists);
    const goalFileSize = goalSnapshot.size;
    const goalFileMtime = goalSnapshot.mtime;
    const total = state.goals.p0.length + state.goals.p1.length;
    const done = state.goals.p0.filter((goal) => goal.done).length + state.goals.p1.filter((goal) => goal.done).length;
    const goalEditor = state.goalEditor;
    const goalsNote = state.snapshotStatus === 'loading'
      ? 'Loading the read-only snapshot...'
      : state.sourceMode === 'fallback'
      ? 'Fallback data is shown locally when the read-only API is unavailable.'
      : state.goalsDirty
      ? 'Browser-local edits are active. The read-only GOALS.md metadata stays visible below.'
      : goalFileExists
      ? 'Read-only GOALS.md snapshot with browser-local edits ready for later save workflow.'
      : 'GOALS.md is missing. Browser-local edits are still kept in the browser.';

    const body = `
      <div class="view-grid">
        ${panel(
          'Goal progress',
          `${escapeHTML(done)}/${escapeHTML(total)} complete`,
          `
            ${sectionNotice('goals')}
            <div class="meter" style="width:100%; height:10px;">
              <div class="meter__fill" style="width:${escapeHTML(total ? progressWidth(done / total) : '0%')}"></div>
            </div>
            <div class="summary-note" style="margin-top:10px;">${escapeHTML(goalsNote)}</div>
            <div class="summary-note" style="margin-top:4px;">Snapshot: ${escapeHTML(toNumber(goalSummary.done || 0, 0))}/${escapeHTML(toNumber(goalSummary.total || 0, 0))} checked · ${escapeHTML(toNumber(goalWarnings.length, 0))} parser warning${goalWarnings.length === 1 ? '' : 's'}</div>
          `
        )}

        ${panel(
          'GOALS.md snapshot',
          goalFileExists ? `${escapeHTML(goalSummary.total || 0)} parsed` : 'missing',
          `
            <div class="compact-list">
              <div class="compact-list__item">
                <span class="compact-list__bullet" style="background:${goalFileExists ? 'var(--accent)' : 'var(--warn)'}"></span>
                <div>
                  <div class="compact-list__body">${escapeHTML(goalFilePath)}</div>
                  <div class="compact-list__meta">Exists: ${escapeHTML(goalFileExists ? 'yes' : 'no')} · Size: ${escapeHTML(goalFileSize != null ? `${goalFileSize} bytes` : 'unknown')} · Mtime: ${escapeHTML(goalFileMtime != null ? fmtDateTime(goalFileMtime) : 'unknown')}</div>
                </div>
              </div>
            </div>
            <div class="summary-note" style="margin-top:10px;">Raw text preview</div>
            <div class="summary-note" style="margin-top:4px; white-space:pre-wrap; max-height:180px; overflow:auto;">${escapeHTML((goalSnapshot.raw_text || '').trim() || '(empty)')}</div>
            <div class="summary-note" style="margin-top:10px;">Parser warnings</div>
            ${goalWarnings.length ? `
              <div class="compact-list" style="margin-top:6px;">
                ${goalWarnings.slice(0, 5).map((warning) => `
                  <div class="compact-list__item">
                    <span class="compact-list__bullet" style="background:var(--warn)"></span>
                    <div>
                      <div class="compact-list__body">Line ${escapeHTML(warning.lineNumber || '?')} · ${escapeHTML(warning.reason)}</div>
                      <div class="compact-list__meta">${escapeHTML(warning.message || warning.line || '')}</div>
                    </div>
                  </div>
                `).join('')}
              </div>
            ` : '<div class="summary-note" style="margin-top:4px;">No parser warnings.</div>'}
          `
        )}

        <div class="goal-grid">
          ${['p0', 'p1']
            .map((bucket) => {
              const goals = state.goals[bucket];
              const color = bucket === 'p0' ? 'var(--err)' : 'var(--warn)';
              return `
                <section class="goal-bucket">
                  <div class="goal-bucket__head">
                    <span class="chip" style="border-color:${color}; color:${color};">${escapeHTML(bucket.toUpperCase())}</span>
                    <span>${escapeHTML(bucket === 'p0' ? 'Must-have' : 'Should-have')}</span>
                    <span class="status-chip" style="margin-left:auto;">${escapeHTML(goals.filter((goal) => goal.done).length)}/${escapeHTML(goals.length)}</span>
                    ${button('Add goal', `goal-add-${bucket}`, 'button--quiet button--tiny')}
                  </div>
                  <div class="goal-bucket__body">
                    ${goals.map((goal, index) => renderGoalItem(bucket, goal, index)).join('') || `<div class="summary-note">No goals yet.</div>`}
                  </div>
                </section>
              `;
            })
            .join('')}
        </div>
      </div>
    `;

    const view = viewShell(
      'goals',
      'Goals',
      'Local checklist with edit, move, and completion actions',
      `
        ${button('Add Goal', 'goal-add-p0', 'button--primary')}
        ${button('Reset Goals', 'reset-goals', 'button--quiet')}
      `,
      body
    );

    if (goalEditor) {
      state.overlayMode = 'goal';
    }
    return view;
  }

  function configControl(path) {
    const schema = state.configSchema[path];
    const value = getAt(state.config, path);
    if (!schema) {
      return `<div class="field-error">Missing schema for ${escapeHTML(path)}</div>`;
    }
    if (schema.kind === 'bool') {
      return `
        <button type="button" class="control-chip ${value ? 'control-chip--active' : ''}" data-config-toggle="${escapeHTML(path)}">
          <span class="dot" style="background:${value ? 'var(--accent)' : 'var(--text-sub)'}"></span>
          ${escapeHTML(value ? 'enabled' : 'disabled')}
        </button>
      `;
    }
    if (schema.kind === 'enum') {
      return `
        <select class="field-control" data-config-field="${escapeHTML(path)}">
          ${schema.options
            .map((option) => `<option value="${escapeHTML(option)}" ${option === value ? 'selected' : ''}>${escapeHTML(option)}</option>`)
            .join('')}
        </select>
      `;
    }
    if (schema.kind === 'multienum') {
      const set = new Set(value || []);
      return `
        <div class="modal-tabs">
          ${schema.options
            .map((option) => `
              <button type="button" class="modal-tab ${set.has(option) ? 'modal-tab--active' : ''}" data-config-multi="${escapeHTML(path)}" data-config-value="${escapeHTML(option)}">${escapeHTML(option)}</button>
            `)
            .join('')}
        </div>
      `;
    }
    if (schema.kind === 'number') {
      return `
        <input
          class="field-control"
          type="number"
          value="${escapeHTML(value)}"
          min="${schema.min != null ? escapeHTML(schema.min) : ''}"
          max="${schema.max != null ? escapeHTML(schema.max) : ''}"
          step="${schema.step != null ? escapeHTML(schema.step) : '1'}"
          data-config-field="${escapeHTML(path)}"
        >
      `;
    }
    return `
      <input
        class="field-control ${schema.kind === 'text' ? '' : ''}"
        type="text"
        value="${escapeHTML(value)}"
        data-config-field="${escapeHTML(path)}"
      >
    `;
  }

  function renderConfig() {
    const diffs = getConfigDiffs();
    const selectedPath = currentConfigSelection();
    const selectedSchema = state.configSchema[selectedPath];
    const selectedValue = getAt(state.config, selectedPath);
    const defaultValue = getAt(state.configDefault, selectedPath);
    const selectedError = validateField(selectedPath, selectedValue, selectedSchema);
    const restartDiffs = diffs.filter((diff) => diff.restart);

    const groupsHTML = configGroups()
      .map((group) => `
        <div class="config-group">
          <div class="config-group__title">${escapeHTML(group.title)}</div>
          <div class="config-list">
            ${group.paths
              .map((path) => {
                const schema = state.configSchema[path];
                const value = getAt(state.config, path);
                const changed = diffs.some((diff) => diff.path === path);
                const active = selectedPath === path;
                const error = validateField(path, value, schema);
                return `
                  <button
                    type="button"
                    class="config-row ${active ? 'config-row--active' : ''}"
                    data-config-select="${escapeHTML(path)}"
                  >
                    <div class="config-row__key">
                      <span class="config-row__name">${escapeHTML(path)}</span>
                      ${changed ? '<span class="badge badge--warn">!</span>' : ''}
                    </div>
                    <div class="config-row__value">${renderConfigValueSummary(path, schema, value)}</div>
                    <div class="config-row__meta">
                      ${schema && schema.restart ? '<span class="chip chip--warn">restart</span>' : ''}
                      ${error ? '<span class="chip chip--dim">error</span>' : ''}
                    </div>
                  </button>
                `;
              })
              .join('')}
          </div>
        </div>
      `)
      .join('');

    const detail = `
      <div class="config-detail">
        <div class="config-detail__head">
          <div>
            <div class="overlay__title" style="display:block;">field details</div>
            <div class="config-detail__title">${escapeHTML(selectedPath)}</div>
          </div>
          <div class="config-row__meta">
            ${selectedSchema && selectedSchema.restart ? '<span class="chip chip--warn">restart required</span>' : ''}
            ${selectedError ? '<span class="chip chip--err">invalid</span>' : ''}
            ${diffs.some((diff) => diff.path === selectedPath) ? '<span class="chip chip--accent">changed</span>' : ''}
          </div>
        </div>
        <div class="config-detail__body">
          ${restartDiffs.length ? `
            <div class="modal-banner">
              <span class="dot" style="background: var(--warn);"></span>
              Some pending changes require a runner restart. ${escapeHTML(restartDiffs.map((diff) => diff.path).join(', '))}
            </div>
          ` : ''}
          <div>
            <div class="detail-label">Description</div>
            <div class="detail-copy">${escapeHTML(selectedSchema ? selectedSchema.desc : '')}</div>
          </div>
          ${selectedSchema && selectedSchema.hint ? `
            <div>
              <div class="detail-label">Hint</div>
              <div class="summary-note">${escapeHTML(selectedSchema.hint)}</div>
            </div>
          ` : ''}
          <div>
            <div class="detail-label">Current value</div>
            <div>${configControl(selectedPath)}</div>
            ${selectedError ? `<div class="field-error" style="margin-top:6px;">${escapeHTML(selectedError)}</div>` : ''}
          </div>
          <div>
            <div class="detail-label">Default</div>
            <div class="field-diff">
              <div class="field-diff__from">${escapeHTML(JSON.stringify(defaultValue))}</div>
              <div class="field-diff__to">${escapeHTML(JSON.stringify(selectedValue))}</div>
            </div>
          </div>
        </div>
      </div>
    `;

    const body = `
      <div class="config-layout">
        <div>
          ${sectionNotice('config')}
          ${groupsHTML}
        </div>
        <div>${detail}</div>
      </div>
    `;

    return viewShell(
      'config',
      'Config',
      `Saved to browser storage | ${escapeHTML(diffs.length)} pending change${diffs.length === 1 ? '' : 's'}`,
      `
        ${button('Reset Config', 'reset-config', 'button--quiet')}
        ${button('Open Prompts', 'nav-prompts', 'button--quiet')}
      `,
      body
    );
  }

  function renderPrompts() {
    const selected = currentPrompt() || {
      file: 'No prompt selected',
      mode: 'template',
      scope: 'PM',
      source: '',
      updated: 'empty',
      summary: '',
      preview: '',
    };
    const overrides = state.prompts.filter((prompt) => prompt.mode === 'override').length;
    const preview = selected.preview;

    const body = `
      <div class="prompt-layout">
        <div class="prompt-list">
          ${panel(
            'Prompt pack',
            `${escapeHTML(overrides)}/${escapeHTML(state.prompts.length)} overrides`,
            `
              ${sectionNotice('prompts')}
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(state.config.prompts_dir)}</div>
                    <div class="compact-list__meta">Primary prompts directory</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(state.prompts.length)} tracked prompt files</div>
                    <div class="compact-list__meta">PM, Dev, QA, Reporter</div>
                  </div>
                </div>
              </div>
            `
          )}
          ${state.prompts.length ? state.prompts.map((prompt) => renderPromptCard(prompt)).join('') : '<div class="summary-note">No prompt files were discovered.</div>'}
        </div>

        <div class="prompt-preview">
          <div class="prompt-preview__head">
            <span class="badge ${selected.mode === 'override' ? 'badge--info' : 'badge--dim'}">${escapeHTML(selected.mode.toUpperCase())}</span>
            <div class="panel__title">${escapeHTML(selected.file)}</div>
            <div class="panel__meta">${escapeHTML(selected.scope)} | ${escapeHTML(selected.source)}</div>
          </div>
          <div class="prompt-preview__body">
            <div class="detail-label">Summary</div>
            <div class="detail-copy">${escapeHTML(selected.summary)}</div>
            <div class="detail-label">Preview</div>
            <pre class="prompt-preview__text">${escapeHTML(preview)}</pre>
            <div class="compact-list">
              <div class="compact-list__item">
                <span class="compact-list__bullet"></span>
                <div>
                  <div class="compact-list__body">${escapeHTML(selected.updated)}</div>
                  <div class="compact-list__meta">Last updated</div>
                </div>
              </div>
              <div class="compact-list__item">
                <span class="compact-list__bullet"></span>
                <div>
                  <div class="compact-list__body">${escapeHTML(selected.mode === 'override' ? 'Local override' : 'Template default')}</div>
                  <div class="compact-list__meta">How the run resolves this prompt</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    return viewShell(
      'prompts',
      'Prompts',
      `${escapeHTML(state.config.prompts_dir || 'prompts')} | selected ${escapeHTML(selected.file)}`,
      `
        ${button('Open Config', 'nav-config', 'button--quiet')}
        ${button('Copy prompt summary', 'copy-prompt-summary', 'button--quiet')}
      `,
      body
    );
  }

  function renderHistory() {
    const selected = currentRun();
    const totalTasks = state.runs.reduce((sum, run) => sum + run.tasksTotal, 0);
    const doneTasks = state.runs.reduce((sum, run) => sum + run.tasksDone, 0);
    const successes = state.runs.filter((run) => run.status === 'success').length;
    const budgetCap = toNumber(state.config?.budget?.max_usd || 0, 0);
    const historyWindow = state.runs.length ? `latest ${fmtRelative(state.runs[0].startedAt)}` : 'no runs yet';

    const body = `
      <div class="history-layout">
        <div>
          ${panel(
            'Run history',
            `${escapeHTML(state.runs.length)} runs | ${escapeHTML(historyWindow)}`,
            `
              ${sectionNotice('history')}
              <div class="kpi-grid kpi-grid--three">
                ${kpiCard('Success', `${successes}/${state.runs.length}`, 'successful runs', true)}
                ${kpiCard('Tasks', `${doneTasks}/${totalTasks}`, 'completed')}
                ${kpiCard('Budget cap', fmtMoney(budgetCap), 'config max_usd')}
              </div>
            `
          )}

            <div class="history-table">
              <div class="history-table__head">
                <span>Status</span>
                <span>Branch / ID</span>
                <span>Tasks</span>
                <span>Duration</span>
                <span>Started</span>
                <span style="text-align:right;">Action</span>
              </div>
              ${state.runs.length ? state.runs.map((run) => renderHistoryRow(run)).join('') : '<div class="summary-note" style="padding:14px;">No run history yet.</div>'}
            </div>
          </div>

        <div>
          ${panel(
            'Selected run',
            escapeHTML(selected ? selected.id : 'none'),
            `
              <div class="history-details">
                <div class="history-details__body">
                  <div class="kpi-grid kpi-grid--three">
                    ${kpiCard('Status', selected ? selected.status.toUpperCase() : 'EMPTY', 'current state', selected ? selected.status === 'success' : false)}
                    ${kpiCard('Tasks', selected ? `${selected.tasksDone}/${selected.tasksTotal}` : '0/0', 'done / total')}
                    ${kpiCard('Duration', selected ? fmtDuration(selected.durationSec) : '--', 'run length')}
                  </div>
                  <div class="compact-list">
                    <div class="compact-list__item">
                      <span class="compact-list__bullet"></span>
                      <div>
                        <div class="compact-list__body">${escapeHTML(selected ? selected.branch : 'none')}</div>
                        <div class="compact-list__meta">Branch</div>
                      </div>
                    </div>
                    <div class="compact-list__item">
                      <span class="compact-list__bullet"></span>
                      <div>
                        <div class="compact-list__body">${escapeHTML(selected ? selected.id : 'none')}</div>
                        <div class="compact-list__meta">Run ID</div>
                      </div>
                    </div>
                    <div class="compact-list__item">
                      <span class="compact-list__bullet"></span>
                      <div>
                        <div class="compact-list__body">${escapeHTML(selected ? fmtRelative(selected.startedAt) : 'no run yet')}</div>
                        <div class="compact-list__meta">Started</div>
                      </div>
                    </div>
                  </div>
                  <div class="summary-note">Use this view to compare recent runs, failure states, and stop reasons before resuming work.</div>
                </div>
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'history',
      'Run History',
      `${escapeHTML(state.runs.length)} runs | latest ${escapeHTML(state.runs[0] ? state.runs[0].id : 'none')}`,
      `
        ${button('Open Logs', 'nav-logs', 'button--quiet')}
        ${button('Open Dashboard', 'nav-dashboard', 'button--quiet')}
      `,
      body
    );
  }

  function renderNotifications() {
    const filters = ['all', 'task_done', 'task_failed', 'quota', 'error', 'stalled'];
    const filtered = state.notifications.filter((item) => state.notificationFilter === 'all' || item.kind === state.notificationFilter);

    const kindCounts = state.notifications.reduce((acc, item) => {
      acc[item.kind] = (acc[item.kind] || 0) + 1;
      return acc;
    }, {});

    const body = `
      <div class="notification-layout">
        <div>
          ${panel(
            'Event feed',
            `${escapeHTML(filtered.length)} visible`,
            `
              ${sectionNotice('notifications')}
              <div class="logs-toolbar">
                <div class="filters">
                  ${filters
                    .map((filter) => `
                      <button type="button" class="filter-chip ${state.notificationFilter === filter ? 'filter-chip--active' : ''}" data-notification-filter="${escapeHTML(filter)}">${escapeHTML(filter.toUpperCase())}</button>
                    `)
                    .join('')}
                </div>
              </div>
            `
          )}
          <div class="notification-feed">
            ${filtered.length ? filtered.map((item) => renderNotificationItem(item)).join('') : '<div class="summary-note" style="padding:14px;">No notifications yet.</div>'}
          </div>
        </div>

        <div class="view-grid">
          ${panel(
            'Telegram mirror',
            escapeHTML(state.config.telegram.instance_name || 'home-pc-main'),
            `
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(state.config.telegram.instance_name)}</div>
                    <div class="compact-list__meta">Connected instance</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">Allowed event kinds: run_start, run_stop, task_done, task_failed, quota, error, stalled</div>
                    <div class="compact-list__meta">Mirrored from the runner</div>
                  </div>
                </div>
              </div>
            `
          )}

          ${panel(
            'Notification counts',
            'current run',
            `
              <div class="kpi-grid kpi-grid--three">
                ${kpiCard('Task done', String(kindCounts.task_done || 0), 'success', true)}
                ${kpiCard('Quota', String(kindCounts.quota || 0), 'budget notices')}
                ${kpiCard('Errors', String((kindCounts.error || 0) + (kindCounts.task_failed || 0)), 'action needed')}
              </div>
            `
          )}

          ${panel(
            'Stalled detection',
            'read only',
            `
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">stalled_seconds = 600</div>
                    <div class="compact-list__meta">Fires when metrics.jsonl stops updating for 10 minutes.</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">Read-only first pass</div>
                    <div class="compact-list__meta">Notifications are surfaced without mutation controls.</div>
                  </div>
                </div>
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'notifications',
      'Notifications',
      `Cross-run event feed | ${escapeHTML(state.notifications.length)} items`,
      `
        ${button('Open Dashboard', 'nav-dashboard', 'button--quiet')}
        ${button('Open Worktree', 'nav-worktree', 'button--quiet')}
      `,
      body
    );
  }

  function renderWorktree() {
    const review = state.worktreeMerge;
    const isError = review.status === 'error';
    const reviewRequired = Boolean(review.reviewRequired || review.status === 'error' || (review.status && review.status !== 'none'));
    const canCopyPatch = Boolean(review.patchPath || review.patch);
    const detailRows = [
      { label: 'Status', value: review.status || 'none', meta: reviewRequired ? 'review required' : 'read only' },
      { label: 'Source repo', value: review.sourceRepo || '--', meta: 'repository root' },
      { label: 'Source branch', value: review.sourceBranch || review.branch || 'HEAD', meta: 'base branch for the patch' },
      { label: 'Base ref', value: review.baseRef || '--', meta: 'merge base' },
      { label: 'Head ref', value: review.headRef || '--', meta: 'worktree head' },
      { label: 'Run dir', value: review.runDir || state.latestRunDir || '--', meta: 'run that produced the patch' },
      { label: 'Worktree dir', value: review.worktreeDir || review.worktree || '--', meta: 'isolated source tree' },
      { label: 'Patch path', value: review.patchPath || review.patch || '--', meta: 'merge patch artifact' },
      { label: 'Pending file', value: review.pendingFile || '--', meta: 'read-only contract source' },
      { label: 'Runner rc', value: String(review.runnerRc ?? review.lastRc ?? 0), meta: 'export status' },
    ];
    const bannerTone = isError ? 'err' : reviewRequired ? 'warn' : 'info';
    const bannerTitle = isError ? 'Malformed pending file' : reviewRequired ? 'Review required before source-repo changes' : 'No pending worktree merge';
    const bannerCopy = isError
      ? review.reviewRequiredMessage || review.summary || 'Pending worktree merge file could not be parsed.'
      : reviewRequired
        ? review.reviewRequiredMessage || 'Review required before applying the patch to the source repository.'
        : review.reviewRequiredMessage || review.summary || 'No pending worktree merge.';
    const actionCopy = reviewRequired
      ? 'The web console is read-only. Apply or discard from the CLI only.'
      : 'No source-repo change is pending.';
    const copyPatchAttrs = canCopyPatch
      ? ''
      : 'disabled aria-disabled="true" title="No patch path is available yet."';
    const disabledMergeAttrs = reviewRequired
      ? 'disabled aria-disabled="true" title="Use /merge-worktree or /discard-worktree in the CLI."'
      : 'disabled aria-disabled="true" title="No pending merge is available."';

    const body = `
      <div class="review-layout">
        <div>
          ${panel(
            'Pending merge',
            `${escapeHTML(review.mode)} | ${escapeHTML(review.status || 'none')}`,
            `
              ${review.status === 'none' ? sectionNotice('worktree') : ''}
              ${reviewRequired ? `
                <div class="modal-banner section-banner section-banner--${bannerTone}" style="margin-bottom:12px;">
                  <span class="dot" style="background: currentColor;"></span>
                  <div>
                    <div class="section-banner__title">${escapeHTML(bannerTitle)}</div>
                    <div class="section-banner__copy">${escapeHTML(bannerCopy)}</div>
                  </div>
                </div>
              ` : ''}
              <div class="compact-list">
                ${detailRows
                  .map(
                    (item) => `
                      <div class="compact-list__item">
                        <span class="compact-list__bullet"></span>
                        <div>
                          <div class="compact-list__body">${escapeHTML(item.value)}</div>
              <div class="compact-list__meta">${escapeHTML(item.label)}${item.meta ? ` | ${escapeHTML(item.meta)}` : ''}</div>
                        </div>
                      </div>
                    `
                  )
                  .join('')}
              </div>
              <div class="summary-note" style="margin-top:12px;">${escapeHTML(review.summary || 'No pending worktree merge.')}</div>
              <div class="summary-note" style="margin-top:8px;">${escapeHTML(actionCopy)}</div>
            `
          )}

          ${panel(
            'Changed files',
            `${escapeHTML(review.changedFiles.length)} files`,
            `
              <div class="review-files">
                ${review.changedFiles
                  .map((file) => `
                    <div class="review-file">
                      <div class="review-file__path">${escapeHTML(file.path)}</div>
                      <div class="review-file__meta">${escapeHTML(file.kind)} | ${escapeHTML(file.note)}</div>
                    </div>
                  `)
                  .join('')}
              </div>
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            'Review checklist',
            reviewRequired ? 'manual only' : 'no pending file',
            `
              <div class="modal-banner section-banner section-banner--info">
                <span class="dot" style="background: currentColor;"></span>
                <div>
                  <div class="section-banner__title">${escapeHTML(reviewRequired ? 'CLI guidance only' : 'Read-only mode')}</div>
                  <div class="section-banner__copy">${escapeHTML(reviewRequired ? 'Use /merge-worktree or /discard-worktree in the CLI after review. The web console never applies or discards the source repository patch.' : 'No pending worktree merge is available in this snapshot.' )}</div>
                </div>
              </div>
              <div class="compact-list" style="margin-top:12px;">
                ${review.checklist.length ? review.checklist.map((item) => `
                  <div class="compact-list__item">
                    <span class="compact-list__bullet" style="background:${reviewRequired ? 'var(--warn)' : 'var(--accent)'}"></span>
                    <div>
                      <div class="compact-list__body">${escapeHTML(item)}</div>
                      <div class="compact-list__meta">${reviewRequired ? 'needs review' : 'informational'}</div>
                    </div>
                  </div>
                `).join('') : '<div class="summary-note">No checklist is available yet.</div>'}
              </div>
            `
          )}

          ${panel(
            'Merge actions',
            reviewRequired ? 'CLI guidance only' : 'read only',
            `
              ${reviewRequired ? `
                <div class="summary-note">${escapeHTML(actionCopy)}</div>
                <div class="modal-actions">
                  ${button('Apply merge', 'worktree-apply', 'button--primary', disabledMergeAttrs)}
                  ${button('Discard merge', 'worktree-discard', 'button--danger', disabledMergeAttrs)}
                </div>
              ` : `
                <div class="summary-note">No pending worktree merge is available.</div>
              `}
              <div class="modal-actions" style="margin-top:12px;">
                ${button('Copy patch path', 'copy-worktree-patch', 'button--quiet', copyPatchAttrs)}
              </div>
            `
          )}

          ${panel(
            'Risk notes',
            'read only',
            `
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(review.risk)}</div>
                    <div class="compact-list__meta">Review before merge</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">Manual merge mode only</div>
                    <div class="compact-list__meta">No destructive action buttons are exposed in this shell.</div>
                  </div>
                </div>
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'worktree',
      'Worktree Review',
      `${escapeHTML(review.mode)} | ${escapeHTML(review.status || 'none')}`,
      `
        ${button('Copy patch path', 'copy-worktree-patch', 'button--quiet', copyPatchAttrs)}
      `,
      body
    );
  }

  function renderLanding() {
    const repoLabel = currentRepoLabel();
    const commandLines = currentRunCommandPreviewLines();
    const body = `
      <div class="preview-layout">
        <div>
          ${panel(
            'Direction A landing preview',
            'marketing shell',
            `
              <div class="landing-card">
                <div class="landing-card__body">
                  <div class="landing-hero">
                    <div>
                      <div class="chip chip--accent">Direction A</div>
                      <h2 class="landing-title">Leave it running.<br>Wake up to a PR.</h2>
                      <div class="landing-copy">
                        CLI-first multi-agent runner with a PM -> Dev -> QA pipeline, local-safe worktree review, and a compact production shell.
                      </div>
                      <div class="landing-actions">
                        ${button('Open Dashboard', 'nav-dashboard', 'button--primary')}
                        ${button('Copy run command', 'copy-run-command', 'button--quiet')}
                      </div>
                    </div>
                    <div class="terminal-card">
                      <div class="terminal-card__head">
                        <div class="terminal-card__lights">
                          <span class="terminal-card__dot"></span>
                          <span class="terminal-card__dot"></span>
                          <span class="terminal-card__dot"></span>
                        </div>
                        <span>~/${escapeHTML(repoLabel)} | agentcli</span>
                      </div>
                      <div class="terminal-card__body">
                        ${commandLines.map((line, index) => `
                          <div class="terminal-line">
                            <span class="terminal-line__prompt">${index === 0 ? '$' : ''}</span>
                            <span class="terminal-line__text">${escapeHTML(line)}</span>
                          </div>
                        `).join('')}
                        <div class="terminal-line"><span class="terminal-line__prompt"></span><span class="terminal-line__text terminal-line__text--accent">${escapeHTML(`${runStatusLabel(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason)} | backend=${state.activeRun.backend} | stage=${state.activeRun.stage}`)}</span></div>
                        <div class="terminal-line"><span class="terminal-line__prompt"></span><span class="terminal-line__text">${escapeHTML(`PM -> Dev -> QA | quota ${metricText(state.activeRun.quotaAvailable, state.activeRun.quota.used, fmtPercent)} | budget ${metricText(state.activeRun.budgetAvailable, state.activeRun.budgetUsed, fmtPercent)}`)}</span></div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div class="landing-strip">
                <div class="landing-strip__item">
                  <div class="landing-strip__label">01</div>
                  <div class="landing-strip__title">PM -> Dev -> QA</div>
                  <div class="landing-strip__copy">Structured backlog emission and stage handoff with live run feedback.</div>
                </div>
                <div class="landing-strip__item">
                  <div class="landing-strip__label">02</div>
                  <div class="landing-strip__title">Read-only first pass</div>
                  <div class="landing-strip__copy">Status, logs, and review surfaces without destructive browser-side controls.</div>
                </div>
                <div class="landing-strip__item">
                  <div class="landing-strip__label">03</div>
                  <div class="landing-strip__title">Compact shell</div>
                  <div class="landing-strip__copy">Thin borders, tight density, and live-running accents aligned to Direction A.</div>
                </div>
              </div>
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            'Production notes',
            'web console',
            `
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">No Babel in browser, no React CDN, no docs/Design runtime imports.</div>
                    <div class="compact-list__meta">Static production asset</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">Top bar, 220px sidebar, and independent main scroll area remain intact.</div>
                    <div class="compact-list__meta">Desktop shell recovery</div>
                  </div>
                </div>
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'landing',
      'Landing preview',
      'Direction A marketing shell',
      `
        ${button('Open Dashboard', 'nav-dashboard', 'button--primary')}
        ${button('Open Mobile', 'nav-mobile', 'button--quiet')}
      `,
      body
    );
  }

  function renderMobile() {
    const latestNotifications = state.notifications.slice(0, 4);
    const body = `
      <div class="preview-layout">
        <div class="phone-frame">
          <div class="phone-frame__screen">
            <div class="phone-top">
              <span>${escapeHTML(fmtTime(state.activeRun.startedAt))}</span>
              <span>LTE | 94%</span>
            </div>
            <div class="phone-head">
              <div class="phone-head__row">
                <span class="dot dot--pulse"></span>
                <div class="phone-head__title">${escapeHTML(state.activeRun.repoLabel)}</div>
                <span class="status-chip" style="margin-left:auto;">${escapeHTML(state.activeRun.status)}</span>
              </div>
              <div class="summary-note" style="margin-top:4px;">${escapeHTML(state.activeRun.id)} | ${escapeHTML(fmtDuration(state.activeRun.elapsedSec))} elapsed</div>
            </div>
            <div class="phone-section">
              <div class="phone-section__title">Pipeline</div>
              <div class="phone-list">
                ${state.stages.length ? state.stages.map((stage) => `
                  <div class="phone-item">
                    <span class="${lifecycleStageIconClass(stage.status)}">${escapeHTML(lifecycleStageIconText(stage.status))}</span>
                    <div class="phone-item__body">
                      <div class="phone-item__title">${escapeHTML(stage.label)} | <span class="muted">${escapeHTML(stage.taskTitle || stage.title || 'Lifecycle record')}</span></div>
                      <div class="phone-item__meta">${escapeHTML([stage.status, stage.taskId || 'task unavailable', stage.attempt != null ? `attempt ${stage.attempt}` : 'attempt unavailable', stage.cycle != null ? `cycle ${stage.cycle}` : 'cycle unavailable'].join(' | '))}</div>
                      <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(stage.recentOutput, 120) || 'Recent output unavailable.')}</div>
                    </div>
                  </div>
                `).join('') : '<div class="summary-note">No lifecycle records were published yet.</div>'}
              </div>
            </div>
            <div class="phone-section" style="flex: 1 1 auto;">
              <div class="phone-section__title">Notifications</div>
              <div class="phone-list">
                ${latestNotifications.length ? latestNotifications.map((item) => `
                  <div class="phone-item">
                    <span class="dot" style="background:${kindColor(item.kind)}; margin-top:5px;"></span>
                    <div class="phone-item__body">
                      <div class="phone-item__title">${escapeHTML(item.text)}</div>
                      <div class="phone-item__meta">${escapeHTML(item.kind)} | ${escapeHTML(fmtRelative(item.t))}</div>
                    </div>
                  </div>
                `).join('') : '<div class="summary-note">No notifications yet.</div>'}
              </div>
            </div>
            <div class="phone-actions">
              <span class="chip">/status</span>
              <span class="chip">/detail</span>
              <span class="chip">/stop</span>
              <span class="chip">/tail</span>
            </div>
          </div>
        </div>

        <div class="view-grid">
          ${panel(
            'Mobile preview notes',
            'Telegram-style remote view',
            `
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">Compact remote status surface for run monitoring.</div>
                    <div class="compact-list__meta">Designed to stay readable at narrow widths</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">Mirrors the Direction A mobile mock without external runtime deps.</div>
                    <div class="compact-list__meta">Static preview shell</div>
                  </div>
                </div>
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'mobile',
      'Mobile preview',
      'Telegram-style status view',
      `
        ${button('Open Notifications', 'nav-notifications', 'button--quiet')}
        ${button('Open Dashboard', 'nav-dashboard', 'button--quiet')}
      `,
      body
    );
  }

  function renderMainView() {
    switch (state.activeView) {
      case 'dashboard':
        return renderDashboard();
      case 'pipeline':
        return renderPipeline();
      case 'logs':
        return renderLogs();
      case 'backlog':
        return renderBacklog();
      case 'goals':
        return renderGoals();
      case 'config':
        return renderConfig();
      case 'prompts':
        return renderPrompts();
      case 'history':
        return renderHistory();
      case 'notifications':
        return renderNotifications();
      case 'worktree':
        return renderWorktree();
      case 'landing':
        return renderLanding();
      case 'mobile':
        return renderMobile();
      default:
        return renderDashboard();
    }
  }

  function renderPaletteCommands() {
    const navCommands = Object.keys(VIEW_LABELS).map((view) => ({
      kind: 'nav',
      view,
      title: `Go to ${VIEW_LABELS[view]}`,
      shortcut: VIEW_SHORTCUTS[view],
    }));
    const actionCommands = [
      { kind: 'action', action: 'refresh-status', title: 'Refresh read-only snapshot', shortcut: 'refresh' },
      { kind: 'action', action: 'open-stop', title: 'Stop current run', shortcut: 'stop' },
      { kind: 'action', action: 'toggle-logs', title: state.logsPaused ? 'Resume live logs' : 'Pause live logs', shortcut: 'logs' },
      { kind: 'action', action: 'nav-worktree', title: 'Open Worktree Review', shortcut: 'worktree' },
      { kind: 'action', action: 'nav-mobile', title: 'Open Mobile preview', shortcut: 'mobile' },
      { kind: 'action', action: 'nav-landing', title: 'Open Landing preview', shortcut: 'landing' },
    ];
    return navCommands.concat(actionCommands);
  }

  function paletteMatches(command) {
    const query = state.paletteQuery.trim().toLowerCase();
    if (!query) return true;
    const haystack = [command.title, command.shortcut, command.kind, command.view || command.action]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return haystack.includes(query);
  }

  function renderPaletteOverlay() {
    const commands = renderPaletteCommands().filter(paletteMatches);
    const selectedIndex = Math.min(state.paletteIndex, Math.max(0, commands.length - 1));
    const listHTML = commands.length
      ? commands
          .map((command, index) => `
            <button
              type="button"
              class="palette-item ${index === selectedIndex ? 'palette-item--active' : ''}"
              data-palette-index="${index}"
            >
              <span class="palette-item__kind">${escapeHTML(command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">none</span><span class="palette-item__title">No matching commands</span><span class="palette-item__shortcut"></span></div>`;

    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="palette">
        <div class="overlay__panel overlay__panel--palette">
          <div class="overlay__head">
            <span class="overlay__title">Command palette</span>
            <span class="overlay__sub">/ or Cmd+K / Ctrl+K</span>
          </div>
          <div class="overlay__body">
            <input
              type="text"
              class="palette-input"
              placeholder="Type a screen or action"
              value="${escapeHTML(state.paletteQuery)}"
              data-palette-input
              autocomplete="off"
              spellcheck="false"
            >
            <div class="palette-list" data-palette-list>
              ${listHTML}
            </div>
          </div>
        </div>
      </div>
    `;

    const input = overlayRoot().querySelector('[data-palette-input]');
    if (input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }

  function renderGoalEditorOverlay() {
    const editor = state.goalEditor;
    if (!editor) {
      overlayRoot().innerHTML = '';
      return;
    }
    const { draft, mode } = editor;
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="goal-editor">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(mode === 'edit' ? 'Edit goal' : 'New goal')}</span>
            <span class="overlay__sub">local only / esc closes</span>
          </div>
          <div class="overlay__body">
            <div class="modal-grid">
              <div class="modal-field">
                <div class="modal-field__label">Bucket</div>
                <div class="modal-tabs">
                  <button type="button" class="modal-tab ${draft.bucket === 'p0' ? 'modal-tab--active' : ''}" data-goal-bucket="p0">P0 | Must-have</button>
                  <button type="button" class="modal-tab ${draft.bucket === 'p1' ? 'modal-tab--active' : ''}" data-goal-bucket="p1">P1 | Should-have</button>
                </div>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">Goal</div>
                <textarea class="field-control field-control--textarea" rows="2" data-goal-field="text">${escapeHTML(draft.text)}</textarea>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">Note</div>
                <textarea class="field-control field-control--textarea" rows="3" data-goal-field="note">${escapeHTML(draft.note || '')}</textarea>
              </div>
              ${editor.error ? `<div class="field-error">${escapeHTML(editor.error)}</div>` : '<div class="modal-copy">Changes stay in browser storage until the save workflow lands.</div>'}
              <div class="modal-actions">
                <button type="button" class="button button--quiet" data-goal-close>Cancel</button>
                <button type="button" class="button button--primary" data-goal-save>Save goal</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderStopOverlay() {
    const action = state.stopAction || 'stop';
    const control = state.runnerControl;
    const confirmation = runnerControlConfirmationPhrase(action);
    const confirmationValue = state.stopConfirmation.trim();
    const actionEnabled = runnerControlActionEnabled(action);
    const confirmEnabled = actionEnabled && confirmationValue === confirmation && !state.stopSubmitting;
    const bannerTone =
      state.stopSubmitting || state.stopError
        ? 'err'
        : !actionEnabled
          ? 'warn'
          : control.status.running
            ? 'running'
            : 'info';
    const actionTitle = runnerControlModalTitle(action);
    const actionSummary = runnerControlActionSummary(action);
    const actionLabel = runnerControlActionLabel(action, state.stopSubmitting);
    const subLabel = !control.enabled ? 'controls disabled' : actionEnabled ? 'type the phrase to continue' : 'action unavailable';
    const details = [
      { label: 'Source', value: control.source || 'unknown' },
      { label: 'Mode', value: control.status.runnerMode || 'unknown' },
      { label: 'Status', value: control.runStatus || (control.status.running ? 'running' : 'idle') },
      { label: 'Controller', value: control.controllerAvailable ? 'available' : 'unavailable' },
    ];
    const detailHTML = details
      .map(
        (item) => `
          <div class="runner-control__detail">
            <div class="runner-control__label">${escapeHTML(item.label)}</div>
            <div class="runner-control__value">${escapeHTML(item.value)}</div>
          </div>
        `
      )
      .join('');
    const bannerMessage = state.stopError || (!actionEnabled ? runnerControlActionDisabledReason(action) : control.message) || actionSummary;
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="stop">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(actionTitle)}</span>
            <span class="overlay__sub">${escapeHTML(subLabel)}${state.stopSubmitting ? ' / working...' : ''}</span>
          </div>
          <div class="overlay__body">
            <div class="modal-banner section-banner section-banner--${bannerTone}">
              <span class="dot" style="background: currentColor;"></span>
              <div>
                <div class="section-banner__title">${escapeHTML(state.stopError ? 'Action failed' : actionEnabled ? 'Confirmation required' : 'Action disabled')}</div>
                <div class="section-banner__copy">${escapeHTML(bannerMessage)}</div>
              </div>
            </div>
            <div style="margin-top:12px;" class="detail-copy">
              ${escapeHTML(actionSummary)}
            </div>
            <div class="runner-control__details" style="margin-top:12px;">
              ${detailHTML}
            </div>
            <div class="modal-field" style="margin-top:12px;">
              <div class="modal-field__label">Confirmation phrase</div>
              <input
                type="text"
                class="field-control"
                data-stop-confirmation
                value="${escapeHTML(state.stopConfirmation)}"
                placeholder="${escapeHTML(confirmation)}"
                autocomplete="off"
                spellcheck="false"
                ${state.stopSubmitting || !actionEnabled ? 'disabled' : ''}
              >
            </div>
            <div class="summary-note" style="margin-top:10px;">
              Type <span class="mono">${escapeHTML(confirmation)}</span> exactly to enable the ${escapeHTML(actionLabel.toLowerCase())} action.
            </div>
            ${state.stopError ? `<div class="field-error" style="margin-top:10px;">${escapeHTML(state.stopError)}</div>` : ''}
            <div class="modal-actions" style="margin-top:16px;">
              <button type="button" class="button button--quiet" data-stop-close ${state.stopSubmitting ? 'disabled' : ''}>Cancel</button>
              <button type="button" class="button ${action === 'stop' ? 'button--danger' : action === 'start' ? 'button--primary' : 'button--quiet'}" data-stop-confirm ${confirmEnabled ? '' : 'disabled'}>${escapeHTML(actionLabel)}</button>
            </div>
          </div>
        </div>
      </div>
    `;
    const input = overlayRoot().querySelector('[data-stop-confirmation]');
    if (input && !state.stopSubmitting) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }

  function renderOverlay() {
    if (state.paletteOpen) {
      renderPaletteOverlay();
      return;
    }
    if (state.goalEditor) {
      renderGoalEditorOverlay();
      return;
    }
    if (state.stopOpen) {
      renderStopOverlay();
      return;
    }
    overlayRoot().innerHTML = '';
  }

  function scrollLogTail() {
    const feed = mainRoot().querySelector('[data-log-scroll]');
    if (feed) {
      feed.scrollTop = feed.scrollHeight;
    }
  }

  function renderShell(options = {}) {
    if (state.paletteOpen || state.goalEditor || state.stopOpen) {
      return;
    }

    const main = mainRoot();
    const previousScroll = options.preserveScroll ? main.scrollTop : 0;

    topbarRoot().innerHTML = renderTopbar();
    sidebarRoot().innerHTML = renderSidebar();
    main.innerHTML = renderMainView();
    main.dataset.view = state.activeView;

    if (state.activeView === 'logs' && !state.logsPaused && options.scrollToBottom) {
      scrollLogTail();
    } else {
      main.scrollTop = previousScroll;
    }

    document.title = `AgentCLI Web Console | ${VIEW_LABELS[state.activeView]}`;
    writeJSON(STORAGE.view, state.activeView);
    renderOverlay();
  }

  function openPalette() {
    state.paletteOpen = true;
    state.paletteQuery = '';
    state.paletteIndex = 0;
    state.stopOpen = false;
    state.goalEditor = null;
    renderOverlay();
  }

  function closePalette() {
    if (!state.paletteOpen) return;
    state.paletteOpen = false;
    renderOverlay();
    renderShell({ preserveScroll: true });
  }

  function openGoalEditor(bucket, index) {
    const source = clone(state.goals[bucket][index]);
    state.goalEditor = {
      mode: 'edit',
      bucket,
      index,
      draft: {
        bucket,
        text: source.text || '',
        note: source.note || '',
      },
      error: '',
    };
    state.paletteOpen = false;
    state.stopOpen = false;
    renderOverlay();
  }

  function openNewGoal(bucket) {
    state.goalEditor = {
      mode: 'new',
      bucket,
      index: -1,
      draft: {
        bucket,
        text: '',
        note: '',
      },
      error: '',
    };
    state.paletteOpen = false;
    state.stopOpen = false;
    renderOverlay();
  }

  function closeGoalEditor() {
    if (!state.goalEditor) return;
    state.goalEditor = null;
    renderOverlay();
    renderShell({ preserveScroll: true });
  }

  function saveGoalEditor() {
    if (!state.goalEditor) return;
    const { mode, bucket, index, draft } = state.goalEditor;
    const text = String(draft.text || '').trim();
    if (!text) {
      state.goalEditor.error = 'Goal text cannot be empty.';
      renderGoalEditorOverlay();
      return;
    }

    const sourceGoal = mode === 'edit' && index >= 0 ? clone(state.goals[bucket][index] || {}) : null;
    const nextGoal = sourceGoal
      ? {
          ...sourceGoal,
          text,
          note: String(draft.note || '').trim(),
          done: Boolean(sourceGoal.done),
          checked: Boolean(sourceGoal.checked ?? sourceGoal.done),
          checkbox: toText(sourceGoal.checkbox, Boolean(sourceGoal.done) ? '[x]' : '[ ]'),
        }
      : {
          done: false,
          checked: false,
          checkbox: '[ ]',
          text,
          note: String(draft.note || '').trim(),
        };

    const nextGoals = clone(state.goals);
    if (mode === 'new' || index < 0) {
      nextGoals[draft.bucket].push(nextGoal);
    } else if (draft.bucket !== bucket) {
      nextGoals[bucket].splice(index, 1);
      nextGoals[draft.bucket].push(nextGoal);
    } else {
      nextGoals[bucket][index] = { ...nextGoals[bucket][index], text, note: String(draft.note || '').trim() };
    }

    state.goals = nextGoals;
    writeJSON(STORAGE.goals, state.goals);
    state.goalsDirty = true;
    state.goalEditor = null;
    renderShell({ preserveScroll: true });
  }

  function updateGoal(bucket, index, patch) {
    const next = clone(state.goals);
    const current = next[bucket][index] || {};
    const nextItem = { ...current, ...patch };
    if (Object.prototype.hasOwnProperty.call(patch, 'done')) {
      const done = Boolean(patch.done);
      nextItem.done = done;
      nextItem.checked = done;
      nextItem.checkbox = done ? '[x]' : '[ ]';
    } else if (Object.prototype.hasOwnProperty.call(patch, 'checked')) {
      const checked = Boolean(patch.checked);
      nextItem.checked = checked;
      nextItem.done = checked;
      nextItem.checkbox = checked ? '[x]' : '[ ]';
    } else if (Object.prototype.hasOwnProperty.call(patch, 'checkbox')) {
      const checkbox = String(patch.checkbox || '').toLowerCase();
      const checked = checkbox.includes('x');
      nextItem.checkbox = checked ? '[x]' : '[ ]';
      nextItem.done = checked;
      nextItem.checked = checked;
    }
    next[bucket][index] = nextItem;
    state.goals = next;
    writeJSON(STORAGE.goals, state.goals);
    state.goalsDirty = true;
    renderShell({ preserveScroll: true });
  }

  function moveGoal(bucket, index) {
    const target = bucket === 'p0' ? 'p1' : 'p0';
    const next = clone(state.goals);
    const item = next[bucket].splice(index, 1)[0];
    next[target].push(item);
    state.goals = next;
    writeJSON(STORAGE.goals, state.goals);
    state.goalsDirty = true;
    renderShell({ preserveScroll: true });
  }

  function deleteGoal(bucket, index) {
    const next = clone(state.goals);
    next[bucket].splice(index, 1);
    state.goals = next;
    writeJSON(STORAGE.goals, state.goals);
    state.goalsDirty = true;
    renderShell({ preserveScroll: true });
  }

  function resetGoals() {
    state.goals = clone(defaults.goals);
    writeJSON(STORAGE.goals, state.goals);
    state.goalsDirty = true;
    renderShell({ preserveScroll: true });
  }

  function resetConfig() {
    state.config = deepMerge(clone(defaults.config), null);
    writeJSON(STORAGE.config, state.config);
    renderShell({ preserveScroll: true });
  }

  function setView(view) {
    const next = normalizeView(view);
    if (next === state.activeView) {
      return;
    }
    state.activeView = next;
    state.paletteOpen = false;
    state.stopOpen = false;
    state.goalEditor = null;
    if (history.replaceState) {
      history.replaceState(null, '', `#${next}`);
    } else {
      location.hash = next;
    }
    renderShell({ preserveScroll: false });
  }

  function selectConfigPath(path) {
    if (!state.configSchema[path]) return;
    state.configSelection = path;
    renderShell({ preserveScroll: true });
  }

  function setConfigValue(path, value) {
    const next = setAt(state.config, path, value);
    state.config = next;
    writeJSON(STORAGE.config, state.config);
    renderShell({ preserveScroll: true });
  }

  function toggleWorktreeReviewed() {
    state.reviewedWorktree = !state.reviewedWorktree;
    writeJSON(STORAGE.worktree, { reviewed: state.reviewedWorktree });
    if (state.reviewedWorktree) {
      state.notifications.unshift({
        t: nowMs(),
        kind: 'task_done',
        text: 'Worktree review marked complete locally.',
        run: state.activeRun.id,
      });
      state.notifications = state.notifications.slice(0, 12);
    }
    renderShell({ preserveScroll: true });
  }

  function stopRun() {
    state.activeRun.status = 'stopped';
    state.activeRun.stage = 'Dev';
    state.logsPaused = true;
    state.notifications.unshift({
      t: nowMs(),
      kind: 'run_stop',
      text: 'Local stop confirmed. UI switched to stopped state.',
      run: state.activeRun.id,
    });
    state.notifications = state.notifications.slice(0, 12);
    state.logs.push({
      t: fmtClock(nowMs()),
      lvl: 'warn',
      stage: 'Dev',
      msg: 'local stop requested and confirmed in web console',
    });
    state.logs = state.logs.slice(-72);
    state.stopOpen = false;
    renderShell({ preserveScroll: true });
  }

  function openNavByAction(action) {
    const map = {
      'nav-dashboard': 'dashboard',
      'nav-pipeline': 'pipeline',
      'nav-logs': 'logs',
      'nav-backlog': 'backlog',
      'nav-goals': 'goals',
      'nav-config': 'config',
      'nav-prompts': 'prompts',
      'nav-history': 'history',
      'nav-notifications': 'notifications',
      'nav-worktree': 'worktree',
      'nav-landing': 'landing',
      'nav-mobile': 'mobile',
    };
    const view = map[action];
    if (view) {
      setView(view);
    }
  }

  function handlePaletteSelection(index) {
    const commands = renderPaletteCommands().filter(paletteMatches);
    const command = commands[index];
    if (!command) return;
    if (command.kind === 'nav') {
      closePalette();
      setView(command.view);
      return;
    }
    if (command.kind === 'action') {
      closePalette();
      handleAction(command.action, null);
    }
  }

  function renderPaletteList() {
    const commands = renderPaletteCommands().filter(paletteMatches);
    const selectedIndex = Math.min(state.paletteIndex, Math.max(0, commands.length - 1));
    const list = overlayRoot().querySelector('[data-palette-list]');
    if (!list) return;
    list.innerHTML = commands.length
      ? commands
          .map((command, index) => `
            <button
              type="button"
              class="palette-item ${index === selectedIndex ? 'palette-item--active' : ''}"
              data-palette-index="${index}"
            >
              <span class="palette-item__kind">${escapeHTML(command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">none</span><span class="palette-item__title">No matching commands</span><span class="palette-item__shortcut"></span></div>`;
  }

  function handleAction(action, target) {
    switch (action) {
      case 'open-palette':
        openPalette();
        return;
      case 'open-stop':
        openStopModal('stop');
        return;
      case 'runner-start':
        openStopModal('start');
        return;
      case 'runner-stop':
        openStopModal('stop');
        return;
      case 'runner-reload':
        openStopModal('reload');
        return;
      case 'runner-restart':
        openStopModal('restart');
        return;
      case 'refresh-status':
        refreshSnapshot({ allowFallback: true });
        return;
      case 'toggle-logs':
        if (state.serverMode) {
          renderShell({ preserveScroll: true });
          return;
        }
        state.logsPaused = !state.logsPaused;
        if (state.sourceMode === 'fallback') {
          if (state.logsPaused) {
            stopLiveLogStream();
          } else {
            startFallbackLogStream();
          }
        }
        renderShell({ preserveScroll: true });
        return;
      case 'nav-dashboard':
      case 'nav-pipeline':
      case 'nav-logs':
      case 'nav-backlog':
      case 'nav-goals':
      case 'nav-config':
      case 'nav-prompts':
      case 'nav-history':
      case 'nav-notifications':
      case 'nav-worktree':
      case 'nav-landing':
      case 'nav-mobile':
        openNavByAction(action);
        return;
      case 'goal-add-p0':
        openNewGoal('p0');
        return;
      case 'goal-add-p1':
        openNewGoal('p1');
        return;
      case 'goal-save':
        saveGoalEditor();
        return;
      case 'goal-close':
        closeGoalEditor();
        return;
      case 'goal-bucket':
        if (state.goalEditor && target) {
          state.goalEditor.draft.bucket = target.dataset.goalBucket;
          renderGoalEditorOverlay();
        }
        return;
      case 'reset-goals':
        resetGoals();
        return;
      case 'reset-config':
        resetConfig();
        return;
      case 'toggle-worktree-reviewed':
        toggleWorktreeReviewed();
        return;
      case 'copy-worktree-patch':
        copyText(state.worktreeMerge.patchPath || state.worktreeMerge.patch);
        return;
      case 'copy-run-command':
        copyText(currentRunCommand());
        return;
      case 'copy-prompt-summary':
        copyText(`${currentPrompt().file} | ${currentPrompt().summary}`);
        return;
      default:
        return;
    }
  }

  function setActiveLogFilter(filter) {
    state.logFilter = filter;
    renderShell({ preserveScroll: true });
  }

  function setNotificationFilter(filter) {
    state.notificationFilter = filter;
    renderShell({ preserveScroll: true });
  }

  function setHistorySelection(id) {
    state.historySelection = id;
    renderShell({ preserveScroll: true });
  }

  function setPromptSelection(id) {
    state.promptSelection = id;
    renderShell({ preserveScroll: true });
  }

  function setBacklogSelection(id) {
    state.backlogSelection = id;
    renderShell({ preserveScroll: true });
  }

  function createModel() {
    return createFallbackFixture();
  }

  const defaults = createBlankModel();
  const fallbackFixture = createFallbackFixture();
  const storedGoals = readJSON(STORAGE.goals, null);

  const state = {
    ok: clone(defaults.ok),
    sourceMode: defaults.sourceMode,
    snapshotStatus: defaults.snapshotStatus,
    snapshotLabel: defaults.snapshotLabel,
    lastSnapshotAt: defaults.lastSnapshotAt,
    latestRunDir: defaults.latestRunDir,
    repo: clone(defaults.repo),
    activeRun: clone(defaults.activeRun),
    stages: clone(defaults.stages),
    backlog: clone(defaults.backlog),
    backlogCounts: clone(defaults.backlogCounts),
    backlogSelectedId: defaults.backlogSelectedId,
    goals: deepMerge(clone(defaults.goals), storedGoals),
    goalsSnapshot: clone(defaults.goalsSnapshot),
    goalsMeta: clone(defaults.goalsMeta),
    goalsPath: defaults.goalsPath,
    goalsCompletion: clone(defaults.goalsCompletion),
    goalsDirty: storedGoals != null,
    history: clone(defaults.history),
    runs: clone(defaults.history),
    historySummary: clone(defaults.historySummary),
    metrics: clone(defaults.metrics),
    logs: clone(defaults.logs),
    logTail: defaults.logTail,
    logFiles: clone(defaults.logFiles),
    notifications: clone(defaults.notifications),
    configDefault: clone(defaults.configDefault),
    config: deepMerge(clone(defaults.config), readJSON(STORAGE.config, null)),
    configMeta: clone(defaults.configMeta),
    configSchema: clone(defaults.configSchema),
    prompts: clone(defaults.prompts),
    promptsDir: defaults.config.prompts_dir,
    worktreeMerge: clone(defaults.worktreeMerge),
    runnerControl: clone(defaults.runnerControl),
    progress: clone(defaults.progress),
    sectionState: clone(defaults.sectionState),
    activeView: normalizeView(location.hash.replace(/^#/, '') || readJSON(STORAGE.view, null) || 'dashboard'),
    paletteOpen: false,
    paletteQuery: '',
    paletteIndex: 0,
    stopOpen: false,
    stopAction: 'stop',
    stopConfirmation: '',
    stopError: '',
    stopSubmitting: false,
    goalEditor: null,
    logsPaused: true,
    logFilter: 'all',
    notificationFilter: 'all',
    configSelection: 'repo',
    backlogSelection: defaults.backlogSelectedId,
    historySelection: defaults.history[0]?.id || '',
    promptSelection: defaults.prompts[0]?.id || '',
    reviewedWorktree: Boolean(readJSON(STORAGE.worktree, null)?.reviewed),
    serverMode: false,
    liveLogTimer: null,
    liveLogTick: 0,
    pollTimer: null,
    lastSnapshotSignature: '',
    fallbackFixture,
  };

  const APP_BOOTSTRAP = `
    <div class="topbar" id="topbar"></div>
    <aside class="sidebar" id="sidebar"><div class="sidebar__inner"></div></aside>
    <main class="main" id="main"></main>
    <div class="overlay-root" id="overlay-root" aria-live="polite"></div>
  `;

  ROOT.innerHTML = APP_BOOTSTRAP;

  function paletteMatches(command) {
    const query = state.paletteQuery.trim().toLowerCase();
    if (!query) return true;
    const haystack = [command.title, command.shortcut, command.kind, command.view, command.action]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return haystack.includes(query);
  }

  function renderPaletteCommands() {
    const navCommands = Object.keys(VIEW_LABELS).map((view) => ({
      kind: 'nav',
      view,
      title: `Go to ${VIEW_LABELS[view]}`,
      shortcut: VIEW_SHORTCUTS[view],
    }));
    const actionCommands = [
      { kind: 'action', action: 'refresh-status', title: 'Refresh read-only snapshot', shortcut: 'refresh' },
      { kind: 'action', action: 'open-stop', title: 'Stop current run', shortcut: 'stop' },
      { kind: 'action', action: 'runner-start', title: 'Start runner', shortcut: 'start' },
      { kind: 'action', action: 'runner-stop', title: 'Stop runner', shortcut: 'stop' },
      { kind: 'action', action: 'runner-reload', title: 'Reload runner', shortcut: 'reload' },
      { kind: 'action', action: 'runner-restart', title: 'Restart runner', shortcut: 'restart' },
      { kind: 'action', action: 'toggle-logs', title: state.logsPaused ? 'Resume live logs' : 'Pause live logs', shortcut: 'logs' },
      { kind: 'action', action: 'nav-worktree', title: 'Open Worktree Review', shortcut: 'worktree' },
      { kind: 'action', action: 'nav-mobile', title: 'Open Mobile preview', shortcut: 'mobile' },
      { kind: 'action', action: 'nav-landing', title: 'Open Landing preview', shortcut: 'landing' },
    ];
    return navCommands.concat(actionCommands);
  }

  function renderPaletteOverlay() {
    const commands = renderPaletteCommands().filter(paletteMatches);
    const selectedIndex = Math.min(state.paletteIndex, Math.max(0, commands.length - 1));
    const listHTML = commands.length
      ? commands
          .map((command, index) => `
            <button
              type="button"
              class="palette-item ${index === selectedIndex ? 'palette-item--active' : ''}"
              data-palette-index="${index}"
            >
              <span class="palette-item__kind">${escapeHTML(command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">none</span><span class="palette-item__title">No matching commands</span><span class="palette-item__shortcut"></span></div>`;

    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="palette">
        <div class="overlay__panel overlay__panel--palette">
          <div class="overlay__head">
            <span class="overlay__title">Command palette</span>
            <span class="overlay__sub">/ or Cmd+K / Ctrl+K</span>
          </div>
          <div class="overlay__body">
            <input
              type="text"
              class="palette-input"
              placeholder="Type a screen or action"
              value="${escapeHTML(state.paletteQuery)}"
              data-palette-input
              autocomplete="off"
              spellcheck="false"
            >
            <div class="palette-list" data-palette-list>
              ${listHTML}
            </div>
          </div>
        </div>
      </div>
    `;

    const input = overlayRoot().querySelector('[data-palette-input]');
    if (input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }

  function renderGoalEditorOverlay() {
    const editor = state.goalEditor;
    if (!editor) {
      overlayRoot().innerHTML = '';
      return;
    }
    const { draft, mode } = editor;
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="goal-editor">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(mode === 'edit' ? 'Edit goal' : 'New goal')}</span>
            <span class="overlay__sub">local only / esc closes</span>
          </div>
          <div class="overlay__body">
            <div class="modal-grid">
              <div class="modal-field">
                <div class="modal-field__label">Bucket</div>
                <div class="modal-tabs">
                  <button type="button" class="modal-tab ${draft.bucket === 'p0' ? 'modal-tab--active' : ''}" data-goal-bucket="p0">P0 | Must-have</button>
                  <button type="button" class="modal-tab ${draft.bucket === 'p1' ? 'modal-tab--active' : ''}" data-goal-bucket="p1">P1 | Should-have</button>
                </div>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">Goal</div>
                <textarea class="field-control field-control--textarea" rows="2" data-goal-field="text">${escapeHTML(draft.text)}</textarea>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">Note</div>
                <textarea class="field-control field-control--textarea" rows="3" data-goal-field="note">${escapeHTML(draft.note || '')}</textarea>
              </div>
              ${editor.error ? `<div class="field-error">${escapeHTML(editor.error)}</div>` : '<div class="modal-copy">Changes stay in browser storage until the save workflow lands.</div>'}
              <div class="modal-actions">
                <button type="button" class="button button--quiet" data-goal-close>Cancel</button>
                <button type="button" class="button button--primary" data-goal-save>Save goal</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderOverlay() {
    if (state.paletteOpen) {
      renderPaletteOverlay();
      return;
    }
    if (state.goalEditor) {
      renderGoalEditorOverlay();
      return;
    }
    if (state.stopOpen) {
      renderStopOverlay();
      return;
    }
    overlayRoot().innerHTML = '';
  }

  function renderShell(options = {}) {
    if (state.paletteOpen || state.goalEditor || state.stopOpen) {
      return;
    }
    const main = mainRoot();
    const preserveScroll = Boolean(options.preserveScroll);
    const previousScroll = preserveScroll ? main.scrollTop : 0;

    topbarRoot().innerHTML = renderTopbar();
    sidebarRoot().innerHTML = renderSidebar();
    main.innerHTML = renderMainView();
    main.dataset.view = state.activeView;

    if (state.activeView === 'logs' && !state.logsPaused && options.scrollToBottom) {
      scrollLogTail();
    } else {
      main.scrollTop = previousScroll;
    }

    document.title = `AgentCLI Web Console | ${VIEW_LABELS[state.activeView]}`;
    writeJSON(STORAGE.view, state.activeView);
    renderOverlay();
  }

  function renderPaletteList() {
    const list = overlayRoot().querySelector('[data-palette-list]');
    if (!list) return;
    const commands = renderPaletteCommands().filter(paletteMatches);
    const selectedIndex = Math.min(state.paletteIndex, Math.max(0, commands.length - 1));
    list.innerHTML = commands.length
      ? commands
          .map((command, index) => `
            <button
              type="button"
              class="palette-item ${index === selectedIndex ? 'palette-item--active' : ''}"
              data-palette-index="${index}"
            >
              <span class="palette-item__kind">${escapeHTML(command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">none</span><span class="palette-item__title">No matching commands</span><span class="palette-item__shortcut"></span></div>`;
  }

  function scrollLogTail() {
    const feed = mainRoot().querySelector('[data-log-scroll]');
    if (feed) {
      feed.scrollTop = feed.scrollHeight;
    }
  }

  function setActiveLogFilter(filter) {
    state.logFilter = filter;
    renderShell({ preserveScroll: true });
  }

  function setNotificationFilter(filter) {
    state.notificationFilter = filter;
    renderShell({ preserveScroll: true });
  }

  function setHistorySelection(id) {
    state.historySelection = id;
    renderShell({ preserveScroll: true });
  }

  function setPromptSelection(id) {
    state.promptSelection = id;
    renderShell({ preserveScroll: true });
  }

  function setBacklogSelection(id) {
    state.backlogSelection = id;
    renderShell({ preserveScroll: true });
  }

  function updateConfigPath(path, rawValue) {
    const schema = state.configSchema[path];
    if (!schema) return;
    let value = rawValue;
    if (schema.kind === 'number') {
      value = rawValue === '' ? '' : Number(rawValue);
    } else if (schema.kind === 'bool') {
      value = Boolean(rawValue);
    }
    state.config = setAt(state.config, path, value);
    writeJSON(STORAGE.config, state.config);
    renderShell({ preserveScroll: true });
  }

  function toggleConfigBool(path) {
    const current = Boolean(getAt(state.config, path));
    state.config = setAt(state.config, path, !current);
    writeJSON(STORAGE.config, state.config);
    renderShell({ preserveScroll: true });
  }

  function toggleConfigMulti(path, value) {
    const current = Array.isArray(getAt(state.config, path)) ? getAt(state.config, path).slice() : [];
    const set = new Set(current);
    if (set.has(value)) {
      set.delete(value);
    } else {
      set.add(value);
    }
    state.config = setAt(state.config, path, Array.from(set));
    writeJSON(STORAGE.config, state.config);
    renderShell({ preserveScroll: true });
  }

  async function applyStop() {
    const action = state.stopAction || 'stop';
    const confirmation = runnerControlConfirmationPhrase(action);
    const provided = state.stopConfirmation.trim();
    if (!runnerControlActionEnabled(action)) {
      state.stopError = runnerControlActionDisabledReason(action) || 'Runner control is disabled.';
      renderStopOverlay();
      return;
    }
    if (!provided) {
      state.stopError = `Type "${confirmation}" to confirm.`;
      renderStopOverlay();
      return;
    }
    if (provided !== confirmation) {
      state.stopError = `Confirmation phrase must be "${confirmation}".`;
      renderStopOverlay();
      return;
    }

    state.stopSubmitting = true;
    state.stopError = '';
    renderStopOverlay();

    try {
      const response = await fetch(runnerControlRequestPath(action), {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ confirmation: provided }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      const normalized = toObject(payload);
      if (!response.ok || normalized.ok === false) {
        const message = toText(normalized.message || toObject(normalized.error).message || `Runner control failed (HTTP ${response.status}).`, 'Runner control failed.');
        const error = new Error(message);
        const snapshot = toObject(normalized.snapshot);
        if (Object.keys(snapshot).length) {
          error.snapshot = snapshot;
        }
        throw error;
      }

      const snapshot = toObject(normalized.snapshot);
      if (Object.keys(snapshot).length) {
        applyServerSnapshot(snapshot);
      } else {
        await refreshSnapshot({ silent: true });
      }
      state.stopOpen = false;
      state.stopSubmitting = false;
      state.stopConfirmation = '';
      state.stopError = '';
      renderShell({ preserveScroll: true });
    } catch (error) {
      const message = toText(error?.message || error, 'Runner control failed.');
      state.stopSubmitting = false;
      state.stopError = message;
      const snapshot = toObject(error?.snapshot);
      if (Object.keys(snapshot).length) {
        applyServerSnapshot(snapshot);
      }
      renderStopOverlay();
      renderShell({ preserveScroll: true });
    }
  }

  function toggleReviewedWorktree() {
    state.reviewedWorktree = !state.reviewedWorktree;
    writeJSON(STORAGE.worktree, { reviewed: state.reviewedWorktree });
    if (state.reviewedWorktree) {
      state.notifications.unshift({
        t: nowMs(),
        kind: 'task_done',
        text: 'Worktree review marked complete locally.',
        run: state.activeRun.id,
      });
      state.notifications = state.notifications.slice(0, 12);
    }
    renderShell({ preserveScroll: true });
  }

  function openPalette() {
    state.paletteOpen = true;
    state.paletteQuery = '';
    state.paletteIndex = 0;
    state.stopOpen = false;
    state.goalEditor = null;
    renderOverlay();
  }

  function closePalette() {
    if (!state.paletteOpen) return;
    state.paletteOpen = false;
    renderOverlay();
    renderShell({ preserveScroll: true });
  }

  function openStopModal(action = 'stop') {
    state.stopOpen = true;
    state.stopAction = action || 'stop';
    state.stopConfirmation = '';
    state.stopError = '';
    state.stopSubmitting = false;
    state.paletteOpen = false;
    state.goalEditor = null;
    renderOverlay();
  }

  function closeStopModal() {
    if (!state.stopOpen || state.stopSubmitting) return;
    state.stopOpen = false;
    state.stopConfirmation = '';
    state.stopError = '';
    renderOverlay();
    renderShell({ preserveScroll: true });
  }

  function openNewGoal(bucket) {
    state.goalEditor = {
      mode: 'new',
      bucket,
      index: -1,
      draft: { bucket, text: '', note: '' },
      error: '',
    };
    state.paletteOpen = false;
    state.stopOpen = false;
    renderOverlay();
  }

  function openGoalEditor(bucket, index) {
    const source = clone(state.goals[bucket][index]);
    state.goalEditor = {
      mode: 'edit',
      bucket,
      index,
      draft: {
        bucket,
        text: source.text || '',
        note: source.note || '',
      },
      error: '',
    };
    state.paletteOpen = false;
    state.stopOpen = false;
    renderOverlay();
  }

  function closeGoalEditor() {
    if (!state.goalEditor) return;
    state.goalEditor = null;
    renderOverlay();
    renderShell({ preserveScroll: true });
  }

  function saveGoalEditor() {
    if (!state.goalEditor) return;
    const editor = state.goalEditor;
    const text = String(editor.draft.text || '').trim();
    if (!text) {
      editor.error = 'Goal text cannot be empty.';
      renderGoalEditorOverlay();
      return;
    }

    const nextGoals = clone(state.goals);
    const targetBucket = editor.draft.bucket;
    const sourceItem = editor.mode === 'edit' && editor.index >= 0 ? clone(nextGoals[editor.bucket][editor.index] || {}) : null;
    const nextItem = sourceItem
      ? {
          ...sourceItem,
          text,
          note: String(editor.draft.note || '').trim(),
          done: Boolean(sourceItem.done),
          checked: Boolean(sourceItem.checked ?? sourceItem.done),
          checkbox: toText(sourceItem.checkbox, Boolean(sourceItem.done) ? '[x]' : '[ ]'),
        }
      : {
          done: false,
          checked: false,
          checkbox: '[ ]',
          text,
          note: String(editor.draft.note || '').trim(),
        };

    if (editor.mode === 'new' || editor.index < 0) {
      nextGoals[targetBucket].push(nextItem);
    } else if (targetBucket !== editor.bucket) {
      nextGoals[editor.bucket].splice(editor.index, 1);
      nextGoals[targetBucket].push(nextItem);
    } else {
      nextGoals[editor.bucket][editor.index] = nextItem;
    }

    state.goals = nextGoals;
    writeJSON(STORAGE.goals, state.goals);
    state.goalsDirty = true;
    state.goalEditor = null;
    renderShell({ preserveScroll: true });
  }

  function resetGoals() {
    state.goals = clone(defaults.goals);
    writeJSON(STORAGE.goals, state.goals);
    state.goalsDirty = true;
    renderShell({ preserveScroll: true });
  }

  function resetConfig() {
    state.config = clone(defaults.config);
    writeJSON(STORAGE.config, state.config);
    renderShell({ preserveScroll: true });
  }

  function setView(view) {
    const next = normalizeView(view);
    state.activeView = next;
    state.paletteOpen = false;
    state.stopOpen = false;
    state.goalEditor = null;
    if (history.replaceState) {
      history.replaceState(null, '', `#${next}`);
    } else {
      location.hash = next;
    }
    renderShell({ preserveScroll: false });
  }

  function applyPaletteSelection(index) {
    const commands = renderPaletteCommands().filter(paletteMatches);
    const command = commands[index];
    if (!command) return;
    if (command.kind === 'nav') {
      closePalette();
      setView(command.view);
      return;
    }
    if (command.kind === 'action') {
      closePalette();
      handleAction(command.action, null);
    }
  }

  function renderMainView() {
    switch (state.activeView) {
      case 'dashboard':
        return renderDashboard();
      case 'pipeline':
        return renderPipeline();
      case 'logs':
        return renderLogs();
      case 'backlog':
        return renderBacklog();
      case 'goals':
        return renderGoals();
      case 'config':
        return renderConfig();
      case 'prompts':
        return renderPrompts();
      case 'history':
        return renderHistory();
      case 'notifications':
        return renderNotifications();
      case 'worktree':
        return renderWorktree();
      case 'landing':
        return renderLanding();
      case 'mobile':
        return renderMobile();
      default:
        return renderDashboard();
    }
  }

  function renderRoot() {
    topbarRoot().innerHTML = renderTopbar();
    sidebarRoot().innerHTML = renderSidebar();
    mainRoot().innerHTML = renderMainView();
    mainRoot().dataset.view = state.activeView;
    overlayRoot().innerHTML = '';
    document.title = `AgentCLI Web Console | ${VIEW_LABELS[state.activeView]}`;
  }

  function stopLiveLogStream() {
    if (state.liveLogTimer) {
      window.clearInterval(state.liveLogTimer);
      state.liveLogTimer = null;
    }
  }

  function stopSnapshotPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function startFallbackLogStream() {
    if (state.sourceMode !== 'fallback' || state.liveLogTimer || state.logsPaused) {
      return;
    }
    state.liveLogTimer = window.setInterval(() => {
      if (state.logsPaused || state.activeRun.status !== 'running') {
        if (!state.paletteOpen && !state.goalEditor && !state.stopOpen) {
          topbarRoot().innerHTML = renderTopbar();
        }
        return;
      }

      const samples = [
        { lvl: 'debug', stage: 'Dev', msg: 'tool_use: inspect worktree.patch' },
        { lvl: 'info', stage: 'Dev', msg: 'edit: src/db/overrides.sql (+12)' },
        { lvl: 'warn', stage: 'Dev', msg: 'checkpoint overdue by 2m, continuing safely' },
        { lvl: 'info', stage: 'QA', msg: 'verification queued for the next cycle' },
        { lvl: 'debug', stage: 'PM', msg: 'refreshing backlog summary from current goals' },
      ];
      const sample = samples[state.liveLogTick % samples.length];
      state.liveLogTick += 1;
      state.logs.push({
        t: fmtClock(nowMs()),
        lvl: sample.lvl,
        stage: sample.stage,
        msg: sample.msg,
      });
      state.logs = state.logs.slice(-72);

      if (!state.paletteOpen && !state.goalEditor && !state.stopOpen) {
        renderShell({
          preserveScroll: true,
          scrollToBottom: state.activeView === 'logs',
        });
      }
    }, 2200);
  }

  async function refreshSnapshot(options = {}) {
    const { allowFallback = false, silent = false } = options;
    try {
      const response = await fetch('/api/status', {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const snapshot = await response.json();
      const normalized = normalizeApiSnapshot(snapshot);
      const signature = JSON.stringify({
        latestRunDir: normalized.latestRunDir,
        activeRun: [normalized.activeRun.id, normalized.activeRun.status, normalized.activeRun.stage, normalized.activeRun.iteration, normalized.activeRun.maxIterations],
        stages: normalized.stages.map((stage) => [
          stage.id,
          stage.status,
          stage.startedAt,
          stage.endedAt,
          stage.durationSec,
          stage.model,
          stage.cycle,
          stage.taskId,
          stage.attempt,
          stage.recentOutput,
          stage.reason,
          stage.rc,
        ]),
        backlog: normalized.backlog.map((task) => [
          task.id,
          task.status,
          task.attempt,
          task.fileScope,
          task.failureReason,
          task.failureDetail,
          task.recentOutput,
          task.cycle,
          task.step,
          task.taskTitle,
          task.model,
          task.dependsOn,
        ]),
        logs: normalized.logs.slice(-12).map((line) => [line.t, line.lvl, line.stage, line.msg]),
        notifications: normalized.notifications.slice(-12).map((item) => [item.t, item.kind, item.text]),
        runnerControl: [
          normalized.runnerControl.enabled,
          normalized.runnerControl.controllerAvailable,
          normalized.runnerControl.busy,
          normalized.runnerControl.status.running,
          normalized.runnerControl.runStatus,
          normalized.runnerControl.message,
          normalized.runnerControl.lastAction,
          normalized.runnerControl.lastMessage,
          normalized.runnerControl.lastError,
        ],
        sourceMode: normalized.sourceMode,
      });
      const previousSignature = state.lastSnapshotSignature;
      state.lastSnapshotSignature = signature;
      applySnapshotModel(normalized);
      stopLiveLogStream();
      if (!state.paletteOpen && !state.goalEditor && !state.stopOpen) {
        renderShell({
          preserveScroll: true,
          scrollToBottom: state.activeView === 'logs',
        });
      }
      return previousSignature !== signature;
    } catch (error) {
      if (!state.lastSnapshotAt && allowFallback) {
        applySnapshotModel(fallbackFixture);
        state.snapshotStatus = 'fallback';
        state.snapshotLabel = 'Fallback data';
        state.sourceMode = 'fallback';
        state.serverMode = false;
        state.lastSnapshotSignature = JSON.stringify({
          sourceMode: state.sourceMode,
          activeRun: state.activeRun.id,
          logs: state.logs.length,
        });
        startFallbackLogStream();
        if (!state.paletteOpen && !state.goalEditor && !state.stopOpen) {
          renderShell({
            preserveScroll: true,
            scrollToBottom: state.activeView === 'logs',
          });
        }
        return true;
      }

      if (state.sourceMode === 'fallback') {
        if (!silent && !state.paletteOpen && !state.goalEditor && !state.stopOpen) {
          topbarRoot().innerHTML = renderTopbar();
        }
        return false;
      }

      if (state.lastSnapshotAt) {
        state.snapshotStatus = 'stale';
        state.snapshotLabel = 'Stale snapshot';
        if (!silent && !state.paletteOpen && !state.goalEditor && !state.stopOpen) {
          renderShell({ preserveScroll: true });
        }
        return false;
      }

      state.snapshotStatus = 'error';
      state.snapshotLabel = 'API error';
      if (!silent && !state.paletteOpen && !state.goalEditor && !state.stopOpen) {
        renderShell({ preserveScroll: true });
      }
      return false;
    }
  }

  function startSnapshotPolling() {
    if (state.pollTimer) {
      return;
    }
    state.pollTimer = window.setInterval(() => {
      refreshSnapshot({ silent: true });
    }, SNAPSHOT_POLL_MS);
  }

  document.addEventListener('click', (event) => {
    const nav = event.target.closest('[data-nav]');
    if (nav) {
      setView(nav.dataset.nav);
      return;
    }

    const action = event.target.closest('[data-action]');
    if (action) {
      handleAction(action.dataset.action, action);
      return;
    }

    const backlog = event.target.closest('[data-backlog-select]');
    if (backlog) {
      setBacklogSelection(backlog.dataset.backlogSelect);
      return;
    }

    const history = event.target.closest('[data-history-select]');
    if (history) {
      setHistorySelection(history.dataset.historySelect);
      return;
    }

    const prompt = event.target.closest('[data-prompt-select]');
    if (prompt) {
      setPromptSelection(prompt.dataset.promptSelect);
      return;
    }

    const filter = event.target.closest('[data-filter]');
    if (filter) {
      setActiveLogFilter(filter.dataset.filter);
      return;
    }

    const notifFilter = event.target.closest('[data-notification-filter]');
    if (notifFilter) {
      setNotificationFilter(notifFilter.dataset.notificationFilter);
      return;
    }

    const configSelect = event.target.closest('[data-config-select]');
    if (configSelect) {
      selectConfigPath(configSelect.dataset.configSelect);
      return;
    }

    const configToggle = event.target.closest('[data-config-toggle]');
    if (configToggle) {
      toggleConfigBool(configToggle.dataset.configToggle);
      return;
    }

    const configMulti = event.target.closest('[data-config-multi]');
    if (configMulti) {
      toggleConfigMulti(configMulti.dataset.configMulti, configMulti.dataset.configValue);
      return;
    }

    const goalToggle = event.target.closest('[data-goal-action="toggle"]');
    if (goalToggle) {
      const bucket = goalToggle.dataset.goalBucket;
      const index = Number(goalToggle.dataset.goalIndex);
      updateGoal(bucket, index, { done: !state.goals[bucket][index].done });
      return;
    }

    const goalEdit = event.target.closest('[data-goal-action="edit"]');
    if (goalEdit) {
      openGoalEditor(goalEdit.dataset.goalBucket, Number(goalEdit.dataset.goalIndex));
      return;
    }

    const goalMove = event.target.closest('[data-goal-action="move"]');
    if (goalMove) {
      moveGoal(goalMove.dataset.goalBucket, Number(goalMove.dataset.goalIndex));
      return;
    }

    const goalDelete = event.target.closest('[data-goal-action="delete"]');
    if (goalDelete) {
      deleteGoal(goalDelete.dataset.goalBucket, Number(goalDelete.dataset.goalIndex));
    }
  });

  document.addEventListener('input', (event) => {
    if (state.paletteOpen && event.target.matches('[data-palette-input]')) {
      state.paletteQuery = event.target.value;
      state.paletteIndex = 0;
      renderPaletteList();
      return;
    }

    if (state.goalEditor && event.target.matches('[data-goal-field]')) {
      const field = event.target.dataset.goalField;
      state.goalEditor.draft[field] = event.target.value;
      return;
    }

    if (state.stopOpen && event.target.matches('[data-stop-confirmation]')) {
      state.stopConfirmation = event.target.value;
      state.stopError = '';
      renderStopOverlay();
    }
  });

  document.addEventListener('change', (event) => {
    if (event.target.matches('[data-config-field]')) {
      const path = event.target.dataset.configField;
      const schema = state.configSchema[path];
      if (!schema) return;
      updateConfigPath(path, event.target.value);
    }
  });

  document.addEventListener('keydown', (event) => {
    const target = event.target;

    if (state.paletteOpen) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closePalette();
        return;
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter') {
        const commands = renderPaletteCommands().filter(paletteMatches);
        if (!commands.length) return;
        event.preventDefault();
        if (event.key === 'ArrowDown') {
          state.paletteIndex = Math.min(commands.length - 1, state.paletteIndex + 1);
          renderPaletteList();
          return;
        }
        if (event.key === 'ArrowUp') {
          state.paletteIndex = Math.max(0, state.paletteIndex - 1);
          renderPaletteList();
          return;
        }
        if (event.key === 'Enter') {
          applyPaletteSelection(state.paletteIndex);
        }
      }
      return;
    }

    if (state.stopOpen) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeStopModal();
      }
      return;
    }

    if (state.goalEditor) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeGoalEditor();
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'enter') {
        event.preventDefault();
        saveGoalEditor();
      }
      return;
    }

    if (isEditableTarget(target)) {
      return;
    }

    if (event.key === '/' || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k')) {
      event.preventDefault();
      openPalette();
      return;
    }

    if (event.key === 'Escape') {
      return;
    }

    if (event.key.toLowerCase() === 'g' && !event.metaKey && !event.ctrlKey && !event.altKey) {
      state.pendingChord = 'g';
      window.clearTimeout(state.pendingChordTimer);
      state.pendingChordTimer = window.setTimeout(() => {
        state.pendingChord = null;
      }, 800);
      return;
    }

    if (state.pendingChord === 'g') {
      const key = event.key.toLowerCase();
      const map = {
        d: 'dashboard',
        p: 'pipeline',
        l: 'logs',
        b: 'backlog',
        g: 'goals',
        c: 'config',
        t: 'prompts',
        r: 'history',
        n: 'notifications',
        w: 'worktree',
        h: 'landing',
        m: 'mobile',
      };
      const nextView = map[key];
      if (nextView) {
        event.preventDefault();
        state.pendingChord = null;
        setView(nextView);
        return;
      }
      state.pendingChord = null;
    }
  });

  document.addEventListener('click', (event) => {
    const overlay = event.target.closest('[data-overlay]');
    if (!overlay) return;

    const paletteInput = event.target.closest('[data-palette-input]');
    if (paletteInput) return;

    const paletteItem = event.target.closest('[data-palette-index]');
    if (paletteItem) {
      applyPaletteSelection(Number(paletteItem.dataset.paletteIndex));
      return;
    }

    const goalClose = event.target.closest('[data-goal-close]');
    if (goalClose) {
      closeGoalEditor();
      return;
    }

    const goalSave = event.target.closest('[data-goal-save]');
    if (goalSave) {
      saveGoalEditor();
      return;
    }

    const goalBucket = event.target.closest('[data-goal-bucket]');
    if (goalBucket) {
      if (state.goalEditor) {
        state.goalEditor.draft.bucket = goalBucket.dataset.goalBucket;
        renderGoalEditorOverlay();
      }
      return;
    }

    const stopClose = event.target.closest('[data-stop-close]');
    if (stopClose) {
      closeStopModal();
      return;
    }

    const stopConfirm = event.target.closest('[data-stop-confirm]');
    if (stopConfirm) {
      applyStop();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (state.paletteOpen || state.goalEditor || state.stopOpen) {
      return;
    }
    if (event.key === 'Enter' && event.target.matches('[data-config-field][type="text"], [data-config-field][type="number"], [data-goal-field]')) {
      event.target.blur();
    }
  });

  document.addEventListener('click', (event) => {
    const configReset = event.target.closest('[data-config-reset]');
    if (configReset) {
      resetConfig();
    }
  });

  function updateClockChips() {
    if (state.paletteOpen || state.goalEditor || state.stopOpen) {
      return;
    }
    topbarRoot().innerHTML = renderTopbar();
  }

  async function bootstrapConsole() {
    renderRoot();
    await refreshSnapshot({ allowFallback: true });
    startSnapshotPolling();
  }

  if (!(typeof globalThis !== 'undefined' && globalThis.__AGENTCLI_SKIP_BOOTSTRAP__)) {
    bootstrapConsole();
  }

  window.addEventListener('hashchange', () => {
    const next = normalizeView(location.hash.replace(/^#/, ''));
    if (next !== state.activeView) {
      state.activeView = next;
      renderShell({ preserveScroll: false });
    }
  });

  window.addEventListener('focus', updateClockChips);
})();
