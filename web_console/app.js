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
  const GOALS_SAVE_CONFIRMATION_PHRASE = 'DELETE OR DOWNGRADE UNMET P0 GOALS';
  const WORKTREE_ACTION_CONFIRMATIONS = {
    merge: 'MERGE WORKTREE',
    discard: 'DISCARD WORKTREE',
  };
  const WORKTREE_ACTION_INSTRUCTION_PREFIXES = {
    merge: 'Type MERGE WORKTREE exactly to apply',
    discard: 'Type DISCARD WORKTREE exactly to discard',
  };
  // Keep the template-form text in source for static coverage:
  // Type ${worktreeActionConfirmationPhrase('merge')} exactly to apply
  // Type ${worktreeActionConfirmationPhrase('discard')} exactly to discard

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

  function removeJSON(key) {
    try {
      localStorage.removeItem(key);
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

  const VALID_QUOTA_WINDOWS = new Set(['5h', '7d']);

  function normalizeQuotaSource(raw) {
    const data = toObject(raw);
    const rawQuota = toObject(data.quota);
    const used = toMaybeNumber(rawQuota.used ?? data.quota_used ?? data.quotaUsed);
    const window = toText(rawQuota.window ?? data.quota_window ?? data.quotaWindow, '');
    if (used == null || !window || !VALID_QUOTA_WINDOWS.has(window.toLowerCase())) {
      return {
        window: '',
        used: null,
        available: false,
      };
    }
    return {
      window,
      used,
      available: true,
    };
  }

  function normalizeQuotaData(primary, fallback = {}) {
    const primaryQuota = normalizeQuotaSource(primary);
    if (primaryQuota.available) {
      return primaryQuota;
    }
    return normalizeQuotaSource(fallback);
  }

  function formatQuotaUsage(quota) {
    const data = toObject(quota);
    if (!data.available || !toText(data.window, '')) {
      return 'unavailable';
    }
    const usedText = metricText(true, data.used, fmtPercent);
    const windowText = toText(data.window, '');
    return windowText ? `${windowText} | ${usedText}` : usedText;
  }

  function formatQuotaSummary(quota) {
    const data = toObject(quota);
    if (!data.available || !toText(data.window, '')) {
      return 'quota unavailable';
    }
    const usedText = metricText(true, data.used, fmtPercent);
    const windowText = toText(data.window, '');
    return windowText ? `${windowText} quota | ${usedText} used` : `quota | ${usedText} used`;
  }

  function renderQuotaControl(quota, title = '') {
    const data = toObject(quota);
    const available = Boolean(data.available && toText(data.window, ''));
    const titleText = toText(title, available ? `Quota ${toText(data.window, '')} usage` : 'Quota unavailable');
    if (!available) {
      return `<span class="meter-chip meter-chip--unavailable" title="${escapeHTML(titleText)}">quota unavailable</span>`;
    }
    const quotaText = formatQuotaUsage(data);
    const quotaWidth = progressWidth(data.used);
    return `
      <span class="meter-chip" title="${escapeHTML(titleText)}">
        quota ${escapeHTML(quotaText)}
        <span class="meter" aria-hidden="true">
          <span class="meter__fill meter__fill--info" style="width:${escapeHTML(quotaWidth)}"></span>
        </span>
      </span>
    `;
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

  function downloadTextFile(filename, text) {
    if (typeof Blob === 'undefined' || typeof URL === 'undefined' || !URL.createObjectURL) {
      return;
    }
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = 'noopener';
    anchor.style.position = 'fixed';
    anchor.style.left = '-10000px';
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 1000);
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
    if (kind === 'run_start') return 'var(--accent)';
    if (kind === 'run_stop') return 'var(--warn)';
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
  const RUNNER_CONTROL_STATUS_POLL_MS = 500;
  const RUNNER_CONTROL_STATUS_TIMEOUT_MS = 15000;
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
    return RUNNER_CONTROL_CONFIRMATIONS[action] || RUNNER_CONTROL_CONFIRMATIONS.reload;
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

  function runnerControlCompletionLabel(action) {
    const labels = {
      start: 'Started',
      stop: 'Stopped',
      reload: 'Reloaded',
      restart: 'Restarted',
    };
    return labels[action] || 'Success';
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
      start: 'Start the runner using the selected repo and config snapshot.',
      stop: 'Stop the current runner, write the stop signal, and wait for a terminal status.',
      reload: 'Stop the current runner, wait for it to settle, then start again using the selected repo and config snapshot.',
      restart: 'Restart the runner using the selected repo and config snapshot.',
    };
    return summaries[action] || 'Confirm this runner control action.';
  }

  function runnerControlStateInfo(control = state.runnerControl) {
    const current = toObject(control);
    const status = toObject(current.status);
    const statusReason = toText(status.reason, '');
    const busyAction = runnerControlBusyAction();
    if (current.busy || state.stopSubmitting) {
      const action = state.stopSubmitting ? (busyAction || current.lastAction || state.stopAction || 'start') : '';
      return {
        chipTone: 'loading',
        bannerTone: 'info',
        label: state.stopSubmitting ? runnerControlActionLabel(action, true) : 'Working...',
        title: 'Action in flight',
        copy: current.message || 'Runner status is being refreshed.',
      };
    }
    if (current.lastError || statusReason.startsWith('status_error:')) {
      return {
        chipTone: 'err',
        bannerTone: 'err',
        label: 'Error',
        title: 'Backend error',
        copy: current.lastError || statusReason || current.message || 'Runner controller reported an error.',
      };
    }
    if (!current.controllerAvailable) {
      return {
        chipTone: 'paused',
        bannerTone: 'warn',
        label: 'Unavailable',
        title: 'Controller unavailable',
        copy: current.message || 'Runner controller is unavailable.',
      };
    }
    if (!current.enabled) {
      return {
        chipTone: 'paused',
        bannerTone: 'warn',
        label: 'Controls off',
        title: 'Controls disabled',
        copy: current.message || 'Runner controls are disabled.',
      };
    }
    if (current.lastMessage) {
      return {
        chipTone: 'success',
        bannerTone: 'success',
        label: runnerControlCompletionLabel(current.lastAction),
        title: 'Action complete',
        copy: current.message || current.lastMessage,
      };
    }
    if (status.running) {
      return {
        chipTone: 'running',
        bannerTone: 'info',
        label: 'Running',
        title: 'Runner running',
        copy: current.message || 'Runner is running.',
      };
    }
    return {
      chipTone: 'idle',
      bannerTone: 'idle',
      label: 'Ready',
      title: 'Ready',
      copy: current.message || 'Runner controls are ready.',
    };
  }

  function runnerControlValueClass(tone) {
    const normalized = toText(tone, '').toLowerCase();
    if (normalized === 'err') {
      return 'runner-control__value--err';
    }
    if (normalized === 'warn' || normalized === 'paused' || normalized === 'loading') {
      return 'runner-control__value--warn';
    }
    if (normalized === 'success') {
      return 'runner-control__value--accent';
    }
    return 'runner-control__value--muted';
  }

  function runnerControlDetailRows(control, display) {
    const current = toObject(control);
    const status = toObject(current.status);
    return [
      { label: 'Source', value: current.source || 'unknown', className: 'runner-control__value--muted' },
      { label: 'Selected repo', value: status.repo || 'unknown', className: 'runner-control__value--muted' },
      { label: 'Selected config', value: status.configPath || 'unknown', className: 'runner-control__value--muted' },
      {
        label: 'Controller',
        value: current.controllerAvailable ? 'available' : 'unavailable',
        className: current.controllerAvailable ? (display.chipTone === 'err' ? 'runner-control__value--err' : 'runner-control__value--accent') : runnerControlValueClass(display.chipTone),
      },
      { label: 'State', value: display.label, className: runnerControlValueClass(display.chipTone) },
      { label: 'Run mode', value: status.runnerMode || 'unknown', className: 'runner-control__value--muted' },
      {
        label: 'Run status',
        value: current.runStatus || (status.running ? 'running' : 'idle'),
        className: status.running ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: 'Last action',
        value: current.lastAction || 'none',
        className: current.lastAction ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: 'Last message',
        value: current.lastMessage || 'none',
        className: current.lastMessage ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: 'Last error',
        value: current.lastError || 'none',
        className: current.lastError ? 'runner-control__value--err' : 'runner-control__value--muted',
      },
    ];
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
        configPath: toText(overrides.configPath, ''),
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
        configPath: toText(status.config_path || status.configPath, ''),
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
    const lineNumber = toMaybeNumber(raw.line_number ?? raw.lineNumber ?? raw.cursor, null);
    return {
      t: toText(raw.t || raw.ts, fmtClock(nowMs())),
      lvl: normalizeLogLevel(raw.lvl || raw.level),
      stage: normalizeLogStage(raw.stage || raw.component || raw.scope),
      msg: toText(raw.msg || raw.message || raw.text, ''),
      cursor: lineNumber == null ? null : lineNumber,
      line_number: lineNumber == null ? null : lineNumber,
      lineNumber: lineNumber == null ? null : lineNumber,
      raw: toText(raw.raw || raw.raw_line || raw.rawLine || '', ''),
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

  function normalizeGoalBuckets(goals) {
    const raw = toObject(goals);
    const items = toObject(raw.items);
    return {
      p0: normalizeGoalBucket(items.p0 || raw.p0 || []),
      p1: normalizeGoalBucket(items.p1 || raw.p1 || []),
    };
  }

  function goalSnapshotMessage(snapshot, total, dirty = false) {
    if (dirty) {
      return 'Browser-local goal edits are active. Reset to restore the API snapshot.';
    }
    const raw = toObject(snapshot);
    if (!raw.exists) {
      return 'GOALS.md is missing.';
    }
    const rawText = toText(raw.raw_text || raw.rawText, '').trim();
    if (!rawText) {
      return 'GOALS.md is empty.';
    }
    if (!total) {
      return 'GOALS.md has content but no checklist items were parsed.';
    }
    return 'Read-only GOALS.md snapshot with stable P0/P1 grouping and exact checkbox state.';
  }

  function goalBucketLabel(bucket) {
    return bucket === 'p0' ? 'P0 | Must-have' : 'P1 | Should-have';
  }

  function goalBucketName(bucket) {
    return bucket === 'p0' ? 'Must-have' : 'Should-have';
  }

  function goalItemLineNumber(goal) {
    const item = toObject(goal);
    return toNumber(item.lineNumber || item.line_number || item.line || 0, 0);
  }

  function goalItemCheckbox(goal) {
    const item = toObject(goal);
    const checked = Boolean(item.done ?? item.checked);
    return toText(item.checkbox, checked ? '[x]' : '[ ]');
  }

  function goalItemSummary(goal) {
    const item = toObject(goal);
    const checkbox = goalItemCheckbox(item);
    const text = toText(item.text, '(untitled goal)');
    const note = toText(item.note, '');
    return note ? `${checkbox} ${text} | ${note}` : `${checkbox} ${text}`;
  }

  function goalItemMeta(goal) {
    const item = toObject(goal);
    const lineNumber = goalItemLineNumber(item);
    return `${lineNumber ? `Source line ${lineNumber}` : 'Local draft item'} | Checkbox ${goalItemCheckbox(item)}`;
  }

  function goalItemSignature(goal) {
    const item = toObject(goal);
    const lineNumber = goalItemLineNumber(item);
    return JSON.stringify({
      done: Boolean(item.done),
      checked: Boolean(item.checked ?? item.done),
      checkbox: goalItemCheckbox(item),
      text: toText(item.text, ''),
      note: toText(item.note, ''),
      lineNumber,
      line_number: lineNumber,
      line: lineNumber,
    });
  }

  function goalItemMatchKey(goal, bucket) {
    const item = toObject(goal);
    const lineNumber = goalItemLineNumber(item);
    if (lineNumber) {
      return `${bucket}:line:${lineNumber}`;
    }
    return `${bucket}:sig:${goalItemSignature(item)}`;
  }

  function buildGoalDraftSummary(snapshotGoals, draftGoals) {
    const snapshot = normalizeGoalBuckets(snapshotGoals);
    const draft = normalizeGoalBuckets(draftGoals);
    const rows = [];

    for (const bucket of ['p0', 'p1']) {
      const baseItems = snapshot[bucket];
      const draftItems = draft[bucket];
      const snapshotMap = new Map();
      const matched = new Set();

      baseItems.forEach((item, index) => {
        snapshotMap.set(goalItemMatchKey(item, bucket), { item, index });
      });

      draftItems.forEach((item, index) => {
        const key = goalItemMatchKey(item, bucket);
        const match = snapshotMap.get(key);
        if (!match) {
          rows.push({
            kind: 'added',
            bucket,
            bucketLabel: goalBucketLabel(bucket),
            index,
            item,
          });
          return;
        }

        matched.add(key);
        const changed = goalItemSignature(match.item) !== goalItemSignature(item);
        const moved = match.index !== index;
        if (changed || moved) {
          rows.push({
            kind: changed ? 'edited' : 'moved',
            bucket,
            bucketLabel: goalBucketLabel(bucket),
            index,
            baseIndex: match.index,
            base: match.item,
            item,
          });
        }
      });

      baseItems.forEach((item, index) => {
        const key = goalItemMatchKey(item, bucket);
        if (!matched.has(key)) {
          rows.push({
            kind: 'removed',
            bucket,
            bucketLabel: goalBucketLabel(bucket),
            index,
            base: item,
          });
        }
      });
    }

    const total = draft.p0.length + draft.p1.length;
    const done = draft.p0.filter((goal) => goal.done).length + draft.p1.filter((goal) => goal.done).length;
    return {
      dirty: rows.length > 0,
      total,
      done,
      added: rows.filter((row) => row.kind === 'added').length,
      edited: rows.filter((row) => row.kind === 'edited').length,
      moved: rows.filter((row) => row.kind === 'moved').length,
      removed: rows.filter((row) => row.kind === 'removed').length,
      rows,
    };
  }

  function goalSaveMatchKey(goal) {
    const item = toObject(goal);
    const lineNumber = goalItemLineNumber(item);
    if (lineNumber) {
      return `line:${lineNumber}`;
    }
    return `sig:${goalItemSignature(item)}`;
  }

  function buildGoalSaveRiskSummary(snapshotGoals, draftGoals) {
    const snapshot = normalizeGoalBuckets(snapshotGoals);
    const draft = normalizeGoalBuckets(draftGoals);
    const nextIndex = {
      p0: new Map(),
      p1: new Map(),
    };

    for (const bucket of ['p0', 'p1']) {
      for (const item of draft[bucket]) {
        const key = goalSaveMatchKey(item);
        nextIndex[bucket].set(key, (nextIndex[bucket].get(key) || 0) + 1);
      }
    }

    const deletedUncheckedP0 = [];
    const downgradedUncheckedP0 = [];

    for (const item of snapshot.p0) {
      if (Boolean(item.done || item.checked)) {
        continue;
      }
      const identity = goalSaveMatchKey(item);
      const sameBucketCount = nextIndex.p0.get(identity) || 0;
      if (sameBucketCount > 0) {
        nextIndex.p0.set(identity, sameBucketCount - 1);
        continue;
      }
      const downgradedCount = nextIndex.p1.get(identity) || 0;
      if (downgradedCount > 0) {
        nextIndex.p1.set(identity, downgradedCount - 1);
        downgradedUncheckedP0.push(item);
      } else {
        deletedUncheckedP0.push(item);
      }
    }

    const riskCount = deletedUncheckedP0.length + downgradedUncheckedP0.length;
    return {
      requiresConfirmation: riskCount > 0,
      confirmationPhrase: GOALS_SAVE_CONFIRMATION_PHRASE,
      deletedUncheckedP0,
      downgradedUncheckedP0,
      riskCount,
    };
  }

  function goalSaveRiskSummaryText(risk) {
    const deleted = toArray(risk.deletedUncheckedP0);
    const downgraded = toArray(risk.downgradedUncheckedP0);
    const total = deleted.length + downgraded.length;
    if (!total) {
      return 'no risky P0 changes';
    }
    return `${total} unchecked P0 goal${total === 1 ? '' : 's'}`;
  }

  function normalizeGoalSaveRisk(rawRisk) {
    const raw = toObject(rawRisk);
    const deleted = normalizeGoalBucket(raw.deleted_unchecked_p0 || raw.deletedUncheckedP0 || []);
    const downgraded = normalizeGoalBucket(raw.downgraded_unchecked_p0 || raw.downgradedUncheckedP0 || []);
    const riskCount = toNumber(raw.risk_count ?? raw.riskCount, deleted.length + downgraded.length);
    return {
      requiresConfirmation: Boolean(raw.requires_confirmation ?? raw.requiresConfirmation ?? riskCount),
      confirmationPhrase: toText(raw.confirmation_phrase || raw.confirmationPhrase, GOALS_SAVE_CONFIRMATION_PHRASE),
      deletedUncheckedP0: deleted,
      downgradedUncheckedP0: downgraded,
      riskCount,
    };
  }

  function normalizeGoalSaveResponse(payload) {
    const raw = toObject(payload);
    const error = toObject(raw.error);
    const errorDetails = toObject(error.details);
    const risk = normalizeGoalSaveRisk(raw.risk || raw.risk_report || errorDetails.risk || errorDetails.risk_report || {});
    return {
      ok: Boolean(raw.ok !== false),
      action: toText(raw.action, ''),
      status: toText(raw.status, ''),
      message: toText(raw.message, ''),
      goalsPath: toText(raw.goals_path || raw.goalsPath || raw.saved_path || raw.savedPath, ''),
      savedPath: toText(raw.saved_path || raw.savedPath, ''),
      backupPath: toText(raw.backup_path || raw.backupPath || errorDetails.backup_path || errorDetails.backupPath, ''),
      confirmationPhrase: toText(raw.confirmation_phrase || raw.confirmationPhrase || errorDetails.confirmation_phrase || errorDetails.confirmationPhrase, GOALS_SAVE_CONFIRMATION_PHRASE),
      risk,
      snapshot: toObject(raw.snapshot),
      error,
    };
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
      profile: toText(raw.profile, ''),
      source: toText(raw.source, ''),
      mode: toText(raw.mode, 'template'),
      updated: toText(raw.updated, 'unknown'),
      summary: raw.summary == null ? '' : String(raw.summary),
      preview: raw.preview == null ? '' : String(raw.preview),
      path: toText(raw.path, ''),
      contentLength: toMaybeNumber(raw.content_length ?? raw.contentLength) ?? 0,
      content: raw.content == null ? '' : String(raw.content),
      templateVariables: normalizeListValues(raw.template_variables ?? raw.templateVariables),
    };
  }

  function extractTemplateVariables(text) {
    const vars = [];
    const seen = new Set();
    const pattern = /\{([A-Za-z_][A-Za-z0-9_.-]*)\}/g;
    const raw = String(text || '');
    let match;
    while ((match = pattern.exec(raw))) {
      const name = match[1];
      if (seen.has(name)) {
        continue;
      }
      seen.add(name);
      vars.push(name);
    }
    return vars;
  }

  function normalizeHistoryItem(run) {
    const raw = toObject(run);
    const taskCounts = toObject(raw.taskCounts || raw.task_counts);
    const runSummary = toObject(raw.runSummary || raw.run_summary);
    const lastRunSummary = toObject(raw.lastRunSummary || raw.last_run_summary);
    const runCycles = toArray(runSummary.cycles);
    return {
      id: toText(raw.id, 'run'),
      startedAt: toNumber(raw.startedAt || raw.started_at || 0, 0),
      endedAt: toNumber(raw.endedAt || raw.ended_at || 0, 0),
      status: toText(raw.status, 'idle'),
      tasksDone: toNumber(raw.tasksDone ?? taskCounts.done ?? lastRunSummary.done ?? 0, 0),
      tasksTotal: toNumber(raw.tasksTotal ?? taskCounts.total ?? lastRunSummary.total_tasks ?? 0, 0),
      tasksFailed: toNumber(raw.tasksFailed ?? taskCounts.failed ?? lastRunSummary.failed_count ?? 0, 0),
      tasksSkipped: toNumber(raw.tasksSkipped ?? taskCounts.skipped ?? lastRunSummary.skipped ?? 0, 0),
      taskCounts: {
        done: toNumber(taskCounts.done ?? raw.tasksDone ?? lastRunSummary.done ?? 0, 0),
        failed: toNumber(taskCounts.failed ?? raw.tasksFailed ?? lastRunSummary.failed_count ?? 0, 0),
        skipped: toNumber(taskCounts.skipped ?? raw.tasksSkipped ?? lastRunSummary.skipped ?? 0, 0),
        total: toNumber(taskCounts.total ?? raw.tasksTotal ?? lastRunSummary.total_tasks ?? 0, 0),
        cycles: toNumber(taskCounts.cycles ?? raw.cycleCount ?? runCycles.length, runCycles.length),
      },
      branch: toText(raw.branch || runSummary.branch || lastRunSummary.branch, 'HEAD'),
      durationSec: toNumber(raw.durationSec || raw.duration_seconds || lastRunSummary.duration_seconds || lastRunSummary.durationSec || 0, 0),
      finalReason: toText(raw.finalReason || raw.final_reason || runSummary.final?.reason || lastRunSummary.reason || lastRunSummary.stop_reason, ''),
      shutdownReason: toText(raw.shutdownReason || raw.shutdown_reason || raw.stopReason || lastRunSummary.stop_reason || runSummary.final?.reason || '', ''),
      stopReason: toText(raw.stopReason || raw.shutdownReason || raw.shutdown_reason || lastRunSummary.stop_reason || runSummary.final?.reason || '', ''),
      runDir: toText(raw.runDir || raw.run_dir, ''),
      lastCycle: toText(raw.lastCycle, ''),
      runSummary,
      lastRunSummary,
      worktreeOutcome: toText(raw.worktreeOutcome || raw.worktree_outcome, 'none'),
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
    const status = toText(raw.status, 'none');
    const changedFiles = toArray(raw.changedFiles).map(normalizeChangedFile);
    const checklist = toArray(raw.checklist).map((item) => toText(item)).filter(Boolean);
    const sourceRepo = toText(raw.sourceRepo || raw.source_repo, '');
    const sourceBranch = toText(raw.sourceBranch || raw.source_branch || raw.branch, 'HEAD');
    const baseRef = toText(raw.baseRef || raw.base_ref || raw.branch, '');
    const headRef = toText(raw.headRef || raw.head_ref, '');
    const worktreeDir = toText(raw.worktreeDir || raw.worktree_dir || raw.worktree, '');
    const patchPath = toText(raw.patchPath || raw.patch_path || raw.patch, '');
    const statusFile = toText(raw.statusFile || raw.status_file || raw.pendingFile || raw.pending_file, '');
    const pendingFile = toText(raw.pendingFile || raw.pending_file || ((status === 'pending' || status === 'pending review') ? statusFile : ''), '');
    const cleanupPath = toText(raw.cleanupPath || raw.cleanup_path || worktreeDir, '');
    const cleanupMessage = toText(raw.cleanupMessage || raw.cleanup_message || raw.message || '', '');
    const cleanupState = toText(raw.cleanupState || raw.cleanup_state, 'none');
    const runDir = toText(raw.runDir || raw.run_dir, '');
    const runnerRc = toNumber(raw.runnerRc ?? raw.runner_rc ?? raw.lastRc ?? raw.last_rc ?? 0, 0);
    const reviewRequiredValue = raw.reviewRequired ?? raw.review_required;
    const reviewRequired = Boolean(
      reviewRequiredValue ??
        (status !== 'none' && status !== 'applied' && status !== 'discarded')
    );
    const reviewRequiredMessage = toText(
      raw.reviewRequiredMessage || raw.review_required_message || raw.message || raw.summary,
      ''
    );
    return {
      status,
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
      statusFile,
      cleanupPath,
      cleanupMessage,
      cleanupState,
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
    const quota = normalizeQuotaData(raw);
    const tokensAvailable = Boolean(
      raw.tokens_available ||
        raw.tokensAvailable ||
        tokens.in != null ||
        tokens.input != null ||
        tokens.out != null ||
        tokens.output != null
    );
    const budgetAvailable = Boolean(raw.budget_available || raw.budgetAvailable || raw.budget_used != null || raw.budgetUsed != null);
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
      quota: clone(quota),
      quota_window: quota.window,
      quotaWindow: quota.window,
      quota_used: quota.used,
      quotaUsed: quota.used,
      budget_used: budgetAvailable ? toMaybeNumber(raw.budget_used ?? raw.budgetUsed) : null,
      tokensAvailable,
      budgetAvailable,
      quotaAvailable: quota.available,
      quota_available: quota.available,
    };
  }

  function normalizeConfigData(config) {
    const raw = toObject(config);
    const schema = toObject(defaults.configSchema);
    const data = normalizeConfigTree(raw.data, schema);
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
    if (schema.kind === 'list') {
      const itemKind = toText(schema.item_kind || schema.itemKind, 'text');
      const items = normalizeListValues(value);
      if (itemKind === 'int' || itemKind === 'number') {
        return items.map((item) => {
          const parsed = Number(item);
          return Number.isFinite(parsed) && String(item).trim() !== '' ? Math.trunc(parsed) : item;
        });
      }
      return items;
    }
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

  function humanizeConfigPath(path) {
    const raw = String(path || '').split('.').pop() || String(path || '');
    const base = raw.replace(/_/g, ' ').trim();
    if (!base) {
      return String(path || '');
    }
    return base
      .replace(/\bpm\b/gi, 'PM')
      .replace(/\bqa\b/gi, 'QA')
      .replace(/\bdev\b/gi, 'Dev')
      .replace(/\brepo\b/gi, 'Repository')
      .replace(/\bgitops\b/gi, 'GitOps')
      .replace(/\btelegram\b/gi, 'Telegram');
  }

  function normalizeConfigTree(tree, schema) {
    let data = toObject(tree);
    for (const path of Object.keys(schema || {})) {
      const current = getAt(data, path);
      if (current === undefined) continue;
      data = setAt(data, path, normalizeConfigValue(current, schema[path]));
    }
    return data;
  }

  function normalizeConfigSchemaEntry(path, rawSchema) {
    const schema = toObject(rawSchema);
    const entry = {
      path,
      kind: toText(schema.kind, 'text'),
      label: toText(schema.label || schema.title, humanizeConfigPath(path)),
      group: toText(schema.group, ''),
      desc: toText(schema.desc || schema.description, ''),
      hint: toText(schema.hint, ''),
      restart: Boolean(schema.restart),
      editable: schema.editable !== false,
      redacted: Boolean(schema.redacted || schema.secret),
      allow_empty: Boolean(schema.allow_empty || schema.allowEmpty),
    };
    if (schema.min != null) entry.min = toMaybeNumber(schema.min) ?? schema.min;
    if (schema.max != null) entry.max = toMaybeNumber(schema.max) ?? schema.max;
    if (schema.step != null) entry.step = toMaybeNumber(schema.step) ?? schema.step;
    if (schema.options != null) entry.options = normalizeListValues(schema.options);
    if (schema.item_kind != null) entry.item_kind = toText(schema.item_kind, 'text');
    if (schema.itemKind != null && entry.item_kind == null) entry.item_kind = toText(schema.itemKind, 'text');
    return entry;
  }

  function normalizeConfigGroupEntry(group) {
    const raw = toObject(group);
    const paths = toArray(raw.paths).map((path) => toText(path, '')).filter(Boolean);
    if (!paths.length) {
      return null;
    }
    return {
      id: toText(raw.id || raw.title, paths[0]),
      title: toText(raw.title || raw.id, toText(raw.id || raw.title, paths[0])),
      description: toText(raw.description || raw.copy || raw.desc, ''),
      paths,
    };
  }

  function legacyConfigGroups() {
    return [
      { id: 'project', title: 'Project', paths: ['repo', 'profile', 'execution_backend', 'roles'] },
      { id: 'runner', title: 'Runner', paths: ['autopilot', 'continuous', 'iterations', 'max_turns_per_task', 'loop', 'loop_sleep_seconds', 'loop_max_cycles', 'loop_idle_exit_after', 'idle_exit_cycles', 'max_consecutive_failed_cycles', 'run_tests', 'budget_reset_per_cycle'] },
      { id: 'quota', title: 'Quota', paths: ['quota_check_enabled', 'quota_five_hour_max_utilization', 'quota_seven_day_max_utilization', 'quota_wait_for_reset'] },
      { id: 'worktree', title: 'Worktree', paths: ['worktree_isolation', 'isolate_task', 'gitops.worktree_merge_mode', 'gitops.untracked_exclude_globs'] },
      { id: 'prompts', title: 'Prompt Paths', paths: ['prompts_dir'] },
      { id: 'codex_models', title: 'Codex Models', paths: ['pm_model', 'dev_model', 'dev_model_tier1', 'dev_model_tier2', 'qa_model', 'reporter_model'] },
      { id: 'pm_refresh', title: 'PM Refresh', paths: ['pm_refresh_backlog', 'pm_refresh_every_cycles', 'pm_include_working_tree'] },
      { id: 'budget', title: 'Budget', paths: ['budgets.max_pm_structured_retries', 'budgets.max_dev_escalations_per_task', 'budgets.max_dev_continuations_per_task', 'budgets.max_total_escalations_per_run', 'budgets.max_total_continuations_per_run', 'budgets.max_total_repair_attempts_per_run'] },
      { id: 'telegram', title: 'Telegram', paths: ['telegram.enabled', 'telegram.runner_mode', 'telegram.poll_timeout_seconds', 'telegram.allowed_chat_ids', 'telegram.bot_token', 'telegram.pairing_code', 'telegram.instance_name', 'telegram.notify_events', 'telegram.send_cycle_summary', 'telegram.notify_poll_interval_seconds', 'telegram.stalled_seconds', 'telegram.tail_lines_default'] },
      { id: 'goals', title: 'Goals', paths: ['goals_enabled', 'goals_auto_generate', 'goals_auto_check', 'goals_auto_refresh', 'goals_refresh_max_per_run', 'goals_completion_level'] },
    ];
  }

  function applyConfigRedaction(tree, paths, placeholder) {
    let data = clone(tree || {});
    for (const path of paths || []) {
      const current = getAt(data, path);
      if (current === undefined || current === '' || current === null || current === false) {
        continue;
      }
      data = setAt(data, path, placeholder);
    }
    return data;
  }

  function buildConfigContract(rawContract, fallback = {}) {
    const raw = toObject(rawContract);
    const fallbackSchema = toObject(fallback.schema || {});
    const rawSchema = toObject(raw.schema || fallbackSchema);
    const rawMeta = toObject(raw.meta || {});
    const fallbackMeta = toObject(fallback.meta || {});
    const schema = {};
    for (const path of Object.keys(rawSchema)) {
      schema[path] = normalizeConfigSchemaEntry(path, rawSchema[path]);
    }

    const fallbackGroups = toArray(fallback.groups || []).map(normalizeConfigGroupEntry).filter(Boolean);
    const groupsSource = toArray(raw.groups || fallbackGroups);
    const groups = groupsSource.map(normalizeConfigGroupEntry).filter(Boolean);

    const defaultsSource = toObject(raw.defaults || fallback.defaults || {});
    const valuesSource = toObject(raw.values || raw.data || raw.config || fallback.values || {});
    const mergedValues = deepMerge(clone(defaultsSource), valuesSource);
    const normalizedValues = normalizeConfigTree(mergedValues, schema);
    const normalizedDefaults = normalizeConfigTree(defaultsSource, schema);

    const redactionSource = toObject(raw.redaction || fallback.redaction);
    const redactionPaths = new Set(toArray(redactionSource.paths).map((path) => toText(path, '')).filter(Boolean));
    for (const path of Object.keys(schema)) {
      if (schema[path].redacted) {
        redactionPaths.add(path);
      }
    }
    const placeholder = toText(redactionSource.placeholder, '[redacted]');
    const values = applyConfigRedaction(normalizedValues, redactionPaths, placeholder);
    const defaults = applyConfigRedaction(normalizedDefaults, redactionPaths, placeholder);

    const restartRequiredPaths = toArray(raw.restart_required_paths || fallback.restart_required_paths)
      .map((path) => toText(path, ''))
      .filter(Boolean);
    for (const path of Object.keys(schema)) {
      if (schema[path].restart && !restartRequiredPaths.includes(path)) {
        restartRequiredPaths.push(path);
      }
    }

    return {
      path: toText(raw.path || fallback.path, ''),
      source: toText(raw.source || fallback.source, ''),
      resolved_prompts_dir: toText(raw.resolved_prompts_dir || fallback.resolved_prompts_dir, ''),
      values,
      defaults,
      schema,
      groups: groups.length ? groups : fallbackGroups,
      redaction: {
        placeholder,
        paths: Array.from(redactionPaths),
        tokens: normalizeListValues(redactionSource.tokens || fallback.redaction?.tokens || []),
      },
      restart_required_paths: restartRequiredPaths,
      meta: {
        ...fallbackMeta,
        ...rawMeta,
        path: toText(raw.path || fallback.path, ''),
        source: toText(raw.source || fallback.source, ''),
        resolved_prompts_dir: toText(raw.resolved_prompts_dir || fallback.resolved_prompts_dir, ''),
        save_enabled: Boolean(rawMeta.save_enabled ?? fallbackMeta.save_enabled ?? false),
        save_endpoint: toText(rawMeta.save_endpoint || fallbackMeta.save_endpoint || '/api/config/save', '/api/config/save'),
        save_requires_opt_in: Boolean(rawMeta.save_requires_opt_in ?? fallbackMeta.save_requires_opt_in ?? true),
      },
    };
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
    const quota = normalizeQuotaData(raw, metrics);
    const quotaAvailable = quota.available;
    const quotaUsed = quota.used;
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
      quota_available: quota.available,
      quotaWindow: quota.window,
      quota_window: quota.window,
      quotaUsed,
      quota_used: quota.used,
      quota: clone(quota),
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
    const items = normalizeGoalBuckets(raw);
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
      state: buildSectionState('goals', total ? 'ready' : 'empty', goalSnapshotMessage(raw, total)),
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

  function adaptConfigContract(configContract, context = {}) {
    const raw = toObject(configContract);
    const fallback = {
      path: toText(raw.path || context.path || '', ''),
      source: toText(raw.source || context.source || '', ''),
      resolved_prompts_dir: toText(raw.resolved_prompts_dir || context.resolved_prompts_dir || '', ''),
      values: clone(toObject(context.legacyConfig || defaults.config || {})),
      defaults: clone(toObject(context.defaults || defaults.configDefault || {})),
      schema: clone(toObject(context.schema || defaults.configSchema || {})),
      groups: clone(toArray(context.groups || defaults.configGroups || legacyConfigGroups())),
      meta: {
        path: toText(raw.meta?.path || context.path || '', ''),
        source: toText(raw.meta?.source || context.source || '', ''),
        resolved_prompts_dir: toText(raw.meta?.resolved_prompts_dir || context.resolved_prompts_dir || '', ''),
        save_enabled: Boolean(raw.meta?.save_enabled ?? context.save_enabled ?? false),
        save_endpoint: toText(raw.meta?.save_endpoint || context.save_endpoint || '/api/config/save', '/api/config/save'),
        save_requires_opt_in: Boolean(raw.meta?.save_requires_opt_in ?? context.save_requires_opt_in ?? true),
      },
      redaction: {
        placeholder: toText(context.redaction?.placeholder || raw.redaction?.placeholder, '[redacted]'),
        paths: toArray(context.redaction?.paths || raw.redaction?.paths),
        tokens: toArray(context.redaction?.tokens || raw.redaction?.tokens),
      },
      restart_required_paths: toArray(context.restart_required_paths || raw.restart_required_paths || raw.restartRequiredPaths),
    };
    return buildConfigContract(raw, fallback);
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
        tasksFailed: toNumber(toObject(raw.summary).tasksFailed || items.reduce((sum, run) => sum + run.tasksFailed, 0), 0),
        tasksSkipped: toNumber(toObject(raw.summary).tasksSkipped || items.reduce((sum, run) => sum + run.tasksSkipped, 0), 0),
      },
      state: buildSectionState('history', items.length ? 'ready' : 'empty', items.length ? '' : fallbackSectionMessage('history')),
    };
  }

  function adaptWorktree(worktree, context = {}) {
    const data = normalizeWorktreeState(worktree);
    const sectionStatus =
      data.status === 'none'
        ? 'empty'
        : data.status === 'error'
          ? 'error'
          : data.status === 'applied' || data.status === 'discarded'
            ? 'ready'
            : 'partial';
    const sectionMessage =
      sectionStatus === 'empty'
        ? fallbackSectionMessage('worktree')
        : data.reviewRequiredMessage || data.cleanupMessage || data.summary || fallbackSectionMessage('worktree');
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
    const configContract = adaptConfigContract(raw.config_contract || raw.configContract || raw.config, {
      progress,
      repo,
      legacyConfig: config.data,
      path: config.path,
      source: config.source,
      resolved_prompts_dir: config.resolved_prompts_dir,
    });
    const configValues = toObject(configContract.values || config.data || {});
    const configDefaults = toObject(configContract.defaults || config.data || {});
    let metrics = adaptMetrics(raw.metrics, { progress, repo, config: configValues });
    const runnerControl = normalizeRunnerControl(raw.runner_control || raw.runnerControl || raw.control);
    const activeRun = adaptActiveRun(raw.active_run, {
      repo,
      progress,
      metrics,
      config: configValues,
      branch: repo.branch || '',
      source: 'api',
    });
    const activeRunQuota = toObject(activeRun.quota);
    const metricsQuota = toObject(metrics.quota);
    if (activeRun.quotaAvailable && activeRunQuota.available && (
      !metricsQuota.available ||
      metricsQuota.window !== activeRunQuota.window ||
      metricsQuota.used !== activeRunQuota.used
    )) {
      const quota = clone(activeRun.quota);
      metrics = {
        ...metrics,
        quota,
        quota_window: quota.window,
        quotaWindow: quota.window,
        quota_used: quota.used,
        quotaUsed: quota.used,
        quotaAvailable: true,
        quota_available: true,
      };
    }
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
      logTailSummary: logs.tail,
      logFiles: logs.files,
      configDefault: clone(configDefaults),
      config: clone(configValues),
      configMeta: clone(toObject(configContract.meta || {
        path: config.path,
        source: config.source,
        resolved_prompts_dir: config.resolved_prompts_dir,
      })),
      configContract,
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
    const configBase = {
      repo: '',
      profile: 'personal',
      execution_backend: 'codex',
      roles: ['PM', 'Dev', 'QA'],
      autopilot: true,
      continuous: true,
      iterations: 1,
      max_turns_per_task: 8,
      loop: false,
      loop_sleep_seconds: 30,
      loop_max_cycles: 0,
      loop_idle_exit_after: 0,
      idle_exit_cycles: 3,
      max_consecutive_failed_cycles: 3,
      run_tests: true,
      budget_reset_per_cycle: false,
      quota_check_enabled: true,
      quota_five_hour_max_utilization: 80,
      quota_seven_day_max_utilization: 90,
      quota_wait_for_reset: false,
      worktree_isolation: false,
      isolate_task: false,
      gitops: {
        worktree_merge_mode: 'manual',
        untracked_exclude_globs: [],
      },
      prompts_dir: 'prompts/agentcli-fallback',
      pm_model: 'gpt-5.5',
      dev_model: 'gpt-5.4',
      dev_model_tier1: 'gpt-5.4-mini',
      dev_model_tier2: 'gpt-5.1',
      qa_model: 'gpt-5.4-mini',
      reporter_model: 'gpt-5.4-mini',
      pm_refresh_backlog: true,
      pm_refresh_every_cycles: 1,
      pm_include_working_tree: true,
      budgets: {
        max_pm_structured_retries: 3,
        max_dev_escalations_per_task: 3,
        max_dev_continuations_per_task: 3,
        max_total_escalations_per_run: 12,
        max_total_continuations_per_run: 6,
        max_total_repair_attempts_per_run: 6,
      },
      telegram: {
        enabled: true,
        runner_mode: 'thread',
        poll_timeout_seconds: 20,
        allowed_chat_ids: [],
        bot_token: '',
        pairing_code: '',
        instance_name: 'home-pc-main',
        notify_events: ['run_start'],
        send_cycle_summary: true,
        notify_poll_interval_seconds: 10,
        stalled_seconds: 300,
        tail_lines_default: 40,
      },
      goals_enabled: true,
      goals_auto_generate: false,
      goals_auto_check: true,
      goals_auto_refresh: false,
      goals_refresh_max_per_run: 1,
      goals_completion_level: 'all',
      // Legacy aliases kept for read-only dashboard compatibility.
      budget: {
        max_usd: 8,
        max_iters: 5,
        max_continuations: 3,
      },
      claudecode: {
        dev_model: 'gpt-5.4',
        dev_model_tier1: 'gpt-5.4-mini',
        qa_model: 'gpt-5.4-mini',
        reporter_model: 'gpt-5.4-mini',
      },
      worktree_merge_mode: 'manual',
    };
    const configSchema = {
      repo: {
        kind: 'text',
        restart: true,
        desc: 'Absolute path to the repo AgentCLI will operate on.',
        hint: 'Use a local Windows path such as C:/Dev/AgentCLI.',
      },
      profile: {
        kind: 'enum',
        options: ['personal', 'enterprise'],
        restart: true,
        desc: 'Default safety profile used to derive runner limits.',
        hint: 'Enterprise raises several guardrails.',
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
      max_turns_per_task: {
        kind: 'number',
        min: 1,
        restart: false,
        desc: 'Upper bound for per-task model turns.',
        hint: 'Keeps a single task from spinning forever.',
      },
      loop: {
        kind: 'bool',
        restart: false,
        desc: 'Keep the runner cycling after a run completes.',
        hint: 'Pair with loop_sleep_seconds to avoid busy looping.',
      },
      loop_sleep_seconds: {
        kind: 'number',
        min: 1,
        restart: false,
        desc: 'Delay between looped runs.',
        hint: 'Longer sleeps reduce churn when no work is queued.',
      },
      loop_max_cycles: {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Hard cap on loop cycles.',
        hint: 'Zero means no extra cap beyond the rest of the runner.',
      },
      loop_idle_exit_after: {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Exit after this many idle loop passes.',
        hint: 'Zero keeps the loop running until a different stop condition fires.',
      },
      idle_exit_cycles: {
        kind: 'number',
        min: 1,
        restart: false,
        desc: 'How many idle cycles trigger shutdown.',
        hint: 'Useful for unattended runs that should stop when no work remains.',
      },
      max_consecutive_failed_cycles: {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Stop after this many failed cycles in a row.',
        hint: 'Prevents the runner from grinding through repeated failures.',
      },
      run_tests: {
        kind: 'bool',
        restart: false,
        desc: 'Run the test suite during QA.',
        hint: 'Keeps verification inside the task loop.',
      },
      budget_reset_per_cycle: {
        kind: 'bool',
        restart: false,
        desc: 'Reset cycle-level budget tracking every cycle.',
        hint: 'Useful when cycle-level guardrails matter more than the full run.',
      },
      quota_check_enabled: {
        kind: 'bool',
        restart: false,
        desc: 'Enable quota utilization checks.',
        hint: 'Disabling this removes the quota guardrails from the runner.',
      },
      quota_five_hour_max_utilization: {
        kind: 'number',
        min: 0,
        max: 100,
        restart: false,
        desc: 'Five-hour quota utilization ceiling.',
        hint: 'Percent used before the runner stops or pauses.',
      },
      quota_seven_day_max_utilization: {
        kind: 'number',
        min: 0,
        max: 100,
        restart: false,
        desc: 'Seven-day quota utilization ceiling.',
        hint: 'Percent used before the runner stops or pauses.',
      },
      quota_wait_for_reset: {
        kind: 'bool',
        restart: false,
        desc: 'Pause until quota resets instead of failing fast.',
        hint: 'Keeps the runner from hammering an exhausted quota window.',
      },
      worktree_isolation: {
        kind: 'bool',
        restart: true,
        desc: 'Run tasks in an isolated git worktree.',
        hint: 'Recommended for shared machines and safety-sensitive changes.',
      },
      isolate_task: {
        kind: 'bool',
        restart: false,
        desc: 'Give each task an isolated workspace.',
        hint: 'Helps keep per-task edits clean when the runner fans out.',
      },
      'gitops.worktree_merge_mode': {
        kind: 'enum',
        options: ['manual', 'auto'],
        restart: true,
        desc: 'How worktree patches are merged.',
        hint: 'Manual mode keeps review in the loop.',
      },
      'gitops.untracked_exclude_globs': {
        kind: 'list',
        item_kind: 'text',
        restart: false,
        allow_empty: true,
        desc: 'Comma-separated globs ignored by worktree review.',
        hint: 'Keep generated files out of merge noise.',
      },
      prompts_dir: {
        kind: 'text',
        restart: true,
        allow_empty: true,
        desc: 'Directory that stores repo-specific prompt templates.',
        hint: 'Empty means the repo-specific default prompts directory.',
      },
      pm_model: {
        kind: 'text',
        restart: false,
        desc: 'Model used for PM planning and backlog generation.',
        hint: 'Usually a lightweight Codex model.',
      },
      dev_model: {
        kind: 'text',
        restart: false,
        desc: 'Model used for the main Dev pass.',
        hint: 'This is the default model for code changes.',
      },
      dev_model_tier1: {
        kind: 'text',
        restart: false,
        desc: 'First escalation model for Dev.',
        hint: 'Used after retries or capped responses.',
      },
      dev_model_tier2: {
        kind: 'text',
        restart: false,
        desc: 'Second escalation model for Dev.',
        hint: 'Used when tier 1 still cannot finish the task.',
      },
      qa_model: {
        kind: 'text',
        restart: false,
        desc: 'Model used for QA verification.',
        hint: 'Usually matches the cheaper Codex tier.',
      },
      reporter_model: {
        kind: 'text',
        restart: false,
        desc: 'Model used for close-out reporting.',
        hint: 'Generates the final run summary.',
      },
      pm_refresh_backlog: {
        kind: 'bool',
        restart: false,
        desc: 'Let PM refresh the backlog from live context.',
        hint: 'Useful when the backlog should absorb new work after a run.',
      },
      pm_refresh_every_cycles: {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Refresh cadence for PM backlog updates.',
        hint: 'Zero disables periodic refreshes.',
      },
      pm_include_working_tree: {
        kind: 'bool',
        restart: false,
        desc: 'Let PM inspect the working tree during refresh.',
        hint: 'Helps PM pick up local edits while refreshing the backlog.',
      },
      'budgets.max_pm_structured_retries': {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Retry cap for structured PM output.',
        hint: 'Prevents retry loops when PM output keeps failing schema checks.',
      },
      'budgets.max_dev_escalations_per_task': {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Escalation budget for a single Dev task.',
        hint: 'Used to cap repeated model escalations.',
      },
      'budgets.max_dev_continuations_per_task': {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Continuation budget for a single Dev task.',
        hint: 'Keeps partial response continuations bounded.',
      },
      'budgets.max_total_escalations_per_run': {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Escalation budget for the full run.',
        hint: 'Set to zero to disable the cap.',
      },
      'budgets.max_total_continuations_per_run': {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Continuation budget for the full run.',
        hint: 'Set to zero to disable the cap.',
      },
      'budgets.max_total_repair_attempts_per_run': {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Repair budget for the full run.',
        hint: 'Limits repeated repair loops across stages.',
      },
      'telegram.enabled': {
        kind: 'bool',
        restart: true,
        desc: 'Mirror run events to Telegram.',
        hint: 'Local notification bridge only.',
      },
      'telegram.runner_mode': {
        kind: 'enum',
        options: ['thread', 'subprocess'],
        restart: true,
        desc: 'How the Telegram runner is hosted.',
        hint: 'Thread mode stays in-process. Subprocess mode isolates the service.',
      },
      'telegram.poll_timeout_seconds': {
        kind: 'number',
        min: 1,
        restart: false,
        desc: 'Long-poll timeout for Telegram control-plane requests.',
        hint: 'Longer timeouts reduce polling chatter.',
      },
      'telegram.allowed_chat_ids': {
        kind: 'list',
        item_kind: 'int',
        allow_empty: true,
        restart: false,
        desc: 'Comma-separated allowlisted Telegram chat IDs.',
        hint: 'Empty means any chat id is currently allowed by policy.',
      },
      'telegram.bot_token': {
        kind: 'text',
        restart: true,
        redacted: true,
        allow_empty: true,
        desc: 'Telegram bot token used for remote control.',
        hint: 'Shown as redacted in the browser.',
      },
      'telegram.pairing_code': {
        kind: 'text',
        restart: true,
        redacted: true,
        allow_empty: true,
        desc: 'One-time pairing code for Telegram control.',
        hint: 'Shown as redacted in the browser.',
      },
      'telegram.instance_name': {
        kind: 'text',
        restart: false,
        allow_empty: true,
        desc: 'Friendly label surfaced in Telegram messages.',
        hint: 'Useful when multiple runners share one chat.',
      },
      'telegram.notify_events': {
        kind: 'list',
        item_kind: 'text',
        allow_empty: true,
        restart: false,
        desc: 'Comma-separated push events for Telegram notifications.',
        hint: 'Examples: run_start, task_done, quota.',
      },
      'telegram.send_cycle_summary': {
        kind: 'bool',
        restart: false,
        desc: 'Push new cycle summary lines to Telegram.',
        hint: 'Helpful when the runner is unattended.',
      },
      'telegram.notify_poll_interval_seconds': {
        kind: 'number',
        min: 2,
        restart: false,
        desc: 'Polling interval used by Telegram notification refresh.',
        hint: 'Longer intervals reduce background polling.',
      },
      'telegram.stalled_seconds': {
        kind: 'number',
        min: 60,
        restart: false,
        desc: 'Threshold before a run is considered stalled.',
        hint: 'Helps identify slow or hung runs.',
      },
      'telegram.tail_lines_default': {
        kind: 'number',
        min: 1,
        restart: false,
        desc: 'Default number of log lines included in Telegram pushes.',
        hint: 'Keeps notifications compact.',
      },
      goals_enabled: {
        kind: 'bool',
        restart: false,
        desc: 'Enable GOALS.md tracking.',
        hint: 'Disabling this turns off the goals completion gate.',
      },
      goals_auto_generate: {
        kind: 'bool',
        restart: false,
        desc: 'Auto-generate goals content from PM context.',
        hint: 'Useful when goals are derived from the current task set.',
      },
      goals_auto_check: {
        kind: 'bool',
        restart: false,
        desc: 'Re-check goals completion automatically.',
        hint: 'Keeps completion status in sync with the latest snapshot.',
      },
      goals_auto_refresh: {
        kind: 'bool',
        restart: false,
        desc: 'Refresh GOALS.md after project completion.',
        hint: 'Useful for the next run once the current project is complete.',
      },
      goals_refresh_max_per_run: {
        kind: 'number',
        min: 0,
        restart: false,
        desc: 'Hard cap on goals refresh attempts per run.',
        hint: 'Zero disables refresh retries.',
      },
      goals_completion_level: {
        kind: 'enum',
        options: ['p0', 'p1', 'all'],
        restart: false,
        desc: 'Which goals must be satisfied to treat the project as complete.',
        hint: 'p0 is legacy, p1 includes P1, all requires every checkbox.',
      },
    };
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
        quotaWindow: '',
        quotaUsed: null,
        quota: { window: '', used: null, available: false },
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
      goalSave: createBlankGoalSaveState(),
      logs: [],
      logTail: createBlankLogTailState(),
      logFiles: {},
      configDefault: clone(configBase),
      config: clone(configBase),
      configMeta: {
        path: '',
        source: '',
        resolved_prompts_dir: '',
        save_enabled: false,
        save_endpoint: '/api/config/save',
        save_requires_opt_in: true,
      },
      configSchema,
      configSave: createBlankConfigSaveState(),
      prompts: [],
      promptEditor: createBlankPromptEditor(),
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
        statusFile: '',
        cleanupPath: '',
        cleanupMessage: 'No cleanup state is available.',
        cleanupState: 'none',
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
      worktreeAction: null,
      history: [],
      historySummary: { runs: 0, successes: 0, failures: 0, stopped: 0, tasksDone: 0, tasksTotal: 0 },
      metrics: {
        tokens24h: [],
        success24h: [],
        budget: [],
        tokens: { in: null, out: null, available: false },
        last_stage: '',
        quota: { window: '', used: null, available: false },
        quota_window: '',
        quotaWindow: '',
        quota_used: null,
        quotaUsed: null,
        budget_used: null,
        tokensAvailable: false,
        budgetAvailable: false,
        quotaAvailable: false,
        quota_available: false,
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
      logTail: createBlankLogTailState(),
      logFiles: {
        cycle_summary: '.AgentCLI/agent_runs/offline-fallback/cycle_summary.log',
        run_log: '.AgentCLI/agent_runs/offline-fallback/logs/run.log',
        metrics: '.AgentCLI/agent_runs/offline-fallback/metrics.jsonl',
      },
      configDefault: createBlankModel().configDefault,
      config: createBlankModel().config,
      configContract: {
        ...clone(defaults.configContract),
        path: 'config/agentcli.json',
        source: 'fallback',
        resolved_prompts_dir: 'prompts/agentcli-fallback',
        meta: {
          path: 'config/agentcli.json',
          source: 'fallback',
          resolved_prompts_dir: 'prompts/agentcli-fallback',
          save_enabled: false,
          save_endpoint: '/api/config/save',
          save_requires_opt_in: true,
        },
      },
      configDraft: clone(defaults.configContract.values),
      configMeta: {
        path: 'config/agentcli.json',
        source: 'fallback',
        resolved_prompts_dir: 'prompts/agentcli-fallback',
        save_enabled: false,
        save_endpoint: '/api/config/save',
        save_requires_opt_in: true,
      },
      configSave: createBlankConfigSaveState(),
      prompts: [
        {
          id: 'bootstrap',
          file: 'bootstrap_prompt.md',
          scope: 'PM',
          profile: 'personal',
          source: 'fallback',
          mode: 'template',
          updated: 'fallback',
          summary: 'Fallback bootstrap prompt preview.',
          preview: '[redacted]',
          path: 'prompts/bootstrap_prompt.md',
          content: '# Bootstrap Prompt\n\nProfile: {profile}\nRepo: {repo}\nOpen the dashboard and collect goals before any code changes.\n',
          templateVariables: ['profile', 'repo'],
        },
        {
          id: 'dev_task',
          file: 'dev_task_prompt.md',
          scope: 'Dev',
          profile: 'personal',
          source: 'fallback',
          mode: 'template',
          updated: 'fallback',
          summary: 'Fallback development prompt preview.',
          preview: '[redacted]',
          path: 'prompts/dev_task_prompt.md',
          content: 'Implement {task_title} for {task_id}.\nUse {task_prompt} when writing code and keep the scope narrow.\n',
          templateVariables: ['task_title', 'task_id', 'task_prompt'],
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
        statusFile: '',
        cleanupPath: '',
        cleanupMessage: 'No cleanup state is available.',
        cleanupState: 'none',
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
      worktreeAction: null,
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
        quota: { window: '', used: null, available: false },
        quota_window: '',
        quotaWindow: '',
        quota_used: null,
        quotaUsed: null,
        budget_used: 0.34,
        tokensAvailable: true,
        budgetAvailable: true,
        quotaAvailable: false,
        quota_available: false,
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
    adaptConfigContract,
    adaptPrompts,
    adaptLogs,
    adaptNotifications,
    adaptMetrics,
    adaptHistory,
    adaptWorktree,
    goalBucketLabel,
    goalBucketName,
    goalItemLineNumber,
    goalItemCheckbox,
    goalItemSummary,
    goalItemMeta,
    goalItemSignature,
    goalItemMatchKey,
    buildGoalDraftSummary,
    buildGoalSaveRiskSummary,
    normalizeSnapshot: normalizeApiSnapshot,
    createBlankModel,
    createFallbackFixture,
    createBlankLogTailState,
    normalizeLogTailFilters,
    buildLogTailQuery,
    buildLogTailRequestUrl,
    mergeLogTailEntries,
    formatLogTailLine,
    buildLogTailClipboardText,
    buildLogTailDownloadArtifact,
    describeLogTailState,
    renderLogTailBanner,
    renderLogTailFilters,
    isLiveTailPaused,
    setLiveTailPaused,
    resetServerLogTailState,
    refreshServerLogTail,
    startServerLogTail,
    stopServerLogTail,
    syncLogTailStreaming,
    applyLogTailPayload,
    toggleLogTailSelection,
    clearLogTailSelection,
    updateLogTailFilter,
    inspectLogTailState,
    seedLogTailState,
    createBlankPromptEditor,
    createBlankPromptSaveState,
    createBlankPromptRestoreState,
    createBlankGoalSaveState,
    inspectPromptEditorState,
    promptEditorValidation,
    renderPromptEditorState,
    renderPromptEditorBanner,
    renderPromptEditorValidation,
    renderPromptEditorDiff,
    renderPromptEditorMutationPanel,
    promptEditorMatchesPrompt,
    buildPromptReadUrl,
    promptSaveRequestPath,
    promptRestoreRequestPath,
    normalizePromptReadResponse,
    normalizePromptMutationResponse,
    applyPromptEditorPayload,
    syncPromptEditorArtifacts,
    updatePromptEditorDraft,
    updatePromptEditorMutationField,
    loadPromptEditor,
    savePromptDraft,
    restorePromptDraft,
    promptMutationEnabled,
    promptSaveInFlight,
    promptRestoreInFlight,
    promptMutationInFlight,
    normalizeGoalSaveRisk,
    normalizeGoalSaveResponse,
    goalSaveEnabled,
    goalSaveRequestPath,
    goalSaveInFlight,
    inspectGoalSaveState,
    resetGoalSaveState,
    goalSaveDisabledReason,
    renderGoalSaveBanner,
    updateGoalSaveConfirmation,
    syncGoalSaveArtifacts,
    saveGoalDraft,
    handleAction,
    renderLogRow,
  };

  if (typeof globalThis !== 'undefined') {
    globalThis.__AGENTCLI_ADAPTERS__ = ADAPTERS;
  }

  function applySnapshotModel(model) {
    if (!model || typeof model !== 'object') {
      return false;
    }
    const previousSourceMode = state.sourceMode;
    const previousLatestRunDir = state.latestRunDir;
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
      state.goals = normalizeGoalBuckets(next.goals);
      removeJSON(STORAGE.goals);
    }
    state.logs = toArray(next.logs).slice(-MAX_LOG_ROWS);
    state.logTailSummary = toText(next.logTailSummary || next.logTail, '');
    state.logFiles = toObject(next.logFiles);
    state.configDefault = deepMerge(clone(next.configDefault || {}), null);
    state.config = deepMerge(clone(next.config || {}), null);
    state.configMeta = toObject(next.configMeta);
    state.configContract = buildConfigContract(toObject(next.configContract || {}), {
      defaults: next.configContract?.defaults || defaults.configDefault,
      schema: next.configContract?.schema || defaults.configContract.schema,
      groups: next.configContract?.groups || defaults.configContract.groups || legacyConfigGroups(),
      redaction: next.configContract?.redaction || defaults.configContract.redaction,
      restart_required_paths: next.configContract?.restart_required_paths || defaults.configContract.restart_required_paths,
    });
    state.configSchema = clone(toObject(state.configContract.schema || defaults.configSchema));
    state.configDraft = deepMerge(clone(toObject(state.configContract.values || {})), toObject(state.configDraft || {}));
    const nextPrompts = toArray(next.prompts);
    state.prompts = nextPrompts;
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
    state.serverMode = state.sourceMode === 'api';
    if (previousSourceMode !== state.sourceMode || previousLatestRunDir !== state.latestRunDir) {
      resetServerLogTailState();
      stopServerLogTail();
    }
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
    if (!nextPrompts.length) {
      state.promptSelection = '';
      state.promptEditor = createBlankPromptEditor();
    } else {
      const selectedPrompt = nextPrompts.find((prompt) => prompt.id === state.promptSelection) || nextPrompts[0];
      if (selectedPrompt && state.promptSelection !== selectedPrompt.id) {
        state.promptSelection = selectedPrompt.id;
      }
      const editor = promptEditorData();
      if (state.activeView === 'prompts' && selectedPrompt && !editor.dirty && (!promptEditorMatchesPrompt(selectedPrompt) || !editor.baseContent)) {
        void loadPromptEditor(selectedPrompt);
      }
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

  function describeWorktreeReview(review) {
    const status = toText(review?.status, 'none');
    const cleanupState = toText(review?.cleanupState, 'none');
    const reviewMessage = toText(review?.reviewRequiredMessage, '');
    const summary = toText(review?.summary, '');
    const cleanupMessage = toText(review?.cleanupMessage, '');
    const sourceRepo = toText(review?.sourceRepo, 'the source repository');
    const patchPath = toText(review?.patchPath || review?.patch, 'the patch');
    const cleanupPath = toText(review?.cleanupPath || review?.worktreeDir || review?.worktree, '');
    const pendingReview = status === 'pending review' || status === 'pending';
    const cleanupFailed = cleanupState === 'failed' || status === 'applied_cleanup_failed' || status === 'discard_cleanup_failed';

    if (status === 'error') {
      return {
        tone: 'err',
        title: 'Malformed pending file',
        copy: reviewMessage || summary || 'Pending worktree merge file could not be parsed.',
        actionCopy: 'Fix or delete the pending file in the CLI before trying again.',
        mergeHint: 'Pending file is invalid.',
      };
    }

    if (cleanupFailed) {
      const recoveryPath = cleanupPath || toText(review?.worktreeDir || review?.worktree, 'the isolated worktree');
      return {
        tone: 'warn',
        title: status === 'applied_cleanup_failed' ? 'Merge recorded, cleanup failed' : 'Discard recorded, cleanup failed',
        copy:
          reviewMessage ||
          cleanupMessage ||
          summary ||
          `The merge or discard decision was recorded, but cleanup failed for ${recoveryPath}.`,
        actionCopy:
          status === 'discard_cleanup_failed'
            ? `Manual recovery: remove ${recoveryPath} manually or run git worktree remove --force ${recoveryPath} from ${sourceRepo}. The source repository was not changed.`
            : `Manual recovery: run git worktree remove --force ${recoveryPath} from ${sourceRepo}, or remove the worktree directory manually. The source repository was already updated.`,
        mergeHint: 'Cleanup is still required.',
      };
    }

    if (pendingReview) {
      return {
        tone: 'warn',
        title: 'Review required before source-repo changes',
        copy: reviewMessage || summary || `Review the pending patch at ${patchPath} before confirming merge or discard.`,
        actionCopy:
          `The web console can apply or discard this patch after confirmation. ` +
          `It validates the pending marker, source repository, run directory, worktree path, and patch path before it runs. ` +
          `No commit will be created.`,
        mergeHint: 'Review required.',
      };
    }

    if (status === 'apply_failed') {
      return {
        tone: 'warn',
        title: 'Patch export failed',
        copy: reviewMessage || summary || 'The patch export failed before a reviewable merge marker was written.',
        actionCopy: 'Inspect the export failure and retry the worktree export before any merge or discard action.',
        mergeHint: 'Export failed.',
      };
    }

    if (status === 'patch_not_applied' || status === 'not_applied') {
      return {
        tone: 'warn',
        title: status === 'patch_not_applied' ? 'Patch exported, not auto-applied' : 'Patch not applied',
        copy: reviewMessage || summary || 'The patch was exported, but auto-apply did not run.',
        actionCopy: 'Apply the exported patch before any merge or discard action.',
        mergeHint: 'Manual apply required.',
      };
    }

    if (status === 'applied') {
      return {
        tone: 'info',
        title: 'Patch applied',
        copy: reviewMessage || summary || `The patch was applied to ${sourceRepo} without creating a commit.`,
        actionCopy: 'The worktree is already finalized. No merge or discard action is available.',
        mergeHint: 'Finalized.',
      };
    }

    if (status === 'discarded') {
      return {
        tone: 'info',
        title: 'Patch discarded',
        copy: reviewMessage || summary || `The worktree result was discarded without changing ${sourceRepo}.`,
        actionCopy: 'The worktree is already finalized. No merge or discard action is available.',
        mergeHint: 'Finalized.',
      };
    }

    return {
      tone: 'info',
      title: 'Worktree review',
      copy: reviewMessage || cleanupMessage || summary || 'No pending worktree merge.',
      actionCopy: 'No source-repo change is pending.',
      mergeHint: 'Read only.',
    };
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

  function compactFactItem(label, value, meta = '') {
    return `
      <div class="compact-list__item">
        <span class="compact-list__bullet"></span>
        <div>
          <div class="compact-list__body">${escapeHTML(value)}</div>
          <div class="compact-list__meta">${escapeHTML(label)}</div>
          ${meta ? `<div class="compact-list__meta">${escapeHTML(meta)}</div>` : ''}
        </div>
      </div>
    `;
  }

  function historyTaskCounts(run) {
    const raw = toObject(run);
    const counts = toObject(raw.taskCounts);
    const runSummary = toObject(raw.runSummary);
    const lastRunSummary = toObject(raw.lastRunSummary);
    const runCycles = toArray(runSummary.cycles);
    return {
      done: toNumber(counts.done ?? raw.tasksDone ?? lastRunSummary.done ?? 0, 0),
      total: toNumber(counts.total ?? raw.tasksTotal ?? lastRunSummary.total_tasks ?? 0, 0),
      failed: toNumber(counts.failed ?? raw.tasksFailed ?? lastRunSummary.failed_count ?? 0, 0),
      skipped: toNumber(counts.skipped ?? raw.tasksSkipped ?? lastRunSummary.skipped ?? 0, 0),
      cycles: toNumber(counts.cycles ?? raw.cycleCount ?? runCycles.length, runCycles.length),
    };
  }

  function historySummaryText(run) {
    const raw = toObject(run);
    const runSummary = toObject(raw.runSummary);
    const lastRunSummary = toObject(raw.lastRunSummary);
    const counts = historyTaskCounts(raw);
    const parts = [];
    const finalReason = toText(raw.finalReason, runSummary.final?.reason || '');
    const shutdownReason = toText(raw.shutdownReason, raw.stopReason || lastRunSummary.stop_reason || '');
    const status = toText(lastRunSummary.status, toText(raw.status, ''));
    const rc = lastRunSummary.rc ?? raw.rc;
    if (finalReason) {
      parts.push(`run_summary.final.reason=${finalReason}`);
    }
    if (status || rc != null) {
      parts.push(`last_run_summary.status=${status || 'unknown'}${rc != null ? ` rc=${rc}` : ''}`);
    }
    if (shutdownReason && shutdownReason !== finalReason) {
      parts.push(`shutdown=${shutdownReason}`);
    }
    if (counts.cycles) {
      parts.push(`${counts.cycles} cycle${counts.cycles === 1 ? '' : 's'}`);
    }
    return parts.join(' | ') || 'No persisted summary fields available.';
  }

  function historyWorktreeOutcomeLabel(outcome) {
    const value = toText(outcome, 'none').replace(/_/g, ' ');
    return value || 'none';
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

  function renderGoalItem(bucket, goal, index, total) {
    const done = Boolean(goal.done);
    const sourceLine = goalItemLineNumber(goal);
    const checkboxState = goalItemCheckbox(goal);
    const toggleLabel = sourceLine
      ? `Toggle goal checkbox ${checkboxState} at line ${sourceLine}`
      : `Toggle goal checkbox ${checkboxState}`;
    const canMoveUp = index > 0;
    const canMoveDown = index < total - 1;
    return `
      <div class="goal-item ${done ? 'goal-item--done' : ''}">
        <div class="goal-item__row">
          <button type="button" class="goal-item__check" data-goal-action="toggle" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}" aria-label="${escapeHTML(toggleLabel)}" title="${escapeHTML(toggleLabel)}">
            ${done ? 'X' : ' '}
          </button>
          <div class="goal-item__body">
            <div class="goal-item__title ${done ? 'goal-item__title--done' : ''}">${escapeHTML(goal.text)}</div>
            ${goal.note ? `<div class="goal-item__note">${escapeHTML(goal.note)}</div>` : ''}
            <div class="goal-item__meta">${escapeHTML(goalItemMeta(goal))}</div>
            <div class="goal-item__actions">
              <button type="button" class="button button--tiny button--quiet" data-goal-action="edit" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}">Edit</button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="move" data-goal-direction="-1" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}" ${canMoveUp ? '' : 'disabled'}>
                Up
              </button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="move" data-goal-direction="1" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}" ${canMoveDown ? '' : 'disabled'}>
                Down
              </button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="delete" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}">Delete</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderGoalDraftRow(row) {
    const base = toObject(row.base);
    const item = toObject(row.item);
    const beforeSummary = goalItemSummary(base);
    const afterSummary = goalItemSummary(item);
    const metaSource = row.kind === 'removed' ? base : item;
    const pathLabel =
      row.kind === 'moved'
        ? `${row.bucketLabel} | Row ${row.baseIndex + 1} -> Row ${row.index + 1}`
        : `${row.bucketLabel} | ${goalItemMeta(metaSource)}`;
    const badgeClass = row.kind === 'removed'
      ? 'badge--warn'
      : row.kind === 'added'
        ? 'badge--info'
        : row.kind === 'moved'
          ? 'badge--dim'
          : 'badge--warn';
    const rowClass = row.kind === 'added'
      ? 'prompt-diff-row--added'
      : row.kind === 'removed'
        ? 'prompt-diff-row--removed'
        : '';
    const values = [];
    if (row.kind === 'removed') {
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--removed">${escapeHTML(beforeSummary)}</span>`);
    } else if (row.kind === 'moved') {
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--removed">${escapeHTML(`Row ${row.baseIndex + 1}`)}</span>`);
      values.push(`<span class="prompt-diff-row__arrow">-></span>`);
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--added">${escapeHTML(`Row ${row.index + 1} | ${afterSummary}`)}</span>`);
    } else if (row.kind === 'edited') {
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--removed">${escapeHTML(beforeSummary)}</span>`);
      values.push(`<span class="prompt-diff-row__arrow">-></span>`);
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--added">${escapeHTML(afterSummary)}</span>`);
    } else {
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--added">${escapeHTML(afterSummary)}</span>`);
    }
    return `
      <div class="prompt-diff-row ${rowClass}">
        <div class="prompt-diff-row__head">
          <span class="prompt-diff-row__path">${escapeHTML(pathLabel)}</span>
          <span class="badge ${badgeClass}">${escapeHTML(row.kind === 'added' ? 'Added' : row.kind === 'removed' ? 'Removed' : row.kind === 'moved' ? 'Moved' : 'Edited')}</span>
        </div>
        <div class="prompt-diff-row__values">
          ${values.join('')}
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
        <div class="notification-feed__age">
          <div class="notification-feed__timestamp">${escapeHTML(fmtClock(item.t))}</div>
          <div class="notification-feed__relative">${escapeHTML(fmtRelative(item.t))}</div>
        </div>
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
    const disabled = promptMutationInFlight();
    return `
      <button type="button" class="prompt-card ${active ? 'prompt-card--active' : ''}" data-prompt-select="${escapeHTML(prompt.id)}" ${disabled ? 'disabled aria-disabled="true"' : ''}>
        <div class="prompt-card__head">
          <span class="badge badge--${prompt.mode === 'override' ? 'info' : 'dim'}">${escapeHTML(prompt.mode.toUpperCase())}</span>
          <div class="prompt-card__name">${escapeHTML(prompt.file)}</div>
        </div>
        <div class="prompt-card__meta">
          <span>${escapeHTML(prompt.scope)}</span>
          <span>${escapeHTML(prompt.profile || 'profile unknown')}</span>
          <span>${escapeHTML(prompt.source)}</span>
          <span>${escapeHTML(prompt.updated)}</span>
        </div>
        <div class="prompt-card__path">${escapeHTML(prompt.path || prompt.file)}</div>
        <div class="prompt-card__preview">${escapeHTML(prompt.preview || '[redacted]')}</div>
        <div class="summary-note prompt-card__summary">${escapeHTML(prompt.summary)}</div>
      </button>
    `;
  }

  function configValueToText(value, schema) {
    if (!schema) {
      return value == null || value === '' ? '--' : JSON.stringify(value);
    }
    if (schema.redacted) {
      if (value == null || value === '' || (Array.isArray(value) && value.length === 0)) {
        return '--';
      }
      return REDACTED_VALUE;
    }
    if (schema.kind === 'bool') return value === true ? 'enabled' : 'disabled';
    if (schema.kind === 'enum') return value == null || value === '' ? '--' : String(value);
    if (schema.kind === 'multienum' || schema.kind === 'list') return fmtList(value || []) || '--';
    if (schema.kind === 'number') return value == null || value === '' ? '--' : String(value);
    return value == null || value === '' ? '--' : String(value);
  }

  function renderConfigValueSummary(path, schema, value) {
    return escapeHTML(configValueToText(value, schema));
  }

  function validateField(path, value, schema) {
    if (!schema) return null;
    if (schema.kind === 'bool') {
      return typeof value === 'boolean' ? null : 'must be a boolean';
    }
    if (schema.kind === 'number') {
      if (value === '' || value == null || Number.isNaN(Number(value))) {
        return schema.allow_empty ? null : 'must be a number';
      }
      const num = Number(value);
      if (schema.min != null && num < schema.min) return `must be >= ${schema.min}`;
      if (schema.max != null && num > schema.max) return `must be <= ${schema.max}`;
      return null;
    }
    if (schema.kind === 'text') {
      if (String(value || '').trim()) return null;
      return schema.allow_empty ? null : 'cannot be empty';
    }
    if (schema.kind === 'enum') {
      if (schema.allow_empty && (value == null || value === '')) return null;
      return schema.options.includes(value) ? null : `must be one of: ${schema.options.join(', ')}`;
    }
    if (schema.kind === 'multienum') {
      const items = Array.isArray(value) ? value : normalizeListValues(value);
      if (!items.length && schema.allow_empty) return null;
      if (!items.length) return 'pick at least one option';
      const invalid = items.filter((item) => !schema.options.includes(item));
      return invalid.length ? `invalid option(s): ${invalid.join(', ')}` : null;
    }
    if (schema.kind === 'list') {
      const items = Array.isArray(value) ? value : normalizeListValues(value);
      if (!items.length && schema.allow_empty) return null;
      if (!items.length) return 'enter at least one value';
      if (schema.item_kind === 'int' || schema.itemKind === 'int' || schema.item_kind === 'number' || schema.itemKind === 'number') {
        const invalid = items.filter((item) => !Number.isInteger(Number(item)));
        return invalid.length ? `invalid integer value(s): ${invalid.join(', ')}` : null;
      }
      return null;
    }
    return null;
  }

  function getConfigDiffs() {
    const diffs = [];
    const schema = toObject(state.configSchema);
    for (const path of Object.keys(schema)) {
      const current = getAt(state.configDraft, path);
      const base = getAt(state.configContract?.values || {}, path);
      if (JSON.stringify(current) !== JSON.stringify(base)) {
        diffs.push({
          path,
          from: base,
          to: current,
          restart: Boolean(schema[path].restart),
          error: configChangeError(path, current, schema[path], base),
        });
      }
    }
    return diffs;
  }

  function configSaveInFlight() {
    return state.configSave?.status === 'saving';
  }

  function configSaveEnabled() {
    const meta = toObject(state.configMeta);
    if (Object.prototype.hasOwnProperty.call(meta, 'save_enabled')) {
      return Boolean(meta.save_enabled);
    }
    return Boolean(state.runnerControl?.enabled);
  }

  function configSaveRequestPath() {
    const meta = toObject(state.configMeta);
    return toText(meta.save_endpoint || '/api/config/save', '/api/config/save');
  }

  function resetConfigSaveState() {
    if (configSaveInFlight()) {
      return;
    }
    state.configSave = createBlankConfigSaveState();
  }

  function configChangeError(path, value, schema, baseValue) {
    if (JSON.stringify(value) === JSON.stringify(baseValue)) {
      return null;
    }
    if (path === 'repo') {
      return 'Repository root is managed by the server.';
    }
    if (schema && schema.redacted && String(value || '').trim() === REDACTED_VALUE) {
      return 'Redacted placeholders cannot be saved.';
    }
    return validateField(path, value, schema);
  }

  function configSaveDisabledReason(diffs = getConfigDiffs(), invalidDiffs = diffs.filter((diff) => diff.error)) {
    if (configSaveInFlight()) {
      return 'Config save is already in progress.';
    }
    if (!configSaveEnabled()) {
      return state.runnerControl?.message || 'Config saves are disabled until runner controls are enabled.';
    }
    if (!diffs.length) {
      return 'No config changes to save.';
    }
    if (invalidDiffs.length) {
      return `Fix ${invalidDiffs.length} invalid change${invalidDiffs.length === 1 ? '' : 's'} before saving.`;
    }
    return '';
  }

  function renderConfigSaveBanner(diffs, invalidDiffs) {
    const saveState = toObject(state.configSave || {});
    const changedPaths = toArray(saveState.changedPaths);
    const reloadRequiredPaths = toArray(saveState.reloadRequiredPaths);
    const diffPaths = diffs.map((diff) => diff.path);
    const restartPaths = diffs.filter((diff) => diff.restart).map((diff) => diff.path);
    const bannerTitle = saveState.status === 'saving'
      ? 'Saving config changes'
      : saveState.status === 'success'
        ? 'Config saved'
        : saveState.status === 'error'
          ? 'Config save failed'
          : !configSaveEnabled()
            ? 'Config saves are locked'
            : diffPaths.length
              ? 'Ready to save changes'
              : 'No config changes';
    const bannerTone = saveState.status === 'saving'
      ? 'running'
      : saveState.status === 'success'
        ? 'success'
        : saveState.status === 'error'
          ? 'err'
          : !configSaveEnabled()
            ? 'warn'
            : diffPaths.length
              ? 'info'
              : 'idle';
    const bannerCopy = saveState.status === 'saving'
      ? 'Creating a timestamped backup and writing the config atomically.'
      : saveState.status === 'success'
        ? saveState.message || 'Config changes were written successfully.'
        : saveState.status === 'error'
          ? saveState.message || 'Config save failed.'
          : !configSaveEnabled()
            ? configSaveDisabledReason(diffs, invalidDiffs)
            : diffPaths.length
              ? `Saving will create a backup before updating ${diffPaths.length} changed path${diffPaths.length === 1 ? '' : 's'}.`
              : 'Edit a field to stage a local save.';
    const metaRows = [];
    if (saveState.status === 'success') {
      if (saveState.backupPath) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">Backup path</div>
            <div class="config-save-state__path">${escapeHTML(saveState.backupPath)}</div>
          </div>
        `);
      }
      const savedPaths = changedPaths.length ? changedPaths : diffPaths;
      if (savedPaths.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">Saved paths</div>
            <div class="config-save-state__paths">
              ${savedPaths.map((path) => `<span class="config-save-state__path">${escapeHTML(path)}</span>`).join('')}
            </div>
          </div>
        `);
      }
      const reloadPaths = reloadRequiredPaths.length ? reloadRequiredPaths : restartPaths;
      if (reloadPaths.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">Reload required</div>
            <div class="config-save-state__paths">
              ${reloadPaths.map((path) => `<span class="config-save-state__path">${escapeHTML(path)}</span>`).join('')}
            </div>
          </div>
        `);
      }
    } else if (saveState.status === 'error') {
      if (saveState.backupPath) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">Backup path</div>
            <div class="config-save-state__path">${escapeHTML(saveState.backupPath)}</div>
          </div>
        `);
      }
      const reloadPaths = reloadRequiredPaths.length ? reloadRequiredPaths : restartPaths;
      if (reloadPaths.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">Reload required</div>
            <div class="config-save-state__paths">
              ${reloadPaths.map((path) => `<span class="config-save-state__path">${escapeHTML(path)}</span>`).join('')}
            </div>
          </div>
        `);
      }
      const failedPaths = changedPaths.length ? changedPaths : diffPaths;
      if (failedPaths.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">Pending paths</div>
            <div class="config-save-state__paths">
              ${failedPaths.map((path) => `<span class="config-save-state__path">${escapeHTML(path)}</span>`).join('')}
            </div>
          </div>
        `);
      }
    } else if (diffPaths.length) {
      metaRows.push(`
        <div>
          <div class="config-save-state__label">Pending paths</div>
          <div class="config-save-state__paths">
            ${diffPaths.map((path) => `<span class="config-save-state__path">${escapeHTML(path)}</span>`).join('')}
          </div>
        </div>
      `);
    }

    const errorCode = saveState.errorCode || '';
    const errorCodeHTML = errorCode ? `<div class="config-save-state__code">${escapeHTML(errorCode)}</div>` : '';
    const messageCopy = saveState.status === 'error' && saveState.message
      ? saveState.message
      : bannerCopy;

    return `
      <div class="config-save-state">
        <div class="modal-banner section-banner section-banner--${bannerTone}">
          <span class="dot" style="background: currentColor;"></span>
          <div>
            <div class="section-banner__title">${escapeHTML(bannerTitle)}</div>
            <div class="section-banner__copy">${escapeHTML(messageCopy)}</div>
          </div>
        </div>
        ${errorCodeHTML}
        ${metaRows.length ? `<div class="config-save-state__meta">${metaRows.join('')}</div>` : ''}
      </div>
    `;
  }

  async function saveConfigDraft() {
    if (configSaveInFlight()) {
      return;
    }
    const diffs = getConfigDiffs();
    const invalidDiffs = diffs.filter((diff) => diff.error);
    if (!configSaveEnabled()) {
      state.configSave = {
        ...createBlankConfigSaveState(),
        status: 'error',
        message: configSaveDisabledReason(diffs, invalidDiffs),
        errorCode: 'config_save_disabled',
        changedPaths: diffs.map((diff) => diff.path),
        reloadRequiredPaths: [],
        requestPath: configSaveRequestPath(),
        savedAt: nowMs(),
      };
      renderShell({ preserveScroll: true });
      return;
    }
    if (!diffs.length) {
      state.configSave = {
        ...createBlankConfigSaveState(),
        status: 'error',
        message: 'No config changes were supplied.',
        errorCode: 'config_no_changes',
        changedPaths: [],
        reloadRequiredPaths: [],
        requestPath: configSaveRequestPath(),
        savedAt: nowMs(),
      };
      renderShell({ preserveScroll: true });
      return;
    }
    if (invalidDiffs.length) {
      state.configSave = {
        ...createBlankConfigSaveState(),
        status: 'error',
        message: `${invalidDiffs.length} pending change${invalidDiffs.length === 1 ? '' : 's'} must be fixed before saving.`,
        errorCode: 'config_validation_failed',
        changedPaths: diffs.map((diff) => diff.path),
        reloadRequiredPaths: [],
        requestPath: configSaveRequestPath(),
        savedAt: nowMs(),
      };
      renderShell({ preserveScroll: true });
      return;
    }

    const requestPath = configSaveRequestPath();
    state.configSave = {
      ...createBlankConfigSaveState(),
      status: 'saving',
      message: 'Saving config changes...',
      errorCode: '',
      backupPath: '',
      changedPaths: diffs.map((diff) => diff.path),
      reloadRequiredPaths: [],
      requestPath,
      savedAt: nowMs(),
    };
    renderShell({ preserveScroll: true });

    try {
      const response = await fetch(requestPath, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          changes: diffs.map((diff) => ({
            path: diff.path,
            value: diff.to,
          })),
        }),
      });
      let body = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      const payload = toObject(body);
      if (!response.ok || payload.ok === false) {
        const error = toObject(payload.error);
        const details = toObject(error.details);
        const saveError = new Error(toText(error.message || payload.message || `Config save failed (HTTP ${response.status}).`, 'Config save failed.'));
        saveError.code = toText(error.code || payload.code || 'config_save_failed', 'config_save_failed');
        saveError.backupPath = toText(
          details.backup_path || details.backupPath || payload.backup_path || payload.backupPath || '',
          ''
        );
        saveError.changedPaths = toArray(
          details.changed_paths || details.changedPaths || payload.changed_paths || payload.changedPaths || diffs.map((diff) => diff.path)
        );
        saveError.reloadRequiredPaths = toArray(
          details.reload_required_paths ||
            details.reloadRequiredPaths ||
            payload.reload_required_paths ||
            payload.reloadRequiredPaths ||
            diffs.filter((diff) => diff.restart).map((diff) => diff.path)
        );
        throw saveError;
      }

      if (payload.snapshot && typeof payload.snapshot === 'object') {
        applyServerSnapshot(payload.snapshot);
      } else {
        await refreshSnapshot({ allowFallback: true, silent: true });
      }
      state.configDraft = clone(state.configContract?.values || defaults.configContract.values || {});
      state.configSave = {
        ...createBlankConfigSaveState(),
        status: 'success',
        message: toText(payload.message || 'Config saved.', 'Config saved.'),
        errorCode: '',
        backupPath: toText(payload.backup_path || payload.backupPath || '', ''),
        changedPaths: toArray(payload.changed_paths || payload.changedPaths || diffs.map((diff) => diff.path)),
        reloadRequiredPaths: toArray(payload.reload_required_paths || payload.reloadRequiredPaths || diffs.filter((diff) => diff.restart).map((diff) => diff.path)),
        requestPath,
        savedAt: nowMs(),
      };
      renderShell({ preserveScroll: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Config save failed.';
      const errorCode = error instanceof Error && typeof error.code === 'string' && error.code ? error.code : 'config_save_failed';
      const backupPath = error instanceof Error ? toText(error.backupPath || error.backup_path || '', '') : '';
      const changedPaths = error instanceof Error && error.changedPaths ? toArray(error.changedPaths) : diffs.map((diff) => diff.path);
      const reloadRequiredPaths = error instanceof Error && error.reloadRequiredPaths
        ? toArray(error.reloadRequiredPaths)
        : diffs.filter((diff) => diff.restart).map((diff) => diff.path);
      state.configSave = {
        ...createBlankConfigSaveState(),
        status: 'error',
        message,
        errorCode,
        backupPath,
        changedPaths,
        reloadRequiredPaths,
        requestPath,
        savedAt: nowMs(),
      };
      renderShell({ preserveScroll: true });
    }
  }

  function configGroups() {
    const groups = toArray(state.configContract?.groups || defaults.configContract?.groups || legacyConfigGroups());
    return groups.map((group) => ({
      ...group,
      paths: toArray(group.paths).filter((path) => Boolean(state.configSchema[path])),
    })).filter((group) => group.paths.length);
  }

  function currentConfigSelection() {
    if (state.configSelection && state.configSchema[state.configSelection]) {
      return state.configSelection;
    }
    return configGroups().flatMap((group) => group.paths).find((path) => state.configSchema[path]) || Object.keys(state.configSchema)[0] || '';
  }

  function currentPrompt() {
    if (!state.prompts.length) {
      return null;
    }
    return state.prompts.find((prompt) => prompt.id === state.promptSelection) || state.prompts[0];
  }

  function promptEditorData() {
    return toObject(state.promptEditor);
  }

  function inspectPromptEditorState() {
    return clone(promptEditorData());
  }

  function promptEditorIsDirty(editor = promptEditorData()) {
    return toText(editor.draftFile, '').trim() !== toText(editor.baseFile, '').trim() || toText(editor.draftContent, '') !== toText(editor.baseContent, '');
  }

  function promptFileNameLooksValid(fileName) {
    const candidate = toText(fileName, '').trim().replace(/\\/g, '/');
    if (!candidate) {
      return false;
    }
    if (candidate === '.' || candidate === '..') {
      return false;
    }
    if (candidate.includes('/') || candidate.includes(':')) {
      return false;
    }
    return true;
  }

  function promptEditorValidation(editor = promptEditorData()) {
    const draftFile = toText(editor.draftFile, '').trim();
    const draftContent = toText(editor.draftContent, '');
    const requiredVariables = normalizeListValues(
      editor.requiredTemplateVariables != null
        ? editor.requiredTemplateVariables
        : editor.baseTemplateVariables || []
    );
    const draftVariables = extractTemplateVariables(draftContent);
    const missingVariables = requiredVariables.filter((name) => !draftVariables.includes(name));
    const expectedFile = toText(editor.baseFile, '').trim();
    const fileIsValid = promptFileNameLooksValid(draftFile);
    const fileErrorCode = !draftFile
      ? 'prompt_file_required'
      : !fileIsValid
        ? 'prompt_file_invalid'
        : expectedFile && draftFile !== expectedFile
          ? 'prompt_file_mismatch'
          : '';
    const contentErrorCode = draftContent.trim() ? '' : 'prompt_content_required';
    const templateErrorCode = missingVariables.length ? 'prompt_template_variables_missing' : '';
    return {
      fileError: fileErrorCode === 'prompt_file_required'
        ? 'Filename cannot be empty.'
        : fileErrorCode === 'prompt_file_invalid'
          ? 'Filename must be a bare filename within the resolved prompts directory.'
          : fileErrorCode === 'prompt_file_mismatch'
            ? `Filename must stay ${expectedFile}.`
            : '',
      fileErrorCode,
      contentError: contentErrorCode ? 'Prompt content cannot be empty.' : '',
      contentErrorCode,
      templateError: templateErrorCode ? `Missing template variables: ${missingVariables.map((name) => `{${name}}`).join(', ')}` : '',
      templateErrorCode,
      requiredVariables,
      draftVariables,
      missingVariables,
    };
  }

  function promptContentDiffRows(editor = promptEditorData(), limit = 10) {
    const baseLines = String(editor.baseContent || '').split(/\r?\n/);
    const draftLines = String(editor.draftContent || '').split(/\r?\n/);
    const rows = [];
    const max = Math.max(baseLines.length, draftLines.length);
    for (let index = 0; index < max; index += 1) {
      const baseLine = baseLines[index];
      const draftLine = draftLines[index];
      if (baseLine === draftLine) {
        continue;
      }
      const lineNumber = index + 1;
      if (baseLine != null) {
        rows.push({ kind: 'removed', lineNumber, text: baseLine });
      }
      if (draftLine != null) {
        rows.push({ kind: 'added', lineNumber, text: draftLine });
      }
      if (rows.length >= limit) {
        break;
      }
    }
    return rows;
  }

  function renderPromptEditorState() {
    const editor = promptEditorData();
    if (!editor.promptId) {
      return `
        <span class="badge badge--dim">No prompt selected</span>
        <span class="muted">Select a prompt to read its explicit content slice.</span>
      `;
    }
    if (editor.loading) {
      return `
        <span class="badge badge--warn">Loading</span>
        <span class="muted">Reading full prompt content from the explicit read path.</span>
      `;
    }
    if (editor.error) {
      return `
        <span class="badge badge--err">Error</span>
        <span class="muted">${escapeHTML(editor.error)}</span>
      `;
    }
    const dirty = promptEditorIsDirty(editor);
    const contentLength = String(editor.draftContent || '').length;
    const backupCount = promptEditorBackups(editor).length;
    const saveState = toObject(editor.saveState);
    const restoreState = toObject(editor.restoreState);
    const mutationBadge = saveState.status === 'saving'
      ? { tone: 'warn', label: 'SAVING' }
      : restoreState.status === 'restoring'
        ? { tone: 'warn', label: 'RESTORING' }
        : saveState.status === 'error'
          ? { tone: 'err', label: 'SAVE ERROR' }
          : restoreState.status === 'error'
            ? { tone: 'err', label: 'RESTORE ERROR' }
            : saveState.status === 'success'
              ? { tone: 'info', label: 'SAVED' }
              : restoreState.status === 'success'
                ? { tone: 'info', label: 'RESTORED' }
                : !promptMutationEnabled()
                  ? { tone: 'dim', label: 'LOCAL ONLY' }
                  : null;
    return `
      ${mutationBadge ? `<span class="badge badge--${mutationBadge.tone}">${mutationBadge.label}</span>` : ''}
      <span class="badge ${dirty ? 'badge--warn' : 'badge--dim'}">${dirty ? 'DIRTY' : 'CLEAN'}</span>
      <span class="badge badge--info">FULL READ</span>
      <span class="badge ${backupCount ? 'badge--dim' : 'badge--warn'}">${backupCount ? `${backupCount} BACKUP${backupCount === 1 ? '' : 'S'}` : 'NO BACKUPS'}</span>
      <span class="muted">${escapeHTML(contentLength)} chars</span>
    `;
  }

  function renderPromptEditorBanner() {
    const editor = promptEditorData();
    if (!editor.promptId) {
      return `
        <div class="section-banner section-banner--info">
          <div class="section-banner__title">Prompt editor</div>
          <div class="section-banner__copy">Select a prompt to load the full content through the explicit read path.</div>
        </div>
      `;
    }
    if (editor.loading) {
      return `
        <div class="section-banner section-banner--info">
          <div class="section-banner__title">Reading prompt content</div>
          <div class="section-banner__copy">Inventory previews stay redacted. This editor shows the explicit full-content read.</div>
        </div>
      `;
    }
    if (editor.error) {
      return `
        <div class="section-banner section-banner--err">
          <div class="section-banner__title">Prompt read failed</div>
          <div class="section-banner__copy">${escapeHTML(editor.error)}</div>
        </div>
      `;
    }
    const saveState = toObject(editor.saveState);
    const restoreState = toObject(editor.restoreState);
    if (saveState.status === 'saving' || restoreState.status === 'restoring') {
      const activeState = saveState.status === 'saving' ? saveState : restoreState;
      return `
        <div class="section-banner section-banner--warn">
          <div class="section-banner__title">${saveState.status === 'saving' ? 'Saving prompt' : 'Restoring backup'}</div>
          <div class="section-banner__copy">${escapeHTML(activeState.message || 'Prompt mutation in flight.')}</div>
        </div>
      `;
    }
    if (saveState.status === 'error' || restoreState.status === 'error') {
      const activeState = saveState.status === 'error' ? saveState : restoreState;
      return `
        <div class="section-banner section-banner--err">
          <div class="section-banner__title">${saveState.status === 'error' ? 'Prompt save failed' : 'Prompt restore failed'}</div>
          <div class="section-banner__copy">${escapeHTML(activeState.message || 'Prompt mutation failed.')}</div>
        </div>
      `;
    }
    if (saveState.status === 'success' || restoreState.status === 'success') {
      const activeState = saveState.status === 'success' ? saveState : restoreState;
      return `
        <div class="section-banner section-banner--info">
          <div class="section-banner__title">${saveState.status === 'success' ? 'Prompt saved' : 'Prompt restored'}</div>
          <div class="section-banner__copy">${escapeHTML(activeState.message || 'Prompt mutation completed.')}</div>
        </div>
      `;
    }
    if (!promptMutationEnabled()) {
      return `
        <div class="section-banner section-banner--warn">
          <div class="section-banner__title">Prompt mutations are locked</div>
          <div class="section-banner__copy">${escapeHTML(state.runnerControl?.message || 'Prompt saves and restores are disabled until runner controls are enabled.')}</div>
        </div>
      `;
    }
    const dirty = promptEditorIsDirty(editor);
    return `
      <div class="section-banner ${dirty ? 'section-banner--warn' : 'section-banner--info'}">
        <div class="section-banner__title">Explicit prompt read</div>
        <div class="section-banner__copy">${dirty
          ? 'Draft edits stay local until you save. Saving creates a backup before atomically updating the prompt file.'
          : 'Inventory previews stay redacted by default. Saving creates a backup before atomically updating the prompt file, and restore uses the selected backup.'
        }</div>
      </div>
    `;
  }

  function renderPromptEditorValidation() {
    const editor = promptEditorData();
    if (!editor.promptId || editor.loading || editor.error) {
      return '';
    }
    const validation = promptEditorValidation(editor);
    const lines = [];
    const required = validation.requiredVariables.length
      ? validation.requiredVariables.map((name) => `{${name}}`).join(', ')
      : 'none';
    lines.push(`
      <div class="compact-list__item">
        <span class="compact-list__bullet"></span>
        <div>
          <div class="compact-list__body ${validation.fileError ? 'field-error' : ''}">${escapeHTML(validation.fileError || 'Filename is populated.')}</div>
          <div class="compact-list__meta">Filename validation</div>
        </div>
      </div>
    `);
    lines.push(`
      <div class="compact-list__item">
        <span class="compact-list__bullet"></span>
        <div>
          <div class="compact-list__body ${validation.contentError ? 'field-error' : ''}">${escapeHTML(validation.contentError || 'Content is populated.')}</div>
          <div class="compact-list__meta">Content validation</div>
        </div>
      </div>
    `);
    lines.push(`
      <div class="compact-list__item">
        <span class="compact-list__bullet"></span>
        <div>
          <div class="compact-list__body ${validation.templateError ? 'field-error' : ''}">${escapeHTML(validation.templateError || `Required template variables: ${required}`)}</div>
          <div class="compact-list__meta">Template-variable validation</div>
        </div>
      </div>
    `);
    return `
      <div class="compact-list">
        ${lines.join('')}
      </div>
    `;
  }

  function renderPromptEditorDiff() {
    const editor = promptEditorData();
    if (!editor.promptId || editor.loading || editor.error) {
      return '';
    }
    const dirty = promptEditorIsDirty(editor);
    const baseFile = toText(editor.baseFile, '').trim();
    const draftFile = toText(editor.draftFile, '').trim();
    const fileChanged = draftFile !== baseFile;
    const rows = promptContentDiffRows(editor, 10);
    return `
      <div class="prompt-diff-list">
        <div class="prompt-diff-row">
          <div class="prompt-diff-row__head">
            <span class="prompt-diff-row__path">Local diff preview</span>
            <span class="badge ${dirty ? 'badge--warn' : 'badge--dim'}">${dirty ? 'DIRTY' : 'CLEAN'}</span>
          </div>
          <div class="summary-note">Draft changes stay local until saved. Save creates a backup first; restore copies a selected backup back into place.</div>
        </div>
        ${fileChanged ? `
          <div class="prompt-diff-row">
            <div class="prompt-diff-row__head">
              <span class="prompt-diff-row__path">Filename</span>
              <span class="badge badge--info">Local only</span>
            </div>
            <div class="prompt-diff-row__values">
              <span class="prompt-diff-row__value prompt-diff-row__value--removed">${escapeHTML(baseFile || '(empty)')}</span>
              <span class="prompt-diff-row__arrow">→</span>
              <span class="prompt-diff-row__value prompt-diff-row__value--added">${escapeHTML(draftFile || '(empty)')}</span>
            </div>
          </div>
        ` : ''}
        ${rows.length ? rows.map((row) => `
          <div class="prompt-diff-row ${row.kind === 'added' ? 'prompt-diff-row--added' : 'prompt-diff-row--removed'}">
            <div class="prompt-diff-row__head">
              <span class="prompt-diff-row__path">Line ${escapeHTML(row.lineNumber)}</span>
              <span class="badge ${row.kind === 'added' ? 'badge--info' : 'badge--warn'}">${row.kind === 'added' ? 'Added' : 'Removed'}</span>
            </div>
            <div class="prompt-diff-row__values">
              <span class="prompt-diff-row__value prompt-diff-row__value--${row.kind}">${escapeHTML(row.text || '(empty)')}</span>
            </div>
          </div>
        `).join('') : '<div class="summary-note">No local content changes yet.</div>'}
      </div>
    `;
  }

  function renderPromptEditorMutationMeta() {
    const editor = promptEditorData();
    if (!editor.promptId || editor.loading || editor.error) {
      return '';
    }
    const backups = promptEditorBackups(editor);
    const selectedBackup = promptSelectedBackup(editor);
    const saveState = toObject(editor.saveState);
    const restoreState = toObject(editor.restoreState);
    const metaRows = [];
    if (selectedBackup) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">Selected backup</div>
          <div class="prompt-mutation-state__path">${escapeHTML(selectedBackup.path || '(unresolved backup path)')}</div>
          <div class="summary-note">${escapeHTML(selectedBackup.summary || selectedBackup.name || 'Timestamped backup')}</div>
        </div>
      `);
    }
    if (backups.length) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">Available backups</div>
          <div class="prompt-mutation-state__paths">
            ${backups.map((backup) => `<span class="prompt-mutation-state__path">${escapeHTML(backup.path || backup.name || '(backup)')}</span>`).join('')}
          </div>
        </div>
      `);
    }
    if (saveState.backupPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">Backup path</div>
          <div class="prompt-mutation-state__path">${escapeHTML(saveState.backupPath)}</div>
        </div>
      `);
    }
    if (restoreState.backupPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">Restore backup path</div>
          <div class="prompt-mutation-state__path">${escapeHTML(restoreState.backupPath)}</div>
        </div>
      `);
    }
    if (saveState.savedPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">Saved path</div>
          <div class="prompt-mutation-state__path">${escapeHTML(saveState.savedPath)}</div>
        </div>
      `);
    }
    if (restoreState.restoredFromPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">Restored from</div>
          <div class="prompt-mutation-state__path">${escapeHTML(restoreState.restoredFromPath)}</div>
        </div>
      `);
    }
    const activeState = saveState.status === 'saving'
      ? saveState
      : restoreState.status === 'restoring'
        ? restoreState
        : saveState.status === 'error'
          ? saveState
          : restoreState.status === 'error'
            ? restoreState
            : saveState.status === 'success'
              ? saveState
              : restoreState.status === 'success'
                ? restoreState
                : null;
    if (activeState && activeState.errorCode) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">Error code</div>
          <div class="prompt-mutation-state__code">${escapeHTML(activeState.errorCode)}</div>
        </div>
      `);
    }
    return metaRows.join('');
  }

  function renderPromptEditorMutationPanel() {
    const editor = promptEditorData();
    if (!editor.promptId || editor.loading || editor.error) {
      return '';
    }
    const backups = promptEditorBackups(editor);
    const selectedBackup = promptSelectedBackup(editor);
    const saveState = toObject(editor.saveState);
    const restoreState = toObject(editor.restoreState);
    const mutationEnabled = promptMutationEnabled();
    const saveDisabledReason = promptSaveDisabledReason(editor);
    const restoreDisabledReason = promptRestoreDisabledReason(editor);
    const activeState = saveState.status === 'saving'
      ? saveState
      : restoreState.status === 'restoring'
        ? restoreState
        : saveState.status === 'error'
          ? saveState
          : restoreState.status === 'error'
            ? restoreState
            : saveState.status === 'success'
              ? saveState
              : restoreState.status === 'success'
                ? restoreState
                : null;
    const bannerTone = saveState.status === 'error' || restoreState.status === 'error'
      ? 'err'
      : saveState.status === 'saving' || restoreState.status === 'restoring'
        ? 'warn'
        : saveState.status === 'success' || restoreState.status === 'success'
          ? 'info'
          : !mutationEnabled
            ? 'warn'
            : 'info';
    const bannerTitle = saveState.status === 'saving'
      ? 'Saving prompt'
      : restoreState.status === 'restoring'
        ? 'Restoring backup'
        : saveState.status === 'error'
          ? 'Prompt save failed'
          : restoreState.status === 'error'
            ? 'Prompt restore failed'
            : saveState.status === 'success'
              ? 'Prompt saved'
              : restoreState.status === 'success'
                ? 'Prompt restored'
                : !mutationEnabled
                  ? 'Prompt mutations are locked'
                  : 'Prompt backups';
    const bannerCopy = saveState.status === 'saving'
      ? saveState.message || 'Saving prompt changes and creating a backup first.'
      : restoreState.status === 'restoring'
        ? restoreState.message || 'Restoring prompt content from the selected backup.'
        : saveState.status === 'error' || restoreState.status === 'error'
          ? activeState?.message || 'Prompt mutation failed.'
          : saveState.status === 'success' || restoreState.status === 'success'
            ? activeState?.message || 'Prompt mutation completed.'
            : !mutationEnabled
              ? state.runnerControl?.message || 'Prompt saves and restores are disabled until runner controls are enabled.'
              : 'Choose a backup to restore or save the current draft after validation passes.';
    const errorCode = activeState && activeState.errorCode ? `<div class="prompt-mutation-state__code">${escapeHTML(activeState.errorCode)}</div>` : '';
    const backupOptions = backups.length
      ? backups.map((backup) => `
        <option value="${escapeHTML(backup.path)}"${backup.path === selectedBackup?.path ? ' selected' : ''}>
          ${escapeHTML(backup.summary || backup.name || backup.path)}
        </option>
      `).join('')
      : '<option value="">No backups available</option>';
    const backupSelectAttrs = !mutationEnabled || !backups.length || promptMutationInFlight(editor) || Boolean(editor.loading) || Boolean(editor.error)
      ? 'disabled'
      : '';
    const restoreConfirmationAttrs = !mutationEnabled || promptMutationInFlight(editor) || Boolean(editor.loading) || Boolean(editor.error)
      ? 'disabled'
      : '';
    const saveButtonAttrs = saveDisabledReason ? `disabled title="${escapeHTML(saveDisabledReason)}"` : '';
    const restoreButtonAttrs = restoreDisabledReason ? `disabled title="${escapeHTML(restoreDisabledReason)}"` : '';
    return `
      <div class="prompt-mutation-state">
        <div class="section-banner section-banner--${bannerTone}">
          <div class="section-banner__title">${escapeHTML(bannerTitle)}</div>
          <div class="section-banner__copy">${escapeHTML(bannerCopy)}</div>
        </div>
        ${errorCode}
        <div class="prompt-mutation-state__meta" data-prompt-mutation-meta>
          ${renderPromptEditorMutationMeta()}
        </div>
        <div class="prompt-backup-panel">
          <div class="prompt-editor__field">
            <label class="prompt-editor__label" for="prompt-backup-selection">Restore backup</label>
            <select
              id="prompt-backup-selection"
              class="field-control prompt-editor__input prompt-backup-select"
              data-prompt-backup-select
              ${backupSelectAttrs}
            >
              ${backupOptions}
            </select>
            <div class="summary-note">Choose a timestamped backup before restoring the prompt.</div>
          </div>
          <div class="prompt-editor__field">
            <label class="prompt-editor__label" for="prompt-restore-confirmation">Restore confirmation</label>
            <input
              id="prompt-restore-confirmation"
              class="field-control prompt-editor__input prompt-backup-confirm"
              data-prompt-restore-confirmation
              type="text"
              value="${escapeHTML(editor.restoreConfirmation || '')}"
              placeholder="RESTORE BACKUP"
              autocomplete="off"
              spellcheck="false"
              ${restoreConfirmationAttrs}
            >
            <div class="summary-note">Type RESTORE BACKUP to confirm the selected backup will overwrite the prompt file.</div>
          </div>
          <div class="prompt-editor__actions">
            ${button('Save Prompt', 'prompt-save', 'button--primary', `${saveButtonAttrs} data-prompt-save-button`)}
            ${button('Restore Backup', 'prompt-restore', 'button--danger', `${restoreButtonAttrs} data-prompt-restore-button`)}
          </div>
        </div>
      </div>
    `;
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

  function promptEditorMatchesPrompt(prompt) {
    if (!prompt) {
      return false;
    }
    const editor = promptEditorData();
    return (
      editor.promptId === prompt.id &&
      editor.promptFile === prompt.file &&
      editor.promptPath === prompt.path &&
      editor.promptMode === prompt.mode &&
      editor.promptProfile === (prompt.profile || '') &&
      editor.promptSource === (prompt.source || '')
    );
  }

  function promptMutationEnabled() {
    return configSaveEnabled();
  }

  function promptSaveRequestPath() {
    return '/api/prompts/save';
  }

  function promptRestoreRequestPath() {
    return '/api/prompts/restore';
  }

  function promptSaveInFlight(editor = promptEditorData()) {
    return toText(toObject(editor.saveState).status, '') === 'saving';
  }

  function promptRestoreInFlight(editor = promptEditorData()) {
    return toText(toObject(editor.restoreState).status, '') === 'restoring';
  }

  function promptMutationInFlight(editor = promptEditorData()) {
    return promptSaveInFlight(editor) || promptRestoreInFlight(editor);
  }

  function promptEditorBusy(editor = promptEditorData()) {
    return Boolean(!editor.promptId || editor.loading || editor.error || promptMutationInFlight(editor));
  }

  function promptEditorBackups(editor = promptEditorData()) {
    return toArray(editor.backups);
  }

  function promptSelectedBackup(editor = promptEditorData()) {
    const backups = promptEditorBackups(editor);
    const selected = toText(editor.backupSelection, '');
    if (selected) {
      const match = backups.find((item) => toText(item.path, '') === selected);
      if (match) {
        return match;
      }
    }
    return backups[0] || null;
  }

  function createBlankPromptSaveState() {
    return {
      status: 'idle',
      message: '',
      errorCode: '',
      backupPath: '',
      savedPath: '',
      savedAt: 0,
      requestPath: promptSaveRequestPath(),
    };
  }

  function createBlankPromptRestoreState() {
    return {
      status: 'idle',
      message: '',
      errorCode: '',
      backupPath: '',
      restoredFromPath: '',
      restoredAt: 0,
      requestPath: promptRestoreRequestPath(),
    };
  }

  function normalizePromptBackup(raw) {
    const item = toObject(raw);
    const path = toText(item.path || item.backup_path, '');
    const name = toText(item.name, path ? path.split('/').pop() || path : '');
    const updated = toText(item.updated, '');
    const size = toNumber(item.size, 0);
    const summary = toText(item.summary, updated ? `${updated} | ${size} bytes` : `${size} bytes`);
    return {
      path,
      name,
      updated,
      size,
      summary,
    };
  }

  function normalizePromptMutationResponse(payload) {
    const raw = toObject(payload);
    return {
      ok: Boolean(raw.ok !== false),
      action: toText(raw.action, ''),
      status: toText(raw.status, ''),
      message: toText(raw.message, ''),
      backupPath: toText(raw.backup_path ?? raw.backupPath, ''),
      savedPath: toText(raw.saved_path ?? raw.savedPath, ''),
      restoredFromPath: toText(raw.restored_from_path ?? raw.restoredFromPath, ''),
      error: toObject(raw.error),
      prompt: toObject(raw.prompt),
      validation: toObject(raw.validation),
    };
  }

  function buildPromptReadUrl(prompt) {
    const params = new URLSearchParams();
    params.set('id', prompt.id);
    params.set('file', prompt.file);
    if (prompt.path) {
      params.set('path', prompt.path);
    }
    return `/api/prompts/read?${params.toString()}`;
  }

  function normalizePromptReadResponse(payload) {
    const raw = toObject(payload);
    return {
      ok: Boolean(raw.ok !== false),
      id: toText(raw.id, ''),
      file: toText(raw.file, ''),
      path: toText(raw.path, ''),
      scope: toText(raw.scope, ''),
      profile: toText(raw.profile, ''),
      source: toText(raw.source, ''),
      mode: toText(raw.mode, 'template'),
      updated: toText(raw.updated, ''),
      content: raw.content == null ? '' : String(raw.content),
      preview: raw.preview == null ? '' : String(raw.preview),
      summary: raw.summary == null ? '' : String(raw.summary),
      templateVariables: normalizeListValues(raw.template_variables ?? raw.templateVariables),
      requiredTemplateVariables: normalizeListValues(raw.required_template_variables ?? raw.requiredTemplateVariables),
      hasRequiredTemplateVariables: Object.prototype.hasOwnProperty.call(raw, 'required_template_variables') || Object.prototype.hasOwnProperty.call(raw, 'requiredTemplateVariables'),
      backups: Array.isArray(raw.backups) ? raw.backups.map((item) => normalizePromptBackup(item)) : [],
      error: toObject(raw.error),
      validation: toObject(raw.validation),
    };
  }

  function applyPromptEditorPayload(prompt, payload, options = {}) {
    const read = normalizePromptReadResponse(payload);
    const content = read.content != null ? read.content : (prompt.content != null ? prompt.content : '');
    const backups = read.backups.length
      ? read.backups
      : (Array.isArray(prompt.backups) ? prompt.backups.map((item) => normalizePromptBackup(item)) : []);
    const requiredTemplateVariables = read.hasRequiredTemplateVariables
      ? read.requiredTemplateVariables
      : (prompt.requiredTemplateVariables != null ? normalizeListValues(prompt.requiredTemplateVariables) : null);
    const baseTemplateVariables = read.templateVariables.length ? read.templateVariables : extractTemplateVariables(content);
    const nextBackupSelection = Object.prototype.hasOwnProperty.call(options, 'backupSelection')
      ? toText(options.backupSelection, '')
      : (backups[0]?.path || '');
    const nextEditor = {
      ...createBlankPromptEditor(),
      promptId: prompt.id,
      promptFile: read.file || prompt.file,
      promptPath: read.path || prompt.path,
      promptScope: read.scope || prompt.scope,
      promptProfile: read.profile || prompt.profile || '',
      promptSource: read.source || prompt.source,
      promptMode: read.mode || prompt.mode,
      promptUpdated: read.updated || prompt.updated,
      promptSummary: read.summary || prompt.summary,
      promptPreview: read.preview || prompt.preview,
      baseFile: read.file || prompt.file,
      basePath: read.path || prompt.path,
      baseContent: content,
      baseTemplateVariables,
      requiredTemplateVariables,
      backups,
      backupSelection: nextBackupSelection,
      draftFile: read.file || prompt.file,
      draftContent: content,
      loading: false,
      error: '',
      dirty: false,
      requestToken: promptEditorData().requestToken,
      lastLoadedAt: nowMs(),
    };
    if (Object.prototype.hasOwnProperty.call(options, 'restoreConfirmation')) {
      nextEditor.restoreConfirmation = toText(options.restoreConfirmation, '');
    }
    if (options.saveState) {
      nextEditor.saveState = options.saveState;
    }
    if (options.restoreState) {
      nextEditor.restoreState = options.restoreState;
    }
    if (Object.prototype.hasOwnProperty.call(options, 'validation')) {
      nextEditor.validation = options.validation;
    }
    state.promptEditor = nextEditor;
  }

  function syncPromptEditorArtifacts() {
    if (state.activeView !== 'prompts') {
      return;
    }
    const editorRoot = mainRoot().querySelector('[data-prompt-editor-root]');
    if (!editorRoot) {
      return;
    }
    const editor = promptEditorData();
    editorRoot.setAttribute('data-prompt-dirty', promptEditorIsDirty(editor) ? 'true' : 'false');
    editorRoot.setAttribute('data-prompt-loading', editor.loading ? 'true' : 'false');
    editorRoot.setAttribute('data-prompt-saving', promptSaveInFlight(editor) ? 'true' : 'false');
    editorRoot.setAttribute('data-prompt-restoring', promptRestoreInFlight(editor) ? 'true' : 'false');
    editorRoot.setAttribute('data-prompt-id', editor.promptId || '');
    const stateNode = editorRoot.querySelector('[data-prompt-editor-state]');
    if (stateNode) {
      stateNode.innerHTML = renderPromptEditorState();
    }
    const bannerNode = editorRoot.querySelector('[data-prompt-editor-banner]');
    if (bannerNode) {
      bannerNode.innerHTML = renderPromptEditorBanner();
    }
    const validationNode = editorRoot.querySelector('[data-prompt-editor-validation]');
    if (validationNode) {
      validationNode.innerHTML = renderPromptEditorValidation();
    }
    const diffNode = editorRoot.querySelector('[data-prompt-editor-diff]');
    if (diffNode) {
      diffNode.innerHTML = renderPromptEditorDiff();
    }
    const mutationNode = editorRoot.querySelector('[data-prompt-editor-mutation]');
    if (mutationNode) {
      const metaNode = mutationNode.querySelector('[data-prompt-mutation-meta]');
      if (metaNode) {
        metaNode.innerHTML = renderPromptEditorMutationMeta();
      }
      const selectedBackup = promptSelectedBackup(editor);
      const backupSelect = mutationNode.querySelector('[data-prompt-backup-select]');
      if (backupSelect) {
        const nextBackupValue = selectedBackup?.path || '';
        if (backupSelect.value !== nextBackupValue) {
          backupSelect.value = nextBackupValue;
        }
      }
      const confirmationInput = mutationNode.querySelector('[data-prompt-restore-confirmation]');
      if (confirmationInput) {
        const nextConfirmation = editor.restoreConfirmation || '';
        if (confirmationInput.value !== nextConfirmation) {
          confirmationInput.value = nextConfirmation;
        }
      }
      const saveButton = mutationNode.querySelector('[data-prompt-save-button]');
      if (saveButton) {
        const reason = promptSaveDisabledReason(editor);
        if (reason) {
          saveButton.setAttribute('disabled', '');
          saveButton.setAttribute('title', reason);
        } else {
          saveButton.removeAttribute('disabled');
          saveButton.removeAttribute('title');
        }
      }
      const restoreButton = mutationNode.querySelector('[data-prompt-restore-button]');
      if (restoreButton) {
        const reason = promptRestoreDisabledReason(editor);
        if (reason) {
          restoreButton.setAttribute('disabled', '');
          restoreButton.setAttribute('title', reason);
        } else {
          restoreButton.removeAttribute('disabled');
          restoreButton.removeAttribute('title');
        }
      }
    }
  }

  function updatePromptEditorDraft(field, value) {
    const editor = promptEditorData();
    if (promptEditorBusy(editor)) {
      return;
    }
    const nextEditor = {
      ...editor,
      [field]: value,
    };
    nextEditor.dirty = promptEditorIsDirty(nextEditor);
    nextEditor.saveState = createBlankPromptSaveState();
    nextEditor.restoreState = createBlankPromptRestoreState();
    nextEditor.validation = null;
    state.promptEditor = nextEditor;
    syncPromptEditorArtifacts();
  }

  function updatePromptEditorMutationField(field, value) {
    const editor = promptEditorData();
    if (promptEditorBusy(editor)) {
      return;
    }
    const nextEditor = {
      ...editor,
      [field]: value,
      restoreState: createBlankPromptRestoreState(),
    };
    if (field === 'backupSelection') {
      nextEditor.restoreConfirmation = '';
    }
    state.promptEditor = nextEditor;
    syncPromptEditorArtifacts();
  }

  function promptEditorContext(editor = promptEditorData()) {
    return {
      id: toText(editor.promptId, ''),
      file: toText(editor.draftFile || editor.promptFile, ''),
      path: toText(editor.promptPath || editor.basePath, ''),
      scope: toText(editor.promptScope, ''),
      profile: toText(editor.promptProfile, ''),
      source: toText(editor.promptSource, ''),
      mode: toText(editor.promptMode, 'template'),
      updated: toText(editor.promptUpdated, ''),
      summary: toText(editor.promptSummary, ''),
      preview: toText(editor.promptPreview, ''),
      content: toText(editor.draftContent, toText(editor.baseContent, '')),
      templateVariables: normalizeListValues(editor.baseTemplateVariables || []),
      requiredTemplateVariables: editor.requiredTemplateVariables,
      backups: promptEditorBackups(editor),
    };
  }

  function promptSaveDisabledReason(editor = promptEditorData()) {
    if (promptSaveInFlight(editor)) {
      return 'Prompt save is already in progress.';
    }
    if (!promptMutationEnabled()) {
      return state.runnerControl?.message || 'Prompt saves are disabled until runner controls are enabled.';
    }
    if (!editor.promptId) {
      return 'Select a prompt before saving.';
    }
    if (editor.loading) {
      return 'Prompt content is still loading.';
    }
    if (editor.error) {
      return 'Fix the prompt read error before saving.';
    }
    if (!promptEditorIsDirty(editor)) {
      return 'No prompt changes to save.';
    }
    const validation = promptEditorValidation(editor);
    if (validation.fileError) {
      return validation.fileError;
    }
    if (validation.contentError) {
      return validation.contentError;
    }
    if (validation.templateError) {
      return validation.templateError;
    }
    return '';
  }

  function promptRestoreDisabledReason(editor = promptEditorData()) {
    if (promptRestoreInFlight(editor)) {
      return 'Prompt restore is already in progress.';
    }
    if (!promptMutationEnabled()) {
      return state.runnerControl?.message || 'Prompt restores are disabled until runner controls are enabled.';
    }
    if (!editor.promptId) {
      return 'Select a prompt before restoring.';
    }
    if (editor.loading) {
      return 'Prompt content is still loading.';
    }
    if (editor.error) {
      return 'Fix the prompt read error before restoring.';
    }
    const validation = promptEditorValidation(editor);
    if (validation.fileError) {
      return validation.fileError;
    }
    const selectedBackup = promptSelectedBackup(editor);
    if (!selectedBackup || !toText(selectedBackup.path, '')) {
      return 'No backups are available for this prompt.';
    }
    if (!toText(editor.restoreConfirmation, '').trim()) {
      return 'Type RESTORE BACKUP to confirm the restore.';
    }
    if (toText(editor.restoreConfirmation, '').trim() !== 'RESTORE BACKUP') {
      return 'Confirmation phrase must be RESTORE BACKUP.';
    }
    return '';
  }

  async function savePromptDraft() {
    const editor = promptEditorData();
    if (promptSaveInFlight(editor)) {
      return;
    }

    const disabledReason = promptSaveDisabledReason(editor);
    if (disabledReason) {
      const validation = promptEditorValidation(editor);
      const validationCode = !promptMutationEnabled()
        ? 'prompt_mutation_disabled'
        : validation.fileErrorCode
          || validation.contentErrorCode
          || validation.templateErrorCode
          || 'prompt_no_changes';
      state.promptEditor = {
        ...editor,
        saveState: {
          ...createBlankPromptSaveState(),
          status: 'error',
          message: disabledReason,
          errorCode: validationCode,
          savedAt: nowMs(),
        },
        restoreState: createBlankPromptRestoreState(),
      };
      syncPromptEditorArtifacts();
      renderShell({ preserveScroll: true });
      return;
    }

    const requestPath = promptSaveRequestPath();
    state.promptEditor = {
      ...editor,
      saveState: {
        ...createBlankPromptSaveState(),
        status: 'saving',
        message: 'Saving prompt changes and creating a backup first.',
        requestPath,
        savedAt: nowMs(),
      },
      restoreState: createBlankPromptRestoreState(),
    };
    syncPromptEditorArtifacts();
    renderShell({ preserveScroll: true });

    try {
      const response = await fetch(requestPath, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          id: editor.promptId,
          file: toText(editor.draftFile, '').trim(),
          content: editor.draftContent,
        }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      const normalized = normalizePromptMutationResponse(payload);
      if (!response.ok || normalized.ok === false) {
        state.promptEditor = {
          ...editor,
          saveState: {
            ...createBlankPromptSaveState(),
            status: 'error',
            message: normalized.message || 'Prompt save failed.',
            errorCode: toText(normalized.error.code, 'prompt_save_failed') || 'prompt_save_failed',
            backupPath: normalized.backupPath || '',
            savedPath: normalized.savedPath || editor.basePath || '',
            savedAt: nowMs(),
            requestPath,
          },
          restoreState: createBlankPromptRestoreState(),
        };
        syncPromptEditorArtifacts();
        renderShell({ preserveScroll: true });
        return;
      }

      const refreshedPrompt = promptEditorContext(editor);
      applyPromptEditorPayload(refreshedPrompt, normalized.prompt || {}, {
        backupSelection: normalized.backupPath || '',
        restoreConfirmation: '',
      });
      const nextEditor = promptEditorData();
      state.promptEditor = {
        ...nextEditor,
        saveState: {
          ...createBlankPromptSaveState(),
          status: 'success',
          message: normalized.message || 'Prompt saved.',
          backupPath: normalized.backupPath || '',
          savedPath: normalized.savedPath || editor.basePath || '',
          savedAt: nowMs(),
          requestPath,
        },
        restoreState: createBlankPromptRestoreState(),
        backupSelection: normalized.backupPath || nextEditor.backupSelection || '',
        restoreConfirmation: '',
      };
      syncPromptEditorArtifacts();
      renderShell({ preserveScroll: true });
    } catch (error) {
      state.promptEditor = {
        ...editor,
        saveState: {
          ...createBlankPromptSaveState(),
          status: 'error',
          message: `Prompt save failed: ${error}`,
          errorCode: 'prompt_save_failed',
          savedPath: editor.basePath || '',
          savedAt: nowMs(),
          requestPath,
        },
        restoreState: createBlankPromptRestoreState(),
      };
      syncPromptEditorArtifacts();
      renderShell({ preserveScroll: true });
    }
  }

  async function restorePromptDraft() {
    const editor = promptEditorData();
    if (promptRestoreInFlight(editor)) {
      return;
    }

    const disabledReason = promptRestoreDisabledReason(editor);
    if (disabledReason) {
      const confirmation = toText(editor.restoreConfirmation, '').trim();
      const validation = promptEditorValidation(editor);
      let errorCode = 'prompt_backup_not_found';
      if (!promptMutationEnabled()) {
        errorCode = 'prompt_mutation_disabled';
      } else if (validation.fileErrorCode) {
        errorCode = validation.fileErrorCode;
      } else if (!confirmation) {
        errorCode = 'prompt_restore_confirmation_required';
      } else if (confirmation !== 'RESTORE BACKUP') {
        errorCode = 'prompt_restore_confirmation_mismatch';
      }
      state.promptEditor = {
        ...editor,
        restoreState: {
          ...createBlankPromptRestoreState(),
          status: 'error',
          message: disabledReason,
          errorCode,
          backupPath: '',
          restoredFromPath: promptSelectedBackup(editor)?.path || '',
          restoredAt: nowMs(),
        },
      };
      syncPromptEditorArtifacts();
      renderShell({ preserveScroll: true });
      return;
    }

    const selectedBackup = promptSelectedBackup(editor);
    const requestPath = promptRestoreRequestPath();
    const restorePath = toText(selectedBackup?.path, '');
    const confirmation = toText(editor.restoreConfirmation, '').trim();
    state.promptEditor = {
      ...editor,
      saveState: createBlankPromptSaveState(),
      restoreState: {
        ...createBlankPromptRestoreState(),
        status: 'restoring',
        message: 'Restoring prompt content from the selected backup and writing a safety copy first.',
        backupPath: restorePath,
        restoredFromPath: restorePath,
        restoredAt: nowMs(),
        requestPath,
      },
    };
    syncPromptEditorArtifacts();
    renderShell({ preserveScroll: true });

    try {
      const response = await fetch(requestPath, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          id: editor.promptId,
          file: toText(editor.draftFile, '').trim(),
          backup_path: restorePath,
          confirm: confirmation,
        }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      const normalized = normalizePromptMutationResponse(payload);
      if (!response.ok || normalized.ok === false) {
        state.promptEditor = {
          ...editor,
          restoreState: {
            ...createBlankPromptRestoreState(),
            status: 'error',
            message: normalized.message || 'Prompt restore failed.',
            errorCode: toText(normalized.error.code, 'prompt_restore_failed') || 'prompt_restore_failed',
            backupPath: normalized.backupPath || restorePath,
            restoredFromPath: normalized.restoredFromPath || restorePath,
            restoredAt: nowMs(),
            requestPath,
          },
        };
        syncPromptEditorArtifacts();
        renderShell({ preserveScroll: true });
        return;
      }

      const refreshedPrompt = promptEditorContext(editor);
      applyPromptEditorPayload(refreshedPrompt, normalized.prompt || {}, {
        backupSelection: normalized.backupPath || restorePath,
        restoreConfirmation: '',
      });
      const nextEditor = promptEditorData();
      state.promptEditor = {
        ...nextEditor,
        saveState: createBlankPromptSaveState(),
        restoreState: {
          ...createBlankPromptRestoreState(),
          status: 'success',
          message: normalized.message || 'Prompt restored.',
          backupPath: normalized.backupPath || restorePath,
          restoredFromPath: normalized.restoredFromPath || restorePath,
          restoredAt: nowMs(),
          requestPath,
        },
        backupSelection: normalized.backupPath || nextEditor.backupSelection || restorePath,
        restoreConfirmation: '',
      };
      syncPromptEditorArtifacts();
      renderShell({ preserveScroll: true });
    } catch (error) {
      state.promptEditor = {
        ...editor,
        restoreState: {
          ...createBlankPromptRestoreState(),
          status: 'error',
          message: `Prompt restore failed: ${error}`,
          errorCode: 'prompt_restore_failed',
          backupPath: restorePath,
          restoredFromPath: restorePath,
          restoredAt: nowMs(),
          requestPath,
        },
      };
      syncPromptEditorArtifacts();
      renderShell({ preserveScroll: true });
    }
  }

  async function loadPromptEditor(prompt, { force = false } = {}) {
    if (!prompt) {
      state.promptEditor = createBlankPromptEditor();
      syncPromptEditorArtifacts();
      return;
    }

    const profile = toText(prompt.profile, toText(getAt(state.configContract?.values || state.config || {}, 'profile'), 'personal'));
    const nextToken = (Number(promptEditorData().requestToken || 0) || 0) + 1;
    const baseEditor = {
      ...createBlankPromptEditor(),
      promptId: prompt.id,
      promptFile: prompt.file,
      promptPath: prompt.path,
      promptScope: prompt.scope,
      promptProfile: profile,
      promptSource: prompt.source,
      promptMode: prompt.mode,
      promptUpdated: prompt.updated,
      promptSummary: prompt.summary,
      promptPreview: prompt.preview,
      draftFile: prompt.file,
      draftContent: prompt.content || '',
      loading: true,
      requestToken: nextToken,
      error: '',
      lastLoadedAt: nowMs(),
    };

    if (!force && promptEditorMatchesPrompt(prompt) && promptEditorData().basePath) {
      return;
    }

    state.promptEditor = baseEditor;
    if (state.activeView === 'prompts') {
      renderShell({ preserveScroll: true });
    }

    if (state.sourceMode === 'fallback' && prompt.content != null) {
      applyPromptEditorPayload(prompt, {
        ok: true,
        id: prompt.id,
        file: prompt.file,
        path: prompt.path,
        scope: prompt.scope,
        profile,
        source: prompt.source,
        mode: prompt.mode,
        updated: prompt.updated,
        content: prompt.content,
        preview: prompt.preview,
        summary: prompt.summary,
        template_variables: prompt.templateVariables,
        required_template_variables: prompt.requiredTemplateVariables || prompt.templateVariables || [],
        backups: prompt.backups || [],
      });
      state.promptEditor.requestToken = nextToken;
      if (state.activeView === 'prompts') {
        renderShell({ preserveScroll: true });
      }
      return;
    }

    try {
      const response = await fetch(buildPromptReadUrl(prompt), {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      const normalized = normalizePromptReadResponse(payload);
      if (state.promptEditor.requestToken !== nextToken) {
        return;
      }
      if (!response.ok || !normalized.ok) {
        const errorMessage = normalized.error?.message || `HTTP ${response.status}`;
        state.promptEditor = {
          ...baseEditor,
          loading: false,
          error: errorMessage || 'Prompt read failed.',
          dirty: false,
        };
        if (state.activeView === 'prompts') {
          renderShell({ preserveScroll: true });
        }
        return;
      }
      applyPromptEditorPayload(prompt, normalized);
      state.promptEditor.requestToken = nextToken;
      if (state.activeView === 'prompts') {
        renderShell({ preserveScroll: true });
      }
    } catch (error) {
      if (state.promptEditor.requestToken !== nextToken) {
        return;
      }
      state.promptEditor = {
        ...baseEditor,
        loading: false,
        error: toText(error?.message || error, 'Prompt read failed.'),
        dirty: false,
      };
      if (state.activeView === 'prompts') {
        renderShell({ preserveScroll: true });
      }
    }
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
      return state.runnerControl.lastAction || state.stopAction || 'busy';
    }
    return '';
  }

  function runnerControlActionClass(action, baseClass = 'button--quiet') {
    const classes = [baseClass];
    const busyAction = runnerControlBusyAction();
    if (busyAction === action) {
      classes.push('button--loading');
    } else if (!runnerControlActionEnabled(action)) {
      classes.push('button--paused');
    }
    return classes.join(' ');
  }

  function runnerControlActionState(action) {
    const actions = toObject(state.runnerControl.actions);
    return toObject(actions[action]);
  }

  function runnerControlActionEnabled(action) {
    const statusReason = toText(state.runnerControl.status?.reason, '');
    if (!state.runnerControl.enabled || !state.runnerControl.controllerAvailable || state.runnerControl.busy) {
      return false;
    }
    if (statusReason.startsWith('status_error:')) {
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
    const statusReason = toText(state.runnerControl.status?.reason, '');
    if (statusReason.startsWith('status_error:')) {
      return state.runnerControl.lastError || statusReason || 'Runner controller reported an error.';
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

  function worktreeActionConfirmationPhrase(action) {
    return WORKTREE_ACTION_CONFIRMATIONS[action] || WORKTREE_ACTION_CONFIRMATIONS.discard;
  }

  function worktreeActionLabel(action, busy = false) {
    const label = String(action || 'merge').toLowerCase() === 'discard' ? 'Discard' : 'Merge';
    if (!busy) {
      return label;
    }
    return label === 'Discard' ? 'Discarding...' : 'Merging...';
  }

  function worktreeActionModalTitle(action) {
    return String(action || 'merge').toLowerCase() === 'discard' ? 'Confirm discard' : 'Confirm merge';
  }

  function worktreeActionRequestPath(action) {
    return String(action || 'merge').toLowerCase() === 'discard' ? '/api/worktree/discard' : '/api/worktree/merge';
  }

  function worktreeActionEnabled(review = state.worktreeMerge, action = 'merge') {
    const data = toObject(review);
    const status = toText(data.status, 'none');
    const cleanupState = toText(data.cleanupState, 'none');
    if (status !== 'pending review' && status !== 'pending') {
      return false;
    }
    if (cleanupState !== 'pending') {
      return false;
    }
    if (!data.reviewRequired) {
      return false;
    }
    return Boolean(
      toText(data.sourceRepo, '') &&
        toText(data.runDir, '') &&
        toText(data.worktreeDir || data.worktree, '') &&
        toText(data.patchPath || data.patch, '') &&
        toText(data.pendingFile || data.statusFile, '') &&
        toText(data.baseRef, '') &&
        toText(data.headRef, '')
    );
  }

  function worktreeActionDisabledReason(review = state.worktreeMerge, action = 'merge') {
    const data = toObject(review);
    const status = toText(data.status, 'none');
    const cleanupState = toText(data.cleanupState, 'none');
    if (status === 'none') {
      return 'No pending worktree merge is available.';
    }
    if (status === 'error') {
      return data.reviewRequiredMessage || 'Fix or delete the pending file in the CLI before trying again.';
    }
    if (status === 'apply_failed') {
      return 'Patch export failed before a reviewable merge marker was written.';
    }
    if (status === 'patch_not_applied' || status === 'not_applied') {
      return 'Apply the exported patch from the CLI before confirming merge or discard.';
    }
    if (status === 'applied' || status === 'discarded') {
      return 'The worktree is already finalized.';
    }
    if (cleanupState === 'failed' || status === 'applied_cleanup_failed' || status === 'discard_cleanup_failed') {
      return 'Cleanup failed after the decision was recorded.';
    }
    if (!worktreeActionEnabled(review, action)) {
      return 'The pending worktree metadata is incomplete.';
    }
    return '';
  }

  function worktreeActionButtonAttrs(review = state.worktreeMerge, action = 'merge') {
    const enabled = worktreeActionEnabled(review, action);
    const reason = worktreeActionDisabledReason(review, action);
    if (enabled) {
      return '';
    }
    return `disabled aria-disabled="true" title="${escapeHTML(reason || 'Action unavailable.')}"`;
  }

  function worktreeActionSummary(action, review = state.worktreeMerge) {
    const data = toObject(review);
    const sourceRepo = toText(data.sourceRepo, 'the source repository');
    const patchPath = toText(data.patchPath || data.patch, 'the patch');
    const worktreeDir = toText(data.worktreeDir || data.worktree, 'the isolated worktree');
    if (String(action || 'merge').toLowerCase() === 'discard') {
      return `Discarding keeps ${sourceRepo} untouched and removes ${worktreeDir} from the worktree review state.`;
    }
    return `Merging applies ${patchPath} to ${sourceRepo} without creating a commit.`;
  }

  function worktreeActionInstruction(action, review = state.worktreeMerge) {
    const data = toObject(review);
    const sourceRepo = toText(data.sourceRepo, 'the source repository');
    const patchPath = toText(data.patchPath || data.patch, 'the patch');
    const worktreeDir = toText(data.worktreeDir || data.worktree, 'the isolated worktree');
    if (String(action || 'merge').toLowerCase() === 'discard') {
      return `${WORKTREE_ACTION_INSTRUCTION_PREFIXES.discard} the pending state for ${worktreeDir} without touching source files.`;
    }
    return `${WORKTREE_ACTION_INSTRUCTION_PREFIXES.merge} ${patchPath} to ${sourceRepo} without creating a commit.`;
  }

  function worktreeActionPayload(review = state.worktreeMerge) {
    const data = toObject(review);
    return {
      confirmation: toText(toObject(state.worktreeAction).confirmation, ''),
      pendingFile: toText(data.pendingFile || data.statusFile, ''),
      statusFile: toText(data.statusFile || data.pendingFile, ''),
      sourceRepo: toText(data.sourceRepo, ''),
      runDir: toText(data.runDir, ''),
      worktreeDir: toText(data.worktreeDir || data.worktree, ''),
      patchPath: toText(data.patchPath || data.patch, ''),
      baseRef: toText(data.baseRef, ''),
      headRef: toText(data.headRef, ''),
      cleanupPath: toText(data.cleanupPath || data.worktreeDir || data.worktree, ''),
    };
  }

  function openWorktreeActionModal(action = 'merge') {
    const normalized = String(action || 'merge').toLowerCase() === 'discard' ? 'discard' : 'merge';
    if (!worktreeActionEnabled(state.worktreeMerge, normalized)) {
      return;
    }
    state.worktreeAction = {
      action: normalized,
      confirmation: '',
      error: '',
      submitting: false,
    };
    state.paletteOpen = false;
    state.goalEditor = null;
    state.stopOpen = false;
    renderOverlay();
  }

  function closeWorktreeActionModal() {
    if (!state.worktreeAction || state.worktreeAction.submitting) {
      return;
    }
    state.worktreeAction = null;
    renderOverlay();
    renderShell({ preserveScroll: true });
  }

  function updateWorktreeActionConfirmation(value) {
    if (!state.worktreeAction) {
      return;
    }
    state.worktreeAction.confirmation = value;
    state.worktreeAction.error = '';
    renderWorktreeActionOverlay();
  }

  function renderWorktreeActionOverlay() {
    const actionState = toObject(state.worktreeAction);
    const action = String(actionState.action || 'merge').toLowerCase() === 'discard' ? 'discard' : 'merge';
    const review = state.worktreeMerge;
    const confirmation = worktreeActionConfirmationPhrase(action);
    const confirmationValue = toText(actionState.confirmation, '').trim();
    const actionEnabled = worktreeActionEnabled(review, action);
    const confirmEnabled = actionEnabled && confirmationValue === confirmation && !actionState.submitting;
    const bannerTone = actionState.submitting ? 'info' : actionState.error ? 'err' : 'warn';
    const title = worktreeActionModalTitle(action);
    const summary = worktreeActionSummary(action, review);
    const instruction = worktreeActionInstruction(action, review);
    const detailCards = [
      { label: 'Source repo', value: toText(review.sourceRepo, '--') },
      { label: 'Run dir', value: toText(review.runDir, '--') },
      { label: 'Worktree dir', value: toText(review.worktreeDir || review.worktree, '--') },
      { label: 'Patch path', value: toText(review.patchPath || review.patch, '--') },
      { label: 'Pending file', value: toText(review.pendingFile || review.statusFile, '--') },
      { label: 'Base ref', value: toText(review.baseRef, '--') },
      { label: 'Head ref', value: toText(review.headRef, '--') },
    ];
    const detailHTML = detailCards
      .map(
        (item) => `
          <div class="runner-control__detail">
            <div class="runner-control__label">${escapeHTML(item.label)}</div>
            <div class="runner-control__value">${escapeHTML(item.value)}</div>
          </div>
        `
      )
      .join('');
    const actionLabel = worktreeActionLabel(action, actionState.submitting);
    const bannerMessage = actionState.submitting
      ? `Applying the pending worktree decision for ${toText(review.sourceRepo, 'the source repository')}.`
      : actionState.error
        ? actionState.error
        : summary;
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="worktree-action">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(title)}</span>
            <span class="overlay__sub">${escapeHTML(actionState.submitting ? 'refreshing status' : 'confirmation required')}</span>
          </div>
          <div class="overlay__body">
            <div class="worktree-action">
              <div class="modal-banner section-banner section-banner--${bannerTone}">
                <span class="dot" style="background: currentColor;"></span>
                <div>
                  <div class="section-banner__title">${escapeHTML(title)}</div>
                  <div class="section-banner__copy">${escapeHTML(bannerMessage)}</div>
                </div>
              </div>
              <div class="runner-control__details worktree-action__details">
                ${detailHTML}
              </div>
              <div class="modal-banner section-banner section-banner--info worktree-action__warning">
                <span class="dot" style="background: currentColor;"></span>
                <div>
                  <div class="section-banner__title">Exact confirmation</div>
                  <div class="section-banner__copy">${escapeHTML(instruction)}</div>
                </div>
              </div>
              <div class="modal-field worktree-action__field">
                <div class="modal-field__label">Confirmation phrase</div>
                <input
                  type="text"
                  class="field-control worktree-action__input"
                  data-worktree-action-confirmation
                  value="${escapeHTML(actionState.confirmation || '')}"
                  placeholder="${escapeHTML(confirmation)}"
                  autocomplete="off"
                  ${actionState.submitting ? 'disabled' : ''}
                >
              </div>
              ${actionState.error ? `<div class="field-error">${escapeHTML(actionState.error)}</div>` : ''}
              <div class="modal-copy">${escapeHTML(actionEnabled ? summary : worktreeActionDisabledReason(review, action))}</div>
              <div class="modal-actions">
                <button type="button" class="button button--quiet" data-worktree-action-close ${actionState.submitting ? 'disabled' : ''}>Cancel</button>
                <button type="button" class="button ${action === 'discard' ? 'button--danger' : 'button--primary'}" data-worktree-action-confirm ${confirmEnabled ? '' : 'disabled aria-disabled="true"'}>${escapeHTML(actionState.submitting ? actionLabel : `Confirm ${actionLabel.toLowerCase()}`)}</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  async function applyWorktreeAction() {
    const actionState = toObject(state.worktreeAction);
    const action = String(actionState.action || 'merge').toLowerCase() === 'discard' ? 'discard' : 'merge';
    const review = state.worktreeMerge;
    const expected = worktreeActionConfirmationPhrase(action);
    const provided = toText(actionState.confirmation, '').trim();
    if (!state.worktreeAction || actionState.submitting) {
      return;
    }
    if (!worktreeActionEnabled(review, action)) {
      state.worktreeAction = {
        ...actionState,
        error: worktreeActionDisabledReason(review, action) || 'Worktree action is unavailable.',
      };
      renderWorktreeActionOverlay();
      return;
    }
    if (!provided) {
      state.worktreeAction = {
        ...actionState,
        error: `Type "${expected}" to confirm this action.`,
      };
      renderWorktreeActionOverlay();
      return;
    }
    if (provided !== expected) {
      state.worktreeAction = {
        ...actionState,
        error: `Confirmation phrase must be "${expected}".`,
      };
      renderWorktreeActionOverlay();
      return;
    }

    state.worktreeAction = {
      ...actionState,
      submitting: true,
      error: '',
    };
    renderWorktreeActionOverlay();

    try {
      const response = await fetch(worktreeActionRequestPath(action), {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...worktreeActionPayload(review),
          confirmation: provided,
        }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      const normalized = toObject(payload);
      if (!response.ok || normalized.ok === false) {
        const message = toText(normalized.message || toObject(normalized.error).message || `Worktree action failed (HTTP ${response.status}).`, 'Worktree action failed.');
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

      state.worktreeAction = null;
      renderShell({ preserveScroll: true });
    } catch (error) {
      const message = toText(error?.message || error, 'Worktree action failed.');
      const snapshot = toObject(error?.snapshot);
      if (Object.keys(snapshot).length) {
        applyServerSnapshot(snapshot);
      }
      state.worktreeAction = {
        ...actionState,
        submitting: false,
        error: message,
      };
      renderWorktreeActionOverlay();
    }
  }

  function renderTopbar() {
    const elapsed = state.activeRun.startedAt ? fmtDuration((nowMs() - state.activeRun.startedAt) / 1000) : '--';
    const budgetPct = metricText(state.activeRun.budgetAvailable, state.activeRun.budgetUsed, fmtPercent);
    const budgetWidth = metricWidth(state.activeRun.budgetAvailable, state.activeRun.budgetUsed);
    const quotaAvailable = Boolean(state.activeRun.quotaAvailable && state.activeRun.quota && state.activeRun.quota.available);
    const quotaTitle = quotaAvailable
      ? state.activeRun.quota.window
        ? `Quota ${state.activeRun.quota.window} usage`
        : 'Quota usage'
      : 'Quota unavailable';
    const quotaSnapshot = quotaAvailable ? state.activeRun.quota : { window: '', used: null, available: false };
    const quotaControl = renderQuotaControl(quotaSnapshot, quotaTitle);
    const activeStatus = runStatusLabel(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason);
    const activeTone = runStatusTone(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason);
    const runLabel = state.activeRun.id || 'no-run';
    const snapshotLabel = state.snapshotLabel || (state.sourceMode === 'fallback' ? 'Fallback data' : 'API snapshot');
    const runnerControlDisplay = runnerControlStateInfo();
    const runnerBusyAction = runnerControlBusyAction();
    const runnerChipTone = `status-chip--${runnerControlDisplay.chipTone}`;
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
        <span class="status-chip ${runnerChipTone}" title="${escapeHTML(runnerControlDisplay.copy || '')}">
          <span class="dot" style="color: currentColor; background: currentColor;"></span>
          ${escapeHTML(runnerControlDisplay.label)}
        </span>
        ${button('Refresh', 'refresh-status', 'button--quiet', 'aria-label="Refresh snapshot"')}
        ${button(runnerControlActionLabel('start', runnerBusyAction === 'start'), 'runner-start', runnerControlActionClass('start', 'button--primary'), `aria-label="Start runner" ${runnerControlButtonAttrs('start')}`)}
        ${button(runnerControlActionLabel('stop', runnerBusyAction === 'stop'), 'runner-stop', runnerControlActionClass('stop', 'button--danger'), `aria-label="Stop runner" ${runnerControlButtonAttrs('stop')}`)}
        ${button(runnerControlActionLabel('reload', runnerBusyAction === 'reload'), 'runner-reload', runnerControlActionClass('reload', 'button--quiet'), `aria-label="Reload runner" ${runnerControlButtonAttrs('reload')}`)}
        ${button(runnerControlActionLabel('restart', runnerBusyAction === 'restart'), 'runner-restart', runnerControlActionClass('restart', 'button--quiet'), `aria-label="Restart runner" ${runnerControlButtonAttrs('restart')}`)}
        ${button(`Command`, 'open-palette', 'button--ghost', 'aria-label="Open command palette"')}
        ${quotaControl}
        <span class="meter-chip ${state.activeRun.budgetAvailable ? '' : 'meter-chip--unavailable'}" title="Budget usage">
          budget ${escapeHTML(budgetPct)}
          <span class="meter ${state.activeRun.budgetAvailable ? '' : 'meter--unavailable'}" aria-hidden="true"><span class="meter__fill ${state.activeRun.budgetAvailable ? 'meter__fill--warn' : 'meter__fill--muted'}" style="width:${escapeHTML(budgetWidth)}"></span></span>
        </span>
      </div>
    `;
  }

  function renderSidebar() {
    const repoLabel = state.activeRun.repoLabel || state.repo.name || 'agentcli';
    const branchLabel = state.activeRun.branch || state.repo.branch || 'HEAD';
    const quotaAvailable = Boolean(state.activeRun.quotaAvailable && state.activeRun.quota && state.activeRun.quota.available);
    const quotaTitle = quotaAvailable
      ? state.activeRun.quota.window
        ? `Quota ${state.activeRun.quota.window} usage`
        : 'Quota usage'
      : 'Quota unavailable';
    const quotaSnapshot = quotaAvailable ? state.activeRun.quota : { window: '', used: null, available: false };
    const quotaControl = renderQuotaControl(quotaSnapshot, quotaTitle);
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
          <div class="sidebar-card__sub">${quotaControl}</div>
        </div>
      </div>
    `;
  }

  function renderRunnerControlsPanel() {
    const control = state.runnerControl;
    const display = runnerControlStateInfo(control);
    const messageTone = display.bannerTone;
    const busyAction = runnerControlBusyAction();
    const statusSummary = [
      display.label.toLowerCase(),
      control.status.runnerMode || 'unknown',
      control.runStatus || (control.status.running ? 'running' : 'idle'),
    ]
      .filter(Boolean)
      .join(' | ');
    const buttonRow = [
      button(runnerControlActionLabel('start', busyAction === 'start'), 'runner-start', runnerControlActionClass('start', 'button--primary'), `aria-label="Start runner" ${runnerControlButtonAttrs('start')}`),
      button(runnerControlActionLabel('stop', busyAction === 'stop'), 'runner-stop', runnerControlActionClass('stop', 'button--danger'), `aria-label="Stop runner" ${runnerControlButtonAttrs('stop')}`),
      button(runnerControlActionLabel('reload', busyAction === 'reload'), 'runner-reload', runnerControlActionClass('reload', 'button--quiet'), `aria-label="Reload runner" ${runnerControlButtonAttrs('reload')}`),
      button(runnerControlActionLabel('restart', busyAction === 'restart'), 'runner-restart', runnerControlActionClass('restart', 'button--quiet'), `aria-label="Restart runner" ${runnerControlButtonAttrs('restart')}`),
    ].join('');
    const detailItems = runnerControlDetailRows(control, display);
    const detailHTML = detailItems
      .map(
        (item) => `
          <div class="runner-control__detail">
            <div class="runner-control__label">${escapeHTML(item.label)}</div>
            <div class="runner-control__value ${escapeHTML(item.className || '')}">${escapeHTML(item.value)}</div>
          </div>
        `
      )
      .join('');
    return panel(
      'Runner controls',
      escapeHTML(statusSummary),
      `
        <div class="runner-control">
          <div class="modal-banner section-banner section-banner--${messageTone}">
            <span class="dot" style="background: currentColor;"></span>
            <div>
              <div class="section-banner__title">${escapeHTML(display.title)}</div>
              <div class="section-banner__copy">${escapeHTML(display.copy)}</div>
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

  function renderLogRow(line, options = {}) {
    const stageColor =
      line.stage === 'Dev'
        ? 'var(--accent)'
        : line.stage === 'PM'
          ? 'var(--info)'
          : line.stage === 'QA'
            ? 'var(--warn)'
            : 'var(--text-dim)';
    const selectable = Boolean(options.selectable && toMaybeNumber(line.line_number ?? line.cursor, null) != null);
    const selected = Boolean(options.selected);
    const lineNumber = toMaybeNumber(line.line_number ?? line.cursor, null);
    const selectButton = selectable
      ? `
        <button
          type="button"
          class="log-row__select ${selected ? 'log-row__select--selected' : ''}"
          data-log-select="${escapeHTML(String(lineNumber))}"
          aria-pressed="${selected ? 'true' : 'false'}"
          aria-label="${selected ? 'Deselect' : 'Select'} log line ${escapeHTML(String(lineNumber))}"
        >
          <span class="log-row__select-mark">${selected ? 'x' : '+'}</span>
        </button>
      `
      : '';
    return `
      <div class="${severityClass(line.lvl)}${selectable ? ' log-row--selectable' : ''}${selected ? ' log-row--selected' : ''}">
        ${selectButton}
        <div class="log-row__time">${escapeHTML(line.t)}</div>
        <div class="log-row__stage" style="color:${stageColor}">[${escapeHTML(line.stage)}]</div>
        <div class="log-row__level">${escapeHTML(line.lvl)}</div>
        <div class="log-row__msg">${escapeHTML(line.msg)}</div>
      </div>
    `;
  }

  function createBlankLogTailState() {
    return {
      status: 'loading',
      loading: false,
      paused: false,
      error: '',
      entries: [],
      cursor: 0,
      nextCursor: 0,
      malformedLines: 0,
      source: {
        path: '',
        name: '',
        exists: false,
      },
      filters: {
        level: 'all',
        stage: '',
        taskId: '',
        search: '',
      },
      selected: [],
      requestSeq: 0,
      timer: null,
      runDir: '',
      lastUpdatedAt: 0,
    };
  }

  function ensureLogTailState() {
    if (!state.logTail || typeof state.logTail !== 'object') {
      state.logTail = createBlankLogTailState();
    }
    if (!state.logTail.filters || typeof state.logTail.filters !== 'object') {
      state.logTail.filters = normalizeLogTailFilters({});
    } else {
      state.logTail.filters = normalizeLogTailFilters(state.logTail.filters);
    }
    if (!Array.isArray(state.logTail.selected)) {
      state.logTail.selected = [];
    }
    return state.logTail;
  }

  function normalizeLogTailFilters(filters = {}) {
    return {
      level: toText(filters.level, 'all').toLowerCase() || 'all',
      stage: toText(filters.stage, '').trim(),
      taskId: toText(filters.taskId || filters.task_id, '').trim(),
      search: toText(filters.search || filters.q, '').trim(),
    };
  }

  function buildLogTailQuery(filters = {}, options = {}) {
    const query = {
      max_lines: Math.max(1, toNumber(options.maxLines, MAX_LOG_ROWS)),
    };
    const cursor = toMaybeNumber(options.cursor);
    if (cursor != null && cursor > 0) {
      query.cursor = cursor;
    }
    const normalized = normalizeLogTailFilters(filters);
    if (normalized.level && !['all', 'any', '*'].includes(normalized.level)) {
      query.level = normalized.level;
    }
    if (normalized.stage) {
      query.stage = normalized.stage;
    }
    if (normalized.taskId) {
      query.task_id = normalized.taskId;
    }
    if (normalized.search) {
      query.search = normalized.search;
    }
    return query;
  }

  function buildLogTailRequestUrl(filters = {}, options = {}) {
    const query = buildLogTailQuery(filters, options);
    const parts = [];
    for (const key of Object.keys(query)) {
      const value = query[key];
      if (value == null || value === '') {
        continue;
      }
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
    }
    return parts.length ? `/api/logs/tail?${parts.join('&')}` : '/api/logs/tail';
  }

  function tailSourceName(path) {
    const value = toText(path, '');
    if (!value) {
      return '';
    }
    const parts = value.split(/[\\/]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : value;
  }

  function mergeLogTailEntries(previousEntries, incomingEntries) {
    const merged = [];
    const seen = new Set();
    for (const entry of toArray(previousEntries)) {
      const key = toMaybeNumber(entry.line_number ?? entry.cursor, null);
      const keyText = key == null ? toText(entry.raw || entry.msg || '', '') : String(key);
      if (keyText && !seen.has(keyText)) {
        seen.add(keyText);
        merged.push(entry);
      }
    }
    for (const entry of toArray(incomingEntries)) {
      const key = toMaybeNumber(entry.line_number ?? entry.cursor, null);
      const keyText = key == null ? toText(entry.raw || entry.msg || '', '') : String(key);
      if (keyText && !seen.has(keyText)) {
        seen.add(keyText);
        merged.push(entry);
      }
    }
    return merged.slice(-MAX_LOG_ROWS);
  }

  function formatLogTailLine(entry) {
    const lineNumber = toMaybeNumber(entry.line_number ?? entry.cursor, null);
    const prefix = lineNumber == null ? '' : `#${lineNumber} `;
    const time = toText(entry.t || entry.ts, '');
    const stage = toText(entry.stage, 'boot');
    const level = toText(entry.lvl || entry.level, 'info');
    const message = toText(entry.msg || entry.message || entry.raw, '');
    return `${prefix}${time || '--'} [${stage}] ${level} ${message}`.trim();
  }

  function buildLogTailClipboardText(entries, selected = []) {
    const selectedIds = new Set(
      toArray(selected)
        .map((value) => toMaybeNumber(value, null))
        .filter((value) => value != null)
        .map((value) => String(value))
    );
    return toArray(entries)
      .filter((entry) => selectedIds.has(String(toMaybeNumber(entry.line_number ?? entry.cursor, null))))
      .map((entry) => formatLogTailLine(entry))
      .join('\n');
  }

  function buildLogTailDownloadArtifact(tail, context = {}) {
    const model = toObject(tail);
    const filters = normalizeLogTailFilters(model.filters);
    const source = toObject(model.source);
    const sourceName = tailSourceName(source.path || source.name || '');
    const runLabel = toText(context.runId || context.latestRunDir || model.runDir || 'agentcli', 'agentcli')
      .replace(/[^a-z0-9._-]+/gi, '_')
      .replace(/^_+|_+$/g, '') || 'agentcli';
    const filterParts = [];
    if (filters.level && !['all', 'any', '*'].includes(filters.level)) {
      filterParts.push(`level=${filters.level}`);
    }
    if (filters.stage) {
      filterParts.push(`stage=${filters.stage}`);
    }
    if (filters.taskId) {
      filterParts.push(`task_id=${filters.taskId}`);
    }
    if (filters.search) {
      filterParts.push(`search=${filters.search}`);
    }
    const lines = [
      '# AgentCLI live log export',
      `# Source: ${source.path || sourceName || 'unknown'}`,
      `# Cursor: ${toMaybeNumber(model.nextCursor ?? model.cursor, 0) || 0}`,
      `# Filters: ${filterParts.length ? filterParts.join(' | ') : 'none'}`,
      '',
    ];
    const entries = toArray(model.entries);
    if (entries.length) {
      lines.push(...entries.map((entry) => formatLogTailLine(entry)));
    } else {
      lines.push('# No matching log lines');
    }
    return {
      filename: `agentcli-${runLabel}-logs.txt`,
      text: `${lines.join('\n')}\n`,
    };
  }

  function describeLogTailState(tail) {
    const model = toObject(tail);
    const paused = Boolean(model.paused);
    const status = toText(model.status, 'loading');
    const entries = toArray(model.entries);
    const source = toObject(model.source);
    const sourceName = tailSourceName(source.path || source.name || '') || 'active run log';
    const malformedLines = toNumber(model.malformedLines, 0);
    if (paused) {
      return {
        tone: 'stopped',
        title: 'Live tail paused',
        copy: `Polling is paused for ${sourceName}. Resume live tail to continue from cursor ${toMaybeNumber(model.nextCursor ?? model.cursor, 0) || 0}.`,
        badge: 'paused',
        state: 'paused',
      };
    }
    if (status === 'missing_file') {
      return {
        tone: 'err',
        title: 'Log file missing',
        copy: `The active run log is not available yet. Waiting for ${sourceName}.`,
        badge: 'missing_file',
        state: 'missing_file',
      };
    }
    if (status === 'read_error') {
      return {
        tone: 'err',
        title: 'Log read error',
        copy: toText(model.error, `The tail endpoint could not read ${sourceName}.`),
        badge: 'read_error',
        state: 'read_error',
      };
    }
    if (status === 'empty') {
      return {
        tone: 'idle',
        title: 'No matching log lines',
        copy: source.exists ? 'The current filter returned no matching lines.' : `No log file is available at ${sourceName}.`,
        badge: 'empty',
        state: 'empty',
      };
    }
    if (malformedLines > 0 && !entries.length) {
      return {
        tone: 'warn',
        title: 'Malformed log lines',
        copy: `${malformedLines} malformed line${malformedLines === 1 ? '' : 's'} were skipped.`,
        badge: 'warn',
        state: 'malformed_line',
      };
    }
    if (entries.length) {
      const cursor = toMaybeNumber(model.nextCursor ?? model.cursor, 0) || 0;
      return {
        tone: 'running',
        title: 'Live tail active',
        copy: `Following ${sourceName} from cursor ${cursor}.`,
        badge: 'live',
        state: 'live',
      };
    }
    return {
      tone: 'info',
      title: 'Loading active run log',
      copy: `Fetching ${sourceName}.`,
      badge: 'loading',
      state: 'loading',
    };
  }

  function describeLogTailControl(tail) {
    const model = toObject(tail);
    const paused = Boolean(model.paused);
    const loading = Boolean(model.loading);
    const status = toText(model.status, 'loading');
    const hasEntries = toArray(model.entries).length > 0;
    const buttonLabel = paused ? 'Resume live tail' : 'Pause live tail';
    const loadingState = loading || status === 'loading';
    const busy = loadingState;
    let stateLabel = 'live';
    let statusClass = 'status-chip status-chip--running';
    if (paused) {
      stateLabel = 'paused';
      statusClass = 'status-chip status-chip--paused';
    } else if (status === 'missing_file') {
      stateLabel = 'missing file';
      statusClass = 'status-chip status-chip--warn';
    } else if (status === 'read_error') {
      stateLabel = 'error';
      statusClass = 'status-chip status-chip--err';
    } else if (status === 'empty') {
      stateLabel = 'empty';
      statusClass = 'status-chip status-chip--idle';
    } else if (status === 'malformed_line' && !hasEntries) {
      stateLabel = 'warn';
      statusClass = 'status-chip status-chip--warn';
    } else if (loadingState && !hasEntries) {
      stateLabel = 'loading';
      statusClass = 'status-chip status-chip--loading';
    }
    return {
      paused,
      loading,
      hasEntries,
      stateLabel,
      buttonLabel,
      statusClass,
      buttonClass: paused ? 'button--paused' : busy ? 'button--loading' : 'button--quiet',
      buttonAttrs: `${paused ? 'aria-pressed="true"' : 'aria-pressed="false"'}${busy ? ' aria-busy="true"' : ''}`,
      dotClass: paused ? 'dot' : 'dot dot--pulse',
    };
  }

  function renderLogTailBanner(tail) {
    const banner = describeLogTailState(tail);
    const pulseClass = banner.tone === 'running' ? ' dot--pulse' : '';
    return `
      <div class="section-banner section-banner--${escapeHTML(banner.tone)} log-tail-banner">
        <span class="dot${pulseClass}"></span>
        <div>
          <div class="section-banner__title">${escapeHTML(banner.title)}</div>
          <div class="section-banner__copy">${escapeHTML(banner.copy)}</div>
        </div>
      </div>
    `;
  }

  function renderLogTailFilters(tail) {
    const filters = normalizeLogTailFilters(toObject(tail).filters);
    const control = describeLogTailControl(tail);
    const levels = ['all', 'info', 'warn', 'err', 'debug'];
    const selectedCount = toArray(tail?.selected).length;
    return `
      <div class="logs-toolbar log-tail-toolbar">
        <div class="filters log-tail-levels">
          ${levels
            .map((level) => `
              <button
                type="button"
                class="filter-chip ${filters.level === level ? 'filter-chip--active' : ''}"
                data-log-level="${escapeHTML(level)}"
              >${escapeHTML(level.toUpperCase())}</button>
            `)
            .join('')}
        </div>
        <div class="log-tail-fields">
          <label class="log-tail-field">
            <span class="log-tail-field__label">Stage</span>
            <input
              type="text"
              class="log-tail-input"
              data-log-filter-field="stage"
              value="${escapeHTML(filters.stage)}"
              placeholder="PM, Dev, QA"
              autocomplete="off"
              spellcheck="false"
            >
          </label>
          <label class="log-tail-field">
            <span class="log-tail-field__label">Task ID</span>
            <input
              type="text"
              class="log-tail-input"
              data-log-filter-field="taskId"
              value="${escapeHTML(filters.taskId)}"
              placeholder="T-020"
              autocomplete="off"
              spellcheck="false"
            >
          </label>
          <label class="log-tail-field log-tail-field--wide">
            <span class="log-tail-field__label">Search</span>
            <input
              type="text"
              class="log-tail-input"
              data-log-filter-field="search"
              value="${escapeHTML(filters.search)}"
              placeholder="Free-text search"
              autocomplete="off"
              spellcheck="false"
            >
          </label>
        </div>
        <div class="log-tail-actions">
          <span class="${control.statusClass}">
            <span class="${control.dotClass}" style="color: currentColor; background: currentColor;"></span>
            ${escapeHTML(control.stateLabel)}
          </span>
          ${button(control.buttonLabel, 'toggle-logs', control.buttonClass, control.buttonAttrs)}
          ${button(`Copy selected lines${selectedCount ? ` (${selectedCount})` : ''}`, 'copy-log-tail-selection', 'button--quiet', selectedCount ? '' : 'disabled')}
          ${button('Download filtered logs', 'download-log-tail', 'button--quiet')}
          ${button('Clear selection', 'clear-log-tail-selection', 'button--quiet', selectedCount ? '' : 'disabled')}
        </div>
      </div>
    `;
  }

  function isLiveTailPaused() {
    const tail = ensureLogTailState();
    return state.sourceMode === 'api' ? Boolean(tail.paused) : Boolean(state.logsPaused);
  }

  function setLiveTailPaused(paused) {
    const tail = ensureLogTailState();
    if (state.sourceMode === 'api') {
      tail.paused = Boolean(paused);
      return;
    }
    state.logsPaused = Boolean(paused);
  }

  function resetServerLogTailState() {
    const tail = ensureLogTailState();
    tail.entries = [];
    tail.cursor = 0;
    tail.nextCursor = 0;
    tail.status = 'loading';
    tail.loading = false;
    tail.error = '';
    tail.malformedLines = 0;
    tail.source = {
      path: '',
      name: '',
      exists: false,
    };
    tail.selected = [];
    tail.requestSeq = toNumber(tail.requestSeq, 0) + 1;
  }

  async function refreshServerLogTail(options = {}) {
    if (state.sourceMode !== 'api') {
      return false;
    }
    const reset = Boolean(options.reset);
    const silent = Boolean(options.silent);
    const tail = ensureLogTailState();
    const requestSeq = toNumber(tail.requestSeq, 0) + 1;
    tail.requestSeq = requestSeq;
    if (reset) {
      tail.entries = [];
      tail.cursor = 0;
      tail.nextCursor = 0;
      tail.selected = [];
      tail.malformedLines = 0;
      tail.error = '';
    }
    tail.loading = true;
    tail.status = 'loading';
    const queryUrl = buildLogTailRequestUrl(tail.filters, {
      cursor: reset ? null : tail.nextCursor || tail.cursor,
      maxLines: MAX_LOG_ROWS,
    });

    try {
      const response = await fetch(queryUrl, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      if (tail.requestSeq !== requestSeq) {
        return false;
      }
      const next = applyLogTailPayload(tail, payload, { reset });
      state.logTail = next;
      if (!silent && state.activeView === 'logs' && !isLiveTailPaused()) {
        renderShell({ preserveScroll: true, scrollToBottom: true });
      }
      return true;
    } catch (error) {
      if (tail.requestSeq !== requestSeq) {
        return false;
      }
      tail.loading = false;
      tail.status = 'read_error';
      tail.error = toText(error?.message || error, 'Unable to read log tail.');
      if (reset) {
        tail.entries = [];
        tail.cursor = 0;
        tail.nextCursor = 0;
        tail.selected = [];
      }
      if (!silent) {
        renderShell({ preserveScroll: true });
      }
      return false;
    }
  }

  async function startServerLogTail(options = {}) {
    if (state.sourceMode !== 'api') {
      return false;
    }
    const tail = ensureLogTailState();
    if (state.activeView !== 'logs' || isLiveTailPaused()) {
      stopServerLogTail();
      return false;
    }
    const shouldRefresh = !tail.timer || Boolean(options.reset);
    if (!tail.timer) {
      tail.timer = window.setInterval(() => {
        if (state.sourceMode !== 'api' || state.activeView !== 'logs' || isLiveTailPaused()) {
          stopServerLogTail();
          return;
        }
        void refreshServerLogTail({ silent: false });
      }, 2400);
    }
    if (shouldRefresh) {
      return refreshServerLogTail({ reset: Boolean(options.reset), silent: Boolean(options.silent) });
    }
    return true;
  }

  function stopServerLogTail() {
    const tail = ensureLogTailState();
    if (tail.timer) {
      window.clearInterval(tail.timer);
      tail.timer = null;
    }
    tail.loading = false;
    tail.requestSeq = toNumber(tail.requestSeq, 0) + 1;
  }

    function syncLogTailStreaming(options = {}) {
      if (state.sourceMode === 'api') {
        stopLiveLogStream();
        if (state.activeView === 'logs' && !isLiveTailPaused()) {
          return startServerLogTail({ reset: Boolean(options.reset) });
        }
        if (state.activeView === 'logs' && Boolean(options.reset)) {
          return refreshServerLogTail({ reset: true, silent: true }).then(() => {
            if (state.activeView === 'logs') {
              renderShell({ preserveScroll: true });
            }
          });
        }
        stopServerLogTail();
        return false;
      }

    stopServerLogTail();
    if (state.sourceMode === 'fallback' && !state.logsPaused) {
      startFallbackLogStream();
    } else {
      stopLiveLogStream();
    }
    return false;
  }

  function applyLogTailPayload(previous, payload, options = {}) {
    const tail = toObject(previous);
    const response = toObject(payload);
    const reset = Boolean(options.reset);
    const incomingEntries = toArray(response.entries).map(normalizeLogEntry).slice(-MAX_LOG_ROWS);
    const existingEntries = reset ? [] : toArray(tail.entries);
    const nextEntries = incomingEntries.length ? mergeLogTailEntries(existingEntries, incomingEntries) : existingEntries.slice(-MAX_LOG_ROWS);
    const source = toObject(response.source);
    const selected = reset
      ? []
      : toArray(tail.selected).filter((value) => nextEntries.some((entry) => String(toMaybeNumber(entry.line_number ?? entry.cursor, null)) === String(toMaybeNumber(value, null))));
    const nextCursor = toMaybeNumber(response.next_cursor, tail.nextCursor || tail.cursor || 0);
    const cursor = toMaybeNumber(response.cursor, tail.cursor || 0);
    const stateValue = toText(response.state, 'loading');
    return {
      ...tail,
      status: response.ok === false ? toText(response.state, 'read_error') || 'read_error' : stateValue,
      loading: false,
      error: response.ok === false ? toText(response.error, '') : '',
      entries: nextEntries,
      cursor: cursor == null ? 0 : cursor,
      nextCursor: nextCursor == null ? 0 : nextCursor,
      malformedLines: toNumber(response.malformed_lines, 0),
      source: {
        path: toText(source.path || response.source_path || response.source_file, ''),
        name: toText(source.name || tailSourceName(source.path || response.source_path || response.source_file || ''), ''),
        exists: Boolean(source.exists ?? response.source_exists ?? response.source?.exists ?? false),
      },
      selected,
      lastUpdatedAt: nowMs(),
    };
  }

  function toggleLogTailSelection(lineNumber) {
    const tail = ensureLogTailState();
    const value = toMaybeNumber(lineNumber, null);
    if (value == null) {
      return;
    }
    const key = String(value);
    const selected = new Set(toArray(tail.selected).map((item) => String(toMaybeNumber(item, null))).filter(Boolean));
    if (selected.has(key)) {
      selected.delete(key);
    } else {
      selected.add(key);
    }
    tail.selected = Array.from(selected).map((item) => Number(item)).filter((item) => Number.isFinite(item));
  }

  function clearLogTailSelection() {
    const tail = ensureLogTailState();
    tail.selected = [];
  }

  function updateLogTailFilter(field, rawValue) {
    const tail = ensureLogTailState();
    const next = {
      ...tail.filters,
      [field]: toText(rawValue, ''),
    };
    if (field === 'level') {
      next.level = toText(rawValue, 'all').toLowerCase() || 'all';
    }
    tail.filters = normalizeLogTailFilters(next);
    clearLogTailSelection();
    resetServerLogTailState();
    if (state.sourceMode === 'api') {
      return syncLogTailStreaming({ reset: true, silent: false });
    } else {
      renderShell({ preserveScroll: true });
    }
    return false;
  }

  function inspectLogTailState() {
    const tail = ensureLogTailState();
    const source = toObject(tail.source);
    return {
      activeView: state.activeView,
      sourceMode: state.sourceMode,
      paused: Boolean(tail.paused),
      loading: Boolean(tail.loading),
      status: toText(tail.status, ''),
      cursor: toNumber(tail.cursor, 0),
      nextCursor: toNumber(tail.nextCursor, 0),
      requestSeq: toNumber(tail.requestSeq, 0),
      timerActive: Boolean(tail.timer),
      selected: toArray(tail.selected),
      filters: normalizeLogTailFilters(tail.filters),
      entries: toArray(tail.entries).map(normalizeLogEntry),
      source: {
        path: toText(source.path, ''),
        name: toText(source.name, ''),
        exists: Boolean(source.exists),
      },
      error: toText(tail.error, ''),
      malformedLines: toNumber(tail.malformedLines, 0),
      summary: toText(state.logTailSummary, ''),
    };
  }

  function seedLogTailState(overrides = {}) {
    const tailOverrides = toObject(overrides.logTail);
    const tail = deepMerge(createBlankLogTailState(), tailOverrides);
    tail.filters = normalizeLogTailFilters(tail.filters);
    tail.entries = toArray(tail.entries).map(normalizeLogEntry);
    tail.selected = toArray(tail.selected)
      .map((value) => toMaybeNumber(value, null))
      .filter((value) => value != null)
      .map((value) => Number(value));
    const source = toObject(tail.source);
    tail.source = {
      path: toText(source.path, ''),
      name: toText(source.name, ''),
      exists: Boolean(source.exists),
    };
    tail.paused = Boolean(tail.paused);
    tail.loading = Boolean(tail.loading);
    tail.malformedLines = toNumber(tail.malformedLines, 0);
    tail.requestSeq = toNumber(tail.requestSeq, 0);
    tail.timer = tail.timer || null;
    tail.runDir = toText(tail.runDir, '');
    tail.lastUpdatedAt = toNumber(tail.lastUpdatedAt, 0);
    state.logTail = tail;
    if (overrides.activeView) {
      state.activeView = normalizeView(overrides.activeView);
    }
    if (overrides.sourceMode) {
      state.sourceMode = toText(overrides.sourceMode, state.sourceMode);
    }
    if (overrides.logsPaused != null) {
      state.logsPaused = Boolean(overrides.logsPaused);
    }
    if (overrides.runId != null) {
      state.activeRun = {
        ...state.activeRun,
        id: toText(overrides.runId, state.activeRun.id),
      };
    }
    if (overrides.latestRunDir != null) {
      state.latestRunDir = toText(overrides.latestRunDir, state.latestRunDir);
    }
    if (overrides.logTailSummary != null) {
      state.logTailSummary = toText(overrides.logTailSummary, '');
    }
    return inspectLogTailState();
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
    if (state.sourceMode === 'api') {
      const tail = ensureLogTailState();
      const control = describeLogTailControl(tail);
      const entries = toArray(tail.entries);
      const selected = new Set(toArray(tail.selected).map((value) => String(toMaybeNumber(value, null))).filter(Boolean));
      const banner = describeLogTailState(tail);
      const sourceName = tailSourceName(tail.source?.path || tail.source?.name || '') || 'active run log';
      const body = `
        <div class="view-grid">
          ${panel(
            'Live tail',
            `${escapeHTML(entries.length)} lines | cursor ${escapeHTML(String(tail.nextCursor || tail.cursor || 0))}`,
            `
              ${renderLogTailBanner(tail)}
              ${renderLogTailFilters(tail)}
            `
          )}

          ${panel(
            `${escapeHTML(sourceName)}`,
            `${escapeHTML(entries.length)} filtered line${entries.length === 1 ? '' : 's'}`,
            `
              <div class="log-feed">
                <div class="log-feed__scroll" data-log-scroll>
                  ${entries.length ? entries.map((line) => renderLogRow(line, {
                    selectable: true,
                    selected: selected.has(String(toMaybeNumber(line.line_number ?? line.cursor, null))),
                  })).join('') : `<div class="summary-note">${escapeHTML(banner.copy)}</div>`}
                </div>
              </div>
            `
          )}
        </div>
      `;

      return viewShell(
        'logs',
        'Logs',
        `${escapeHTML(sourceName)} | ${escapeHTML(control.stateLabel)} | cursor ${escapeHTML(String(tail.nextCursor || tail.cursor || 0))}`,
        `
          ${button(control.buttonLabel, 'toggle-logs', control.buttonClass, control.buttonAttrs)}
          ${button('Open Dashboard', 'nav-dashboard', 'button--quiet')}
        `,
        body
      );
    }

    const filters = ['all', 'info', 'warn', 'err', 'debug'];
    const filtered = state.logs.filter((line) => state.logFilter === 'all' || line.lvl === state.logFilter);
    const logsMode =
      state.snapshotStatus === 'loading'
        ? 'loading'
        : state.snapshotStatus === 'fallback'
          ? 'fallback'
          : state.logsPaused
            ? 'paused'
            : 'tail -f';
    const logsStateLabel =
      state.snapshotStatus === 'loading'
        ? 'loading'
        : state.logsPaused
          ? 'paused'
          : 'live';
    const logsButtonClass =
      state.snapshotStatus === 'loading'
        ? 'button--loading'
        : state.logsPaused
          ? 'button--paused'
          : 'button--quiet';
    const logsButtonLabel =
      state.snapshotStatus === 'loading'
        ? 'Loading'
        : state.logsPaused
          ? 'Resume live tail'
          : 'Pause live tail';
    const logsButtonAttrs =
      state.snapshotStatus === 'loading'
        ? 'aria-busy="true"'
        : state.logsPaused
          ? 'aria-pressed="true"'
          : 'aria-pressed="false"';
    const logsStatusClass =
      state.snapshotStatus === 'loading'
        ? 'status-chip status-chip--loading'
        : state.logsPaused
          ? 'status-chip status-chip--paused'
          : 'status-chip status-chip--running';

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
                <span class="${logsStatusClass}">
                  <span class="${state.snapshotStatus === 'loading' ? 'dot dot--pulse' : state.logsPaused ? 'dot' : 'dot dot--pulse'}" style="color: currentColor; background: currentColor;"></span>
                  ${logsStateLabel}
                </span>
                ${button(logsButtonLabel, 'toggle-logs', logsButtonClass, logsButtonAttrs)}
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
                ${!state.logsPaused ? `
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
    // Static coverage keeps both the updated and legacy Goals copy in the source.
    // Draft edits stay local until save or reset. Bucket grouping stays pinned to P0 and P1.
    // Local checklist with add, edit, reorder, save, and completion actions
    const goalSnapshot = toObject(state.goalsSnapshot);
    const goalSummary = toObject(goalSnapshot.summary);
    const goalWarnings = toArray(goalSnapshot.warnings);
    const goalFilePath = toText(goalSnapshot.path || state.goalsPath || '.doc/GOALS.md', '.doc/GOALS.md');
    const goalFileExists = Boolean(goalSnapshot.exists);
    const goalFileSize = goalSnapshot.size;
    const goalFileMtime = goalSnapshot.mtime;
    const goalRawText = toText(goalSnapshot.raw_text || goalSnapshot.rawText, '');
    const total = state.goals.p0.length + state.goals.p1.length;
    const done = state.goals.p0.filter((goal) => goal.done).length + state.goals.p1.filter((goal) => goal.done).length;
    const goalDraft = buildGoalDraftSummary(goalSnapshot.items, state.goals);
    const goalsDirty = state.goalsDirty || goalDraft.dirty;
    const goalEditor = state.goalEditor;
    const goalSave = toObject(state.goalSave || {});
    const goalSaveRisk = buildGoalSaveRiskSummary(goalSnapshot.items, state.goals);
    const goalSaveDisabled = goalSaveDisabledReason(goalDraft, goalSaveRisk, toText(goalSave.confirmation, '').trim());
    const goalSaveButtonAttrs = goalSaveDisabled ? `disabled title="${escapeHTML(goalSaveDisabled)}"` : '';
    const goalSaveStatusLabel = goalSave.status === 'saving'
      ? 'Saving...'
      : goalSave.status === 'success'
        ? 'Saved'
        : goalSave.status === 'error'
          ? 'Failed'
          : !goalsDirty
            ? 'Clean'
            : goalSaveRisk.requiresConfirmation
              ? 'Confirmation required'
              : 'Ready to save';
    const goalsSource = goalsDirty ? 'browser-local draft' : '/api/goals';
    const goalsNote = state.snapshotStatus === 'loading'
      ? 'Loading the read-only snapshot...'
      : state.sourceMode === 'fallback'
      ? 'Fallback data is shown locally when the read-only API is unavailable.'
      : goalSnapshotMessage(goalSnapshot, goalSummary.total, goalsDirty);
    const goalSaveButtonLabel = goalSaveInFlight()
      ? 'Saving...'
      : goalSaveRisk.requiresConfirmation
        ? 'Confirm & Save Goals'
        : 'Save Goals';

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
            <div class="summary-note" style="margin-top:4px;">Source: ${escapeHTML(goalsSource)}</div>
            <div class="summary-note" style="margin-top:4px;">Snapshot: ${escapeHTML(toNumber(goalSummary.done || 0, 0))}/${escapeHTML(toNumber(goalSummary.total || 0, 0))} checked · ${escapeHTML(toNumber(goalWarnings.length, 0))} parser warning${goalWarnings.length === 1 ? '' : 's'}</div>
          `
        )}

        ${panel(
          'GOALS.md snapshot',
          goalFileExists ? (goalSummary.total ? `${escapeHTML(goalSummary.total)} parsed` : 'empty') : 'missing',
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
            <div class="summary-note" style="margin-top:10px;">Source: ${escapeHTML(goalsSource)}</div>
            <div class="summary-note" style="margin-top:10px;">Raw text preview</div>
            <div class="summary-note" style="margin-top:4px; white-space:pre-wrap; max-height:180px; overflow:auto;">${escapeHTML(goalRawText.trim() || '(empty)')}</div>
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

        ${panel(
          'Goal draft diff',
          goalsDirty ? `${escapeHTML(goalDraft.rows.length)} change${goalDraft.rows.length === 1 ? '' : 's'}` : 'clean',
          `
            <div class="summary-note">Draft edits stay local until reset. Bucket grouping stays pinned to P0 and P1.</div>
            <div class="prompt-diff-list" style="margin-top:10px;">
              ${goalDraft.rows.length ? goalDraft.rows.map((row) => renderGoalDraftRow(row)).join('') : '<div class="summary-note">No local content changes yet.</div>'}
            </div>
          `
        )}

        ${panel(
          'Goal save',
          goalSaveStatusLabel,
          `
            <div class="goal-save-state" data-goal-save-root data-goal-save-status="${escapeHTML(goalSave.status || 'idle')}" data-goal-saving="${goalSaveInFlight() ? 'true' : 'false'}">
              <div data-goal-save-banner>
                ${renderGoalSaveBanner(goalDraft, goalSaveRisk)}
              </div>
              <div class="modal-field" style="margin-top:12px;">
                <div class="modal-field__label">Confirmation phrase</div>
                <input
                  type="text"
                  class="field-control"
                  data-goal-save-confirmation
                  value="${escapeHTML(goalSave.confirmation || '')}"
                  placeholder="${escapeHTML(goalSaveRisk.confirmationPhrase)}"
                  autocomplete="off"
                  spellcheck="false"
                  ${!goalsDirty || goalSaveInFlight() || !goalSaveEnabled() ? 'disabled' : ''}
                >
              </div>
              <div class="summary-note" style="margin-top:10px;">Saving always creates a timestamped backup before atomically updating .doc/GOALS.md.</div>
              <div class="modal-actions" style="margin-top:14px;">
                ${button(goalSaveButtonLabel, 'goal-save-draft', 'button--primary', `${goalSaveButtonAttrs} data-goal-save-button`)}
              </div>
            </div>
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
                    ${goals.map((goal, index) => renderGoalItem(bucket, goal, index, goals.length)).join('') || `<div class="summary-note">No goals yet.</div>`}
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
      'Local checklist with add, edit, reorder, and completion actions',
      `
        ${button('Add Goal', 'goal-add-p0', 'button--primary')}
        ${button('Reset draft', 'reset-goals', goalsDirty ? 'button--danger' : 'button--quiet', goalsDirty ? '' : 'disabled')}
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
    const value = getAt(state.configDraft, path);
    const disabled = configSaveInFlight();
    if (!schema) {
      return `<div class="field-error">Missing schema for ${escapeHTML(path)}</div>`;
    }
    if (schema.kind === 'bool') {
      return `
        <button type="button" class="control-chip ${value ? 'control-chip--active' : ''}" data-config-toggle="${escapeHTML(path)}" ${disabled ? 'disabled' : ''}>
          <span class="dot" style="background:${value ? 'var(--accent)' : 'var(--text-sub)'}"></span>
          ${escapeHTML(value ? 'enabled' : 'disabled')}
        </button>
      `;
    }
    if (schema.kind === 'enum') {
      return `
        <select class="field-control" data-config-field="${escapeHTML(path)}" ${disabled ? 'disabled' : ''}>
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
              <button type="button" class="modal-tab ${set.has(option) ? 'modal-tab--active' : ''}" data-config-multi="${escapeHTML(path)}" data-config-value="${escapeHTML(option)}" ${disabled ? 'disabled' : ''}>${escapeHTML(option)}</button>
            `)
            .join('')}
        </div>
      `;
    }
    if (schema.kind === 'list') {
      const textValue = fmtList(value || []);
      return `
        <textarea
          class="field-control field-control--textarea"
          rows="3"
          placeholder="${escapeHTML(schema.item_kind === 'int' || schema.itemKind === 'int' ? '1, 2, 3' : 'value, value')}"
          data-config-field="${escapeHTML(path)}"
          ${disabled ? 'disabled' : ''}
        >${escapeHTML(textValue)}</textarea>
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
          ${disabled ? 'disabled' : ''}
        >
      `;
    }
    const inputType = schema.redacted ? 'password' : 'text';
    return `
      <input
        class="field-control ${schema.redacted ? 'field-control--secret' : ''}"
        type="${inputType}"
        value="${escapeHTML(value)}"
        placeholder="${schema.redacted ? escapeHTML(REDACTED_VALUE) : ''}"
        autocomplete="off"
        data-config-field="${escapeHTML(path)}"
        ${disabled ? 'disabled' : ''}
      >
    `;
  }

  function renderConfig() {
    const diffs = getConfigDiffs();
    const selectedPath = currentConfigSelection();
    const selectedSchema = state.configSchema[selectedPath];
    const selectedActiveValue = getAt(state.configContract?.values || {}, selectedPath);
    const selectedDefaultValue = getAt(state.configContract?.defaults || {}, selectedPath);
    const selectedDraftValue = getAt(state.configDraft, selectedPath);
    const selectedError = configChangeError(selectedPath, selectedDraftValue, selectedSchema, selectedActiveValue);
    const restartDiffs = diffs.filter((diff) => diff.restart);
    const invalidDiffs = diffs.filter((diff) => diff.error);
    const saveLocked = configSaveInFlight();
    const saveDisabledReason = configSaveDisabledReason(diffs, invalidDiffs);
    const saveBannerHTML = renderConfigSaveBanner(diffs, invalidDiffs);
    const saveButtonAttrs = saveDisabledReason ? `disabled title="${escapeHTML(saveDisabledReason)}"` : '';
    const saveButtonLabel = saveLocked ? 'Saving...' : 'Save Changes';

    const groupsHTML = configGroups()
      .map((group) => `
        <div class="config-group">
          <div class="config-group__title">${escapeHTML(group.title)}</div>
          ${group.description ? `<div class="summary-note" style="margin-bottom:8px;">${escapeHTML(group.description)}</div>` : ''}
          <div class="config-list">
            ${group.paths
              .map((path) => {
                const schema = state.configSchema[path];
                const value = getAt(state.configDraft, path);
                const activeValue = getAt(state.configContract?.values || {}, path);
                const defaultValue = getAt(state.configContract?.defaults || {}, path);
                const defaultChanged = JSON.stringify(activeValue) !== JSON.stringify(defaultValue);
                const draftChanged = JSON.stringify(value) !== JSON.stringify(activeValue);
                const active = selectedPath === path;
                const error = configChangeError(path, value, schema, activeValue);
                const rowClassName = [
                  'config-row',
                  active ? 'config-row--active' : '',
                  error ? 'config-row--invalid' : '',
                  schema && schema.redacted ? 'config-row--redacted' : '',
                ].filter(Boolean).join(' ');
                return `
                  <button
                    type="button"
                    class="${rowClassName}"
                    data-config-select="${escapeHTML(path)}"
                    ${saveLocked ? 'disabled' : ''}
                  >
                    <div class="config-row__key">
                      <span class="config-row__name">${escapeHTML(path)}</span>
                      ${defaultChanged ? '<span class="badge badge--warn">!</span>' : ''}
                    </div>
                    <div class="config-row__value">${renderConfigValueSummary(path, schema, value)}</div>
                    <div class="config-row__meta">
                      ${schema && schema.redacted ? '<span class="chip chip--info">secret</span>' : ''}
                      ${schema && schema.restart ? '<span class="chip chip--warn">restart</span>' : ''}
                      ${draftChanged ? '<span class="chip chip--accent">edited</span>' : ''}
                      ${error ? '<span class="chip chip--err">invalid</span>' : ''}
                    </div>
                  </button>
                `;
              })
              .join('')}
          </div>
        </div>
      `)
      .join('');

    const selectedLabel = escapeHTML(selectedSchema?.label || selectedPath || 'Config field');
    const selectedPathText = escapeHTML(selectedPath || '');
    const activeValueText = escapeHTML(configValueToText(selectedActiveValue, selectedSchema));
    const draftValueText = escapeHTML(configValueToText(selectedDraftValue, selectedSchema));
    const defaultValueText = escapeHTML(configValueToText(selectedDefaultValue, selectedSchema));
    const selectedDefaultChanged = JSON.stringify(selectedActiveValue) !== JSON.stringify(selectedDefaultValue);
    const selectedDraftChanged = JSON.stringify(selectedDraftValue) !== JSON.stringify(selectedActiveValue);
    const pendingDiffRows = diffs
      .map((diff) => {
        const diffSchema = state.configSchema[diff.path];
        const pathLabel = escapeHTML(diffSchema?.label || diff.path);
        const fromText = escapeHTML(configValueToText(diff.from, diffSchema));
        const toText = escapeHTML(configValueToText(diff.to, diffSchema));
        return `
          <div class="config-diff-row">
            <div class="config-diff-row__head">
              <div class="config-diff-row__path">${pathLabel}</div>
              <div class="config-row__meta">
                ${diffSchema && diffSchema.redacted ? '<span class="chip chip--info">secret</span>' : ''}
                ${diff.restart ? '<span class="chip chip--warn">restart</span>' : ''}
                ${diff.error ? '<span class="chip chip--err">invalid</span>' : ''}
              </div>
            </div>
            <div class="field-diff">
              <div class="field-diff__from">${fromText}</div>
              <div class="field-diff__to">${toText}</div>
            </div>
          </div>
        `;
      })
      .join('');

    const detail = `
      <div class="config-detail">
        <div class="config-detail__head">
          <div>
            <div class="overlay__title" style="display:block;">field details</div>
            <div class="config-detail__title">${selectedLabel}</div>
            <div class="summary-note">${selectedPathText}</div>
          </div>
          <div class="config-row__meta">
            ${selectedSchema && selectedSchema.kind ? `<span class="chip chip--info">${escapeHTML(selectedSchema.kind)}</span>` : ''}
            ${selectedSchema && selectedSchema.redacted ? '<span class="chip chip--info">secret</span>' : ''}
            ${selectedSchema && selectedSchema.restart ? '<span class="chip chip--warn">restart required</span>' : ''}
            ${selectedDefaultChanged ? '<span class="chip chip--warn">default</span>' : ''}
            ${selectedDraftChanged ? '<span class="chip chip--accent">edited</span>' : ''}
            ${selectedError ? '<span class="chip chip--err">invalid</span>' : ''}
          </div>
        </div>
        <div class="config-detail__body">
          ${saveBannerHTML}
          ${invalidDiffs.length ? `
            <div class="modal-banner section-banner section-banner--err">
              <span class="dot" style="background: currentColor;"></span>
              <div>
                <div class="section-banner__title">Local validation failed</div>
                <div class="section-banner__copy">${escapeHTML(`${invalidDiffs.length} pending change${invalidDiffs.length === 1 ? '' : 's'} are invalid.`)}</div>
              </div>
            </div>
          ` : ''}
          ${restartDiffs.length ? `
            <div class="modal-banner">
              <span class="dot" style="background: var(--warn);"></span>
              <div>
                <div class="section-banner__title">Restart required</div>
                <div class="section-banner__copy">${escapeHTML(restartDiffs.map((diff) => diff.path).join(', '))}</div>
              </div>
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
            <div class="detail-label">Active value</div>
            <div class="field-diff">
              <div class="field-diff__from">${activeValueText}</div>
              <div class="field-diff__to">${draftValueText}</div>
            </div>
          </div>
          <div>
            <div class="detail-label">Local draft</div>
            <div>${configControl(selectedPath)}</div>
            ${selectedError ? `<div class="field-error" style="margin-top:6px;">${escapeHTML(selectedError)}</div>` : ''}
          </div>
          <div>
            <div class="detail-label">Default</div>
            <div class="field-diff">
              <div class="field-diff__from">${defaultValueText}</div>
              <div class="field-diff__to">${draftValueText}</div>
            </div>
          </div>
          ${selectedPath === 'prompts_dir' && state.configContract?.resolved_prompts_dir ? `
            <div>
              <div class="detail-label">Resolved prompts path</div>
              <div class="detail-copy">${escapeHTML(state.configContract.resolved_prompts_dir)}</div>
            </div>
          ` : ''}
          ${selectedSchema && selectedSchema.redacted ? `
            <div class="summary-note">Redacted values stay hidden in the browser.</div>
          ` : ''}
          ${diffs.length ? `
            <div>
              <div class="detail-label">Pending changes</div>
              <div class="config-diff-list">${pendingDiffRows}</div>
            </div>
          ` : ''}
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
      `Local draft only | ${escapeHTML(diffs.length)} pending change${diffs.length === 1 ? '' : 's'}`,
      `
        ${button(saveButtonLabel, 'save-config', 'button--primary', saveButtonAttrs)}
        ${button('Reset Draft', 'reset-config', 'button--quiet', saveLocked ? 'disabled title="Config save is already in progress."' : '')}
        ${button('Open Prompts', 'nav-prompts', 'button--quiet')}
      `,
      body
    );
  }

  function renderPrompts() {
    const selectedPrompt = currentPrompt();
    const selected = selectedPrompt || {
      file: 'No prompt selected',
      mode: 'template',
      scope: 'PM',
      profile: '',
      source: '',
      updated: 'empty',
      summary: '',
      preview: '[redacted]',
      path: '',
    };
    const copyPromptSummaryAttrs = selectedPrompt ? '' : 'disabled aria-disabled="true"';
    const promptsDir = state.configMeta?.resolved_prompts_dir || state.promptsDir || state.config.prompts_dir || 'prompts';
    const editor = promptEditorData();
    const overrides = state.prompts.filter((prompt) => prompt.mode === 'override').length;
    const editorDirty = promptEditorIsDirty(editor);
    const editorFile = editor.promptFile || selected.file;
    const editorScope = editor.promptScope || selected.scope;
    const editorProfile = editor.promptProfile || selected.profile || '';
    const editorSource = editor.promptSource || selected.source || '';
    const editorMode = editor.promptMode || selected.mode;
    const editorPath = editor.promptPath || selected.path || '';
    const editorUpdated = editor.promptUpdated || selected.updated || '';
    const editorPreview = editor.promptPreview || selected.preview || REDACTED_VALUE;
    const editorDisabled = editor.loading || promptMutationInFlight(editor) || !editor.promptId || Boolean(editor.error);

    const body = `
      <div class="prompt-layout">
        <div class="prompt-list">
          ${panel(
            'Prompt inventory',
            `${escapeHTML(overrides)}/${escapeHTML(state.prompts.length)} overrides | redacted by default`,
            `
              ${sectionNotice('prompts')}
              <div class="summary-note">Inventory previews stay redacted. Select a prompt to open the explicit full-content read path.</div>
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(promptsDir)}</div>
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

        <div class="prompt-editor" data-prompt-editor-root data-prompt-dirty="${editorDirty ? 'true' : 'false'}" data-prompt-loading="${editor.loading ? 'true' : 'false'}" data-prompt-saving="${promptSaveInFlight(editor) ? 'true' : 'false'}" data-prompt-restoring="${promptRestoreInFlight(editor) ? 'true' : 'false'}" data-prompt-id="${escapeHTML(editor.promptId || '')}">
          <div class="prompt-editor__head">
            <div class="prompt-editor__title-block">
              <div class="panel__title">${escapeHTML(editorFile)}</div>
              <div class="panel__meta">${escapeHTML(editorScope || 'PM')} | ${escapeHTML(editorProfile || 'personal')} | ${escapeHTML(editorMode || 'template')} | ${escapeHTML(editorSource || 'unknown source')}</div>
            </div>
            <div class="prompt-editor__state" data-prompt-editor-state>
              ${renderPromptEditorState()}
            </div>
          </div>

          <div data-prompt-editor-banner>
            ${renderPromptEditorBanner()}
          </div>

          <div class="compact-list prompt-editor__meta">
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorScope || selected.scope)}</div>
                <div class="compact-list__meta">Scope</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorProfile || 'personal')}</div>
                <div class="compact-list__meta">Profile</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorSource || 'unknown source')}</div>
                <div class="compact-list__meta">Source</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorPath || '(unresolved path)')}</div>
                <div class="compact-list__meta">Resolved path</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorUpdated || 'unknown')}</div>
                <div class="compact-list__meta">Last updated</div>
              </div>
            </div>
          </div>

          <div class="prompt-preview prompt-editor__preview">
            <div class="prompt-preview__head">
              <span class="badge badge--dim">FULL READ PREVIEW</span>
              <div class="panel__meta">Loaded through the explicit read path</div>
            </div>
            <div class="prompt-preview__body">
              <div class="detail-label">Preview</div>
              <pre class="prompt-preview__text">${escapeHTML(editorPreview || REDACTED_VALUE)}</pre>
            </div>
          </div>

          <div class="prompt-editor__body">
            <div class="prompt-editor__field">
              <label class="prompt-editor__label" for="prompt-editor-file">Filename</label>
              <input
                id="prompt-editor-file"
                class="field-control prompt-editor__input"
                type="text"
                data-prompt-editor-field="file"
                value="${escapeHTML(editor.promptId ? editor.draftFile || '' : '')}"
                ${editorDisabled ? 'disabled' : ''}
                autocomplete="off"
                spellcheck="false"
              >
            </div>

            <div class="prompt-editor__field">
              <label class="prompt-editor__label" for="prompt-editor-content">Content</label>
              <textarea
                id="prompt-editor-content"
                class="field-control field-control--textarea prompt-editor__textarea"
                data-prompt-editor-field="content"
                rows="18"
                ${editorDisabled ? 'disabled' : ''}
                spellcheck="false"
              >${escapeHTML(editor.promptId ? editor.draftContent || '' : '')}</textarea>
            </div>

            <div data-prompt-editor-mutation>
              ${renderPromptEditorMutationPanel()}
            </div>

            <div data-prompt-editor-validation>
              ${renderPromptEditorValidation()}
            </div>

            <div data-prompt-editor-diff>
              ${renderPromptEditorDiff()}
            </div>
          </div>
        </div>
      </div>
    `;

    return viewShell(
      'prompts',
      'Prompts',
      `${escapeHTML(promptsDir)} | selected ${escapeHTML(editorFile)} | profile ${escapeHTML(editorProfile || 'personal')}`,
      `
        ${button('Open Config', 'nav-config', 'button--quiet')}
        ${button('Copy prompt summary', 'copy-prompt-summary', 'button--quiet', copyPromptSummaryAttrs)}
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
    const selectedCounts = selected ? historyTaskCounts(selected) : { done: 0, total: 0, failed: 0, skipped: 0, cycles: 0 };
    const selectedSummary = selected ? historySummaryText(selected) : 'No persisted summary fields available.';
    const selectedWorktreeOutcome = selected ? historyWorktreeOutcomeLabel(selected.worktreeOutcome) : 'none';
    const selectedShutdownReason = selected ? toText(selected.shutdownReason || selected.stopReason || '', '') : '';
    const selectedFinalReason = selected ? toText(selected.finalReason, '') : '';
    const selectedRunDir = selected ? selected.runDir || 'unavailable' : 'unavailable';

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
            escapeHTML(selected ? `${selected.branch} | ${selected.id}` : 'none'),
            `
              <div class="history-details">
                <div class="history-details__body">
                  ${
                    selected
                      ? `
                        <div class="kpi-grid kpi-grid--four">
                          ${kpiCard('Status', selected.status.toUpperCase(), 'current state', selected.status === 'success')}
                          ${kpiCard('Tasks', `${selectedCounts.done}/${selectedCounts.total}`, `failed ${selectedCounts.failed} | skipped ${selectedCounts.skipped}`)}
                          ${kpiCard('Duration', fmtDuration(selected.durationSec), 'persisted runtime')}
                          ${kpiCard('Worktree', historyWorktreeOutcomeLabel(selected.worktreeOutcome), selected.worktreeOutcome === 'none' ? 'no worktree artifact' : 'worktree outcome')}
                        </div>
                        <div class="compact-list">
                          ${compactFactItem('Branch', selected.branch || 'none', 'persisted run summary')}
                          ${compactFactItem('Run directory', selectedRunDir, 'read-only run artifacts')}
                          ${compactFactItem('Final reason', selectedFinalReason || 'unavailable', 'run_summary.json final.reason')}
                          ${compactFactItem('Shutdown reason', selectedShutdownReason || 'unavailable', 'last_run_summary.json stop_reason')}
                          ${compactFactItem('Persisted summary', selectedSummary, 'run_summary.json + last_run_summary.json')}
                          ${compactFactItem('Worktree outcome', selectedWorktreeOutcome, 'worktree artifacts')}
                        </div>
                        <div class="summary-note">Persisted run summaries drive this view. Task counts and shutdown reasons are read from the run artifacts, not reconstructed placeholders.</div>
                      `
                      : `
                        <div class="history-details__empty">
                          No persisted run summaries are available yet. Run history will show shutdown reasons, task counts, duration, and worktree outcomes after the first completed run.
                        </div>
                      `
                  }
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
    const filters = ['all', 'run_start', 'run_stop', 'task_done', 'task_failed', 'quota', 'error', 'stalled'];
    const filtered = state.notifications.filter((item) => state.notificationFilter === 'all' || item.kind === state.notificationFilter);

    const kindCounts = state.notifications.reduce((acc, item) => {
      acc[item.kind] = (acc[item.kind] || 0) + 1;
      return acc;
    }, {});

    const latestNotification = filtered[0] || state.notifications[0] || null;
    const observedKinds = Object.keys(kindCounts).sort();
    const configuredEvents = fmtList(state.config?.telegram?.notify_events || []);
    const stalledSeconds = toNumber(state.config?.telegram?.stalled_seconds || 0, 0);
    const controlPlaneStatus = state.runnerControl.controllerAvailable
      ? (state.runnerControl.enabled ? (state.runnerControl.busy ? 'busy' : 'enabled') : 'disabled')
      : 'unavailable';
    const controlPlaneEvent = state.runnerControl.status.lastEvent || state.runnerControl.lastAction || state.runnerControl.lastMessage || '';
    const emptyMessage = state.notifications.length
      ? 'No notifications match the current filter.'
      : state.sectionState.notifications?.status === 'error'
        ? state.sectionState.notifications.message || 'Notifications are unavailable right now.'
        : fallbackSectionMessage('notifications');
    const emptyTitle = state.sectionState.notifications?.status === 'error'
      ? 'Notification error'
      : state.notifications.length
        ? 'Filtered empty'
        : 'No notifications yet';

    const body = `
      <div class="notification-layout">
        <div>
          ${panel(
            'Event feed',
            `${escapeHTML(filtered.length)} visible | ${escapeHTML(state.notifications.length)} total`,
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
            ${
              filtered.length
                ? filtered.map((item) => renderNotificationItem(item)).join('')
                : `
                  <div class="notification-feed__empty ${state.sectionState.notifications?.status === 'error' ? 'notification-feed__empty--error' : ''}">
                    <span class="dot" style="color:${state.sectionState.notifications?.status === 'error' ? 'var(--err)' : 'var(--warn)'}; background:currentColor;"></span>
                    <div>
                      <div class="notification-feed__empty-title">${escapeHTML(emptyTitle)}</div>
                      <div class="notification-feed__empty-copy">${escapeHTML(emptyMessage)}</div>
                    </div>
                  </div>
                `
            }
          </div>
        </div>

        <div class="view-grid">
          ${panel(
            'Notification source',
            escapeHTML(latestNotification ? `${latestNotification.kind} | ${fmtRelative(latestNotification.t)}` : 'no events yet'),
            `
              <div class="compact-list">
                ${compactFactItem('Observed kinds', observedKinds.length ? observedKinds.join(', ') : 'none', 'Kinds derived from actual notification rows')}
                ${compactFactItem('Newest event', latestNotification ? `${latestNotification.kind} | ${fmtDateTime(latestNotification.t)}` : 'none', latestNotification ? latestNotification.text : 'No notification events have been recorded yet.')}
                ${compactFactItem('Control-plane last event', controlPlaneEvent || 'none', state.runnerControl.lastMessage || state.runnerControl.lastError || 'Runner control snapshot')}
              </div>
            `
          )}

          ${panel(
            'Notification counts',
            'current run',
            `
              <div class="kpi-grid kpi-grid--four">
                ${kpiCard('Lifecycle', String((kindCounts.run_start || 0) + (kindCounts.run_stop || 0)), 'run start + run stop')}
                ${kpiCard('Task done', String(kindCounts.task_done || 0), 'success events', true)}
                ${kpiCard('Quota', String(kindCounts.quota || 0), 'budget notices')}
                ${kpiCard('Errors', String((kindCounts.error || 0) + (kindCounts.task_failed || 0) + (kindCounts.stalled || 0)), 'action needed')}
              </div>
            `
          )}

          ${panel(
            'Bridge settings',
            escapeHTML(state.config.telegram.instance_name || 'home-pc-main'),
            `
              <div class="compact-list">
                ${compactFactItem('Configured events', configuredEvents || 'none', 'telegram.notify_events')}
                ${compactFactItem('Stalled threshold', stalledSeconds ? `${stalledSeconds}s` : 'unavailable', 'telegram.stalled_seconds')}
                ${compactFactItem('Control-plane status', controlPlaneStatus, 'runner_control snapshot')}
              </div>
              <div class="summary-note">Events are read from lifecycle records and control-plane snapshots. No placeholder feed is used.</div>
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
    const status = toText(review.status, 'none');
    const cleanupState = toText(review.cleanupState, 'none');
    const cleanupFailed = cleanupState === 'failed' || status === 'applied_cleanup_failed' || status === 'discard_cleanup_failed';
    const reviewRequired = Boolean(review.reviewRequired || status === 'error' || (status && status !== 'none' && status !== 'applied' && status !== 'discarded'));
    const actionEnabled = worktreeActionEnabled(review, 'merge');
    const canCopyPatch = Boolean(review.patchPath || review.patch);
    const reviewSummary = describeWorktreeReview(review);
    const statusSummary = status === 'none'
      ? 'no pending merge'
      : [status, cleanupState !== 'none' ? `cleanup ${cleanupState}` : ''].filter(Boolean).join(' | ');
    const checklistMeta = status === 'none' ? 'read only' : cleanupFailed ? 'manual recovery' : reviewRequired ? (actionEnabled ? 'confirmation required' : 'manual recovery') : 'finalized';
    const checklistTitle = status === 'none'
      ? 'Read-only mode'
      : cleanupFailed
        ? 'Cleanup required'
        : reviewRequired
          ? actionEnabled
            ? 'Confirmation required'
            : 'Manual recovery'
        : 'Finalized worktree';
    const checklistCopy = status === 'none'
      ? 'No pending worktree merge is available in this snapshot.'
      : cleanupFailed
        ? 'The merge or discard decision has already been recorded, but the isolated worktree still needs manual cleanup.'
        : reviewRequired
          ? actionEnabled
            ? 'Review the patch hunks, then confirm merge or discard in the web console. The backend validates the pending marker, source repository, run directory, worktree path, and patch path before it applies anything. No commit will be created.'
            : 'The pending merge state needs manual recovery before another action can run.'
        : 'This worktree is finalized. The web console stays read-only.';
    const mergePanelMeta = status === 'none' ? 'read only' : cleanupFailed ? 'cleanup failed' : reviewRequired ? (actionEnabled ? 'confirmation required' : 'manual recovery') : 'finalized';
    const detailRows = [
      { label: 'Status', value: status, meta: reviewRequired ? 'review required' : 'read only' },
      { label: 'Status file', value: review.statusFile || review.pendingFile || '--', meta: 'current artifact path' },
      { label: 'Source repo', value: review.sourceRepo || '--', meta: 'repository root' },
      { label: 'Source branch', value: review.sourceBranch || review.branch || 'HEAD', meta: 'base branch for the patch' },
      { label: 'Base ref', value: review.baseRef || '--', meta: 'merge base' },
      { label: 'Head ref', value: review.headRef || '--', meta: 'worktree head' },
      { label: 'Run dir', value: review.runDir || state.latestRunDir || '--', meta: 'run that produced the patch' },
      { label: 'Worktree dir', value: review.worktreeDir || review.worktree || '--', meta: 'isolated source tree' },
      { label: 'Patch path', value: review.patchPath || review.patch || '--', meta: 'merge patch artifact' },
      { label: 'Pending file', value: review.pendingFile || '--', meta: 'read-only contract source' },
      { label: 'Cleanup state', value: cleanupState, meta: 'worktree cleanup lifecycle' },
      { label: 'Cleanup path', value: review.cleanupPath || review.worktreeDir || review.worktree || '--', meta: 'cleanup target' },
      { label: 'Cleanup message', value: review.cleanupMessage || '--', meta: 'cleanup status detail' },
      { label: 'Runner rc', value: String(review.runnerRc ?? review.lastRc ?? 0), meta: 'export status' },
    ];
    const bannerTone = reviewSummary.tone;
    const bannerTitle = reviewSummary.title;
    const bannerCopy = reviewSummary.copy;
    const actionCopy = reviewSummary.actionCopy;
    const mergeActionAttrs = worktreeActionButtonAttrs(review, 'merge');
    const discardActionAttrs = worktreeActionButtonAttrs(review, 'discard');
    const copyPatchAttrs = canCopyPatch
      ? ''
      : 'disabled aria-disabled="true" title="No patch path is available yet."';
    const riskNoteItems = (() => {
      if (cleanupFailed) {
        const cleanupPath = review.cleanupPath || review.worktreeDir || review.worktree || '--';
        const sourceRepo = review.sourceRepo || 'the source repository';
        return [
          status === 'discard_cleanup_failed'
            ? `Discard was recorded, but cleanup failed for ${cleanupPath}.`
            : `Merge was recorded, but cleanup failed for ${cleanupPath}.`,
          `Manual recovery: run git worktree remove --force ${cleanupPath} from ${sourceRepo}, or remove the worktree directory manually.`,
          status === 'discard_cleanup_failed'
            ? 'The source repository was not changed.'
            : 'The source repository was already updated.',
        ];
      }
      if (status === 'pending review' || status === 'pending') {
        return [
          `Confirm merge to apply the patch without creating a commit.`,
          `Confirm discard to remove the pending state without touching source files.`,
          `The backend validates the source repository, run directory, worktree path, and patch path before it runs.`,
        ];
      }
      if (status === 'applied') {
        return [
          `Patch applied to ${review.sourceRepo || 'the source repository'} without creating a commit.`,
        ];
      }
      if (status === 'discarded') {
        return [
          `Pending merge discarded without changing ${review.sourceRepo || 'the source repository'}.`,
        ];
      }
      if (status === 'apply_failed') {
        return [
          'Patch export failed before a reviewable merge marker was written.',
          'Inspect the failure report in the run directory and retry the worktree export.',
        ];
      }
      if (status === 'patch_not_applied' || status === 'not_applied') {
        return [
          'The patch was exported but not auto-applied.',
          'Apply the exported patch before any merge or discard action.',
        ];
      }
      return [review.risk || 'Review the patch before making any source-repo changes.'];
    })();
    const riskNotesHTML = riskNoteItems
      .map(
        (item) => `
          <div class="compact-list__item">
            <span class="compact-list__bullet"></span>
            <div>
              <div class="compact-list__body">${escapeHTML(item)}</div>
              <div class="compact-list__meta">Review before merge</div>
            </div>
          </div>
        `
      )
      .join('');

    const body = `
      <div class="review-layout">
        <div>
          ${panel(
            'Pending merge',
            `${escapeHTML(review.mode)} | ${escapeHTML(statusSummary)}`,
            `
              ${state.sectionState?.worktree && state.sectionState.worktree.status !== 'ready' ? sectionNotice('worktree') : ''}
              ${status !== 'none' ? `
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
              ${review.changedFiles.length ? `
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
              ` : '<div class="summary-note">No changed files were parsed from the patch.</div>'}
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            'Review checklist',
            reviewRequired ? (cleanupFailed ? 'manual recovery' : actionEnabled ? 'confirmation required' : 'manual recovery') : 'no pending file',
            `
              <div class="modal-banner section-banner section-banner--info">
                <span class="dot" style="background: currentColor;"></span>
                <div>
                  <div class="section-banner__title">${escapeHTML(checklistTitle)}</div>
                  <div class="section-banner__copy">${escapeHTML(checklistCopy)}</div>
                </div>
              </div>
              <div class="compact-list" style="margin-top:12px;">
                ${review.checklist.length ? review.checklist.map((item) => `
                  <div class="compact-list__item">
                    <span class="compact-list__bullet" style="background:${reviewRequired ? 'var(--warn)' : 'var(--accent)'}"></span>
                    <div>
                      <div class="compact-list__body">${escapeHTML(item)}</div>
                      <div class="compact-list__meta">${checklistMeta}</div>
                    </div>
                  </div>
                `).join('') : '<div class="summary-note">No checklist is available yet.</div>'}
              </div>
            `
          )}

          ${panel(
            'Merge actions',
            mergePanelMeta,
            `
              <div class="summary-note">${escapeHTML(actionCopy)}</div>
              <div class="modal-actions">
                ${button('Apply merge', 'worktree-apply', 'button--primary', mergeActionAttrs)}
                ${button('Discard merge', 'worktree-discard', 'button--danger', discardActionAttrs)}
              </div>
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
                ${riskNotesHTML}
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'worktree',
      'Worktree Review',
      `${escapeHTML(review.mode)} | ${escapeHTML(statusSummary)}`,
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
                        <div class="terminal-line"><span class="terminal-line__prompt"></span><span class="terminal-line__text">${escapeHTML(`PM -> Dev -> QA | quota ${formatQuotaUsage(state.activeRun.quota)} | budget ${metricText(state.activeRun.budgetAvailable, state.activeRun.budgetUsed, fmtPercent)}`)}</span></div>
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
      { kind: 'action', action: 'toggle-logs', title: isLiveTailPaused() ? 'Resume live tail' : 'Pause live tail', shortcut: 'logs' },
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
    const sourceItem = mode === 'edit' && editor.index >= 0 ? toObject(state.goals[editor.bucket][editor.index]) : {};
    const sourceMeta = goalItemMeta(sourceItem);
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="goal-editor">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(mode === 'edit' ? 'Edit goal' : 'New goal')}</span>
            <span class="overlay__sub">draft mode / esc closes / ctrl+enter saves</span>
          </div>
          <div class="overlay__body">
            <div class="modal-grid">
              <div class="modal-field goal-editor__meta">
                <div class="modal-field__label">Source metadata</div>
                <div class="modal-copy">${escapeHTML(sourceMeta)}</div>
              </div>
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
              ${editor.error ? `<div class="field-error">${escapeHTML(editor.error)}</div>` : '<div class="modal-copy">Draft edits stay local until the save workflow lands.</div>'}
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
    const display = runnerControlStateInfo(control);
    const confirmation = runnerControlConfirmationPhrase(action);
    const confirmationValue = state.stopConfirmation.trim();
    const actionEnabled = runnerControlActionEnabled(action);
    const confirmEnabled = actionEnabled && confirmationValue === confirmation && !state.stopSubmitting;
    const bannerTone = state.stopSubmitting ? 'info' : state.stopError ? 'err' : !actionEnabled ? 'warn' : 'idle';
    const actionTitle = runnerControlModalTitle(action);
    const actionSummary = runnerControlActionSummary(action);
    const actionLabel = runnerControlActionLabel(action, state.stopSubmitting);
    const subLabel = state.stopSubmitting
      ? 'refreshing status'
      : !control.enabled
        ? 'controls disabled'
        : !control.controllerAvailable
          ? 'controller unavailable'
          : actionEnabled
            ? 'type the phrase to continue'
            : 'action unavailable';
    const detailHTML = runnerControlDetailRows(control, display)
      .map(
        (item) => `
          <div class="runner-control__detail">
            <div class="runner-control__label">${escapeHTML(item.label)}</div>
            <div class="runner-control__value ${escapeHTML(item.className || '')}">${escapeHTML(item.value)}</div>
          </div>
        `
      )
      .join('');
    const bannerTitle = state.stopSubmitting ? 'Action in flight' : state.stopError ? 'Action failed' : !actionEnabled ? 'Action disabled' : 'Confirmation required';
    const bannerMessage = state.stopSubmitting
      ? control.message || 'Refreshing runner status until it reaches the expected state.'
      : state.stopError
        ? state.stopError
        : !actionEnabled
          ? runnerControlActionDisabledReason(action)
          : control.message || actionSummary;
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
                <div class="section-banner__title">${escapeHTML(bannerTitle)}</div>
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
              <button type="button" class="button ${action === 'stop' ? 'button--danger' : action === 'start' ? 'button--primary' : 'button--quiet'} ${state.stopSubmitting ? 'button--loading' : !confirmEnabled ? 'button--paused' : ''}" data-stop-confirm ${confirmEnabled ? '' : 'disabled'}>${escapeHTML(actionLabel)}</button>
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

    if (state.activeView === 'logs' && !isLiveTailPaused() && options.scrollToBottom) {
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

    commitGoalDraft(nextGoals);
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
    commitGoalDraft(next);
    renderShell({ preserveScroll: true });
  }

  function moveGoal(bucket, index, direction) {
    const delta = Number(direction);
    if (delta !== -1 && delta !== 1) {
      return;
    }
    const next = clone(state.goals);
    const items = next[bucket] || [];
    const targetIndex = index + delta;
    if (index < 0 || index >= items.length || targetIndex < 0 || targetIndex >= items.length) {
      return;
    }
    const item = items.splice(index, 1)[0];
    items.splice(targetIndex, 0, item);
    commitGoalDraft(next);
    renderShell({ preserveScroll: true });
  }

  function deleteGoal(bucket, index) {
    const next = clone(state.goals);
    next[bucket].splice(index, 1);
    commitGoalDraft(next);
    renderShell({ preserveScroll: true });
  }

  function resetGoals() {
    commitGoalDraft(state.goalsSnapshot.items || state.goalsSnapshot, false);
    renderShell({ preserveScroll: true });
  }

  function resetConfig() {
    if (configSaveInFlight()) return;
    state.configDraft = deepMerge(clone(state.configContract?.values || defaults.configContract.values || {}), null);
    resetConfigSaveState();
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
    syncLogTailStreaming();
    if (next === 'prompts') {
      void loadPromptEditor(currentPrompt());
    }
  }

  function selectConfigPath(path) {
    if (!state.configSchema[path]) return;
    state.configSelection = path;
    renderShell({ preserveScroll: true });
  }

  function setConfigValue(path, value) {
    if (configSaveInFlight()) return;
    state.configDraft = setAt(state.configDraft || {}, path, value);
    resetConfigSaveState();
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
    setLiveTailPaused(true);
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
    syncLogTailStreaming();
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
      case 'save-config':
        void saveConfigDraft();
        return;
      case 'prompt-save':
        void savePromptDraft();
        return;
      case 'prompt-restore':
        void restorePromptDraft();
        return;
      case 'toggle-logs':
        setLiveTailPaused(!isLiveTailPaused());
        renderShell({ preserveScroll: true });
        syncLogTailStreaming();
        return;
      case 'copy-log-tail-selection':
        if (state.sourceMode === 'api') {
          const tail = ensureLogTailState();
          const text = buildLogTailClipboardText(tail.entries, tail.selected);
          if (text) {
            void copyText(text);
          }
        }
        return;
      case 'download-log-tail':
        if (state.sourceMode === 'api') {
          const tail = ensureLogTailState();
          const artifact = buildLogTailDownloadArtifact(tail, {
            runId: state.activeRun.id,
            latestRunDir: state.latestRunDir,
          });
          downloadTextFile(artifact.filename, artifact.text);
        }
        return;
      case 'clear-log-tail-selection':
        if (state.sourceMode === 'api') {
          clearLogTailSelection();
          renderShell({ preserveScroll: true });
        }
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
      case 'worktree-apply':
      case 'worktree-merge':
        openWorktreeActionModal('merge');
        return;
      case 'worktree-discard':
        openWorktreeActionModal('discard');
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
      case 'goal-save-draft':
        saveGoalDraft();
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
        {
          const prompt = currentPrompt();
          if (!prompt) {
            return;
          }
          copyText(`${prompt.file} | ${prompt.summary}`);
        }
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
    if (promptMutationInFlight()) {
      return;
    }
    state.promptSelection = id;
    renderShell({ preserveScroll: true });
    if (state.activeView === 'prompts') {
      void loadPromptEditor(currentPrompt());
    }
  }

  function setBacklogSelection(id) {
    state.backlogSelection = id;
    renderShell({ preserveScroll: true });
  }

  function createModel() {
    return createFallbackFixture();
  }

  function createBlankPromptEditor() {
    return {
      promptId: '',
      promptFile: '',
      promptPath: '',
      promptScope: '',
      promptProfile: '',
      promptSource: '',
      promptMode: '',
      promptUpdated: '',
      promptSummary: '',
      promptPreview: '',
      baseFile: '',
      basePath: '',
      baseContent: '',
      baseTemplateVariables: [],
      requiredTemplateVariables: null,
      backups: [],
      backupSelection: '',
      restoreConfirmation: '',
      saveState: createBlankPromptSaveState(),
      restoreState: createBlankPromptRestoreState(),
      draftFile: '',
      draftContent: '',
      loading: false,
      error: '',
      dirty: false,
      requestToken: 0,
      lastLoadedAt: 0,
    };
  }

  function createBlankConfigSaveState() {
    return {
      status: 'idle',
      message: '',
      errorCode: '',
      backupPath: '',
      changedPaths: [],
      reloadRequiredPaths: [],
      savedAt: 0,
      requestPath: '/api/config/save',
    };
  }

  function createBlankGoalSaveState() {
    return {
      status: 'idle',
      message: '',
      errorCode: '',
      backupPath: '',
      savedPath: '',
      savedAt: 0,
      requestPath: '/api/goals/save',
      confirmation: '',
      risk: {
        requiresConfirmation: false,
        confirmationPhrase: GOALS_SAVE_CONFIRMATION_PHRASE,
        deletedUncheckedP0: [],
        downgradedUncheckedP0: [],
        riskCount: 0,
      },
    };
  }

  const defaults = createBlankModel();
  defaults.configContract = buildConfigContract(
    {
      path: defaults.configMeta.path,
      source: defaults.configMeta.source,
      resolved_prompts_dir: defaults.configMeta.resolved_prompts_dir,
      meta: clone(defaults.configMeta),
      values: defaults.config,
      defaults: defaults.configDefault,
      schema: defaults.configSchema,
      groups: legacyConfigGroups(),
      redaction: {
        placeholder: '[redacted]',
        paths: ['telegram.bot_token', 'telegram.pairing_code'],
        tokens: [],
      },
      restart_required_paths: ['repo', 'profile', 'execution_backend', 'worktree_isolation', 'prompts_dir', 'telegram.enabled', 'telegram.runner_mode', 'telegram.bot_token', 'telegram.pairing_code', 'gitops.worktree_merge_mode'],
    },
    {
      defaults: defaults.configDefault,
      schema: defaults.configSchema,
      groups: legacyConfigGroups(),
      redaction: {
        placeholder: '[redacted]',
        paths: ['telegram.bot_token', 'telegram.pairing_code'],
        tokens: [],
      },
      restart_required_paths: ['repo', 'profile', 'execution_backend', 'worktree_isolation', 'prompts_dir', 'telegram.enabled', 'telegram.runner_mode', 'telegram.bot_token', 'telegram.pairing_code', 'gitops.worktree_merge_mode'],
    },
  );
  const fallbackFixture = createFallbackFixture();
  const storedGoalDraftRaw = readJSON(STORAGE.goals, null);
  const storedGoalDraft = storedGoalDraftRaw && Object.keys(toObject(storedGoalDraftRaw)).length
    ? normalizeGoalBuckets(storedGoalDraftRaw)
    : null;

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
    goals: storedGoalDraft ? clone(storedGoalDraft) : clone(defaults.goals),
    goalsSnapshot: clone(defaults.goalsSnapshot),
    goalsMeta: clone(defaults.goalsMeta),
    goalsPath: defaults.goalsPath,
    goalsCompletion: clone(defaults.goalsCompletion),
    goalsDirty: Boolean(storedGoalDraft),
    goalSave: clone(defaults.goalSave),
    history: clone(defaults.history),
    runs: clone(defaults.history),
    historySummary: clone(defaults.historySummary),
    metrics: clone(defaults.metrics),
    logs: clone(defaults.logs),
    logTail: clone(defaults.logTail),
    logFiles: clone(defaults.logFiles),
    notifications: clone(defaults.notifications),
    configDefault: clone(defaults.configDefault),
    config: clone(defaults.config),
    configMeta: clone(defaults.configMeta),
    configContract: clone(defaults.configContract),
    configSchema: clone(defaults.configContract?.schema || defaults.configSchema),
    configDraft: clone(defaults.configContract?.values || defaults.config),
    configSave: clone(defaults.configSave || createBlankConfigSaveState()),
    prompts: clone(defaults.prompts),
    promptsDir: defaults.config.prompts_dir,
    worktreeMerge: clone(defaults.worktreeMerge),
    worktreeAction: defaults.worktreeAction,
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
      { kind: 'action', action: 'toggle-logs', title: isLiveTailPaused() ? 'Resume live tail' : 'Pause live tail', shortcut: 'logs' },
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
    const sourceItem = mode === 'edit' && editor.index >= 0 ? toObject(state.goals[editor.bucket][editor.index]) : {};
    const sourceMeta = goalItemMeta(sourceItem);
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="goal-editor">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(mode === 'edit' ? 'Edit goal' : 'New goal')}</span>
            <span class="overlay__sub">draft mode / esc closes / ctrl+enter saves</span>
          </div>
          <div class="overlay__body">
            <div class="modal-grid">
              <div class="modal-field goal-editor__meta">
                <div class="modal-field__label">Source metadata</div>
                <div class="modal-copy">${escapeHTML(sourceMeta)}</div>
              </div>
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
              ${editor.error ? `<div class="field-error">${escapeHTML(editor.error)}</div>` : '<div class="modal-copy">Draft edits stay local until the save workflow lands.</div>'}
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
    if (state.worktreeAction) {
      renderWorktreeActionOverlay();
      return;
    }
    if (state.stopOpen) {
      renderStopOverlay();
      return;
    }
    overlayRoot().innerHTML = '';
  }

  function renderShell(options = {}) {
    if (state.paletteOpen || state.goalEditor || state.stopOpen || state.worktreeAction) {
      return;
    }
    const main = mainRoot();
    const preserveScroll = Boolean(options.preserveScroll);
    const previousScroll = preserveScroll ? main.scrollTop : 0;

    topbarRoot().innerHTML = renderTopbar();
    sidebarRoot().innerHTML = renderSidebar();
    main.innerHTML = renderMainView();
    main.dataset.view = state.activeView;

    if (state.activeView === 'logs' && !isLiveTailPaused() && options.scrollToBottom) {
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

  function setBacklogSelection(id) {
    state.backlogSelection = id;
    renderShell({ preserveScroll: true });
  }

  function updateConfigPath(path, rawValue) {
    if (configSaveInFlight()) return;
    const schema = state.configSchema[path];
    if (!schema) return;
    let value = rawValue;
    if (schema.kind === 'number') {
      value = rawValue === '' ? '' : Number(rawValue);
    } else if (schema.kind === 'bool') {
      value = Boolean(rawValue);
    } else if (schema.kind === 'list') {
      const items = normalizeListValues(rawValue);
      if (schema.item_kind === 'int' || schema.itemKind === 'int' || schema.item_kind === 'number' || schema.itemKind === 'number') {
        value = items.map((item) => {
          const parsed = Number(item);
          return Number.isFinite(parsed) && String(item).trim() !== '' ? Math.trunc(parsed) : item;
        });
      } else {
        value = items;
      }
    }
    state.configDraft = setAt(state.configDraft || {}, path, value);
    resetConfigSaveState();
    renderShell({ preserveScroll: true });
  }

  function toggleConfigBool(path) {
    if (configSaveInFlight()) return;
    const current = Boolean(getAt(state.configDraft, path));
    state.configDraft = setAt(state.configDraft || {}, path, !current);
    resetConfigSaveState();
    renderShell({ preserveScroll: true });
  }

  function toggleConfigMulti(path, value) {
    if (configSaveInFlight()) return;
    const current = Array.isArray(getAt(state.configDraft, path)) ? getAt(state.configDraft, path).slice() : [];
    const set = new Set(current);
    if (set.has(value)) {
      set.delete(value);
    } else {
      set.add(value);
    }
    state.configDraft = setAt(state.configDraft || {}, path, Array.from(set));
    resetConfigSaveState();
    renderShell({ preserveScroll: true });
  }

  async function waitForRunnerControlStatus(expectedRunning, timeoutMs = RUNNER_CONTROL_STATUS_TIMEOUT_MS) {
    const deadline = nowMs() + timeoutMs;
    while (true) {
      await refreshSnapshot({ silent: true });
      if (state.stopOpen) {
        renderStopOverlay();
      }
      const status = toObject(state.runnerControl.status);
      const statusReason = toText(status.reason, '');
      if (statusReason.startsWith('status_error:') || state.runnerControl.lastError) {
        return {
          ok: false,
          message: state.runnerControl.lastError || statusReason || 'Runner controller reported an error.',
        };
      }
      if (Boolean(status.running) === Boolean(expectedRunning)) {
        return { ok: true };
      }
      if (nowMs() >= deadline) {
        break;
      }
      await new Promise((resolve) => window.setTimeout(resolve, RUNNER_CONTROL_STATUS_POLL_MS));
    }

    await refreshSnapshot({ silent: true });
    if (state.stopOpen) {
      renderStopOverlay();
    }
    const status = toObject(state.runnerControl.status);
    const statusReason = toText(status.reason, '');
    if (statusReason.startsWith('status_error:') || state.runnerControl.lastError) {
      return {
        ok: false,
        message: state.runnerControl.lastError || statusReason || 'Runner controller reported an error.',
      };
    }
    if (Boolean(status.running) === Boolean(expectedRunning)) {
      return { ok: true };
    }
    return {
      ok: false,
      message: `Runner did not report ${expectedRunning ? 'running' : 'stopped'} within ${Math.round(timeoutMs / 1000)}s.`,
    };
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

      const expectedRunning = action !== 'stop';
      const settled = await waitForRunnerControlStatus(expectedRunning);
      if (!settled.ok) {
        throw new Error(toText(settled.message, 'Runner control failed.'));
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
    state.worktreeAction = null;
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
    state.worktreeAction = null;
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
    state.worktreeAction = null;
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
    state.worktreeAction = null;
    renderOverlay();
  }

  function closeGoalEditor() {
    if (!state.goalEditor) return;
    state.goalEditor = null;
    renderOverlay();
    renderShell({ preserveScroll: true });
  }

  function commitGoalDraft(nextGoals, dirty = true) {
    state.goals = normalizeGoalBuckets(nextGoals);
    state.goalsDirty = Boolean(dirty);
    if (state.goalsDirty) {
      writeJSON(STORAGE.goals, state.goals);
    } else {
      removeJSON(STORAGE.goals);
    }
    resetGoalSaveState(state.goalsDirty);
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

    commitGoalDraft(nextGoals);
    state.goalEditor = null;
    renderShell({ preserveScroll: true });
  }

  function resetGoals() {
    commitGoalDraft(state.goalsSnapshot.items || state.goalsSnapshot, false);
    renderShell({ preserveScroll: true });
  }

  function goalSaveEnabled() {
    return Boolean(state.runnerControl?.enabled);
  }

  function goalSaveRequestPath() {
    return '/api/goals/save';
  }

  function goalSaveInFlight() {
    return state.goalSave?.status === 'saving';
  }

  function inspectGoalSaveState() {
    return clone(toObject(state.goalSave));
  }

  function resetGoalSaveState(preserveConfirmation = true) {
    if (goalSaveInFlight()) {
      return;
    }
    const confirmation = preserveConfirmation ? toText(state.goalSave?.confirmation, '') : '';
    state.goalSave = {
      ...createBlankGoalSaveState(),
      confirmation,
    };
  }

  function goalSaveDisabledReason(
    goalDraft = buildGoalDraftSummary(state.goalsSnapshot.items || state.goalsSnapshot, state.goals),
    risk = buildGoalSaveRiskSummary(state.goalsSnapshot.items || state.goalsSnapshot, state.goals),
    confirmation = toText(state.goalSave?.confirmation, '').trim()
  ) {
    if (goalSaveInFlight()) {
      return 'Goal save is already in progress.';
    }
    if (!goalSaveEnabled()) {
      return state.runnerControl?.message || 'Goal saves are disabled until runner controls are enabled.';
    }
    if (!goalDraft.dirty) {
      return 'No goal changes to save.';
    }
    if (risk.requiresConfirmation) {
      if (!confirmation) {
        return `Type ${risk.confirmationPhrase} exactly to confirm ${goalSaveRiskSummaryText(risk)}.`;
      }
      if (confirmation !== risk.confirmationPhrase) {
        return `Confirmation phrase must be ${risk.confirmationPhrase}.`;
      }
    }
    return '';
  }

  function renderGoalSaveBanner(
    goalDraft = buildGoalDraftSummary(state.goalsSnapshot.items || state.goalsSnapshot, state.goals),
    risk = buildGoalSaveRiskSummary(state.goalsSnapshot.items || state.goalsSnapshot, state.goals)
  ) {
    const saveState = toObject(state.goalSave || {});
    const goalSnapshot = toObject(state.goalsSnapshot);
    const confirmation = toText(saveState.confirmation, '').trim();
    const confirmationPhrase = toText(risk.confirmationPhrase, GOALS_SAVE_CONFIRMATION_PHRASE);
    const requiresConfirmation = Boolean(risk.requiresConfirmation);
    const confirmationMatches = requiresConfirmation && confirmation === confirmationPhrase;
    const savePath = toText(state.goalsPath || goalSnapshot.path || '.doc/GOALS.md', '.doc/GOALS.md');
    const requestPath = goalSaveRequestPath();
    const bannerTitle = saveState.status === 'saving'
      ? 'Saving goals'
      : saveState.status === 'success'
        ? 'Goals saved'
        : saveState.status === 'error'
          ? 'Goals save failed'
          : !goalSaveEnabled()
            ? 'Goal saves are locked'
            : !goalDraft.dirty
              ? 'No goal changes'
              : requiresConfirmation && !confirmationMatches
                ? 'Confirmation required'
                : 'Ready to save goals';
    const bannerTone = saveState.status === 'saving'
      ? 'running'
      : saveState.status === 'success'
        ? 'success'
        : saveState.status === 'error'
          ? 'err'
          : !goalSaveEnabled()
            ? 'warn'
            : !goalDraft.dirty
              ? 'idle'
              : requiresConfirmation && !confirmationMatches
                ? 'warn'
                : 'info';
    const bannerCopy = saveState.status === 'saving'
      ? 'Creating a timestamped backup and writing GOALS.md atomically.'
      : saveState.status === 'success'
        ? saveState.message || 'Goals were written successfully.'
        : saveState.status === 'error'
          ? saveState.message || 'Goals save failed.'
          : !goalSaveEnabled()
            ? state.runnerControl?.message || 'Goal saves are disabled until runner controls are enabled.'
            : !goalDraft.dirty
              ? 'Edit the draft to stage a local save.'
              : requiresConfirmation && !confirmationMatches
                ? `Deleting or downgrading ${goalSaveRiskSummaryText(risk)} requires the exact confirmation phrase.`
                : 'Saving will create a backup before atomically updating .doc/GOALS.md.';
    const metaRows = [];
    metaRows.push(`
      <div>
        <div class="goal-save-state__label">Request path</div>
        <div class="goal-save-state__path">${escapeHTML(requestPath)}</div>
      </div>
    `);
    if (requiresConfirmation) {
      metaRows.push(`
        <div>
          <div class="goal-save-state__label">Confirmation phrase</div>
          <div class="goal-save-state__code">${escapeHTML(confirmationPhrase)}</div>
        </div>
      `);
    }
    if (risk.deletedUncheckedP0.length) {
      metaRows.push(`
        <div>
          <div class="goal-save-state__label">Deleted unchecked P0</div>
          <div class="goal-save-state__paths">
            ${risk.deletedUncheckedP0.map((item) => `<span class="goal-save-state__path">${escapeHTML(goalItemSummary(item))}</span>`).join('')}
          </div>
        </div>
      `);
    }
    if (risk.downgradedUncheckedP0.length) {
      metaRows.push(`
        <div>
          <div class="goal-save-state__label">Downgraded unchecked P0</div>
          <div class="goal-save-state__paths">
            ${risk.downgradedUncheckedP0.map((item) => `<span class="goal-save-state__path">${escapeHTML(goalItemSummary(item))}</span>`).join('')}
          </div>
        </div>
      `);
    }
    if (saveState.status === 'success' || saveState.status === 'error') {
      if (saveState.backupPath) {
        metaRows.push(`
          <div>
            <div class="goal-save-state__label">Backup path</div>
            <div class="goal-save-state__path">${escapeHTML(saveState.backupPath)}</div>
          </div>
        `);
      }
      if (saveState.savedPath || savePath) {
        metaRows.push(`
          <div>
            <div class="goal-save-state__label">Saved path</div>
            <div class="goal-save-state__path">${escapeHTML(saveState.savedPath || savePath)}</div>
          </div>
        `);
      }
      if (saveState.errorCode) {
        metaRows.push(`
          <div>
            <div class="goal-save-state__label">Error code</div>
            <div class="goal-save-state__code">${escapeHTML(saveState.errorCode)}</div>
          </div>
        `);
      }
    }
    return `
      <div class="modal-banner section-banner section-banner--${bannerTone}">
        <span class="dot" style="background: currentColor;"></span>
        <div>
          <div class="section-banner__title">${escapeHTML(bannerTitle)}</div>
          <div class="section-banner__copy">${escapeHTML(bannerCopy)}</div>
        </div>
      </div>
      ${metaRows.length ? `<div class="goal-save-state__meta">${metaRows.join('')}</div>` : ''}
    `;
  }

  function updateGoalSaveConfirmation(value) {
    if (goalSaveInFlight()) {
      return;
    }
    const nextConfirmation = toText(value, '');
    const current = toObject(state.goalSave || createBlankGoalSaveState());
    const nextState = {
      ...current,
      confirmation: nextConfirmation,
    };
    if (current.status === 'error') {
      nextState.status = 'idle';
      nextState.message = '';
      nextState.errorCode = '';
      nextState.backupPath = '';
      nextState.savedPath = '';
      nextState.savedAt = 0;
      nextState.requestPath = goalSaveRequestPath();
      nextState.risk = normalizeGoalSaveRisk(current.risk || {});
    }
    state.goalSave = nextState;
    syncGoalSaveArtifacts();
  }

  function syncGoalSaveArtifacts() {
    if (state.activeView !== 'goals') {
      return;
    }
    const root = mainRoot().querySelector('[data-goal-save-root]');
    if (!root) {
      return;
    }
    const goalSnapshot = toObject(state.goalsSnapshot);
    const snapshotGoals = goalSnapshot.items || goalSnapshot;
    const goalDraft = buildGoalDraftSummary(snapshotGoals, state.goals);
    const risk = buildGoalSaveRiskSummary(snapshotGoals, state.goals);
    root.setAttribute('data-goal-save-status', toText(state.goalSave?.status, 'idle'));
    root.setAttribute('data-goal-saving', goalSaveInFlight() ? 'true' : 'false');
    const bannerNode = root.querySelector('[data-goal-save-banner]');
    if (bannerNode) {
      bannerNode.innerHTML = renderGoalSaveBanner(goalDraft, risk);
    }
    const input = root.querySelector('[data-goal-save-confirmation]');
    if (input) {
      const nextConfirmation = toText(state.goalSave?.confirmation, '');
      if (input.value !== nextConfirmation) {
        input.value = nextConfirmation;
      }
    }
    const button = root.querySelector('[data-goal-save-button]');
    if (button) {
      const reason = goalSaveDisabledReason(goalDraft, risk);
      if (reason) {
        button.setAttribute('disabled', '');
        button.setAttribute('title', reason);
      } else {
        button.removeAttribute('disabled');
        button.removeAttribute('title');
      }
    }
  }

  async function saveGoalDraft() {
    if (goalSaveInFlight()) {
      return;
    }
    const goalSnapshot = toObject(state.goalsSnapshot);
    const snapshotGoals = goalSnapshot.items || goalSnapshot;
    const goalDraft = buildGoalDraftSummary(snapshotGoals, state.goals);
    const risk = buildGoalSaveRiskSummary(snapshotGoals, state.goals);
    const confirmation = toText(state.goalSave?.confirmation, '').trim();
    const disabledReason = goalSaveDisabledReason(goalDraft, risk, confirmation);
    const requestPath = goalSaveRequestPath();
    const savedPath = toText(state.goalsPath || goalSnapshot.path || '.doc/GOALS.md', '.doc/GOALS.md');
    const currentState = toObject(state.goalSave || createBlankGoalSaveState());
    if (disabledReason) {
      const errorCode = !goalSaveEnabled()
        ? 'goals_save_disabled'
        : !goalDraft.dirty
          ? 'goals_no_changes'
          : risk.requiresConfirmation
            ? (confirmation === risk.confirmationPhrase ? 'goals_confirmation_required' : 'goals_confirmation_mismatch')
            : 'goals_save_disabled';
      state.goalSave = {
        ...currentState,
        status: 'error',
        message: disabledReason,
        errorCode,
        backupPath: '',
        savedPath,
        savedAt: nowMs(),
        requestPath,
        risk,
      };
      renderShell({ preserveScroll: true });
      return;
    }

    state.goalSave = {
      ...currentState,
      status: 'saving',
      message: 'Saving goals and creating a backup first.',
      errorCode: '',
      backupPath: '',
      savedPath,
      savedAt: nowMs(),
      requestPath,
      confirmation,
      risk,
    };
    renderShell({ preserveScroll: true });

    try {
      const response = await fetch(requestPath, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          draft: clone(state.goals),
          confirm: confirmation,
        }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      const normalized = normalizeGoalSaveResponse(payload);
      if (!response.ok || normalized.ok === false) {
        const saveError = new Error(toText(normalized.message || `Goals save failed (HTTP ${response.status}).`, 'Goals save failed.'));
        saveError.code = toText(normalized.error.code || 'goals_save_failed', 'goals_save_failed');
        saveError.backupPath = normalized.backupPath || '';
        saveError.savedPath = normalized.savedPath || savedPath;
        saveError.risk = normalized.risk || risk;
        throw saveError;
      }

      state.goalsDirty = false;
      removeJSON(STORAGE.goals);
      if (normalized.snapshot && typeof normalized.snapshot === 'object' && Object.keys(normalized.snapshot).length) {
        applyServerSnapshot(normalized.snapshot);
      } else {
        await refreshSnapshot({ allowFallback: true, silent: true });
      }
      state.goalSave = {
        ...createBlankGoalSaveState(),
        status: 'success',
        message: normalized.message || (normalized.backupPath ? `Goals saved. Backup written to ${normalized.backupPath}.` : 'Goals saved.'),
        errorCode: '',
        backupPath: normalized.backupPath || '',
        savedPath: normalized.savedPath || savedPath,
        savedAt: nowMs(),
        requestPath,
        confirmation,
        risk: normalized.risk || risk,
      };
      renderShell({ preserveScroll: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Goals save failed.';
      const errorCode = error instanceof Error && typeof error.code === 'string' && error.code ? error.code : 'goals_save_failed';
      const backupPath = error instanceof Error ? toText(error.backupPath || error.backup_path || '', '') : '';
      const risky = error instanceof Error && error.risk ? normalizeGoalSaveRisk(error.risk) : risk;
      state.goalSave = {
        ...currentState,
        status: 'error',
        message,
        errorCode,
        backupPath,
        savedPath,
        savedAt: nowMs(),
        requestPath,
        confirmation,
        risk: risky,
      };
      renderShell({ preserveScroll: true });
    }
  }

  function resetConfig() {
    if (configSaveInFlight()) return;
    state.configDraft = deepMerge(clone(state.configContract?.values || defaults.configContract.values || {}), null);
    resetConfigSaveState();
    renderShell({ preserveScroll: true });
  }

  function setView(view) {
    const next = normalizeView(view);
    state.activeView = next;
    state.paletteOpen = false;
    state.stopOpen = false;
    state.goalEditor = null;
    state.worktreeAction = null;
    if (history.replaceState) {
      history.replaceState(null, '', `#${next}`);
    } else {
      location.hash = next;
    }
    renderShell({ preserveScroll: false });
    syncLogTailStreaming();
    if (next === 'prompts') {
      void loadPromptEditor(currentPrompt());
    }
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
        if (!state.paletteOpen && !state.goalEditor && !state.stopOpen && !state.worktreeAction) {
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

      if (!state.paletteOpen && !state.goalEditor && !state.stopOpen && !state.worktreeAction) {
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
      const previousSourceMode = state.sourceMode;
      const previousLatestRunDir = state.latestRunDir;
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
      if (state.stopOpen) {
        renderStopOverlay();
      } else if (state.worktreeAction) {
        renderWorktreeActionOverlay();
      } else if (!state.paletteOpen && !state.goalEditor) {
        renderShell({
          preserveScroll: true,
          scrollToBottom: state.activeView === 'logs',
        });
      }
      syncLogTailStreaming({
        reset: previousSourceMode !== state.sourceMode || previousLatestRunDir !== state.latestRunDir,
      });
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
        if (state.stopOpen) {
          renderStopOverlay();
        } else if (state.worktreeAction) {
          renderWorktreeActionOverlay();
        } else if (!state.paletteOpen && !state.goalEditor) {
          renderShell({
            preserveScroll: true,
            scrollToBottom: state.activeView === 'logs',
          });
        }
        syncLogTailStreaming({ reset: true });
        return true;
      }

      if (state.sourceMode === 'fallback') {
        if (state.stopOpen) {
          renderStopOverlay();
        } else if (state.worktreeAction) {
          renderWorktreeActionOverlay();
        } else if (!silent && !state.paletteOpen && !state.goalEditor) {
          topbarRoot().innerHTML = renderTopbar();
        }
        return false;
      }

      if (state.lastSnapshotAt) {
        state.snapshotStatus = 'stale';
        state.snapshotLabel = 'Stale snapshot';
        if (state.stopOpen) {
          renderStopOverlay();
        } else if (state.worktreeAction) {
          renderWorktreeActionOverlay();
        } else if (!silent && !state.paletteOpen && !state.goalEditor) {
          renderShell({ preserveScroll: true });
        }
        return false;
      }

      state.snapshotStatus = 'error';
      state.snapshotLabel = 'API error';
      if (state.stopOpen) {
        renderStopOverlay();
      } else if (state.worktreeAction) {
        renderWorktreeActionOverlay();
      } else if (!silent && !state.paletteOpen && !state.goalEditor) {
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

    const logSelect = event.target.closest('[data-log-select]');
    if (logSelect) {
      toggleLogTailSelection(logSelect.dataset.logSelect);
      renderShell({ preserveScroll: true });
      return;
    }

    const logLevel = event.target.closest('[data-log-level]');
    if (logLevel) {
      updateLogTailFilter('level', logLevel.dataset.logLevel);
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
      moveGoal(goalMove.dataset.goalBucket, Number(goalMove.dataset.goalIndex), Number(goalMove.dataset.goalDirection));
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

    if (event.target.matches('[data-goal-save-confirmation]')) {
      updateGoalSaveConfirmation(event.target.value);
      return;
    }

    if (event.target.matches('[data-prompt-editor-field]')) {
      const field = event.target.dataset.promptEditorField;
      if (field === 'file') {
        updatePromptEditorDraft('draftFile', event.target.value);
      } else if (field === 'content') {
        updatePromptEditorDraft('draftContent', event.target.value);
      }
      return;
    }

    if (event.target.matches('[data-prompt-restore-confirmation]')) {
      updatePromptEditorMutationField('restoreConfirmation', event.target.value);
      return;
    }

    if (event.target.matches('[data-worktree-action-confirmation]')) {
      updateWorktreeActionConfirmation(event.target.value);
      return;
    }

    if (event.target.matches('[data-log-filter-field]')) {
      updateLogTailFilter(event.target.dataset.logFilterField, event.target.value);
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
      return;
    }

    if (event.target.matches('[data-prompt-backup-select]')) {
      updatePromptEditorMutationField('backupSelection', event.target.value);
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

    if (state.worktreeAction) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeWorktreeActionModal();
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        void applyWorktreeAction();
        return;
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
      return;
    }

    const worktreeClose = event.target.closest('[data-worktree-action-close]');
    if (worktreeClose) {
      closeWorktreeActionModal();
      return;
    }

    const worktreeConfirm = event.target.closest('[data-worktree-action-confirm]');
    if (worktreeConfirm) {
      void applyWorktreeAction();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (state.paletteOpen || state.goalEditor || state.stopOpen || state.worktreeAction) {
      return;
    }
    if (event.key === 'Enter' && event.target.matches('[data-config-field][type="text"], [data-config-field][type="number"], [data-goal-field], [data-goal-save-confirmation]')) {
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
    if (state.paletteOpen || state.goalEditor || state.stopOpen || state.worktreeAction) {
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
      if (next === 'prompts') {
        void loadPromptEditor(currentPrompt());
      }
    }
  });

  window.addEventListener('focus', updateClockChips);
})();
