param(
    [string]$RepoRoot = "",
    [string]$RunDir = "",
    [int]$IntervalSeconds = 60,
    [int]$DurationMinutes = 0,
    [int]$TopProcessCount = 25,
    [switch]$IncludeCommandLine,
    [switch]$IncludeLogHandles,
    [switch]$IncludeWindowsEvents,
    [switch]$StopOnResourcePressure,
    [int]$LogHandleEvery = 5,
    [int]$WindowsEventEvery = 5,
    [int]$WindowsEventLookbackMinutes = 10,
    [int]$SystemHandleLimit = 550000,
    [int]$TopProcessHandleLimit = 250000,
    [string]$OutputPath = ""
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    param([string]$Value)
    if ($Value) {
        return (Resolve-Path -LiteralPath $Value).Path
    }
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path
}

function Resolve-LatestRunDir {
    param([string]$RepoRoot, [string]$Value)
    if ($Value) {
        return (Resolve-Path -LiteralPath $Value).Path
    }
    $runsRoot = Join-Path $RepoRoot ".AgentCLI\agent_runs"
    if (-not (Test-Path -LiteralPath $runsRoot)) {
        return $null
    }
    $latest = Get-ChildItem -LiteralPath $runsRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $latest) {
        return $null
    }
    return $latest.FullName
}

function Read-JsonFile {
    param([string]$Path)
    if (-not $Path) {
        return $null
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        if (-not $raw.Trim()) {
            return $null
        }
        return $raw | ConvertFrom-Json
    } catch {
        return [ordered]@{
            error = $_.Exception.Message
            path = $Path
        }
    }
}

function Read-LastJsonLine {
    param([string]$Path)
    if (-not $Path) {
        return $null
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        $line = Get-Content -LiteralPath $Path -Tail 1 -Encoding UTF8
        if (-not $line) {
            return $null
        }
        return $line | ConvertFrom-Json
    } catch {
        return [ordered]@{
            error = $_.Exception.Message
            path = $Path
        }
    }
}

function Get-FileProbe {
    param([string]$Path)
    if (-not $Path) {
        return [ordered]@{
            path = ""
            exists = $false
        }
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return [ordered]@{
            path = $Path
            exists = $false
        }
    }
    $item = Get-Item -LiteralPath $Path
    return [ordered]@{
        path = $item.FullName
        exists = $true
        length = [int64]$item.Length
        lastWriteTime = $item.LastWriteTime.ToString("o")
    }
}

function Get-SessionRecords {
    $dirs = @(
        (Join-Path $env:USERPROFILE ".agentcli\sessions"),
        (Join-Path $env:TEMP "agentcli_sessions")
    )
    $records = @()
    foreach ($dir in $dirs) {
        if (-not (Test-Path -LiteralPath $dir)) {
            continue
        }
        foreach ($file in Get-ChildItem -LiteralPath $dir -Filter "session_*.json" -File -ErrorAction SilentlyContinue) {
            $data = Read-JsonFile -Path $file.FullName
            if ($null -eq $data) {
                continue
            }
            $records += [ordered]@{
                path = $file.FullName
                lastWriteTime = $file.LastWriteTime.ToString("o")
                childPid = [int]($data.child_pid)
                parentPid = [int]($data.parent_pid)
                childCreateTime = $data.child_create_time
                parentCreateTime = $data.parent_create_time
                createdAt = $data.created_at
            }
        }
    }
    return @($records)
}

