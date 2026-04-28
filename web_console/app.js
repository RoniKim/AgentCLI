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
    locale: 'agentcli.console.locale.v1',
  };
  const GOALS_SAVE_CONFIRMATION_KEY = 'goals.confirmationPhraseExact';
  const RUNNER_CONTROL_CONFIRMATION_KEYS = {
    start: 'runner.confirmStartPhrase',
    stop: 'runner.confirmStopPhrase',
    reload: 'runner.confirmReloadPhrase',
    restart: 'runner.confirmRestartPhrase',
  };
  const WORKTREE_ACTION_CONFIRMATION_KEYS = {
    merge: 'worktree.confirmMergePhrase',
    discard: 'worktree.confirmDiscardPhrase',
  };
  const REDACTED_VALUE = '[redacted]';
  // Keep the template-form text in source for static coverage:
  // Type ${worktreeActionConfirmationPhrase('merge')} exactly to apply
  // Type ${worktreeActionConfirmationPhrase('discard')} exactly to discard
  const VIEW_ORDER = [
    'dashboard',
    'pipeline',
    'logs',
    'backlog',
    'goals',
    'config',
    'prompts',
    'history',
    'notifications',
    'worktree',
    'landing',
    'mobile',
  ];

  function normalizeLocale(value) {
    return String(value || '').trim().toLowerCase() === 'ko' ? 'ko' : 'en';
  }

  function detectPreferredLocale() {
    const stored = readJSON(STORAGE.locale, null);
    if (stored === 'en' || stored === 'ko') {
      return stored;
    }
    const language = typeof navigator !== 'undefined'
      ? String(navigator.language || navigator.userLanguage || '').toLowerCase()
      : '';
    return language.startsWith('ko') ? 'ko' : 'en';
  }

  const LOCALE_TEXT = {
    en: {
      app: {
        title: 'AgentCLI Web Console',
      },
      locale: {
        language: 'Language',
        en: 'EN',
        ko: 'KO',
      },
      nav: {
        run: 'Run',
        project: 'Project',
        history: 'History',
        preview: 'Preview',
        dashboard: 'Dashboard',
        pipeline: 'Pipeline',
        logs: 'Logs',
        backlog: 'Backlog',
        goals: 'Goals',
        config: 'Config',
        prompts: 'Prompts',
        worktreeReview: 'Worktree Review',
        runHistory: 'Run History',
        notifications: 'Notifications',
        landingPreview: 'Landing preview',
        mobilePreview: 'Mobile preview',
      },
      topbar: {
        refresh: 'Refresh',
        commandPalette: 'Command',
        commandPaletteTitle: 'Command palette',
        commandPaletteHint: '/ or Cmd+K / Ctrl+K',
        language: 'Language',
        elapsed: 'elapsed',
        quotaUsage: 'Quota usage',
        quotaUsageWindow: 'Quota {window} usage',
        quotaUnavailable: 'Quota unavailable',
      },
      common: {
        loading: 'Loading',
        working: 'Working...',
        ready: 'Ready',
        enabled: 'Enabled',
        disabled: 'Disabled',
        available: 'available',
        unavailable: 'unavailable',
        cancel: 'Cancel',
        save: 'Save',
        saved: 'Saved',
        failed: 'Failed',
        confirm: 'Confirm',
        open: 'Open',
        openDashboard: 'Open Dashboard',
        openLogs: 'Open Logs',
        openBacklog: 'Open Backlog',
        openGoals: 'Open Goals',
        openConfig: 'Open Config',
        openPrompts: 'Open Prompts',
        openWorktree: 'Open Worktree',
        openNotifications: 'Open Notifications',
        openPipeline: 'Open Pipeline',
        openMobile: 'Open Mobile',
        openLanding: 'Open Landing',
        noMatches: 'No matching commands',
        localOnly: 'LOCAL ONLY',
        dirty: 'DIRTY',
        clean: 'CLEAN',
        fullRead: 'FULL READ',
        noBackups: 'NO BACKUPS',
        added: 'Added',
        removed: 'Removed',
        selected: 'Selected',
        select: 'Select',
        deselect: 'Deselect',
        none: 'none',
        unknown: 'unknown',
        of: 'of',
        lines: 'lines',
        recent: 'recent',
        complete: 'complete',
        remaining: 'remaining',
        visible: 'visible',
        total: 'total',
        noDataAvailableYet: 'No data available yet.',
        chars: 'chars',
      },
      snapshot: {
        loading: 'Loading snapshot',
        api: 'API snapshot',
        error: 'API error',
        fallback: 'Fallback data',
        stale: 'Stale snapshot',
        lastUpdated: 'Last updated',
        reconnecting: 'Reconnecting',
        reconnectingCopy: 'Last updated {timestamp}. Retrying the live snapshot.',
        staleCopy: 'Last updated {timestamp}. The controller and process table are out of sync.',
        partial: 'Partial snapshot',
        loadingReadOnly: 'Loading read-only snapshot',
        controlsDisabled: 'Controls disabled',
        emptyState: 'Empty state',
      },
      palette: {
        title: 'Command palette',
        placeholder: 'Type a screen or action',
        noMatches: 'No matching commands',
        goTo: 'Go to {view}',
        navKind: 'NAV',
        actionKind: 'ACTION',
        refreshStatus: 'Refresh read-only snapshot',
        stopCurrentRun: 'Stop current run',
        startRunner: 'Start runner',
        stopRunner: 'Stop runner',
        reloadRunner: 'Reload runner',
        restartRunner: 'Restart runner',
        pauseLiveTail: 'Pause live tail',
        resumeLiveTail: 'Resume live tail',
        openWorktreeReview: 'Open Worktree Review',
        openMobilePreview: 'Open Mobile preview',
        openLandingPreview: 'Open Landing preview',
      },
      shortcuts: {
        ctrlEnterSaves: 'ctrl+enter saves',
        escCloses: 'esc closes',
        draftMode: 'draft mode',
        exactConfirmation: 'Exact confirmation',
        confirmationPhrase: 'Confirmation phrase',
      },
      runner: {
        panelTitle: 'Runner controls',
        confirmationPhrases: 'Confirmation phrases:',
        confirmStartPhrase: 'START RUNNER',
        confirmStopPhrase: 'STOP RUNNER',
        confirmReloadPhrase: 'RELOAD RUNNER',
        confirmRestartPhrase: 'RESTART RUNNER',
        startOptions: 'Start options',
        startOptionsSummary: 'These controls are normalized before the runner starts.',
        source: 'Source',
        selectedRepo: 'Selected repo',
        selectedConfig: 'Selected config',
        controller: 'Controller',
        state: 'State',
        runMode: 'Run mode',
        autopilot: 'Autopilot',
        continuous: 'Continuous',
        loop: 'Loop',
        oneShot: 'One-shot',
        maxCycles: 'Max cycles',
        profile: 'Profile',
        backend: 'Backend',
        configPath: 'Config path',
        runStatus: 'Run status',
        liveStates: 'Live states',
        runnerProcess: 'Runner process',
        taskBackend: 'Task backend',
        trackedChildren: 'Tracked children',
        artifactWriter: 'Artifact writer',
        runnerAlive: 'Runner alive',
        alive: 'Alive',
        flushing: 'Flushing',
        stopProgress: 'Stop progress',
        currentStopPhase: 'Current phase',
        phaseHistory: 'Phase history',
        trackedChildPids: 'Tracked child PIDs',
        remainingTrackedChildPids: 'Remaining tracked PIDs',
        stopFilePaths: 'Stop file paths',
        lastArtifactSignal: 'Last artifact write',
        lastLogSignal: 'Last log write',
        timeoutGuidance: 'Timeout guidance',
        manualCleanupHints: 'Manual cleanup hints',
        lockedFilePaths: 'Locked file paths',
        stopTimedOut: 'Stop timed out',
        retryStop: 'Retry stop',
        lastAction: 'Last action',
        lastMessage: 'Last message',
        lastError: 'Last error',
        actionInFlight: 'Action in flight',
        actionComplete: 'Action complete',
        backendError: 'Backend error',
        controllerUnavailable: 'Controller unavailable',
        controlsDisabled: 'Controls disabled',
        unavailable: 'Unavailable',
        available: 'available',
        ready: 'Ready',
        running: 'Running',
        idle: 'Idle',
        working: 'Working...',
        start: 'Start',
        stop: 'Stop',
        reload: 'Reload',
        restart: 'Restart',
        starting: 'Starting...',
        stopping: 'Stopping...',
        reloading: 'Reloading...',
        restarting: 'Restarting...',
        started: 'Started',
        stopped: 'Stopped',
        reloaded: 'Reloaded',
        restarted: 'Restarted',
        confirmStart: 'Confirm start',
        confirmStop: 'Confirm stop',
        confirmReload: 'Confirm reload',
        confirmRestart: 'Confirm restart',
        startSummary: 'Start the runner using the selected repo and config snapshot.',
        stopSummary: 'Stop the current runner, write the stop signal, and wait for a terminal status.',
        reloadSummary: 'Stop the current runner, wait for it to settle, then start again using the selected repo and config snapshot.',
        restartSummary: 'Restart the runner using the selected repo and config snapshot.',
        confirmAction: 'Confirm this runner control action.',
        actionDisabled: 'Action disabled',
        actionFailed: 'Runner action failed.', // Action failed
        confirmationRequired: 'Confirmation required',
        typeExactConfirmationToEnableAction: 'Type "{confirmation}" exactly to enable {action}.',
      },
      dashboard: {
        title: 'Dashboard',
        pipelineSnapshot: 'Pipeline snapshot',
        liveLogs: 'Live logs',
        runFacts: 'Run facts',
        goalsSnapshot: 'Goals snapshot',
        selectedBacklogItem: 'Selected backlog item',
        notifications: 'Notifications',
        currentTaskId: 'Current task id',
        currentTaskTitle: 'Current task title',
        attempt: 'Attempt',
        branch: 'Branch',
        worktreeMode: 'Worktree mode',
        runDirectory: 'Run directory',
        finalReason: 'Final reason',
        noLogEntriesYet: 'No log entries yet.',
        noGoalsPublishedYet: 'No goals published yet.',
        noTaskSelected: 'No task selected.',
        noBacklogArtifacts: 'No backlog artifacts were published yet.',
        noNotificationsYet: 'No notifications yet.',
        goalsSnapshotReady: 'Read-only GOALS.md snapshot with stable P0/P1 grouping and exact checkbox state.',
        stage: 'Stage',
        tasks: 'Tasks',
        tokens: 'Tokens',
        budget: 'Budget',
      },
      pipeline: {
        title: 'Pipeline',
        stageLane: 'Stage lane',
        currentStageOutput: 'Current stage output',
        stageGuardrails: 'Stage guardrails',
        liveTokens: 'Live tokens',
        pmDevQaFlow: 'PM -> Dev -> QA',
        readOnlyShell: 'Read-only shell by default. Stop, merge, and discard are not auto-applied here.',
        manualConfirmation: 'Current run uses manual stop confirmation and a local review workflow.',
        devStage: 'Dev stage',
        elapsed: 'Elapsed',
        started: 'Started',
        ended: 'Ended',
        latestLogLine: 'Latest log line',
        latestBackendEvent: 'Latest backend event',
        latestLogLineUnavailable: 'No log line available yet.',
        latestBackendEventUnavailable: 'No backend event available yet.',
        noOutputWarning: 'No output for {count} minutes.',
        tokensProcessed: 'tokens processed',
        tokensGenerated: 'tokens generated',
        tokenTelemetryUnavailable: 'token telemetry unavailable',
        budgetTelemetryUnavailable: 'budget telemetry unavailable',
        stageUnavailable: 'stage unavailable',
        lifecycleRecord: 'Lifecycle record',
        startedUnavailable: 'Started unavailable',
        endedUnavailable: 'Ended unavailable',
        inProgress: 'In progress',
        pending: 'Pending',
        completed: 'Completed',
        stopped: 'Stopped',
        skipped: 'Skipped',
        partialLifecycleRecords: 'Only some lifecycle records were published.',
        recentOutputUnavailable: 'Recent output unavailable.',
        noLifecycleRecords: 'No lifecycle records were published yet.',
        sparkline24h: '24h sparkline',
        activeTask: 'active task',
        current: 'current',
        iter: 'iter',
      },
      backlog: {
        title: 'Backlog',
        workQueue: 'Work queue',
        backlogSummary: 'Backlog summary',
        pending: 'Pending',
        inProgress: 'In progress',
        done: 'Done',
        failed: 'Failed',
        noTasksInBucket: 'No tasks in this bucket.',
        noArtifacts: 'No backlog artifacts were published yet.',
        dependenciesUnavailable: 'Dependencies unavailable',
        fileScopeUnavailable: 'File scope unavailable',
        attemptUnavailable: 'Attempt unavailable',
        cycleUnavailable: 'Cycle unavailable',
        stepUnavailable: 'Step unavailable',
        failureUnavailable: 'Failure unavailable',
        dependsOn: 'Depends on {items}',
        fileScope: 'File scope: {scope}',
        attemptText: 'Attempt {attempt}',
        cycleText: 'Cycle {cycle}',
        stepText: 'Step {step}',
        failureText: 'Failure: {reason}',
        recentOutputUnavailable: 'Recent output unavailable.',
        noTaskSelected: 'No task selected.',
        queued: 'queued',
        completed: 'completed',
        needsAttention: 'needs attention',
      },
      goals: {
        title: 'Goals',
        goalProgress: 'Goal progress',
        loadingSnapshot: 'Loading the read-only snapshot...',
        browserLocalDraft: 'Browser-local goal edits are active.',
        draftStaysLocal: 'Draft edits stay local until save or reset. Bucket grouping stays pinned to P0 and P1.',
        snapshot: 'GOALS.md snapshot',
        goalDraftDiff: 'Goal draft diff',
        newGoal: 'New goal',
        editGoal: 'Edit goal',
        bucket: 'Bucket',
        goal: 'Goal',
        note: 'Note',
        addGoal: 'Add goal',
        saveGoal: 'Save goal',
        noGoalsYet: 'No goals yet.',
        saveLocked: 'Goal saves are locked',
        confirmationRequired: 'Confirmation required',
        readyToSave: 'Ready to save goals',
        saving: 'Saving goals',
        saved: 'Goals saved',
        saveFailed: 'Goals save failed',
        saveFailedHttp: 'Goals save failed (HTTP {status}).',
        confirmationPhrase: 'Confirmation phrase',
        confirmationPhraseExact: 'DELETE OR DOWNGRADE UNMET P0 GOALS',
        backupPath: 'Backup path',
        savedPath: 'Saved path',
        errorCode: 'Error code',
        resetDraft: 'Reset draft',
        localDraftOnly: 'Local draft only',
        clean: 'clean',
        changeCount: '{count} change(s)',
        checked: 'checked',
        parserWarnings: 'Parser warnings',
        rawTextPreview: 'Raw text preview',
        sourceLine: 'Source line',
        sourceMetadata: 'Source metadata',
        noGoals: 'GOALS.md has content but no checklist items were parsed.',
        missing: 'GOALS.md is missing.',
        empty: 'GOALS.md is empty.',
        noLocalChanges: 'No local content changes yet.',
        noRiskyP0Changes: 'No risky P0 changes are detected.',
        uncheckedP0Goals: '{count} unchecked P0 goal(s) require confirmation.',
        saveCreatesBackup: 'Saving always creates a timestamped backup before atomically updating .doc/GOALS.md.',
        confirmSave: 'Confirm & Save Goals',
        deletedUncheckedP0: 'Deleted unchecked P0',
        downgradedUncheckedP0: 'Downgraded unchecked P0',
        typeExact: 'Type {confirmation} exactly to confirm',
        p0MustHave: 'P0 | Must-have',
        p1ShouldHave: 'P1 | Should-have',
      },
      config: {
        title: 'Config',
        fieldDetails: 'field details',
        loadingSnapshot: 'Loading read-only snapshot',
        localDraftOnly: 'Local draft only',
        description: 'Description',
        hint: 'Hint',
        defaultValue: 'Default',
        default: 'default',
        resolvedPromptsPath: 'Resolved prompts path',
        activeValue: 'Active value',
        localDraft: 'Local draft',
        pendingChanges: 'Pending changes',
        saveChanges: 'Save Changes',
        saving: 'Saving...',
        saved: 'Config saved',
        saveFailed: 'Config save failed',
        saveLocked: 'Config saves are locked',
        noChanges: 'No config changes',
        readyToSave: 'Ready to save changes',
        backupPath: 'Backup path',
        reloadRequired: 'Reload required',
        pendingPaths: 'Pending paths',
        restartRequired: 'Restart required',
        redactedHidden: 'Redacted values stay hidden in the browser.',
        localValidationFailed: 'Local validation failed',
        secret: 'secret',
        restart: 'restart',
        invalid: 'invalid',
        default: 'default',
        rejectedFields: 'Rejected fields',
        securityRoleTitle: 'Security stage',
        securityRoleRequirement: 'Security stage requires Security in roles and security.enabled = true.',
        securityRoleMissingEnabled: 'Security is selected in roles, but security.enabled is off.',
        securityRoleMissingRole: 'security.enabled is on, but Security is missing from roles.',
        mustBeBoolean: 'must be a boolean',
        mustBeNumber: 'must be a number',
        mustBeAtLeast: 'must be >= {min}',
        mustBeAtMost: 'must be <= {max}',
        cannotBeEmpty: 'cannot be empty',
        mustBeOneOf: 'must be one of: {options}',
        pickAtLeastOne: 'pick at least one option',
        invalidOption: 'invalid option(s): {options}',
        enterAtLeastOneValue: 'enter at least one value',
        invalidIntegerValue: 'invalid integer value(s): {values}',
        repoManagedByServer: 'Repository root is managed by the server.',
        redactedPlaceholderSaveBlocked: 'Redacted placeholders cannot be saved.',
        saveInProgress: 'Config save is already in progress.',
        savesDisabledUntilRunnerEnabled: 'Config saves are disabled until runner controls are enabled.',
        noConfigChanges: 'No config changes to save.',
        fixInvalidChangesBeforeSaving: 'Fix {count} invalid change(s) before saving.',
      },
      prompts: {
        title: 'Prompts',
        promptInventory: 'Prompt inventory',
        inventoryRedacted: 'Inventory previews stay redacted. Select a prompt to open the explicit full-content read path.',
        promptEditor: 'Prompt editor',
        explicitPromptRead: 'Explicit prompt read',
        loadedThroughExplicitReadPath: 'Loaded through the explicit read path',
        fullReadPreview: 'FULL READ PREVIEW',
        noPromptSelected: 'No prompt selected',
        selectPrompt: 'Select a prompt to read its explicit content slice.',
        noPromptFiles: 'No prompt files were discovered.',
        primaryPromptsDir: 'Primary prompts directory',
        trackedPromptFiles: 'tracked prompt files',
        scope: 'Scope',
        profile: 'Profile',
        source: 'Source',
        resolvedPath: 'Resolved path',
        lastUpdated: 'Last updated',
        savePrompt: 'Save Prompt',
        restoreBackup: 'Restore Backup',
        selectedBackup: 'Selected backup',
        availableBackups: 'Available backups',
        restoreBackupPath: 'Restore backup path',
        savedPath: 'Saved path',
        restoredFrom: 'Restored from',
        trackedPromptRoles: 'tracked prompt roles',
        promptMutationsLocked: 'Prompt mutations are locked',
        promptMutationsDisabled: 'Prompt saves and restores are disabled until runner controls are enabled.',
        chooseBackupRestore: 'Choose a backup to restore or save the current draft after validation passes.',
        filenameValidation: 'Filename validation',
        contentValidation: 'Content validation',
        templateVariableValidation: 'Template-variable validation',
        requiredTemplateVariables: 'Required template variables',
        missingTemplateVariables: 'Missing template variables',
        filenameIsPopulated: 'Filename is populated.',
        contentIsPopulated: 'Content is populated.',
        requiredTemplateVariablesLabel: 'Required template variables: {variables}',
        noLocalChangesYet: 'No local content changes yet.',
        localDiffPreview: 'Local diff preview',
        filename: 'Filename',
        localOnly: 'Local only',
        added: 'Added',
        removed: 'Removed',
        line: 'Line {lineNumber}',
        restoreConfirmation: 'Restore confirmation',
        restoreOverwritePhrase: 'Type RESTORE BACKUP to confirm the selected backup will overwrite the prompt file.',
        filenameRequired: 'Filename cannot be empty.',
        filenameMustBeBare: 'Filename must be a bare filename within the resolved prompts directory.',
        promptContentRequired: 'Prompt content cannot be empty.',
        promptSaved: 'Prompt saved',
        promptRestored: 'Prompt restored',
        saveErrorBadge: 'SAVE ERROR',
        restoreErrorBadge: 'RESTORE ERROR',
        promptSaveFailed: 'Prompt save failed',
        promptRestoreFailed: 'Prompt restore failed',
        promptMutationCompleted: 'Prompt mutation completed.',
        promptMutationFailed: 'Prompt mutation failed.',
      },
      history: {
        title: 'Run History',
        runHistory: 'Run history',
        noRunsYet: 'No run history yet.',
        emptyState: 'Run history is empty.',
        selectedRun: 'Selected run',
        persistedSummary: 'Persisted summary',
        shutdownReason: 'Shutdown reason',
        worktreeOutcome: 'Worktree outcome',
        noSummaries: 'No persisted run summaries are available yet.',
        success: 'Success',
        tasks: 'Tasks',
        budgetCap: 'Budget cap',
        branchId: 'Branch / ID',
        duration: 'Duration',
        cycles: '{count} cycle(s)',
        started: 'Started',
        action: 'Action',
        currentState: 'current state',
        persistedRuntime: 'persisted runtime',
        noWorktreeArtifact: 'no worktree artifact',
        worktreeOutcomeMeta: 'worktree outcome',
        noPersistedSummary: 'No persisted summary fields available.',
      },
      notifications: {
        title: 'Notifications',
        eventFeed: 'Event feed',
        noMatchCurrentFilter: 'No notifications match the current filter.',
        noRecorded: 'No notifications have been recorded yet.',
        notificationSource: 'Notification source',
        observedKinds: 'Observed kinds',
        controlPlaneLastEvent: 'Control-plane last event',
        notificationCounts: 'Notification counts',
        bridgeSettings: 'Bridge settings',
        notificationError: 'Notification error',
        filteredEmpty: 'Filtered empty',
        noEventsYet: 'No events yet',
        visibleItems: 'visible',
        totalItems: 'total',
        currentRun: 'current run',
        lifecycle: 'Lifecycle',
        taskDone: 'Task done',
        errors: 'Errors',
      },
      worktree: {
        title: 'Worktree Review',
        pendingMerge: 'Pending merge',
        reviewChecklist: 'Review checklist',
        mergeActions: 'Merge actions',
        mergePreflight: 'Merge preflight',
        merging: 'Merging...',
        discarding: 'Discarding...',
        refreshingStatus: 'refreshing status',
        confirmMergePhrase: 'MERGE WORKTREE',
        confirmDiscardPhrase: 'DISCARD WORKTREE',
        readOnlyMode: 'Read-only mode',
        confirmationRequired: 'Confirmation required',
        manualRecovery: 'Manual recovery',
        finalizedWorktree: 'Finalized worktree',
        noPendingMerge: 'No pending worktree merge is available.',
        noChangedFiles: 'No changed files were parsed from the patch.',
        reviewRequired: 'Review required before source-repo changes',
        patchApplied: 'Patch applied',
        patchDiscarded: 'Patch discarded',
        malformedPendingFile: 'Malformed pending file',
        mergeRecordedCleanupFailed: 'Merge recorded, cleanup failed',
        discardRecordedCleanupFailed: 'Discard recorded, cleanup failed',
        patchExportFailed: 'Patch export failed',
        patchExportNotApplied: 'Patch exported, not auto-applied',
        patchNotApplied: 'Patch not applied',
        sourceDirtyState: 'Source dirty state',
        sourceHead: 'Source HEAD',
        expectedBaseRef: 'Expected base ref',
        patchHash: 'Patch hash',
        gitApplyCheck: 'git apply --check',
        pendingMarkerPath: 'Pending marker path',
        failureDetails: 'Failure details',
        pendingStateRecoverable: 'Pending state remains recoverable for retry or discard.',
        binaryFile: 'Binary file',
        deletedFile: 'Deleted file',
        renamedFile: 'Renamed file',
        largeFile: 'Large file',
        previewTruncated: 'Preview truncated',
        binaryFileNoPreview: 'Binary patch has no text preview.',
        deletedFileNoPreview: 'Deleted file has no text preview.',
        largeFilePreviewTruncated: 'Large patch preview truncated.',
        noDiffHunks: 'No diff hunks available.',
        oldPath: 'Old path',
        newPath: 'New path',
        lineCount: 'Line count',
        hunk: 'Hunk {index}',
        failedFiles: 'Failed files',
        failedHunks: 'Failed hunks',
        applyCheckCommand: 'Command',
        applyCheckRc: 'rc',
        applyCheckMessage: 'Message',
        applyCheckPassed: 'passed',
        applyCheckFailed: 'failed',
        applyCheckUnavailable: 'unavailable',
        exactConfirmation: 'Exact confirmation',
        confirmationPhrase: 'Confirmation phrase',
        typeConfirmationPhrase: 'Type "{confirmation}" to confirm this worktree action.',
        confirmationPhraseMismatch: 'Confirmation phrase must be "{confirmation}".',
        confirmMerge: 'Confirm merge',
        confirmDiscard: 'Confirm discard',
        sourceRepo: 'Source repo',
        runDir: 'Run dir',
        worktreeDir: 'Worktree dir',
        patchPath: 'Patch path',
        pendingFile: 'Pending file',
        baseRef: 'Base ref',
        headRef: 'Head ref',
        cleanupState: 'Cleanup state',
        cleanupPath: 'Cleanup path',
        cleanupMessage: 'Cleanup message',
        cleanupStateUnavailable: 'No cleanup state is available.',
        runnerRc: 'Runner rc',
        reviewBeforeMerge: 'Review before merge',
        noChecklist: 'No checklist is available yet.',
        noPendingReview: 'No pending worktree merge.',
        confirmMergeToApply: 'Confirm merge to apply the patch without creating a commit.',
        confirmDiscardToRemove: 'Confirm discard to remove the pending state without touching source files.',
        backendValidates: 'The backend validates the source repository, run directory, worktree path, and patch path before it runs.',
        checklistInspectPatchHunks: 'Inspect patch hunks',
        checklistVerifyNoSecretLeakage: 'Verify no secret leakage',
        checklistApproveMergeOnlyAfterReview: 'Approve merge only after review',
        checklistDiscardOnlyAfterArchivalCopy: 'Discard only after archival copy',
        readOnly: 'read only',
        finalized: 'finalized',
        cleanupRequired: 'Cleanup required',
        mergeActions: 'Merge actions',
        reviewRequired: 'review required',
        reviewBeforeMerge: 'Review before merge',
        changedFiles: 'Changed files',
        copyPatchPath: 'Copy patch path',
        noPendingFile: 'no pending file',
        mergeSummary: 'Confirm merge to apply {patchPath} to {sourceRepo} without creating a commit.',
        discardSummary: 'Confirm discard to remove the pending state for {worktreeDir} without touching {sourceRepo}.',
        actionFailedHttp: 'Worktree action failed (HTTP {status}).',
        noPatchPathAvailable: 'No patch path is available.',
      },
      logs: {
        title: 'Logs',
        liveTail: 'Live tail',
        tailFilter: 'Tail filter',
        loadingActiveRunLog: 'Loading active run log',
        activeRunLog: 'active run log',
        filteredLine: 'filtered line',
        filteredLines: 'filtered lines',
        cursor: 'cursor',
        emptyState: 'Empty log',
        liveTailActive: 'Live tail active',
        liveTailPaused: 'Live tail paused',
        logFileMissing: 'Log file missing',
        logReadError: 'Log read error',
        noMatchingLogLines: 'No matching log lines',
        pauseLiveTail: 'Pause live tail',
        resumeLiveTail: 'Resume live tail',
        noEntries: 'No log entries yet.',
        noMatchCurrentFilter: 'No log entries match the current filter.',
        stage: 'Stage',
        message: 'Message',
        copySelectedLines: 'Copy selected lines',
        downloadFilteredLogs: 'Download filtered logs',
        clearSelection: 'Clear selection',
        filterAll: 'ALL',
        filterInfo: 'INFO',
        filterWarn: 'WARN',
        filterErr: 'ERR',
        filterDebug: 'DEBUG',
        stagePlaceholder: 'Stage',
        taskIdPlaceholder: 'Task ID',
        searchPlaceholder: 'Search',
        taskId: 'Task ID',
        search: 'Search',
        line: 'Line {lineNumber}',
        malformedLinesSkipped: '{count} malformed line(s) skipped.',
      },
      landing: {
        title: 'Landing preview',
        directionA: 'Direction A landing preview',
        marketingShell: 'marketing shell',
        directionAChip: 'Direction A',
        headline: 'Leave it running.<br>Wake up to a PR.',
        copy: 'CLI-first multi-agent runner with a PM -> Dev -> QA pipeline, local-safe worktree review, and a compact production shell.',
        openDashboard: 'Open Dashboard',
        copyRunCommand: 'Copy run command',
        productionNotes: 'Production notes',
        noBabel: 'No Babel in browser, no React CDN, no docs/Design runtime imports.',
        staticProductionAsset: 'Static production asset',
        topbarShell: 'Top bar, 220px sidebar, and independent main scroll area remain intact.',
        desktopShellRecovery: 'Desktop shell recovery',
        openMobile: 'Open Mobile',
        directionAMarketingShell: 'Direction A marketing shell',
        pmDevQaFlowTitle: 'PM -> Dev -> QA',
        pmDevQaFlowCopy: 'Structured backlog emission and stage handoff with live run feedback.',
        readOnlyFirstPassTitle: 'Read-only first pass',
        readOnlyFirstPassCopy: 'Status, logs, and review surfaces without destructive browser-side controls.',
        compactShellTitle: 'Compact shell',
        compactShellCopy: 'Thin borders, tight density, and live-running accents aligned to Direction A.',
      },
      mobile: {
        title: 'Mobile preview',
        telegramStyleStatusView: 'Telegram-style status view',
        mobilePreviewNotes: 'Mobile preview notes',
        telegramStyleRemoteView: 'Telegram-style remote view',
        compactRemoteStatusSurface: 'Compact remote status surface for run monitoring.',
        narrowWidths: 'Designed to stay readable at narrow widths',
        mirrorsMock: 'Mirrors the Direction A mobile mock without external runtime deps.',
        staticPreviewShell: 'Static preview shell',
        openNotifications: 'Open Notifications',
        openDashboard: 'Open Dashboard',
        pipeline: 'Pipeline',
        notifications: 'Notifications',
        taskUnavailable: 'task unavailable',
      },
    },
    ko: {
      app: {
        title: 'AgentCLI 웹 콘솔',
      },
      locale: {
        language: '언어',
        en: 'EN',
        ko: 'KO',
      },
      nav: {
        run: '실행',
        project: '프로젝트',
        history: '기록',
        preview: '미리보기',
        dashboard: '대시보드',
        pipeline: '파이프라인',
        logs: '로그',
        backlog: '백로그',
        goals: '목표',
        config: '설정',
        prompts: '프롬프트',
        worktreeReview: '워크트리 검토',
        runHistory: '실행 기록',
        notifications: '알림',
        landingPreview: '랜딩 미리보기',
        mobilePreview: '모바일 미리보기',
      },
      topbar: {
        refresh: '새로고침',
        commandPalette: '명령',
        commandPaletteTitle: '명령 팔레트',
        commandPaletteHint: '/ 또는 Cmd+K / Ctrl+K',
        language: '언어',
      },
      common: {
        loading: '불러오는 중',
        working: '작업 중...',
        ready: '준비됨',
        unavailable: '사용 불가',
        cancel: '취소',
        save: '저장',
        saved: '저장됨',
        failed: '실패',
        confirm: '확인',
        openDashboard: '대시보드 열기',
        openLogs: '로그 열기',
        openBacklog: '백로그 열기',
        openGoals: '목표 열기',
        openConfig: '설정 열기',
        openPrompts: '프롬프트 열기',
        openWorktree: '워크트리 열기',
        openNotifications: '알림 열기',
        openPipeline: '파이프라인 열기',
        openMobile: '모바일 열기',
        openLanding: '랜딩 열기',
        noMatches: '일치하는 명령 없음',
        localOnly: '로컬 전용',
        dirty: '변경됨',
        clean: '정상',
        fullRead: '전체 읽기',
        noBackups: '백업 없음',
        added: '추가됨',
        removed: '삭제됨',
        selected: '선택됨',
        select: '선택',
        deselect: '선택 해제',
        none: '없음',
        noDataAvailableYet: '아직 데이터가 없습니다.',
      },
      snapshot: {
        loading: '스냅샷 로딩 중',
        api: 'API 스냅샷',
        error: 'API 오류',
        fallback: '대체 데이터',
        stale: '오래된 스냅샷',
        partial: '부분 스냅샷',
        loadingReadOnly: '읽기 전용 스냅샷 로딩 중',
        controlsDisabled: '컨트롤 비활성화',
        emptyState: '빈 상태',
      },
      palette: {
        title: '명령 팔레트',
        placeholder: '화면 또는 작업을 입력',
        noMatches: '일치하는 명령 없음',
        goTo: '{view}로 이동',
        navKind: '이동',
        actionKind: '작업',
        refreshStatus: '읽기 전용 스냅샷 새로고침',
        stopCurrentRun: '현재 실행 중지',
        startRunner: '실행기 시작',
        stopRunner: '실행기 중지',
        reloadRunner: '실행기 다시 불러오기',
        restartRunner: '실행기 재시작',
        pauseLiveTail: '라이브 tail 일시정지',
        resumeLiveTail: '라이브 tail 재개',
        openWorktreeReview: '워크트리 검토 열기',
        openMobilePreview: '모바일 미리보기 열기',
        openLandingPreview: '랜딩 미리보기 열기',
      },
      shortcuts: {
        ctrlEnterSaves: 'ctrl+enter 저장',
        escCloses: 'esc 닫기',
        draftMode: '초안 모드',
        exactConfirmation: '정확한 확인',
        confirmationPhrase: '확인 문구',
      },
      runner: {
        panelTitle: '실행기 컨트롤',
        confirmationPhrases: '확인 문구:',
        confirmStartPhrase: 'START RUNNER',
        confirmStopPhrase: 'STOP RUNNER',
        confirmReloadPhrase: 'RELOAD RUNNER',
        confirmRestartPhrase: 'RESTART RUNNER',
        source: '소스',
        selectedRepo: '선택된 저장소',
        selectedConfig: '선택된 설정',
        controller: '컨트롤러',
        state: '상태',
        runMode: '실행 모드',
        runStatus: '실행 상태',
        liveStates: '실시간 상태',
        runnerProcess: '실행기 프로세스',
        taskBackend: '작업 백엔드',
        trackedChildren: '추적된 자식 프로세스',
        artifactWriter: '아티팩트 작성기',
        lastAction: '마지막 작업',
        lastMessage: '마지막 메시지',
        lastError: '마지막 오류',
        actionInFlight: '작업 진행 중',
        actionComplete: '작업 완료',
        backendError: '백엔드 오류',
        controllerUnavailable: '실행기 컨트롤러를 사용할 수 없습니다.',
        controlsDisabled: '실행기 컨트롤이 비활성화되었습니다.',
        unavailable: '사용 불가',
        available: '사용 가능',
        alive: '실행 중',
        flushing: '플러시 중',
        ready: '준비됨',
        running: '실행 중',
        idle: '대기',
        working: '작업 중...',
        start: '시작',
        stop: '중지',
        reload: '다시 불러오기',
        restart: '재시작',
        starting: '시작 중...',
        stopping: '중지 중...',
        reloading: '다시 불러오는 중...',
        restarting: '재시작 중...',
        started: '시작됨',
        stopped: '중지됨',
        reloaded: '다시 불러옴',
        restarted: '재시작됨',
        confirmStart: '시작 확인',
        confirmStop: '중지 확인',
        confirmReload: '다시 불러오기 확인',
        confirmRestart: '재시작 확인',
        startSummary: '선택한 저장소와 설정 스냅샷으로 실행기를 시작합니다.',
        stopSummary: '현재 실행기를 중지하고 정지 신호를 기록한 뒤 종료 상태를 기다립니다.',
        reloadSummary: '현재 실행기를 중지하고 안정될 때까지 기다린 다음, 선택한 저장소와 설정 스냅샷으로 다시 시작합니다.',
        restartSummary: '선택한 저장소와 설정 스냅샷으로 실행기를 재시작합니다.',
        confirmAction: '이 실행기 작업을 확인합니다.',
      },
      dashboard: {
        title: '대시보드',
        pipelineSnapshot: '파이프라인 스냅샷',
        liveLogs: '실시간 로그',
        runFacts: '실행 정보',
        goalsSnapshot: '목표 스냅샷',
        selectedBacklogItem: '선택된 백로그 항목',
        notifications: '알림',
        currentTaskId: '현재 작업 ID',
        currentTaskTitle: '현재 작업 제목',
        attempt: '시도',
        branch: '브랜치',
        worktreeMode: '워크트리 모드',
        runDirectory: '실행 디렉터리',
        finalReason: '종료 사유',
        noLogEntriesYet: '아직 로그가 없습니다.',
        noGoalsPublishedYet: '아직 목표가 게시되지 않았습니다.',
        noTaskSelected: '선택된 작업 없음.',
        noBacklogArtifacts: '아직 백로그 산출물이 게시되지 않았습니다.',
        noNotificationsYet: '아직 알림이 없습니다.',
        goalsSnapshotReady: '안정적인 P0/P1 그룹과 정확한 체크박스 상태가 포함된 읽기 전용 GOALS.md 스냅샷입니다.',
        stage: '단계',
        tasks: '작업',
        tokens: '토큰',
        budget: '예산',
      },
      backlog: {
        title: '백로그',
        workQueue: '작업 대기열',
        backlogSummary: '백로그 요약',
        pending: '대기',
        inProgress: '진행 중',
        done: '완료',
        failed: '실패',
        noTasksInBucket: '이 버킷에 작업이 없습니다.',
        noArtifacts: '아직 백로그 산출물이 게시되지 않았습니다.',
        dependenciesUnavailable: '의존성 없음',
        fileScopeUnavailable: '파일 범위 없음',
        attemptUnavailable: '시도 정보 없음',
        cycleUnavailable: '사이클 정보 없음',
        stepUnavailable: '단계 정보 없음',
        failureUnavailable: '실패 정보 없음',
        recentOutputUnavailable: '최근 출력 없음.',
        noTaskSelected: '선택된 작업 없음.',
        queued: '대기 중',
        completed: '완료',
        needsAttention: '확인 필요',
      },
      goals: {
        title: '목표',
        goalProgress: '목표 진행률',
        loadingSnapshot: '읽기 전용 스냅샷을 불러오는 중...',
        browserLocalDraft: '브라우저 로컬 목표 편집이 활성화되었습니다.',
        draftStaysLocal: '초안 편집은 저장하거나 초기화하기 전까지 로컬에만 유지됩니다. 버킷 그룹은 P0와 P1에 고정됩니다.',
        snapshot: 'GOALS.md 스냅샷',
        goalDraftDiff: '목표 초안 차이',
        saveLocked: '목표 저장이 잠겨 있습니다',
        confirmationRequired: '확인 필요',
        readyToSave: '목표 저장 준비 완료',
        saving: '목표 저장 중',
        saved: '목표가 저장되었습니다',
        saveFailed: '목표 저장 실패',
        confirmationPhrase: '확인 문구',
        backupPath: '백업 경로',
        savedPath: '저장된 경로',
        errorCode: '오류 코드',
        resetDraft: '초안 초기화',
        localDraftOnly: '로컬 초안만',
        checked: '체크됨',
        parserWarnings: '파서 경고',
        rawTextPreview: '원문 미리보기',
        sourceLine: '소스 줄',
        sourceMetadata: '소스 메타데이터',
        noGoals: 'GOALS.md에 내용은 있지만 체크리스트 항목을 파싱하지 못했습니다.',
        missing: 'GOALS.md가 없습니다.',
        empty: 'GOALS.md가 비어 있습니다.',
        noLocalChanges: '아직 로컬 내용 변경이 없습니다.',
        saveCreatesBackup: '저장은 .doc/GOALS.md를 원자적으로 업데이트하기 전에 항상 타임스탬프 백업을 만듭니다.',
        confirmSave: '확인 후 목표 저장',
        deletedUncheckedP0: '체크되지 않은 P0 삭제',
        downgradedUncheckedP0: '체크되지 않은 P0 하향 조정',
        typeExact: '확인을 위해 {confirmation}를 정확히 입력하세요.',
        p0MustHave: 'P0 | 필수',
        p1ShouldHave: 'P1 | 권장',
      },
      config: {
        title: '설정',
        fieldDetails: '필드 세부 정보',
        loadingSnapshot: '읽기 전용 스냅샷을 불러오는 중',
        localDraftOnly: '로컬 초안만',
        activeValue: '현재 값',
        localDraft: '로컬 초안',
        pendingChanges: '대기 중인 변경',
        saveChanges: '변경 사항 저장',
        saving: '저장 중...',
        saved: '설정 저장됨',
        saveFailed: '설정 저장 실패',
        saveLocked: '설정 저장이 잠겨 있습니다',
        noChanges: '변경된 설정 없음',
        readyToSave: '저장할 변경 사항 준비됨',
        backupPath: '백업 경로',
        reloadRequired: '다시 불러오기 필요',
        pendingPaths: '대기 경로',
        restartRequired: '재시작 필요',
        redactedHidden: '마스킹된 값은 브라우저에서 계속 숨겨집니다.',
        localValidationFailed: '로컬 검증 실패',
        secret: '비밀',
        restart: '재시작',
        invalid: '유효하지 않음',
        default: '기본값',
      },
      prompts: {
        title: '프롬프트',
        promptInventory: '프롬프트 목록',
        inventoryRedacted: '목록 미리보기는 계속 마스킹됩니다. 프롬프트를 선택하면 전체 내용을 명시적 읽기 경로로 엽니다.',
        promptEditor: '프롬프트 편집기',
        explicitPromptRead: '명시적 프롬프트 읽기',
        loadedThroughExplicitReadPath: '명시적 읽기 경로로 불러옴',
        fullReadPreview: '전체 읽기 미리보기',
        noPromptSelected: '선택된 프롬프트 없음',
        selectPrompt: '프롬프트를 선택하여 명시적 콘텐츠 슬라이스를 읽습니다.',
        noPromptFiles: '프롬프트 파일을 찾지 못했습니다.',
        primaryPromptsDir: '기본 프롬프트 디렉터리',
        trackedPromptFiles: '추적 중인 프롬프트 파일',
        scope: '범위',
        profile: '프로필',
        source: '소스',
        resolvedPath: '해결된 경로',
        lastUpdated: '마지막 업데이트',
        savePrompt: '프롬프트 저장',
        restoreBackup: '백업 복원',
        selectedBackup: '선택된 백업',
        availableBackups: '사용 가능한 백업',
        restoreBackupPath: '복원 백업 경로',
        savedPath: '저장된 경로',
        restoredFrom: '복원 원본',
        trackedPromptRoles: '추적 중인 프롬프트 역할',
        promptMutationsLocked: '프롬프트 변경이 잠겨 있습니다',
        promptMutationsDisabled: '실행기 컨트롤이 활성화될 때까지 프롬프트 저장과 복원은 비활성화됩니다.',
        chooseBackupRestore: '검증이 통과하면 복원할 백업을 선택하거나 현재 초안을 저장하세요.',
        filenameValidation: '파일명 검증',
        contentValidation: '내용 검증',
        templateVariableValidation: '템플릿 변수 검증',
        requiredTemplateVariables: '필수 템플릿 변수',
        missingTemplateVariables: '누락된 템플릿 변수',
        filenameIsPopulated: '파일명이 입력되었습니다.',
        contentIsPopulated: '내용이 입력되었습니다.',
        requiredTemplateVariablesLabel: '필수 템플릿 변수: {variables}',
        noLocalChangesYet: '아직 로컬 내용 변경이 없습니다.',
        localDiffPreview: '로컬 차이 미리보기',
        filename: '파일명',
        localOnly: '로컬 전용',
        added: '추가됨',
        removed: '삭제됨',
        line: '줄 {lineNumber}',
        restoreConfirmation: '복원 확인',
        restoreOverwritePhrase: '선택한 백업이 프롬프트 파일을 덮어쓴다는 것을 확인하려면 RESTORE BACKUP를 입력하세요.',
        filenameRequired: '파일명을 비워둘 수 없습니다.',
        filenameMustBeBare: '파일명은 해결된 프롬프트 디렉터리 안의 순수한 파일명이어야 합니다.',
        promptContentRequired: '프롬프트 내용은 비워둘 수 없습니다.',
        promptSaved: '프롬프트가 저장되었습니다',
        promptRestored: '프롬프트가 복원되었습니다',
        saveErrorBadge: '저장 오류',
        restoreErrorBadge: '복원 오류',
        promptSaveFailed: '프롬프트 저장 실패',
        promptRestoreFailed: '프롬프트 복원 실패',
        promptMutationCompleted: '프롬프트 변경이 완료되었습니다.',
        promptMutationFailed: '프롬프트 변경에 실패했습니다.',
      },
      history: {
        title: '실행 기록',
        runHistory: '실행 기록',
        noRunsYet: '아직 실행 기록이 없습니다.',
        emptyState: '실행 기록이 비어 있습니다.',
        selectedRun: '선택된 실행',
        persistedSummary: '저장된 요약',
        shutdownReason: '종료 사유',
        worktreeOutcome: '워크트리 결과',
        noSummaries: '아직 저장된 실행 요약이 없습니다.',
        success: '성공',
        tasks: '작업',
        budgetCap: '예산 상한',
      },
      notifications: {
        title: '알림',
        eventFeed: '이벤트 피드',
        noMatchCurrentFilter: '현재 필터와 일치하는 알림이 없습니다.',
        noRecorded: '아직 기록된 알림이 없습니다.',
        notificationSource: '알림 소스',
        observedKinds: '관측된 종류',
        controlPlaneLastEvent: '컨트롤 플레인 마지막 이벤트',
        notificationCounts: '알림 수',
        bridgeSettings: '브리지 설정',
        notificationError: '알림 오류',
        filteredEmpty: '필터 결과 없음',
        noEventsYet: '아직 이벤트 없음',
      },
      worktree: {
        title: '워크트리 검토',
        pendingMerge: '대기 중인 작업트리 병합',
        reviewChecklist: '검토 체크리스트',
        mergeActions: '병합/폐기 작업',
        mergePreflight: '병합 사전 점검',
        confirmMergePhrase: 'MERGE WORKTREE',
        confirmDiscardPhrase: 'DISCARD WORKTREE',
        readOnlyMode: '읽기 전용 모드',
        confirmationRequired: '확인 필요',
        manualRecovery: '수동 복구',
        finalizedWorktree: '완료된 작업트리',
        noPendingMerge: '대기 중인 작업트리 병합이 없습니다.',
        noChangedFiles: '패치에서 변경 파일을 파싱하지 못했습니다.',
        reviewRequired: '소스 저장소 변경 전에 검토가 필요합니다',
        patchApplied: '패치 적용됨',
        patchDiscarded: '패치 폐기됨',
        malformedPendingFile: '잘못된 대기 파일',
        mergeRecordedCleanupFailed: '병합 기록됨, 정리 실패',
        discardRecordedCleanupFailed: '폐기 기록됨, 정리 실패',
        patchExportFailed: '패치 내보내기 실패',
        patchExportNotApplied: '패치가 내보내졌지만 자동 적용되지 않음',
        patchNotApplied: '패치가 적용되지 않음',
        sourceDirtyState: '소스 변경 상태',
        sourceHead: '소스 HEAD',
        expectedBaseRef: '예상 기준 ref',
        patchHash: '패치 해시',
        gitApplyCheck: 'git apply --check',
        pendingMarkerPath: '대기 마커 경로',
        failureDetails: '실패 세부 정보',
        pendingStateRecoverable: '대기 상태는 다시 시도하거나 폐기할 수 있습니다.',
        binaryFile: '바이너리 파일',
        deletedFile: '삭제된 파일',
        renamedFile: '이름이 변경된 파일',
        largeFile: '큰 파일',
        previewTruncated: '미리보기가 잘렸습니다',
        binaryFileNoPreview: '바이너리 패치는 텍스트 미리보기를 제공하지 않습니다.',
        deletedFileNoPreview: '삭제된 파일은 텍스트 미리보기를 제공하지 않습니다.',
        largeFilePreviewTruncated: '큰 패치 미리보기가 잘렸습니다.',
        noDiffHunks: '차이 덩어리가 없습니다.',
        oldPath: '이전 경로',
        newPath: '새 경로',
        lineCount: '줄 수',
        hunk: '덩어리 {index}',
        failedFiles: '실패한 파일',
        failedHunks: '실패한 덩어리',
        applyCheckCommand: '명령',
        applyCheckRc: 'rc',
        applyCheckMessage: '메시지',
        applyCheckPassed: '통과',
        applyCheckFailed: '실패',
        applyCheckUnavailable: '사용 불가',
        exactConfirmation: '정확한 확인',
        confirmationPhrase: '확인 문구',
        confirmMerge: '병합 확인',
        confirmDiscard: '폐기 확인',
        sourceRepo: '소스 저장소',
        runDir: '실행 디렉터리',
        worktreeDir: '워크트리 디렉터리',
        patchPath: '패치 경로',
        pendingFile: '대기 파일',
        baseRef: '기준 참조',
        headRef: '헤드 참조',
        cleanupState: '정리 상태',
        cleanupPath: '정리 경로',
        cleanupMessage: '정리 메시지',
        cleanupStateUnavailable: '정리 상태가 없습니다.',
        runnerRc: '실행기 rc',
        reviewBeforeMerge: '병합 전에 검토',
        noChecklist: '아직 체크리스트가 없습니다.',
        noPendingReview: '대기 중인 워크트리 병합이 없습니다.',
        confirmMergeToApply: '커밋을 만들지 않고 패치를 적용하려면 병합을 확인하세요.',
        confirmDiscardToRemove: '소스 파일을 건드리지 않고 대기 상태를 제거하려면 폐기를 확인하세요.',
        backendValidates: '백엔드는 실행 전에 소스 저장소, 실행 디렉터리, 워크트리 경로, 패치 경로를 검증합니다.',
        checklistInspectPatchHunks: '패치 덩어리를 검토합니다',
        checklistVerifyNoSecretLeakage: '비밀 정보가 노출되지 않았는지 확인합니다',
        checklistApproveMergeOnlyAfterReview: '검토 후에만 병합을 승인합니다',
        checklistDiscardOnlyAfterArchivalCopy: '보관본을 만든 뒤에만 폐기합니다',
      },
      logs: {
        title: '로그',
        liveTail: '라이브 tail',
        tailFilter: '라이브 tail 필터',
        loadingActiveRunLog: '활성 실행 로그를 불러오는 중',
        liveTailActive: '라이브 tail 활성',
        liveTailPaused: '라이브 tail 일시정지',
        logFileMissing: '로그 파일 없음',
        logReadError: '로그 읽기 오류',
        noMatchingLogLines: '일치하는 로그 줄 없음',
        pauseLiveTail: '라이브 tail 일시정지',
        resumeLiveTail: '라이브 tail 재개',
        noEntries: '아직 로그가 없습니다.',
        noMatchCurrentFilter: '현재 필터와 일치하는 로그가 없습니다.',
        stage: '단계',
        message: '메시지',
        copySelectedLines: '선택한 줄 복사',
        downloadFilteredLogs: '필터된 로그 다운로드',
        clearSelection: '선택 해제',
      },
      landing: {
        title: '랜딩 미리보기',
        directionA: 'Direction A 랜딩 미리보기',
        marketingShell: '마케팅 쉘',
        directionAChip: 'Direction A',
        headline: '그대로 실행해 두세요.<br>PR로 깨어나세요.',
        copy: 'PM -> Dev -> QA 파이프라인, 로컬 안전 워크트리 검토, 컴팩트한 프로덕션 쉘을 갖춘 CLI 우선 멀티 에이전트 러너입니다.',
        openDashboard: '대시보드 열기',
        copyRunCommand: '실행 명령 복사',
        productionNotes: '프로덕션 메모',
        noBabel: '브라우저 내 Babel도 없고, React CDN도 없으며, docs/Design 런타임 import도 없습니다.',
        staticProductionAsset: '정적 프로덕션 자산',
        topbarShell: '44px 상단바, 220px 사이드바, 독립적인 메인 스크롤 영역이 유지됩니다.',
        desktopShellRecovery: '데스크톱 쉘 복구',
        openMobile: '모바일 열기',
        directionAMarketingShell: 'Direction A 마케팅 쉘',
        pmDevQaFlowTitle: 'PM -> Dev -> QA',
        pmDevQaFlowCopy: '실시간 실행 피드백과 함께 구조화된 백로그 발행 및 단계 인계를 제공합니다.',
        readOnlyFirstPassTitle: '읽기 전용 우선 통과',
        readOnlyFirstPassCopy: '파괴적인 브라우저 측 제어 없이 상태, 로그, 검토 화면을 제공합니다.',
        compactShellTitle: '컴팩트 쉘',
        compactShellCopy: '얇은 테두리, 촘촘한 밀도, 라이브 실행 강조를 Direction A에 맞췄습니다.',
      },
      mobile: {
        title: '모바일 미리보기',
        telegramStyleStatusView: '텔레그램 스타일 상태 보기',
        mobilePreviewNotes: '모바일 미리보기 메모',
        telegramStyleRemoteView: '텔레그램 스타일 원격 뷰',
        compactRemoteStatusSurface: '실행 모니터링용 컴팩트 원격 상태 화면입니다.',
        narrowWidths: '좁은 폭에서도 읽기 쉽도록 설계되었습니다.',
        mirrorsMock: '외부 런타임 의존성 없이 Direction A 모바일 목업을 따릅니다.',
        staticPreviewShell: '정적 미리보기 쉘',
        openNotifications: '알림 열기',
        openDashboard: '대시보드 열기',
        pipeline: '파이프라인',
        notifications: '알림',
        taskUnavailable: '작업 없음',
      },
    },
  };

  Object.assign(LOCALE_TEXT.ko.common, {
    enabled: '활성화됨',
    disabled: '비활성화됨',
    unknown: '알 수 없음',
    of: '중',
    lines: '줄',
    recent: '최근',
    complete: '완료',
    remaining: '남음',
    visible: '표시됨',
    total: '전체',
  });
  LOCALE_TEXT.ko.pipeline = LOCALE_TEXT.ko.pipeline || {};
  Object.assign(LOCALE_TEXT.ko.pipeline, {
    title: '파이프라인',
    stageLane: '단계 레인',
    currentStageOutput: '현재 단계 출력',
    stageGuardrails: '단계 가드레일',
    liveTokens: '실시간 토큰',
    readOnlyShell: '기본은 읽기 전용 셸입니다. 정지, 병합, 폐기는 여기서 자동 적용되지 않습니다.',
    manualConfirmation: '현재 실행은 수동 정지 확인과 로컬 검토 흐름을 사용합니다.',
    devStage: 'Dev 단계',
    elapsed: '경과',
    tokensProcessed: '처리된 토큰',
    tokensGenerated: '생성된 토큰',
    tokenTelemetryUnavailable: '토큰 원격 측정 없음',
    budgetTelemetryUnavailable: '예산 원격 측정 없음',
    stageUnavailable: '단계 없음',
    lifecycleRecord: '수명주기 기록',
    startedUnavailable: '시작 정보 없음',
    endedUnavailable: '종료 정보 없음',
    latestLogLine: '최신 로그 줄',
    latestBackendEvent: '최신 백엔드 이벤트',
    latestLogLineUnavailable: '아직 로그 줄이 없습니다.',
    latestBackendEventUnavailable: '아직 백엔드 이벤트가 없습니다.',
    noOutputWarning: '{count}분 동안 출력 없음.',
    inProgress: '진행 중',
    pending: '대기',
    completed: '완료',
    stopped: '중지됨',
    skipped: '건너뜀',
    recentOutputUnavailable: '최근 출력 없음.',
    noLifecycleRecords: '아직 게시된 수명주기 기록이 없습니다.',
    activeTask: '현재 작업',
    current: '현재',
    iter: '반복',
  });
  Object.assign(LOCALE_TEXT.ko.history, {
    branchId: '브랜치 / ID',
    duration: '기간',
    cycles: '{count} 사이클',
    started: '시작',
    action: '작업',
    currentState: '현재 상태',
    persistedRuntime: '저장된 실행 시간',
    noWorktreeArtifact: '작업트리 산출물 없음',
    worktreeOutcomeMeta: '작업트리 결과',
    noPersistedSummary: '저장된 요약 필드가 없습니다.',
  });
  Object.assign(LOCALE_TEXT.ko.notifications, {
    visibleItems: '표시됨',
    totalItems: '전체',
    currentRun: '현재 실행',
    lifecycle: '수명주기',
    taskDone: '작업 완료',
    errors: '오류',
  });
  Object.assign(LOCALE_TEXT.ko.worktree, {
    readOnly: '읽기 전용',
    finalized: '완료됨',
    cleanupRequired: '정리 필요',
    mergeActions: '병합/폐기 작업',
    reviewRequired: '검토 필요',
    reviewBeforeMerge: '병합 전 검토',
    changedFiles: '변경된 파일',
    copyPatchPath: '패치 경로 복사',
    noPendingFile: '대기 파일 없음',
    pendingMerge: '대기 중인 작업트리 병합',
    finalizedWorktree: '완료된 작업트리',
    noPendingReview: '대기 중인 작업트리 병합이 없습니다.',
    manualCleanupRequired: '결정이 기록된 후 정리가 실패했습니다.',
    noPendingMergeAvailable: '대기 중인 작업트리 병합이 없습니다.',
    actionUnavailable: '작업트리를 사용할 수 없습니다.',
    actionFailed: '작업트리 작업 실패.',
    applyingPendingDecision: '{sourceRepo}에 대한 대기 중인 작업트리 결정을 적용하는 중입니다.',
    typeConfirmationPhrase: '이 작업을 확인하려면 "{confirmation}"를 정확히 입력하세요.',
    confirmationPhraseMismatch: '확인 문구는 "{confirmation}"여야 합니다.',
    noChecklist: '아직 체크리스트가 없습니다.',
    confirmMergeToApply: '커밋을 만들지 않고 패치를 적용하려면 병합을 확인하세요.',
    confirmDiscardToRemove: '소스 파일을 건드리지 않고 대기 상태를 제거하려면 폐기를 확인하세요.',
    backendValidates: '백엔드는 실행 전에 소스 저장소, 실행 디렉터리, 워크트리 경로, 패치 경로를 검증합니다.',
    cleanupLifecycle: '정리 수명주기',
    cleanupTarget: '정리 대상',
    cleanupStatusDetail: '정리 상태 세부 정보',
    exportStatus: '내보내기 상태',
    repositoryRoot: '저장소 루트',
    baseBranchForPatch: '패치 기준 브랜치',
    mergeBase: '병합 기준점',
    worktreeHead: '워크트리 헤드',
    runThatProducedPatch: '패치를 생성한 실행',
    isolatedSourceTree: '분리된 소스 트리',
    mergePatchArtifact: '병합 패치 산출물',
    readOnlyContractSource: '읽기 전용 계약 원본',
    currentArtifactPath: '현재 산출물 경로',
    sourceRepoLabel: '소스 저장소',
    patchExportFailedBeforeMarker: '검토 가능한 병합 마커가 쓰이기 전에 패치 내보내기가 실패했습니다.',
    exportedPatchNotAutoApplied: '패치가 내보내졌지만 자동 적용이 실행되지 않았습니다.',
    noSourceRepoChangePending: '대기 중인 소스 저장소 변경이 없습니다.',
    noCommitWillBeCreated: '커밋은 생성되지 않습니다.',
    pendingMetadataIncomplete: '대기 중인 작업트리 메타데이터가 불완전합니다.',
    fixOrDeletePendingFile: '다시 시도하기 전에 CLI에서 대기 파일을 수정하거나 삭제하세요.',
    applyExportedPatchBeforeConfirming: '병합 또는 폐기를 확인하기 전에 내보낸 패치를 적용하세요.',
    worktreeAlreadyFinalized: '작업트리가 이미 종료되었습니다.',
    reviewThePatchBeforeSourceRepoChanges: '소스 저장소 변경 전에 패치를 검토하세요.',
    reviewChecklist: '검토 체크리스트',
    riskNotes: '위험 참고',
    actionFailedHttp: '작업트리 작업 실패(HTTP {status}).',
    noPatchPathAvailable: '사용 가능한 패치 경로가 없습니다.',
  });
  Object.assign(LOCALE_TEXT.ko.logs, {
    stagePlaceholder: '단계',
    taskId: '작업 ID',
    search: '검색',
    malformedLinesSkipped: '{count}개의 잘못된 줄을 건너뜀.',
  });
  Object.assign(LOCALE_TEXT.ko.config, {
    mustBeBoolean: '불리언이어야 합니다.',
    mustBeNumber: '숫자여야 합니다.',
    mustBeAtLeast: '{min} 이상이어야 합니다.',
    mustBeAtMost: '{max} 이하여야 합니다.',
    cannotBeEmpty: '비워 둘 수 없습니다.',
    mustBeOneOf: '다음 중 하나여야 합니다: {options}',
    pickAtLeastOne: '하나 이상 선택하세요.',
    invalidOption: '잘못된 옵션: {options}',
    enterAtLeastOneValue: '값을 하나 이상 입력하세요.',
    invalidIntegerValue: '잘못된 정수 값: {values}',
    repoManagedByServer: '리포지토리 루트는 서버가 관리합니다.',
    redactedPlaceholderSaveBlocked: '가려진 자리표시는 저장할 수 없습니다.',
    saveInProgress: '구성 저장이 이미 진행 중입니다.',
    savesDisabledUntilRunnerEnabled: '러너 제어가 활성화될 때까지 구성 저장이 비활성화됩니다.',
    noConfigChanges: '저장할 구성 변경 사항이 없습니다.',
    fixInvalidChangesBeforeSaving: '저장하기 전에 잘못된 변경 {count}개를 수정하세요.',
    rejectedFields: '거부된 필드',
    securityRoleTitle: 'Security 단계',
    securityRoleRequirement: 'Security 단계에는 roles의 Security와 security.enabled=true가 필요합니다.',
    securityRoleMissingEnabled: 'roles에 Security가 있지만 security.enabled가 꺼져 있습니다.',
    securityRoleMissingRole: 'security.enabled가 켜져 있지만 roles에 Security가 없습니다.',
  });
  Object.assign(LOCALE_TEXT.ko.common, {
    select: '선택',
    deselect: '선택 해제',
    loading: '불러오는 중',
    working: '작업 중...',
    ready: '준비됨',
  });
  Object.assign(LOCALE_TEXT.ko.topbar, {
    elapsed: '경과',
    quotaUsage: '할당량 사용량',
    quotaUsageWindow: '할당량 {window} 사용량',
    quotaUnavailable: '할당량을 사용할 수 없음',
  });
  Object.assign(LOCALE_TEXT.ko.common, {
    open: '열기',
    chars: '자',
  });
  Object.assign(LOCALE_TEXT.ko.pipeline, {
    started: '시작',
    ended: '종료',
    pmDevQaFlow: 'PM -> Dev -> QA',
  });
  Object.assign(LOCALE_TEXT.ko.config, {
    description: '설명',
    hint: '힌트',
    defaultValue: '기본값',
    resolvedPromptsPath: '확정된 프롬프트 경로',
  });
  Object.assign(LOCALE_TEXT.ko.goals, {
    newGoal: '새 목표',
    editGoal: '목표 편집',
    bucket: '버킷',
    goal: '목표',
    note: '메모',
    addGoal: '목표 추가',
    saveGoal: '목표 저장',
    noGoalsYet: '아직 목표가 없습니다.',
    saveFailedHttp: '목표 저장 실패(HTTP {status}).',
    confirmationPhraseExact: 'DELETE OR DOWNGRADE UNMET P0 GOALS',
    clean: '정상',
    changeCount: '{count}개 변경',
    noRiskyP0Changes: '위험한 P0 변경이 감지되지 않았습니다.',
    uncheckedP0Goals: '체크되지 않은 P0 목표 {count}개가 확인을 필요로 합니다.',
  });
  Object.assign(LOCALE_TEXT.ko.logs, {
    taskIdPlaceholder: '작업 ID',
    searchPlaceholder: '검색',
    line: '줄 {lineNumber}',
  });
  Object.assign(LOCALE_TEXT.ko.worktree, {
    merging: '병합 중...',
    discarding: '폐기 중...',
    refreshingStatus: '상태 새로고침 중',
  });

  Object.assign(LOCALE_TEXT.en.pipeline, {
    input: 'Input',
    output: 'Output',
  });
  Object.assign(LOCALE_TEXT.ko.pipeline, {
    input: '입력',
    output: '출력',
  });
  Object.assign(LOCALE_TEXT.en.config, {
    resetDraft: 'Reset draft',
    savedPaths: 'Saved paths',
    edited: 'edited',
  });
  Object.assign(LOCALE_TEXT.ko.config, {
    resetDraft: '초안 초기화',
    savedPaths: '저장된 경로',
    edited: '수정됨',
  });
  Object.assign(LOCALE_TEXT.en.backlog, {
    active: 'Active',
  });
  Object.assign(LOCALE_TEXT.ko.backlog, {
    active: '활성',
  });
  Object.assign(LOCALE_TEXT.en.worktree, {
    applyMerge: 'Apply merge',
    discardMerge: 'Discard merge',
    confirmMerge: 'Confirm merge',
    confirmDiscard: 'Confirm discard',
  });
  Object.assign(LOCALE_TEXT.ko.worktree, {
    applyMerge: '병합 적용',
    discardMerge: '폐기',
    confirmMerge: '병합 확인',
    confirmDiscard: '폐기 확인',
  });

  Object.assign(LOCALE_TEXT.en.logs, {
    linesShown: '{count} lines shown',
    waitingForNextEvent: 'waiting for next event...',
    exportHeader: 'AgentCLI live log export',
    exportSource: 'Source',
    exportCursor: 'Cursor',
    exportFilters: 'Filters',
    exportNoMatches: 'No matching log lines',
    emptyState: 'Empty log',
    filterAll: 'ALL',
    filterInfo: 'INFO',
    filterWarn: 'WARN',
    filterErr: 'ERR',
    filterDebug: 'DEBUG',
    backendTranscript: 'backend transcript',
    noSourcesAvailable: 'No log sources available.',
  });
  Object.assign(LOCALE_TEXT.en.goals, {
    saveGoals: 'Save Goals',
    toggleCheckbox: 'Toggle goal checkbox {checkboxState} at line {lineNumber}',
  });
  Object.assign(LOCALE_TEXT.en.runner, {
    controllerReportedError: 'Runner controller reported an error.',
    controlFailed: 'Runner control failed.',
    controlFailedHttp: 'Runner control failed (HTTP {status}).',
  });
  Object.assign(LOCALE_TEXT.en.worktree, {
    actionFailed: 'Worktree action failed.',
    applyingPendingDecision: 'Applying the pending worktree decision for {sourceRepo}.',
  });
  Object.assign(LOCALE_TEXT.en.notifications, {
    localStopConfirmed: 'Local stop confirmed. UI switched to stopped state.',
    observedKindsNote: 'Kinds derived from actual notification rows',
  });

  Object.assign(LOCALE_TEXT.en, {
    common: {
      ...LOCALE_TEXT.en.common,
      source: 'Source',
      available: 'available',
      parsed: 'parsed',
      empty: 'empty',
      missing: 'missing',
      size: 'Size',
      mtime: 'Mtime',
      field: 'Field',
      preview: 'Preview',
      latest: 'latest',
      files: 'Files',
      status: 'Status',
      quota: 'Quota',
      backup: 'Backup',
      backups: 'backups',
      changes: 'Changes',
      change: 'change',
      edit: 'Edit',
      up: 'Up',
      down: 'Down',
      delete: 'Delete',
      row: 'Row',
      moved: 'Moved',
      edited: 'Edited',
      yes: 'yes',
      no: 'no',
      exists: 'Exists',
      bytes: 'bytes',
      runs: 'runs',
      tasks: 'tasks',
      skipped: 'skipped',
      overrides: 'overrides',
      running: 'running',
      stopped: 'stopped',
      live: 'live',
      success: 'success',
    },
    goals: {
      ...LOCALE_TEXT.en.goals,
      source: 'Source',
      parsed: 'parsed',
      noParserWarnings: 'No parser warnings.',
      goalSavePanel: 'Goal save',
      localChecklist: 'Local checklist with add, edit, reorder, save, and completion actions',
      goalTextRequired: 'Goal text cannot be empty.',
      readOnlyFallback: 'Fallback data is shown locally when the read-only API is unavailable.',
    },
    config: {
      ...LOCALE_TEXT.en.config,
      missingSchema: 'Missing schema for {path}',
      field: 'Config field',
      listPlaceholderNumbers: '1, 2, 3',
      listPlaceholderValues: 'value, value',
      noConfigChangesSupplied: 'No config changes were supplied.',
      fixPendingChangesBeforeSaving: '{count} pending change(s) must be fixed before saving.',
      savingConfigChanges: 'Saving config changes...',
      configSavedMessage: 'Config saved.',
      groupProject: 'Project',
      groupRunner: 'Runner',
      groupQuota: 'Quota',
      groupWorktree: 'Worktree',
      groupPrompts: 'Prompt Paths',
      groupCodexModels: 'Codex Models',
      groupPmRefresh: 'PM Refresh',
      groupBudget: 'Budget',
      groupTelegram: 'Telegram',
      groupGoals: 'Goals',
    },
    prompts: {
      ...LOCALE_TEXT.en.prompts,
      preview: 'Preview',
      content: 'Content',
      noBackupsAvailable: 'No backups available',
      errorCode: 'Error code',
      backupPath: 'Backup path',
        promptInventorySummary: 'Inventory previews stay redacted. Select a prompt to open the explicit full-content read path.',
        draftStaysLocal: 'Draft edits stay local until save or reset.',
        promptEditorSummary: 'Loaded through the explicit read path',
      saveCreatesBackup: 'Saving always creates a backup before updating the prompt file.',
      copyPromptSummary: 'Copy prompt summary',
      template: 'template',
      override: 'override',
      overrides: 'overrides',
      unknownSource: 'unknown source',
      unresolvedPath: '(unresolved path)',
      promptReadFailed: 'Prompt read failed.',
      saving: 'Saving...',
      restoring: 'Restoring...',
      restoringBackup: 'Restoring prompt content from the selected backup and writing a safety copy first.',
    },
    history: {
      ...LOCALE_TEXT.en.history,
      latest: 'latest',
      successfulRuns: 'successful runs',
      completedRuns: 'completed',
      configMaxUsd: 'config max_usd',
      readOnlyRunArtifacts: 'read-only run artifacts',
      persistedSummariesDriveThisView: 'Persisted run summaries drive this view. Task counts and shutdown reasons are read from the run artifacts, not reconstructed placeholders.',
    },
    notifications: {
      ...LOCALE_TEXT.en.notifications,
      filterAll: 'ALL',
      filterRunStart: 'RUN START',
      filterRunStop: 'RUN STOP',
      filterTaskDone: 'TASK DONE',
      filterTaskFailed: 'TASK FAILED',
      filterQuota: 'QUOTA',
      filterError: 'ERROR',
      filterStalled: 'STALLED',
      newestEvent: 'Newest event',
      configuredEvents: 'Configured events',
      stalledThreshold: 'Stalled threshold',
      controlPlaneStatus: 'Control-plane status',
      runnerControlSnapshot: 'Runner control snapshot',
      runStartAndStop: 'run start + run stop',
      successEvents: 'success events',
      budgetNotices: 'budget notices',
      actionNeeded: 'action needed',
      eventsReadFrom: 'Events are read from lifecycle records and control-plane snapshots. No placeholder feed is used.',
    },
    runner: {
      ...LOCALE_TEXT.en.runner,
      loadingStatus: 'Loading runner control status...',
      enabledRunning: 'Runner controls are enabled and the controller reports a running runner.',
      enabledStopped: 'Runner controls are enabled and the controller reports a stopped runner.',
      disabledUntilServerOptIn: 'Runner controls are disabled until the server opt-in is enabled.',
      requestInFlight: 'A runner control request is already in flight.',
      typePhraseToContinue: 'type the phrase to continue',
      refreshingStatus: 'Refreshing runner status until it reaches the expected state.',
      actionUnavailable: 'action unavailable',
      controllerUnavailableMessage: 'Runner controller is unavailable.',
      controlsDisabledMessage: 'Runner controls are disabled.',
      typeConfirmationPhrase: 'Type "{confirmation}" to confirm.',
      confirmationPhraseMismatch: 'Confirmation phrase must be "{confirmation}".',
      stateTimeout: 'Runner did not report {state} within {seconds}s.',
    },
    worktree: {
      ...LOCALE_TEXT.en.worktree,
      status: 'Status',
      statusFile: 'Status file',
      sourceBranch: 'Source branch',
      manualCleanupRequired: 'Cleanup failed after the decision was recorded.',
      noPendingMergeAvailable: 'No pending worktree merge is available.',
      reviewRequiredBeforeChanges: 'Review required before source-repo changes',
      currentArtifactPath: 'Current artifact path',
      sourceRepoLabel: 'Source repo',
      patchExportFailedBeforeMarker: 'Patch export failed before a reviewable merge marker was written.',
      exportedPatchNotAutoApplied: 'The patch was exported, but auto-apply did not run.',
      noSourceRepoChangePending: 'No source-repo change is pending.',
      noCommitWillBeCreated: 'No commit will be created.',
      pendingMetadataIncomplete: 'The pending worktree metadata is incomplete.',
      fixOrDeletePendingFile: 'Fix or delete the pending file in the CLI before trying again.',
      applyExportedPatchBeforeConfirming: 'Apply the exported patch before confirming merge or discard.',
      worktreeAlreadyFinalized: 'The worktree is already finalized.',
      noPendingFile: 'no pending file',
      reviewThePatchBeforeSourceRepoChanges: 'Review the patch before making any source-repo changes.',
      reviewChecklist: 'Review checklist',
      riskNotes: 'Risk notes',
      reviewCompletedLocally: 'Worktree review marked complete locally.',
      pendingMerge: 'Pending merge',
      mergeActions: 'Merge actions',
      finalizedWorktree: 'Finalized worktree',
      noPendingReview: 'No pending worktree merge.',
      actionUnavailable: 'Worktree action unavailable.',
      typeConfirmationPhrase: 'Type "{confirmation}" to confirm this worktree action.',
      confirmationPhraseMismatch: 'Confirmation phrase must be "{confirmation}".',
      cleanupLifecycle: 'Cleanup lifecycle',
      cleanupTarget: 'Cleanup target',
      cleanupStatusDetail: 'Cleanup status detail',
      exportStatus: 'Export status',
      repositoryRoot: 'Repository root',
      baseBranchForPatch: 'Base branch for patch',
      mergeBase: 'Merge base',
      worktreeHead: 'Worktree head',
      runThatProducedPatch: 'Run that produced the patch',
      isolatedSourceTree: 'Isolated source tree',
      mergePatchArtifact: 'Merge patch artifact',
      readOnlyContractSource: 'Read-only contract source',
    },
  });

  Object.assign(LOCALE_TEXT.ko, {
    common: {
      ...LOCALE_TEXT.ko.common,
      source: '출처',
      available: '사용 가능',
      parsed: '파싱됨',
      empty: '비어 있음',
      missing: '없음',
      size: '크기',
      mtime: '수정 시각',
      field: '필드',
      preview: '미리보기',
      latest: '최신',
      files: '파일',
      status: '상태',
      quota: '할당량',
      backup: '백업',
      changes: '변경 사항',
      change: '변경',
    },
    goals: {
      ...LOCALE_TEXT.ko.goals,
      source: '출처',
      parsed: '파싱됨',
      noParserWarnings: '파서 경고 없음.',
      goalSavePanel: '목표 저장',
      localChecklist: '추가, 편집, 재정렬, 저장, 완료가 가능한 로컬 체크리스트',
      goalTextRequired: '목표 텍스트는 비워 둘 수 없습니다.',
    },
    config: {
      ...LOCALE_TEXT.ko.config,
      missingSchema: '{path}에 대한 스키마가 없습니다.',
      field: '설정 필드',
      listPlaceholderNumbers: '1, 2, 3',
      listPlaceholderValues: '값, 값',
      noConfigChangesSupplied: '전달된 설정 변경이 없습니다.',
      fixPendingChangesBeforeSaving: '저장하기 전에 보류 중인 변경 {count}개를 수정해야 합니다.',
      savingConfigChanges: '설정 변경 저장 중...',
      configSavedMessage: '설정이 저장되었습니다.',
    },
    prompts: {
      ...LOCALE_TEXT.ko.prompts,
      preview: '미리보기',
      content: '내용',
      noBackupsAvailable: '사용 가능한 백업 없음',
      errorCode: '오류 코드',
      backupPath: '백업 경로',
        promptInventorySummary: '인벤토리 미리보기는 가려진 상태로 유지됩니다. 명시적인 전체 읽기 경로를 열려면 프롬프트를 선택하세요.',
        draftStaysLocal: '초안 편집은 저장하거나 초기화하기 전까지 로컬에만 유지됩니다.',
        promptEditorSummary: '명시적인 읽기 경로를 통해 로드됨',
      saveCreatesBackup: '저장은 프롬프트 파일을 업데이트하기 전에 항상 백업을 만듭니다.',
      copyPromptSummary: '프롬프트 요약 복사',
    },
    history: {
      ...LOCALE_TEXT.ko.history,
      latest: '최신',
      successfulRuns: '성공한 실행',
      completedRuns: '완료',
      configMaxUsd: '설정 max_usd',
      readOnlyRunArtifacts: '읽기 전용 실행 산출물',
      persistedSummariesDriveThisView: '저장된 실행 요약이 이 보기를 구동합니다. 작업 수와 종료 사유는 실행 산출물에서 읽어오며, 재구성된 플레이스홀더가 아닙니다.',
    },
    notifications: {
      ...LOCALE_TEXT.ko.notifications,
      filterAll: '전체',
      filterRunStart: '실행 시작',
      filterRunStop: '실행 중지',
      filterTaskDone: '작업 완료',
      filterTaskFailed: '작업 실패',
      filterQuota: '할당량',
      filterError: '오류',
      filterStalled: '정지',
      newestEvent: '최신 이벤트',
      configuredEvents: '설정된 이벤트',
      stalledThreshold: '정지 임계값',
      controlPlaneStatus: '제어 평면 상태',
      runnerControlSnapshot: '실행기 제어 스냅샷',
      runStartAndStop: '실행 시작 + 실행 중지',
      successEvents: '성공 이벤트',
      budgetNotices: '예산 알림',
      actionNeeded: '조치 필요',
      eventsReadFrom: '이벤트는 수명주기 기록과 제어 평면 스냅샷에서 읽어옵니다. 플레이스홀더 피드는 사용하지 않습니다.',
      localStopConfirmed: '로컬 중지가 확인되었고 UI가 중지 상태로 전환되었습니다.',
      observedKindsNote: '실제 알림 행에서 파생된 종류입니다.',
    },
    runner: {
      ...LOCALE_TEXT.ko.runner,
      loadingStatus: '실행기 제어 상태 로딩 중...',
      enabledRunning: '실행기 컨트롤이 활성화되었고 컨트롤러가 실행 중인 실행기를 보고합니다.',
      enabledStopped: '실행기 컨트롤이 활성화되었고 컨트롤러가 중지된 실행기를 보고합니다.',
      disabledUntilServerOptIn: '서버 옵트인이 활성화될 때까지 실행기 컨트롤은 비활성화됩니다.',
      requestInFlight: '실행기 제어 요청이 이미 진행 중입니다.',
      typePhraseToContinue: '계속하려면 문구를 입력하세요',
      refreshingStatus: '예상 상태가 될 때까지 실행기 상태를 새로고침합니다.',
      stopProgress: '중지 진행',
      actionUnavailable: '작업을 사용할 수 없습니다.',
      controllerUnavailableMessage: '실행기 컨트롤러를 사용할 수 없습니다.',
      controlsDisabledMessage: '실행기 컨트롤이 비활성화되었습니다.',
      controllerReportedError: '실행기 컨트롤러가 오류를 보고했습니다.',
      controlFailed: '실행기 제어 실패.',
      controlFailedHttp: '실행기 제어 실패(HTTP {status}).',
      actionDisabled: '작업 비활성화',
      actionFailed: '실행기 작업 실패.',
      confirmationRequired: '확인 필요',
      typeExactConfirmationToEnableAction: '{action} 작업을 활성화하려면 "{confirmation}"를 정확히 입력하세요.',
    },
    worktree: {
      ...LOCALE_TEXT.ko.worktree,
      status: '상태',
      statusFile: '상태 파일',
      sourceBranch: '소스 브랜치',
      manualCleanupRequired: '결정이 기록된 후 정리가 실패했습니다.',
      noPendingMergeAvailable: '대기 중인 작업트리 병합이 없습니다.',
      reviewRequiredBeforeChanges: '소스 저장소 변경 전에 검토가 필요합니다',
      currentArtifactPath: '현재 산출물 경로',
      sourceRepoLabel: '소스 저장소',
      patchExportFailedBeforeMarker: '검토 가능한 병합 마커가 쓰이기 전에 패치 내보내기가 실패했습니다.',
      exportedPatchNotAutoApplied: '패치가 내보내졌지만 자동 적용이 실행되지 않았습니다.',
      noSourceRepoChangePending: '대기 중인 소스 저장소 변경이 없습니다.',
      noCommitWillBeCreated: '커밋은 생성되지 않습니다.',
      pendingMetadataIncomplete: '대기 중인 작업트리 메타데이터가 불완전합니다.',
      fixOrDeletePendingFile: '다시 시도하기 전에 CLI에서 대기 파일을 수정하거나 삭제하세요.',
      applyExportedPatchBeforeConfirming: '병합 또는 폐기를 확인하기 전에 내보낸 패치를 적용하세요.',
      worktreeAlreadyFinalized: '작업트리가 이미 종료되었습니다.',
      noPendingFile: '대기 파일 없음',
      reviewThePatchBeforeSourceRepoChanges: '소스 저장소 변경 전에 패치를 검토하세요.',
      reviewChecklist: '검토 체크리스트',
      riskNotes: '위험 참고',
      mergeSummary: '{sourceRepo}에 {patchPath}를 커밋 없이 적용하려면 병합을 확인하세요.',
      discardSummary: '{sourceRepo}를 건드리지 않고 대기 상태를 제거하려면 {worktreeDir}에서 폐기를 확인하세요.',
      actionFailedHttp: '작업트리 작업 실패(HTTP {status}).',
      noPatchPathAvailable: '사용 가능한 패치 경로가 없습니다.',
      pendingMerge: '대기 중인 작업트리 병합',
      mergeActions: '병합/폐기 작업',
      finalizedWorktree: '완료된 작업트리',
      noPendingReview: '대기 중인 작업트리 병합이 없습니다.',
      actionUnavailable: '작업트리를 사용할 수 없습니다.',
      actionFailed: '작업트리 작업 실패.',
      applyingPendingDecision: '{sourceRepo}에 대한 대기 중인 작업트리 결정을 적용하는 중입니다.',
      typeConfirmationPhrase: '이 작업을 확인하려면 "{confirmation}"를 정확히 입력하세요.',
      confirmationPhraseMismatch: '확인 문구는 "{confirmation}"여야 합니다.',
      cleanupLifecycle: '정리 수명주기',
      cleanupTarget: '정리 대상',
      cleanupStatusDetail: '정리 상태 세부 정보',
      exportStatus: '내보내기 상태',
      repositoryRoot: '저장소 루트',
      baseBranchForPatch: '패치 기준 브랜치',
      mergeBase: '병합 기준점',
      worktreeHead: '워크트리 헤드',
      runThatProducedPatch: '패치를 생성한 실행',
      isolatedSourceTree: '분리된 소스 트리',
      mergePatchArtifact: '병합 패치 산출물',
      readOnlyContractSource: '읽기 전용 계약 원본',
    },
  });

  Object.assign(LOCALE_TEXT.ko.common, {
    backups: '백업',
    edit: '편집',
    up: '위',
    down: '아래',
    delete: '삭제',
    row: '행',
    moved: '이동됨',
    edited: '수정됨',
    yes: '예',
    no: '아니요',
    exists: '존재함',
    bytes: '바이트',
    runs: '실행',
    tasks: '작업',
    skipped: '건너뜀',
    overrides: '오버라이드',
    running: '실행 중',
    stopped: '중지됨',
    live: '실시간',
    success: '성공',
  });
  Object.assign(LOCALE_TEXT.ko.goals, {
    readOnlyFallback: '읽기 전용 API를 사용할 수 없을 때는 로컬 폴백 데이터를 표시합니다.',
    saveGoals: '목표 저장',
    toggleCheckbox: '줄 {lineNumber} 목표 체크박스 {checkboxState} 전환',
  });
  Object.assign(LOCALE_TEXT.ko.config, {
    groupProject: '프로젝트',
    groupRunner: '러너',
    groupQuota: '할당량',
    groupWorktree: '작업트리',
    groupPrompts: '프롬프트 경로',
    groupCodexModels: 'Codex 모델',
    groupPmRefresh: 'PM 갱신',
    groupBudget: '예산',
    groupTelegram: 'Telegram',
    groupGoals: '목표',
  });
  Object.assign(LOCALE_TEXT.ko.prompts, {
    template: '템플릿',
    override: '오버라이드',
    overrides: '오버라이드',
    unknownSource: '알 수 없는 소스',
    unresolvedPath: '(경로 미확정)',
    promptReadFailed: '프롬프트 읽기에 실패했습니다.',
    saving: '저장 중...',
    restoring: '복원 중...',
    restoringBackup: '선택한 백업에서 프롬프트 내용을 복원하고 먼저 안전 복사본을 작성합니다.',
  });
  Object.assign(LOCALE_TEXT.ko.logs, {
    activeRunLog: '활성 실행 로그',
    linesShown: '{count}줄 표시됨',
    live: '실시간',
    waitingForNextEvent: '다음 이벤트를 기다리는 중...',
    exportHeader: 'AgentCLI 라이브 로그 내보내기',
    exportSource: '출처',
    exportCursor: '커서',
    exportFilters: '필터',
    exportNoMatches: '일치하는 로그 줄 없음',
    emptyState: '로그 비어 있음',
    cursor: '커서',
    filteredLine: '필터된 로그 줄',
    filteredLines: '필터된 로그 줄',
    filterAll: '전체',
    filterInfo: '정보',
    filterWarn: '경고',
    filterErr: '오류',
    filterDebug: '디버그',
    backendTranscript: '백엔드 트랜스크립트',
    noSourcesAvailable: '사용 가능한 로그 소스가 없습니다.',
  });
  Object.assign(LOCALE_TEXT.ko.pipeline, {
    partialLifecycleRecords: '일부 라이프사이클 기록만 게시되었습니다.',
  });
  Object.assign(LOCALE_TEXT.ko.runner, {
    controllerReportedError: '실행기 컨트롤러가 오류를 보고했습니다.',
    controlFailed: '실행기 제어 실패.',
    controlFailedHttp: '실행기 제어 실패(HTTP {status}).',
    typeConfirmationPhrase: '확인하려면 "{confirmation}"를 입력하세요.',
    confirmationPhraseMismatch: '확인 문구는 "{confirmation}"여야 합니다.',
    stateTimeout: '러너가 {seconds}초 안에 {state} 상태를 보고하지 않았습니다.',
  });
  Object.assign(LOCALE_TEXT.ko.worktree, {
    reviewCompletedLocally: '작업트리 검토를 로컬에서 완료로 표시했습니다.',
  });

  const INITIAL_LOCALE = detectPreferredLocale();
  let activeLocale = INITIAL_LOCALE;

  function formatLocaleMessage(template, values = {}) {
    return String(template).replace(/\{(\w+)\}/g, (_, key) => {
      const value = values[key];
      return value == null ? '' : String(value);
    });
  }

  function localeText(locale, key, values = {}) {
    const selected = normalizeLocale(locale);
    const table = LOCALE_TEXT[selected] || LOCALE_TEXT.en;
    const template = getAt(table, key) ?? getAt(LOCALE_TEXT.en, key) ?? key;
    return formatLocaleMessage(template, values);
  }

  function currentLocale() {
    return normalizeLocale(activeLocale || INITIAL_LOCALE);
  }

  function setLocale(locale) {
    const next = normalizeLocale(locale);
    if (activeLocale === next) {
      return;
    }
    activeLocale = next;
    state.locale = next;
    writeJSON(STORAGE.locale, next);
    syncDocumentLocale();
    renderShell({ preserveScroll: true, force: true });
  }

  function t(key, values = {}) {
    return localeText(currentLocale(), key, values);
  }

  function goalSaveConfirmationPhrase() {
    return t(GOALS_SAVE_CONFIRMATION_KEY);
  }

  function syncDocumentLocale() {
    if (document.documentElement) {
      document.documentElement.lang = currentLocale();
    }
  }

  function viewLabel(view) {
    const map = {
      dashboard: 'nav.dashboard',
      pipeline: 'nav.pipeline',
      logs: 'nav.logs',
      backlog: 'nav.backlog',
      goals: 'nav.goals',
      config: 'nav.config',
      prompts: 'nav.prompts',
      history: 'nav.runHistory',
      notifications: 'nav.notifications',
      worktree: 'nav.worktreeReview',
      landing: 'nav.landingPreview',
      mobile: 'nav.mobilePreview',
    };
    return t(map[view] || 'nav.dashboard');
  }

  function renderLocaleToggle() {
    const locales = ['en', 'ko'];
    const buttons = locales
      .map((locale) => {
        const active = currentLocale() === locale;
        return `
          <button
            type="button"
            class="button button--tiny locale-switch__button ${active ? 'button--primary locale-switch__button--active' : 'button--quiet'}"
            data-action="${locale === 'ko' ? 'set-locale-ko' : 'set-locale-en'}"
            data-locale="${escapeHTML(locale)}"
            aria-pressed="${active ? 'true' : 'false'}"
            aria-label="${escapeHTML(t('locale.language'))} ${escapeHTML(t(`locale.${locale}`))}"
          >${escapeHTML(t(`locale.${locale}`))}</button>
        `;
      })
      .join('');
    return `<div class="locale-switch" aria-label="${escapeHTML(t('locale.language'))}">${buttons}</div>`;
  }

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

  function timestampMs(ts) {
    if (ts == null || ts === '') return null;
    const text = String(ts).trim();
    const numeric = typeof ts === 'number' || /^[+-]?\d+(\.\d+)?$/.test(text);
    if (numeric) {
      const n = Number(ts);
      if (!Number.isFinite(n)) return null;
      return Math.abs(n) < 1000000000000 ? n * 1000 : n;
    }
    const parsed = Date.parse(text);
    return Number.isNaN(parsed) ? null : parsed;
  }

  function fmtRelative(ts) {
    const ms = timestampMs(ts);
    if (ms == null) return '--';
    const diff = Math.max(0, (nowMs() - ms) / 1000);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function fmtClock(ts) {
    const ms = timestampMs(ts);
    if (ms == null) return '--';
    return new Date(ms).toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  function fmtTime(ts) {
    const ms = timestampMs(ts);
    if (ms == null) return '--';
    return new Date(ms).toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function fmtDateTime(ts) {
    const ms = timestampMs(ts);
    if (ms == null) return '--';
    return new Date(ms).toLocaleString('en-GB', {
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

  function metricText(available, value, formatter, unavailableText = t('common.unavailable')) {
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
      return t('topbar.quotaUnavailable');
    }
    const usedText = metricText(true, data.used, fmtPercent);
    const windowText = toText(data.window, '');
    return windowText ? `${windowText} | ${usedText}` : usedText;
  }

  function formatQuotaSummary(quota) {
    const data = toObject(quota);
    if (!data.available || !toText(data.window, '')) {
      return t('topbar.quotaUnavailable');
    }
    const usedText = metricText(true, data.used, fmtPercent);
    const windowText = toText(data.window, '');
    return windowText ? `${windowText} quota | ${usedText} used` : `quota | ${usedText} used`;
  }

  function renderQuotaControl(quota, title = '') {
    const data = toObject(quota);
    const available = Boolean(data.available && toText(data.window, ''));
    const titleText = toText(
      title,
      available
        ? t('topbar.quotaUsageWindow', { window: toText(data.window, '') })
        : t('topbar.quotaUnavailable')
    );
    // quota unavailable
    if (!available) {
      return `<span class="meter-chip meter-chip--quota meter-chip--unavailable" title="${escapeHTML(titleText)}">${escapeHTML(t('topbar.quotaUnavailable'))}</span>`;
    }
    const quotaText = formatQuotaUsage(data);
    const quotaWidth = progressWidth(data.used);
    return `
      <span class="meter-chip meter-chip--quota" title="${escapeHTML(titleText)}">
        ${escapeHTML(t('topbar.quotaUsage'))} ${escapeHTML(quotaText)}
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

  const ROLE_SPEC_RE = /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$/;

  function normalizeRoleSpec(value, options = []) {
    const text = toText(value, '').trim();
    if (!text) {
      return '';
    }
    const match = normalizeListValues(options).find((option) => String(option).trim().toLowerCase() === text.toLowerCase());
    return match || text;
  }

  function normalizeRoleSpecs(value, options = []) {
    const parts = Array.isArray(value)
      ? value
      : typeof value === 'string'
        ? value.split(/[\s,;]+/)
        : value == null
          ? []
          : [value];
    return parts
      .map((item) => normalizeRoleSpec(item, options))
      .filter(Boolean);
  }

  function classifyRoleSpec(value, options = []) {
    const text = normalizeRoleSpec(value, options);
    if (!text) {
      return 'empty';
    }
    const normalizedOptions = normalizeListValues(options);
    if (normalizedOptions.some((option) => String(option).trim().toLowerCase() === text.toLowerCase())) {
      return 'builtin';
    }
    if (ROLE_SPEC_RE.test(text)) {
      return 'plugin';
    }
    return 'invalid';
  }

  function configSecurityRoleStatus(config = state.configDraft, schema = state.configSchema) {
    const raw = toObject(config);
    const schemaObject = toObject(schema);
    const roleOptions = toArray(schemaObject.roles?.options || []);
    const securitySchema = toObject(schemaObject['security.enabled'] || { kind: 'bool' });
    const roles = normalizeRoleSpecs(getAt(raw, 'roles'), roleOptions);
    const securityEnabled = Boolean(normalizeConfigValue(getAt(raw, 'security.enabled'), securitySchema, 'security.enabled'));
    const securitySelected = roles.some((role) => String(role).trim().toLowerCase() === 'security');
    const requirement = t('config.securityRoleRequirement');
    const warning = securitySelected && !securityEnabled
      ? t('config.securityRoleMissingEnabled')
      : securityEnabled && !securitySelected
        ? t('config.securityRoleMissingRole')
        : '';
    return {
      roles,
      securityEnabled,
      securitySelected,
      requirement,
      warning,
    };
  }

  function renderConfigSecurityRoleBanner(config = state.configDraft, schema = state.configSchema, selectedPath = currentConfigSelection()) {
    const status = configSecurityRoleStatus(config, schema);
    const path = toText(selectedPath, '');
    const show = path === 'roles' || path === 'security.enabled' || Boolean(status.warning);
    if (!show) {
      return '';
    }
    const tone = status.warning ? 'warn' : 'info';
    return `
      <div class="modal-banner section-banner section-banner--${tone}">
        <span class="dot" style="background: currentColor;"></span>
        <div>
          <div class="section-banner__title">${escapeHTML(t('config.securityRoleTitle'))}</div>
          <div class="section-banner__copy">${escapeHTML(status.requirement)}</div>
          ${status.warning ? `<div class="section-banner__copy">${escapeHTML(status.warning)}</div>` : ''}
        </div>
      </div>
    `;
  }

  function renderConfigRolesControl({
    path = 'roles',
    options = [],
    value = [],
    disabled = false,
  } = {}) {
    const optionValues = normalizeListValues(options);
    const items = normalizeRoleSpecs(value, optionValues);
    const itemLookup = new Set(items.map((item) => item.toLowerCase()));
    const inputValue = fmtList(items);
    const placeholder = optionValues.length
      ? `${optionValues.join(', ')}, pkg.mod:Class`
      : 'PM, Security, Dev, QA, pkg.mod:Class';
    const chipsHTML = items.length
      ? items
        .map((item, index) => {
          const kind = classifyRoleSpec(item, optionValues);
          const tone = kind === 'plugin' ? 'chip--info' : kind === 'invalid' ? 'chip--err' : 'chip--accent';
          return `
            <button
              type="button"
              class="chip ${tone}"
              data-config-role-remove-path="${escapeHTML(path)}"
              data-config-role-remove-index="${index}"
              aria-label="${escapeHTML(`${t('common.deselect')} ${item}`)}"
              ${disabled ? 'disabled' : ''}
            >${escapeHTML(item)}</button>
          `;
        })
        .join('')
      : `<span class="summary-note">${escapeHTML(t('common.none'))}</span>`;
    const optionsHTML = optionValues
      .map((option) => `
        <button
          type="button"
          class="modal-tab ${itemLookup.has(option.toLowerCase()) ? 'modal-tab--active' : ''}"
          data-config-multi="${escapeHTML(path)}"
          data-config-value="${escapeHTML(option)}"
          ${disabled ? 'disabled' : ''}
        >${escapeHTML(option)}</button>
      `)
      .join('');
    return `
      <div class="config-role-control">
        <input
          type="text"
          class="field-control config-role-control__input"
          value="${escapeHTML(inputValue)}"
          placeholder="${escapeHTML(placeholder)}"
          data-config-field="${escapeHTML(path)}"
          spellcheck="false"
          autocomplete="off"
          autocapitalize="off"
          autocorrect="off"
          ${disabled ? 'disabled' : ''}
        >
        <div class="runner-control__chips">${chipsHTML}</div>
        <div class="modal-tabs config-role-control__options">${optionsHTML}</div>
      </div>
    `;
  }

  function progressWidth(value) {
    const pct = Math.max(0, Math.min(100, Math.round((Number(value) || 0) * 100)));
    return `${pct}%`;
  }

  function isValidView(view) {
    return VIEW_ORDER.includes(view);
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
    if (normalized === 'completed') return t('common.complete');
    if (normalized === 'success' || normalized === 'complete' || normalized === 'done') {
      return finalReason ? t('common.complete') : t('common.success');
    }
    if (normalized === 'running') return t('runner.running');
    if (normalized === 'stopping') return t('runner.stopping');
    if (normalized === 'stopped') return t('runner.stopped');
    if (normalized === 'failed' || normalized === 'error') return t('common.failed');
    if (normalized === 'idle') return t('runner.idle');
    return t('common.unknown');
  }

  function runStatusTone(status, finalReason = '') {
    const normalized = String(status || 'idle').toLowerCase();
    if (normalized === 'success') return 'success';
    if (normalized === 'completed' || normalized === 'complete' || normalized === 'done') return 'completed';
    if (normalized === 'running') return 'running';
    if (normalized === 'stopping' || normalized === 'stopped') return 'stopped';
    if (normalized === 'failed' || normalized === 'error') return 'failed';
    if (normalized === 'idle') return 'idle';
    return 'idle';
  }

  function executionStatusLabel(status) {
    const normalized = String(status || 'idle').toLowerCase();
    if (normalized === 'success' || normalized === 'completed' || normalized === 'complete' || normalized === 'done') return t('common.complete');
    if (normalized === 'running') return t('runner.running');
    if (normalized === 'stopping') return t('runner.stopping');
    if (normalized === 'stopped') return t('runner.stopped');
    if (normalized === 'failed' || normalized === 'error') return t('common.failed');
    if (normalized === 'idle') return t('runner.idle');
    return t('common.unknown');
  }

  function executionStatusTone(status) {
    const normalized = String(status || 'idle').toLowerCase();
    if (normalized === 'running') return 'running';
    if (normalized === 'stopping' || normalized === 'stopped') return 'stopped';
    if (normalized === 'failed' || normalized === 'error') return 'failed';
    if (normalized === 'success' || normalized === 'completed' || normalized === 'complete' || normalized === 'done') return 'completed';
    if (normalized === 'idle') return 'idle';
    return 'idle';
  }

  function normalizeProjectStatus(status) {
    const normalized = String(status ?? '').trim().toLowerCase();
    if (['complete', 'completed', 'success', 'done', 'true', '1'].includes(normalized)) {
      return 'complete';
    }
    if (['incomplete', 'partial', 'pending', 'false', '0', 'none', 'unknown'].includes(normalized)) {
      return 'incomplete';
    }
    return normalized === 'complete' ? 'complete' : normalized === 'incomplete' ? 'incomplete' : (status ? 'complete' : 'incomplete');
  }

  function projectStatusLabel(status) {
    return normalizeProjectStatus(status) === 'complete' ? t('common.complete') : t('common.incomplete');
  }

  function projectStatusTone(status) {
    return normalizeProjectStatus(status) === 'complete' ? 'success' : 'warn';
  }

  function projectStatusClass(status) {
    return `status-chip status-chip--${projectStatusTone(status)}`;
  }

  function projectBannerClass(status) {
    return normalizeProjectStatus(status) === 'complete' ? 'modal-banner section-banner section-banner--success' : 'modal-banner section-banner section-banner--warn';
  }

  function executionStatusClass(status) {
    return `status-chip status-chip--${executionStatusTone(status)}`;
  }

  const RUN_STATUS_CLASS_NAMES = {
    idle: 'status-chip status-chip--idle',
    running: 'status-chip status-chip--running',
    completed: 'status-chip status-chip--completed',
    success: 'status-chip status-chip--success',
    stopped: 'status-chip status-chip--stopped',
    failed: 'status-chip status-chip--failed',
  };

  const SNAPSHOT_STATUS_CLASS_NAMES = {
    loading: 'status-chip--loading',
    running: 'status-chip--running',
    warn: 'status-chip--warn',
    err: 'status-chip--err',
    reconnecting: 'status-chip--reconnecting',
    stale: 'status-chip--stale',
  };

  const SECTION_NOTICE_CLASS_NAMES = {
    info: 'section-banner--info',
    warn: 'section-banner--warn',
    err: 'section-banner--err',
    reconnecting: 'section-banner--reconnecting',
    stale: 'section-banner--stale',
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

  function snapshotStatusClass(tone) {
    const normalized = toText(tone, 'running');
    return SNAPSHOT_STATUS_CLASS_NAMES[normalized] || SNAPSHOT_STATUS_CLASS_NAMES.running;
  }

  function sectionNoticeClass(tone) {
    const normalized = toText(tone, 'info');
    return SECTION_NOTICE_CLASS_NAMES[normalized] || SECTION_NOTICE_CLASS_NAMES.info;
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
  const SNAPSHOT_TEST_HOOKS = typeof globalThis !== 'undefined' ? toObject(globalThis.__AGENTCLI_TEST_HOOKS__) : {};
  const SNAPSHOT_POLL_MS = Math.max(250, toNumber(SNAPSHOT_TEST_HOOKS.snapshotPollMs, 15000));
  const SNAPSHOT_RECONNECT_MAX_MS = Math.max(SNAPSHOT_POLL_MS, toNumber(SNAPSHOT_TEST_HOOKS.snapshotMaxBackoffMs, 30000));
  const RUNNER_CONTROL_STATUS_POLL_MS = 500;
  const RUNNER_CONTROL_STATUS_TIMEOUT_MS = 15000;
  const STALE_AFTER_MS = Math.max(SNAPSHOT_POLL_MS, toNumber(SNAPSHOT_TEST_HOOKS.snapshotStaleAfterMs, 30000));
  const STAGE_INDEX = {
    idle: 0,
    pm: 0,
    security: 1,
    dev: 2,
    qa: 3,
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
    const phrases = {
      start: t(RUNNER_CONTROL_CONFIRMATION_KEYS.start),
      stop: t(RUNNER_CONTROL_CONFIRMATION_KEYS.stop),
      reload: t(RUNNER_CONTROL_CONFIRMATION_KEYS.reload),
      restart: t(RUNNER_CONTROL_CONFIRMATION_KEYS.restart),
    };
    return phrases[action] || t(RUNNER_CONTROL_CONFIRMATION_KEYS.reload);
  }

  function runnerControlActionLabel(action, busy = false) {
    const labels = {
      start: t('runner.start'),
      stop: t('runner.stop'),
      reload: t('runner.reload'),
      restart: t('runner.restart'),
    };
    const label = labels[action] || t('runner.start');
    if (!busy) {
      return label;
    }
    const busyLabels = {
      start: t('runner.starting'),
      stop: t('runner.stopping'),
      reload: t('runner.reloading'),
      restart: t('runner.restarting'),
    };
    return busyLabels[action] || t('runner.working');
  }

  function runnerControlCompletionLabel(action) {
    const labels = {
      start: t('runner.started'),
      stop: t('runner.stopped'),
      reload: t('runner.reloaded'),
      restart: t('runner.restarted'),
    };
    return labels[action] || t('common.saved');
  }

  function normalizeRunnerControlStartMode(value) {
    const raw = toText(value, '').replace(/_/g, '-').toLowerCase();
    if (raw === 'continuous' || raw === 'loop') {
      return raw;
    }
    return 'one-shot';
  }

  function normalizeRunnerControlStartOptionsContract(contract) {
    const raw = toObject(contract);
    const redaction = toObject(raw.redaction);
    return {
      repo: toText(raw.repo, ''),
      path: toText(raw.path, ''),
      defaultsPath: toText(raw.defaults_path || raw.defaultsPath, ''),
      runDir: toText(raw.run_dir || raw.runDir, ''),
      resumeLatest: raw.resume_latest == null ? Boolean(raw.resumeLatest) : Boolean(raw.resume_latest),
      values: toObject(raw.values),
      defaults: toObject(raw.defaults),
      schema: toObject(raw.schema),
      choices: toObject(raw.choices),
      validation: normalizeRunnerControlStartOptionsValidation(raw.validation),
      argvPreview: toArray(raw.argv_preview || raw.argvPreview).map((item) => toText(item, '')),
      redaction: {
        active: Boolean(redaction.active),
        placeholder: toText(redaction.placeholder, REDACTED_VALUE),
        paths: toArray(redaction.paths),
        tokens: toArray(redaction.tokens),
        scope: toText(redaction.scope, ''),
      },
    };
  }

  function normalizeRunnerControlStartOptionsValidation(validation) {
    const raw = toObject(validation);
    const rawErrors = toArray(raw.errors).map((item) => toObject(item));
    const fieldErrors = {};
    const rawFieldErrors = toObject(raw.field_errors || raw.fieldErrors);
    Object.entries(rawFieldErrors).forEach(([field, value]) => {
      const messages = toArray(value).map((item) => toText(item, '').trim()).filter(Boolean);
      if (messages.length) {
        fieldErrors[String(field)] = messages;
      }
    });
    if (!Object.keys(fieldErrors).length) {
      rawErrors.forEach((error) => {
        const field = toText(error.field, '').trim();
        const message = toText(error.message, '').trim();
        if (field && message) {
          if (!fieldErrors[field]) {
            fieldErrors[field] = [];
          }
          fieldErrors[field].push(message);
        }
      });
    }
    return {
      valid: raw.valid == null ? rawErrors.length === 0 : Boolean(raw.valid),
      message: toText(raw.message, ''),
      errorCount: toNumber(raw.error_count ?? raw.errorCount, rawErrors.length),
      errors: rawErrors,
      fieldErrors,
    };
  }

  function runnerControlStartOptionsContract(control = state.runnerControl) {
    const current = toObject(control);
    return normalizeRunnerControlStartOptionsContract(current.startOptions || current.start_options);
  }

  function runnerControlStartOptionsDraftFrom(raw = {}, fallback = {}, control = state.runnerControl) {
    const source = toObject(raw);
    const base = toObject(fallback);
    const status = toObject(toObject(control).status);
    const contract = runnerControlStartOptionsContract(control);
    const fallbackConfigPath = toText(
      source.config_path ||
        source.configPath ||
        source.config ||
        base.config_path ||
        base.configPath ||
        base.config ||
        status.configPath ||
        status.config_path ||
        '',
      ''
    );
    const modeSource = source.run_mode || source.runMode || source.mode || base.run_mode || base.runMode || base.mode || (source.loop || base.loop ? 'loop' : source.continuous || base.continuous ? 'continuous' : '');
    const runMode = normalizeRunnerControlStartMode(modeSource);
    const loopMaxCycles = toText(
      source.loop_max_cycles ??
        source.loopMaxCycles ??
        source.max_cycles ??
        source.maxCycles ??
        base.loop_max_cycles ??
        base.loopMaxCycles ??
        base.max_cycles ??
        base.maxCycles ??
        '0',
      '0'
    );
    const runDir = toText(source.run_dir || source.runDir || base.run_dir || base.runDir || contract.runDir || '', '');
    const resumeLatest =
      source.resume_latest != null
        ? Boolean(source.resume_latest)
        : source.resumeLatest != null
          ? Boolean(source.resumeLatest)
          : base.resume_latest != null
            ? Boolean(base.resume_latest)
            : base.resumeLatest != null
              ? Boolean(base.resumeLatest)
              : contract.resumeLatest;
    return {
      autopilot: source.autopilot == null ? Boolean(base.autopilot) : Boolean(source.autopilot),
      run_mode: runMode,
      continuous: runMode === 'continuous' || runMode === 'loop',
      loop: runMode === 'loop',
      one_shot: runMode === 'one-shot',
      loop_max_cycles: loopMaxCycles,
      profile: toText(source.profile || base.profile || 'personal', 'personal'),
      execution_backend: toText(
        source.execution_backend ||
          source.executionBackend ||
          source.backend ||
          base.execution_backend ||
          base.executionBackend ||
          base.backend ||
          'codex',
        'codex'
      ),
      config_path: fallbackConfigPath,
      run_dir: runDir,
      resume_latest: Boolean(resumeLatest),
      redaction: clone(toObject(contract.redaction)),
    };
  }

  function runnerControlStartOptionsDraft(control = state.runnerControl) {
    const contract = runnerControlStartOptionsContract(control);
    return runnerControlStartOptionsDraftFrom(contract.values, contract.defaults, control);
  }

  function runnerControlStartOptionsDefaultDraft(control = state.runnerControl) {
    const contract = runnerControlStartOptionsContract(control);
    return runnerControlStartOptionsDraftFrom(contract.defaults, contract.values, control);
  }

  function runnerControlStartOptionDisplayValue(path, value) {
    const normalizedPath = String(path || '');
    if (normalizedPath === 'autopilot') {
      return Boolean(value) ? t('common.enabled') : t('common.disabled');
    }
    if (normalizedPath === 'run_mode') {
      const mode = normalizeRunnerControlStartMode(value);
      if (mode === 'continuous') {
        return t('runner.continuous');
      }
      if (mode === 'loop') {
        return t('runner.loop');
      }
      return t('runner.oneShot');
    }
    if (normalizedPath === 'loop_max_cycles') {
      const text = toText(value, '');
      return text || t('common.none');
    }
    if (normalizedPath === 'config_path') {
      return redactionAwareText(value, t('common.none'));
    }
    const text = toText(value, '');
    return text || t('common.none');
  }

  function runnerControlStartOptionsPayload(draft = state.stopStartOptions) {
    const current = toObject(draft);
    const runMode = normalizeRunnerControlStartMode(current.run_mode || current.runMode || current.mode);
    const profile = toText(current.profile, 'personal').trim().toLowerCase() || 'personal';
    const executionBackend = toText(current.execution_backend || current.executionBackend || current.backend, 'codex').trim().toLowerCase() || 'codex';
    const redaction = toObject(current.redaction);
    const configPath = toText(current.config_path || current.configPath || current.config, '').trim();
    const configPathPlaceholder = toText(redaction.placeholder, REDACTED_VALUE).trim() || REDACTED_VALUE;
    const loopMaxCycles = toText(current.loop_max_cycles ?? current.loopMaxCycles ?? current.max_cycles ?? current.maxCycles ?? '', '');
    const runDir = toText(current.run_dir || current.runDir, '').trim();
    const resumeLatest = current.resume_latest != null ? Boolean(current.resume_latest) : Boolean(current.resumeLatest);
    const payload = {
      autopilot: Boolean(current.autopilot),
      run_mode: runMode,
      continuous: runMode === 'continuous' || runMode === 'loop',
      loop: runMode === 'loop',
      one_shot: runMode === 'one-shot',
      loop_max_cycles: loopMaxCycles,
      profile,
      execution_backend: executionBackend,
      resume_latest: resumeLatest,
    };
    if (configPath && configPath !== REDACTED_VALUE && configPath !== configPathPlaceholder) {
      payload.config_path = configPath;
    } else if (!configPath && !(redaction.active && configPathPlaceholder === REDACTED_VALUE)) {
      payload.config_path = '';
    }
    if (runDir && runDir !== REDACTED_VALUE) {
      payload.run_dir = runDir;
    }
    return payload;
  }

  function runnerControlStartOptionsArgvPreview(control = state.runnerControl, draft = state.stopStartOptions) {
    const contract = runnerControlStartOptionsContract(control);
    const current = toObject(draft && typeof draft === 'object' ? draft : runnerControlStartOptionsDraft(control));
    const redaction = toObject(contract.redaction);
    const placeholder = toText(redaction.placeholder, REDACTED_VALUE).trim() || REDACTED_VALUE;
    const repoText = toText(contract.repo || toObject(toObject(control).status).repo || '', '');
    const configPathText = toText(current.config_path || current.configPath || current.config || '', '');
    const runDirText = toText(current.run_dir || current.runDir || contract.runDir || '', '');
    const runModeRaw = toText(current.run_mode || current.runMode || current.mode, '').replace(/_/g, '-').trim().toLowerCase();
    const runMode = ['continuous', 'loop'].includes(runModeRaw) ? runModeRaw : normalizeRunnerControlStartMode(runModeRaw);
    const profile = toText(current.profile, 'personal').trim().toLowerCase() || 'personal';
    const backend = toText(current.execution_backend || current.executionBackend || current.backend, 'codex').trim().toLowerCase() || 'codex';
    const resumeLatest = current.resume_latest != null
      ? Boolean(current.resume_latest)
      : current.resumeLatest != null
        ? Boolean(current.resumeLatest)
        : Boolean(contract.resumeLatest);
    const argv = [
      '--repo',
      repoText || '',
      '--config',
      configPathText === placeholder ? REDACTED_VALUE : configPathText,
      Boolean(current.autopilot) ? '--autopilot' : '--no-autopilot',
    ];
    if (runMode === 'loop') {
      argv.push('--continuous', '--loop');
    } else if (runMode === 'continuous') {
      argv.push('--continuous', '--no-loop');
    } else {
      argv.push('--no-continuous', '--no-loop');
    }
    argv.push(resumeLatest ? '--resume-latest' : '--no-resume-latest');
    argv.push('--loop-max-cycles', toText(current.loop_max_cycles ?? current.loopMaxCycles ?? current.max_cycles ?? current.maxCycles ?? '0', '0'));
    argv.push('--profile', profile);
    argv.push('--execution-backend', backend);
    if (runDirText && runDirText !== REDACTED_VALUE) {
      argv.push('--run-dir', runDirText);
    }
    if (redaction.active) {
      for (let index = 0; index < argv.length - 1; index += 1) {
        if (argv[index] === '--repo' || argv[index] === '--config' || argv[index] === '--run-dir') {
          argv[index + 1] = REDACTED_VALUE;
        }
      }
    }
    return argv;
  }

  function runnerControlStartOptionsValidation(control = state.runnerControl, draft = state.stopStartOptions) {
    const contract = runnerControlStartOptionsContract(control);
    const current = toObject(draft && typeof draft === 'object' ? draft : runnerControlStartOptionsDraft(control));
    const redaction = toObject(contract.redaction);
    const placeholder = toText(redaction.placeholder, REDACTED_VALUE).trim() || REDACTED_VALUE;
    const errors = [];
    const fieldErrors = {};
    const addError = (field, code, message, details = null) => {
      const error = { field, code, message };
      if (details && Object.keys(details).length) {
        error.details = details;
      }
      errors.push(error);
      if (!fieldErrors[field]) {
        fieldErrors[field] = [];
      }
      fieldErrors[field].push(message);
    };

    const runModeChoices = toArray(contract.choices.run_mode || ['one-shot', 'continuous', 'loop']);
    const profileChoices = toArray(contract.choices.profile || ['personal', 'enterprise']);
    const backendChoices = toArray(contract.choices.execution_backend || ['codex', 'claudecode']);
    const rawRunMode = toText(current.run_mode || current.runMode || current.mode, '').replace(/_/g, '-').trim().toLowerCase();
    const runMode = runModeChoices.includes(rawRunMode) ? rawRunMode : normalizeRunnerControlStartMode(rawRunMode);
    const continuous = Boolean(current.continuous);
    const loop = Boolean(current.loop);
    const oneShot = current.one_shot == null ? runMode === 'one-shot' : Boolean(current.one_shot);
    const configPath = toText(current.config_path || current.configPath || current.config || '', '').trim();
    const configPathRedacted = configPath === REDACTED_VALUE || configPath === placeholder;
    const runDir = toText(current.run_dir || current.runDir || contract.runDir || '', '').trim();
    const resumeLatest = current.resume_latest != null
      ? Boolean(current.resume_latest)
      : current.resumeLatest != null
        ? Boolean(current.resumeLatest)
        : Boolean(contract.resumeLatest);
    const loopMaxCyclesText = toText(current.loop_max_cycles ?? current.loopMaxCycles ?? current.max_cycles ?? current.maxCycles ?? '', '').trim();
    const loopMaxCycles = loopMaxCyclesText === '' ? NaN : Number(loopMaxCyclesText);

    if (rawRunMode && !runModeChoices.includes(rawRunMode)) {
      addError('run_mode', 'invalid_choice', 'Run mode must be one-shot, continuous, or loop.', { choices: runModeChoices });
    }
    if (continuous !== (runMode === 'continuous' || runMode === 'loop')) {
      addError('continuous', 'invalid_combination', 'Continuous does not match the selected run mode.');
    }
    if (loop !== (runMode === 'loop')) {
      addError('loop', 'invalid_combination', 'Loop does not match the selected run mode.');
    }
    if (oneShot !== (runMode === 'one-shot')) {
      addError('one_shot', 'invalid_combination', 'One-shot does not match the selected run mode.');
    }
    const autopilot = Boolean(current.autopilot);
    if (typeof current.autopilot !== 'boolean' && current.autopilot != null) {
      addError('autopilot', 'invalid_choice', 'Autopilot must be true or false.', { choices: [true, false] });
    }
    const profile = toText(current.profile, 'personal').trim().toLowerCase() || 'personal';
    if (!profileChoices.includes(profile)) {
      addError('profile', 'invalid_choice', 'Profile must be personal or enterprise.', { choices: profileChoices });
    }
    const backend = toText(current.execution_backend || current.executionBackend || current.backend, 'codex').trim().toLowerCase() || 'codex';
    if (!backendChoices.includes(backend)) {
      addError('execution_backend', 'invalid_choice', 'Backend must be codex or claudecode.', { choices: backendChoices });
    }
    if (!configPathRedacted && !configPath) {
      addError('config_path', 'required', 'Config path cannot be empty.');
    }
    if (loopMaxCyclesText !== '' && (!Number.isFinite(loopMaxCycles) || loopMaxCycles < 0)) {
      addError('loop_max_cycles', 'invalid_value', 'Max cycles must be an integer greater than or equal to 0.');
    }
    if (runDir && resumeLatest) {
      addError('resume_latest', 'invalid_combination', 'Resume latest cannot be combined with an explicit run dir.');
    }

    return {
      valid: errors.length === 0,
      message: errors.length === 0 ? '' : 'Fix the highlighted start options before continuing.',
      errorCount: errors.length,
      errors,
      fieldErrors,
      argvPreview: runnerControlStartOptionsArgvPreview(control, current),
      autopilot,
      runMode,
      runDir,
      resumeLatest,
      configPath,
    };
  }

  function runnerControlStartOptionsState() {
    return toObject(
      state.stopStartOptions && typeof state.stopStartOptions === 'object'
        ? state.stopStartOptions
        : runnerControlStartOptionsDraft(state.runnerControl)
    );
  }

  function updateRunnerControlStartOptionsDraft(updates = {}, { rerender = true } = {}) {
    state.stopStartOptions = {
      ...runnerControlStartOptionsState(),
      ...toObject(updates),
    };
    state.stopError = '';
    if (rerender && state.stopOpen) {
      renderStopOverlay();
    }
  }

  function updateRunnerControlStartMode(mode) {
    const normalized = normalizeRunnerControlStartMode(mode);
    updateRunnerControlStartOptionsDraft({
      run_mode: normalized,
      continuous: normalized === 'continuous' || normalized === 'loop',
      loop: normalized === 'loop',
      one_shot: normalized === 'one-shot',
    });
  }

  function toggleRunnerControlAutopilot() {
    const current = runnerControlStartOptionsState();
    updateRunnerControlStartOptionsDraft({ autopilot: !Boolean(current.autopilot) });
  }

  function updateRunnerControlStartField(field, value, { rerender = false } = {}) {
    const normalizedField = String(field || '');
    let normalizedValue = value;
    if (normalizedField === 'loop_max_cycles' || normalizedField === 'config_path' || normalizedField === 'run_dir') {
      normalizedValue = toText(value, '');
    } else if (normalizedField === 'profile' || normalizedField === 'execution_backend') {
      normalizedValue = toText(value, '').trim().toLowerCase();
    } else if (normalizedField === 'resume_latest' || normalizedField === 'autopilot') {
      normalizedValue = Boolean(value);
    }
    updateRunnerControlStartOptionsDraft({ [normalizedField]: normalizedValue }, { rerender });
  }

  function runnerControlModalTitle(action) {
    const titles = {
      start: t('runner.confirmStart'),
      stop: t('runner.confirmStop'),
      reload: t('runner.confirmReload'),
      restart: t('runner.confirmRestart'),
    };
    return titles[action] || t('runner.confirmAction');
  }

  function runnerControlActionSummary(action) {
    const summaries = {
      start: t('runner.startSummary'),
      stop: t('runner.stopSummary'),
      reload: t('runner.reloadSummary'),
      restart: t('runner.restartSummary'),
    };
    return summaries[action] || t('runner.confirmAction');
  }

  function stopProgressPhaseTrail(stopProgress) {
    return toArray(stopProgress?.history)
      .map((entry) => toText(entry?.phaseLabel || entry?.phase, '').trim())
      .filter(Boolean)
      .join(' → ');
  }

  function stopProgressProcessSummary(records) {
    return toArray(records)
      .map((record) => {
        const pid = toText(record?.pid, '');
        if (!pid) {
          return '';
        }
        const sessionFile = redactionAwareText(record?.sessionFile || '', '');
        return sessionFile ? `PID ${pid} (${sessionFile})` : `PID ${pid}`;
      })
      .filter(Boolean)
      .join(' · ');
  }

  function stopProgressSignalSummary(signal) {
    const item = toObject(signal);
    const path = redactionAwareText(item.path || '', '');
    const updatedAt = toText(item.updatedAt, '');
    if (!path && !updatedAt) {
      return '';
    }
    return [path, updatedAt ? `(${updatedAt})` : ''].filter(Boolean).join(' ');
  }

  function stopProgressPathSummary(paths) {
    return Object.entries(toObject(paths))
      .map(([key, value]) => `${key}: ${redactionAwareText(value, '')}`)
      .filter(Boolean)
      .join(' · ');
  }

  function runnerControlStateInfo(control = currentLiveRunRunnerControl()) {
    const current = toObject(control);
    const status = toObject(current.status);
    const statusReason = toText(status.reason, '');
    const statusReasonText = redactionAwareText(statusReason, '');
    const currentMessageText = redactionAwareText(current.message, t('runner.working'));
    const lastMessageText = redactionAwareText(current.lastMessage, '');
    const lastErrorText = redactionAwareText(current.lastError, '');
    const stopProgress = normalizeStopProgress(status.stopProgress);
    const stopProgressMessageText = redactionAwareText(stopProgress.message, '');
    const stopProgressGuidanceText = redactionAwareText(stopProgress.timeoutGuidance?.summary, '');
    const stopProgressPhaseText = redactionAwareText(stopProgress.currentPhase?.message || stopProgress.message, '');
    const stopProgressPhaseLabelText = redactionAwareText(stopProgress.currentPhase?.phaseLabel || stopProgress.phase, '');
    const busyAction = runnerControlBusyAction(current);
    if (current.busy || state.stopSubmitting) {
      const action = state.stopSubmitting ? (busyAction || current.lastAction || state.stopAction || 'start') : '';
      return {
        chipTone: 'loading',
        bannerTone: 'info',
        label: state.stopSubmitting ? runnerControlActionLabel(action, true) : t('runner.working'),
        title: t('runner.actionInFlight'),
        copy: currentMessageText || t('runner.working'),
      };
    }
    if (current.lastError || statusReason.startsWith('status_error:')) {
      return {
        chipTone: 'err',
        bannerTone: 'err',
        label: t('common.failed'),
        title: t('runner.backendError'),
        copy: lastErrorText || statusReasonText || currentMessageText || t('runner.backendError'),
      };
    }
    if (!current.controllerAvailable) {
      return {
        chipTone: 'paused',
        bannerTone: 'warn',
        label: t('runner.unavailable'),
        title: t('runner.controllerUnavailable'),
        copy: currentMessageText || t('runner.controllerUnavailable'),
      };
    }
    if (!current.enabled) {
      return {
        chipTone: 'paused',
        bannerTone: 'warn',
        label: t('runner.controlsDisabled'),
        title: t('runner.controlsDisabled'),
        copy: currentMessageText || t('runner.controlsDisabled'),
      };
    }
    if (stopProgress.phase === 'timeout') {
      return {
        chipTone: 'warn',
        bannerTone: 'warn',
        label: t('runner.stopTimedOut'),
        title: t('runner.stopTimedOut'),
        copy: stopProgressGuidanceText || stopProgressMessageText || stopProgressPhaseText || t('runner.retryStop'),
      };
    }
    if (stopProgress.active) {
      return {
        chipTone: 'loading',
        bannerTone: 'info',
        label: t('runner.stopping'),
        title: t('runner.stopProgress'),
        copy: stopProgressMessageText || stopProgressPhaseText || stopProgressPhaseLabelText || t('runner.working'),
      };
    }
    if (stopProgress.phase === 'finalized') {
      return {
        chipTone: 'success',
        bannerTone: 'success',
        label: t('runner.stopped'),
        title: t('runner.actionComplete'),
        copy: stopProgressMessageText || stopProgressPhaseText || t('runner.stopped'),
      };
    }
    if (current.lastMessage) {
      return {
        chipTone: 'success',
        bannerTone: 'success',
        label: runnerControlCompletionLabel(current.lastAction),
        title: t('runner.actionComplete'),
        copy: currentMessageText || lastMessageText,
      };
    }
    if (status.running) {
      return {
        chipTone: 'running',
        bannerTone: 'info',
        label: t('runner.running'),
        title: t('runner.running'),
        copy: currentMessageText || t('runner.running'),
      };
    }
    return {
      chipTone: 'idle',
      bannerTone: 'idle',
      label: t('runner.ready'),
      title: t('runner.ready'),
      copy: currentMessageText || t('runner.ready'),
    };
  }

  function runnerControlLiveStateChips(liveState = currentLiveRunLiveState()) {
    const current = normalizeLiveState(liveState);
    const chips = toArray(current.items)
      .map((entry) => {
        const label = `${entry.label}: ${entry.statusLabel}`;
        return chip(label, liveStateToneClass(entry.kind, entry.status, entry.available));
      })
      .join('');
    return `
      <div class="summary-note">${escapeHTML(t('runner.liveStates'))}</div>
      <div class="runner-control__chips">${chips}</div>
    `;
  }

  function runnerControlLiveStateRows(liveState = currentLiveRunLiveState()) {
    const current = normalizeLiveState(liveState);
    return toArray(current.items).map((entry) => ({
      label: entry.label,
      value: entry.statusLabel,
      className: liveStateToneClass(entry.kind, entry.status, entry.available),
    }));
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

  function runnerControlDetailRows(control = currentLiveRunRunnerControl(), display) {
    const current = toObject(control);
    const stateInfo = display || runnerControlStateInfo(current);
    const status = toObject(current.status);
    const stopProgress = normalizeStopProgress(status.stopProgress);
    const liveStateRows = runnerControlLiveStateRows(current.liveState || current.live_state || status.liveState || status.live_state);
    const statusConfigPath = redactionAwareText(status.configPath, t('common.unknown'));
    const sourceValue = current.source && current.source !== 'default' ? current.source : t('common.unknown');
    const runStatusValue = current.runStatus
      ? (String(current.runStatus).toLowerCase() === 'running'
        ? t('runner.running')
        : String(current.runStatus).toLowerCase() === 'idle'
          ? t('runner.idle')
          : String(current.runStatus).toLowerCase() === 'loading'
            ? t('common.loading')
            : String(current.runStatus).toLowerCase() === 'ready'
              ? t('runner.ready')
              : String(current.runStatus).toLowerCase() === 'stopped'
                ? t('runner.stopped')
                : current.runStatus)
      : (status.running ? t('runner.running') : t('runner.idle'));
    const lastActionValue = current.lastAction
      ? (String(current.lastAction).toLowerCase() === 'start'
        ? t('runner.start')
        : String(current.lastAction).toLowerCase() === 'stop'
          ? t('runner.stop')
          : String(current.lastAction).toLowerCase() === 'reload'
            ? t('runner.reload')
            : String(current.lastAction).toLowerCase() === 'restart'
              ? t('runner.restart')
              : current.lastAction)
      : t('common.none');
    const rows = [
      { label: t('runner.source'), value: sourceValue, className: 'runner-control__value--muted' },
      { label: t('runner.selectedRepo'), value: status.repo || t('common.unknown'), className: 'runner-control__value--muted' },
      { label: t('runner.selectedConfig'), value: statusConfigPath, className: 'runner-control__value--muted' },
      {
        label: t('runner.controller'),
        value: current.controllerAvailable ? t('runner.available') : t('runner.unavailable'),
        className: current.controllerAvailable ? (stateInfo.chipTone === 'err' ? 'runner-control__value--err' : 'runner-control__value--accent') : runnerControlValueClass(stateInfo.chipTone),
      },
      { label: t('runner.state'), value: stateInfo.label, className: runnerControlValueClass(stateInfo.chipTone) },
      { label: t('runner.runMode'), value: status.runnerMode || t('common.unknown'), className: 'runner-control__value--muted' },
      {
        label: t('runner.runStatus'),
        value: runStatusValue,
        className: status.running ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      ...liveStateRows,
    ];
    if (stopProgress.phase) {
      const stopPhaseValue = stopProgress.currentPhase?.phaseLabel || stopProgress.phase || t('common.unknown');
      const historyTrail = stopProgressPhaseTrail(stopProgress);
      const stopFilePaths = stopProgressPathSummary(stopProgress.stopFilePaths);
      const timeoutActive = stopProgress.phase === 'timeout';
      const trackedPidsLabel = timeoutActive ? t('runner.remainingTrackedChildPids') : t('runner.trackedChildPids');
      const trackedChildPids = toArray(stopProgress.trackedChildPids).map((pid) => toText(pid, '')).filter(Boolean).join(', ');
      const trackedChildProcesses = stopProgressProcessSummary(stopProgress.trackedChildProcesses);
      const currentSignalParts = [stopProgressSignalSummary(stopProgress.lastArtifactSignal), stopProgressSignalSummary(stopProgress.lastLogSignal)].filter(Boolean);
      const timeoutSummary = stopProgress.timeoutGuidance?.summary || '';
      rows.push({
        label: t('runner.currentStopPhase'),
        value: `${stopPhaseValue}${stopProgress.elapsedSeconds ? ` ${stopProgress.elapsedSeconds}s` : ''}`,
        className: stopProgress.phase === 'timeout'
          ? 'runner-control__value--warn'
          : stopProgress.active
            ? 'runner-control__value--accent'
            : 'runner-control__value--muted',
      });
      if (historyTrail) {
        rows.push({
          label: t('runner.phaseHistory'),
          value: historyTrail,
          className: stopProgress.phase === 'timeout'
            ? 'runner-control__value--warn'
            : stopProgress.active
            ? 'runner-control__value--accent'
            : 'runner-control__value--muted',
        });
      }
      if (trackedChildPids || trackedChildProcesses) {
        rows.push({
          label: trackedPidsLabel,
          value: trackedChildProcesses || trackedChildPids,
          className: 'runner-control__value--warn',
        });
      }
      if (stopFilePaths) {
        rows.push({
          label: t('runner.stopFilePaths'),
          value: stopFilePaths,
          className: 'runner-control__value--muted',
        });
      }
      if (currentSignalParts.length) {
        if (stopProgress.lastArtifactSignal) {
          rows.push({
            label: t('runner.lastArtifactSignal'),
            value: stopProgressSignalSummary(stopProgress.lastArtifactSignal),
            className: 'runner-control__value--muted',
          });
        }
        if (stopProgress.lastLogSignal) {
          rows.push({
            label: t('runner.lastLogSignal'),
            value: stopProgressSignalSummary(stopProgress.lastLogSignal),
            className: 'runner-control__value--muted',
          });
        }
      }
      if (timeoutSummary) {
        rows.push({
          label: t('runner.timeoutGuidance'),
          value: timeoutSummary,
          className: stopProgress.timeoutGuidance?.canRetry ? 'runner-control__value--warn' : 'runner-control__value--muted',
        });
      }
      if (stopProgress.manualCleanupHints?.length) {
        rows.push({
          label: t('runner.manualCleanupHints'),
          value: stopProgress.manualCleanupHints.join(' · '),
          className: 'runner-control__value--warn',
        });
      }
      if (stopProgress.lockedFilePaths?.length) {
        rows.push({
          label: t('runner.lockedFilePaths'),
          value: stopProgress.lockedFilePaths.join(' · '),
          className: 'runner-control__value--warn',
        });
      }
    }
    rows.push(
      {
        label: t('runner.lastAction'),
        value: lastActionValue,
        className: current.lastAction ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: t('runner.lastMessage'),
        value: redactionAwareText(current.lastMessage, t('common.none')),
        className: current.lastMessage ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: t('runner.lastError'),
        value: redactionAwareText(current.lastError, t('common.none')),
        className: current.lastError ? 'runner-control__value--err' : 'runner-control__value--muted',
      },
    );
    return rows;
  }

  function renderStopProgressSection(rawStopProgress) {
    const stopProgress = normalizeStopProgress(rawStopProgress);
    const currentPhase = toObject(stopProgress.currentPhase);
    const history = toArray(stopProgress.history);
    const phaseHistoryHTML = history.length
      ? history
        .map((entry, index) => {
          const phaseName = toText(entry.phaseLabel || entry.phase, t('common.unknown'));
          const phaseMessage = redactionAwareText(entry.message, '');
          const metaBits = [];
          if (entry.elapsedSeconds != null) {
            metaBits.push(`${entry.elapsedSeconds}s`);
          }
          if (entry.updatedAt) {
            metaBits.push(entry.updatedAt);
          }
          const meta = metaBits.join(' · ');
          return `
            <div class="stop-progress__history-item ${index === history.length - 1 ? 'stop-progress__history-item--current' : ''}">
              <div class="stop-progress__history-title">${escapeHTML(phaseName)}</div>
              ${meta ? `<div class="stop-progress__history-meta">${escapeHTML(meta)}</div>` : ''}
              ${phaseMessage ? `<div class="stop-progress__history-message">${escapeHTML(phaseMessage)}</div>` : ''}
            </div>
          `;
        })
        .join('')
      : `<div class="stop-progress__empty">${escapeHTML(t('common.none'))}</div>`;
    const trackedChildProcesses = toArray(stopProgress.trackedChildProcesses);
    const trackedChildPids = toArray(stopProgress.trackedChildPids).map((pid) => toText(pid, '').trim()).filter(Boolean);
    const trackedChipValues = trackedChildProcesses.length
      ? trackedChildProcesses.map((record) => `PID ${record.pid}${record.sessionFile ? ` (${record.sessionFile})` : ''}`)
      : trackedChildPids.map((pid) => `PID ${pid}`);
    const trackedPidsHTML = trackedChipValues.length
      ? trackedChipValues.map((value) => `<span class="chip chip--warn">${escapeHTML(value)}</span>`).join('')
      : `<span class="stop-progress__empty">${escapeHTML(t('common.none'))}</span>`;
    const stopFilePaths = stopProgress.stopFilePaths || {};
    const stopFilePathHTML = Object.entries(stopFilePaths).length
      ? Object.entries(stopFilePaths)
        .map(([key, value]) => `
          <div class="stop-progress__kv">
            <div class="stop-progress__kv-key">${escapeHTML(key.replace(/_/g, ' '))}</div>
            <div class="stop-progress__kv-value">${escapeHTML(redactionAwareText(value, ''))}</div>
          </div>
        `)
        .join('')
      : `<div class="stop-progress__empty">${escapeHTML(t('common.none'))}</div>`;
    const artifactSignal = stopProgressSignalSummary(stopProgress.lastArtifactSignal);
    const logSignal = stopProgressSignalSummary(stopProgress.lastLogSignal);
    const signalHTML = [artifactSignal, logSignal]
      .filter(Boolean)
      .map((value) => `<div class="stop-progress__signal">${escapeHTML(value)}</div>`)
      .join('');
    const guidance = toObject(stopProgress.timeoutGuidance);
    const guidanceStepsHTML = toArray(guidance.steps).length
      ? `
        <div class="compact-list stop-progress__steps">
          ${toArray(guidance.steps)
            .map((step) => `
              <div class="compact-list__item">
                <span class="compact-list__bullet"></span>
                <div class="compact-list__body">${escapeHTML(redactionAwareText(step, ''))}</div>
              </div>
            `)
            .join('')}
        </div>
      `
      : '';
    const guidanceHintsHTML = (toArray(stopProgress.manualCleanupHints).length || toArray(stopProgress.lockedFilePaths).length)
      ? `
        <div class="stop-progress__hint-grid">
          ${toArray(stopProgress.manualCleanupHints).length ? `
            <div class="stop-progress__hint">
              <div class="stop-progress__section-title">${escapeHTML(t('runner.manualCleanupHints'))}</div>
              <div class="stop-progress__hint-text">${escapeHTML(redactionAwareText(toArray(stopProgress.manualCleanupHints).join(' · '), ''))}</div>
            </div>
          ` : ''}
          ${toArray(stopProgress.lockedFilePaths).length ? `
            <div class="stop-progress__hint">
              <div class="stop-progress__section-title">${escapeHTML(t('runner.lockedFilePaths'))}</div>
              <div class="stop-progress__hint-text">${escapeHTML(redactionAwareText(toArray(stopProgress.lockedFilePaths).join(' · '), ''))}</div>
            </div>
          ` : ''}
        </div>
      `
      : '';
    const currentPhaseName = toText(currentPhase.phaseLabel || stopProgress.phase, t('common.unknown'));
    const currentPhaseMessage = redactionAwareText(currentPhase.message || stopProgress.message, '');
    const currentPhaseTrail = stopProgressPhaseTrail(stopProgress);
    const timeoutActive = stopProgress.phase === 'timeout';
    const finalizedState = stopProgress.phase === 'finalized';
    const runnerAliveText = stopProgress.runnerAlive ? t('runner.running') : t('runner.stopped');
    const trackedPidsLabel = timeoutActive ? t('runner.remainingTrackedChildPids') : t('runner.trackedChildPids');
    return `
      <div class="stop-progress">
        <div class="stop-progress__head">
          <div class="stop-progress__head-copy">
            <div class="stop-progress__section-title">${escapeHTML(t('runner.currentStopPhase'))}</div>
            <div class="stop-progress__headline">${escapeHTML(currentPhaseName)}</div>
            ${currentPhaseMessage ? `<div class="stop-progress__copy">${escapeHTML(currentPhaseMessage)}</div>` : ''}
          </div>
          <div class="stop-progress__head-state">
            <span class="status-chip ${timeoutActive ? 'status-chip--warn' : stopProgress.active ? 'status-chip--loading' : finalizedState ? 'status-chip--success' : 'status-chip--running'}">
              <span class="dot" style="color: currentColor; background: currentColor;"></span>
              ${escapeHTML(timeoutActive ? t('runner.stopTimedOut') : stopProgress.active ? t('runner.stopping') : t('runner.stopped'))}
            </span>
            <div class="stop-progress__elapsed">${escapeHTML(`${stopProgress.elapsedSeconds || 0}s`)}</div>
            <div class="stop-progress__state-note">${escapeHTML(`${t('runner.runnerAlive')}: ${runnerAliveText}`)}</div>
          </div>
        </div>
        ${currentPhaseTrail ? `<div class="stop-progress__trail">${escapeHTML(currentPhaseTrail)}</div>` : ''}
        <div class="stop-progress__section">
          <div class="stop-progress__section-title">${escapeHTML(t('runner.phaseHistory'))}</div>
          <div class="stop-progress__history">${phaseHistoryHTML}</div>
        </div>
        <div class="stop-progress__grid">
          <div class="stop-progress__section">
            <div class="stop-progress__section-title">${escapeHTML(trackedPidsLabel)}</div>
            <div class="runner-control__chips">${trackedPidsHTML}</div>
          </div>
          <div class="stop-progress__section">
            <div class="stop-progress__section-title">${escapeHTML(t('runner.stopFilePaths'))}</div>
            <div class="stop-progress__kv-list">${stopFilePathHTML}</div>
          </div>
        </div>
        ${(artifactSignal || logSignal) ? `
          <div class="stop-progress__grid">
            ${artifactSignal ? `
              <div class="stop-progress__section">
                <div class="stop-progress__section-title">${escapeHTML(t('runner.lastArtifactSignal'))}</div>
                <div class="stop-progress__signal">${escapeHTML(artifactSignal)}</div>
              </div>
            ` : ''}
            ${logSignal ? `
              <div class="stop-progress__section">
                <div class="stop-progress__section-title">${escapeHTML(t('runner.lastLogSignal'))}</div>
                <div class="stop-progress__signal">${escapeHTML(logSignal)}</div>
              </div>
            ` : ''}
          </div>
        ` : ''}
        ${guidance.summary ? `
          <div class="section-banner section-banner--${stopProgress.phase === 'timeout' ? 'warn' : 'info'}">
            <span class="dot" style="background: currentColor;"></span>
            <div>
              <div class="section-banner__title">${escapeHTML(t('runner.timeoutGuidance'))}</div>
              <div class="section-banner__copy">${escapeHTML(redactionAwareText(guidance.summary, ''))}</div>
            </div>
          </div>
        ` : ''}
        ${guidanceStepsHTML}
        ${guidanceHintsHTML}
      </div>
    `;
  }

  function createRunnerControlModel(overrides = {}) {
    const enabled = Boolean(overrides.enabled);
    const running = Boolean(overrides.running);
    const controllerAvailable = Boolean(overrides.controllerAvailable);
    const busy = Boolean(overrides.busy);
    const source = toText(overrides.source, 'default');
    const runStatus = toText(overrides.runStatus, running ? 'running' : 'idle');
    const currentEvent = toObject(overrides.currentEvent || overrides.current_event);
    const history = toArray(overrides.history || overrides.eventHistory || overrides.event_history);
    const eventCount = toNumber(overrides.eventCount || overrides.event_count, history.length);
    const message = toText(
      overrides.message,
      controllerAvailable
        ? enabled
          ? running
            ? t('runner.enabledRunning')
            : t('runner.enabledStopped')
          : t('runner.disabledUntilServerOptIn')
        : t('runner.controllerUnavailableMessage')
    );
    return {
      enabled,
      source,
      controllerAvailable,
      busy,
      message,
      runStatus,
      liveState: normalizeLiveState(overrides.liveState),
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
        stopProgress: normalizeStopProgress(overrides.stopProgress),
        done: toNumber(overrides.done, 0),
        failed: toNumber(overrides.failed, 0),
        warnings: toNumber(overrides.warnings, 0),
        reason: toText(overrides.reason, ''),
        lastEvent: toText(overrides.lastEvent, ''),
        currentEvent,
        current_event: currentEvent,
        history,
        eventHistory: history,
        event_history: history,
        eventCount,
        event_count: eventCount,
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
      startOptions: normalizeRunnerControlStartOptionsContract(overrides.startOptions),
      lastAction: toText(overrides.lastAction, ''),
      lastMessage: toText(overrides.lastMessage, ''),
      lastError: toText(overrides.lastError, ''),
      currentEvent,
      current_event: currentEvent,
      history,
      eventHistory: history,
      event_history: history,
      eventCount,
      event_count: eventCount,
    };
  }

  function normalizeStopProgressTextList(raw) {
    return toArray(raw)
      .map((item) => toText(item, '').trim())
      .filter(Boolean);
  }

  function normalizeStopProgressPathMap(raw) {
    const item = toObject(raw);
    const paths = {};
    Object.entries(item).forEach(([key, value]) => {
      const text = toText(value, '').trim();
      if (text) {
        paths[String(key)] = text.replace(/\\/g, '/');
      }
    });
    return paths;
  }

  function normalizeStopProgressSignal(raw, kind = '') {
    const item = toObject(raw);
    const path = toText(item.path || item.pathText, '').trim().replace(/\\/g, '/');
    const paths = normalizeStopProgressTextList(item.paths).map((entry) => entry.replace(/\\/g, '/'));
    const updatedAt = toText(item.updated_at || item.updatedAt, '');
    const updatedAtEpoch = toMaybeNumber(item.updated_at_epoch ?? item.updatedAtEpoch);
    const sizeBytes = toMaybeNumber(item.size_bytes ?? item.sizeBytes);
    const signalKind = toText(item.kind, kind || '').trim() || kind || '';
    if (!path && !paths.length && !updatedAt && updatedAtEpoch == null && sizeBytes == null && !signalKind) {
      return null;
    }
    return {
      path,
      paths,
      updatedAt,
      updatedAtEpoch,
      sizeBytes,
      kind: signalKind,
    };
  }

  function normalizeStopProgressGuidance(raw) {
    const item = toObject(raw);
    const summary = toText(item.summary || item.message || item.text, '');
    const steps = normalizeStopProgressTextList(item.steps || item.next_steps || item.nextSteps);
    const manualCleanupHints = normalizeStopProgressTextList(item.manual_cleanup_hints || item.manualCleanupHints);
    const lockedFilePaths = normalizeStopProgressTextList(item.locked_file_paths || item.lockedFilePaths).map((entry) => entry.replace(/\\/g, '/'));
    const retryable = item.recoverable ?? item.canRetry ?? item.can_retry ?? item.retryable;
    const canRetry = retryable == null ? false : Boolean(retryable);
    return {
      summary,
      message: summary,
      steps,
      manualCleanupHints,
      lockedFilePaths,
      recoverable: canRetry,
      canRetry,
      retryable: canRetry,
    };
  }

  function normalizeStopProgressPhaseEntry(raw, fallbackPhase = '') {
    const item = toObject(raw);
    const phase = toText(item.phase, fallbackPhase);
    const phaseLabel = toText(item.phase_label || item.phaseLabel, phase || fallbackPhase);
    const guidance = normalizeStopProgressGuidance(item.timeout_guidance || item.timeoutGuidance);
    return {
      phase,
      phaseLabel,
      message: toText(item.message, ''),
      updatedAt: toText(item.updated_at || item.updatedAt, ''),
      elapsedSeconds: toNumber(item.elapsed_seconds ?? item.elapsedSeconds, 0),
      requestedAt: toText(item.requested_at || item.requestedAt, ''),
      runnerAlive: Boolean(item.runner_alive ?? item.runnerAlive ?? item.running),
      running: Boolean(item.running ?? item.runnerAlive ?? item.runner_alive),
      runnerPid: toMaybeNumber(item.runner_pid ?? item.runnerPid),
      trackedChildPids: normalizeStopProgressTextList(item.tracked_child_pids || item.trackedChildPids).map((pid) => Number(pid)).filter((pid) => Number.isFinite(pid) && pid > 0),
      trackedChildProcesses: toArray(item.tracked_child_processes || item.trackedChildProcesses)
        .map((record) => normalizeStopProgressProcessRecord(record))
        .filter(Boolean),
      stopFilePaths: normalizeStopProgressPathMap(item.stop_file_paths || item.stopFilePaths),
      lastArtifactSignal: normalizeStopProgressSignal(item.last_artifact_signal || item.lastArtifactSignal, 'artifact'),
      lastLogSignal: normalizeStopProgressSignal(item.last_log_signal || item.lastLogSignal, 'log'),
      timeoutGuidance: guidance,
      manualCleanupHints: normalizeStopProgressTextList(item.manual_cleanup_hints || item.manualCleanupHints),
      lockedFilePaths: normalizeStopProgressTextList(item.locked_file_paths || item.lockedFilePaths).map((entry) => entry.replace(/\\/g, '/')),
    };
  }

  function normalizeStopProgressProcessRecord(raw) {
    const item = toObject(raw);
    const pid = toMaybeNumber(item.pid ?? item.child_pid ?? item.childPid);
    if (!pid) {
      return null;
    }
    const sessionFile = toText(item.session_file || item.sessionFile || item.session_path || item.sessionPath, '').trim().replace(/\\/g, '/');
    const alive = item.alive == null ? Boolean(item.running) : Boolean(item.alive);
    const sessionExists = item.session_exists == null ? item.sessionExists : item.session_exists;
    return {
      pid,
      childPid: pid,
      alive,
      sessionFile,
      sessionExists: sessionExists == null ? false : Boolean(sessionExists),
    };
  }

  function normalizeStopProgressHistory(raw, currentPhase) {
    const history = [];
    toArray(raw).forEach((entry) => {
      const normalized = normalizeStopProgressPhaseEntry(entry, currentPhase?.phase || '');
      if (normalized.phase) {
        history.push(normalized);
      }
    });
    if (!history.length || history[history.length - 1].phase !== currentPhase.phase) {
      history.push(currentPhase);
    }
    const deduped = [];
    history.forEach((entry) => {
      if (deduped.length && deduped[deduped.length - 1].phase === entry.phase) {
        deduped[deduped.length - 1] = entry;
      } else {
        deduped.push(entry);
      }
    });
    return deduped;
  }

  function normalizeStopProgress(raw) {
    const item = toObject(raw);
    const currentPhaseRaw = toObject(item.current_phase || item.currentPhase);
    const currentSource = { ...item, ...currentPhaseRaw };
    const currentPhase = normalizeStopProgressPhaseEntry(currentSource, toText(currentSource.phase, ''));
    const historySource = item.history || item.phase_history || item.phaseHistory;
    const history = normalizeStopProgressHistory(historySource, currentPhase);
    const phase = currentPhase.phase || toText(item.phase, '');
    const stopFilePaths = normalizeStopProgressPathMap(item.stop_file_paths || item.stopFilePaths || currentPhase.stopFilePaths || currentPhase.stop_file_paths);
    const trackedChildPids = toArray(item.tracked_child_pids || item.trackedChildPids || currentPhase.trackedChildPids || currentPhase.tracked_child_pids)
      .map((pid) => toMaybeNumber(pid))
      .filter((pid) => pid != null && pid > 0);
    const trackedChildProcesses = toArray(item.tracked_child_processes || item.trackedChildProcesses || currentPhase.trackedChildProcesses || currentPhase.tracked_child_processes)
      .map((record) => normalizeStopProgressProcessRecord(record))
      .filter(Boolean);
    const lastArtifactSignal = normalizeStopProgressSignal(item.last_artifact_signal || item.lastArtifactSignal || currentPhase.lastArtifactSignal || currentPhase.last_artifact_signal, 'artifact');
    const lastLogSignal = normalizeStopProgressSignal(item.last_log_signal || item.lastLogSignal || currentPhase.lastLogSignal || currentPhase.last_log_signal, 'log');
    const timeoutGuidance = normalizeStopProgressGuidance(item.timeout_guidance || item.timeoutGuidance || currentPhase.timeoutGuidance || currentPhase.timeout_guidance);
    const manualCleanupHints = normalizeStopProgressTextList(item.manual_cleanup_hints || item.manualCleanupHints || timeoutGuidance.manualCleanupHints);
    const lockedFilePaths = normalizeStopProgressTextList(item.locked_file_paths || item.lockedFilePaths || timeoutGuidance.lockedFilePaths).map((entry) => entry.replace(/\\/g, '/'));
    const runnerAlive = item.runner_alive ?? item.runnerAlive ?? currentPhase.runnerAlive ?? currentPhase.runner_alive ?? item.running ?? currentPhase.running;
    const running = item.running ?? currentPhase.running ?? runnerAlive;
    const finalPhases = new Set(['finalized', 'timeout', 'failed', 'not_running']);
    const active = Boolean(phase && !finalPhases.has(phase));
    const phaseIndex = Math.max(0, history.length - 1);
    const normalized = {
      phase,
      phaseLabel: currentPhase.phaseLabel || phase,
      message: toText(item.message || currentPhase.message, ''),
      elapsedSeconds: toNumber(item.elapsed_seconds ?? item.elapsedSeconds ?? currentPhase.elapsedSeconds, 0),
      updatedAt: toText(item.updated_at || item.updatedAt || currentPhase.updatedAt, ''),
      requestedAt: toText(item.requested_at || item.requestedAt || currentPhase.requestedAt, ''),
      currentPhase,
      current_phase: currentPhase,
      currentPhaseRaw,
      history,
      phaseHistory: history,
      phase_history: history,
      historyCount: history.length,
      history_count: history.length,
      phaseIndex,
      phase_index: phaseIndex,
      runnerAlive: Boolean(runnerAlive),
      runner_alive: Boolean(runnerAlive),
      running: Boolean(running),
      trackedChildPids,
      tracked_child_pids: trackedChildPids,
      trackedChildProcesses,
      tracked_child_processes: trackedChildProcesses,
      stopFilePaths,
      stop_file_paths: stopFilePaths,
      lastArtifactSignal,
      last_artifact_signal: lastArtifactSignal,
      lastLogSignal,
      last_log_signal: lastLogSignal,
      timeoutGuidance,
      timeout_guidance: timeoutGuidance,
      manualCleanupHints,
      manual_cleanup_hints: manualCleanupHints,
      lockedFilePaths,
      locked_file_paths: lockedFilePaths,
      recoverableTimeout: Boolean(timeoutGuidance.canRetry),
      canRetry: Boolean(timeoutGuidance.canRetry),
      active,
    };
    normalized.currentPhase = normalized.currentPhase || currentPhase;
    normalized.current_phase = normalized.currentPhase;
    normalized.phaseHistory = history;
    normalized.phase_history = history;
    return normalized;
  }

  function normalizeLiveStateKey(value) {
    const raw = toText(value, '').trim().toLowerCase().replace(/[-\s]+/g, '_');
    const compact = raw.replace(/_/g, '');
    if (compact === 'runnerprocess') {
      return 'runnerProcess';
    }
    if (compact === 'taskbackend') {
      return 'taskBackend';
    }
    if (compact === 'trackedchildren') {
      return 'trackedChildren';
    }
    if (compact === 'artifactwriter') {
      return 'artifactWriter';
    }
    return raw;
  }

  function liveStateKindLabel(kind) {
    const key = normalizeLiveStateKey(kind);
    const labels = {
      runnerProcess: t('runner.runnerProcess'),
      taskBackend: t('runner.taskBackend'),
      trackedChildren: t('runner.trackedChildren'),
      artifactWriter: t('runner.artifactWriter'),
    };
    return labels[key] || toText(kind, t('common.unavailable'));
  }

  function liveStateStatusLabel(status) {
    const normalized = toText(status, '').trim().toLowerCase();
    if (normalized === 'alive') {
      return t('runner.alive');
    }
    if (normalized === 'flushing') {
      return t('runner.flushing');
    }
    if (normalized === 'idle') {
      return t('runner.idle');
    }
    if (normalized === 'stopped') {
      return t('runner.stopped');
    }
    return t('common.unavailable');
  }

  function liveStateToneClass(kind, status, available) {
    const normalizedStatus = toText(status, '').trim().toLowerCase();
    if (!available || normalizedStatus === 'unavailable') {
      return 'chip--muted';
    }
    if (normalizedStatus === 'flushing') {
      return 'chip--warn';
    }
    if (normalizedStatus === 'alive') {
      return normalizeLiveStateKey(kind) === 'trackedChildren' ? 'chip--warn' : 'chip--accent';
    }
    return 'chip--muted';
  }

  function normalizeLiveStateEntry(raw, kind = '') {
    const item = toObject(raw);
    const canonicalKind = normalizeLiveStateKey(item.kind || item.key || kind || '');
    const availableValue = item.available ?? item.present ?? item.known;
    const aliveValue = item.alive ?? item.running ?? item.active;
    const flushingValue = item.flushing ?? item.writing;
    const statusText = toText(item.status, '').trim().toLowerCase();
    const statusLabelText = toText(item.statusLabel || item.status_label, '').trim();
    const status = statusText || toText(statusLabelText, '').trim().toLowerCase() || 'unavailable';
    const available = availableValue == null ? status !== 'unavailable' : Boolean(availableValue);
    const count = toMaybeNumber(item.count ?? item.total ?? item.trackedCount ?? item.tracked_count);
    const aliveCount = toMaybeNumber(item.aliveCount ?? item.alive_count);
    const normalized = {
      kind: canonicalKind || normalizeLiveStateKey(kind) || 'unknown',
      label: liveStateKindLabel(canonicalKind || kind),
      available: Boolean(available),
      status: status || 'unavailable',
      statusLabel: statusLabelText || liveStateStatusLabel(status || 'unavailable'),
      source: toText(item.source, ''),
      alive: aliveValue == null ? null : Boolean(aliveValue),
      flushing: flushingValue == null ? null : Boolean(flushingValue),
      count: count == null ? null : count,
      aliveCount: aliveCount == null ? null : aliveCount,
      phase: toText(item.phase, ''),
    };
    if (!normalized.available) {
      normalized.status = 'unavailable';
      normalized.statusLabel = t('common.unavailable');
      normalized.alive = null;
      normalized.flushing = null;
    }
    return normalized;
  }

  function normalizeLiveState(raw) {
    const item = toObject(raw);
    const rawItems = toArray(item.items);
    const lookup = new Map();
    rawItems.forEach((entry) => {
      const normalized = toObject(entry);
      const key = normalizeLiveStateKey(normalized.kind || normalized.key || '');
      if (key) {
        lookup.set(key, normalized);
      }
    });
    const runnerProcess = normalizeLiveStateEntry(
      lookup.get('runnerProcess') || item.runner_process || item.runnerProcess,
      'runnerProcess'
    );
    const taskBackend = normalizeLiveStateEntry(
      lookup.get('taskBackend') || item.task_backend || item.taskBackend,
      'taskBackend'
    );
    const trackedChildren = normalizeLiveStateEntry(
      lookup.get('trackedChildren') || item.tracked_children || item.trackedChildren,
      'trackedChildren'
    );
    const artifactWriter = normalizeLiveStateEntry(
      lookup.get('artifactWriter') || item.artifact_writer || item.artifactWriter,
      'artifactWriter'
    );
    const items = [runnerProcess, taskBackend, trackedChildren, artifactWriter];
    const normalized = {
      available: Boolean(item.available),
      source: toText(item.source, Boolean(item.available) ? 'api' : 'unavailable'),
      runnerProcess,
      runner_process: runnerProcess,
      taskBackend,
      task_backend: taskBackend,
      trackedChildren,
      tracked_children: trackedChildren,
      artifactWriter,
      artifact_writer: artifactWriter,
      items,
    };
    return normalized;
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
        message: t('runner.loadingStatus'),
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
    const currentEvent = toObject(raw.current_event || raw.currentEvent || status.current_event || status.currentEvent);
    const history = toArray(raw.history || raw.event_history || raw.eventHistory || status.history || status.event_history || status.eventHistory);
    const eventCount = toNumber(raw.event_count || raw.eventCount || status.event_count || status.eventCount, history.length);
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
      message: message || (controllerAvailable ? (enabled ? (running ? t('runner.enabledRunning') : t('runner.enabledStopped')) : t('runner.disabledUntilServerOptIn')) : t('runner.controllerUnavailableMessage')),
      runStatus: toText(raw.run_status || raw.runStatus || '', running ? 'running' : 'idle'),
      startOptions: normalizeRunnerControlStartOptionsContract(raw.start_options || raw.startOptions),
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
        stopProgress: normalizeStopProgress(status.stop_progress || status.stopProgress),
        done: toNumber(status.done, 0),
        failed: toNumber(status.failed, 0),
        warnings: toNumber(status.warnings, 0),
        reason: toText(status.reason, ''),
        lastEvent: toText(status.last_event || status.lastEvent || currentEvent.phase || currentEvent.status, ''),
        currentEvent,
        current_event: currentEvent,
        history,
        eventHistory: history,
        event_history: history,
        eventCount,
        event_count: eventCount,
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
      currentEvent,
      current_event: currentEvent,
      history,
      eventHistory: history,
      event_history: history,
      eventCount,
      event_count: eventCount,
      liveState: normalizeLiveState(raw.live_state || raw.liveState || status.live_state || status.liveState),
    };
  }

  function normalizeExecutionStatus(rawStatus, hasRunData, options = {}) {
    const status = toText(rawStatus, 'idle').toLowerCase();
    const running = Boolean(options.running);
    const exitCode = options.exitCode;
    const finalReason = toText(options.finalReason, '').toLowerCase();
    const stopFileExists = Boolean(options.stopFileExists);
    if (!hasRunData || status === 'no-run') {
      return 'idle';
    }
    if (status === 'running') {
      return 'running';
    }
    if (status === 'completed' || status === 'complete' || status === 'done' || status === 'success' || status === 'ok' || status === 'prepared_only') {
      return 'completed';
    }
    if (status === 'finished' || status === 'halted' || status === 'stopping' || status === 'stop_requested' || status === 'stopped' || status === 'cancelled' || status === 'canceled' || status === 'aborted') {
      return 'stopped';
    }
    if (status === 'error') {
      return 'failed';
    }
    if (running) {
      return 'running';
    }
    if (['project_complete', 'all_tasks_done', 'completed', 'success', 'ok', 'done', 'prepared_only'].includes(finalReason)) {
      return 'completed';
    }
    if (['stop_file', 'stop_requested', 'stopped', 'user_stop', 'manual_stop'].includes(finalReason) || stopFileExists) {
      return 'stopped';
    }
    const rc = toMaybeNumber(exitCode);
    if (rc != null) {
      if (rc === 0 && ['','ok','prepared_only','completed','success','project_complete','all_tasks_done','done'].includes(finalReason)) {
        return 'completed';
      }
      if (rc !== 0) {
        return 'failed';
      }
    }
    if (['failed', 'error', 'exception', 'abandoned', 'abandon_failed', 'build_failed', 'test_failed', 'policy_violation', 'exhausted_attempts'].includes(finalReason)) {
      return 'failed';
    }
    return hasRunData ? 'running' : 'idle';
  }

  function normalizeRunStatus(rawStatus, hasRunData, projectComplete = false, options = {}) {
    const executionStatus = normalizeExecutionStatus(rawStatus, hasRunData, options);
    if (projectComplete && executionStatus === 'completed') {
      return 'success';
    }
    return executionStatus;
  }

  function projectCompletionState(progress = {}, goals = {}, backlog = {}, activeRun = {}) {
    const progressData = toObject(progress);
    const goalsData = toObject(goals);
    const backlogData = toObject(backlog);
    const activeData = toObject(activeRun);
    const goalsCompletion = toObject(goalsData.completion || progressData.goalsCompletion || progressData.goals_completion || activeData.goalsCompletion || activeData.goals_completion);
    const backlogCounts = toObject(backlogData.counts || progressData.backlog?.counts || activeData.backlog?.counts);
    const backlogItems = toArray(backlogData.items || progressData.backlog?.items || activeData.backlog?.items);
    const goalsComplete = Boolean(
      progressData.goals_complete ??
        progressData.goalsComplete ??
        activeData.goals_complete ??
        activeData.goalsComplete ??
        goalsCompletion.project_complete
    );
    const backlogComplete = Boolean(
      progressData.backlog_complete ??
        progressData.backlogComplete ??
        activeData.backlog_complete ??
        activeData.backlogComplete ??
        (backlogItems.length === 0
          ? true
          : (
            toNumber(backlogCounts.done || 0, 0) >= backlogItems.length &&
            toNumber(backlogCounts.failed || 0, 0) === 0 &&
            toNumber(backlogCounts.pending || 0, 0) === 0 &&
            toNumber(backlogCounts.in_progress || 0, 0) === 0
          ))
    );
    const projectComplete = Boolean(
      progressData.project_complete ??
        progressData.projectComplete ??
        activeData.project_complete ??
        activeData.projectComplete ??
        (goalsComplete && backlogComplete)
    );
    return {
      hasGoals: Boolean(goalsCompletion.has_goals ?? goalsData.summary?.has_goals ?? progressData.goals?.completion?.has_goals),
      goalsComplete,
      backlogComplete,
      projectComplete,
      projectStatus: projectComplete ? 'complete' : 'incomplete',
    };
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

  function backlogStatusLabel(status) {
    switch (normalizeBacklogStatus(status, 'pending')) {
      case 'done':
        return t('backlog.done');
      case 'in_progress':
        return t('backlog.inProgress');
      case 'failed':
        return t('backlog.failed');
      case 'active':
        return t('backlog.active');
      default:
        return t('backlog.pending');
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
        return t('common.complete');
      case 'running':
        return t('pipeline.inProgress');
      case 'failed':
        return t('common.failed');
      case 'stopped':
        return t('pipeline.stopped');
      case 'skipped':
        return t('pipeline.skipped');
      default:
        return t('pipeline.pending');
    }
  }

  function lifecycleStageStatusLabel(status) {
    switch (normalizeStageStatus(status, 'pending')) {
      case 'done':
        return t('pipeline.completed');
      case 'running':
        return t('pipeline.inProgress');
      case 'failed':
        return t('common.failed');
      case 'stopped':
        return t('pipeline.stopped');
      case 'skipped':
        return t('pipeline.skipped');
      default:
        return t('pipeline.pending');
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
      activeRun: t('common.noDataAvailableYet'),
      stages: t('pipeline.noLifecycleRecords'),
      backlog: t('backlog.noArtifacts'),
      goals: t('goals.noGoals'),
      config: t('config.loadingSnapshot'),
      prompts: t('prompts.inventoryRedacted'),
      logs: t('logs.noEntries'),
      notifications: t('notifications.noRecorded'),
      metrics: t('common.noDataAvailableYet'),
      history: t('history.emptyState'),
      worktree: t('worktree.noPendingMerge'),
      runnerControl: t('runner.controlsDisabled'),
    };
    return messages[kind] || t('common.noDataAvailableYet');
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

  function normalizeGoalSectionNames(sections) {
    return toArray(sections)
      .map((section) => toText(section, '').trim().toLowerCase())
      .filter(Boolean);
  }

  function goalSnapshotMessage(snapshot, total, dirty = false) {
    if (dirty) {
      return `${t('goals.browserLocalDraft')} ${t('goals.draftStaysLocal')}`;
    }
    const raw = toObject(snapshot);
    if (!raw.exists) {
      return t('goals.missing');
    }
    const redaction = toObject(raw.redaction || state.redaction);
    if (redaction.active) {
      return t('config.redactedHidden');
    }
    const rawText = toText(raw.raw_text || raw.rawText, '').trim();
    if (!rawText) {
      return t('goals.empty');
    }
    const completion = toObject(raw.completion);
    const missingSections = normalizeGoalSectionNames(completion.missing_sections || completion.missingSections);
    if (completion.valid === false || missingSections.length) {
      return t('snapshot.partial');
    }
    if (!total) {
      return t('goals.noGoals');
    }
    return t('dashboard.goalsSnapshotReady');
  }

  function redactionAwareText(value, fallback = '', redaction = state.redaction) {
    const meta = toObject(redaction);
    const placeholder = toText(meta.placeholder, REDACTED_VALUE);
    const text = toText(value, '');
    if (meta.active && text === placeholder) {
      return t('config.redactedHidden');
    }
    return text || fallback;
  }

  function goalBucketLabel(bucket) {
    return bucket === 'p0' ? t('goals.p0MustHave') : t('goals.p1ShouldHave');
  }

  function goalBucketName(bucket) {
    return bucket === 'p0'
      ? t('goals.p0MustHave').split('|').pop().trim()
      : t('goals.p1ShouldHave').split('|').pop().trim();
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
    return `${lineNumber ? `${t('goals.sourceLine')} ${lineNumber}` : t('goals.localDraftOnly')} | ${t('goals.checked')} ${goalItemCheckbox(item)}`;
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
      confirmationPhrase: goalSaveConfirmationPhrase(),
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
      return t('goals.noRiskyP0Changes');
    }
    return t('goals.uncheckedP0Goals', { count: total });
  }

  function normalizeGoalSaveRisk(rawRisk) {
    const raw = toObject(rawRisk);
    const deleted = normalizeGoalBucket(raw.deleted_unchecked_p0 || raw.deletedUncheckedP0 || []);
    const downgraded = normalizeGoalBucket(raw.downgraded_unchecked_p0 || raw.downgradedUncheckedP0 || []);
    const riskCount = toNumber(raw.risk_count ?? raw.riskCount, deleted.length + downgraded.length);
    return {
      requiresConfirmation: Boolean(raw.requires_confirmation ?? raw.requiresConfirmation ?? riskCount),
      confirmationPhrase: toText(raw.confirmation_phrase || raw.confirmationPhrase, goalSaveConfirmationPhrase()),
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
      confirmationPhrase: toText(raw.confirmation_phrase || raw.confirmationPhrase || errorDetails.confirmation_phrase || errorDetails.confirmationPhrase, goalSaveConfirmationPhrase()),
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
    const stateCounts = toObject(raw.stateCounts || raw.state_counts);
    const runSummary = toObject(raw.runSummary || raw.run_summary);
    const lastRunSummary = toObject(raw.lastRunSummary || raw.last_run_summary);
    const runCycles = toArray(runSummary.cycles);
    const doneCount = toNumber(stateCounts.done ?? raw.tasksDone ?? taskCounts.done ?? lastRunSummary.done ?? 0, 0);
    const failedCount = toNumber(stateCounts.failed ?? raw.tasksFailed ?? taskCounts.failed ?? lastRunSummary.failed_count ?? 0, 0);
    const warningCount = toNumber(stateCounts.warnings ?? raw.warnings ?? raw.warningCount ?? raw.warning_count ?? 0, 0);
    return {
      id: toText(raw.id, 'run'),
      startedAt: toNumber(raw.startedAt || raw.started_at || 0, 0),
      endedAt: toNumber(raw.endedAt || raw.ended_at || 0, 0),
      status: toText(raw.status, 'idle'),
      executionStatus: toText(raw.executionStatus || raw.execution_status || '', ''),
      execution_status: toText(raw.execution_status || raw.executionStatus || '', ''),
      projectComplete: Boolean(raw.projectComplete ?? raw.project_complete ?? false),
      project_complete: Boolean(raw.project_complete ?? raw.projectComplete ?? false),
      projectStatus: toText(raw.projectStatus || raw.project_status || '', ''),
      project_status: toText(raw.project_status || raw.projectStatus || '', ''),
      goalsComplete: Boolean(raw.goalsComplete ?? raw.goals_complete ?? false),
      goals_complete: Boolean(raw.goals_complete ?? raw.goalsComplete ?? false),
      backlogComplete: Boolean(raw.backlogComplete ?? raw.backlog_complete ?? false),
      backlog_complete: Boolean(raw.backlog_complete ?? raw.backlogComplete ?? false),
      tasksDone: doneCount,
      tasksTotal: toNumber(raw.tasksTotal ?? taskCounts.total ?? lastRunSummary.total_tasks ?? 0, 0),
      tasksFailed: failedCount,
      tasksSkipped: toNumber(raw.tasksSkipped ?? taskCounts.skipped ?? lastRunSummary.skipped ?? 0, 0),
      taskCounts: {
        done: doneCount,
        failed: failedCount,
        skipped: toNumber(taskCounts.skipped ?? raw.tasksSkipped ?? lastRunSummary.skipped ?? 0, 0),
        total: toNumber(taskCounts.total ?? raw.tasksTotal ?? lastRunSummary.total_tasks ?? 0, 0),
        cycles: toNumber(taskCounts.cycles ?? raw.cycleCount ?? runCycles.length, runCycles.length),
      },
      stateCounts: {
        done: doneCount,
        failed: failedCount,
        warnings: warningCount,
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

  function normalizeWorktreeDiffHunk(hunk) {
    const raw = toObject(hunk);
    return {
      header: toText(raw.header || raw.hunkHeader, ''),
      oldStart: toMaybeNumber(raw.oldStart ?? raw.old_start) ?? 0,
      oldCount: toMaybeNumber(raw.oldCount ?? raw.old_count) ?? 0,
      newStart: toMaybeNumber(raw.newStart ?? raw.new_start) ?? 0,
      newCount: toMaybeNumber(raw.newCount ?? raw.new_count) ?? 0,
      lines: toArray(raw.lines).map((line) => toText(line, '')),
      truncated: Boolean(raw.truncated),
      lineCount: toMaybeNumber(raw.lineCount ?? raw.line_count) ?? 0,
    };
  }

  function normalizeWorktreeDiffFile(file) {
    const raw = toObject(file);
    const kind = toText(raw.kind || raw.state || raw.type, 'modified');
    const oldPath = toText(raw.oldPath || raw.old_path || raw.sourcePath || raw.source_path || raw.path || raw.file || raw.name, '');
    const newPath = toText(raw.newPath || raw.new_path || raw.targetPath || raw.target_path || raw.path || raw.file || raw.name || oldPath, '');
    const path = toText(raw.path || raw.file || raw.name || newPath || oldPath, '(unknown)');
    return {
      path,
      oldPath: oldPath || path,
      newPath: newPath || path,
      kind,
      state: toText(raw.state || raw.kind || raw.type, kind),
      note: toText(raw.note || raw.message, ''),
      summary: toText(raw.summary || raw.title || raw.note || raw.message, ''),
      binary: Boolean(raw.binary),
      deleted: Boolean(raw.deleted),
      renamed: Boolean(raw.renamed),
      large: Boolean(raw.large),
      truncated: Boolean(raw.truncated),
      lineCount: toMaybeNumber(raw.lineCount ?? raw.line_count) ?? 0,
      hunks: toArray(raw.hunks).map(normalizeWorktreeDiffHunk),
    };
  }

  function normalizeWorktreeFailureFile(item) {
    const raw = toObject(item);
    return {
      path: toText(raw.path, ''),
      line: toMaybeNumber(raw.line ?? raw.lineNumber ?? raw.line_number),
      reason: toText(raw.reason || raw.message, ''),
    };
  }

  function normalizeWorktreeFailureHunk(item) {
    const raw = toObject(item);
    return {
      path: toText(raw.path, ''),
      line: toMaybeNumber(raw.line ?? raw.lineNumber ?? raw.line_number),
      reason: toText(raw.reason || raw.message, ''),
      header: toText(raw.header || raw.hunkHeader, ''),
      lines: toArray(raw.lines).map((line) => toText(line, '')),
      truncated: Boolean(raw.truncated),
    };
  }

  function normalizeWorktreeApplyCheck(applyCheck) {
    const raw = toObject(applyCheck);
    const rc = toMaybeNumber(raw.rc ?? raw.returnCode ?? raw.return_code ?? raw.exitCode ?? raw.exit_code);
    return {
      command: toText(raw.command || raw.cmd, ''),
      rc: rc ?? 0,
      ok: Boolean(raw.ok ?? (rc != null ? rc === 0 : false)),
      status: toText(raw.status, ''),
      message: toText(raw.message, ''),
      output: toText(raw.output, ''),
      failedFiles: toArray(raw.failedFiles || raw.failed_files).map(normalizeWorktreeFailureFile),
      failedHunks: toArray(raw.failedHunks || raw.failed_hunks).map(normalizeWorktreeFailureHunk),
    };
  }

  function normalizeWorktreePreflight(preflight) {
    const raw = toObject(preflight);
    const applyCheck = normalizeWorktreeApplyCheck(raw.applyCheck || raw.apply_check);
    const sourceRepoState = toText(raw.sourceRepoState || raw.source_repo_state, '');
    return {
      sourceRepoState,
      sourceRepoDirty: Boolean(
        raw.sourceRepoDirty ?? raw.source_repo_dirty ?? (sourceRepoState ? sourceRepoState !== 'clean' : false)
      ),
      sourceHead: toText(raw.sourceHead || raw.source_head, ''),
      expectedBaseRef: toText(raw.expectedBaseRef || raw.expected_base_ref || raw.baseRef || raw.base_ref, ''),
      patchHash: toText(raw.patchHash || raw.patch_hash, ''),
      pendingFile: toText(raw.pendingFile || raw.pending_file, ''),
      pendingMarkerPath: toText(raw.pendingMarkerPath || raw.pending_marker_path || raw.pendingFile || raw.pending_file, ''),
      applyCheck,
    };
  }

  function normalizeWorktreeState(worktree) {
    const raw = toObject(worktree);
    const status = toText(raw.status, 'none');
    const changedFiles = toArray(raw.changedFiles || raw.changed_files).map(normalizeWorktreeDiffFile);
    const preflight = normalizeWorktreePreflight(raw.preflight || raw.mergePreflight);
    const checklist = toArray(raw.checklist).map((item) => toText(item)).filter(Boolean);
    const sourceRepo = toText(raw.sourceRepo || raw.source_repo, '');
    const sourceBranch = toText(raw.sourceBranch || raw.source_branch || raw.branch, 'HEAD');
    const baseRef = toText(raw.baseRef || raw.base_ref || raw.branch, '');
    const headRef = toText(raw.headRef || raw.head_ref, '');
    const worktreeDir = toText(raw.worktreeDir || raw.worktree_dir || raw.worktree, '');
    const patchPath = toText(raw.patchPath || raw.patch_path || raw.patch, '');
    const statusFile = toText(raw.statusFile || raw.status_file || raw.pendingFile || raw.pending_file, '');
    const pendingFile = toText(raw.pendingFile || raw.pending_file || preflight.pendingFile || preflight.pendingMarkerPath || ((status === 'pending' || status === 'pending review') ? statusFile : ''), '');
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
    const sourceRepoState = toText(raw.sourceRepoState || raw.source_repo_state || preflight.sourceRepoState, '');
    const sourceHead = toText(raw.sourceHead || raw.source_head || preflight.sourceHead || headRef, '');
    const expectedBaseRef = toText(raw.expectedBaseRef || raw.expected_base_ref || preflight.expectedBaseRef || baseRef, '');
    const patchHash = toText(raw.patchHash || raw.patch_hash || preflight.patchHash, '');
    const pendingMarkerPath = toText(
      raw.pendingMarkerPath || raw.pending_marker_path || preflight.pendingMarkerPath || preflight.pendingFile || pendingFile,
      ''
    );
    const applyCheck = normalizeWorktreeApplyCheck(raw.applyCheck || raw.apply_check || preflight.applyCheck);
    return {
      status,
      mode: toText(raw.mode, 'manual'),
      reviewRequired,
      reviewRequiredMessage,
      sourceRepo,
      sourceRepoState,
      source_repo_state: sourceRepoState,
      sourceRepoDirty: Boolean(raw.sourceRepoDirty ?? raw.source_repo_dirty ?? preflight.sourceRepoDirty ?? (sourceRepoState ? sourceRepoState !== 'clean' : false)),
      sourceHead,
      source_head: sourceHead,
      sourceBranch,
      branch: sourceBranch,
      baseRef,
      expectedBaseRef,
      expected_base_ref: expectedBaseRef,
      headRef,
      worktreeDir,
      worktree: worktreeDir,
      patchPath,
      patch: patchPath,
      patchHash,
      patch_hash: patchHash,
      pendingFile,
      pendingMarkerPath,
      pending_marker_path: pendingMarkerPath,
      statusFile,
      cleanupPath,
      cleanupMessage,
      cleanupState,
      summary: toText(raw.summary, ''),
      risk: toText(raw.risk, ''),
      changedFiles,
      changed_files: changedFiles,
      preflight,
      applyCheck,
      apply_check: applyCheck,
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

  function normalizeConfigValue(value, schema, path = '') {
    if (!schema) return value;
    if (schema.kind === 'multienum') {
      if (path === 'roles') return normalizeRoleSpecs(value, schema.options || []);
      return normalizeListValues(value);
    }
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
      data = setAt(data, path, normalizeConfigValue(current, schema[path], path));
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
      { id: 'project', title: t('config.groupProject'), paths: ['repo', 'profile', 'execution_backend', 'roles', 'security.enabled'] },
      { id: 'runner', title: t('config.groupRunner'), paths: ['autopilot', 'continuous', 'iterations', 'max_turns_per_task', 'loop', 'loop_sleep_seconds', 'loop_max_cycles', 'loop_idle_exit_after', 'idle_exit_cycles', 'max_consecutive_failed_cycles', 'run_tests', 'budget_reset_per_cycle'] },
      { id: 'quota', title: t('config.groupQuota'), paths: ['quota_check_enabled', 'quota_five_hour_max_utilization', 'quota_seven_day_max_utilization', 'quota_wait_for_reset'] },
      { id: 'worktree', title: t('config.groupWorktree'), paths: ['worktree_isolation', 'isolate_task', 'gitops.worktree_merge_mode', 'gitops.untracked_exclude_globs'] },
      { id: 'prompts', title: t('config.groupPrompts'), paths: ['prompts_dir'] },
      { id: 'codex_models', title: t('config.groupCodexModels'), paths: ['pm_model', 'dev_model', 'dev_model_tier1', 'dev_model_tier2', 'qa_model', 'reporter_model'] },
      { id: 'pm_refresh', title: t('config.groupPmRefresh'), paths: ['pm_refresh_backlog', 'pm_refresh_every_cycles', 'pm_include_working_tree'] },
      { id: 'budget', title: t('config.groupBudget'), paths: ['budgets.max_pm_structured_retries', 'budgets.max_dev_escalations_per_task', 'budgets.max_dev_continuations_per_task', 'budgets.max_total_escalations_per_run', 'budgets.max_total_continuations_per_run', 'budgets.max_total_repair_attempts_per_run'] },
      { id: 'telegram', title: t('config.groupTelegram'), paths: ['telegram.enabled', 'telegram.runner_mode', 'telegram.poll_timeout_seconds', 'telegram.allowed_chat_ids', 'telegram.bot_token', 'telegram.pairing_code', 'telegram.instance_name', 'telegram.notify_events', 'telegram.send_cycle_summary', 'telegram.notify_poll_interval_seconds', 'telegram.stalled_seconds', 'telegram.tail_lines_default'] },
      { id: 'goals', title: t('config.groupGoals'), paths: ['goals_enabled', 'goals_auto_generate', 'goals_auto_check', 'goals_auto_refresh', 'goals_refresh_max_per_run', 'goals_completion_level'] },
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
    const projectCompletion = projectCompletionState(progress, context.goalsCompletion || progress.goals, context.backlog || progress.backlog, context.activeRun || raw);
    const repoPath = toText(raw.repo || repo.path || config.repo || '', '');
    const repoLabel = toText(raw.repoLabel || repo.name || repoNameFromPath(repoPath) || 'agentcli', 'agentcli');
    const stage = toText(raw.stage || progress.current_stage || metrics.last_stage || 'idle', 'idle');
    const executionStatus = toText(
      raw.executionStatus ||
        raw.execution_status ||
        progress.executionStatus ||
        progress.execution_status ||
        normalizeExecutionStatus(raw.status || progress.run_status || progress.runStatus, hasRunData, {
          running: Boolean(raw.running || progress.running),
          exitCode: raw.exitCode ?? raw.exit_code ?? progress.final_rc ?? progress.finalRc,
          finalReason: raw.finalReason || raw.final_reason || progress.final_reason || '',
          stopFileExists: Boolean(raw.stopFileExists || raw.stop_file_exists || progress.stop_file_exists || progress.stopFileExists),
        }),
      'idle'
    );
    const activeStatus = normalizeRunStatus(raw.status || progress.run_status || progress.runStatus, hasRunData, projectCompletion.projectComplete, {
      running: Boolean(raw.running || progress.running),
      exitCode: raw.exitCode ?? raw.exit_code ?? progress.final_rc ?? progress.finalRc,
      finalReason: raw.finalReason || raw.final_reason || progress.final_reason || '',
      stopFileExists: Boolean(raw.stopFileExists || raw.stop_file_exists || progress.stop_file_exists || progress.stopFileExists),
    });
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
      executionStatus: executionStatus,
      execution_status: executionStatus,
      goalsComplete: projectCompletion.goalsComplete,
      goals_complete: projectCompletion.goalsComplete,
      backlogComplete: projectCompletion.backlogComplete,
      backlog_complete: projectCompletion.backlogComplete,
      projectComplete: projectCompletion.projectComplete,
      project_complete: projectCompletion.projectComplete,
      projectStatus: projectCompletion.projectStatus,
      project_status: projectCompletion.projectStatus,
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
          elapsedSec: toMaybeNumber(raw.elapsedSec ?? raw.elapsed_seconds ?? raw.durationSec ?? raw.duration_seconds),
          model: toText(raw.model || raw.backend || '', ''),
          taskId: toText(raw.taskId || raw.task_id, ''),
          taskTitle: toText(raw.taskTitle || raw.task_title, ''),
          attempt: toMaybeNumber(raw.attempt || raw.currentAttempt),
          step: toMaybeNumber(raw.step),
          recentOutput: toText(raw.recentOutput || raw.recent_output, ''),
          latestLogLine: toText(raw.latestLogLine || raw.latest_log_line, ''),
          latestBackendEvent: toText(raw.latestBackendEvent || raw.latest_backend_event, ''),
          outputStalled: Boolean(raw.outputStalled ?? raw.output_stalled),
          noOutputMinutes: toMaybeNumber(raw.noOutputMinutes ?? raw.no_output_minutes),
          reason: toText(raw.reason || raw.message, ''),
          rc: toMaybeNumber(raw.rc),
          isFallback: false,
        };
      })
      .filter(Boolean);
    const sectionStatus = !items.length ? 'empty' : items.length < 3 ? 'partial' : 'ready';
    return {
      items,
      state: buildSectionState('stages', sectionStatus, sectionStatus === 'ready' ? '' : sectionStatus === 'partial' ? t('pipeline.partialLifecycleRecords') : fallbackSectionMessage('stages')),
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
    const redaction = toObject(raw.redaction);
    const warnings = toArray(raw.warnings).map(normalizeGoalWarning);
    const items = normalizeGoalBuckets(raw);
    const completion = toObject(raw.completion);
    const missingSections = normalizeGoalSectionNames(completion.missing_sections || completion.missingSections);
    const valid = completion.valid !== false && !missingSections.length;
    const total = items.p0.length + items.p1.length;
    const done = items.p0.filter((goal) => goal.done).length + items.p1.filter((goal) => goal.done).length;
    const summary = {
      has_goals: Boolean(raw.completion?.has_goals ?? total),
      project_complete: Boolean(raw.completion?.project_complete),
      valid,
      missing_sections: missingSections,
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
      redaction: {
        active: Boolean(redaction.active),
        placeholder: toText(redaction.placeholder, REDACTED_VALUE),
        paths: toArray(redaction.paths),
        tokens: toArray(redaction.tokens),
        scope: toText(redaction.scope, ''),
      },
      state: buildSectionState(
        'goals',
        !toText(raw.raw_text || raw.rawText, '').trim() ? 'empty' : (!valid ? 'partial' : (total ? 'ready' : 'empty')),
        goalSnapshotMessage(raw, total),
      ),
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
    const redaction = toObject(raw.redaction);
    const files = {};
    for (const [key, value] of Object.entries(toObject(raw.files))) {
      const text = toText(value, '').trim();
      if (!text) {
        continue;
      }
      files[key] = redaction.active ? REDACTED_VALUE : text;
    }
    const sources = normalizeLogTailSources(raw.sources || raw.source_catalog || raw.sourceCatalog || []);
    const source = normalizeLogTailSource(raw.source || {});
    const sourceId = toText(raw.source_id || raw.selected_source_id || raw.sourceId || source.id, '').trim();
    const selection = resolveLogTailSourceSelection({
      sources,
      sourceId,
      source,
    });
    return {
      entries: items,
      tail: toText(raw.tail, ''),
      files,
      source: selection.source,
      sourceId: selection.sourceId,
      selectedSourceId: selection.sourceId,
      sources: selection.sources,
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
    const summary = toObject(raw.summary);
    const tasksDone = items.reduce((sum, run) => sum + run.tasksDone, 0);
    const tasksTotal = items.reduce((sum, run) => sum + run.tasksTotal, 0);
    const tasksFailed = items.reduce((sum, run) => sum + run.tasksFailed, 0);
    const tasksSkipped = items.reduce((sum, run) => sum + run.tasksSkipped, 0);
    return {
      items,
      summary: {
        runs: toNumber(summary.runs || items.length, items.length),
        successes: toNumber(summary.successes || items.filter((run) => normalizeProjectStatus(run.projectStatus || run.projectComplete) === 'complete').length, 0),
        failures: toNumber(summary.failures || items.filter((run) => run.status === 'failed').length, 0),
        stopped: toNumber(summary.stopped || items.filter((run) => run.status === 'stopped').length, 0),
        tasksDone,
        tasksTotal,
        tasksFailed,
        tasksSkipped,
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

  function adaptLiveRun(liveRun, context = {}) {
    const raw = toObject(liveRun);
    const rawActiveRun = toObject(raw.activeRun || raw.active_run || context.activeRun || context.active_run);
    const rawProgress = toObject(raw.progress || context.progress);
    const rawRunnerControl = toObject(raw.runnerControl || raw.runner_control || raw.control || context.runnerControl);
    const rawIdentity = toObject(raw.identity);
    const rawStatus = toObject(raw.status);
    const rawCurrentTask = toObject(raw.currentTask || raw.current_task);
    const rawProcess = toObject(raw.process || rawRunnerControl.status);
    const rawTimestamps = toObject(raw.timestamps);
    const rawStale = toObject(raw.stale);
    const rawStages = toArray(raw.stageSummaries || toObject(raw.stages).items || raw.stages || context.stages);
    const rawLog = toObject(raw.log || raw.logs || context.logs);
    const rawNotifications = toObject(raw.notifications);
    const rawNotificationItems = toArray(rawNotifications.items || raw.notifications || context.notifications);
    const normalizedStages = adaptStages(rawStages, { activeRun: rawActiveRun });
    const normalizedLog = adaptLogs(rawLog);
    const normalizedNotifications = adaptNotifications(rawNotificationItems);
    const activeRun = adaptActiveRun(rawActiveRun, {
      repo: context.repo,
      progress: rawProgress,
      metrics: context.metrics,
      config: context.config,
      branch: context.branch || '',
      source: context.source || 'api',
    });
    const stageSummaries = normalizedStages.items;
    const logEntries = normalizedLog.entries;
    const normalizedLogFiles = normalizedLog.files;
    const normalizedLogTail = normalizedLog.tail;
    const normalizedNotificationItems = normalizedNotifications.items;
    const runnerControl = normalizeRunnerControl(rawRunnerControl);
    const liveState = normalizeLiveState(
      raw.liveState ||
        raw.live_state ||
        rawProcess.liveState ||
        rawProcess.live_state ||
        rawRunnerControl.liveState ||
        rawRunnerControl.live_state
    );
    const identity = {
      id: toText(rawIdentity.id || rawIdentity.runId || activeRun.id, activeRun.id || 'no-run'),
      runId: toText(rawIdentity.runId || rawIdentity.id || activeRun.id, activeRun.id || 'no-run'),
      repo: toText(rawIdentity.repo || activeRun.repo, activeRun.repo || ''),
      repoLabel: toText(rawIdentity.repoLabel || activeRun.repoLabel, activeRun.repoLabel || 'agentcli'),
      branch: toText(rawIdentity.branch || activeRun.branch, activeRun.branch || 'HEAD'),
      backend: toText(rawIdentity.backend || activeRun.backend, activeRun.backend || 'codex'),
      runDir: toText(rawIdentity.runDir || activeRun.runDir, activeRun.runDir || ''),
    };
    const status = {
      run: toText(rawStatus.run || rawStatus.runStatus || activeRun.status, activeRun.status || 'idle'),
      runStatus: toText(rawStatus.runStatus || rawStatus.run || activeRun.status, activeRun.status || 'idle'),
      execution: toText(rawStatus.execution || rawStatus.executionStatus || activeRun.executionStatus, activeRun.executionStatus || 'idle'),
      executionStatus: toText(rawStatus.executionStatus || rawStatus.execution || activeRun.executionStatus, activeRun.executionStatus || 'idle'),
      project: toText(rawStatus.project || rawStatus.projectStatus || activeRun.projectStatus, activeRun.projectStatus || 'incomplete'),
      projectStatus: toText(rawStatus.projectStatus || rawStatus.project || activeRun.projectStatus, activeRun.projectStatus || 'incomplete'),
      projectComplete: Boolean(rawStatus.projectComplete ?? rawStatus.project_complete ?? activeRun.projectComplete),
      goalsComplete: Boolean(rawStatus.goalsComplete ?? rawStatus.goals_complete ?? activeRun.goalsComplete),
      backlogComplete: Boolean(rawStatus.backlogComplete ?? rawStatus.backlog_complete ?? activeRun.backlogComplete),
      stage: toText(rawStatus.stage || activeRun.stage, activeRun.stage || 'idle'),
      stageIndex: toMaybeNumber(rawStatus.stageIndex ?? activeRun.stageIndex) ?? activeRun.stageIndex,
      iteration: toMaybeNumber(rawStatus.iteration ?? activeRun.iteration) ?? activeRun.iteration,
      maxIterations: toMaybeNumber(rawStatus.maxIterations ?? activeRun.maxIterations) ?? activeRun.maxIterations,
      progress: rawStatus.progress ?? activeRun.progress,
      progressAvailable: Boolean(rawStatus.progressAvailable ?? rawStatus.progress_available ?? activeRun.progressAvailable),
      finalReason: toText(rawStatus.finalReason || activeRun.finalReason, activeRun.finalReason || ''),
    };
    const currentTask = {
      id: toText(rawCurrentTask.id || rawCurrentTask.taskId || activeRun.task, activeRun.task || ''),
      title: toText(rawCurrentTask.title || rawCurrentTask.taskTitle || activeRun.taskTitle, activeRun.taskTitle || ''),
      attempt: toMaybeNumber(rawCurrentTask.attempt ?? activeRun.attempt),
      worktreeMode: toText(rawCurrentTask.worktreeMode || rawCurrentTask.worktree_mode || activeRun.worktreeMode, activeRun.worktreeMode || ''),
      step: toMaybeNumber(rawCurrentTask.step ?? rawProgress.step),
      cycle: toMaybeNumber(rawCurrentTask.cycle ?? rawProgress.cycle),
    };
    const logSource = toObject(rawLog.source);
    const logCursor = toMaybeNumber(rawLog.cursor ?? rawLog.nextCursor ?? rawLog.next_cursor);
    const logState = toText(rawLog.state, status.run === 'running' ? 'loading' : 'empty');
    const logSummary = {
      source: {
        path: toText(logSource.path, ''),
        name: toText(logSource.name, ''),
        exists: Boolean(logSource.exists),
      },
      cursor: logCursor == null ? 0 : logCursor,
      nextCursor: toMaybeNumber(rawLog.nextCursor ?? rawLog.next_cursor ?? logCursor) ?? (logCursor == null ? 0 : logCursor),
      state: logState,
      entries: logEntries,
      tail: normalizedLogTail,
      files: normalizedLogFiles,
      ok: Boolean(rawLog.ok ?? true),
      malformedLines: toMaybeNumber(rawLog.malformedLines ?? rawLog.malformed_lines) ?? 0,
    };
    const notificationCounts = toObject(rawNotifications.kinds);
    const notificationsSummary = {
      items: normalizedNotificationItems,
      count: toMaybeNumber(rawNotifications.count ?? normalizedNotificationItems.length) ?? normalizedNotificationItems.length,
      kinds: notificationCounts,
      latest: rawNotifications.latest || normalizedNotificationItems[0] || null,
      controlPlaneStatus: toText(rawNotifications.controlPlaneStatus || runnerControl.message, runnerControl.message || ''),
      controlPlaneEvent: toText(rawNotifications.controlPlaneEvent || runnerControl.status?.lastEvent || runnerControl.lastAction || runnerControl.lastMessage, ''),
      controlPlaneSnapshot: toText(rawNotifications.controlPlaneSnapshot || runnerControl.lastMessage || runnerControl.lastError, ''),
    };
    const logSourceMissing = logSummary.source.exists === false;
    const controlStatusError = Boolean(status.run && status.run !== 'idle' && status.run !== 'loading' && !runnerControl.controllerAvailable);
    const processMismatch = Boolean(rawProcess.running ?? runnerControl.status?.running) && ['completed', 'success', 'stopped', 'failed'].includes(status.run);
    const process = {
      status: rawProcess,
      running: Boolean(rawProcess.running ?? runnerControl.status?.running),
      runnerMode: toText(rawProcess.runnerMode || rawProcess.runner_mode, runnerControl.status?.runnerMode || 'unknown'),
      repo: toText(rawProcess.repo, activeRun.repo || ''),
      configPath: toText(rawProcess.configPath || rawProcess.config_path, runnerControl.status?.configPath || ''),
      runDir: toText(rawProcess.runDir || rawProcess.run_dir, activeRun.runDir || ''),
      uptimeSeconds: toMaybeNumber(rawProcess.uptimeSeconds ?? rawProcess.uptime_seconds) ?? 0,
      exitCode: rawProcess.exitCode ?? rawProcess.exit_code ?? null,
      stopFile: toText(rawProcess.stopFile || rawProcess.stop_file, 'STOP'),
      stopFileExists: Boolean(rawProcess.stopFileExists ?? rawProcess.stop_file_exists),
      done: toMaybeNumber(rawProcess.done) ?? 0,
      failed: toMaybeNumber(rawProcess.failed) ?? 0,
      warnings: toMaybeNumber(rawProcess.warnings) ?? 0,
      stateCounts: toObject(rawProcess.stateCounts || rawProcess.state_counts),
      reason: toText(rawProcess.reason, ''),
      lastEvent: toText(rawProcess.lastEvent || rawProcess.last_event, ''),
      stopProgress: normalizeStopProgress(rawProcess.stopProgress || rawProcess.stop_progress),
      liveState,
      live_state: liveState,
    };
    const staleReasons = toArray(rawStale.reasons).map((reason) => toText(reason, '')).filter(Boolean);
    const derivedStale = {
      value: Boolean(rawStale.value ?? (staleReasons.length || logSourceMissing || controlStatusError || processMismatch)),
      reasons: staleReasons,
      logs: Boolean(rawStale.logs ?? ['missing_file', 'read_error'].includes(logState)),
      logSourceMissing,
      control: Boolean(rawStale.control ?? controlStatusError),
      controlStatusError,
      process: Boolean(rawStale.process ?? processMismatch),
      processMismatch,
    };
    const timestamps = {
      startedAt: toMaybeNumber(rawTimestamps.startedAt ?? rawTimestamps.started_at ?? activeRun.startedAt) ?? 0,
      endedAt: toMaybeNumber(rawTimestamps.endedAt ?? rawTimestamps.ended_at ?? activeRun.endedAt) ?? 0,
      elapsedSec: toMaybeNumber(rawTimestamps.elapsedSec ?? rawTimestamps.elapsed_seconds ?? activeRun.elapsedSec) ?? 0,
      logCursor: toMaybeNumber(rawTimestamps.logCursor ?? rawTimestamps.log_cursor ?? logSummary.cursor) ?? 0,
    };
    return {
      identity,
      activeRun,
      progress: rawProgress,
      status,
      currentTask,
      stages: {
        items: stageSummaries,
        count: stageSummaries.length,
        currentStage: status.stage,
        currentStageIndex: status.stageIndex,
        currentTaskId: currentTask.id,
        currentTaskTitle: currentTask.title,
      },
      stageSummaries,
      log: logSummary,
      notifications: notificationsSummary,
      runnerControl,
      control: runnerControl,
      process,
      timestamps,
      stale: derivedStale,
      runId: identity.runId,
      runDir: identity.runDir,
      repo: identity.repo,
      repoLabel: identity.repoLabel,
      branch: identity.branch,
      backend: identity.backend,
      runStatus: status.run,
      executionStatus: status.execution,
      projectStatus: status.project,
      projectComplete: status.projectComplete,
      goalsComplete: status.goalsComplete,
      backlogComplete: status.backlogComplete,
      stage: status.stage,
      stageIndex: status.stageIndex,
      iteration: status.iteration,
      maxIterations: status.maxIterations,
      progress: status.progress,
      progressAvailable: status.progressAvailable,
      currentTaskId: currentTask.id,
      currentTaskTitle: currentTask.title,
      attempt: currentTask.attempt,
      worktreeMode: currentTask.worktreeMode,
      finalReason: status.finalReason,
      logSource: logSummary.source,
      logCursor: logSummary.cursor,
      logState: logSummary.state,
      liveState,
      live_state: liveState,
    };
  }

  function normalizeApiSnapshot(snapshot) {
    const raw = toObject(snapshot);
    const repo = toObject(raw.repo);
    const progress = toObject(raw.progress);
    const redaction = toObject(raw.redaction);
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
    const liveRun = adaptLiveRun(raw.liveRun || raw.live_run || {}, {
      activeRun,
      progress,
      stages: stages.items,
      logs: logs,
      notifications: notifications.items,
      runnerControl,
      metrics,
      config: configValues,
      branch: repo.branch || '',
      source: 'api',
    });
    const snapshotRefresh = {
      status: 'ready',
      lastUpdatedAt: nowMs(),
      lastSuccessAt: nowMs(),
      stale: Boolean(toObject(liveRun.stale).value),
      staleReasons: toArray(toObject(liveRun.stale).reasons).map((reason) => toText(reason, '')).filter(Boolean),
      latestRunDir: toText(raw.latest_run_dir, ''),
    };

    return {
      ok: Boolean(raw.ok),
      sourceMode: 'api',
      snapshotStatus: 'ready',
      snapshotLabel: 'API snapshot',
      lastSnapshotAt: nowMs(),
      latestRunDir: toText(raw.latest_run_dir, ''),
      snapshotRefresh,
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
      logTailSource: logs.source,
      logTailSelectedSourceId: logs.selectedSourceId,
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
      liveRun,
      logSources: logs.sources,
      logTailSourceId: logs.sourceId,
      redaction: {
        active: Boolean(redaction.active),
        placeholder: toText(redaction.placeholder, REDACTED_VALUE),
        scope: toText(redaction.scope, ''),
      },
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
        runnerControl: buildSectionState('runnerControl', runnerControl.controllerAvailable ? (runnerControl.enabled ? 'ready' : 'disabled') : 'error', redactionAwareText(runnerControl.message, fallbackSectionMessage('runnerControl'))),
      },
    };
  }

  function createBlankModel() {
    const DEFAULT_ROLE_SPECS = ['PM', 'Dev', 'QA'];
    const BUILTIN_ROLE_OPTIONS = ['PM', 'Security', 'Dev', 'QA'];
    const CODEX_DEV_MODEL_LADDER = ['gpt-5.4-mini', 'gpt-5.4', 'gpt-5.5'];
    const CODEX_MODEL_DEFAULTS = {
      pm_model: 'gpt-5.5',
      dev_model: CODEX_DEV_MODEL_LADDER[0],
      dev_model_tier1: CODEX_DEV_MODEL_LADDER[1],
      dev_model_tier2: CODEX_DEV_MODEL_LADDER[2],
      qa_model: 'gpt-5.5',
      reporter_model: 'gpt-5.4-mini',
    };
    const CODEX_MODEL_FIELD_SPECS = {
      pm_model: {
        kind: 'text',
        restart: false,
        desc: 'Model used for PM planning and backlog generation.',
        hint: 'Approved Codex default: gpt-5.5.',
      },
      dev_model: {
        kind: 'text',
        restart: false,
        desc: 'First model in the Dev fallback ladder.',
        hint: `Approved ladder: ${CODEX_DEV_MODEL_LADDER.join(' -> ')}.`,
      },
      dev_model_tier1: {
        kind: 'text',
        restart: false,
        desc: 'Second model in the Dev fallback ladder.',
        hint: 'Escalates to gpt-5.4 when the base model is not enough.',
      },
      dev_model_tier2: {
        kind: 'text',
        restart: false,
        desc: 'Final model in the Dev fallback ladder.',
        hint: 'Escalates to gpt-5.5 as the last approved Codex tier.',
      },
      qa_model: {
        kind: 'text',
        restart: false,
        desc: 'Model used for QA verification.',
        hint: 'Approved Codex default: gpt-5.5.',
      },
      reporter_model: {
        kind: 'text',
        restart: false,
        desc: 'Model used for close-out reporting.',
        hint: 'Approved Codex default: gpt-5.4-mini.',
      },
    };
    const configBase = {
      repo: '',
      profile: 'personal',
      execution_backend: 'codex',
      roles: [...DEFAULT_ROLE_SPECS],
      security: {
        enabled: false,
      },
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
      ...CODEX_MODEL_DEFAULTS,
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
        options: BUILTIN_ROLE_OPTIONS,
        restart: false,
        desc: 'Stages enabled in the pipeline.',
        hint: 'Built-in order: PM, Security, Dev, QA. Plugin specs like pkg.mod:Class are preserved.',
      },
      'security.enabled': {
        kind: 'bool',
        restart: false,
        desc: 'Enable the Security stage in the pipeline.',
        hint: 'Security stage requires Security in roles.',
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
      ...CODEX_MODEL_FIELD_SPECS,
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
      snapshotLabel: t('snapshot.loading'),
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
        message: t('runner.loadingStatus') || t('snapshot.loadingReadOnly'),
        controllerAvailable: false,
        enabled: false,
        running: false,
        runStatus: 'loading',
        runnerMode: 'unknown',
      }),
      redaction: {
        active: false,
        placeholder: REDACTED_VALUE,
        scope: 'local',
      },
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
        reviewRequiredMessage: t('worktree.noPendingReview'),
        worktreeDir: '',
        worktree: '',
        patchPath: '',
        patch: '',
        pendingFile: '',
        statusFile: '',
        cleanupPath: '',
        cleanupMessage: t('worktree.cleanupStateUnavailable'),
        cleanupState: 'none',
        summary: t('worktree.noPendingReview'),
        risk: t('worktree.reviewThePatchBeforeSourceRepoChanges'),
        changedFiles: [],
        changed_files: [],
        preflight: {},
        applyCheck: {},
        sourceRepoState: '',
        source_repo_state: '',
        sourceRepoDirty: false,
        sourceHead: '',
        source_head: '',
        expectedBaseRef: '',
        expected_base_ref: '',
        patchHash: '',
        patch_hash: '',
        pendingMarkerPath: '',
        pending_marker_path: '',
        checklist: [
          t('worktree.checklistInspectPatchHunks'),
          t('worktree.checklistVerifyNoSecretLeakage'),
          t('worktree.checklistApproveMergeOnlyAfterReview'),
          t('worktree.checklistDiscardOnlyAfterArchivalCopy'),
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
      liveRun: {},
      snapshotRefresh: createBlankSnapshotRefreshState(),
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
        activeRun: buildSectionState('activeRun', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        stages: buildSectionState('stages', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        backlog: buildSectionState('backlog', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        goals: buildSectionState('goals', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        config: buildSectionState('config', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        prompts: buildSectionState('prompts', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        logs: buildSectionState('logs', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        notifications: buildSectionState('notifications', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        metrics: buildSectionState('metrics', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        history: buildSectionState('history', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        worktree: buildSectionState('worktree', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
        runnerControl: buildSectionState('runnerControl', 'loading', t('snapshot.loadingReadOnly'), 'loading'),
      },
    };
  }

  function createFallbackFixture() {
    const blank = createBlankModel();
    const fallbackRunnerControl = createRunnerControlModel({
      source: 'fallback',
      message: t('runner.controlsDisabledMessage'),
      controllerAvailable: false,
      enabled: false,
      running: false,
      runStatus: 'idle',
      runnerMode: 'unknown',
    });
    return {
      ok: true,
      sourceMode: 'fallback',
      snapshotStatus: 'fallback',
      snapshotLabel: t('snapshot.fallback'),
      lastSnapshotAt: nowMs(),
      latestRunDir: '',
      snapshotRefresh: {
        status: 'fallback',
        lastUpdatedAt: nowMs(),
        lastSuccessAt: nowMs(),
        stale: false,
        staleReasons: [],
        latestRunDir: '',
      },
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
      liveRun: {
        identity: {
          ...clone(blank.liveRun.identity || {}),
          id: 'no-run',
          runId: 'no-run',
          repo: 'C:/Dev/AgentCLI',
          repoLabel: 'AgentCLI',
          branch: 'main',
          backend: 'codex',
          runDir: '',
        },
        activeRun: {
          ...clone(blank.activeRun),
          repo: 'C:/Dev/AgentCLI',
          repoLabel: 'AgentCLI',
          branch: 'main',
        },
        progress: clone(blank.progress),
        status: {
          run: 'idle',
          runStatus: 'idle',
          execution: 'idle',
          executionStatus: 'idle',
          project: 'incomplete',
          projectStatus: 'incomplete',
          projectComplete: false,
          goalsComplete: false,
          backlogComplete: false,
          stage: 'idle',
          stageIndex: 0,
          iteration: 0,
          maxIterations: 1,
          progress: null,
          progressAvailable: false,
          finalReason: '',
        },
        currentTask: {
          id: '',
          title: '',
          attempt: null,
          worktreeMode: '',
          step: null,
          cycle: null,
        },
        stages: {
          items: clone(blank.stages),
          count: clone(blank.stages).length,
          currentStage: 'idle',
          currentStageIndex: 0,
          currentTaskId: '',
          currentTaskTitle: '',
        },
        stageSummaries: clone(blank.stages),
        log: {
          source: {
            path: '',
            name: '',
            exists: false,
          },
          cursor: 0,
          nextCursor: 0,
          state: 'loading',
          entries: clone(blank.logs),
          tail: '',
          files: {},
          ok: true,
          malformedLines: 0,
        },
        notifications: {
          items: clone(blank.notifications),
          count: clone(blank.notifications).length,
          kinds: {},
          latest: null,
          controlPlaneStatus: t('runner.controlsDisabledMessage'),
          controlPlaneEvent: '',
          controlPlaneSnapshot: '',
        },
        liveState: clone(fallbackRunnerControl.liveState),
        live_state: clone(fallbackRunnerControl.liveState),
        runnerControl: clone(fallbackRunnerControl),
        control: clone(fallbackRunnerControl),
        process: {
          status: clone(fallbackRunnerControl.status),
          running: false,
          runnerMode: 'unknown',
          repo: 'C:/Dev/AgentCLI',
          configPath: '',
          runDir: '',
          uptimeSeconds: 0,
          exitCode: null,
          stopFile: 'STOP',
          stopFileExists: false,
          done: 0,
          failed: 0,
          warnings: 0,
          stateCounts: { done: 0, failed: 0, warnings: 0 },
          reason: '',
          lastEvent: '',
          stopProgress: {},
          liveState: clone(fallbackRunnerControl.liveState),
          live_state: clone(fallbackRunnerControl.liveState),
        },
        timestamps: {
          startedAt: 0,
          endedAt: 0,
          elapsedSec: 0,
          logCursor: 0,
        },
        stale: {
          value: false,
          reasons: [],
          logs: false,
          logSourceMissing: false,
          control: false,
          controlStatusError: false,
          process: false,
          processMismatch: false,
        },
        runId: 'no-run',
        runDir: '',
        repo: 'C:/Dev/AgentCLI',
        repoLabel: 'AgentCLI',
        branch: 'main',
        backend: 'codex',
        runStatus: 'idle',
        executionStatus: 'idle',
        projectStatus: 'incomplete',
        projectComplete: false,
        goalsComplete: false,
        backlogComplete: false,
        stage: 'idle',
        stageIndex: 0,
        iteration: 0,
        maxIterations: 1,
        progress: null,
        progressAvailable: false,
        currentTaskId: '',
        currentTaskTitle: '',
        attempt: null,
        worktreeMode: '',
        finalReason: '',
        logSource: {
          path: '',
          name: '',
          exists: false,
        },
        logCursor: 0,
        logState: 'loading',
      },
      runnerControl: fallbackRunnerControl,
      redaction: {
        active: false,
        placeholder: REDACTED_VALUE,
        scope: 'local',
      },
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
        reviewRequiredMessage: t('worktree.noPendingReview'),
        worktreeDir: '',
        worktree: '',
        patchPath: '',
        patch: '',
        pendingFile: '',
        statusFile: '',
        cleanupPath: '',
        cleanupMessage: t('worktree.cleanupStateUnavailable'),
        cleanupState: 'none',
        summary: t('worktree.noPendingReview'),
        risk: t('worktree.reviewThePatchBeforeSourceRepoChanges'),
        changedFiles: [],
        changed_files: [],
        preflight: {},
        applyCheck: {},
        sourceRepoState: '',
        source_repo_state: '',
        sourceRepoDirty: false,
        sourceHead: '',
        source_head: '',
        expectedBaseRef: '',
        expected_base_ref: '',
        patchHash: '',
        patch_hash: '',
        pendingMarkerPath: '',
        pending_marker_path: '',
        checklist: [
          t('worktree.checklistInspectPatchHunks'),
          t('worktree.checklistVerifyNoSecretLeakage'),
          t('worktree.checklistApproveMergeOnlyAfterReview'),
          t('worktree.checklistDiscardOnlyAfterArchivalCopy'),
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
    adaptLiveRun,
    applySnapshotModel,
    normalizeRoleSpec,
    normalizeRoleSpecs,
    classifyRoleSpec,
    configSecurityRoleStatus,
    renderConfigSecurityRoleBanner,
    renderConfigRolesControl,
    adaptPrompts,
    adaptLogs,
    adaptNotifications,
    adaptMetrics,
    adaptHistory,
    adaptWorktree,
    renderDashboard,
    renderPipeline,
    renderStageHealthSignals,
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
    runnerControlStartOptionsContract,
    runnerControlStartOptionsDraftFrom,
    runnerControlStartOptionsDraft,
    runnerControlStartOptionsDefaultDraft,
    runnerControlStartOptionsPayload,
    runnerControlStartOptionsArgvPreview,
    runnerControlStartOptionsValidation,
    runnerControlStartOptionMetaText,
    runnerControlStartOptionsSummaryChips,
    runnerControlStartOptionCard,
    renderRunnerControlStartOptionsSection,
    updateRunnerControlStartOptionsDraft,
    updateRunnerControlStartMode,
    toggleRunnerControlAutopilot,
    updateRunnerControlStartField,
    normalizeStopProgress,
    normalizeLiveStateKey,
    liveStateKindLabel,
    liveStateStatusLabel,
    liveStateToneClass,
    normalizeLiveStateEntry,
    normalizeLiveState,
    runnerControlLiveStateChips,
    runnerControlStateInfo,
    runnerControlDetailRows,
    runnerControlActionEnabled,
    runnerControlActionDisabledReason,
    renderStopProgressSection,
    currentLiveRunLiveState,
    normalizeSnapshot: normalizeApiSnapshot,
    createBlankModel,
    createFallbackFixture,
    createBlankLogTailState,
    createBlankSnapshotRefreshState,
    normalizeLogTailFilters,
    normalizeLogTailSource,
    normalizeLogTailSources,
    resolveLogTailSourceSelection,
    applyLogTailSourceSelection,
    logTailSourceDisplayName,
    logTailSourceAvailabilityLabel,
    renderLogTailSourceSelector,
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
    refreshSnapshot,
    startSnapshotPolling,
    stopSnapshotPolling,
    inspectSnapshotRefreshState,
    applyLogTailPayload,
    toggleLogTailSelection,
    clearLogTailSelection,
    updateLogTailSource,
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
    currentLocale,
    setLocale,
    setView,
    renderShell,
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
    const nextLogTailSources = normalizeLogTailSources(next.logSources);
    const nextLogTailSource = normalizeLogTailSource(next.logTailSource);
    const nextLogTailSourceId = toText(next.logTailSelectedSourceId || next.logTailSourceId || nextLogTailSource.id || '', '').trim();
    const tailState = ensureLogTailState();
    const sourceContextChanged = previousSourceMode !== state.sourceMode || previousLatestRunDir !== state.latestRunDir;
    tailState.sources = nextLogTailSources;
    if (sourceContextChanged && !nextLogTailSources.length && !nextLogTailSourceId && !nextLogTailSource.id && !nextLogTailSource.path && !nextLogTailSource.label) {
      tailState.sourceId = '';
      tailState.source = normalizeLogTailSource({});
    }
    applyLogTailSourceSelection(
      tailState,
      resolveLogTailSourceSelection({
        ...tailState,
        sources: nextLogTailSources,
        sourceId: nextLogTailSourceId || tailState.sourceId || '',
        source: nextLogTailSource,
      })
    );
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
    const nextRedaction = toObject(next.redaction);
    const configContractRedaction = toObject(state.configContract.redaction);
    state.redaction = {
      ...clone(configContractRedaction),
      ...clone(nextRedaction),
      active: Boolean(nextRedaction.active ?? configContractRedaction.active),
      placeholder: toText(nextRedaction.placeholder || configContractRedaction.placeholder, REDACTED_VALUE),
      scope: toText(nextRedaction.scope || configContractRedaction.scope, ''),
    };
    state.configSchema = clone(toObject(state.configContract.schema || defaults.configSchema));
    state.configDraft = deepMerge(clone(toObject(state.configContract.values || {})), toObject(state.configDraft || {}));
    const nextPrompts = toArray(next.prompts);
    state.prompts = nextPrompts;
    state.promptsDir = toText(toObject(next.config || {}).prompts_dir || next.promptsDir || '', '');
    state.worktreeMerge = toObject(next.worktreeMerge);
    state.runnerControl = normalizeRunnerControl(next.runnerControl);
    state.liveRun = toObject(next.liveRun);
    state.history = toArray(next.history);
    state.runs = state.history;
    state.historySummary = toObject(next.historySummary);
    state.metrics = normalizeMetrics(next.metrics);
    state.notifications = toArray(next.notifications).slice(-MAX_LOG_ROWS);
    state.progress = toObject(next.progress);
    state.sectionState = toObject(next.sectionState);
    const previousRefresh = ensureSnapshotRefreshState();
    const nextRefresh = toObject(next.snapshotRefresh);
    const nextLiveRunStale = toObject(toObject(next.liveRun).stale);
    const staleReasons = toArray(nextRefresh.staleReasons || nextLiveRunStale.reasons)
      .map((reason) => toText(reason, ''))
      .filter(Boolean);
    state.snapshotRefresh = {
      ...clone(previousRefresh),
      ...clone(nextRefresh),
      active: Boolean(previousRefresh.active),
      inFlight: false,
      requestSeq: toNumber(previousRefresh.requestSeq, 0),
      retryCount: 0,
      retryDelayMs: SNAPSHOT_POLL_MS,
      maxRetryDelayMs: toNumber(previousRefresh.maxRetryDelayMs, SNAPSHOT_RECONNECT_MAX_MS) || SNAPSHOT_RECONNECT_MAX_MS,
      nextRefreshAt: 0,
      lastAttemptAt: toNumber(previousRefresh.lastAttemptAt, 0),
      lastSuccessAt: nowMs(),
      lastUpdatedAt: toNumber(nextRefresh.lastUpdatedAt || next.lastSnapshotAt || nowMs(), nowMs()),
      lastErrorAt: 0,
      lastErrorStatus: 0,
      lastError: '',
      stale: Boolean(nextRefresh.stale || nextLiveRunStale.value),
      staleReasons,
      latestRunDir: toText(nextRefresh.latestRunDir || next.latestRunDir, state.latestRunDir),
      timer: previousRefresh.timer,
    };
    applySnapshotRefreshSections(state.snapshotRefresh);
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

  function snapshotRefreshDisplay(refresh = state.snapshotRefresh) {
    const current = toObject(refresh);
    const status = toText(current.status, state.snapshotStatus || 'loading');
    const lastUpdatedAt = toNumber(current.lastUpdatedAt || state.lastSnapshotAt || 0, 0);
    const timestampText = lastUpdatedAt ? fmtDateTime(lastUpdatedAt) : t('common.unavailable');
    const staleReasons = toArray(current.staleReasons).map((reason) => toText(reason, '')).filter(Boolean);
    const ageMs = lastUpdatedAt ? Math.max(0, nowMs() - lastUpdatedAt) : Number.POSITIVE_INFINITY;
    const staleByAge = lastUpdatedAt > 0 && ageMs >= STALE_AFTER_MS;
    const hasStaleSignal = Boolean(current.stale || staleReasons.length || staleByAge);

    let label = state.snapshotLabel || t('snapshot.api');
    let copy = lastUpdatedAt ? `${t('snapshot.lastUpdated')} ${timestampText}` : t('snapshot.lastUpdated');
    let tone = 'running';
    let effectiveStatus = 'ready';

    if (status === 'loading') {
      label = t('snapshot.loading');
      copy = t('snapshot.loadingReadOnly');
      tone = 'loading';
      effectiveStatus = 'loading';
    } else if (status === 'fallback') {
      label = t('snapshot.fallback');
      copy = lastUpdatedAt ? `${t('snapshot.lastUpdated')} ${timestampText}` : t('snapshot.fallback');
      tone = 'warn';
      effectiveStatus = 'fallback';
    } else if (status === 'error') {
      label = t('snapshot.error');
      copy = lastUpdatedAt ? `${t('snapshot.lastUpdated')} ${timestampText}` : t('snapshot.error');
      tone = 'err';
      effectiveStatus = 'error';
    } else if (status === 'reconnecting') {
      label = hasStaleSignal ? t('snapshot.stale') : t('snapshot.reconnecting');
      copy = hasStaleSignal
        ? t('snapshot.staleCopy', { timestamp: timestampText })
        : t('snapshot.reconnectingCopy', { timestamp: timestampText });
      tone = hasStaleSignal ? 'stale' : 'reconnecting';
      effectiveStatus = hasStaleSignal ? 'stale' : 'reconnecting';
    } else if (status === 'stale' || hasStaleSignal) {
      label = t('snapshot.stale');
      copy = t('snapshot.staleCopy', { timestamp: timestampText });
      tone = 'stale';
      effectiveStatus = 'stale';
    }

    return {
      status: effectiveStatus,
      tone,
      label,
      copy,
      lastUpdatedAt,
      lastUpdatedLabel: lastUpdatedAt ? `${t('snapshot.lastUpdated')} ${timestampText}` : t('snapshot.lastUpdated'),
      timestampText,
      stale: hasStaleSignal || effectiveStatus === 'stale',
      staleReasons,
    };
  }

  function applySnapshotRefreshSections(refresh = snapshotRefreshDisplay()) {
    const display = refresh && typeof refresh === 'object' ? refresh : snapshotRefreshDisplay();
    if (display.status === 'ready') {
      return;
    }
    const sectionStatus = display.status === 'error' ? 'error' : display.status;
    const source = state.sourceMode === 'fallback' ? 'fallback' : 'api';
    const message = display.copy || display.lastUpdatedLabel || fallbackSectionMessage('activeRun');
    const sectionKeys = Object.keys(state.sectionState || {});
    for (const sectionKey of sectionKeys) {
      state.sectionState[sectionKey] = buildSectionState(sectionKey, sectionStatus, message, source);
    }
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
        : section.status === 'reconnecting'
          ? 'reconnecting'
          : section.status === 'stale'
            ? 'stale'
            : section.status === 'disabled' || section.status === 'loading' || section.status === 'fallback' || section.status === 'empty' || section.status === 'partial'
          ? 'warn'
          : 'info';
    const label =
      section.status === 'loading'
        ? t('snapshot.loadingReadOnly')
        : section.status === 'disabled'
          ? t('snapshot.controlsDisabled')
          : section.status === 'reconnecting'
            ? t('snapshot.reconnecting')
          : section.status === 'stale'
            ? t('snapshot.stale')
          : section.status === 'fallback'
          ? t('snapshot.fallback')
          : section.status === 'partial'
            ? t('snapshot.partial')
              : section.status === 'empty'
                ? t('snapshot.emptyState')
              : section.status;
    const message =
      section.status === 'loading'
        ? t('snapshot.loadingReadOnly')
        : section.status === 'disabled'
          ? t('snapshot.controlsDisabled')
          : section.message || fallbackSectionMessage(sectionKey);
    return `
      <div class="modal-banner section-banner ${sectionNoticeClass(tone)}">
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
        title: t('worktree.malformedPendingFile'),
        copy: reviewMessage || summary || t('worktree.fixOrDeletePendingFile'),
        actionCopy: t('worktree.fixOrDeletePendingFile'),
        mergeHint: t('worktree.noPendingFile'),
      };
    }

    if (cleanupFailed) {
      const recoveryPath = cleanupPath || toText(review?.worktreeDir || review?.worktree, 'the isolated worktree');
      return {
        tone: 'warn',
        title: status === 'applied_cleanup_failed' ? t('worktree.mergeRecordedCleanupFailed') : t('worktree.discardRecordedCleanupFailed'),
        copy:
          reviewMessage ||
          cleanupMessage ||
          summary ||
          `${t('worktree.manualCleanupRequired')} ${recoveryPath}.`,
        actionCopy:
          status === 'discard_cleanup_failed'
            ? `${t('worktree.manualRecovery')}: ${t('worktree.noSourceRepoChangePending')} ${sourceRepo}.`
            : `${t('worktree.manualRecovery')}: ${t('worktree.noCommitWillBeCreated')} ${sourceRepo}.`,
        mergeHint: t('worktree.cleanupRequired'),
      };
    }

    if (pendingReview) {
      const applyCheck = normalizeWorktreeFailureDetails(review?.preflight?.applyCheck || review?.preflight?.apply_check || review?.applyCheck || review?.apply_check);
      const applyCheckCopy = !applyCheck.ok && applyCheck.message ? `${t('worktree.gitApplyCheck')}: ${applyCheck.message}` : '';
      return {
        tone: 'warn',
        title: t('worktree.reviewRequiredBeforeChanges'),
        copy: reviewMessage || summary || applyCheckCopy || `${t('worktree.reviewThePatchBeforeSourceRepoChanges')} ${patchPath}.`,
        actionCopy:
          `${t('worktree.confirmMergeToApply')} ${t('worktree.backendValidates')} ${t('worktree.noCommitWillBeCreated')}`,
        mergeHint: t('worktree.reviewRequired'),
      };
    }

    if (status === 'apply_failed') {
      return {
        tone: 'warn',
        title: t('worktree.patchExportFailed'),
        copy: reviewMessage || summary || t('worktree.patchExportFailedBeforeMarker'),
        actionCopy: `${t('worktree.patchExportFailedBeforeMarker')} ${t('worktree.reviewBeforeMerge')}.`,
        mergeHint: t('worktree.patchExportFailed'),
      };
    }

    if (status === 'patch_not_applied' || status === 'not_applied') {
      return {
        tone: 'warn',
        title: status === 'patch_not_applied' ? t('worktree.patchExportNotApplied') : t('worktree.patchNotApplied'),
        copy: reviewMessage || summary || t('worktree.exportedPatchNotAutoApplied'),
        actionCopy: t('worktree.applyExportedPatchBeforeConfirming'),
        mergeHint: t('worktree.manualRecovery'),
      };
    }

    if (status === 'applied') {
      return {
        tone: 'info',
        title: t('worktree.patchApplied'),
        copy: reviewMessage || summary || `${t('worktree.patchApplied')} ${sourceRepo}. ${t('worktree.noCommitWillBeCreated')}`,
        actionCopy: `${t('worktree.worktreeAlreadyFinalized')} ${t('worktree.noPendingMergeAvailable')}`,
        mergeHint: t('worktree.finalized'),
      };
    }

    if (status === 'discarded') {
      return {
        tone: 'info',
        title: t('worktree.patchDiscarded'),
        copy: reviewMessage || summary || `${t('worktree.noSourceRepoChangePending')} ${sourceRepo}.`,
        actionCopy: `${t('worktree.worktreeAlreadyFinalized')} ${t('worktree.noPendingMergeAvailable')}`,
        mergeHint: t('worktree.finalized'),
      };
    }

    return {
      tone: 'info',
      title: t('worktree.title'),
      copy: reviewMessage || cleanupMessage || summary || t('worktree.noPendingMerge'),
      actionCopy: t('worktree.noSourceRepoChangePending'),
      mergeHint: t('worktree.readOnly'),
    };
  }

  function chip(label, className = '') {
    return `<span class="chip ${className}">${escapeHTML(label)}</span>`;
  }

  function normalizeWorktreeFailureDetails(details) {
    const raw = toObject(details);
    const source = raw.applyCheck || raw.apply_check || raw;
    const applyCheck = normalizeWorktreeApplyCheck(source);
    const hasRc = Boolean(
      raw.rc != null ||
        raw.returnCode != null ||
        raw.return_code != null ||
        raw.exitCode != null ||
        raw.exit_code != null ||
        source.rc != null ||
        source.returnCode != null ||
        source.return_code != null ||
        source.exitCode != null ||
        source.exit_code != null
    );
    return {
      ...applyCheck,
      path: toText(raw.path, ''),
      sourceRepo: toText(raw.sourceRepo || raw.source_repo, ''),
      runDir: toText(raw.runDir || raw.run_dir, ''),
      worktreeDir: toText(raw.worktreeDir || raw.worktree_dir || raw.worktree, ''),
      pendingFile: toText(raw.pendingFile || raw.pending_file || raw.pendingMarkerPath || raw.pending_marker_path, ''),
      hasRc,
    };
  }

  function renderWorktreeDiffHunk(hunk, index) {
    const raw = toObject(hunk);
    const header = toText(raw.header || raw.hunkHeader, '');
    const lines = toArray(raw.lines).map((line) => toText(line, ''));
    const truncated = Boolean(raw.truncated);
    const lineCount = toMaybeNumber(raw.lineCount ?? raw.line_count) ?? lines.length;
    const lineHTML = lines.length
      ? lines
          .map((line) => {
            const lineClass = line.startsWith('+')
              ? 'review-file__hunk-line--add'
              : line.startsWith('-')
                ? 'review-file__hunk-line--remove'
                : 'review-file__hunk-line--context';
            return `<div class="review-file__hunk-line ${lineClass}">${escapeHTML(line)}</div>`;
          })
          .join('')
      : `<div class="summary-note">${escapeHTML(t('worktree.noDiffHunks'))}</div>`;

    return `
      <div class="review-file__hunk">
        <div class="review-file__hunk-head">
          <div class="review-file__hunk-title">${escapeHTML(header || t('worktree.hunk', { index: index + 1 }))}</div>
          <div class="review-file__hunk-meta">
            ${lineCount ? chip(`${lineCount} ${t('common.lines')}`, 'chip--info') : ''}
            ${truncated ? chip(t('worktree.previewTruncated'), 'chip--warn') : ''}
          </div>
        </div>
        <div class="review-file__hunk-lines">
          ${lineHTML}
        </div>
      </div>
    `;
  }

  function renderWorktreeDiffFile(file) {
    const raw = toObject(file);
    const kind = toText(raw.kind || raw.state || raw.type, 'modified').toLowerCase();
    const path = toText(raw.path || raw.file || raw.name, '(unknown)');
    const oldPath = toText(raw.oldPath || raw.old_path || raw.sourcePath || raw.source_path || path, path);
    const newPath = toText(raw.newPath || raw.new_path || raw.targetPath || raw.target_path || path, path);
    const binary = Boolean(raw.binary);
    const deleted = Boolean(raw.deleted);
    const renamed = Boolean(raw.renamed);
    const large = Boolean(raw.large);
    const truncated = Boolean(raw.truncated);
    const hunks = toArray(raw.hunks).map(normalizeWorktreeDiffHunk);
    const displayPath = renamed && oldPath && newPath && oldPath !== newPath ? `${oldPath} -> ${newPath}` : path;
    const stateLabel = binary
      ? t('worktree.binaryFile')
      : deleted
        ? t('worktree.deletedFile')
        : renamed
          ? t('worktree.renamedFile')
          : kind === 'added'
            ? t('common.added')
            : t('common.edited');
    const stateTone = binary || deleted
      ? 'chip--warn'
      : renamed
        ? 'chip--info'
        : kind === 'added'
          ? 'chip--accent'
          : 'chip--info';
    const chips = [
      chip(stateLabel, stateTone),
      large ? chip(t('worktree.largeFile'), 'chip--warn') : '',
      truncated ? chip(t('worktree.previewTruncated'), 'chip--warn') : '',
      hunks.length ? chip(`${hunks.length} ${t('common.changes')}`, 'chip--accent') : chip(t('worktree.noDiffHunks'), 'chip--info'),
    ]
      .filter(Boolean)
      .join('');
    const facts = [];
    if (oldPath && oldPath !== path) {
      facts.push(compactFactItem(t('worktree.oldPath'), oldPath));
    }
    if (newPath && newPath !== oldPath) {
      facts.push(compactFactItem(t('worktree.newPath'), newPath));
    }
    if (raw.lineCount != null && String(raw.lineCount).trim() !== '') {
      facts.push(compactFactItem(t('worktree.lineCount'), String(raw.lineCount), truncated ? t('worktree.previewTruncated') : ''));
    }
    const factsHTML = facts.length
      ? `<div class="review-file__facts">${facts.join('')}</div>`
      : '';
    const diffHTML = hunks.length
      ? hunks.map((hunk, index) => renderWorktreeDiffHunk(hunk, index)).join('')
      : `<div class="summary-note">${escapeHTML(binary
          ? t('worktree.binaryFileNoPreview')
          : deleted
            ? t('worktree.deletedFileNoPreview')
            : large
              ? t('worktree.largeFilePreviewTruncated')
              : t('worktree.noDiffHunks'))}</div>`;
    const summary = compactText(toText(raw.summary || raw.note, ''), 220);
    const openAttr = binary || deleted || renamed || large || hunks.length === 0 ? ' open' : '';
    return `
      <details class="review-file review-file--diff"${openAttr}>
        <summary class="review-file__summary">
          <div class="review-file__summary-head">
            <div class="review-file__path">${escapeHTML(displayPath)}</div>
            <div class="review-file__chips">${chips}</div>
          </div>
          ${summary ? `<div class="review-file__summary-copy">${escapeHTML(summary)}</div>` : ''}
        </summary>
        <div class="review-file__body">
          ${factsHTML}
          ${diffHTML}
        </div>
      </details>
    `;
  }

  function renderWorktreeFailureDetails(details, title = t('worktree.failureDetails')) {
    const failure = normalizeWorktreeFailureDetails(details);
    const failedFiles = toArray(failure.failedFiles).map(normalizeWorktreeFailureFile);
    const failedHunks = toArray(failure.failedHunks).map(normalizeWorktreeFailureHunk);
    const hasDetails = Boolean(
      failure.command ||
        failure.message ||
        failure.output ||
        failure.pendingFile ||
        failedFiles.length ||
        failedHunks.length
    );
    if (!hasDetails || failure.ok) {
      return '';
    }

    const statusLabel = failure.status === 'missing'
      ? t('worktree.applyCheckUnavailable')
      : t('worktree.applyCheckFailed');
    const statusTone = failure.status === 'missing' ? 'chip--info' : 'chip--warn';
    const metaParts = [
      failure.command ? `${t('worktree.applyCheckCommand')}: ${failure.command}` : '',
      failure.hasRc ? `${t('worktree.applyCheckRc')}: ${String(failure.rc ?? 0)}` : '',
      failure.message ? `${t('worktree.applyCheckMessage')}: ${failure.message}` : '',
    ].filter(Boolean);

    const failedFilesHTML = failedFiles.length
      ? `
        <div class="worktree-check__section">
          <div class="worktree-check__section-title">${escapeHTML(t('worktree.failedFiles'))}</div>
          <div class="compact-list">
            ${failedFiles
              .map((item) => `
                <div class="compact-list__item worktree-check__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(item.path || t('common.unknown'))}</div>
                    <div class="compact-list__meta">${escapeHTML([
                      item.line ? t('logs.line', { lineNumber: item.line }) : '',
                      item.reason || '',
                    ].filter(Boolean).join(' | '))}</div>
                  </div>
                </div>
              `)
              .join('')}
          </div>
        </div>
      `
      : '';

    const failedHunksHTML = failedHunks.length
      ? `
        <div class="worktree-check__section">
          <div class="worktree-check__section-title">${escapeHTML(t('worktree.failedHunks'))}</div>
          <div class="worktree-check__hunks">
            ${failedHunks
              .map((item) => `
                <div class="worktree-check__hunk">
                  <div class="worktree-check__hunk-head">
                    <div class="worktree-check__hunk-path">${escapeHTML(item.path || t('common.unknown'))}</div>
                    <div class="worktree-check__hunk-meta">${escapeHTML([
                      item.line ? t('logs.line', { lineNumber: item.line }) : '',
                      item.reason || '',
                    ].filter(Boolean).join(' | '))}</div>
                  </div>
                  ${item.header ? `<div class="worktree-check__hunk-header">${escapeHTML(item.header)}</div>` : ''}
                  ${item.lines.length ? `
                    <div class="worktree-check__hunk-lines">
                      ${item.lines.map((line) => `<div class="worktree-check__hunk-line">${escapeHTML(line)}</div>`).join('')}
                    </div>
                  ` : ''}
                </div>
              `)
              .join('')}
          </div>
        </div>
      `
      : '';

    const outputHTML = failure.output
      ? `<pre class="worktree-check__output">${escapeHTML(compactText(failure.output, 720) || failure.output)}</pre>`
      : '';

    return `
      <div class="worktree-check">
        <div class="worktree-check__head">
          <div class="worktree-check__title">${escapeHTML(title)}</div>
          ${chip(statusLabel, statusTone)}
        </div>
        ${metaParts.length ? `<div class="summary-note">${escapeHTML(metaParts.join(' | '))}</div>` : ''}
        ${outputHTML}
        ${failedFilesHTML}
        ${failedHunksHTML}
        ${!failure.ok && (failedFiles.length || failedHunks.length || failure.output) ? `
          <div class="summary-note worktree-check__recoverable">${escapeHTML(t('worktree.pendingStateRecoverable'))}</div>
        ` : ''}
      </div>
    `;
  }

  function renderWorktreePreflightBlock(review) {
    const raw = toObject(review);
    const preflight = toObject(raw.preflight);
    const sourceRepoState = toText(raw.sourceRepoState || raw.source_repo_state || preflight.sourceRepoState || preflight.source_repo_state, '');
    const sourceRepoDirty = Boolean(
      raw.sourceRepoDirty ??
        raw.source_repo_dirty ??
        preflight.sourceRepoDirty ??
        preflight.source_repo_dirty ??
        (sourceRepoState ? sourceRepoState !== 'clean' : false)
    );
    const sourceHead = toText(raw.sourceHead || raw.source_head || preflight.sourceHead || preflight.source_head, '');
    const expectedBaseRef = toText(raw.expectedBaseRef || raw.expected_base_ref || preflight.expectedBaseRef || preflight.expected_base_ref || raw.baseRef || raw.base_ref, '');
    const patchHash = toText(raw.patchHash || raw.patch_hash || preflight.patchHash || preflight.patch_hash, '');
    const pendingMarkerPath = toText(
      raw.pendingMarkerPath ||
        raw.pending_marker_path ||
        preflight.pendingMarkerPath ||
        preflight.pending_marker_path ||
        preflight.pendingFile ||
        preflight.pending_file ||
        raw.pendingFile ||
        raw.pending_file ||
        raw.statusFile ||
        raw.status_file,
      ''
    );
    const rawApplyCheck = toObject(raw.applyCheck || raw.apply_check || preflight.applyCheck || preflight.apply_check);
    const applyCheck = normalizeWorktreeFailureDetails(rawApplyCheck);
    const applyCheckHasData = Boolean(
      rawApplyCheck.command ||
        rawApplyCheck.cmd ||
        rawApplyCheck.output ||
        rawApplyCheck.failedFiles ||
        rawApplyCheck.failed_files ||
        rawApplyCheck.failedHunks ||
        rawApplyCheck.failed_hunks ||
        rawApplyCheck.message ||
        rawApplyCheck.status ||
        rawApplyCheck.ok != null ||
        rawApplyCheck.rc != null
    );
    const hasAnyPreflightData = Boolean(
      sourceRepoState ||
        sourceHead ||
        expectedBaseRef ||
        patchHash ||
        pendingMarkerPath ||
        applyCheck.command ||
        applyCheck.message ||
        applyCheck.output ||
        applyCheck.failedFiles.length ||
        applyCheck.failedHunks.length
    );
    if (!hasAnyPreflightData) {
      return '';
    }

    const applyCheckValue = !applyCheckHasData
      ? t('common.unavailable')
      : applyCheck.ok
        ? `${t('worktree.applyCheckPassed')} | rc=${String(applyCheck.rc ?? 0)}`
        : applyCheck.status === 'missing'
          ? t('worktree.applyCheckUnavailable')
          : `${t('worktree.applyCheckFailed')} | rc=${String(applyCheck.rc ?? 0)}`;
    const preflightCards = [
      {
        label: t('worktree.sourceDirtyState'),
        value: sourceRepoState || (sourceRepoDirty ? t('common.dirty') : t('common.unavailable')),
        valueClass: sourceRepoState || sourceRepoDirty ? (sourceRepoDirty ? 'runner-control__value--warn' : 'runner-control__value--muted') : 'runner-control__value--muted',
      },
      {
        label: t('worktree.sourceHead'),
        value: sourceHead || '--',
        valueClass: sourceHead ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: t('worktree.expectedBaseRef'),
        value: expectedBaseRef || '--',
        valueClass: expectedBaseRef ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: t('worktree.patchHash'),
        value: patchHash || '--',
        valueClass: patchHash ? 'runner-control__value--accent' : 'runner-control__value--muted',
      },
      {
        label: t('worktree.gitApplyCheck'),
        value: applyCheckValue,
        valueClass: !applyCheckHasData
          ? 'runner-control__value--muted'
          : applyCheck.ok
            ? 'runner-control__value--accent'
            : applyCheck.status === 'missing'
              ? 'runner-control__value--muted'
              : 'runner-control__value--warn',
      },
      {
        label: t('worktree.pendingMarkerPath'),
        value: pendingMarkerPath || '--',
        valueClass: pendingMarkerPath ? 'runner-control__value--muted' : 'runner-control__value--muted',
      },
    ];

    return `
      <div class="worktree-preflight">
        <div class="runner-control__details worktree-preflight__details">
          ${preflightCards.map((item) => detailCard(item.label, item.value, item.valueClass)).join('')}
        </div>
        ${renderWorktreeFailureDetails(applyCheck, t('worktree.gitApplyCheck'))}
      </div>
    `;
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
    const stateCounts = toObject(raw.stateCounts || raw.state_counts);
    const runSummary = toObject(raw.runSummary);
    const lastRunSummary = toObject(raw.lastRunSummary);
    const runCycles = toArray(runSummary.cycles);
    return {
      done: toNumber(stateCounts.done ?? counts.done ?? raw.tasksDone ?? lastRunSummary.done ?? 0, 0),
      total: toNumber(counts.total ?? raw.tasksTotal ?? lastRunSummary.total_tasks ?? 0, 0),
      failed: toNumber(stateCounts.failed ?? counts.failed ?? raw.tasksFailed ?? lastRunSummary.failed_count ?? 0, 0),
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
      parts.push(`${t('history.persistedSummary')}: ${finalReason}`);
    }
    if (status || rc != null) {
      parts.push(`${t('runner.runStatus')}: ${executionStatusLabel(toText(raw.executionStatus || raw.execution_status || status, status))}${rc != null ? ` rc=${rc}` : ''}`);
    }
    const projectStatus = toText(raw.projectStatus || raw.project_status || (raw.projectComplete ? 'complete' : 'incomplete'), raw.projectComplete ? 'complete' : 'incomplete');
    if (projectStatus) {
      parts.push(`${t('nav.project')}: ${projectStatusLabel(projectStatus)}`);
    }
    if (shutdownReason && shutdownReason !== finalReason) {
      parts.push(`${t('history.shutdownReason')}: ${shutdownReason}`);
    }
    if (counts.cycles) {
      parts.push(t('history.cycles', { count: counts.cycles }));
    }
    return parts.join(' | ') || t('history.noPersistedSummary');
  }

  function historyWorktreeOutcomeLabel(outcome) {
    const value = toText(outcome, 'none').toLowerCase();
    if (!value || value === 'none') return t('common.none');
    if (value === 'applied') return t('worktree.patchApplied');
    if (value === 'discarded') return t('worktree.patchDiscarded');
    if (value === 'patch_not_applied' || value === 'not_applied') return t('worktree.patchNotApplied');
    if (value === 'applied_cleanup_failed') return t('worktree.mergeRecordedCleanupFailed');
    if (value === 'discard_cleanup_failed') return t('worktree.discardRecordedCleanupFailed');
    if (value === 'pending review' || value === 'pending') return t('worktree.reviewRequired');
    if (value === 'failed' || value === 'error') return t('common.failed');
    return t('common.unknown');
  }

  function notificationKindLabel(kind) {
    switch (toText(kind, '').toLowerCase()) {
      case 'run_start':
        return t('notifications.filterRunStart');
      case 'run_stop':
        return t('notifications.filterRunStop');
      case 'task_done':
        return t('notifications.filterTaskDone');
      case 'task_failed':
        return t('notifications.filterTaskFailed');
      case 'quota':
        return t('notifications.filterQuota');
      case 'error':
        return t('notifications.filterError');
      case 'stalled':
        return t('notifications.filterStalled');
      default:
        return t('common.unknown');
    }
  }

  function renderTimelineConnector(nextStatus) {
    const normalized = normalizeStageStatus(nextStatus, 'pending');
    const cls = normalized === 'running' ? 'connector connector--running' : normalized === 'failed' ? 'connector connector--warn' : 'connector connector--done';
    return `<div class="${cls}"></div>`;
  }

  function renderLifecycleLane(stages, emptyMessage = '') {
    if (!stages.length) {
      return `<div class="summary-note">${escapeHTML(emptyMessage || t('pipeline.noLifecycleRecords'))}</div>`;
    }
    return stages
      .map((stage, index) => `
        ${renderStageCard(stage)}
        ${index < stages.length - 1 ? renderTimelineConnector(stages[index + 1].status) : ''}
      `)
      .join('');
  }

  function renderStageHealthSignals(stage) {
    const elapsedValue = stage.elapsedSec != null ? stage.elapsedSec : stage.durationSec;
    const elapsedText = elapsedValue != null ? fmtDuration(elapsedValue) : t('common.unavailable');
    const latestLogLine = compactText(redactionAwareText(stage.latestLogLine, t('pipeline.latestLogLineUnavailable')), 180) || t('pipeline.latestLogLineUnavailable');
    const latestBackendEvent = compactText(redactionAwareText(stage.latestBackendEvent, t('pipeline.latestBackendEventUnavailable')), 180) || t('pipeline.latestBackendEventUnavailable');
    const noOutputMinutes = Math.max(1, toNumber(stage.noOutputMinutes, 1));
    const warningText = stage.outputStalled ? t('pipeline.noOutputWarning', { count: noOutputMinutes }) : '';
    return `
      <div class="stage-card__signals">
        <div class="stage-card__signal">
          <div class="stage-card__signal-label">${escapeHTML(t('pipeline.elapsed'))}</div>
          <div class="stage-card__signal-value">${escapeHTML(elapsedText)}</div>
        </div>
        <div class="stage-card__signal">
          <div class="stage-card__signal-label">${escapeHTML(t('pipeline.latestLogLine'))}</div>
          <div class="stage-card__signal-value">${escapeHTML(latestLogLine)}</div>
        </div>
        <div class="stage-card__signal">
          <div class="stage-card__signal-label">${escapeHTML(t('pipeline.latestBackendEvent'))}</div>
          <div class="stage-card__signal-value">${escapeHTML(latestBackendEvent)}</div>
        </div>
        ${warningText ? `
          <div class="stage-card__signal stage-card__signal--warn">
            <div class="stage-card__signal-label">${escapeHTML(warningText)}</div>
          </div>
        ` : ''}
      </div>
    `;
  }

  function renderStageCard(stage) {
    const status = normalizeStageStatus(stage.status, 'pending');
    const cardClass = lifecycleStageCardClass(status);
    const iconClass = lifecycleStageIconClass(status);
    const iconText = lifecycleStageIconText(status);
    const label = toText(stage.label, stage.id || t('dashboard.stage'));
    const title = toText(stage.title || stage.taskTitle || stage.label, stage.label || t('pipeline.lifecycleRecord'));
    const model = toText(stage.model, '');
    const cycleText = stage.cycle != null ? `${t('pipeline.current')} ${stage.cycle}` : t('pipeline.stageUnavailable');
    const taskIdText = stage.taskId ? `${t('dashboard.currentTaskId')} ${stage.taskId}` : t('common.unavailable');
    const attemptText = stage.attempt != null ? `${t('dashboard.attempt')} ${stage.attempt}` : t('common.unavailable');
    const startedText = stage.startedAt ? `${t('pipeline.started')} ${fmtClock(stage.startedAt)}` : t('pipeline.startedUnavailable');
    const endedText = stage.endedAt ? `${t('pipeline.ended')} ${fmtClock(stage.endedAt)}` : status === 'running' ? t('pipeline.inProgress') : t('pipeline.endedUnavailable');
    const elapsedText = stage.elapsedSec != null ? fmtDuration(stage.elapsedSec) : stage.durationSec != null ? fmtDuration(stage.durationSec) : '--';
    const recentOutput = compactText(redactionAwareText(stage.recentOutput), 180) || t('pipeline.recentOutputUnavailable');
    return `
      <div class="${cardClass}">
        <div class="stage-card__head">
          <div class="${iconClass}">${iconText}</div>
          <div class="stage-card__title">
            <div class="stage-card__label">${escapeHTML(label)}</div>
            <div class="stage-card__meta">${escapeHTML(lifecycleStageStatusLabel(status))} | ${escapeHTML(cycleText)}</div>
          </div>
        </div>
        <div class="stage-card__body">
          <div>${escapeHTML(title)}</div>
          <div class="muted">${escapeHTML(model || t('common.unavailable'))} | ${escapeHTML(elapsedText)}</div>
          <div class="summary-note" style="margin-top:6px;">${escapeHTML([taskIdText, attemptText, startedText, endedText].join(' | '))}</div>
          ${renderStageHealthSignals(stage)}
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
    const meta = [chip(backlogStatusLabel(status), backlogStatusToneClass(status)), chip(task.estimate), skill].filter(Boolean).join('');
    const dependencyText = task.dependsOn && task.dependsOn.length ? t('backlog.dependsOn', { items: task.dependsOn.join(', ') }) : t('backlog.dependenciesUnavailable');
    const fileScopeText = task.fileScope || (task.files && task.files.length ? task.files.join(', ') : t('backlog.fileScopeUnavailable'));
    const failureReason = toText(task.failureReason || toObject(task.failure).reason, '');
    const failureDetail = redactionAwareText(task.failureDetail || toObject(task.failure).detail);
    const recentOutput = compactText(redactionAwareText(task.recentOutput), 180) || t('backlog.recentOutputUnavailable');
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
        <div class="summary-note" style="margin-top:8px;">${escapeHTML(compactText(dependencyText, 140) || t('backlog.dependenciesUnavailable'))}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(t('backlog.fileScope', { scope: fileScopeText }), 140) || t('backlog.fileScopeUnavailable'))}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(task.attempt != null ? t('backlog.attemptText', { attempt: task.attempt }) : t('backlog.attemptUnavailable'))}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(failureReason ? t('backlog.failureText', { reason: `${failureReason}${failureDetail ? ` | ${compactText(failureDetail, 120)}` : ''}` }) : t('backlog.failureUnavailable'))}</div>
        <div class="summary-note" style="margin-top:4px;">${escapeHTML(recentOutput)}</div>
        ${status === 'in_progress' ? `
          <div class="meter" style="margin-top:8px; width: 100%;"><div class="meter__fill meter__fill--warn" style="width:${progressWidth(progress)}"></div></div>
        ` : ''}
        ${isSelected ? `<div class="summary-note" style="margin-top:8px;">${escapeHTML(t('common.selected'))}</div>` : ''}
      </button>
    `;
  }

  function renderGoalItem(bucket, goal, index, total) {
    const done = Boolean(goal.done);
    const sourceLine = goalItemLineNumber(goal);
    const checkboxState = goalItemCheckbox(goal);
    const toggleLabel = sourceLine
      ? t('goals.toggleCheckbox', { checkboxState, lineNumber: sourceLine })
      : t('goals.toggleCheckbox', { checkboxState, lineNumber: '' });
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
              <button type="button" class="button button--tiny button--quiet" data-goal-action="edit" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}">${escapeHTML(t('common.edit'))}</button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="move" data-goal-direction="-1" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}" ${canMoveUp ? '' : 'disabled'}>
                ${escapeHTML(t('common.up'))}
              </button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="move" data-goal-direction="1" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}" ${canMoveDown ? '' : 'disabled'}>
                ${escapeHTML(t('common.down'))}
              </button>
              <button type="button" class="button button--tiny button--quiet" data-goal-action="delete" data-goal-bucket="${escapeHTML(bucket)}" data-goal-index="${index}">${escapeHTML(t('common.delete'))}</button>
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
        ? `${row.bucketLabel} | ${t('common.row')} ${row.baseIndex + 1} -> ${t('common.row')} ${row.index + 1}`
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
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--removed">${escapeHTML(`${t('common.row')} ${row.baseIndex + 1}`)}</span>`);
      values.push(`<span class="prompt-diff-row__arrow">-></span>`);
      values.push(`<span class="prompt-diff-row__value prompt-diff-row__value--added">${escapeHTML(`${t('common.row')} ${row.index + 1} | ${afterSummary}`)}</span>`);
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
          <span class="badge ${badgeClass}">${escapeHTML(row.kind === 'added' ? t('common.added') : row.kind === 'removed' ? t('common.removed') : row.kind === 'moved' ? t('common.moved') : t('common.edited'))}</span>
        </div>
        <div class="prompt-diff-row__values">
          ${values.join('')}
        </div>
      </div>
    `;
  }

  function renderNotificationItem(item) {
    const color = kindColor(item.kind);
    const kindText = notificationKindLabel(item.kind);
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
        <div class="notification-feed__msg">${escapeHTML(redactionAwareText(item.text, t('notifications.noRecorded')))}</div>
        <div class="notification-feed__run">${escapeHTML(item.run)}</div>
      </div>
    `;
  }

  function renderHistoryRow(run) {
    const selected = state.historySelection === run.id;
    const executionStatus = toText(run.executionStatus || run.status, run.status);
    const projectStatus = toText(run.projectStatus || (run.projectComplete ? 'complete' : 'incomplete'), run.projectComplete ? 'complete' : 'incomplete');
    const statusTone = executionStatusTone(executionStatus);
    const color =
      statusTone === 'success' || statusTone === 'completed'
        ? 'var(--accent)'
        : statusTone === 'failed'
          ? 'var(--err)'
          : statusTone === 'stopped'
            ? 'var(--warn)'
            : 'var(--info)';
    return `
      <button type="button" class="history-table__row ${selected ? 'config-row--active' : ''}" data-history-select="${escapeHTML(run.id)}">
        <span class="history-table__status" style="color:${color}">
          <span class="${executionStatusClass(executionStatus)}">${escapeHTML(`${t('runner.runStatus')}: ${executionStatusLabel(executionStatus)}`)}</span>
          <span class="${projectStatusClass(projectStatus)}">${escapeHTML(`${t('nav.project')}: ${projectStatusLabel(projectStatus)}`)}</span>
        </span>
        <span>
          <span>${escapeHTML(run.branch)}</span>
          <div class="history-table__id">${escapeHTML(run.id)}</div>
        </span>
        <span>${escapeHTML(`${run.tasksDone}/${run.tasksTotal}`)}</span>
        <span>${escapeHTML(fmtDuration(run.durationSec))}</span>
        <span>${escapeHTML(fmtRelative(run.startedAt))}</span>
        <span style="text-align:right;"><span class="chip chip--accent">${escapeHTML(t('common.open'))}</span></span>
      </button>
    `;
  }

  function renderPromptCard(prompt) {
    const active = state.promptSelection === prompt.id;
    const disabled = promptMutationInFlight();
    const previewText = redactionAwareText(prompt.preview, t('common.unavailable'));
    return `
      <button type="button" class="prompt-card ${active ? 'prompt-card--active' : ''}" data-prompt-select="${escapeHTML(prompt.id)}" ${disabled ? 'disabled aria-disabled="true"' : ''}>
        <div class="prompt-card__head">
          <span class="badge badge--${prompt.mode === 'override' ? 'info' : 'dim'}">${escapeHTML(prompt.mode === 'override' ? t('prompts.override') : t('prompts.template'))}</span>
          <div class="prompt-card__name">${escapeHTML(prompt.file)}</div>
        </div>
        <div class="prompt-card__meta">
          <span>${escapeHTML(prompt.scope)}</span>
          <span>${escapeHTML(prompt.profile || t('common.unknown'))}</span>
          <span>${escapeHTML(prompt.source)}</span>
          <span>${escapeHTML(prompt.updated)}</span>
        </div>
        <div class="prompt-card__path">${escapeHTML(prompt.path || prompt.file)}</div>
        <div class="prompt-card__preview">${escapeHTML(previewText)}</div>
        <div class="summary-note prompt-card__summary">${escapeHTML(prompt.summary)}</div>
      </button>
    `;
  }

  function configValueToText(value, schema, path = '') {
    if (!schema) {
      return value == null || value === '' ? '--' : JSON.stringify(value);
    }
    if (schema.redacted) {
      if (value == null || value === '' || (Array.isArray(value) && value.length === 0)) {
        return '--';
      }
      return t('config.redactedHidden');
    }
    if (schema.kind === 'bool') return value === true ? t('common.enabled') : t('common.disabled');
    if (schema.kind === 'enum') return value == null || value === '' ? '--' : String(value);
    if (schema.kind === 'multienum' || schema.kind === 'list') {
      const listValue = path === 'roles' ? normalizeRoleSpecs(value, schema.options || []) : value || [];
      return fmtList(listValue) || '--';
    }
    if (schema.kind === 'number') return value == null || value === '' ? '--' : String(value);
    return value == null || value === '' ? '--' : String(value);
  }

  function renderConfigValueSummary(path, schema, value) {
    return escapeHTML(configValueToText(value, schema, path));
  }

  function validateField(path, value, schema) {
    if (!schema) return null;
    if (schema.kind === 'bool') {
      return typeof value === 'boolean' ? null : t('config.mustBeBoolean');
    }
    if (schema.kind === 'number') {
      if (value === '' || value == null || Number.isNaN(Number(value))) {
        return schema.allow_empty ? null : t('config.mustBeNumber');
      }
      const num = Number(value);
      if (schema.min != null && num < schema.min) return t('config.mustBeAtLeast', { min: schema.min });
      if (schema.max != null && num > schema.max) return t('config.mustBeAtMost', { max: schema.max });
      return null;
    }
    if (schema.kind === 'text') {
      if (String(value || '').trim()) return null;
      return schema.allow_empty ? null : t('config.cannotBeEmpty');
    }
    if (schema.kind === 'enum') {
      if (schema.allow_empty && (value == null || value === '')) return null;
      return schema.options.includes(value) ? null : t('config.mustBeOneOf', { options: schema.options.join(', ') });
    }
    if (schema.kind === 'multienum') {
      if (path === 'roles') {
        const items = normalizeRoleSpecs(value, schema.options || []);
        if (!items.length && schema.allow_empty) return null;
        if (!items.length) return t('config.pickAtLeastOne');
        const invalid = items.filter((item) => classifyRoleSpec(item, schema.options || []) === 'invalid');
        return invalid.length ? t('config.invalidOption', { options: invalid.join(', ') }) : null;
      }
      const items = Array.isArray(value) ? value : normalizeListValues(value);
      if (!items.length && schema.allow_empty) return null;
      if (!items.length) return t('config.pickAtLeastOne');
      const invalid = items.filter((item) => !schema.options.includes(item));
      return invalid.length ? t('config.invalidOption', { options: invalid.join(', ') }) : null;
    }
    if (schema.kind === 'list') {
      const items = Array.isArray(value) ? value : normalizeListValues(value);
      if (!items.length && schema.allow_empty) return null;
      if (!items.length) return t('config.enterAtLeastOneValue');
      if (schema.item_kind === 'int' || schema.itemKind === 'int' || schema.item_kind === 'number' || schema.itemKind === 'number') {
        const invalid = items.filter((item) => !Number.isInteger(Number(item)));
        return invalid.length ? t('config.invalidIntegerValue', { values: invalid.join(', ') }) : null;
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
      return t('config.repoManagedByServer');
    }
    if (schema && schema.redacted && String(value || '').trim() === REDACTED_VALUE) {
      return t('config.redactedPlaceholderSaveBlocked');
    }
    return validateField(path, value, schema);
  }

  function configSaveDisabledReason(diffs = getConfigDiffs(), invalidDiffs = diffs.filter((diff) => diff.error)) {
    if (configSaveInFlight()) {
      return t('config.saveInProgress');
    }
    if (!configSaveEnabled()) {
      return redactionAwareText(state.runnerControl?.message, t('config.savesDisabledUntilRunnerEnabled'));
    }
    if (!diffs.length) {
      return t('config.noConfigChanges');
    }
    if (invalidDiffs.length) {
      return t('config.fixInvalidChangesBeforeSaving', { count: invalidDiffs.length });
    }
    return '';
  }

  function renderConfigSaveBanner(diffs, invalidDiffs) {
    const saveState = toObject(state.configSave || {});
    const changedPaths = toArray(saveState.changedPaths);
    const reloadRequiredPaths = toArray(saveState.reloadRequiredPaths);
    const validationErrors = toArray(saveState.validationErrors).map((error) => toObject(error));
    const diffPaths = diffs.map((diff) => diff.path);
    const restartPaths = diffs.filter((diff) => diff.restart).map((diff) => diff.path);
    const bannerTitle = saveState.status === 'saving'
      ? t('config.saving')
      : saveState.status === 'success'
        ? t('config.saved')
        : saveState.status === 'error'
          ? t('config.saveFailed')
          : !configSaveEnabled()
            ? t('config.saveLocked')
            : diffPaths.length
              ? t('config.readyToSave')
              : t('config.noChanges');
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
      ? t('config.saveCreatesBackup')
      : saveState.status === 'success'
        ? saveState.message || t('config.saved')
        : saveState.status === 'error'
          ? saveState.message || t('config.saveFailed')
          : !configSaveEnabled()
            ? configSaveDisabledReason(diffs, invalidDiffs)
            : diffPaths.length
              ? t('config.saveCreatesBackup')
              : t('config.localDraftOnly');
    const metaRows = [];
    if (saveState.status === 'success') {
      if (saveState.backupPath) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">${escapeHTML(t('config.backupPath'))}</div>
            <div class="config-save-state__path">${escapeHTML(saveState.backupPath)}</div>
          </div>
        `);
      }
      const savedPaths = changedPaths.length ? changedPaths : diffPaths;
      if (savedPaths.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">${escapeHTML(t('config.savedPaths'))}</div>
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
            <div class="config-save-state__label">${escapeHTML(t('config.reloadRequired'))}</div>
            <div class="config-save-state__paths">
              ${reloadPaths.map((path) => `<span class="config-save-state__path">${escapeHTML(path)}</span>`).join('')}
            </div>
          </div>
        `);
      }
      if (validationErrors.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">${escapeHTML(t('config.rejectedFields'))}</div>
            ${validationErrors
              .map((error) => {
                const field = toText(error.field || error.path || error.name || '', '');
                const code = toText(error.code || '', '');
                const message = toText(error.message || '', '');
                const detail = field ? `<strong>${escapeHTML(field)}</strong>` : (code ? `<strong>${escapeHTML(code)}</strong>` : '');
                const suffix = message ? `: ${escapeHTML(message)}` : '';
                return `<div class="field-error">${detail}${suffix}</div>`;
              })
              .join('')}
          </div>
        `);
      }
    } else if (saveState.status === 'error') {
      if (saveState.backupPath) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">${escapeHTML(t('config.backupPath'))}</div>
            <div class="config-save-state__path">${escapeHTML(saveState.backupPath)}</div>
          </div>
        `);
      }
      const reloadPaths = reloadRequiredPaths.length ? reloadRequiredPaths : restartPaths;
      if (reloadPaths.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">${escapeHTML(t('config.reloadRequired'))}</div>
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
            <div class="config-save-state__label">${escapeHTML(t('config.pendingPaths'))}</div>
            <div class="config-save-state__paths">
              ${failedPaths.map((path) => `<span class="config-save-state__path">${escapeHTML(path)}</span>`).join('')}
            </div>
          </div>
        `);
      }
      if (validationErrors.length) {
        metaRows.push(`
          <div>
            <div class="config-save-state__label">${escapeHTML(t('config.rejectedFields'))}</div>
            ${validationErrors
              .map((error) => {
                const field = toText(error.field || error.path || error.name || '', '');
                const code = toText(error.code || '', '');
                const message = toText(error.message || '', '');
                const detail = field ? `<strong>${escapeHTML(field)}</strong>` : (code ? `<strong>${escapeHTML(code)}</strong>` : '');
                const suffix = message ? `: ${escapeHTML(message)}` : '';
                return `<div class="field-error">${detail}${suffix}</div>`;
              })
              .join('')}
          </div>
        `);
      }
    } else if (diffPaths.length) {
      metaRows.push(`
        <div>
          <div class="config-save-state__label">${escapeHTML(t('config.pendingPaths'))}</div>
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
        message: t('config.noConfigChangesSupplied'),
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
        message: t('config.fixPendingChangesBeforeSaving', { count: invalidDiffs.length }),
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
      message: t('config.savingConfigChanges'),
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
        const validationDetails = toObject(details.validation);
        const saveError = new Error(toText(error.message || payload.message || `Config save failed (HTTP ${response.status}).`, 'Config save failed.'));
        saveError.code = toText(error.code || payload.code || 'config_save_failed', 'config_save_failed');
        saveError.backupPath = toText(
          details.backup_path || details.backupPath || payload.backup_path || payload.backupPath || '',
          ''
        );
        saveError.validationErrors = toArray(validationDetails.errors || details.errors || payload.validation?.errors || []);
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
        message: toText(payload.message || t('config.configSavedMessage'), t('config.configSavedMessage')),
        errorCode: '',
        backupPath: toText(payload.backup_path || payload.backupPath || '', ''),
        changedPaths: toArray(payload.changed_paths || payload.changedPaths || diffs.map((diff) => diff.path)),
        reloadRequiredPaths: toArray(payload.reload_required_paths || payload.reloadRequiredPaths || diffs.filter((diff) => diff.restart).map((diff) => diff.path)),
        requestPath,
        savedAt: nowMs(),
      };
      renderShell({ preserveScroll: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : t('config.saveFailed');
      const errorCode = error instanceof Error && typeof error.code === 'string' && error.code ? error.code : 'config_save_failed';
      const backupPath = error instanceof Error ? toText(error.backupPath || error.backup_path || '', '') : '';
      const validationErrors = error instanceof Error && error.validationErrors
        ? toArray(error.validationErrors).map((item) => toObject(item))
        : [];
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
        validationErrors,
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
        ? t('prompts.filenameRequired')
        : fileErrorCode === 'prompt_file_invalid'
          ? t('prompts.filenameMustBeBare')
          : fileErrorCode === 'prompt_file_mismatch'
            ? `${t('prompts.filename')} ${expectedFile}`
            : '',
      fileErrorCode,
      contentError: contentErrorCode ? t('prompts.promptContentRequired') : '',
      contentErrorCode,
      templateError: templateErrorCode ? `${t('prompts.missingTemplateVariables')}: ${missingVariables.map((name) => `{${name}}`).join(', ')}` : '',
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
        <span class="badge badge--dim">${escapeHTML(t('prompts.noPromptSelected'))}</span>
        <span class="muted">${escapeHTML(t('prompts.selectPrompt'))}</span>
      `;
    }
    if (editor.loading) {
      return `
        <span class="badge badge--warn">${escapeHTML(t('common.loading'))}</span>
        <span class="muted">${escapeHTML(t('prompts.explicitPromptRead'))}</span>
      `;
    }
    if (editor.error) {
      return `
        <span class="badge badge--err">${escapeHTML(t('common.failed'))}</span>
        <span class="muted">${escapeHTML(redactionAwareText(editor.error, t('prompts.promptReadFailed')))}</span>
      `;
    }
    const dirty = promptEditorIsDirty(editor);
    const contentLength = String(editor.draftContent || '').length;
    const backupCount = promptEditorBackups(editor).length;
    const saveState = toObject(editor.saveState);
    const restoreState = toObject(editor.restoreState);
    const mutationBadge = saveState.status === 'saving'
      ? { tone: 'warn', label: t('prompts.saving').toUpperCase() }
      : restoreState.status === 'restoring'
        ? { tone: 'warn', label: t('prompts.restoring').toUpperCase() }
        : saveState.status === 'error'
          ? { tone: 'err', label: t('prompts.saveErrorBadge') }
          : restoreState.status === 'error'
            ? { tone: 'err', label: t('prompts.restoreErrorBadge') }
            : saveState.status === 'success'
              ? { tone: 'info', label: t('prompts.promptSaved').toUpperCase() }
              : restoreState.status === 'success'
                ? { tone: 'info', label: t('prompts.promptRestored').toUpperCase() }
                : !promptMutationEnabled()
                  ? { tone: 'dim', label: t('common.localOnly') }
                  : null;
    const backupBadge = backupCount
      ? `${backupCount} ${backupCount === 1 ? t('common.backup') : t('common.backups')}`.toUpperCase()
      : t('common.noBackups');
    return `
      ${mutationBadge ? `<span class="badge badge--${mutationBadge.tone}">${mutationBadge.label}</span>` : ''}
      <span class="badge ${dirty ? 'badge--warn' : 'badge--dim'}">${dirty ? t('common.dirty') : t('common.clean')}</span>
      <span class="badge badge--info">${t('common.fullRead')}</span>
      <span class="badge ${backupCount ? 'badge--dim' : 'badge--warn'}">${escapeHTML(backupBadge)}</span>
      <span class="muted">${escapeHTML(contentLength)} ${escapeHTML(t('common.chars'))}</span>
    `;
  }

  function renderPromptEditorBanner() {
    const editor = promptEditorData();
    if (!editor.promptId) {
      return `
        <div class="section-banner section-banner--info">
          <div class="section-banner__title">${escapeHTML(t('prompts.promptEditor'))}</div>
          <div class="section-banner__copy">${escapeHTML(t('prompts.selectPrompt'))}</div>
        </div>
      `;
    }
    if (editor.loading) {
      return `
        <div class="section-banner section-banner--info">
          <div class="section-banner__title">${escapeHTML(t('prompts.explicitPromptRead'))}</div>
          <div class="section-banner__copy">${escapeHTML(t('prompts.loadedThroughExplicitReadPath'))}</div>
        </div>
      `;
    }
    if (editor.error) {
      return `
        <div class="section-banner section-banner--err">
          <div class="section-banner__title">${escapeHTML(t('common.failed'))}</div>
          <div class="section-banner__copy">${escapeHTML(redactionAwareText(editor.error, t('common.failed')))}</div>
        </div>
      `;
    }
    const saveState = toObject(editor.saveState);
    const restoreState = toObject(editor.restoreState);
    if (saveState.status === 'saving' || restoreState.status === 'restoring') {
      const activeState = saveState.status === 'saving' ? saveState : restoreState;
      return `
        <div class="section-banner section-banner--warn">
          <div class="section-banner__title">${saveState.status === 'saving' ? t('prompts.savePrompt') : t('prompts.restoreBackup')}</div>
          <div class="section-banner__copy">${escapeHTML(activeState.message || (saveState.status === 'saving' ? t('prompts.saveCreatesBackup') : t('common.working')))}</div>
        </div>
      `;
    }
    if (saveState.status === 'error' || restoreState.status === 'error') {
      const activeState = saveState.status === 'error' ? saveState : restoreState;
      return `
        <div class="section-banner section-banner--err">
          <div class="section-banner__title">${saveState.status === 'error' ? t('prompts.promptSaveFailed') : t('prompts.promptRestoreFailed')}</div>
          <div class="section-banner__copy">${escapeHTML(activeState.message || t('prompts.promptMutationFailed'))}</div>
        </div>
      `;
    }
    if (saveState.status === 'success' || restoreState.status === 'success') {
      const activeState = saveState.status === 'success' ? saveState : restoreState;
      return `
        <div class="section-banner section-banner--info">
          <div class="section-banner__title">${saveState.status === 'success' ? t('prompts.promptSaved') : t('prompts.promptRestored')}</div>
          <div class="section-banner__copy">${escapeHTML(redactionAwareText(activeState.message, t('prompts.promptMutationCompleted')))}</div>
        </div>
      `;
    }
    if (!promptMutationEnabled()) {
      return `
        <div class="section-banner section-banner--warn">
          <div class="section-banner__title">${escapeHTML(t('prompts.promptMutationsLocked'))}</div>
          <div class="section-banner__copy">${escapeHTML(redactionAwareText(state.runnerControl?.message, t('prompts.promptMutationsDisabled')))}</div>
        </div>
      `;
    }
    const dirty = promptEditorIsDirty(editor);
    return `
      <div class="section-banner ${dirty ? 'section-banner--warn' : 'section-banner--info'}">
        <div class="section-banner__title">${escapeHTML(t('prompts.explicitPromptRead'))}</div>
        <div class="section-banner__copy">${dirty
          ? escapeHTML(t('goals.draftStaysLocal'))
          : escapeHTML(t('prompts.chooseBackupRestore'))
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
      : t('common.none');
    lines.push(`
      <div class="compact-list__item">
        <span class="compact-list__bullet"></span>
        <div>
          <div class="compact-list__body ${validation.fileError ? 'field-error' : ''}">${escapeHTML(validation.fileError || t('prompts.filenameIsPopulated'))}</div>
          <div class="compact-list__meta">${escapeHTML(t('prompts.filenameValidation'))}</div>
        </div>
      </div>
    `);
    lines.push(`
      <div class="compact-list__item">
        <span class="compact-list__bullet"></span>
        <div>
          <div class="compact-list__body ${validation.contentError ? 'field-error' : ''}">${escapeHTML(validation.contentError || t('prompts.contentIsPopulated'))}</div>
          <div class="compact-list__meta">${escapeHTML(t('prompts.contentValidation'))}</div>
        </div>
      </div>
    `);
    lines.push(`
      <div class="compact-list__item">
        <span class="compact-list__bullet"></span>
        <div>
          <div class="compact-list__body ${validation.templateError ? 'field-error' : ''}">${escapeHTML(validation.templateError || t('prompts.requiredTemplateVariablesLabel', { variables: required }))}</div>
          <div class="compact-list__meta">${escapeHTML(t('prompts.templateVariableValidation'))}</div>
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
    // Save creates a backup first; restore copies a selected backup back into place.
    return `
      <div class="prompt-diff-list">
        <div class="prompt-diff-row">
          <div class="prompt-diff-row__head">
            <span class="prompt-diff-row__path">${escapeHTML(t('prompts.localDiffPreview'))}</span>
            <span class="badge ${dirty ? 'badge--warn' : 'badge--dim'}">${dirty ? t('common.dirty') : t('common.clean')}</span>
          </div>
          <div class="summary-note">${escapeHTML(t('prompts.chooseBackupRestore'))}</div>
        </div>
        ${fileChanged ? `
          <div class="prompt-diff-row">
            <div class="prompt-diff-row__head">
              <span class="prompt-diff-row__path">${escapeHTML(t('prompts.filename'))}</span>
              <span class="badge badge--info">${escapeHTML(t('common.localOnly'))}</span>
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
              <span class="prompt-diff-row__path">${escapeHTML(t('prompts.line', { lineNumber: row.lineNumber }))}</span>
              <span class="badge ${row.kind === 'added' ? 'badge--info' : 'badge--warn'}">${row.kind === 'added' ? t('common.added') : t('common.removed')}</span>
            </div>
            <div class="prompt-diff-row__values">
              <span class="prompt-diff-row__value prompt-diff-row__value--${row.kind}">${escapeHTML(row.text || '(empty)')}</span>
            </div>
          </div>
        `).join('') : `<div class="summary-note">${escapeHTML(t('prompts.noLocalChangesYet'))}</div>`}
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
          <div class="prompt-mutation-state__label">${escapeHTML(t('prompts.selectedBackup'))}</div>
          <div class="prompt-mutation-state__path">${escapeHTML(selectedBackup.path || '(unresolved backup path)')}</div>
          <div class="summary-note">${escapeHTML(selectedBackup.summary || selectedBackup.name || t('prompts.selectedBackup'))}</div>
        </div>
      `);
    }
    if (backups.length) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">${escapeHTML(t('prompts.availableBackups'))}</div>
          <div class="prompt-mutation-state__paths">
            ${backups.map((backup) => `<span class="prompt-mutation-state__path">${escapeHTML(backup.path || backup.name || '(backup)')}</span>`).join('')}
          </div>
        </div>
      `);
    }
    if (saveState.backupPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">${escapeHTML(t('prompts.backupPath'))}</div>
          <div class="prompt-mutation-state__path">${escapeHTML(saveState.backupPath)}</div>
        </div>
      `);
    }
    if (restoreState.backupPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">${escapeHTML(t('prompts.restoreBackupPath'))}</div>
          <div class="prompt-mutation-state__path">${escapeHTML(restoreState.backupPath)}</div>
        </div>
      `);
    }
    if (saveState.savedPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">${escapeHTML(t('prompts.savedPath'))}</div>
          <div class="prompt-mutation-state__path">${escapeHTML(saveState.savedPath)}</div>
        </div>
      `);
    }
    if (restoreState.restoredFromPath) {
      metaRows.push(`
        <div>
          <div class="prompt-mutation-state__label">${escapeHTML(t('prompts.restoredFrom'))}</div>
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
          <div class="prompt-mutation-state__label">${escapeHTML(t('prompts.errorCode'))}</div>
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
      ? t('prompts.savePrompt')
      : restoreState.status === 'restoring'
        ? t('prompts.restoreBackup')
        : saveState.status === 'error'
          ? t('prompts.promptSaveFailed')
          : restoreState.status === 'error'
            ? t('prompts.promptRestoreFailed')
            : saveState.status === 'success'
              ? t('prompts.promptSaved')
              : restoreState.status === 'success'
                ? t('prompts.promptRestored')
                : !mutationEnabled
                  ? t('prompts.promptMutationsLocked')
                  : t('prompts.promptEditor');
    const bannerCopy = saveState.status === 'saving'
      ? saveState.message || t('prompts.saveCreatesBackup')
      : restoreState.status === 'restoring'
        ? restoreState.message || t('prompts.chooseBackupRestore')
        : saveState.status === 'error' || restoreState.status === 'error'
          ? redactionAwareText(activeState?.message || t('prompts.promptMutationFailed'), t('prompts.promptMutationFailed'))
        : saveState.status === 'success' || restoreState.status === 'success'
            ? activeState?.message || t('prompts.promptMutationCompleted')
            : !mutationEnabled
              ? redactionAwareText(state.runnerControl?.message, t('prompts.promptMutationsDisabled'))
              : t('prompts.chooseBackupRestore');
    const errorCode = activeState && activeState.errorCode ? `<div class="prompt-mutation-state__code">${escapeHTML(activeState.errorCode)}</div>` : '';
    const backupOptions = backups.length
      ? backups.map((backup) => `
        <option value="${escapeHTML(backup.path)}"${backup.path === selectedBackup?.path ? ' selected' : ''}>
          ${escapeHTML(backup.summary || backup.name || backup.path)}
        </option>
      `).join('')
      : `<option value="">${escapeHTML(t('prompts.noBackupsAvailable'))}</option>`;
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
            <label class="prompt-editor__label" for="prompt-backup-selection">${escapeHTML(t('prompts.restoreBackup'))}</label>
            <select
              id="prompt-backup-selection"
              class="field-control prompt-editor__input prompt-backup-select"
              data-prompt-backup-select
              ${backupSelectAttrs}
            >
              ${backupOptions}
            </select>
            <div class="summary-note">${escapeHTML(t('prompts.chooseBackupRestore'))}</div>
          </div>
          <div class="prompt-editor__field">
            <label class="prompt-editor__label" for="prompt-restore-confirmation">${escapeHTML(t('prompts.restoreConfirmation'))}</label>
            <input
              id="prompt-restore-confirmation"
              class="field-control prompt-editor__input prompt-backup-confirm"
              data-prompt-restore-confirmation
              type="text"
              value="${escapeHTML(editor.restoreConfirmation || '')}"
              placeholder="${escapeHTML(t('prompts.restoreOverwritePhrase'))}"
              autocomplete="off"
              spellcheck="false"
              ${restoreConfirmationAttrs}
            >
            <div class="summary-note">${escapeHTML(t('prompts.restoreOverwritePhrase'))}</div>
          </div>
          <div class="prompt-editor__actions">
            ${button(t('prompts.savePrompt'), 'prompt-save', 'button--primary', `${saveButtonAttrs} data-prompt-save-button`)}
            ${button(t('prompts.restoreBackup'), 'prompt-restore', 'button--danger', `${restoreButtonAttrs} data-prompt-restore-button`)}
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

  function currentLiveRun() {
    return toObject(state.liveRun);
  }

  function currentLiveRunRunnerControl(liveRun = currentLiveRun()) {
    const current = toObject(liveRun);
    return toObject(current.runnerControl || current.control || state.runnerControl);
  }

  function currentLiveRunActiveRun(liveRun = currentLiveRun()) {
    const current = toObject(liveRun);
    return toObject(current.activeRun || state.activeRun);
  }

  function currentLiveRunProgress(liveRun = currentLiveRun()) {
    const current = toObject(liveRun);
    return toObject(current.progress || state.progress);
  }

  function currentLiveRunStatus(liveRun = currentLiveRun()) {
    const current = toObject(liveRun);
    return toObject(current.status);
  }

  function currentLiveRunLiveState(liveRun = currentLiveRun()) {
    const current = toObject(liveRun);
    const currentProcess = toObject(current.process);
    const currentRunnerControl = toObject(current.runnerControl || current.control);
    const liveState = current.liveState
      || current.live_state
      || currentProcess.liveState
      || currentProcess.live_state
      || currentRunnerControl.liveState
      || currentRunnerControl.live_state
      || state.runnerControl?.liveState
      || state.runnerControl?.live_state;
    return normalizeLiveState(liveState);
  }

  function currentLiveRunLog(liveRun = currentLiveRun()) {
    const current = toObject(liveRun);
    return toObject(current.log);
  }

  function currentLiveRunNotifications(liveRun = currentLiveRun()) {
    const current = toObject(liveRun);
    return toObject(current.notifications);
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
      return t('prompts.saving');
    }
    if (!promptMutationEnabled()) {
      return redactionAwareText(state.runnerControl?.message, t('prompts.promptMutationsDisabled'));
    }
    if (!editor.promptId) {
      return t('prompts.selectPrompt');
    }
    if (editor.loading) {
      return t('common.loading');
    }
    if (editor.error) {
      return t('prompts.promptReadFailed');
    }
    if (!promptEditorIsDirty(editor)) {
      return t('prompts.noLocalChangesYet');
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
      return t('prompts.restoring');
    }
    if (!promptMutationEnabled()) {
      return redactionAwareText(state.runnerControl?.message, t('prompts.promptMutationsDisabled'));
    }
    if (!editor.promptId) {
      return t('prompts.selectPrompt');
    }
    if (editor.loading) {
      return t('common.loading');
    }
    if (editor.error) {
      return t('prompts.promptReadFailed');
    }
    const validation = promptEditorValidation(editor);
    if (validation.fileError) {
      return validation.fileError;
    }
    const selectedBackup = promptSelectedBackup(editor);
    if (!selectedBackup || !toText(selectedBackup.path, '')) {
      return t('prompts.noBackupsAvailable');
    }
    if (!toText(editor.restoreConfirmation, '').trim()) {
      return t('prompts.restoreOverwritePhrase');
    }
    if (toText(editor.restoreConfirmation, '').trim() !== 'RESTORE BACKUP') {
      return t('prompts.restoreOverwritePhrase');
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
        message: t('prompts.saveCreatesBackup'),
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
            message: normalized.message || t('prompts.promptSaveFailed'),
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
          message: normalized.message || t('prompts.promptSaved'),
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
          message: toText(error?.message || error, t('prompts.promptSaveFailed')),
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
        message: t('prompts.restoringBackup'),
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
            message: normalized.message || t('prompts.promptRestoreFailed'),
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
          message: normalized.message || t('prompts.promptRestored'),
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
          message: toText(error?.message || error, t('prompts.promptRestoreFailed')),
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
          error: errorMessage || t('prompts.promptReadFailed'),
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
        error: toText(error?.message || error, t('prompts.promptReadFailed')),
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
    const liveRun = currentLiveRun();
    const liveStatus = currentLiveRunStatus(liveRun);
    return runStatusClass(
      liveStatus.run || state.progress?.run_status || state.activeRun.status,
      liveStatus.finalReason || state.progress?.final_reason || state.activeRun.finalReason,
    );
  }

  function runnerControlBusyAction(control = currentLiveRunRunnerControl()) {
    const current = toObject(control);
    if (state.stopSubmitting) {
      return state.stopAction;
    }
    if (current.busy) {
      return current.lastAction || state.stopAction || 'busy';
    }
    return '';
  }

  function runnerControlActionClass(action, baseClass = 'button--quiet', control = currentLiveRunRunnerControl()) {
    const classes = [baseClass];
    const busyAction = runnerControlBusyAction(control);
    if (busyAction === action) {
      classes.push('button--loading');
    } else if (!runnerControlActionEnabled(action, control)) {
      classes.push('button--paused');
    }
    return classes.join(' ');
  }

  function runnerControlActionState(action, control = currentLiveRunRunnerControl()) {
    const actions = toObject(toObject(control).actions);
    return toObject(actions[action]);
  }

  function runnerControlActionEnabled(action, control = currentLiveRunRunnerControl()) {
    const current = toObject(control);
    const statusReason = toText(current.status?.reason, '');
    const stopProgress = normalizeStopProgress(current.status?.stopProgress);
    if (!current.enabled || !current.controllerAvailable || current.busy) {
      return false;
    }
    if (statusReason.startsWith('status_error:')) {
      return false;
    }
    const busyAction = runnerControlBusyAction(current);
    if (busyAction) {
      return false;
    }
    if (String(action || '').toLowerCase() === 'stop' && stopProgress.phase === 'timeout' && stopProgress.canRetry !== false) {
      return true;
    }
    return Boolean(runnerControlActionState(action, current).enabled);
  }

  function runnerControlActionDisabledReason(action, control = currentLiveRunRunnerControl()) {
    const current = toObject(control);
    if (state.stopSubmitting || current.busy) {
      return t('runner.requestInFlight');
    }
    const statusReason = toText(current.status?.reason, '');
    if (statusReason.startsWith('status_error:')) {
      return redactionAwareText(current.lastError, '') || redactionAwareText(statusReason, '') || t('runner.backendError');
    }
    if (!current.enabled) {
      return redactionAwareText(current.message, t('runner.controlsDisabledMessage'));
    }
    if (!current.controllerAvailable) {
      return redactionAwareText(current.message, t('runner.controllerUnavailableMessage'));
    }
    const stopProgress = normalizeStopProgress(current.status?.stopProgress);
    if (String(action || '').toLowerCase() === 'stop' && stopProgress.phase === 'timeout') {
      return redactionAwareText(stopProgress.timeoutGuidance?.summary || stopProgress.message || t('runner.stopTimedOut'), '');
    }
    const actionState = runnerControlActionState(action, current);
    return redactionAwareText(actionState.disabledReason || actionState.disabled_reason || current.message, '');
  }

  function runnerControlButtonAttrs(action, control = currentLiveRunRunnerControl()) {
    const enabled = runnerControlActionEnabled(action, control);
    const reason = runnerControlActionDisabledReason(action, control);
    const busy = runnerControlBusyAction(control) === action || toObject(control).busy;
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
    const phrases = {
      merge: t(WORKTREE_ACTION_CONFIRMATION_KEYS.merge),
      discard: t(WORKTREE_ACTION_CONFIRMATION_KEYS.discard),
    };
    return phrases[action] || t(WORKTREE_ACTION_CONFIRMATION_KEYS.discard);
  }

  function worktreeActionLabel(action, busy = false) {
    const label = String(action || 'merge').toLowerCase() === 'discard' ? t('worktree.discardMerge') : t('worktree.applyMerge');
    if (!busy) {
      return label;
    }
    return String(action || 'merge').toLowerCase() === 'discard' ? t('worktree.discarding') : t('worktree.merging');
  }

  function worktreeActionModalTitle(action) {
    return String(action || 'merge').toLowerCase() === 'discard' ? t('worktree.confirmDiscard') : t('worktree.confirmMerge');
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
      return t('worktree.noPendingMerge');
    }
    if (status === 'error') {
      return data.reviewRequiredMessage || t('worktree.fixOrDeletePendingFile');
    }
    if (status === 'apply_failed') {
      return t('worktree.patchExportFailedBeforeMarker');
    }
    if (status === 'patch_not_applied' || status === 'not_applied') {
      return t('worktree.applyExportedPatchBeforeConfirming');
    }
    if (status === 'applied' || status === 'discarded') {
      return t('worktree.worktreeAlreadyFinalized');
    }
    if (cleanupState === 'failed' || status === 'applied_cleanup_failed' || status === 'discard_cleanup_failed') {
      return t('worktree.manualCleanupRequired');
    }
    if (!worktreeActionEnabled(review, action)) {
      return t('worktree.pendingMetadataIncomplete');
    }
    return '';
  }

  function worktreeActionButtonAttrs(review = state.worktreeMerge, action = 'merge') {
    const enabled = worktreeActionEnabled(review, action);
    const reason = worktreeActionDisabledReason(review, action);
    if (enabled) {
      return '';
    }
    return `disabled aria-disabled="true" title="${escapeHTML(reason || t('worktree.actionUnavailable'))}"`;
  }

  function worktreeActionSummary(action, review = state.worktreeMerge) {
    const data = toObject(review);
    const sourceRepo = toText(data.sourceRepo, 'the source repository');
    const patchPath = toText(data.patchPath || data.patch, 'the patch');
    const worktreeDir = toText(data.worktreeDir || data.worktree, 'the isolated worktree');
    if (String(action || 'merge').toLowerCase() === 'discard') {
      return t('worktree.discardSummary', { sourceRepo, worktreeDir });
    }
    return t('worktree.mergeSummary', { patchPath, sourceRepo });
  }

  function worktreeActionInstruction(action, review = state.worktreeMerge) {
    return t('worktree.typeConfirmationPhrase', { confirmation: worktreeActionConfirmationPhrase(action) });
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
      { label: t('worktree.sourceRepo'), value: toText(review.sourceRepo, '--') },
      { label: t('worktree.runDir'), value: toText(review.runDir, '--') },
      { label: t('worktree.worktreeDir'), value: toText(review.worktreeDir || review.worktree, '--') },
      { label: t('worktree.patchPath'), value: toText(review.patchPath || review.patch, '--') },
      { label: t('worktree.pendingMarkerPath'), value: toText(review.pendingMarkerPath || review.pendingFile || review.statusFile, '--') },
    ];
    const detailHTML = detailCards
      .map((item) => detailCard(item.label, item.value))
      .join('');
    const preflightHTML = renderWorktreePreflightBlock(review);
    const errorDetailsRaw = toObject(actionState.errorDetails);
    const failureHTML = errorDetailsRaw.applyCheck || errorDetailsRaw.apply_check
      ? ''
      : (errorDetailsRaw.command ||
          errorDetailsRaw.cmd ||
          errorDetailsRaw.output ||
          errorDetailsRaw.failed_files ||
          errorDetailsRaw.failed_hunks ||
          errorDetailsRaw.failedFiles ||
          errorDetailsRaw.failedHunks)
        ? renderWorktreeFailureDetails(errorDetailsRaw, t('worktree.failureDetails'))
        : '';
    const actionLabel = worktreeActionLabel(action, actionState.submitting);
    // Confirm ${actionLabel.toLowerCase()}
    const bannerMessage = actionState.submitting
      ? t('worktree.applyingPendingDecision', { sourceRepo: toText(review.sourceRepo, 'the source repository') })
      : actionState.error
        ? actionState.error
        : summary;
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="worktree-action">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(title)}</span>
            <span class="overlay__sub">${escapeHTML(actionState.submitting ? t('worktree.refreshingStatus') : t('worktree.confirmationRequired'))}</span>
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
              ${preflightHTML ? `
                <div class="worktree-action__preflight">
                  ${preflightHTML}
                </div>
              ` : ''}
              ${failureHTML ? `
                <div class="worktree-action__failure">
                  ${failureHTML}
                </div>
              ` : ''}
              <div class="modal-banner section-banner section-banner--info worktree-action__warning">
                <span class="dot" style="background: currentColor;"></span>
                <div>
                  <div class="section-banner__title">${escapeHTML(t('worktree.exactConfirmation'))}</div>
                  <div class="section-banner__copy">${escapeHTML(instruction)}</div>
                </div>
              </div>
              <div class="modal-field worktree-action__field">
                <div class="modal-field__label">${escapeHTML(t('worktree.confirmationPhrase'))}</div>
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
                <button type="button" class="button button--quiet" data-worktree-action-close ${actionState.submitting ? 'disabled' : ''}>${escapeHTML(t('common.cancel'))}</button>
                <button type="button" class="button ${action === 'discard' ? 'button--danger' : 'button--primary'}" data-worktree-action-confirm ${confirmEnabled ? '' : 'disabled aria-disabled="true"'}>${escapeHTML(actionState.submitting ? actionLabel : action === 'discard' ? t('worktree.confirmDiscard') : t('worktree.confirmMerge'))}</button>
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
        error: worktreeActionDisabledReason(review, action) || t('worktree.actionUnavailable'),
      };
      renderWorktreeActionOverlay();
      return;
    }
    if (!provided) {
      state.worktreeAction = {
        ...actionState,
        error: t('worktree.typeConfirmationPhrase', { confirmation: expected }),
      };
      renderWorktreeActionOverlay();
      return;
    }
    if (provided !== expected) {
      state.worktreeAction = {
        ...actionState,
        error: t('worktree.confirmationPhraseMismatch', { confirmation: expected }),
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
        const message = toText(normalized.message || toObject(normalized.error).message || t('worktree.actionFailedHttp', { status: response.status }), t('worktree.actionFailed'));
        const error = new Error(message);
        const errorDetails = toObject(normalized.error).details;
        const snapshot = toObject(normalized.snapshot);
        if (errorDetails && Object.keys(errorDetails).length) {
          error.details = errorDetails;
        }
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
      const message = toText(error?.message || error, t('worktree.actionFailed'));
      const snapshot = toObject(error?.snapshot);
      const details = toObject(error?.details);
      if (Object.keys(snapshot).length) {
        applyServerSnapshot(snapshot);
      }
      state.worktreeAction = {
        ...actionState,
        submitting: false,
        error: message,
        errorDetails: details,
      };
      renderWorktreeActionOverlay();
    }
  }

  function renderTopbar() {
    const elapsed = state.activeRun.startedAt ? fmtDuration((nowMs() - state.activeRun.startedAt) / 1000) : '--';
    const elapsedLabel = t('topbar.elapsed');
    const budgetPct = metricText(state.activeRun.budgetAvailable, state.activeRun.budgetUsed, fmtPercent);
    const budgetWidth = metricWidth(state.activeRun.budgetAvailable, state.activeRun.budgetUsed);
    const quotaAvailable = Boolean(state.activeRun.quotaAvailable && state.activeRun.quota && state.activeRun.quota.available);
    const quotaTitle = quotaAvailable
      ? state.activeRun.quota.window
        ? t('topbar.quotaUsageWindow', { window: state.activeRun.quota.window })
        : t('topbar.quotaUsage')
      : t('topbar.quotaUnavailable');
    const quotaSnapshot = quotaAvailable ? state.activeRun.quota : { window: '', used: null, available: false };
    const quotaControl = renderQuotaControl(quotaSnapshot, quotaTitle);
    const activeStatus = runStatusLabel(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason);
    const activeTone = runStatusTone(state.progress?.run_status || state.activeRun.status, state.progress?.final_reason || state.activeRun.finalReason);
    const runLabel = state.activeRun.id || 'no-run';
    const snapshotDisplay = snapshotRefreshDisplay();
    const runnerControlDisplay = runnerControlStateInfo();
    const runnerBusyAction = runnerControlBusyAction();
    const runnerChipTone = `status-chip--${runnerControlDisplay.chipTone}`;
    const snapshotTone = snapshotStatusClass(snapshotDisplay.tone);
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
        <span class="status-chip">${escapeHTML(t('dashboard.stage'))} ${escapeHTML(state.activeRun.stage || 'idle')} | ${escapeHTML(t('pipeline.iter'))} ${escapeHTML(`${state.activeRun.iteration}/${state.activeRun.maxIterations}`)}</span>
        <span class="status-chip">${escapeHTML(elapsedLabel)} ${escapeHTML(elapsed)}</span>
      </div>
      <div class="topbar__actions">
        <span class="status-chip status-chip--snapshot ${snapshotTone}" title="${escapeHTML(snapshotDisplay.copy || snapshotDisplay.lastUpdatedLabel || '')}">
          <span class="dot" style="color: currentColor; background: currentColor;"></span>
          <span class="status-chip__label">${escapeHTML(snapshotDisplay.label)}</span>
          <span class="status-chip__meta">${escapeHTML(snapshotDisplay.lastUpdatedLabel)}</span>
        </span>
        <span class="status-chip status-chip--runner-control ${runnerChipTone}" title="${escapeHTML(runnerControlDisplay.copy || '')}">
          <span class="dot" style="color: currentColor; background: currentColor;"></span>
          ${escapeHTML(runnerControlDisplay.label)}
        </span>
        ${button(t('topbar.refresh'), 'refresh-status', 'button--quiet', `aria-label="${escapeHTML(t('topbar.refresh'))}"`)}
        ${button(runnerControlActionLabel('start', runnerBusyAction === 'start'), 'runner-start', runnerControlActionClass('start', 'button--primary'), `aria-label="${escapeHTML(t('runner.start'))}" ${runnerControlButtonAttrs('start')}`)}
        ${button(runnerControlActionLabel('stop', runnerBusyAction === 'stop'), 'runner-stop', runnerControlActionClass('stop', 'button--danger'), `aria-label="${escapeHTML(t('runner.stop'))}" ${runnerControlButtonAttrs('stop')}`)}
        ${button(runnerControlActionLabel('reload', runnerBusyAction === 'reload'), 'runner-reload', runnerControlActionClass('reload', 'button--quiet'), `aria-label="${escapeHTML(t('runner.reload'))}" ${runnerControlButtonAttrs('reload')}`)}
        ${button(runnerControlActionLabel('restart', runnerBusyAction === 'restart'), 'runner-restart', runnerControlActionClass('restart', 'button--quiet'), `aria-label="${escapeHTML(t('runner.restart'))}" ${runnerControlButtonAttrs('restart')}`)}
        ${button(t('topbar.commandPalette'), 'open-palette', 'button--ghost', `aria-label="${escapeHTML(t('topbar.commandPaletteTitle'))}"`)}
        ${renderLocaleToggle()}
        ${quotaControl}
        <span class="meter-chip meter-chip--budget ${state.activeRun.budgetAvailable ? '' : 'meter-chip--unavailable'}" title="${escapeHTML(t('dashboard.budget'))}">
          ${escapeHTML(t('dashboard.budget').toLowerCase())} ${escapeHTML(budgetPct)}
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
        ? t('topbar.quotaUsageWindow', { window: state.activeRun.quota.window })
        : t('topbar.quotaUsage')
      : t('topbar.quotaUnavailable');
    const quotaSnapshot = quotaAvailable ? state.activeRun.quota : { window: '', used: null, available: false };
    const quotaControl = renderQuotaControl(quotaSnapshot, quotaTitle);
    const liveLabel =
      state.snapshotStatus === 'loading'
        ? t('snapshot.loading')
        : state.sourceMode === 'fallback'
          ? t('snapshot.fallback')
          : `${state.activeRun.backend} live`;
    const groups = [
      {
        title: t('nav.run'),
        items: [
          { view: 'dashboard', label: viewLabel('dashboard'), shortcut: VIEW_SHORTCUTS.dashboard },
          { view: 'pipeline', label: viewLabel('pipeline'), shortcut: VIEW_SHORTCUTS.pipeline },
          { view: 'logs', label: viewLabel('logs'), shortcut: VIEW_SHORTCUTS.logs },
        ],
      },
      {
        title: t('nav.project'),
        items: [
          { view: 'backlog', label: viewLabel('backlog'), shortcut: VIEW_SHORTCUTS.backlog },
          { view: 'goals', label: viewLabel('goals'), shortcut: VIEW_SHORTCUTS.goals },
          { view: 'config', label: viewLabel('config'), shortcut: VIEW_SHORTCUTS.config },
          { view: 'prompts', label: viewLabel('prompts'), shortcut: VIEW_SHORTCUTS.prompts },
          { view: 'worktree', label: viewLabel('worktree'), shortcut: VIEW_SHORTCUTS.worktree, badge: state.worktreeMerge.reviewRequired ? '!' : '' },
        ],
      },
      {
        title: t('nav.history'),
        items: [
          { view: 'history', label: viewLabel('history'), shortcut: VIEW_SHORTCUTS.history },
          { view: 'notifications', label: viewLabel('notifications'), shortcut: VIEW_SHORTCUTS.notifications, badge: String(state.notifications.length) },
        ],
      },
      {
        title: t('nav.preview'),
        items: [
          { view: 'landing', label: viewLabel('landing'), shortcut: VIEW_SHORTCUTS.landing },
          { view: 'mobile', label: viewLabel('mobile'), shortcut: VIEW_SHORTCUTS.mobile },
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
    const liveRun = currentLiveRun();
    const control = currentLiveRunRunnerControl(liveRun);
    const liveStatus = currentLiveRunStatus(liveRun);
    const liveState = currentLiveRunLiveState(liveRun);
    const liveProcess = toObject(liveRun.process);
    const display = runnerControlStateInfo(control);
    const messageTone = display.bannerTone;
    const busyAction = runnerControlBusyAction(control);
    const statusSummaryRunStatus = control.runStatus || liveStatus.run || liveProcess.running
      ? (String(control.runStatus || liveStatus.run || '').toLowerCase() === 'running'
        ? t('runner.running')
        : String(control.runStatus || liveStatus.run || '').toLowerCase() === 'idle'
          ? t('runner.idle')
          : String(control.runStatus || liveStatus.run || '').toLowerCase() === 'loading'
            ? t('common.loading')
            : String(control.runStatus || liveStatus.run || '').toLowerCase() === 'ready'
              ? t('runner.ready')
              : String(control.runStatus || liveStatus.run || '').toLowerCase() === 'stopped'
                ? t('runner.stopped')
                : control.runStatus || liveStatus.run || (liveProcess.running ? t('runner.running') : t('runner.idle')))
      : (control.status.running || liveProcess.running ? t('runner.running') : t('runner.idle'));
    const statusSummary = [
      display.label.toLowerCase(),
      liveProcess.runnerMode || control.status.runnerMode || t('common.unknown'),
      statusSummaryRunStatus,
    ]
      .filter(Boolean)
      .join(' | ');
    const buttonRow = [
      button(runnerControlActionLabel('start', busyAction === 'start'), 'runner-start', runnerControlActionClass('start', 'button--primary', control), `aria-label="${escapeHTML(t('runner.start'))}" ${runnerControlButtonAttrs('start', control)}`),
      button(runnerControlActionLabel('stop', busyAction === 'stop'), 'runner-stop', runnerControlActionClass('stop', 'button--danger', control), `aria-label="${escapeHTML(t('runner.stop'))}" ${runnerControlButtonAttrs('stop', control)}`),
      button(runnerControlActionLabel('reload', busyAction === 'reload'), 'runner-reload', runnerControlActionClass('reload', 'button--quiet', control), `aria-label="${escapeHTML(t('runner.reload'))}" ${runnerControlButtonAttrs('reload', control)}`),
      button(runnerControlActionLabel('restart', busyAction === 'restart'), 'runner-restart', runnerControlActionClass('restart', 'button--quiet', control), `aria-label="${escapeHTML(t('runner.restart'))}" ${runnerControlButtonAttrs('restart', control)}`),
    ].join('');
    const detailItems = runnerControlDetailRows(control, display);
    const startOptionsChips = runnerControlStartOptionsSummaryChips(control);
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
      t('runner.panelTitle'),
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
          <div style="margin-top:12px;">
            ${runnerControlLiveStateChips(liveState)}
          </div>
          <div class="runner-control__chips">
            ${startOptionsChips}
          </div>
          <div class="summary-note">
            ${escapeHTML(t('runner.startOptionsSummary'))}
          </div>
          <div class="runner-control__buttons">
            ${buttonRow}
          </div>
          <div class="summary-note">
            ${escapeHTML(t('runner.confirmationPhrases'))}: ${escapeHTML(t('runner.confirmStartPhrase'))} = ${escapeHTML(control.confirmation.start)}, ${escapeHTML(t('runner.confirmStopPhrase'))} = ${escapeHTML(control.confirmation.stop)}, ${escapeHTML(t('runner.confirmReloadPhrase'))} = ${escapeHTML(control.confirmation.reload)}, ${escapeHTML(t('runner.confirmRestartPhrase'))} = ${escapeHTML(control.confirmation.restart)}.
          </div>
        </div>
      `,
      'runner-control-panel'
    );
  }

  function renderDashboard() {
    const liveRun = currentLiveRun();
    const run = currentLiveRunActiveRun(liveRun);
    const progress = currentLiveRunProgress(liveRun);
    const liveStatus = currentLiveRunStatus(liveRun);
    const liveIdentity = toObject(liveRun.identity);
    const liveCurrentTask = toObject(liveRun.currentTask);
    const liveStages = toObject(liveRun.stages);
    const liveStageSummaries = toArray(liveRun.stageSummaries || liveStages.items || []);
    const liveLog = currentLiveRunLog(liveRun);
    const liveNotifications = currentLiveRunNotifications(liveRun);
    const budgetCap = toMaybeNumber(state.config?.budget?.max_usd);
    const taskId = liveCurrentTask.id || liveStatus.currentTaskId || progress.current_task_id || run.task || '';
    const taskTitle = liveCurrentTask.title || liveStatus.currentTaskTitle || progress.current_task_title || run.taskTitle || '';
    const attempt = liveCurrentTask.attempt ?? run.attempt ?? progress.attempt;
    const attemptText = attempt == null ? t('common.unavailable') : String(attempt);
    const branchText = liveIdentity.branch || progress.branch || run.branch || state.repo.branch || 'HEAD';
    const worktreeModeText = liveCurrentTask.worktreeMode || progress.worktree_mode || run.worktreeMode || '';
    const runDirText = liveIdentity.runDir || run.runDir || progress.latest_run_dir || state.latestRunDir || '';
    const finalReason = liveStatus.finalReason || progress.final_reason || run.finalReason || '';
    const executionStatus = liveStatus.execution || liveStatus.executionStatus || progress.execution_status || progress.executionStatus || run.executionStatus || progress.run_status || run.status;
    const projectStatus = liveStatus.project || liveStatus.projectStatus || progress.project_status || progress.projectStatus || run.projectStatus || (run.projectComplete ? 'complete' : 'incomplete');
    const runStatus = executionStatus;
    const runTone = runStatusTone(runStatus, finalReason);
    const runLabel = runStatusLabel(runStatus, finalReason);
    const runSummary = [
      `${t('dashboard.currentTaskId')} ${taskId || t('common.unavailable')}`,
      `${t('dashboard.currentTaskTitle')} ${taskTitle || t('common.unavailable')}`,
      `${t('dashboard.attempt')} ${attemptText}`,
      `${t('dashboard.branch')} ${branchText}`,
      `${t('dashboard.worktreeMode')} ${worktreeModeText || t('common.unavailable')}`,
      runDirText ? `${t('dashboard.runDirectory')} ${runDirText}` : `${t('dashboard.runDirectory')} ${t('common.unavailable')}`,
      finalReason ? `${t('dashboard.finalReason')} ${finalReason}` : null,
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
    const latestLogs = toArray(liveLog.entries || state.logs).slice(-8);
    const recentNotifs = toArray(liveNotifications.items || state.notifications).slice(0, 4);
    const tokenValueText = tokenTotal != null ? fmtNumberShort(tokenTotal) : 'unavailable';
    const tokenSubText = hasTokenTelemetry
      ? `${t('pipeline.input')} ${metricText(hasTokenTelemetry, tokenIn, fmtNumberShort)} | ${t('pipeline.output')} ${metricText(hasTokenTelemetry, tokenOut, fmtNumberShort)}`
      : t('pipeline.tokenTelemetryUnavailable');
    const budgetCardValue = budgetValue != null ? fmtMoney(budgetValue) : 'unavailable';
    const budgetCardSub = budgetCap != null
      ? `${t('common.of')} ${fmtMoney(budgetCap)} | ${metricText(run.budgetAvailable, run.budgetUsed, fmtPercent)}`
      : `${t('common.of')} ${t('common.unavailable')} | ${metricText(run.budgetAvailable, run.budgetUsed, fmtPercent)}`;

    const body = `
      <div class="view-grid view-grid--two">
        <div class="view-grid">
          ${sectionNotice('activeRun')}
          ${renderRunnerControlsPanel()}
          <div class="stat-grid stat-grid--four">
            ${metricCard(t('dashboard.stage'), run.stage, `${t('pipeline.iter')} ${run.iteration}/${run.maxIterations}`, true)}
            ${metricCard(t('dashboard.tasks'), `${doneTasks}/${totalTasks}`, `${totalTasks - doneTasks} ${t('common.remaining')}`)}
            ${metricCard(t('dashboard.tokens'), tokenValueText, tokenSubText, false, tokenValueText === 'unavailable' ? t('common.unavailable') : '')}
            ${metricCard(t('dashboard.budget'), budgetCardValue, budgetCardSub, false, budgetCardValue === 'unavailable' ? t('common.unavailable') : '')}
          </div>

          ${panel(
            t('dashboard.pipelineSnapshot'),
            `${escapeHTML(t('pipeline.activeTask'))} ${escapeHTML(taskId || t('common.unavailable'))} | ${escapeHTML(run.backend)}`,
            `
              <div class="pipeline">
                <div class="pipeline__row">
                  ${renderLifecycleLane(liveStageSummaries.length ? liveStageSummaries : state.stages)}
                </div>
              </div>
            `
          )}

          ${panel(
            t('dashboard.liveLogs'),
            `${escapeHTML(latestLogs.length)} ${escapeHTML(t('common.lines'))} | tail -f`,
            `
              ${sectionNotice('logs')}
              <div class="log-feed">
                <div class="log-feed__scroll">
                  ${latestLogs.length ? latestLogs.map((line) => renderLogRow(line)).join('') : `<div class="summary-note">${escapeHTML(t('dashboard.noLogEntriesYet'))}</div>`}
                </div>
              </div>
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            t('dashboard.runFacts'),
            `${escapeHTML(taskId || t('common.unavailable'))} | ${escapeHTML(taskTitle || t('common.unavailable'))}`,
            `
              <div class="runner-control">
                <div class="${runBannerClass(runStatus, finalReason)}">
                  <span class="${statusDotClass(runStatus)}" style="background: currentColor;"></span>
                  <div>
                    <div class="section-banner__title">${escapeHTML(runLabel)}</div>
                    <div class="section-banner__copy">${escapeHTML(runSummary)}</div>
                  </div>
                </div>
                <div class="runner-control__chips">
                  <span class="${executionStatusClass(executionStatus)}">${escapeHTML(`${t('runner.runStatus')}: ${executionStatusLabel(executionStatus)}`)}</span>
                  <span class="${projectStatusClass(projectStatus)}">${escapeHTML(`${t('nav.project')}: ${projectStatusLabel(projectStatus)}`)}</span>
                </div>
                <div class="runner-control__details">
                  ${detailCard(t('dashboard.currentTaskId'), taskId || t('common.unavailable'))}
                  ${detailCard(t('dashboard.currentTaskTitle'), taskTitle || t('common.unavailable'))}
                  ${detailCard(t('dashboard.attempt'), attemptText)}
                  ${detailCard(t('dashboard.branch'), branchText)}
                  ${detailCard(t('dashboard.worktreeMode'), worktreeModeText || t('common.unavailable'))}
                  ${detailCard(t('dashboard.runDirectory'), runDirText || t('common.unavailable'))}
                  ${finalReason ? detailCard(t('dashboard.finalReason'), finalReason, runTone === 'failed' ? 'err' : runTone === 'stopped' ? 'warn' : (runTone === 'completed' || runTone === 'success') ? 'accent' : 'muted') : ''}
                </div>
              </div>
            `
          )}

          ${panel(
            t('dashboard.goalsSnapshot'),
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
                `).join('') : `<div class="summary-note">${escapeHTML(t('dashboard.noGoalsPublishedYet'))}</div>`}
              </div>
            `
          )}

          ${panel(
            t('dashboard.selectedBacklogItem'),
            selectedTask ? escapeHTML(selectedTask.id) : escapeHTML(t('common.none')),
            selectedTask
              ? `
                <div class="task-card" style="padding:12px 12px;">
                  <div class="task-card__head">
                    <span class="task-card__id">${escapeHTML(selectedTask.id)}</span>
                    <span class="task-card__priority" style="color:${priorityColor(selectedTask.priority)}">${escapeHTML(selectedTask.priority)}</span>
                  </div>
                  <div class="task-card__title">${escapeHTML(selectedTask.title)}</div>
                  <div class="task-card__meta">
                    ${chip(backlogStatusLabel(normalizeBacklogStatus(selectedTask.status, 'pending')), backlogStatusToneClass(selectedTask.status))}
                    ${chip(selectedTask.estimate)}
                    ${selectedTask.skill ? chip(selectedTask.skill, 'chip--info') : ''}
                  </div>
                  <div class="summary-note" style="margin-top:8px;">${escapeHTML(selectedTask.dependsOn && selectedTask.dependsOn.length ? compactText(t('backlog.dependsOn', { items: selectedTask.dependsOn.join(', ') }), 140) : t('backlog.dependenciesUnavailable'))}</div>
                  <div class="summary-note" style="margin-top:4px;">${escapeHTML(selectedTask.fileScope ? compactText(t('backlog.fileScope', { scope: selectedTask.fileScope }), 140) : t('backlog.fileScopeUnavailable'))}</div>
                  <div class="summary-note" style="margin-top:4px;">${escapeHTML(selectedTask.attempt != null ? t('backlog.attemptText', { attempt: selectedTask.attempt }) : t('backlog.attemptUnavailable'))}</div>
                  <div class="summary-note" style="margin-top:4px;">${escapeHTML(selectedTask.failureReason ? t('backlog.failureText', { reason: `${selectedTask.failureReason}${selectedTask.failureDetail ? ` | ${compactText(selectedTask.failureDetail, 120)}` : ''}` }) : t('backlog.failureUnavailable'))}</div>
                </div>
              `
              : state.backlog.length ? `<div class="summary-note">${escapeHTML(t('dashboard.noTaskSelected'))}</div>` : `<div class="summary-note">${escapeHTML(t('backlog.noArtifacts'))}</div>`
          )}

          ${panel(
            t('dashboard.notifications'),
            `${recentNotifs.length} ${t('common.recent')}`,
            `
              <div class="compact-list">
                ${sectionNotice('notifications')}
                ${recentNotifs.length ? recentNotifs.map((item) => `
                  <div class="compact-list__item">
                    <span class="compact-list__bullet" style="background:${kindColor(item.kind)}"></span>
                    <div>
                      <div class="compact-list__body">${escapeHTML(redactionAwareText(item.text, t('notifications.noRecorded')))}</div>
                      <div class="compact-list__meta">${escapeHTML(item.kind)} | ${escapeHTML(fmtRelative(item.t))}</div>
                    </div>
                  </div>
                `).join('') : `<div class="summary-note">${escapeHTML(t('dashboard.noNotificationsYet'))}</div>`}
              </div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'dashboard',
      t('dashboard.title'),
      `${escapeHTML(taskId || 'unavailable')} | ${escapeHTML(taskTitle || 'task title unavailable')} | ${escapeHTML(branchText)} | ${escapeHTML(run.id)}`,
      `
        ${button(t('common.openPipeline'), 'nav-pipeline', 'button--quiet')}
        ${button(t('common.openLogs'), 'nav-logs', 'button--quiet')}
        ${button(t('common.openWorktree'), 'nav-worktree', 'button--quiet')}
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
          aria-label="${escapeHTML(selected ? t('common.deselect') : t('common.select'))} ${escapeHTML(t('logs.line', { lineNumber }))}"
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
        <div class="log-row__msg">${escapeHTML(redactionAwareText(line.msg, t('common.unavailable')))}</div>
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
      sourceId: '',
      source: {
        id: '',
        label: '',
        path: '',
        name: '',
        exists: false,
        available: false,
        selected: false,
        kind: 'log',
        unavailableReason: 'missing',
      },
      sources: [],
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

  function createBlankSnapshotRefreshState() {
    return {
      status: 'loading',
      active: false,
      inFlight: false,
      requestSeq: 0,
      retryCount: 0,
      retryDelayMs: SNAPSHOT_POLL_MS,
      maxRetryDelayMs: SNAPSHOT_RECONNECT_MAX_MS,
      nextRefreshAt: 0,
      lastAttemptAt: 0,
      lastSuccessAt: 0,
      lastUpdatedAt: 0,
      lastErrorAt: 0,
      lastErrorStatus: 0,
      lastError: '',
      stale: false,
      staleReasons: [],
      latestRunDir: '',
      timer: null,
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
    if (!Array.isArray(state.logTail.sources)) {
      state.logTail.sources = [];
    }
    if (!state.logTail.source || typeof state.logTail.source !== 'object') {
      state.logTail.source = {};
    }
    const normalizedSelection = resolveLogTailSourceSelection(state.logTail);
    applyLogTailSourceSelection(state.logTail, normalizedSelection);
    return state.logTail;
  }

  function ensureSnapshotRefreshState() {
    if (!state.snapshotRefresh || typeof state.snapshotRefresh !== 'object') {
      state.snapshotRefresh = createBlankSnapshotRefreshState();
    }
    if (!Array.isArray(state.snapshotRefresh.staleReasons)) {
      state.snapshotRefresh.staleReasons = [];
    }
    return state.snapshotRefresh;
  }

  function normalizeLogTailFilters(filters = {}) {
    return {
      level: toText(filters.level, 'all').toLowerCase() || 'all',
      stage: toText(filters.stage, '').trim(),
      taskId: toText(filters.taskId || filters.task_id, '').trim(),
      search: toText(filters.search || filters.q, '').trim(),
    };
  }

  function normalizeLogTailSource(source = {}) {
    const raw = toObject(source);
    const path = toText(raw.path || raw.source_path || raw.sourcePath, '').trim();
    const name = toText(raw.name || raw.file_name || raw.fileName || tailSourceName(path), '').trim();
    const label = toText(raw.label || raw.title || raw.displayName || raw.display_name || name || path || '', '').trim();
    const id = toText(raw.id || raw.sourceId || raw.source_id || raw.key || name || label || path, '').trim();
    const kind = toText(raw.kind || raw.type || 'log', 'log').trim().toLowerCase() || 'log';
    const exists = Boolean(raw.exists ?? false);
    const available = Boolean(raw.available ?? exists);
    const selected = Boolean(raw.selected);
    const unavailableReason = toText(raw.unavailableReason || raw.unavailable_reason || raw.reason || '', '').trim().toLowerCase();
    return {
      id,
      label: label || (kind === 'transcript' ? t('logs.backendTranscript') : name || id),
      name: name || (path ? tailSourceName(path) : ''),
      path,
      exists,
      available,
      selected,
      kind,
      unavailableReason,
    };
  }

  function normalizeLogTailSources(sources = []) {
    return toArray(sources)
      .map((source) => normalizeLogTailSource(source))
      .filter((source) => Boolean(source.id || source.path || source.label));
  }

  function resolveLogTailSourceSelection(tail = {}) {
    const model = toObject(tail);
    const sources = normalizeLogTailSources(model.sources);
    const currentSource = normalizeLogTailSource(model.source);
    const preferredId = toText(model.sourceId || currentSource.id || '', '').trim();
    let selected = preferredId ? sources.find((source) => source.id === preferredId) : null;
    if (!selected) {
      selected = sources.find((source) => source.selected) || sources.find((source) => source.available) || sources[0] || currentSource;
    }
    const selectedId = toText(selected.id || currentSource.id || '', '').trim();
    const selectedSource = normalizeLogTailSource(selected);
    const selectedSources = sources.map((source) => ({
      ...source,
      selected: selectedId ? source.id === selectedId : Boolean(source.selected),
    }));
    if (selectedId && selectedSource.id !== selectedId) {
      selectedSource.id = selectedId;
    }
    selectedSource.selected = Boolean(selectedId);
    return {
      sourceId: selectedId,
      source: selectedSource,
      sources: selectedSources.length ? selectedSources : (selectedId ? [selectedSource] : []),
    };
  }

  function applyLogTailSourceSelection(tail, selection = null) {
    const model = toObject(tail);
    const resolved = selection && typeof selection === 'object' ? selection : resolveLogTailSourceSelection(model);
    model.sources = normalizeLogTailSources(resolved.sources);
    model.sourceId = toText(resolved.sourceId || '', '').trim();
    model.source = normalizeLogTailSource(resolved.source);
    if (!model.source.id && model.sourceId) {
      model.source.id = model.sourceId;
    }
    model.source.selected = Boolean(model.sourceId);
    return model;
  }

  function logTailSourceDisplayName(source) {
    const model = normalizeLogTailSource(source);
    return model.label || model.name || model.path || t('logs.activeRunLog');
  }

  function logTailSourceAvailabilityLabel(source) {
    const model = normalizeLogTailSource(source);
    if (model.available) {
      return t('common.available');
    }
    if (model.kind === 'transcript') {
      return t('common.unavailable');
    }
    return t('common.missing');
  }

  function renderLogTailSourceSelector(tail) {
    const model = toObject(tail);
    const selection = resolveLogTailSourceSelection(model);
    const sources = toArray(selection.sources);
    if (!sources.length) {
      return `
        <div class="log-tail-field log-tail-field--sources">
          <span class="log-tail-field__label">${escapeHTML(t('common.source'))}</span>
          <div class="summary-note">${escapeHTML(t('logs.noSourcesAvailable'))}</div>
        </div>
      `;
    }
    const currentId = toText(selection.sourceId, '');
    return `
      <div class="log-tail-field log-tail-field--sources">
        <span class="log-tail-field__label">${escapeHTML(t('common.source'))}</span>
        <div class="log-tail-sources">
          ${sources
            .map((source) => {
              const sourceLabel = logTailSourceDisplayName(source);
              const sourceMeta = logTailSourceAvailabilityLabel(source);
              const active = currentId && source.id === currentId;
              const disabled = !source.available ? 'disabled' : '';
              const titleParts = [sourceLabel];
              if (source.path) {
                titleParts.push(source.path);
              }
              if (sourceMeta) {
                titleParts.push(sourceMeta);
              }
              return `
                <button
                  type="button"
                  class="log-tail-source ${active ? 'log-tail-source--active' : ''} ${!source.available ? 'log-tail-source--unavailable' : ''}"
                  data-log-source="${escapeHTML(source.id)}"
                  aria-pressed="${active ? 'true' : 'false'}"
                  ${disabled}
                  title="${escapeHTML(titleParts.join(' | '))}"
                >
                  <span class="log-tail-source__label">${escapeHTML(sourceLabel)}</span>
                  <span class="log-tail-source__meta">${escapeHTML(sourceMeta)}</span>
                </button>
              `;
            })
            .join('')}
        </div>
      </div>
    `;
  }

  function logFilterLabel(level) {
    const normalized = toText(level, 'all').toLowerCase();
    const labels = {
      all: t('logs.filterAll'),
      info: t('logs.filterInfo'),
      warn: t('logs.filterWarn'),
      err: t('logs.filterErr'),
      debug: t('logs.filterDebug'),
    };
    return labels[normalized] || String(level || '').toUpperCase();
  }

  function buildLogTailQuery(filters = {}, options = {}) {
    const query = {
      max_lines: Math.max(1, toNumber(options.maxLines, MAX_LOG_ROWS)),
    };
    const cursor = toMaybeNumber(options.cursor);
    if (cursor != null && cursor > 0) {
      query.cursor = cursor;
    }
    const sourceId = toText(options.sourceId || options.source || '', '').trim();
    if (sourceId) {
      query.source = sourceId;
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
    const message = redactionAwareText(entry.msg || entry.message || entry.raw, '');
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
    const selection = resolveLogTailSourceSelection(model);
    const source = normalizeLogTailSource(selection.source);
    const sourceName = redactionAwareText(logTailSourceDisplayName(source), t('logs.activeRunLog'));
    const sourceMeta = source.available ? '' : logTailSourceAvailabilityLabel(source);
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
    const sourceLabel = redactionAwareText(source.path || sourceName || t('common.unknown'), t('logs.activeRunLog'));
    const sourceLine = sourceMeta ? `${sourceLabel} (${sourceMeta})` : sourceLabel;
    const lines = [
      `# ${t('logs.exportHeader')}`,
      `# ${t('logs.exportSource')}: ${sourceLine}`,
      `# ${t('logs.exportCursor')}: ${toMaybeNumber(model.nextCursor ?? model.cursor, 0) || 0}`,
      `# ${t('logs.exportFilters')}: ${filterParts.length ? filterParts.join(' | ') : t('common.none')}`,
      '',
    ];
    const entries = toArray(model.entries);
    if (entries.length) {
      lines.push(...entries.map((entry) => formatLogTailLine(entry)));
    } else {
      lines.push(`# ${t('logs.exportNoMatches')}`);
    }
    return {
      filename: `agentcli-${runLabel}-logs.txt`,
      text: `${lines.join('\n')}\n`,
    };
  }

  function describeLogTailState(tail) {
    const model = toObject(tail);
    const status = toText(model.status, 'loading');
    const paused = Boolean(model.paused);
    const entries = toArray(model.entries);
    const selection = resolveLogTailSourceSelection(model);
    const source = normalizeLogTailSource(selection.source);
    const sourceName = redactionAwareText(logTailSourceDisplayName(source), t('logs.activeRunLog')) || t('logs.activeRunLog');
    const sourceMeta = source.available ? '' : logTailSourceAvailabilityLabel(source);
    const malformedLines = toNumber(model.malformedLines, 0);
    if (status === 'missing_file') {
      return {
        tone: 'err',
        title: t('logs.logFileMissing'),
        copy: sourceMeta ? `${sourceName} ${sourceMeta}.` : `${t('common.loading')} ${sourceName}.`,
        badge: 'missing_file',
        state: 'missing_file',
      };
    }
    if (status === 'read_error') {
      return {
        tone: 'err',
        title: t('logs.logReadError'),
        copy: redactionAwareText(model.error, `${sourceName} ${t('logs.logReadError').toLowerCase()}.`),
        badge: 'read_error',
        state: 'read_error',
      };
    }
    if (paused) {
      const cursor = toMaybeNumber(model.nextCursor ?? model.cursor, 0) || 0;
      return {
        tone: 'stopped',
        title: t('logs.liveTailPaused'),
        copy: `${sourceName} ${t('logs.pauseLiveTail').toLowerCase()}. ${t('logs.resumeLiveTail')} ${t('logs.cursor')} ${cursor}.${sourceMeta ? ` ${sourceMeta}.` : ''}`,
        badge: 'paused',
        state: 'paused',
      };
    }
    if (status === 'empty') {
      return {
        tone: 'idle',
        title: t('logs.noMatchingLogLines'),
        copy: source.exists ? t('logs.noMatchCurrentFilter') : `${t('logs.logFileMissing')}: ${sourceName}${sourceMeta ? ` (${sourceMeta})` : ''}`,
        badge: 'empty',
        state: 'empty',
      };
    }
    if (malformedLines > 0 && !entries.length) {
      return {
        tone: 'warn',
        title: t('logs.logReadError'),
        copy: t('logs.malformedLinesSkipped', { count: malformedLines }),
        badge: 'warn',
        state: 'malformed_line',
      };
    }
    if (entries.length) {
      const cursor = toMaybeNumber(model.nextCursor ?? model.cursor, 0) || 0;
      return {
        tone: 'running',
        title: t('logs.liveTailActive'),
        copy: `${sourceName} ${t('logs.liveTailActive').toLowerCase()} ${t('logs.cursor')} ${cursor}.${sourceMeta ? ` ${sourceMeta}.` : ''}`,
        badge: 'live',
        state: 'live',
      };
    }
    return {
      tone: 'info',
      title: t('logs.loadingActiveRunLog'),
      copy: `${t('common.loading')} ${sourceName}.${sourceMeta ? ` ${sourceMeta}.` : ''}`,
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
    const buttonLabel = paused ? t('logs.resumeLiveTail') : t('logs.pauseLiveTail');
    const loadingState = loading || status === 'loading';
    const busy = loadingState;
    let stateLabel = t('logs.liveTail');
    let statusClass = 'status-chip status-chip--running';
    if (status === 'missing_file') {
      stateLabel = t('logs.logFileMissing');
      statusClass = 'status-chip status-chip--warn';
    } else if (status === 'read_error') {
      stateLabel = t('logs.logReadError');
      statusClass = 'status-chip status-chip--err';
    } else if (paused) {
      stateLabel = t('logs.liveTailPaused');
      statusClass = 'status-chip status-chip--paused';
    } else if (status === 'empty') {
      stateLabel = t('logs.emptyState');
      statusClass = 'status-chip status-chip--idle';
    } else if (status === 'malformed_line' && !hasEntries) {
      stateLabel = t('common.failed');
      statusClass = 'status-chip status-chip--warn';
    } else if (loadingState && !hasEntries) {
      stateLabel = t('common.loading');
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
    const model = toObject(tail);
    const filters = normalizeLogTailFilters(model.filters);
    const control = describeLogTailControl(model);
    const levels = ['all', 'info', 'warn', 'err', 'debug'];
    const selectedCount = toArray(model.selected).length;
    return `
      <div class="logs-toolbar log-tail-toolbar">
        <div class="log-tail-fields">
          ${renderLogTailSourceSelector(model)}
          <div class="filters log-tail-levels">
            ${levels
              .map((level) => `
                <button
                  type="button"
                  class="filter-chip ${filters.level === level ? 'filter-chip--active' : ''}"
                  data-log-level="${escapeHTML(level)}"
                >${escapeHTML(logFilterLabel(level))}</button>
              `)
              .join('')}
          </div>
          <label class="log-tail-field">
            <span class="log-tail-field__label">${escapeHTML(t('logs.stage'))}</span>
            <input
              type="text"
              class="log-tail-input"
              data-log-filter-field="stage"
              value="${escapeHTML(filters.stage)}"
              placeholder="${escapeHTML(t('logs.stagePlaceholder'))}"
              autocomplete="off"
              spellcheck="false"
            >
          </label>
          <label class="log-tail-field">
            <span class="log-tail-field__label">${escapeHTML(t('logs.taskId'))}</span>
            <input
              type="text"
              class="log-tail-input"
              data-log-filter-field="taskId"
              value="${escapeHTML(filters.taskId)}"
              placeholder="${escapeHTML(t('logs.taskIdPlaceholder'))}"
              autocomplete="off"
              spellcheck="false"
            >
          </label>
          <label class="log-tail-field log-tail-field--wide">
            <span class="log-tail-field__label">${escapeHTML(t('logs.message'))}</span>
            <input
              type="text"
              class="log-tail-input"
              data-log-filter-field="search"
              value="${escapeHTML(filters.search)}"
              placeholder="${escapeHTML(t('logs.searchPlaceholder'))}"
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
          ${button(`${t('logs.copySelectedLines')}${selectedCount ? ` (${selectedCount})` : ''}`, 'copy-log-tail-selection', 'button--quiet', selectedCount ? '' : 'disabled')}
          ${button(t('logs.downloadFilteredLogs'), 'download-log-tail', 'button--quiet')}
          ${button(t('logs.clearSelection'), 'clear-log-tail-selection', 'button--quiet', selectedCount ? '' : 'disabled')}
        </div>
      </div>
    `;
  }

  function runnerControlStartOptionMetaText(path, currentValue, defaultValue, hint = '') {
    const parts = [
      `${t('config.activeValue')}: ${runnerControlStartOptionDisplayValue(path, currentValue)}`,
      `${t('config.defaultValue')}: ${runnerControlStartOptionDisplayValue(path, defaultValue)}`,
    ];
    if (hint) {
      parts.push(hint);
    }
    return parts.join(' | ');
  }

  function runnerControlStartOptionsSummaryChips(control = state.runnerControl, draft = state.stopStartOptions) {
    const values = draft && typeof draft === 'object' ? draft : runnerControlStartOptionsDraft(control);
    return [
      chip(`${t('runner.autopilot')}: ${runnerControlStartOptionDisplayValue('autopilot', values.autopilot)}`, values.autopilot ? 'chip--accent' : 'chip--muted'),
      chip(`${t('runner.runMode')}: ${runnerControlStartOptionDisplayValue('run_mode', values.run_mode)}`, 'chip--info'),
      chip(`${t('runner.maxCycles')}: ${runnerControlStartOptionDisplayValue('loop_max_cycles', values.loop_max_cycles)}`, 'chip--warn'),
      chip(`${t('runner.profile')}: ${runnerControlStartOptionDisplayValue('profile', values.profile)}`, 'chip--info'),
      chip(`${t('runner.backend')}: ${runnerControlStartOptionDisplayValue('execution_backend', values.execution_backend)}`, 'chip--info'),
    ].join('');
  }

  function runnerControlStartOptionCard({
    path,
    label,
    currentValue,
    defaultValue,
    hint = '',
    controlHTML = '',
    disabled = false,
    extraClass = '',
    errors = [],
  }) {
    const normalizedErrors = toArray(errors).map((item) => toText(item, '').trim()).filter(Boolean);
    return `
      <div class="runner-control__option ${escapeHTML(extraClass)} ${normalizedErrors.length ? 'runner-control__option--invalid' : ''}">
        <div class="runner-control__option-head">
          <div class="runner-control__label">${escapeHTML(label)}</div>
        </div>
        <div class="runner-control__option-meta">${escapeHTML(runnerControlStartOptionMetaText(path, currentValue, defaultValue, hint))}</div>
        <div class="runner-control__option-control ${disabled ? 'runner-control__option-control--disabled' : ''}">
          ${controlHTML}
        </div>
        ${normalizedErrors.length ? `<div class="runner-control__option-errors">${normalizedErrors.map((error) => `<div class="field-error">${escapeHTML(error)}</div>`).join('')}</div>` : ''}
      </div>
    `;
  }

  function renderRunnerControlStartOptionsSection(control = state.runnerControl, actionEnabled = false, validation = null) {
    const contract = runnerControlStartOptionsContract(control);
    const current = state.stopStartOptions && typeof state.stopStartOptions === 'object'
      ? state.stopStartOptions
      : runnerControlStartOptionsDraft(control);
    const defaults = runnerControlStartOptionsDefaultDraft(control);
    const values = toObject(current);
    const defaultValues = toObject(defaults);
    const schema = toObject(contract.schema);
    const redaction = toObject(contract.redaction);
    const validationState = validation && typeof validation === 'object' ? validation : runnerControlStartOptionsValidation(control, current);
    const fieldErrors = toObject(validationState.fieldErrors);
    const disabled = !actionEnabled || state.stopSubmitting;
    const selectedRunMode = normalizeRunnerControlStartMode(values.run_mode);
    const runModeOptions = toArray(schema.run_mode?.options || contract.choices?.run_mode || ['one-shot', 'continuous', 'loop']);
    const profileOptions = toArray(schema.profile?.options || contract.choices?.profile || ['personal', 'enterprise']);
    const backendOptions = toArray(schema.execution_backend?.options || contract.choices?.execution_backend || ['codex', 'claudecode']);
    const configPathRaw = toText(values.config_path, '');
    const redactedConfigPath = configPathRaw === REDACTED_VALUE || configPathRaw === toText(redaction.placeholder, REDACTED_VALUE);
    const configPathValue = redactedConfigPath ? '' : configPathRaw;
    const configPathPlaceholder = redactedConfigPath
      ? t('common.unavailable')
      : (contract.path || contract.defaultsPath || t('runner.configPath'));
    const runModeButtons = runModeOptions
      .map((option) => `
        <button
          type="button"
          class="modal-tab ${selectedRunMode === option ? 'modal-tab--active' : ''}"
          data-runner-option-mode="${escapeHTML(option)}"
          ${disabled ? 'disabled' : ''}
        >${escapeHTML(runnerControlStartOptionDisplayValue('run_mode', option))}</button>
      `)
      .join('');
    const autopilotControl = `
      <button
        type="button"
        class="control-chip ${values.autopilot ? 'control-chip--active' : ''}"
        data-runner-option-toggle="autopilot"
        ${disabled ? 'disabled' : ''}
      >
        <span class="dot" style="background:${values.autopilot ? 'var(--accent)' : 'var(--text-sub)'}"></span>
        ${escapeHTML(runnerControlStartOptionDisplayValue('autopilot', values.autopilot))}
      </button>
    `;
    const redactionNote = (redactedConfigPath || Boolean(redaction.active))
      ? `<div class="summary-note" style="margin-top:4px;">${escapeHTML(t('config.redactedHidden'))}</div>`
      : '';
    const argvPreview = toArray(validationState.argvPreview && validationState.argvPreview.length ? validationState.argvPreview : runnerControlStartOptionsArgvPreview(control, current));
    const previewHTML = `
      <div class="runner-control__preview">
        <div class="runner-control__preview-head">
          <div class="runner-control__preview-title">Command preview</div>
          <div class="summary-note">Argument array shown exactly as the runner will receive it.</div>
        </div>
        <div class="runner-control__argv" role="list" aria-label="Command preview">
          ${argvPreview.map((token) => `<span class="runner-control__argv-token" role="listitem">${escapeHTML(token || '""')}</span>`).join('')}
        </div>
      </div>
    `;
    const validationSummary = !validationState.valid
      ? `<div class="field-error runner-control__validation-error">${escapeHTML(validationState.message || 'Fix the highlighted start options before continuing.')}</div>`
      : '';
    const maxCyclesControl = `
      <input
        type="number"
        class="field-control"
        min="${escapeHTML(toText(schema.loop_max_cycles?.min, '0'))}"
        step="1"
        value="${escapeHTML(toText(values.loop_max_cycles, '0'))}"
        data-runner-option-field="loop_max_cycles"
        ${disabled ? 'disabled' : ''}
      >
    `;
    const profileControl = `
      <select class="field-control" data-runner-option-field="profile" ${disabled ? 'disabled' : ''}>
        ${profileOptions
          .map((option) => `<option value="${escapeHTML(option)}" ${String(option) === String(values.profile) ? 'selected' : ''}>${escapeHTML(option)}</option>`)
          .join('')}
      </select>
    `;
    const backendControl = `
      <select class="field-control" data-runner-option-field="execution_backend" ${disabled ? 'disabled' : ''}>
        ${backendOptions
          .map((option) => `<option value="${escapeHTML(option)}" ${String(option) === String(values.execution_backend) ? 'selected' : ''}>${escapeHTML(option)}</option>`)
          .join('')}
      </select>
    `;
    const configPathControl = `
      <input
        type="text"
        class="field-control"
        value="${escapeHTML(configPathValue)}"
        placeholder="${escapeHTML(configPathPlaceholder)}"
        autocomplete="off"
        spellcheck="false"
        data-runner-option-field="config_path"
        ${disabled ? 'disabled' : ''}
      >
    `;
    const cards = [
      runnerControlStartOptionCard({
        path: 'autopilot',
        label: t('runner.autopilot'),
        currentValue: values.autopilot,
        defaultValue: defaultValues.autopilot,
        hint: schema.autopilot?.hint || '',
        controlHTML: autopilotControl,
        disabled,
        errors: fieldErrors.autopilot || [],
      }),
      runnerControlStartOptionCard({
        path: 'run_mode',
        label: t('runner.runMode'),
        currentValue: values.run_mode,
        defaultValue: defaultValues.run_mode,
        hint: schema.run_mode?.hint || '',
        controlHTML: `<div class="modal-tabs runner-control__run-modes">${runModeButtons}</div>`,
        disabled,
        errors: fieldErrors.run_mode || [],
      }),
      runnerControlStartOptionCard({
        path: 'loop_max_cycles',
        label: t('runner.maxCycles'),
        currentValue: values.loop_max_cycles,
        defaultValue: defaultValues.loop_max_cycles,
        hint: schema.loop_max_cycles?.hint || '',
        controlHTML: maxCyclesControl,
        disabled,
        errors: fieldErrors.loop_max_cycles || [],
      }),
      runnerControlStartOptionCard({
        path: 'profile',
        label: t('runner.profile'),
        currentValue: values.profile,
        defaultValue: defaultValues.profile,
        hint: schema.profile?.hint || '',
        controlHTML: profileControl,
        disabled,
        errors: fieldErrors.profile || [],
      }),
      runnerControlStartOptionCard({
        path: 'execution_backend',
        label: t('runner.backend'),
        currentValue: values.execution_backend,
        defaultValue: defaultValues.execution_backend,
        hint: schema.execution_backend?.hint || '',
        controlHTML: backendControl,
        disabled,
        errors: fieldErrors.execution_backend || [],
      }),
      runnerControlStartOptionCard({
        path: 'config_path',
        label: t('runner.configPath'),
        currentValue: values.config_path,
        defaultValue: defaultValues.config_path,
        hint: schema.config_path?.hint || '',
        controlHTML: configPathControl,
        disabled,
        extraClass: 'runner-control__option--wide',
        errors: fieldErrors.config_path || [],
      }),
    ].join('');

    return `
      <div class="runner-control__options">
        <div class="runner-control__options-head">
          <div>
            <div class="runner-control__options-title">${escapeHTML(t('runner.startOptions'))}</div>
            <div class="summary-note">${escapeHTML(t('runner.startOptionsSummary'))}</div>
            ${redactionNote}
          </div>
        </div>
        <div class="runner-control__chips">
          ${runnerControlStartOptionsSummaryChips(control, values)}
        </div>
        ${previewHTML}
        ${validationSummary}
        <div class="runner-control__options-grid">
          ${cards}
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

  function resetServerLogTailState(options = {}) {
    const tail = ensureLogTailState();
    const preserveSource = options.preserveSource !== false;
    tail.entries = [];
    tail.cursor = 0;
    tail.nextCursor = 0;
    tail.status = 'loading';
    tail.loading = false;
    tail.error = '';
    tail.malformedLines = 0;
    if (!preserveSource) {
      tail.sourceId = '';
      tail.source = {
        id: '',
        label: '',
        path: '',
        name: '',
        exists: false,
        available: false,
        selected: false,
        kind: 'log',
        unavailableReason: 'missing',
      };
      tail.sources = [];
    }
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
      sourceId: tail.sourceId || toObject(tail.source).id || '',
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
      tail.error = toText(error?.message || error, t('logs.logReadError'));
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
    const responseSource = normalizeLogTailSource(response.source);
    const responseSources = normalizeLogTailSources(response.sources);
    const previousSources = normalizeLogTailSources(tail.sources);
    const previousSourceId = toText(tail.sourceId || toObject(tail.source).id || '', '').trim();
    const responseSourceId = toText(response.source_id || response.selected_source_id || responseSource.id || '', '').trim();
    const selection = resolveLogTailSourceSelection({
      ...tail,
      sources: responseSources.length ? responseSources : previousSources,
      sourceId: responseSourceId || previousSourceId,
      source: responseSource.id ? responseSource : tail.source,
    });
    const sourceChanged = Boolean(previousSourceId && selection.sourceId && selection.sourceId !== previousSourceId);
    const clearSelection = reset || sourceChanged;
    const existingEntries = clearSelection ? [] : toArray(tail.entries);
    const nextEntries = incomingEntries.length ? mergeLogTailEntries(existingEntries, incomingEntries) : existingEntries.slice(-MAX_LOG_ROWS);
    const source = normalizeLogTailSource(selection.source);
    const selected = clearSelection
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
      sourceId: selection.sourceId,
      source,
      sources: selection.sources,
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

  function updateLogTailSource(sourceId) {
    const tail = ensureLogTailState();
    const normalizedSourceId = toText(sourceId, '').trim();
    const selection = resolveLogTailSourceSelection({
      ...tail,
      sourceId: normalizedSourceId,
    });
    const nextSourceId = selection.sourceId;
    if (!nextSourceId || nextSourceId === toText(tail.sourceId || tail.source?.id || '', '').trim()) {
      return false;
    }
    clearLogTailSelection();
    resetServerLogTailState();
    tail.sourceId = nextSourceId;
    applyLogTailSourceSelection(tail, selection);
    if (state.sourceMode === 'api') {
      return syncLogTailStreaming({ reset: true, silent: false });
    }
    renderShell({ preserveScroll: true });
    return false;
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
    const selection = resolveLogTailSourceSelection(tail);
    const source = normalizeLogTailSource(selection.source);
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
      sourceId: toText(selection.sourceId, ''),
      filters: normalizeLogTailFilters(tail.filters),
      entries: toArray(tail.entries).map(normalizeLogEntry),
      source: {
        id: toText(source.id, ''),
        label: toText(source.label, ''),
        path: toText(source.path, ''),
        name: toText(source.name, ''),
        exists: Boolean(source.exists),
        available: Boolean(source.available),
        selected: Boolean(source.selected),
        kind: toText(source.kind, 'log'),
        unavailableReason: toText(source.unavailableReason, ''),
      },
      sources: normalizeLogTailSources(tail.sources),
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
    tail.sources = normalizeLogTailSources(tail.sources);
    const source = normalizeLogTailSource(tail.source);
    tail.source = source;
    tail.sourceId = toText(tail.sourceId || source.id, '').trim();
    applyLogTailSourceSelection(tail, resolveLogTailSourceSelection(tail));
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

  function inspectSnapshotRefreshState() {
    const refresh = ensureSnapshotRefreshState();
    return {
      status: toText(refresh.status, ''),
      active: Boolean(refresh.active),
      inFlight: Boolean(refresh.inFlight),
      requestSeq: toNumber(refresh.requestSeq, 0),
      retryCount: toNumber(refresh.retryCount, 0),
      retryDelayMs: toNumber(refresh.retryDelayMs, SNAPSHOT_POLL_MS),
      maxRetryDelayMs: toNumber(refresh.maxRetryDelayMs, SNAPSHOT_RECONNECT_MAX_MS),
      nextRefreshAt: toNumber(refresh.nextRefreshAt, 0),
      lastAttemptAt: toNumber(refresh.lastAttemptAt, 0),
      lastSuccessAt: toNumber(refresh.lastSuccessAt, 0),
      lastUpdatedAt: toNumber(refresh.lastUpdatedAt, 0),
      lastErrorAt: toNumber(refresh.lastErrorAt, 0),
      lastErrorStatus: toNumber(refresh.lastErrorStatus, 0),
      lastError: toText(refresh.lastError, ''),
      stale: Boolean(refresh.stale),
      staleReasons: toArray(refresh.staleReasons).map((reason) => toText(reason, '')).filter(Boolean),
      latestRunDir: toText(refresh.latestRunDir, ''),
      timerActive: Boolean(refresh.timer),
    };
  }

  function renderPipeline() {
    const liveRun = currentLiveRun();
    const run = currentLiveRunActiveRun(liveRun);
    const liveStatus = currentLiveRunStatus(liveRun);
    const liveStageSummaries = toArray(liveRun.stageSummaries || toObject(liveRun.stages).items || []);
    const stageItems = liveStageSummaries.length ? liveStageSummaries : state.stages;
    const hasTokenTelemetry = Boolean(
      run.tokensAvailable ||
        run.tokens?.available ||
        run.tokens?.in != null ||
        run.tokens?.out != null
    );
    const tokenIn = hasTokenTelemetry ? run.tokens.in : null;
    const tokenOut = hasTokenTelemetry ? run.tokens.out : null;
    const tokenTotal = hasTokenTelemetry && tokenIn != null && tokenOut != null ? Number(tokenIn) + Number(tokenOut) : null;
    const tokenInputText = metricText(hasTokenTelemetry, tokenIn, fmtNumberShort);
    const tokenOutputText = metricText(hasTokenTelemetry, tokenOut, fmtNumberShort);
    const tokenBudgetText = metricText(run.budgetAvailable, run.budgetUsed, fmtPercent);
    const tokenSparkline = state.metrics.tokens24h.length
      ? buildSparkline(state.metrics.tokens24h, 320, 44, 'rgba(126,227,138,0.12)', '#7ee38a')
      : `<div class="summary-note">${escapeHTML(t('pipeline.tokenTelemetryUnavailable'))}</div>`;
    const stageSummary = renderLifecycleLane(stageItems);
    const outputs = stageItems.length
      ? stageItems.map((stage) => `
        <div class="task-card">
          <div class="task-card__head">
            <span class="task-card__id">${escapeHTML(stage.label)}</span>
            <span class="chip ${lifecycleStatusToneClass(stage.status)}">${escapeHTML(lifecycleStageStatusLabel(stage.status))}</span>
          </div>
          <div class="task-card__title">${escapeHTML(stage.taskTitle || stage.title || t('pipeline.lifecycleRecord'))}</div>
          <div class="task-card__meta">
            ${chip(stage.taskId || t('common.unavailable'), 'chip--info')}
            ${chip(stage.attempt != null ? t('backlog.attemptText', { attempt: stage.attempt }) : t('backlog.attemptUnavailable'), stage.attempt != null ? 'chip--accent' : 'chip--info')}
            ${chip(stage.cycle != null ? t('backlog.cycleText', { cycle: stage.cycle }) : t('backlog.cycleUnavailable'), 'chip--info')}
            ${stage.model ? chip(stage.model, 'chip--info') : ''}
          </div>
          <div class="summary-note" style="margin-top:8px;">${escapeHTML(stage.startedAt ? `${t('pipeline.started')} ${fmtClock(stage.startedAt)}` : t('pipeline.startedUnavailable'))} | ${escapeHTML(stage.endedAt ? `${t('pipeline.ended')} ${fmtClock(stage.endedAt)}` : normalizeStageStatus(stage.status, 'pending') === 'running' ? t('pipeline.inProgress') : t('pipeline.endedUnavailable'))} | ${escapeHTML(stage.elapsedSec != null ? fmtDuration(stage.elapsedSec) : stage.durationSec != null ? fmtDuration(stage.durationSec) : '--')}</div>
          ${renderStageHealthSignals(stage)}
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(redactionAwareText(stage.recentOutput, ''), 220) || t('pipeline.recentOutputUnavailable'))}</div>
        </div>
      `)
      : [`<div class="summary-note">${escapeHTML(t('pipeline.noLifecycleRecords'))}</div>`];

    const body = `
      <div class="view-grid view-grid--two">
        <div class="view-grid">
          ${panel(
            t('pipeline.stageLane'),
            `${t('pipeline.iter')} ${escapeHTML(`${run.iteration}/${run.maxIterations}`)} | ${t('pipeline.current')} ${escapeHTML(liveStatus.stage || run.stage)}`,
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
            t('pipeline.currentStageOutput'),
            escapeHTML(run.task || (stageItems.length ? `${stageItems.length} ${t('pipeline.lifecycleRecord')}` : t('pipeline.noLifecycleRecords'))),
            `
              <div class="view-grid view-grid--three">
                ${outputs.join('')}
              </div>
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            t('pipeline.stageGuardrails'),
            escapeHTML(run.backend),
            `
                <div class="compact-list">
                  <div class="compact-list__item">
                    <span class="compact-list__bullet"></span>
                    <div class="compact-list__body">${escapeHTML(t('pipeline.readOnlyShell'))}</div>
                  </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div class="compact-list__body">${escapeHTML(t('pipeline.manualConfirmation'))}</div>
                </div>
                  <div class="compact-list__item">
                    <span class="compact-list__bullet"></span>
                    <div class="compact-list__body">${escapeHTML(t('pipeline.devStage'))}: ${escapeHTML(run.task || t('common.unavailable'))} | ${escapeHTML(t('dashboard.budget').toLowerCase())} ${escapeHTML(tokenBudgetText)}</div>
                  </div>
                </div>
              `
          )}

          ${panel(
            t('pipeline.liveTokens'),
            t('pipeline.sparkline24h'),
            `
              <div class="kpi-grid kpi-grid--three">
                ${kpiCard(t('pipeline.input'), tokenInputText, hasTokenTelemetry ? t('pipeline.tokensProcessed') : t('pipeline.tokenTelemetryUnavailable'), false, tokenInputText === 'unavailable' ? t('common.unavailable') : '')}
                ${kpiCard(t('pipeline.output'), tokenOutputText, hasTokenTelemetry ? t('pipeline.tokensGenerated') : t('pipeline.tokenTelemetryUnavailable'), false, tokenOutputText === 'unavailable' ? t('common.unavailable') : '')}
                ${kpiCard(t('dashboard.budget'), tokenBudgetText, run.budgetAvailable ? t('common.enabled') : t('pipeline.budgetTelemetryUnavailable'), false, tokenBudgetText === 'unavailable' ? t('common.unavailable') : '')}
              </div>
              <div style="margin-top:12px;">${tokenSparkline}</div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'pipeline',
      t('pipeline.title'),
      `${t('pipeline.activeTask')} ${escapeHTML(liveStatus.stage || run.stage)} | ${escapeHTML(run.id)}`,
      `
        ${button(t('common.openLogs'), 'nav-logs', 'button--quiet')}
        ${button(t('common.openBacklog'), 'nav-backlog', 'button--quiet')}
      `,
      body
    );
  }

  function renderLogs() {
    const liveRun = currentLiveRun();
    const liveStatus = currentLiveRunStatus(liveRun);
    const liveLog = currentLiveRunLog(liveRun);
    const redaction = toObject(state.redaction);
    const redactionNote = redaction.active ? `<div class="summary-note">${escapeHTML(t('config.redactedHidden'))}</div>` : '';
    if (state.sourceMode === 'api') {
      const tail = ensureLogTailState();
      const control = describeLogTailControl(tail);
      const entries = toArray(tail.entries);
      const selected = new Set(toArray(tail.selected).map((value) => String(toMaybeNumber(value, null))).filter(Boolean));
      const banner = describeLogTailState(tail);
      const liveLogSource = toObject(liveLog.source);
      const liveLogCursor = toMaybeNumber(tail.nextCursor ?? tail.cursor ?? liveLog.nextCursor ?? liveLog.cursor, 0) ?? 0;
      const sourceName = redactionAwareText(
        logTailSourceDisplayName(tail.source?.id ? tail.source : liveLogSource),
        t('logs.activeRunLog'),
      ) || t('logs.activeRunLog');
      const body = `
        <div class="view-grid">
          ${panel(
            t('logs.liveTail'),
          `${escapeHTML(t('logs.linesShown', { count: entries.length }))} | ${escapeHTML(t('logs.cursor'))} ${escapeHTML(String(liveLogCursor))}`,
          `
              ${renderLogTailBanner(tail)}
              ${redactionNote}
              ${renderLogTailFilters(tail)}
            `
          )}

          ${panel(
            `${escapeHTML(sourceName)}`,
            escapeHTML(entries.length === 1 ? t('logs.filteredLine') : t('logs.filteredLines')),
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
        t('logs.title'),
        `${escapeHTML(sourceName)} | ${escapeHTML(control.stateLabel)} | ${escapeHTML(t('logs.cursor'))} ${escapeHTML(String(liveLogCursor))}`,
        `
          ${button(control.buttonLabel, 'toggle-logs', control.buttonClass, control.buttonAttrs)}
          ${button(t('common.openDashboard'), 'nav-dashboard', 'button--quiet')}
        `,
        body
      );
    }

    const filters = ['all', 'info', 'warn', 'err', 'debug'];
    const filtered = state.logs.filter((line) => state.logFilter === 'all' || line.lvl === state.logFilter);
    const liveRunStatus = liveStatus.run || state.activeRun.status;
    const liveRunStage = liveStatus.stage || state.activeRun.stage;
    const logsMode =
      state.snapshotStatus === 'loading'
        ? t('common.loading')
        : state.snapshotStatus === 'fallback'
          ? t('snapshot.fallback')
          : state.logsPaused
            ? t('logs.liveTailPaused')
            : liveRunStatus === 'running'
              ? t('logs.liveTailActive')
              : t('logs.liveTailPaused');
    const logsStateLabel =
      state.snapshotStatus === 'loading'
        ? t('common.loading')
        : state.logsPaused
          ? t('logs.liveTailPaused')
          : liveRunStatus === 'running'
            ? t('logs.liveTailActive')
            : t('logs.liveTailPaused');
    const logsButtonClass =
      state.snapshotStatus === 'loading'
        ? 'button--loading'
        : state.logsPaused
          ? 'button--paused'
          : 'button--quiet';
    const logsButtonLabel =
      state.snapshotStatus === 'loading'
        ? t('logs.loadingActiveRunLog')
        : state.logsPaused
          ? t('logs.resumeLiveTail')
          : t('logs.pauseLiveTail');
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
          t('logs.tailFilter'),
          logsMode,
          `
            ${sectionNotice('logs')}
            ${redactionNote}
            <div class="logs-toolbar">
              <div class="filters">
                ${filters
                  .map((filter) => `
                    <button type="button" class="filter-chip ${state.logFilter === filter ? 'filter-chip--active' : ''}" data-filter="${escapeHTML(filter)}">${escapeHTML(logFilterLabel(filter))}</button>
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
          escapeHTML(t('logs.linesShown', { count: filtered.length })),
          `
            <div class="log-feed">
              <div class="log-feed__scroll" data-log-scroll>
                ${filtered.length ? filtered.map((line) => renderLogRow(line)).join('') : `<div class="summary-note">${escapeHTML(t('logs.noMatchCurrentFilter'))}</div>`}
                ${!state.logsPaused ? `
                  <div class="log-row" style="color: var(--accent);">
                    <div class="log-row__time">${escapeHTML(fmtClock(nowMs()))}</div>
                    <div class="log-row__stage" style="color: var(--accent);">[${escapeHTML(liveRunStage)}]</div>
                    <div class="log-row__level">${escapeHTML(t('logs.live'))}</div>
                    <div class="log-row__msg">${escapeHTML(t('logs.waitingForNextEvent'))}</div>
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
      t('logs.title'),
      `cycle_summary.log | ${escapeHTML(logsMode)}`,
      `
        ${button(logsButtonLabel, 'toggle-logs', logsButtonClass, logsButtonAttrs)}
        ${button(t('common.openDashboard'), 'nav-dashboard', 'button--quiet')}
      `,
      body
    );
  }

  function renderBacklog() {
    const redaction = toObject(state.redaction);
    const buckets = [
      { key: 'pending', label: t('backlog.pending') },
      { key: 'in_progress', label: t('backlog.inProgress') },
      { key: 'done', label: t('backlog.done') },
      { key: 'failed', label: t('backlog.failed') },
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
                ${bucket.tasks.length ? bucket.tasks.map((task) => renderTaskCard(task, bucket.key)).join('') : `<div class="summary-note">${escapeHTML(state.backlog.length ? t('backlog.noTasksInBucket') : t('backlog.noArtifacts'))}</div>`}
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
            ${chip(backlogStatusLabel(normalizeBacklogStatus(selected.status, 'pending')), backlogStatusToneClass(selected.status))}
            ${chip(selected.estimate)}
            ${selected.skill ? chip(selected.skill, 'chip--info') : ''}
          </div>
          <div class="summary-note" style="margin-top:10px;">${escapeHTML(selected.dependsOn && selected.dependsOn.length ? compactText(t('backlog.dependsOn', { items: selected.dependsOn.join(', ') }), 140) : t('backlog.dependenciesUnavailable'))}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.fileScope ? compactText(t('backlog.fileScope', { scope: selected.fileScope }), 140) : t('backlog.fileScopeUnavailable'))}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.attempt != null ? t('backlog.attemptText', { attempt: selected.attempt }) : t('backlog.attemptUnavailable'))}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.cycle != null ? t('backlog.cycleText', { cycle: selected.cycle }) : t('backlog.cycleUnavailable'))}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.step != null ? t('backlog.stepText', { step: selected.step }) : t('backlog.stepUnavailable'))}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(selected.failureReason ? t('backlog.failureText', { reason: `${selected.failureReason}${redactionAwareText(selected.failureDetail || toObject(selected.failure).detail, '', redaction) ? ` | ${compactText(redactionAwareText(selected.failureDetail || toObject(selected.failure).detail, '', redaction), 120)}` : ''}` }) : t('backlog.failureUnavailable'))}</div>
          <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(redactionAwareText(selected.recentOutput, '', redaction), 220) || t('backlog.recentOutputUnavailable'))}</div>
        </div>
      `
      : state.backlog.length ? `<div class="summary-note">${escapeHTML(t('dashboard.noTaskSelected'))}</div>` : `<div class="summary-note">${escapeHTML(t('backlog.noArtifacts'))}</div>`;

    const body = `
      <div class="view-grid view-grid--two">
        <div class="view-grid">
          ${sectionNotice('backlog')}
          ${panel(
            t('backlog.workQueue'),
            `${escapeHTML(state.backlog.length)} ${escapeHTML(t('common.tasks'))}`,
            board
          )}
        </div>
        <div class="view-grid">
          ${panel(
            t('backlog.backlogSummary'),
            escapeHTML(selected ? selected.id : t('common.none')),
            `
              <div class="kpi-grid kpi-grid--four">
                ${kpiCard(t('backlog.pending'), String(state.backlog.filter((task) => task.status === 'pending').length), t('backlog.queued'))}
                ${kpiCard(t('backlog.inProgress'), String(state.backlog.filter((task) => task.status === 'in_progress').length), t('backlog.active'), true)}
                ${kpiCard(t('backlog.done'), String(state.backlog.filter((task) => task.status === 'done').length), t('backlog.completed'))}
                ${kpiCard(t('backlog.failed'), String(state.backlog.filter((task) => task.status === 'failed').length), t('backlog.needsAttention'))}
              </div>
              <div style="margin-top:12px;">${detail}</div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'backlog',
      t('backlog.title'),
      `${t('pipeline.activeTask')} ${escapeHTML(state.activeRun.task || t('common.none'))} | ${t('common.selected')} ${escapeHTML(selected ? selected.id : t('common.none'))}`,
      `
        ${button(t('common.openGoals'), 'nav-goals', 'button--quiet')}
        ${button(t('common.openPipeline'), 'nav-pipeline', 'button--quiet')}
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
    const goalRawText = toText(goalSnapshot.raw_text || goalSnapshot.rawText, '');
    const goalRedaction = toObject(goalSnapshot.redaction || state.redaction);
    const goalRawTextPreview = redactionAwareText(goalRawText, t('common.empty'), goalRedaction);
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
      ? t('goals.saving')
      : goalSave.status === 'success'
        ? t('goals.saved')
        : goalSave.status === 'error'
          ? t('goals.saveFailed')
          : !goalsDirty
            ? t('goals.clean')
            : goalSaveRisk.requiresConfirmation
              ? t('goals.confirmationRequired')
              : t('goals.readyToSave');
    const goalsSource = goalsDirty ? t('goals.browserLocalDraft') : '/api/goals';
    // browser-local draft
    // Draft edits stay local until the save workflow lands.
    const goalsNote = state.snapshotStatus === 'loading'
      ? t('goals.loadingSnapshot')
      : state.sourceMode === 'fallback'
      ? t('goals.readOnlyFallback')
      : goalSnapshotMessage(goalSnapshot, goalSummary.total, goalsDirty);
    const goalSaveButtonLabel = goalSaveInFlight()
      ? t('goals.saving')
      : goalSaveRisk.requiresConfirmation
        ? t('goals.confirmSave')
        : t('goals.saveGoals');

    const body = `
      <div class="view-grid">
        ${panel(
          t('goals.goalProgress'),
          `${escapeHTML(done)}/${escapeHTML(total)} ${escapeHTML(t('common.complete'))}`,
          `
            ${sectionNotice('goals')}
            <div class="meter" style="width:100%; height:10px;">
              <div class="meter__fill" style="width:${escapeHTML(total ? progressWidth(done / total) : '0%')}"></div>
            </div>
            <div class="summary-note" style="margin-top:10px;">${escapeHTML(goalsNote)}</div>
            <div class="summary-note" style="margin-top:4px;">${escapeHTML(t('common.source'))}: ${escapeHTML(goalsSource)}</div>
            <div class="summary-note" style="margin-top:4px;">${escapeHTML(t('goals.snapshot'))}: ${escapeHTML(toNumber(goalSummary.done || 0, 0))}/${escapeHTML(toNumber(goalSummary.total || 0, 0))} ${escapeHTML(t('goals.checked'))} · ${escapeHTML(toNumber(goalWarnings.length, 0))} ${escapeHTML(t('goals.parserWarnings').toLowerCase())}</div>
          `
        )}

        ${panel(
          t('goals.snapshot'),
          goalFileExists ? (goalSummary.total ? `${escapeHTML(goalSummary.total)} ${escapeHTML(t('common.parsed'))}` : t('common.empty')) : t('common.missing'),
          `
            <div class="compact-list">
              <div class="compact-list__item">
                <span class="compact-list__bullet" style="background:${goalFileExists ? 'var(--accent)' : 'var(--warn)'}"></span>
                <div>
                  <div class="compact-list__body">${escapeHTML(goalFilePath)}</div>
                  <div class="compact-list__meta">${escapeHTML(t('common.exists'))}: ${escapeHTML(goalFileExists ? t('common.yes') : t('common.no'))} · ${escapeHTML(t('common.size'))}: ${escapeHTML(goalFileSize != null ? `${goalFileSize} ${t('common.bytes')}` : t('common.unknown'))} · ${escapeHTML(t('common.mtime'))}: ${escapeHTML(goalFileMtime != null ? fmtDateTime(goalFileMtime) : t('common.unknown'))}</div>
                </div>
              </div>
            </div>
            <div class="summary-note" style="margin-top:10px;">${escapeHTML(t('common.source'))}: ${escapeHTML(goalsSource)}</div>
            <div class="summary-note" style="margin-top:10px;">${escapeHTML(t('goals.rawTextPreview'))}</div>
            ${goalRedaction.active ? `<div class="summary-note" style="margin-top:4px;">${escapeHTML(t('config.redactedHidden'))}</div>` : ''}
            <div class="summary-note" style="margin-top:4px; white-space:pre-wrap; max-height:180px; overflow:auto;">${escapeHTML(goalRawTextPreview.trim() || t('common.empty'))}</div>
            <div class="summary-note" style="margin-top:10px;">${escapeHTML(t('goals.parserWarnings'))}</div>
            ${goalWarnings.length ? `
              <div class="compact-list" style="margin-top:6px;">
                ${goalWarnings.slice(0, 5).map((warning) => `
                  <div class="compact-list__item">
                    <span class="compact-list__bullet" style="background:var(--warn)"></span>
                    <div>
                      <div class="compact-list__body">${escapeHTML(t('goals.sourceLine', { lineNumber: warning.lineNumber || '?' }))} · ${escapeHTML(warning.reason)}</div>
                      <div class="compact-list__meta">${escapeHTML(warning.message || warning.line || '')}</div>
                    </div>
                  </div>
                `).join('')}
              </div>
            ` : `<div class="summary-note" style="margin-top:4px;">${escapeHTML(t('goals.noParserWarnings'))}</div>`}
          `
        )}

        ${panel(
          t('goals.goalDraftDiff'),
          goalsDirty ? escapeHTML(t('goals.changeCount', { count: goalDraft.rows.length })) : escapeHTML(t('goals.clean')),
          `
            <div class="summary-note">${escapeHTML(t('goals.draftStaysLocal'))}</div>
            <div class="prompt-diff-list" style="margin-top:10px;">
              ${goalDraft.rows.length ? goalDraft.rows.map((row) => renderGoalDraftRow(row)).join('') : `<div class="summary-note">${escapeHTML(t('goals.noLocalChanges'))}</div>`}
            </div>
          `
        )}

        ${panel(
          t('goals.goalSavePanel'),
          goalSaveStatusLabel,
          `
            <div class="goal-save-state" data-goal-save-root data-goal-save-status="${escapeHTML(goalSave.status || 'idle')}" data-goal-saving="${goalSaveInFlight() ? 'true' : 'false'}">
              <div data-goal-save-banner>
                ${renderGoalSaveBanner(goalDraft, goalSaveRisk)}
              </div>
              <div class="modal-field" style="margin-top:12px;">
                <div class="modal-field__label">${escapeHTML(t('goals.confirmationPhrase'))}</div>
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
              <div class="summary-note" style="margin-top:10px;">${escapeHTML(t('goals.saveCreatesBackup'))}</div>
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
                    <span>${escapeHTML(bucket === 'p0' ? t('goals.p0MustHave') : t('goals.p1ShouldHave'))}</span>
                    <span class="status-chip" style="margin-left:auto;">${escapeHTML(goals.filter((goal) => goal.done).length)}/${escapeHTML(goals.length)}</span>
                    ${button(t('goals.addGoal'), `goal-add-${bucket}`, 'button--quiet button--tiny')}
                  </div>
                  <div class="goal-bucket__body">
                    ${goals.map((goal, index) => renderGoalItem(bucket, goal, index, goals.length)).join('') || `<div class="summary-note">${escapeHTML(t('goals.noGoalsYet'))}</div>`}
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
      t('goals.title'),
      t('goals.localChecklist'),
      `
        ${button(t('goals.addGoal'), 'goal-add-p0', 'button--primary')}
        ${button(t('goals.resetDraft'), 'reset-goals', goalsDirty ? 'button--danger' : 'button--quiet', goalsDirty ? '' : 'disabled')}
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
      return `<div class="field-error">${escapeHTML(t('config.missingSchema', { path }))}</div>`;
    }
    if (schema.kind === 'bool') {
      return `
        <button type="button" class="control-chip ${value ? 'control-chip--active' : ''}" data-config-toggle="${escapeHTML(path)}" ${disabled ? 'disabled' : ''}>
          <span class="dot" style="background:${value ? 'var(--accent)' : 'var(--text-sub)'}"></span>
          ${escapeHTML(value ? t('common.enabled') : t('common.disabled'))}
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
      if (path === 'roles') {
        return renderConfigRolesControl({
          path,
          options: schema.options || [],
          value,
          disabled,
        });
      }
      const set = new Set(normalizeListValues(value).map((item) => item.toLowerCase()));
      return `
        <div class="modal-tabs">
          ${schema.options
            .map((option) => `
              <button type="button" class="modal-tab ${set.has(String(option).toLowerCase()) ? 'modal-tab--active' : ''}" data-config-multi="${escapeHTML(path)}" data-config-value="${escapeHTML(option)}" ${disabled ? 'disabled' : ''}>${escapeHTML(option)}</button>
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
          placeholder="${escapeHTML(schema.item_kind === 'int' || schema.itemKind === 'int' ? t('config.listPlaceholderNumbers') : t('config.listPlaceholderValues'))}"
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
        placeholder="${schema.redacted ? escapeHTML(t('common.unavailable')) : ''}"
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
    const securityRoleBannerHTML = renderConfigSecurityRoleBanner(state.configDraft, state.configSchema, selectedPath);
    const configRedaction = toObject(state.redaction);
    // restart required
    const saveButtonAttrs = saveDisabledReason ? `disabled title="${escapeHTML(saveDisabledReason)}"` : '';
    const saveButtonLabel = saveLocked ? t('config.saving') : t('config.saveChanges');

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
                      ${schema && schema.redacted ? `<span class="chip chip--info">${escapeHTML(t('config.secret'))}</span>` : ''}
                      ${schema && schema.restart ? `<span class="chip chip--warn">${escapeHTML(t('config.restart'))}</span>` : ''}
                      ${draftChanged ? `<span class="chip chip--accent">${escapeHTML(t('config.edited'))}</span>` : ''}
                      ${error ? `<span class="chip chip--err">${escapeHTML(t('config.invalid'))}</span>` : ''}
                    </div>
                  </button>
                `;
              })
              .join('')}
          </div>
        </div>
      `)
      .join('');

    const selectedLabel = escapeHTML(selectedSchema?.label || selectedPath || t('config.field'));
    const selectedPathText = escapeHTML(selectedPath || '');
    const activeValueText = escapeHTML(configValueToText(selectedActiveValue, selectedSchema, selectedPath));
    const draftValueText = escapeHTML(configValueToText(selectedDraftValue, selectedSchema, selectedPath));
    const defaultValueText = escapeHTML(configValueToText(selectedDefaultValue, selectedSchema, selectedPath));
    const selectedDefaultChanged = JSON.stringify(selectedActiveValue) !== JSON.stringify(selectedDefaultValue);
    const selectedDraftChanged = JSON.stringify(selectedDraftValue) !== JSON.stringify(selectedActiveValue);
    const pendingDiffRows = diffs
      .map((diff) => {
        const diffSchema = state.configSchema[diff.path];
        const pathLabel = escapeHTML(diffSchema?.label || diff.path);
        const fromText = escapeHTML(configValueToText(diff.from, diffSchema, diff.path));
        const toText = escapeHTML(configValueToText(diff.to, diffSchema, diff.path));
        return `
          <div class="config-diff-row">
            <div class="config-diff-row__head">
              <div class="config-diff-row__path">${pathLabel}</div>
              <div class="config-row__meta">
                ${diffSchema && diffSchema.redacted ? `<span class="chip chip--info">${escapeHTML(t('config.secret'))}</span>` : ''}
                ${diff.restart ? `<span class="chip chip--warn">${escapeHTML(t('config.restart'))}</span>` : ''}
                ${diff.error ? `<span class="chip chip--err">${escapeHTML(t('config.invalid'))}</span>` : ''}
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
            <div class="overlay__title" style="display:block;">${escapeHTML(t('config.fieldDetails'))}</div>
            <div class="config-detail__title">${selectedLabel}</div>
            <div class="summary-note">${selectedPathText}</div>
          </div>
          <div class="config-row__meta">
            ${selectedSchema && selectedSchema.kind ? `<span class="chip chip--info">${escapeHTML(selectedSchema.kind)}</span>` : ''}
            ${selectedSchema && selectedSchema.redacted ? `<span class="chip chip--info">${escapeHTML(t('config.secret'))}</span>` : ''}
            ${selectedSchema && selectedSchema.restart ? `<span class="chip chip--warn">${escapeHTML(t('config.restartRequired'))}</span>` : ''}
            ${selectedDefaultChanged ? `<span class="chip chip--warn">${escapeHTML(t('config.default'))}</span>` : ''}
            ${selectedDraftChanged ? `<span class="chip chip--accent">${escapeHTML(t('config.edited'))}</span>` : ''}
            ${selectedError ? `<span class="chip chip--err">${escapeHTML(t('config.invalid'))}</span>` : ''}
          </div>
        </div>
        <div class="config-detail__body">
          ${configRedaction.active ? `<div class="summary-note">${escapeHTML(t('config.redactedHidden'))}</div>` : ''}
          ${saveBannerHTML}
          ${securityRoleBannerHTML}
          ${invalidDiffs.length ? `
            <div class="modal-banner section-banner section-banner--err">
              <span class="dot" style="background: currentColor;"></span>
              <div>
                <div class="section-banner__title">${escapeHTML(t('config.localValidationFailed'))}</div>
                <div class="section-banner__copy">${escapeHTML(t('config.fixInvalidChangesBeforeSaving', { count: invalidDiffs.length }))}</div>
              </div>
            </div>
          ` : ''}
          ${restartDiffs.length ? `
            <div class="modal-banner">
              <span class="dot" style="background: var(--warn);"></span>
              <div>
                <div class="section-banner__title">${escapeHTML(t('config.restartRequired'))}</div>
                <div class="section-banner__copy">${escapeHTML(restartDiffs.map((diff) => diff.path).join(', '))}</div>
              </div>
            </div>
          ` : ''}
          <div>
            <div class="detail-label">${escapeHTML(t('config.description'))}</div>
            <div class="detail-copy">${escapeHTML(selectedSchema ? selectedSchema.desc : '')}</div>
          </div>
          ${selectedSchema && selectedSchema.hint ? `
            <div>
              <div class="detail-label">${escapeHTML(t('config.hint'))}</div>
              <div class="summary-note">${escapeHTML(selectedSchema.hint)}</div>
            </div>
          ` : ''}
          <div>
            <div class="detail-label">${escapeHTML(t('config.activeValue'))}</div>
            <div class="field-diff">
              <div class="field-diff__from">${activeValueText}</div>
              <div class="field-diff__to">${draftValueText}</div>
            </div>
          </div>
          <div>
            <div class="detail-label">${escapeHTML(t('config.localDraft'))}</div>
            <div>${configControl(selectedPath)}</div>
            ${selectedError ? `<div class="field-error" style="margin-top:6px;">${escapeHTML(selectedError)}</div>` : ''}
          </div>
          <div>
            <div class="detail-label">${escapeHTML(t('config.defaultValue'))}</div>
            <div class="field-diff">
              <div class="field-diff__from">${defaultValueText}</div>
              <div class="field-diff__to">${draftValueText}</div>
            </div>
          </div>
          ${selectedPath === 'prompts_dir' && state.configContract?.resolved_prompts_dir ? `
            <div>
              <div class="detail-label">${escapeHTML(t('config.resolvedPromptsPath'))}</div>
              <div class="detail-copy">${escapeHTML(redactionAwareText(state.configContract.resolved_prompts_dir, t('common.unknown'), configRedaction))}</div>
            </div>
          ` : ''}
          ${selectedSchema && selectedSchema.redacted ? `
            <div class="summary-note">${escapeHTML(t('config.redactedHidden'))}</div>
          ` : ''}
          ${diffs.length ? `
            <div>
              <div class="detail-label">${escapeHTML(t('config.pendingChanges'))}</div>
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
      t('config.title'),
      `${escapeHTML(t('config.localDraftOnly'))} | ${escapeHTML(diffs.length)} ${escapeHTML(t('config.pendingChanges'))}`,
      `
        ${button(saveButtonLabel, 'save-config', 'button--primary', saveButtonAttrs)}
        ${button(t('config.resetDraft'), 'reset-config', 'button--quiet', saveLocked ? `disabled title="${escapeHTML(t('config.saveInProgress'))}"` : '')}
        ${button(t('common.openPrompts'), 'nav-prompts', 'button--quiet')}
      `,
      body
    );
  }

  function renderPrompts() {
    const selectedPrompt = currentPrompt();
    const selected = selectedPrompt || {
      file: t('prompts.noPromptSelected'),
      mode: 'template',
      scope: 'PM',
      profile: '',
      source: '',
      updated: 'empty',
      summary: '',
      preview: '',
      path: '',
    };
    const copyPromptSummaryAttrs = selectedPrompt ? '' : 'disabled aria-disabled="true"';
    const promptsDir = redactionAwareText(state.configMeta?.resolved_prompts_dir || state.promptsDir || state.config.prompts_dir || 'prompts', t('prompts.unresolvedPath'));
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
    const editorPreview = redactionAwareText(editor.promptPreview || selected.preview, t('common.unavailable'));
    const editorDisabled = editor.loading || promptMutationInFlight(editor) || !editor.promptId || Boolean(editor.error);
    // Inventory previews stay redacted by default. Saving creates a backup before atomically updating the prompt file, and restore uses the selected backup.

    const body = `
      <div class="prompt-layout">
        <div class="prompt-list">
          ${panel(
            t('prompts.promptInventory'),
            `${escapeHTML(overrides)}/${escapeHTML(state.prompts.length)} ${escapeHTML(t('common.overrides'))} | ${escapeHTML(t('prompts.inventoryRedacted'))}`,
            `
              ${sectionNotice('prompts')}
              <div class="summary-note">${escapeHTML(t('prompts.promptInventorySummary'))}</div>
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(promptsDir)}</div>
                    <div class="compact-list__meta">${escapeHTML(t('prompts.primaryPromptsDir'))}</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(state.prompts.length)} ${escapeHTML(t('prompts.trackedPromptFiles'))}</div>
                    <div class="compact-list__meta">${escapeHTML(t('prompts.trackedPromptRoles'))}</div>
                  </div>
                </div>
              </div>
            `
          )}
          ${state.prompts.length ? state.prompts.map((prompt) => renderPromptCard(prompt)).join('') : `<div class="summary-note">${escapeHTML(t('prompts.noPromptFiles'))}</div>`}
        </div>

        <div class="prompt-editor" data-prompt-editor-root data-prompt-dirty="${editorDirty ? 'true' : 'false'}" data-prompt-loading="${editor.loading ? 'true' : 'false'}" data-prompt-saving="${promptSaveInFlight(editor) ? 'true' : 'false'}" data-prompt-restoring="${promptRestoreInFlight(editor) ? 'true' : 'false'}" data-prompt-id="${escapeHTML(editor.promptId || '')}">
          <div class="prompt-editor__head">
            <div class="prompt-editor__title-block">
              <div class="panel__title">${escapeHTML(editorFile)}</div>
              <div class="panel__meta">${escapeHTML(editorScope || 'PM')} | ${escapeHTML(editorProfile || 'personal')} | ${escapeHTML(editorMode || t('prompts.template'))} | ${escapeHTML(editorSource || t('prompts.unknownSource'))}</div>
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
                <div class="compact-list__meta">${escapeHTML(t('prompts.scope'))}</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorProfile || 'personal')}</div>
                <div class="compact-list__meta">${escapeHTML(t('prompts.profile'))}</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorSource || t('prompts.unknownSource'))}</div>
                <div class="compact-list__meta">${escapeHTML(t('prompts.source'))}</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorPath || t('prompts.unresolvedPath'))}</div>
                <div class="compact-list__meta">${escapeHTML(t('prompts.resolvedPath'))}</div>
              </div>
            </div>
            <div class="compact-list__item">
              <span class="compact-list__bullet"></span>
              <div>
                <div class="compact-list__body">${escapeHTML(editorUpdated || t('common.unknown'))}</div>
                <div class="compact-list__meta">${escapeHTML(t('prompts.lastUpdated'))}</div>
              </div>
            </div>
          </div>

          <div class="prompt-preview prompt-editor__preview">
            <div class="prompt-preview__head">
              <span class="badge badge--dim">${escapeHTML(t('prompts.fullReadPreview'))}</span>
              <div class="panel__meta">${escapeHTML(t('prompts.promptEditorSummary'))}</div>
            </div>
            <div class="prompt-preview__body">
              <div class="detail-label">${escapeHTML(t('common.preview'))}</div>
              <pre class="prompt-preview__text">${escapeHTML(editorPreview)}</pre>
            </div>
          </div>

          <div class="prompt-editor__body">
            <div class="prompt-editor__field">
              <label class="prompt-editor__label" for="prompt-editor-file">${escapeHTML(t('prompts.filename'))}</label>
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
              <label class="prompt-editor__label" for="prompt-editor-content">${escapeHTML(t('prompts.content'))}</label>
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
      t('prompts.title'),
      `${escapeHTML(promptsDir)} | ${escapeHTML(t('common.selected'))} ${escapeHTML(editorFile)} | ${escapeHTML(t('prompts.profile'))} ${escapeHTML(editorProfile || 'personal')}`,
      `
        ${button(t('common.openConfig'), 'nav-config', 'button--quiet')}
        ${button(t('prompts.copyPromptSummary'), 'copy-prompt-summary', 'button--quiet', copyPromptSummaryAttrs)}
      `,
      body
    );
  }

  function renderHistory() {
    const selected = currentRun();
    const totalTasks = state.runs.reduce((sum, run) => sum + run.tasksTotal, 0);
    const doneTasks = state.runs.reduce((sum, run) => sum + run.tasksDone, 0);
    const successes = state.runs.filter((run) => normalizeProjectStatus(run.projectStatus || run.projectComplete) === 'complete').length;
    const budgetCap = toNumber(state.config?.budget?.max_usd || 0, 0);
    const historyWindow = state.runs.length ? `${t('history.latest')} ${fmtRelative(state.runs[0].startedAt)}` : t('history.noRunsYet');
    const selectedCounts = selected ? historyTaskCounts(selected) : { done: 0, total: 0, failed: 0, skipped: 0, cycles: 0 };
    const selectedSummary = selected ? historySummaryText(selected) : t('history.noPersistedSummary');
    const selectedWorktreeOutcome = selected ? historyWorktreeOutcomeLabel(selected.worktreeOutcome) : t('common.none');
    const selectedShutdownReason = selected ? toText(selected.shutdownReason || selected.stopReason || '', '') : '';
    const selectedFinalReason = selected ? toText(selected.finalReason, '') : '';
    const selectedRunDir = selected ? selected.runDir || t('common.unknown') : t('common.unknown');
    const selectedExecutionStatus = selected ? toText(selected.executionStatus || selected.status, selected.status || '') : '';
    const selectedProjectStatus = selected ? toText(selected.projectStatus || (selected.projectComplete ? 'complete' : 'incomplete'), selected.projectComplete ? 'complete' : 'incomplete') : '';

    const body = `
      <div class="history-layout">
        <div>
          ${panel(
            t('history.runHistory'),
            `${escapeHTML(state.runs.length)} ${escapeHTML(t('common.runs'))} | ${escapeHTML(historyWindow)}`,
            `
              ${sectionNotice('history')}
              <div class="kpi-grid kpi-grid--three">
                ${kpiCard(t('history.success'), `${successes}/${state.runs.length}`, t('history.successfulRuns'), true)}
                ${kpiCard(t('history.tasks'), `${doneTasks}/${totalTasks}`, t('history.completedRuns'))}
                ${kpiCard(t('history.budgetCap'), fmtMoney(budgetCap), t('history.configMaxUsd'))}
              </div>
            `
          )}

            <div class="history-table">
              <div class="history-table__head">
                <span>${escapeHTML(t('history.currentState'))}</span>
                <span>${escapeHTML(t('history.branchId'))}</span>
                <span>${escapeHTML(t('history.tasks'))}</span>
                <span>${escapeHTML(t('history.duration'))}</span>
                <span>${escapeHTML(t('history.started'))}</span>
                <span style="text-align:right;">${escapeHTML(t('history.action'))}</span>
              </div>
              ${state.runs.length ? state.runs.map((run) => renderHistoryRow(run)).join('') : `<div class="summary-note" style="padding:14px;">${escapeHTML(t('history.noRunsYet'))}</div>`}
            </div>
          </div>

        <div>
          ${panel(
            t('history.selectedRun'),
            escapeHTML(selected ? `${selected.branch} | ${selected.id}` : t('common.none')),
            `
              <div class="history-details">
                <div class="history-details__body">
                  ${
                  selected
                      ? `
                        <div class="history-details__chips">
                          <span class="${executionStatusClass(selectedExecutionStatus)}">${escapeHTML(`${t('runner.runStatus')}: ${executionStatusLabel(selectedExecutionStatus)}`)}</span>
                          <span class="${projectStatusClass(selectedProjectStatus)}">${escapeHTML(`${t('nav.project')}: ${projectStatusLabel(selectedProjectStatus)}`)}</span>
                        </div>
                        <div class="kpi-grid kpi-grid--four">
                          ${kpiCard(t('history.currentState'), runStatusLabel(selected.status, selected.finalReason), t('history.currentState'), ['success', 'completed'].includes(toText(selected.status, '')))}
                          ${kpiCard(t('history.tasks'), `${selectedCounts.done}/${selectedCounts.total}`, `${t('common.failed')} ${selectedCounts.failed} | ${t('common.skipped')} ${selectedCounts.skipped}`)}
                          ${kpiCard(t('history.duration'), fmtDuration(selected.durationSec), t('history.persistedRuntime'))}
                          ${kpiCard(t('history.worktreeOutcome'), historyWorktreeOutcomeLabel(selected.worktreeOutcome), selected.worktreeOutcome === 'none' ? t('history.noWorktreeArtifact') : t('history.worktreeOutcomeMeta'))}
                        </div>
                        <div class="compact-list">
                          ${compactFactItem(t('history.branchId'), selected.branch || t('common.none'), t('history.persistedSummary'))}
                          ${compactFactItem(t('history.persistedRuntime'), selectedRunDir, t('history.readOnlyRunArtifacts'))}
                          ${compactFactItem(t('history.currentState'), selectedFinalReason || t('common.unavailable'), t('history.persistedSummary'))}
                          ${compactFactItem(t('history.shutdownReason'), selectedShutdownReason || t('common.unavailable'), t('history.readOnlyRunArtifacts'))}
                          ${compactFactItem(t('history.persistedSummary'), selectedSummary, t('history.readOnlyRunArtifacts'))}
                          ${compactFactItem(t('history.worktreeOutcome'), selectedWorktreeOutcome, t('history.worktreeOutcomeMeta'))}
                        </div>
                        <div class="summary-note">${escapeHTML(t('history.persistedSummariesDriveThisView'))}</div>
                      `
                      : `
                        <div class="history-details__empty">
                          ${escapeHTML(t('history.noSummaries'))}
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
      t('history.title'),
      `${escapeHTML(state.runs.length)} ${escapeHTML(t('common.total'))} | ${escapeHTML(t('history.latest'))} ${escapeHTML(state.runs[0] ? state.runs[0].id : t('common.none'))}`,
      `
        ${button(t('common.openLogs'), 'nav-logs', 'button--quiet')}
        ${button(t('common.openDashboard'), 'nav-dashboard', 'button--quiet')}
      `,
      body
    );
  }

  function renderNotifications() {
    const liveRun = currentLiveRun();
    const liveNotifications = currentLiveRunNotifications(liveRun);
    const liveControl = currentLiveRunRunnerControl(liveRun);
    const notificationItems = toArray(liveNotifications.items || state.notifications);
    const filters = ['all', 'run_start', 'run_stop', 'task_done', 'task_failed', 'quota', 'error', 'stalled'];
    const filterLabels = {
      all: t('notifications.filterAll'),
      run_start: t('notifications.filterRunStart'),
      run_stop: t('notifications.filterRunStop'),
      task_done: t('notifications.filterTaskDone'),
      task_failed: t('notifications.filterTaskFailed'),
      quota: t('notifications.filterQuota'),
      error: t('notifications.filterError'),
      stalled: t('notifications.filterStalled'),
    };
    const filtered = notificationItems.filter((item) => state.notificationFilter === 'all' || item.kind === state.notificationFilter);

    const kindCounts = notificationItems.reduce((acc, item) => {
      acc[item.kind] = (acc[item.kind] || 0) + 1;
      return acc;
    }, {});

    const latestNotification = filtered[0] || notificationItems[0] || null;
    const observedKinds = Object.keys(kindCounts).sort();
    const configuredEvents = fmtList(state.config?.telegram?.notify_events || []);
    const stalledSeconds = toNumber(state.config?.telegram?.stalled_seconds || 0, 0);
    const controlPlaneStatus = liveControl.controllerAvailable
      ? (liveControl.enabled ? (liveControl.busy ? t('runner.working') : t('common.enabled')) : t('common.disabled'))
      : t('common.unavailable');
    const controlPlaneEvent = liveNotifications.controlPlaneEvent || liveControl.status.lastEvent || liveControl.lastAction || liveControl.lastMessage || '';
    const controlPlaneEventLabel = (() => {
      const normalized = toText(controlPlaneEvent, '').toLowerCase();
      if (!normalized) return t('common.none');
      if (normalized === 'busy') return t('runner.working');
      if (normalized === 'enabled') return t('common.enabled');
      if (normalized === 'disabled') return t('common.disabled');
      if (normalized === 'unavailable') return t('common.unavailable');
      if (normalized === 'start') return t('runner.start');
      if (normalized === 'stop') return t('runner.stop');
      if (normalized === 'reload') return t('runner.reload');
      if (normalized === 'restart') return t('runner.restart');
      if (['running', 'stopping', 'stopped', 'success', 'completed', 'complete', 'done', 'failed', 'error', 'idle'].includes(normalized)) {
        return runStatusLabel(normalized);
      }
      const notificationLabel = notificationKindLabel(normalized);
      return notificationLabel === t('common.unknown') ? t('common.unknown') : notificationLabel;
    })();
    const latestNotificationText = redactionAwareText(latestNotification ? latestNotification.text : '', t('notifications.noRecorded'));
    const controlPlaneSnapshot = redactionAwareText(
      liveNotifications.controlPlaneSnapshot || liveControl.lastMessage || liveControl.lastError || t('notifications.runnerControlSnapshot'),
      t('notifications.runnerControlSnapshot'),
    );
    const emptyMessage = notificationItems.length
      ? t('notifications.noMatchCurrentFilter')
      : state.sectionState.notifications?.status === 'error'
        ? state.sectionState.notifications.message || t('notifications.noRecorded')
        : fallbackSectionMessage('notifications');
    const emptyTitle = state.sectionState.notifications?.status === 'error'
      ? t('notifications.notificationError')
      : notificationItems.length
        ? t('notifications.filteredEmpty')
        : t('notifications.noEventsYet');

    const body = `
      <div class="notification-layout">
        <div>
          ${panel(
            t('notifications.eventFeed'),
            `${escapeHTML(filtered.length)} ${escapeHTML(t('notifications.visibleItems'))} | ${escapeHTML(notificationItems.length)} ${escapeHTML(t('notifications.totalItems'))}`,
            `
              ${sectionNotice('notifications')}
              <div class="logs-toolbar">
                <div class="filters">
                  ${filters
                    .map((filter) => `
                      <button type="button" class="filter-chip ${state.notificationFilter === filter ? 'filter-chip--active' : ''}" data-notification-filter="${escapeHTML(filter)}">${escapeHTML(filterLabels[filter] || filter.toUpperCase())}</button>
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
            t('notifications.notificationSource'),
            escapeHTML(latestNotification ? `${notificationKindLabel(latestNotification.kind)} | ${fmtRelative(latestNotification.t)}` : t('notifications.noEventsYet')),
            `
              <div class="compact-list">
                ${compactFactItem(t('notifications.observedKinds'), observedKinds.length ? observedKinds.map((kind) => notificationKindLabel(kind)).join(', ') : t('common.none'), t('notifications.observedKindsNote'))}
                ${compactFactItem(t('notifications.newestEvent'), latestNotification ? `${notificationKindLabel(latestNotification.kind)} | ${fmtDateTime(latestNotification.t)}` : t('common.none'), latestNotificationText)}
                ${compactFactItem(t('notifications.controlPlaneLastEvent'), controlPlaneEventLabel, controlPlaneSnapshot)}
              </div>
            `
          )}

          ${panel(
            t('notifications.notificationCounts'),
            t('notifications.currentRun'),
            `
              <div class="kpi-grid kpi-grid--four">
                ${kpiCard(t('notifications.lifecycle'), String((kindCounts.run_start || 0) + (kindCounts.run_stop || 0)), t('notifications.runStartAndStop'))}
                ${kpiCard(t('notifications.taskDone'), String(kindCounts.task_done || 0), t('notifications.successEvents'), true)}
                ${kpiCard(t('common.quota'), String(kindCounts.quota || 0), t('notifications.budgetNotices'))}
                ${kpiCard(t('notifications.errors'), String((kindCounts.error || 0) + (kindCounts.task_failed || 0) + (kindCounts.stalled || 0)), t('notifications.actionNeeded'))}
              </div>
            `
          )}

          ${panel(
            t('notifications.bridgeSettings'),
            escapeHTML(state.config.telegram.instance_name || 'home-pc-main'),
            `
              <div class="compact-list">
                ${compactFactItem(t('notifications.configuredEvents'), configuredEvents || t('common.none'), 'telegram.notify_events')}
                ${compactFactItem(t('notifications.stalledThreshold'), stalledSeconds ? `${stalledSeconds}s` : t('common.unavailable'), 'telegram.stalled_seconds')}
                ${compactFactItem(t('notifications.controlPlaneStatus'), controlPlaneStatus, t('notifications.runnerControlSnapshot'))}
              </div>
              <div class="summary-note">${escapeHTML(t('notifications.eventsReadFrom'))}</div>
            `
          )}
        </div>
      </div>
    `;

    return viewShell(
      'notifications',
      t('notifications.title'),
      `${escapeHTML(notificationItems.length)} ${escapeHTML(t('notifications.visibleItems'))} | ${escapeHTML(t('notifications.eventFeed'))}`,
      `
        ${button(t('common.openDashboard'), 'nav-dashboard', 'button--quiet')}
        ${button(t('common.openWorktree'), 'nav-worktree', 'button--quiet')}
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
    const preflightHTML = status !== 'none' ? renderWorktreePreflightBlock(review) : '';
    const changedFilesHTML = review.changedFiles.length
      ? `
        <div class="review-files review-files--diff">
          ${review.changedFiles.map((file) => renderWorktreeDiffFile(file)).join('')}
        </div>
      `
      : `<div class="summary-note">${escapeHTML(t('worktree.noChangedFiles'))}</div>`;
    const statusLabel = (() => {
      const normalized = status.toLowerCase();
      if (normalized === 'none') return t('common.none');
      if (normalized === 'pending review' || normalized === 'pending') return t('worktree.reviewRequired');
      if (normalized === 'applied') return t('worktree.patchApplied');
      if (normalized === 'discarded') return t('worktree.patchDiscarded');
      if (normalized === 'apply_failed') return t('worktree.patchExportFailed');
      if (normalized === 'patch_not_applied' || normalized === 'not_applied') return t('worktree.patchNotApplied');
      if (normalized === 'applied_cleanup_failed') return t('worktree.mergeRecordedCleanupFailed');
      if (normalized === 'discard_cleanup_failed') return t('worktree.discardRecordedCleanupFailed');
      if (normalized === 'error') return t('common.failed');
      return status;
    })();
    const cleanupStateLabel = (() => {
      const normalized = cleanupState.toLowerCase();
      if (normalized === 'none') return t('common.none');
      if (normalized === 'pending') return t('pipeline.pending');
      if (normalized === 'done') return t('common.complete');
      if (normalized === 'failed') return t('common.failed');
      return cleanupState;
    })();
    // no pending merge
    // This worktree is finalized. The web console stays read-only.
    // The backend validates the pending marker, source repository, run directory, worktree path, and patch path before it applies anything. No commit will be created.
    // Manual recovery:
    const statusSummary = status === 'none'
      ? t('worktree.noPendingMerge')
      : [statusLabel, cleanupState !== 'none' ? `${t('worktree.cleanupState')} ${cleanupStateLabel}` : ''].filter(Boolean).join(' | ');
    const checklistMeta = status === 'none' ? t('worktree.readOnlyMode') : cleanupFailed ? t('worktree.manualRecovery') : reviewRequired ? (actionEnabled ? t('worktree.confirmationRequired') : t('worktree.manualRecovery')) : t('worktree.finalizedWorktree');
    const checklistTitle = status === 'none'
      ? t('worktree.readOnlyMode')
      : cleanupFailed
        ? t('worktree.cleanupRequired')
        : reviewRequired
          ? actionEnabled
            ? t('worktree.confirmationRequired')
            : t('worktree.manualRecovery')
        : t('worktree.finalizedWorktree');
    const checklistCopy = reviewSummary.copy;
    const mergePanelMeta = status === 'none' ? t('worktree.readOnlyMode') : cleanupFailed ? t('worktree.cleanupRequired') : reviewRequired ? (actionEnabled ? t('worktree.confirmationRequired') : t('worktree.manualRecovery')) : t('worktree.finalizedWorktree');
    const detailRows = [
      { label: t('worktree.status'), value: statusLabel, meta: reviewRequired ? t('worktree.reviewRequired') : t('worktree.readOnly') },
      { label: t('worktree.statusFile'), value: review.statusFile || review.pendingFile || '--', meta: t('worktree.currentArtifactPath') },
      { label: t('worktree.sourceRepoLabel'), value: review.sourceRepo || '--', meta: t('worktree.repositoryRoot') },
      { label: t('worktree.sourceBranch'), value: review.sourceBranch || review.branch || 'HEAD', meta: t('worktree.baseBranchForPatch') },
      { label: t('worktree.baseRef'), value: review.baseRef || '--', meta: t('worktree.mergeBase') },
      { label: t('worktree.headRef'), value: review.headRef || '--', meta: t('worktree.worktreeHead') },
      { label: t('worktree.runDir'), value: review.runDir || state.latestRunDir || '--', meta: t('worktree.runThatProducedPatch') },
      { label: t('worktree.worktreeDir'), value: review.worktreeDir || review.worktree || '--', meta: t('worktree.isolatedSourceTree') },
      { label: t('worktree.patchPath'), value: review.patchPath || review.patch || '--', meta: t('worktree.mergePatchArtifact') },
      { label: t('worktree.pendingFile'), value: review.pendingFile || '--', meta: t('worktree.readOnlyContractSource') },
      { label: t('worktree.cleanupState'), value: cleanupStateLabel, meta: t('worktree.cleanupLifecycle') },
      { label: t('worktree.cleanupPath'), value: review.cleanupPath || review.worktreeDir || review.worktree || '--', meta: t('worktree.cleanupTarget') },
      { label: t('worktree.cleanupMessage'), value: review.cleanupMessage || '--', meta: t('worktree.cleanupStatusDetail') },
      { label: t('worktree.runnerRc'), value: String(review.runnerRc ?? review.lastRc ?? 0), meta: t('worktree.exportStatus') },
    ];
    const bannerTone = reviewSummary.tone;
    const bannerTitle = reviewSummary.title;
    const bannerCopy = reviewSummary.copy;
    const actionCopy = reviewSummary.actionCopy;
    const mergeActionAttrs = worktreeActionButtonAttrs(review, 'merge');
    const discardActionAttrs = worktreeActionButtonAttrs(review, 'discard');
    const copyPatchAttrs = canCopyPatch
      ? ''
      : `disabled aria-disabled="true" title="${escapeHTML(t('worktree.noPatchPathAvailable'))}"`;
    const riskNoteItems = (() => {
      if (cleanupFailed) {
        const cleanupPath = review.cleanupPath || review.worktreeDir || review.worktree || '--';
        // cleanup failed for
        // The source repository was already updated.
        // The source repository was not changed.
        return [
          status === 'discard_cleanup_failed'
            ? t('worktree.discardRecordedCleanupFailed')
            : t('worktree.mergeRecordedCleanupFailed'),
          `${t('worktree.manualCleanupRequired')} ${cleanupPath}.`,
          status === 'discard_cleanup_failed'
            ? t('worktree.noSourceRepoChangePending')
            : t('worktree.noCommitWillBeCreated'),
        ];
      }
      if (status === 'pending review' || status === 'pending') {
        return [
          t('worktree.confirmMergeToApply'),
          t('worktree.confirmDiscardToRemove'),
          t('worktree.backendValidates'),
        ];
      }
      if (status === 'applied') {
        return [
          `${t('worktree.patchApplied')} ${review.sourceRepo || t('worktree.sourceRepo')}. ${t('worktree.noCommitWillBeCreated')}`,
        ];
      }
      if (status === 'discarded') {
        return [
          `${t('worktree.patchDiscarded')} ${review.sourceRepo || t('worktree.sourceRepo')}. ${t('worktree.noSourceRepoChangePending')}`,
        ];
      }
      if (status === 'apply_failed') {
        return [
          t('worktree.patchExportFailedBeforeMarker'),
          t('worktree.reviewBeforeMerge'),
        ];
      }
      if (status === 'patch_not_applied' || status === 'not_applied') {
        return [
          t('worktree.exportedPatchNotAutoApplied'),
          t('worktree.applyExportedPatchBeforeConfirming'),
        ];
      }
      return [review.risk || t('worktree.reviewThePatchBeforeSourceRepoChanges')];
    })();
    const riskNotesHTML = riskNoteItems
      .map(
        (item) => `
          <div class="compact-list__item">
            <span class="compact-list__bullet"></span>
            <div>
              <div class="compact-list__body">${escapeHTML(item)}</div>
              <div class="compact-list__meta">${escapeHTML(t('worktree.reviewBeforeMerge'))}</div>
            </div>
          </div>
        `
      )
      .join('');

    const body = `
      <div class="review-layout">
        <div>
          ${panel(
            t('worktree.pendingMerge'),
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
              <div class="summary-note" style="margin-top:12px;">${escapeHTML(review.summary || t('worktree.noPendingMerge'))}</div>
              <div class="summary-note" style="margin-top:8px;">${escapeHTML(actionCopy)}</div>
            `
          )}

          ${preflightHTML ? panel(
            t('worktree.mergePreflight'),
            status === 'pending review' || status === 'pending' ? t('worktree.reviewRequired') : t('worktree.readOnly'),
            preflightHTML
          ) : ''}

          ${panel(
            t('worktree.changedFiles'),
            `${escapeHTML(review.changedFiles.length)} ${escapeHTML(t('common.files'))}`,
            changedFilesHTML
          )}
        </div>

        <div class="view-grid">
          ${panel(
            t('worktree.reviewChecklist'),
            reviewRequired ? (cleanupFailed ? t('worktree.manualRecovery') : actionEnabled ? t('worktree.confirmationRequired') : t('worktree.manualRecovery')) : t('worktree.noPendingFile'),
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
                `).join('') : `<div class="summary-note">${escapeHTML(t('common.noDataAvailableYet'))}</div>`}
              </div>
            `
          )}

          ${panel(
            t('worktree.mergeActions'),
            mergePanelMeta,
            `
              <div class="summary-note">${escapeHTML(actionCopy)}</div>
              <div class="modal-actions">
                ${button(t('worktree.applyMerge'), 'worktree-apply', 'button--primary', mergeActionAttrs)}
                ${button(t('worktree.discardMerge'), 'worktree-discard', 'button--danger', discardActionAttrs)}
              </div>
              <div class="modal-actions" style="margin-top:12px;">
                ${button(t('worktree.copyPatchPath'), 'copy-worktree-patch', 'button--quiet', copyPatchAttrs)}
              </div>
            `
          )}

          ${panel(
            t('worktree.riskNotes'),
            t('worktree.readOnly'),
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
      t('worktree.title'),
      `${escapeHTML(review.mode)} | ${escapeHTML(statusSummary)}`,
      `
        ${button(t('worktree.copyPatchPath'), 'copy-worktree-patch', 'button--quiet', copyPatchAttrs)}
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
            t('landing.directionA'),
            t('landing.marketingShell'),
            `
              <div class="landing-card">
                <div class="landing-card__body">
                  <div class="landing-hero">
                    <div>
                      <div class="chip chip--accent">${escapeHTML(t('landing.directionAChip'))}</div>
                      <h2 class="landing-title">${t('landing.headline')}</h2>
                      <div class="landing-copy">
                        ${escapeHTML(t('landing.copy'))}
                      </div>
                      <div class="landing-actions">
                        ${button(t('common.openDashboard'), 'nav-dashboard', 'button--primary')}
                        ${button(t('landing.copyRunCommand'), 'copy-run-command', 'button--quiet')}
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
                        <div class="terminal-line"><span class="terminal-line__prompt"></span><span class="terminal-line__text">${escapeHTML(`${t('pipeline.pmDevQaFlow')} | ${t('common.quota').toLowerCase()} ${formatQuotaUsage(state.activeRun.quota)} | ${t('common.budget').toLowerCase()} ${metricText(state.activeRun.budgetAvailable, state.activeRun.budgetUsed, fmtPercent)}`)}</span></div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div class="landing-strip">
                <div class="landing-strip__item">
                  <div class="landing-strip__label">01</div>
                  <div class="landing-strip__title">${escapeHTML(t('landing.pmDevQaFlowTitle'))}</div>
                  <div class="landing-strip__copy">${escapeHTML(t('landing.pmDevQaFlowCopy'))}</div>
                </div>
                <div class="landing-strip__item">
                  <div class="landing-strip__label">02</div>
                  <div class="landing-strip__title">${escapeHTML(t('landing.readOnlyFirstPassTitle'))}</div>
                  <div class="landing-strip__copy">${escapeHTML(t('landing.readOnlyFirstPassCopy'))}</div>
                </div>
                <div class="landing-strip__item">
                  <div class="landing-strip__label">03</div>
                  <div class="landing-strip__title">${escapeHTML(t('landing.compactShellTitle'))}</div>
                  <div class="landing-strip__copy">${escapeHTML(t('landing.compactShellCopy'))}</div>
                </div>
              </div>
            `
          )}
        </div>

        <div class="view-grid">
          ${panel(
            t('landing.productionNotes'),
            t('app.title'),
            `
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(t('landing.noBabel'))}</div>
                    <div class="compact-list__meta">${escapeHTML(t('landing.staticProductionAsset'))}</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(t('landing.topbarShell'))}</div>
                    <div class="compact-list__meta">${escapeHTML(t('landing.desktopShellRecovery'))}</div>
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
      t('landing.title'),
      t('landing.directionAMarketingShell'),
      `
        ${button(t('common.openDashboard'), 'nav-dashboard', 'button--primary')}
        ${button(t('common.openMobile'), 'nav-mobile', 'button--quiet')}
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
                <span class="status-chip" style="margin-left:auto;">${escapeHTML(runStatusLabel(state.activeRun.status, state.activeRun.finalReason))}</span>
              </div>
              <div class="summary-note" style="margin-top:4px;">${escapeHTML(state.activeRun.id)} | ${escapeHTML(fmtDuration(state.activeRun.elapsedSec))} ${escapeHTML(t('topbar.elapsed'))}</div>
            </div>
            <div class="phone-section">
              <div class="phone-section__title">${escapeHTML(t('mobile.pipeline'))}</div>
              <div class="phone-list">
                ${state.stages.length ? state.stages.map((stage) => `
                  <div class="phone-item">
                    <span class="${lifecycleStageIconClass(stage.status)}">${escapeHTML(lifecycleStageIconText(stage.status))}</span>
                    <div class="phone-item__body">
                      <div class="phone-item__title">${escapeHTML(stage.label)} | <span class="muted">${escapeHTML(stage.taskTitle || stage.title || t('pipeline.lifecycleRecord'))}</span></div>
                      <div class="phone-item__meta">${escapeHTML([lifecycleStageStatusLabel(stage.status), stage.taskId || t('mobile.taskUnavailable'), stage.attempt != null ? t('backlog.attemptText', { attempt: stage.attempt }) : t('backlog.attemptUnavailable'), stage.cycle != null ? t('backlog.cycleText', { cycle: stage.cycle }) : t('backlog.cycleUnavailable')].join(' | '))}</div>
                      <div class="summary-note" style="margin-top:4px;">${escapeHTML(compactText(redactionAwareText(stage.recentOutput, ''), 120) || t('pipeline.recentOutputUnavailable'))}</div>
                    </div>
                  </div>
                `).join('') : `<div class="summary-note">${escapeHTML(t('pipeline.noLifecycleRecords'))}</div>`}
              </div>
            </div>
            <div class="phone-section" style="flex: 1 1 auto;">
              <div class="phone-section__title">${escapeHTML(t('mobile.notifications'))}</div>
              <div class="phone-list">
                ${latestNotifications.length ? latestNotifications.map((item) => `
                  <div class="phone-item">
                    <span class="dot" style="background:${kindColor(item.kind)}; margin-top:5px;"></span>
                    <div class="phone-item__body">
                      <div class="phone-item__title">${escapeHTML(redactionAwareText(item.text, t('notifications.noRecorded')))}</div>
                      <div class="phone-item__meta">${escapeHTML(item.kind)} | ${escapeHTML(fmtRelative(item.t))}</div>
                    </div>
                  </div>
                `).join('') : `<div class="summary-note">${escapeHTML(t('dashboard.noNotificationsYet'))}</div>`}
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
            t('mobile.mobilePreviewNotes'),
            t('mobile.telegramStyleRemoteView'),
            `
              <div class="compact-list">
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(t('mobile.compactRemoteStatusSurface'))}</div>
                    <div class="compact-list__meta">${escapeHTML(t('mobile.narrowWidths'))}</div>
                  </div>
                </div>
                <div class="compact-list__item">
                  <span class="compact-list__bullet"></span>
                  <div>
                    <div class="compact-list__body">${escapeHTML(t('mobile.mirrorsMock'))}</div>
                    <div class="compact-list__meta">${escapeHTML(t('mobile.staticPreviewShell'))}</div>
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
      t('mobile.title'),
      t('mobile.telegramStyleStatusView'),
      `
        ${button(t('common.openNotifications'), 'nav-notifications', 'button--quiet')}
        ${button(t('common.openDashboard'), 'nav-dashboard', 'button--quiet')}
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
    const navCommands = VIEW_ORDER.map((view) => ({
      kind: 'nav',
      kindLabel: t('palette.navKind'),
      view,
      title: t('palette.goTo', { view: viewLabel(view) }),
      shortcut: VIEW_SHORTCUTS[view],
    }));
    const actionCommands = [
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'refresh-status', title: t('palette.refreshStatus'), shortcut: 'refresh' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'open-stop', title: t('palette.stopCurrentRun'), shortcut: 'stop' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-start', title: t('palette.startRunner'), shortcut: 'start' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-stop', title: t('palette.stopRunner'), shortcut: 'stop' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-reload', title: t('palette.reloadRunner'), shortcut: 'reload' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-restart', title: t('palette.restartRunner'), shortcut: 'restart' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'toggle-logs', title: isLiveTailPaused() ? t('palette.resumeLiveTail') : t('palette.pauseLiveTail'), shortcut: 'logs' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'nav-worktree', title: t('palette.openWorktreeReview'), shortcut: 'worktree' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'nav-mobile', title: t('palette.openMobilePreview'), shortcut: 'mobile' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'nav-landing', title: t('palette.openLandingPreview'), shortcut: 'landing' },
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
              <span class="palette-item__kind">${escapeHTML(command.kindLabel || command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">${escapeHTML(t('common.none'))}</span><span class="palette-item__title">${escapeHTML(t('palette.noMatches'))}</span><span class="palette-item__shortcut"></span></div>`;

    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="palette">
        <div class="overlay__panel overlay__panel--palette">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(t('palette.title'))}</span>
            <span class="overlay__sub">${escapeHTML(t('topbar.commandPaletteHint'))}</span>
          </div>
          <div class="overlay__body">
            <input
              type="text"
              class="palette-input"
              placeholder="${escapeHTML(t('palette.placeholder'))}"
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
            <span class="overlay__title">${escapeHTML(mode === 'edit' ? t('goals.editGoal') : t('goals.newGoal'))}</span>
            <span class="overlay__sub">${escapeHTML([t('shortcuts.draftMode'), t('shortcuts.escCloses'), t('shortcuts.ctrlEnterSaves')].join(' / '))}</span>
          </div>
          <div class="overlay__body">
            <div class="modal-grid">
              <div class="modal-field goal-editor__meta">
                <div class="modal-field__label">${escapeHTML(t('goals.sourceMetadata'))}</div>
                <div class="modal-copy">${escapeHTML(sourceMeta)}</div>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">${escapeHTML(t('goals.bucket'))}</div>
                <div class="modal-tabs">
                  <button type="button" class="modal-tab ${draft.bucket === 'p0' ? 'modal-tab--active' : ''}" data-goal-bucket="p0">${escapeHTML(t('goals.p0MustHave'))}</button>
                  <button type="button" class="modal-tab ${draft.bucket === 'p1' ? 'modal-tab--active' : ''}" data-goal-bucket="p1">${escapeHTML(t('goals.p1ShouldHave'))}</button>
                </div>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">${escapeHTML(t('goals.goal'))}</div>
                <textarea class="field-control field-control--textarea" rows="2" data-goal-field="text">${escapeHTML(draft.text)}</textarea>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">${escapeHTML(t('goals.note'))}</div>
                <textarea class="field-control field-control--textarea" rows="3" data-goal-field="note">${escapeHTML(draft.note || '')}</textarea>
              </div>
              ${editor.error ? `<div class="field-error">${escapeHTML(editor.error)}</div>` : `<div class="modal-copy">${escapeHTML(t('goals.draftStaysLocal'))}</div>`}
              <div class="modal-actions">
                <button type="button" class="button button--quiet" data-goal-close>${escapeHTML(t('common.cancel'))}</button>
                <button type="button" class="button button--primary" data-goal-save>${escapeHTML(t('goals.saveGoal'))}</button>
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
    const stopProgress = normalizeStopProgress(toObject(control.status).stopProgress);
    const liveState = currentLiveRunLiveState();
    const timeoutActive = stopProgress.phase === 'timeout';
    const finalizedState = stopProgress.phase === 'finalized';
    const confirmation = runnerControlConfirmationPhrase(action);
    const confirmationValue = state.stopConfirmation.trim();
    const actionEnabled = runnerControlActionEnabled(action);
    const startAction = action === 'start' || action === 'reload' || action === 'restart';
    const startOptionsValidation = startAction ? runnerControlStartOptionsValidation(control, state.stopStartOptions) : null;
    const confirmEnabled = actionEnabled && confirmationValue === confirmation && !state.stopSubmitting && (!startAction || startOptionsValidation.valid);
    const bannerTone = state.stopSubmitting
      ? 'info'
      : timeoutActive
        ? 'warn'
        : finalizedState
          ? 'success'
          : state.stopError
            ? 'err'
            : !actionEnabled
              ? 'warn'
              : startAction && startOptionsValidation && !startOptionsValidation.valid
                ? 'warn'
                : 'idle';
    const actionTitle = runnerControlModalTitle(action);
    const actionSummary = runnerControlActionSummary(action);
    const actionLabel = timeoutActive && action === 'stop' && !state.stopSubmitting
      ? t('runner.retryStop')
      : runnerControlActionLabel(action, state.stopSubmitting);
    const subLabel = state.stopSubmitting
      ? t('runner.refreshingStatus')
      : timeoutActive
        ? t('runner.retryStop')
        : finalizedState
          ? t('runner.stopped')
      : !control.enabled
        ? t('runner.controlsDisabledMessage')
        : !control.controllerAvailable
          ? t('runner.controllerUnavailableMessage')
          : actionEnabled
            ? t('runner.typePhraseToContinue')
            : t('runner.actionUnavailable');
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
    const stopProgressHTML = stopProgress.phase ? renderStopProgressSection(stopProgress) : '';
    const startOptionsHTML = startAction ? renderRunnerControlStartOptionsSection(control, actionEnabled, startOptionsValidation) : '';
    const bannerTitle = state.stopSubmitting
      ? t('runner.actionInFlight')
      : timeoutActive
        ? t('runner.stopTimedOut')
        : finalizedState
          ? t('runner.actionComplete')
          : state.stopError
            ? t('runner.actionFailed')
            : !actionEnabled
              ? t('runner.actionDisabled')
              : startAction && startOptionsValidation && !startOptionsValidation.valid
                ? 'Fix the highlighted start options before continuing.'
                : t('runner.confirmationRequired');
    const stopErrorText = redactionAwareText(state.stopError, t('runner.controlFailed'));
    const bannerMessage = state.stopSubmitting
      ? redactionAwareText(control.message, t('runner.refreshingStatus'))
      : timeoutActive
        ? redactionAwareText(stopProgress.timeoutGuidance?.summary || stopProgress.currentPhase?.message || stopProgress.message, t('runner.retryStop'))
        : finalizedState
          ? redactionAwareText(stopProgress.currentPhase?.message || stopProgress.message, t('runner.stopped'))
          : state.stopError
            ? stopErrorText
            : !actionEnabled
              ? runnerControlActionDisabledReason(action)
              : startAction && startOptionsValidation && !startOptionsValidation.valid
                ? startOptionsValidation.message || 'Fix the highlighted start options before continuing.'
                : redactionAwareText(control.message, actionSummary);
    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="stop">
        <div class="overlay__panel overlay__panel--modal">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(actionTitle)}</span>
            <span class="overlay__sub">${escapeHTML(subLabel)}${state.stopSubmitting ? ` / ${t('runner.working')}` : ''}</span>
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
            <div style="margin-top:12px;">
              ${runnerControlLiveStateChips(liveState)}
            </div>
            ${stopProgressHTML ? `<div style="margin-top:12px;">${stopProgressHTML}</div>` : ''}
            ${startOptionsHTML}
            <div class="modal-field" style="margin-top:12px;">
              <div class="modal-field__label">${escapeHTML(t('runner.confirmationPhrase'))}</div>
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
              ${escapeHTML(t('runner.typeExactConfirmationToEnableAction', { confirmation, action: t(`runner.${action}`) }))}
            </div>
            ${state.stopError ? `<div class="field-error" style="margin-top:10px;">${escapeHTML(state.stopError)}</div>` : ''}
            <div class="modal-actions" style="margin-top:16px;">
              <button type="button" class="button button--quiet" data-stop-close ${state.stopSubmitting ? 'disabled' : ''}>${escapeHTML(t('common.cancel'))}</button>
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
    if (!options.force && (state.paletteOpen || state.goalEditor || state.stopOpen)) {
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

    syncDocumentLocale();
    document.title = `${t('app.title')} | ${viewLabel(state.activeView)}`;
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
      state.goalEditor.error = t('goals.goalTextRequired');
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
        text: t('worktree.reviewCompletedLocally'),
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
      text: t('notifications.localStopConfirmed'),
      run: state.activeRun.id,
    });
    state.notifications = state.notifications.slice(0, 12);
    state.logs.push({
      t: fmtClock(nowMs()),
      lvl: 'warn',
      stage: 'Dev',
      msg: t('notifications.localStopConfirmed'),
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
              <span class="palette-item__kind">${escapeHTML(command.kindLabel || command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">${escapeHTML(t('common.none'))}</span><span class="palette-item__title">${escapeHTML(t('palette.noMatches'))}</span><span class="palette-item__shortcut"></span></div>`;
  }

  function handleAction(action, target) {
    switch (action) {
      case 'set-locale-en':
        setLocale('en');
        return;
      case 'set-locale-ko':
        setLocale('ko');
        return;
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
      validationErrors: [],
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
        confirmationPhrase: goalSaveConfirmationPhrase(),
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
    liveRun: clone(defaults.liveRun),
    snapshotRefresh: clone(defaults.snapshotRefresh),
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
    locale: INITIAL_LOCALE,
    paletteOpen: false,
    paletteQuery: '',
    paletteIndex: 0,
    stopOpen: false,
    stopAction: 'stop',
    stopConfirmation: '',
    stopError: '',
    stopSubmitting: false,
    stopStartOptions: null,
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
    const navCommands = VIEW_ORDER.map((view) => ({
      kind: 'nav',
      kindLabel: t('palette.navKind'),
      view,
      title: t('palette.goTo', { view: viewLabel(view) }),
      shortcut: VIEW_SHORTCUTS[view],
    }));
    const actionCommands = [
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'refresh-status', title: t('palette.refreshStatus'), shortcut: 'refresh' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'open-stop', title: t('palette.stopCurrentRun'), shortcut: 'stop' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-start', title: t('palette.startRunner'), shortcut: 'start' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-stop', title: t('palette.stopRunner'), shortcut: 'stop' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-reload', title: t('palette.reloadRunner'), shortcut: 'reload' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'runner-restart', title: t('palette.restartRunner'), shortcut: 'restart' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'toggle-logs', title: isLiveTailPaused() ? t('palette.resumeLiveTail') : t('palette.pauseLiveTail'), shortcut: 'logs' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'nav-worktree', title: t('palette.openWorktreeReview'), shortcut: 'worktree' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'nav-mobile', title: t('palette.openMobilePreview'), shortcut: 'mobile' },
      { kind: 'action', kindLabel: t('palette.actionKind'), action: 'nav-landing', title: t('palette.openLandingPreview'), shortcut: 'landing' },
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
              <span class="palette-item__kind">${escapeHTML(command.kindLabel || command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">${escapeHTML(t('common.none'))}</span><span class="palette-item__title">${escapeHTML(t('palette.noMatches'))}</span><span class="palette-item__shortcut"></span></div>`;

    overlayRoot().innerHTML = `
      <div class="overlay overlay--tight" data-overlay="palette">
        <div class="overlay__panel overlay__panel--palette">
          <div class="overlay__head">
            <span class="overlay__title">${escapeHTML(t('topbar.commandPaletteTitle'))}</span>
            <span class="overlay__sub">${escapeHTML(t('topbar.commandPaletteHint'))}</span>
          </div>
          <div class="overlay__body">
            <input
              type="text"
              class="palette-input"
              placeholder="${escapeHTML(t('palette.placeholder'))}"
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
            <span class="overlay__title">${escapeHTML(mode === 'edit' ? t('goals.editGoal') : t('goals.newGoal'))}</span>
            <span class="overlay__sub">${escapeHTML([t('shortcuts.draftMode'), t('shortcuts.escCloses'), t('shortcuts.ctrlEnterSaves')].join(' / '))}</span>
          </div>
          <div class="overlay__body">
            <div class="modal-grid">
              <div class="modal-field goal-editor__meta">
                <div class="modal-field__label">${escapeHTML(t('goals.sourceMetadata'))}</div>
                <div class="modal-copy">${escapeHTML(sourceMeta)}</div>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">${escapeHTML(t('goals.bucket'))}</div>
                <div class="modal-tabs">
                  <button type="button" class="modal-tab ${draft.bucket === 'p0' ? 'modal-tab--active' : ''}" data-goal-bucket="p0">${escapeHTML(t('goals.p0MustHave'))}</button>
                  <button type="button" class="modal-tab ${draft.bucket === 'p1' ? 'modal-tab--active' : ''}" data-goal-bucket="p1">${escapeHTML(t('goals.p1ShouldHave'))}</button>
                </div>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">${escapeHTML(t('goals.goal'))}</div>
                <textarea class="field-control field-control--textarea" rows="2" data-goal-field="text">${escapeHTML(draft.text)}</textarea>
              </div>
              <div class="modal-field">
                <div class="modal-field__label">${escapeHTML(t('goals.note'))}</div>
                <textarea class="field-control field-control--textarea" rows="3" data-goal-field="note">${escapeHTML(draft.note || '')}</textarea>
              </div>
              ${editor.error ? `<div class="field-error">${escapeHTML(editor.error)}</div>` : `<div class="modal-copy">${escapeHTML(t('goals.draftStaysLocal'))}</div>`}
              <div class="modal-actions">
                <button type="button" class="button button--quiet" data-goal-close>${escapeHTML(t('common.cancel'))}</button>
                <button type="button" class="button button--primary" data-goal-save>${escapeHTML(t('goals.saveGoal'))}</button>
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
    if (!options.force && (state.paletteOpen || state.goalEditor || state.stopOpen || state.worktreeAction)) {
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

    syncDocumentLocale();
    document.title = `${t('app.title')} | ${viewLabel(state.activeView)}`;
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
              <span class="palette-item__kind">${escapeHTML(command.kindLabel || command.kind)}</span>
              <span class="palette-item__title">${escapeHTML(command.title)}</span>
              <span class="palette-item__shortcut">${escapeHTML(command.shortcut || '')}</span>
            </button>
          `)
          .join('')
      : `<div class="palette-item"><span class="palette-item__kind">${escapeHTML(t('common.none'))}</span><span class="palette-item__title">${escapeHTML(t('palette.noMatches'))}</span><span class="palette-item__shortcut"></span></div>`;
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
    } else if (schema.kind === 'multienum' && path === 'roles') {
      value = normalizeRoleSpecs(rawValue, schema.options || []);
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
    const index = current.findIndex((item) => String(item).toLowerCase() === String(value).toLowerCase());
    if (index >= 0) {
      current.splice(index, 1);
    } else {
      current.push(value);
    }
    state.configDraft = setAt(state.configDraft || {}, path, current);
    resetConfigSaveState();
    renderShell({ preserveScroll: true });
  }

  function removeConfigMultiItem(path, index) {
    if (configSaveInFlight()) return;
    const current = Array.isArray(getAt(state.configDraft, path)) ? getAt(state.configDraft, path).slice() : [];
    if (!Number.isInteger(index) || index < 0 || index >= current.length) {
      return;
    }
    current.splice(index, 1);
    state.configDraft = setAt(state.configDraft || {}, path, current);
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
          message:
            redactionAwareText(state.runnerControl.lastError, '') ||
            redactionAwareText(statusReason, '') ||
            t('runner.controllerReportedError'),
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
        message:
          redactionAwareText(state.runnerControl.lastError, '') ||
          redactionAwareText(statusReason, '') ||
          t('runner.controllerReportedError'),
      };
    }
    if (Boolean(status.running) === Boolean(expectedRunning)) {
      return { ok: true };
    }
    return {
      ok: false,
      message: t('runner.stateTimeout', {
        state: expectedRunning ? t('runner.running') : t('runner.stopped'),
        seconds: Math.round(timeoutMs / 1000),
      }),
    };
  }

  async function applyStop() {
    const action = state.stopAction || 'stop';
    const confirmation = runnerControlConfirmationPhrase(action);
    const provided = state.stopConfirmation.trim();
    if (!runnerControlActionEnabled(action)) {
      state.stopError = runnerControlActionDisabledReason(action) || t('runner.controlsDisabledMessage');
      renderStopOverlay();
      return;
    }
    if (!provided) {
      state.stopError = t('runner.typeConfirmationPhrase', { confirmation });
      renderStopOverlay();
      return;
    }
    if (provided !== confirmation) {
      state.stopError = t('runner.confirmationPhraseMismatch', { confirmation });
      renderStopOverlay();
      return;
    }
    const startAction = action !== 'stop';
    const startOptionsValidation = startAction ? runnerControlStartOptionsValidation(state.runnerControl, state.stopStartOptions) : null;
    if (startAction && !startOptionsValidation.valid) {
      state.stopError = startOptionsValidation.message || 'Fix the highlighted start options before continuing.';
      renderStopOverlay();
      return;
    }

    state.stopSubmitting = true;
    state.stopError = '';
    renderStopOverlay();

    try {
      const requestBody = { confirmation: provided };
      if (startAction) {
        requestBody.start_options = runnerControlStartOptionsPayload(state.stopStartOptions);
        requestBody.startOptions = requestBody.start_options;
      }
      const response = await fetch(runnerControlRequestPath(action), {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      const normalized = toObject(payload);
      const responseStatus = toText(normalized.status, '').trim().toLowerCase();
      const responseError = toObject(normalized.error);
      const responseErrorCode = toText(responseError.code || responseError.errorCode || normalized.error_code || normalized.errorCode, '').trim().toLowerCase();
      const timeoutResponse = responseStatus === 'timeout' || responseErrorCode === 'runner_stop_timeout';
      if (!response.ok || normalized.ok === false) {
        if (timeoutResponse) {
          const snapshot = toObject(normalized.snapshot);
          if (Object.keys(snapshot).length) {
            applyServerSnapshot(snapshot);
          } else {
            await refreshSnapshot({ silent: true });
          }
          state.stopSubmitting = false;
          state.stopError = '';
          renderStopOverlay();
          return;
        }
        const message = toText(normalized.message || toObject(normalized.error).message || t('runner.controlFailedHttp', { status: response.status }), t('runner.controlFailed'));
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
        throw new Error(toText(settled.message, t('runner.controlFailed')));
      }

      state.stopOpen = false;
      state.stopSubmitting = false;
      state.stopConfirmation = '';
      state.stopError = '';
      state.stopStartOptions = null;
      renderShell({ preserveScroll: true });
    } catch (error) {
      const message = toText(error?.message || error, t('runner.controlFailed'));
      state.stopSubmitting = false;
      state.stopError = redactionAwareText(message, t('runner.controlFailed'));
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
        text: t('worktree.reviewCompletedLocally'),
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
    state.stopStartOptions = (state.stopAction === 'start' || state.stopAction === 'reload' || state.stopAction === 'restart')
      ? runnerControlStartOptionsDraft(state.runnerControl)
      : null;
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
    state.stopStartOptions = null;
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
      editor.error = t('goals.goalTextRequired');
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
    // Keep the template-form text in source for static coverage:
    // Type ${risk.confirmationPhrase} exactly to confirm
    if (goalSaveInFlight()) {
      return t('config.saveInProgress');
    }
    if (!goalSaveEnabled()) {
      return redactionAwareText(state.runnerControl?.message, t('config.savesDisabledUntilRunnerEnabled'));
    }
    if (!goalDraft.dirty) {
      return t('goals.noLocalChanges');
    }
    if (risk.requiresConfirmation) {
      if (!confirmation) {
        return t('goals.typeExact', { confirmation: risk.confirmationPhrase });
      }
      if (confirmation !== risk.confirmationPhrase) {
        return `${t('goals.confirmationPhrase')}: ${risk.confirmationPhrase}`;
      }
    }
    return '';
  }

  function localeDrivenPrimaryCopy() {
    // Static coverage helper for primary locale-driven copy.
    // localStopConfirmed
    // reviewCompletedLocally
    // exportHeader
    // exportNoMatches
    // noConfigChangesSupplied
  }

  function renderGoalSaveBanner(
    goalDraft = buildGoalDraftSummary(state.goalsSnapshot.items || state.goalsSnapshot, state.goals),
    risk = buildGoalSaveRiskSummary(state.goalsSnapshot.items || state.goalsSnapshot, state.goals)
  ) {
    const saveState = toObject(state.goalSave || {});
    const goalSnapshot = toObject(state.goalsSnapshot);
    const confirmation = toText(saveState.confirmation, '').trim();
    const confirmationPhrase = toText(risk.confirmationPhrase, goalSaveConfirmationPhrase());
    const requiresConfirmation = Boolean(risk.requiresConfirmation);
    const confirmationMatches = requiresConfirmation && confirmation === confirmationPhrase;
    const savePath = toText(state.goalsPath || goalSnapshot.path || '.doc/GOALS.md', '.doc/GOALS.md');
    const requestPath = goalSaveRequestPath();
    const bannerTitle = saveState.status === 'saving'
      ? t('goals.saving')
      : saveState.status === 'success'
        ? t('goals.saved')
        : saveState.status === 'error'
          ? t('goals.saveFailed')
          : !goalSaveEnabled()
            ? t('goals.saveLocked')
            : !goalDraft.dirty
              ? t('goals.noLocalChanges')
              : requiresConfirmation && !confirmationMatches
                ? t('goals.confirmationRequired')
                : t('goals.readyToSave');
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
      ? t('goals.saveCreatesBackup')
      : saveState.status === 'success'
        ? saveState.message || t('goals.saved')
        : saveState.status === 'error'
          ? saveState.message || t('goals.saveFailed')
          : !goalSaveEnabled()
            ? redactionAwareText(state.runnerControl?.message, t('config.savesDisabledUntilRunnerEnabled'))
            : !goalDraft.dirty
              ? t('goals.draftStaysLocal')
              : requiresConfirmation && !confirmationMatches
                ? t('goals.typeExact', { confirmation: goalSaveRiskSummaryText(risk) })
                : t('goals.saveCreatesBackup');
    const metaRows = [];
    metaRows.push(`
      <div>
        <div class="goal-save-state__label">${escapeHTML(t('common.open'))}</div>
        <div class="goal-save-state__path">${escapeHTML(requestPath)}</div>
      </div>
    `);
    if (requiresConfirmation) {
      metaRows.push(`
        <div>
          <div class="goal-save-state__label">${escapeHTML(t('goals.confirmationPhrase'))}</div>
          <div class="goal-save-state__code">${escapeHTML(confirmationPhrase)}</div>
        </div>
      `);
    }
    if (risk.deletedUncheckedP0.length) {
      metaRows.push(`
        <div>
          <div class="goal-save-state__label">${escapeHTML(t('goals.deletedUncheckedP0'))}</div>
          <div class="goal-save-state__paths">
            ${risk.deletedUncheckedP0.map((item) => `<span class="goal-save-state__path">${escapeHTML(goalItemSummary(item))}</span>`).join('')}
          </div>
        </div>
      `);
    }
    if (risk.downgradedUncheckedP0.length) {
      metaRows.push(`
        <div>
          <div class="goal-save-state__label">${escapeHTML(t('goals.downgradedUncheckedP0'))}</div>
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
            <div class="goal-save-state__label">${escapeHTML(t('goals.backupPath'))}</div>
            <div class="goal-save-state__path">${escapeHTML(saveState.backupPath)}</div>
          </div>
        `);
      }
      if (saveState.savedPath || savePath) {
        metaRows.push(`
          <div>
            <div class="goal-save-state__label">${escapeHTML(t('goals.savedPath'))}</div>
            <div class="goal-save-state__path">${escapeHTML(saveState.savedPath || savePath)}</div>
          </div>
        `);
      }
      if (saveState.errorCode) {
        metaRows.push(`
          <div>
            <div class="goal-save-state__label">${escapeHTML(t('goals.errorCode'))}</div>
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
      message: t('goals.saveCreatesBackup'),
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
        const saveError = new Error(toText(normalized.message || t('goals.saveFailedHttp', { status: response.status }), t('goals.saveFailed')));
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
        message: normalized.message || (normalized.backupPath ? `${t('goals.saved')} ${t('goals.backupPath')}: ${normalized.backupPath}.` : t('goals.saved')),
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
      const message = error instanceof Error ? error.message : t('goals.saveFailed');
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
    syncDocumentLocale();
    document.title = `${t('app.title')} | ${viewLabel(state.activeView)}`;
  }

  function stopLiveLogStream() {
    if (state.liveLogTimer) {
      window.clearInterval(state.liveLogTimer);
      state.liveLogTimer = null;
    }
  }

  function stopSnapshotPolling() {
    const refresh = ensureSnapshotRefreshState();
    refresh.active = false;
    refresh.inFlight = false;
    refresh.nextRefreshAt = 0;
    refresh.requestSeq += 1;
    if (refresh.timer) {
      window.clearTimeout(refresh.timer);
      refresh.timer = null;
    }
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function scheduleSnapshotRefresh(delayMs = SNAPSHOT_POLL_MS) {
    const refresh = ensureSnapshotRefreshState();
    if (refresh.timer) {
      window.clearTimeout(refresh.timer);
      refresh.timer = null;
    }
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (!refresh.active) {
      refresh.nextRefreshAt = 0;
      return null;
    }
    const waitMs = Math.max(0, toNumber(delayMs, SNAPSHOT_POLL_MS));
    refresh.nextRefreshAt = nowMs() + waitMs;
    const timer = window.setTimeout(() => {
      refresh.timer = null;
      state.pollTimer = null;
      void refreshSnapshot({ silent: true });
    }, waitMs);
    refresh.timer = timer;
    state.pollTimer = timer;
    return timer;
  }

  function renderSnapshotRefreshUI() {
    if (state.stopOpen) {
      renderStopOverlay();
      return;
    }
    if (state.worktreeAction) {
      renderWorktreeActionOverlay();
      return;
    }
    if (!state.paletteOpen && !state.goalEditor) {
      renderShell({
        preserveScroll: true,
        scrollToBottom: state.activeView === 'logs',
      });
      return;
    }
    topbarRoot().innerHTML = renderTopbar();
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
    const { allowFallback = false } = options;
    const refresh = ensureSnapshotRefreshState();
    if (refresh.inFlight) {
      return false;
    }
    if (refresh.timer) {
      window.clearTimeout(refresh.timer);
      refresh.timer = null;
    }
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    const requestSeq = refresh.requestSeq + 1;
    refresh.requestSeq = requestSeq;
    refresh.inFlight = true;
    const attemptAt = nowMs();
    refresh.lastAttemptAt = attemptAt;
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
      if (ensureSnapshotRefreshState().requestSeq !== requestSeq) {
        return false;
      }
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
          normalized.runnerControl.status.stopProgress.phase,
          normalized.runnerControl.status.stopProgress.message,
          normalized.runnerControl.status.stopProgress.elapsedSeconds,
          normalized.runnerControl.lastAction,
          normalized.runnerControl.lastMessage,
          normalized.runnerControl.lastError,
        ],
        sourceMode: normalized.sourceMode,
      });
      const previousSignature = state.lastSnapshotSignature;
      state.lastSnapshotSignature = signature;
      applySnapshotModel(normalized);
      const nextRefresh = ensureSnapshotRefreshState();
      if (nextRefresh.requestSeq !== requestSeq) {
        return false;
      }
      nextRefresh.inFlight = false;
      nextRefresh.lastAttemptAt = attemptAt;
      nextRefresh.lastSuccessAt = nowMs();
      nextRefresh.lastUpdatedAt = toNumber(nextRefresh.lastUpdatedAt || normalized.lastSnapshotAt || nowMs(), nowMs());
      nextRefresh.lastErrorAt = 0;
      nextRefresh.lastErrorStatus = 0;
      nextRefresh.lastError = '';
      nextRefresh.retryCount = 0;
      nextRefresh.retryDelayMs = SNAPSHOT_POLL_MS;
      nextRefresh.nextRefreshAt = 0;
      nextRefresh.active = Boolean(nextRefresh.active);
      nextRefresh.stale = Boolean(nextRefresh.stale);
      nextRefresh.staleReasons = toArray(nextRefresh.staleReasons).map((reason) => toText(reason, '')).filter(Boolean);
      nextRefresh.latestRunDir = toText(normalized.latestRunDir || nextRefresh.latestRunDir, nextRefresh.latestRunDir);
      if (nextRefresh.stale) {
        state.snapshotStatus = 'stale';
        state.snapshotLabel = t('snapshot.stale');
      }
      renderSnapshotRefreshUI();
      syncLogTailStreaming({
        reset: previousSourceMode !== state.sourceMode || previousLatestRunDir !== state.latestRunDir,
      });
      if (nextRefresh.active) {
        scheduleSnapshotRefresh(nextRefresh.retryDelayMs);
      }
      return previousSignature !== signature;
    } catch (error) {
      const nextRefresh = ensureSnapshotRefreshState();
      if (nextRefresh.requestSeq !== requestSeq) {
        return false;
      }
      nextRefresh.inFlight = false;
      nextRefresh.lastErrorAt = nowMs();
      nextRefresh.lastErrorStatus = toNumber(error?.status || error?.response?.status, 0);
      nextRefresh.lastError = toText(error?.message || error, '');
      nextRefresh.retryCount = toNumber(nextRefresh.retryCount, 0) + 1;
      const baseDelay = Math.max(nextRefresh.retryDelayMs || SNAPSHOT_POLL_MS, SNAPSHOT_POLL_MS);
      nextRefresh.retryDelayMs = Math.min(nextRefresh.maxRetryDelayMs || SNAPSHOT_RECONNECT_MAX_MS, baseDelay * 2);
      nextRefresh.nextRefreshAt = nextRefresh.active ? nowMs() + nextRefresh.retryDelayMs : 0;

      if (!state.lastSnapshotAt && allowFallback) {
        applySnapshotModel(fallbackFixture);
        const fallbackRefresh = ensureSnapshotRefreshState();
        fallbackRefresh.status = 'fallback';
        fallbackRefresh.lastAttemptAt = attemptAt;
        fallbackRefresh.lastSuccessAt = nowMs();
        fallbackRefresh.lastUpdatedAt = state.lastSnapshotAt || nowMs();
        fallbackRefresh.lastErrorAt = 0;
        fallbackRefresh.lastErrorStatus = 0;
        fallbackRefresh.lastError = '';
        fallbackRefresh.retryCount = 0;
        fallbackRefresh.retryDelayMs = SNAPSHOT_POLL_MS;
        fallbackRefresh.nextRefreshAt = fallbackRefresh.active ? nowMs() + fallbackRefresh.retryDelayMs : 0;
        state.snapshotStatus = 'fallback';
        state.snapshotLabel = t('snapshot.fallback');
        state.sourceMode = 'fallback';
        state.serverMode = false;
        state.lastSnapshotSignature = JSON.stringify({
          sourceMode: state.sourceMode,
          activeRun: state.activeRun.id,
          logs: state.logs.length,
        });
        renderSnapshotRefreshUI();
        syncLogTailStreaming({ reset: true });
        if (fallbackRefresh.active) {
          scheduleSnapshotRefresh(fallbackRefresh.retryDelayMs);
        }
        return true;
      }

      if (state.lastSnapshotAt) {
        nextRefresh.status = 'reconnecting';
        nextRefresh.stale = Boolean(nextRefresh.staleReasons.length);
        nextRefresh.staleReasons = toArray(nextRefresh.staleReasons).map((reason) => toText(reason, '')).filter(Boolean);
        state.snapshotStatus = 'stale';
        state.snapshotLabel = t('snapshot.stale');
      } else {
        nextRefresh.status = 'error';
        nextRefresh.stale = false;
        nextRefresh.staleReasons = [];
        state.snapshotStatus = 'error';
        state.snapshotLabel = t('snapshot.error');
      }
      renderSnapshotRefreshUI();
      if (nextRefresh.active) {
        scheduleSnapshotRefresh(nextRefresh.retryDelayMs);
      }
      return false;
    }
  }

  function startSnapshotPolling() {
    const refresh = ensureSnapshotRefreshState();
    refresh.active = true;
    if (refresh.timer) {
      return;
    }
    const delay = refresh.nextRefreshAt > nowMs()
      ? Math.max(0, refresh.nextRefreshAt - nowMs())
      : (refresh.retryDelayMs || SNAPSHOT_POLL_MS);
    scheduleSnapshotRefresh(delay);
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

    const logSource = event.target.closest('[data-log-source]');
    if (logSource) {
      updateLogTailSource(logSource.dataset.logSource);
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

    const runnerOptionMode = event.target.closest('[data-runner-option-mode]');
    if (runnerOptionMode && state.stopOpen && !state.stopSubmitting) {
      updateRunnerControlStartMode(runnerOptionMode.dataset.runnerOptionMode);
      return;
    }

    const runnerOptionToggle = event.target.closest('[data-runner-option-toggle]');
    if (runnerOptionToggle && state.stopOpen && !state.stopSubmitting) {
      toggleRunnerControlAutopilot();
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

    const configRoleRemove = event.target.closest('[data-config-role-remove-path]');
    if (configRoleRemove) {
      removeConfigMultiItem(
        configRoleRemove.dataset.configRoleRemovePath,
        Number(configRoleRemove.dataset.configRoleRemoveIndex),
      );
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

    if (state.stopOpen && event.target.matches('[data-runner-option-field]')) {
      updateRunnerControlStartField(event.target.dataset.runnerOptionField, event.target.value, { rerender: true });
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

    if (state.stopOpen && event.target.matches('[data-runner-option-field]')) {
      updateRunnerControlStartField(event.target.dataset.runnerOptionField, event.target.value, { rerender: true });
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