function Get-ProcessTreeSnapshot {
    param(
        [array]$Sessions,
        [switch]$IncludeCommandLine
    )
    try {
        $all = @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId,ParentProcessId,Name,CreationDate,HandleCount,ThreadCount,WorkingSetSize,VirtualSize,CommandLine)
    } catch {
        return @([ordered]@{
            error = $_.Exception.Message
            source = "Get-CimInstance Win32_Process"
        })
    }
    $byParent = @{}
    foreach ($proc in $all) {
        $parent = [int]$proc.ParentProcessId
        if (-not $byParent.ContainsKey($parent)) {
            $byParent[$parent] = New-Object System.Collections.Generic.List[object]
        }
        $byParent[$parent].Add($proc)
    }

    $roots = New-Object System.Collections.Generic.HashSet[int]
    foreach ($session in $Sessions) {
        if ($session.parentPid) {
            [void]$roots.Add([int]$session.parentPid)
        }
        if ($session.childPid) {
            [void]$roots.Add([int]$session.childPid)
        }
    }

    $seen = New-Object System.Collections.Generic.HashSet[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    foreach ($root in $roots) {
        $queue.Enqueue([int]$root)
    }

    while ($queue.Count -gt 0) {
        $currentPid = $queue.Dequeue()
        if (-not $seen.Add($currentPid)) {
            continue
        }
        if ($byParent.ContainsKey($currentPid)) {
            foreach ($child in $byParent[$currentPid]) {
                $queue.Enqueue([int]$child.ProcessId)
            }
        }
    }

    $items = @()
    foreach ($proc in $all) {
        if (-not $seen.Contains([int]$proc.ProcessId)) {
            continue
        }
        $creationDate = $null
        if ($proc.CreationDate) {
            try {
                if ($proc.CreationDate -is [datetime]) {
                    $creationDate = $proc.CreationDate.ToString("o")
                } else {
                    $creationDate = ([Management.ManagementDateTimeConverter]::ToDateTime([string]$proc.CreationDate)).ToString("o")
                }
            } catch {
                $creationDate = [string]$proc.CreationDate
            }
        }
        $item = [ordered]@{
            pid = [int]$proc.ProcessId
            parentPid = [int]$proc.ParentProcessId
            name = [string]$proc.Name
            creationDate = $creationDate
            handleCount = [int]($proc.HandleCount)
            threadCount = [int]($proc.ThreadCount)
            workingSetBytes = [int64]($proc.WorkingSetSize)
            virtualSizeBytes = [int64]($proc.VirtualSize)
        }
        if ($IncludeCommandLine) {
            $item.commandLine = [string]$proc.CommandLine
        }
        $items += $item
    }
    return @($items | Sort-Object parentPid,pid)
}

function Get-SystemCounters {
    $counterPaths = @(
        "\Memory\Pool Nonpaged Bytes",
        "\Memory\Pool Paged Bytes",
        "\Process(_Total)\Handle Count"
    )
    try {
        $samples = Get-Counter $counterPaths | Select-Object -ExpandProperty CounterSamples
        $result = [ordered]@{}
        foreach ($sample in $samples) {
            $key = ($sample.Path -replace "^.*\\", "") -replace " ", "_"
            $result[$key] = [double]$sample.CookedValue
        }
        return $result
    } catch {
        return [ordered]@{ error = $_.Exception.Message }
    }
}

function Get-TopHandleProcesses {
    param([int]$Count)
    try {
        return @(Get-Process |
            Sort-Object HandleCount -Descending |
            Select-Object -First $Count |
            ForEach-Object {
                [ordered]@{
                    pid = [int]$_.Id
                    name = [string]$_.ProcessName
                    handleCount = [int]$_.HandleCount
                    workingSetBytes = [int64]$_.WorkingSet64
                    cpu = if ($null -eq $_.CPU) { $null } else { [double]$_.CPU }
                    startTime = try { $_.StartTime.ToString("o") } catch { $null }
                }
            })
    } catch {
        return @([ordered]@{ error = $_.Exception.Message })
    }
}

function Get-WindowsDiagnosticEvents {
    param(
        [datetime]$Since,
        [datetime]$Until
    )
    $events = @()
    $systemIds = @(26, 100, 1074, 109, 577, 13, 7031, 7034, 7040, 7045)
    $systemPattern = 'cmd\.exe|conhost\.exe|explorer\.exe|Application Error|0xc0000142|virtual memory|resource|handle|memory'
    $appPattern = 'cmd\.exe|conhost\.exe|explorer\.exe|WerFault|LiveKernelEvent|0xc0000142|xTend|darkFlash'
    try {
        $systemEvents = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$Since; EndTime=$Until} -ErrorAction SilentlyContinue
        $systemEvents = $systemEvents | Where-Object { $_.Id -in $systemIds -or $_.Message -match $systemPattern } | Select-Object -First 40
        foreach ($event in @($systemEvents)) {
            $events += [ordered]@{
                log = 'System';
                timeCreated = $event.TimeCreated.ToString("o");
                id = [int]$event.Id;
                level = [string]$event.LevelDisplayName;
                provider = [string]$event.ProviderName;
                message = ([string]$event.Message -replace '\s+', ' ');
            }
        }
    } catch {
        $events += [ordered]@{ log = 'System'; error = $_.Exception.Message }
    }

    try {
        $appEvents = Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$Since; EndTime=$Until} -ErrorAction SilentlyContinue
        $appEvents = $appEvents | Where-Object { $_.ProviderName -match 'Application Error|Windows Error Reporting|Application Hang' -or $_.Message -match $appPattern } | Select-Object -First 40
        foreach ($event in @($appEvents)) {
            $events += [ordered]@{
                log = 'Application';
                timeCreated = $event.TimeCreated.ToString("o");
                id = [int]$event.Id;
                level = [string]$event.LevelDisplayName;
                provider = [string]$event.ProviderName;
                message = ([string]$event.Message -replace '\s+', ' ');
            }
        }
    } catch {
        $events += [ordered]@{ log = 'Application'; error = $_.Exception.Message }
    }

    try {
        $resourceLog = 'Microsoft-Windows-Resource-Exhaustion-Detector/Operational'
        $resourceEvents = Get-WinEvent -FilterHashtable @{LogName=$resourceLog; StartTime=$Since; EndTime=$Until} -ErrorAction SilentlyContinue |
            Select-Object -First 20
        foreach ($event in @($resourceEvents)) {
            $events += [ordered]@{
                log = $resourceLog;
                timeCreated = $event.TimeCreated.ToString("o");
                id = [int]$event.Id;
                level = [string]$event.LevelDisplayName;
                provider = [string]$event.ProviderName;
                message = ([string]$event.Message -replace '\s+', ' ');
            }
        }
    } catch {
        $events += [ordered]@{ log = 'Microsoft-Windows-Resource-Exhaustion-Detector/Operational'; error = $_.Exception.Message }
    }

    return @($events)
}

function Get-ResourcePressure {
    param(
        [object]$SystemCounters,
        [array]$TopHandleProcesses
    )
    $handleCount = $null
    try {
        if ($SystemCounters -and $null -ne $SystemCounters.handle_count) {
            $handleCount = [int64]$SystemCounters.handle_count
        }
    } catch {
        $handleCount = $null
    }

    $top = $null
    foreach ($proc in @($TopHandleProcesses)) {
        if ($proc -and $null -ne $proc.handleCount) {
            $top = $proc
            break
        }
    }

    $topHandles = $null
    try {
        if ($top) {
            $topHandles = [int64]$top.handleCount
        }
    } catch {
        $topHandles = $null
    }

    $reasons = @()
    if ($null -ne $handleCount -and $SystemHandleLimit -gt 0 -and $handleCount -ge $SystemHandleLimit) {
        $reasons += "system_handles>=$SystemHandleLimit"
    }
    if ($null -ne $topHandles -and $TopProcessHandleLimit -gt 0 -and $topHandles -ge $TopProcessHandleLimit) {
        $reasons += "top_process_handles>=$TopProcessHandleLimit"
    }

    return [ordered]@{
        exceeded = ($reasons.Count -gt 0)
        reasons = @($reasons)
        systemHandleCount = $handleCount
        systemHandleLimit = $SystemHandleLimit
        topProcess = if ($top) {
            [ordered]@{
                pid = [int]$top.pid
                name = [string]$top.name
                handleCount = $topHandles
                handleLimit = $TopProcessHandleLimit
            }
        } else {
            $null
        }
    }
}

function Request-AgentCliStopForResourcePressure {
    param(
        [string]$RunDir,
        [object]$Pressure
    )
    if (-not $RunDir) {
        return $false
    }
    $stopPath = Join-Path $RunDir "STOP"
    if (Test-Path -LiteralPath $stopPath) {
        return $false
    }
    try {
        $reasonText = "windows_resource_pressure`n" + ($Pressure | ConvertTo-Json -Depth 6 -Compress)
        Set-Content -LiteralPath $stopPath -Value $reasonText -Encoding UTF8
        return $true
    } catch {
        return $false
    }
}

function Get-LogHandles {
    param(
        [array]$Processes,
        [string]$RepoRoot
    )
    $handleCommand = Get-Command handle.exe -ErrorAction SilentlyContinue
    if (-not $handleCommand) {
        return @([ordered]@{ error = "handle.exe not found in PATH" })
    }
    $matches = @()
    $oldLocation = Get-Location
    try {
        Set-Location -LiteralPath $env:TEMP
        foreach ($proc in $Processes) {
            if ($null -eq $proc.pid) {
                continue
            }
            $targetPid = [int]$proc.pid
            if ($targetPid -le 0) {
                continue
            }
            foreach ($pattern in @(".AgentCLI", "error.log", "run.log", "events.jsonl")) {
                try {
                    $output = & handle.exe -accepteula -nobanner -p $targetPid $pattern 2>&1
                    foreach ($line in @($output)) {
                        $text = [string]$line
                        if (-not $text.Trim()) {
                            continue
                        }
                        if ($text -like "*$RepoRoot*") {
                            $matches += [ordered]@{
                                pid = $targetPid
                                process = [string]$proc.name
                                pattern = $pattern
                                line = $text
                            }
                        }
                    }
                } catch {
                    $matches += [ordered]@{
                        pid = $targetPid
                        process = [string]$proc.name
                        pattern = $pattern
                        error = $_.Exception.Message
                    }
                }
            }
        }
    } finally {
        Set-Location -LiteralPath $oldLocation
    }
    return @($matches)
}

$repoRootResolved = Resolve-RepoRoot -Value $RepoRoot
$explicitRunDir = [bool]$RunDir
$runDirResolved = Resolve-LatestRunDir -RepoRoot $repoRootResolved -Value $RunDir
$diagnosticsDir = if ($runDirResolved) {
    Join-Path $runDirResolved "diagnostics"
} else {
    Join-Path $repoRootResolved ".AgentCLI\diagnostics"
}
New-Item -ItemType Directory -Force -Path $diagnosticsDir | Out-Null

if (-not $OutputPath) {
    if ($runDirResolved) {
        $OutputPath = Join-Path $diagnosticsDir "windows-handle-diagnostics.jsonl"
    } else {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $OutputPath = Join-Path $diagnosticsDir "windows-handle-diagnostics-$stamp.jsonl"
    }
}

$outputParent = Split-Path -Parent $OutputPath
if ($outputParent) {
    New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
}

$startedAt = Get-Date
$sampleIndex = 0
$durationSeconds = if ($DurationMinutes -gt 0) { $DurationMinutes * 60 } else { 0 }

Write-Host "Writing diagnostics to $OutputPath"
Write-Host "RepoRoot=$repoRootResolved"
if ($runDirResolved) {
    Write-Host "RunDir=$runDirResolved"
} else {
    Write-Host "RunDir=(none yet; will auto-detect latest .AgentCLI\agent_runs entry)"
}
Write-Host "Press Ctrl+C to stop when DurationMinutes is 0."

while ($true) {
    $now = Get-Date
    if ($durationSeconds -gt 0 -and (($now - $startedAt).TotalSeconds -ge $durationSeconds)) {
        break
    }

    $sessions = Get-SessionRecords
    $processes = Get-ProcessTreeSnapshot -Sessions $sessions -IncludeCommandLine:$IncludeCommandLine
    if (-not $explicitRunDir) {
        $latestRunDir = Resolve-LatestRunDir -RepoRoot $repoRootResolved -Value ""
        if ($latestRunDir) {
            $runDirResolved = $latestRunDir
        }
    }
    $metricsPath = if ($runDirResolved) { Join-Path $runDirResolved "metrics.jsonl" } else { "" }
    $statePath = if ($runDirResolved) { Join-Path $runDirResolved "STATE.json" } else { "" }
    $logDir = if ($runDirResolved) { Join-Path $runDirResolved "logs" } else { "" }
    $runLogPath = if ($logDir) { Join-Path $logDir "run.log" } else { "" }
    $errorLogPath = if ($logDir) { Join-Path $logDir "error.log" } else { "" }
    $eventsPath = if ($logDir) { Join-Path $logDir "events.jsonl" } else { "" }

    $systemCounters = Get-SystemCounters
    $topHandleProcesses = Get-TopHandleProcesses -Count $TopProcessCount
    $resourcePressure = Get-ResourcePressure -SystemCounters $systemCounters -TopHandleProcesses $topHandleProcesses
    $stopRequestedByResourcePressure = $false
    if ($StopOnResourcePressure -and $resourcePressure.exceeded) {
        $stopRequestedByResourcePressure = Request-AgentCliStopForResourcePressure -RunDir $runDirResolved -Pressure $resourcePressure
    }

    $sample = [ordered]@{
        schema = 1
        sample = $sampleIndex
        ts = $now.ToString("o")
        repoRoot = $repoRootResolved
        runDir = $runDirResolved
        processCount = @($processes).Count
        latestMetric = Read-LastJsonLine -Path $metricsPath
        state = Read-JsonFile -Path $statePath
        files = [ordered]@{
            metrics = Get-FileProbe -Path $metricsPath
            state = Get-FileProbe -Path $statePath
            runLog = Get-FileProbe -Path $runLogPath
            errorLog = Get-FileProbe -Path $errorLogPath
            events = Get-FileProbe -Path $eventsPath
        }
        sessions = $sessions
        processTree = $processes
        systemCounters = $systemCounters
        topHandleProcesses = $topHandleProcesses
        resourcePressure = $resourcePressure
        stopRequestedByResourcePressure = $stopRequestedByResourcePressure
    }

    if ($IncludeLogHandles -and $LogHandleEvery -gt 0 -and ($sampleIndex % $LogHandleEvery -eq 0)) {
        $sample.logHandles = Get-LogHandles -Processes $processes -RepoRoot $repoRootResolved
    }
    if ($IncludeWindowsEvents -and $WindowsEventEvery -gt 0 -and ($sampleIndex % $WindowsEventEvery -eq 0)) {
        $eventSince = $now.AddMinutes(-1 * [Math]::Max(1, $WindowsEventLookbackMinutes))
        $sample.windowsEvents = Get-WindowsDiagnosticEvents -Since $eventSince -Until $now
    }

    $json = $sample | ConvertTo-Json -Depth 12 -Compress
    Add-Content -LiteralPath $OutputPath -Value $json -Encoding UTF8
    $latestEvent = ""
    if ($sample.latestMetric -and $sample.latestMetric.event) {
        $latestEvent = [string]$sample.latestMetric.event
    }
    $pressureText = ""
    if ($resourcePressure.exceeded) {
        $pressureText = " resourcePressure=" + (($resourcePressure.reasons -join ",") -replace "\s+", "_")
    }
    Write-Host ("[{0}] sample={1} processes={2} latestEvent={3}{4} output={5}" -f $now.ToString("HH:mm:ss"), $sampleIndex, @($processes).Count, $latestEvent, $pressureText, $OutputPath)

    $sampleIndex += 1
    Start-Sleep -Seconds ([Math]::Max(1, $IntervalSeconds))
}
