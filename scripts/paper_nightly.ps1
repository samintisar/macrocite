<#
.SYNOPSIS
    The nightly SignalBench paper run (spec 07), for Task Scheduler.

.DESCRIPTION
    Runs `uv run --frozen signalbench paper run` in -RepoRoot and appends everything to
    <RepoRoot>\logs\paper-<yyyy-MM>.log (git-ignored). Shows a Windows toast when this run fails,
    and another when no paper run has succeeded for more than -StaleAfterDays days (checked
    before this run, so it catches a task that has not been running; it never stops the run).

    The scheduled task runs this script from a dedicated git worktree checked out at a tag
    (plan Task 16), so the code that steps the portfolios changes only by a deliberate
    `git -C <worktree> checkout <new tag>`. `.env` is not tracked, so the worktree has none:
    the variables in -EnvFile (the main checkout's `.env` by default) are loaded into this
    process first. A variable already set in the environment wins, as it does for
    pydantic-settings. Values are never printed or logged.

    Needs Windows PowerShell 5.1 (powershell.exe): the toast uses the built-in WinRT
    notification API, which PowerShell 7 cannot load. No modules are installed.

.PARAMETER RepoRoot
    The checkout to run: the paper worktree for the scheduled task. Default: this script's repo.

.PARAMETER EnvFile
    A `.env` file of KEY=value lines. Default: `.env` in the main checkout of -RepoRoot's
    repository (the folder that holds the shared .git directory).

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = '',
    [int]$StaleAfterDays = 3,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$script:log = Join-Path $env:TEMP 'signalbench-paper-nightly.log'  # until RepoRoot\logs exists

function Write-Log([string[]]$Lines) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    $text = @($Lines | ForEach-Object { "$stamp  $_" })
    try {
        Add-Content -Path $script:log -Encoding UTF8 -Value $text
    }
    catch {
        $fallback = Join-Path $env:TEMP 'signalbench-paper-nightly.log'
        Add-Content -Path $fallback -Encoding UTF8 -Value ($text + @("$stamp  (could not write $($script:log): $_)"))
    }
}

function Show-Toast([string]$Title, [string]$Message) {
    # Never throws: a toast that cannot be shown is logged, and the script goes on.
    try {
        if ($NoToast) {
            Write-Log @("toast (not shown): $Title | $Message")
            return
        }
        $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
        $title = [System.Security.SecurityElement]::Escape($Title)
        $body = [System.Security.SecurityElement]::Escape($Message)
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>$title</text><text>$body</text></binding></visual></toast>")
        $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
        # Windows PowerShell's own app id: toasts need a registered app, and this one always is.
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    }
    catch {
        try { Write-Log @("toast FAILED ($Title | $Message): $_") } catch { }
    }
}

function Get-MainEnvFile([string]$Root) {
    # The shared .git directory of a worktree lives in the main checkout.
    $common = & git -C $Root rev-parse --path-format=absolute --git-common-dir
    if ($LASTEXITCODE -ne 0 -or -not $common) {
        throw "git rev-parse --git-common-dir failed in $Root"
    }
    Join-Path (Split-Path -Parent ([System.IO.Path]::GetFullPath($common.Trim()))) '.env'
}

function Import-EnvFile([string]$Path) {
    # Simple KEY=value lines; blank lines and # comments are skipped; one pair of surrounding
    # quotes is removed. A variable already set in this process is kept. Values are never logged.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "env file not found: $Path"
    }
    $loaded = 0
    $kept = 0
    foreach ($raw in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).TrimStart() }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ([Environment]::GetEnvironmentVariable($key, 'Process')) {
            $kept++
            continue
        }
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
        $loaded++
    }
    Write-Log @("env: $loaded variables loaded from $Path, $kept already set in the environment kept")
}

function Invoke-SignalBench([string[]]$Arguments) {
    # Native stderr must not stop the script: take every line, stdout and stderr, as text.
    # --frozen: run the worktree's uv.lock as committed, never rewrite it.
    $ErrorActionPreference = 'Continue'
    $output = @(& $script:uv run --frozen signalbench @Arguments 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    Write-Log (@("> signalbench $($Arguments -join ' ')  (exit $code)") + $output)
    [pscustomobject]@{ Code = $code; Output = $output }
}

$exitCode = 1
try {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    $logDir = Join-Path $RepoRoot 'logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $script:log = Join-Path $logDir ('paper-{0:yyyy-MM}.log' -f (Get-Date))
    Set-Location -LiteralPath $RepoRoot
    $env:PYTHONUTF8 = '1'
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    if (-not $EnvFile) {
        $EnvFile = Get-MainEnvFile $RepoRoot
    }
    Write-Log @("repo: $RepoRoot")
    Import-EnvFile $EnvFile
    $script:uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $script:uv) {
        throw 'uv is not on PATH'
    }
    try {
        $status = Invoke-SignalBench @('paper', 'status', '--stale-after-days', "$StaleAfterDays")
        if ($status.Code -eq 3) {
            $stale = $status.Output | Where-Object { $_ -like 'STALE:*' } | Select-Object -First 1
            Show-Toast 'SignalBench paper runs are stale' "$stale"
        }
    }
    catch {
        Write-Log @("stale check error (the run goes on): $_")
    }
    $run = Invoke-SignalBench @('paper', 'run')
    $exitCode = $run.Code
    if ($run.Code -ne 0) {
        $line = $run.Output | Where-Object { $_ -like 'ERROR:*' } | Select-Object -First 1
        if (-not $line) {
            $line = $run.Output | Where-Object { $_.Trim() } | Select-Object -Last 1
        }
        if (-not $line) {
            $line = "exit code $($run.Code)"
        }
        Show-Toast 'SignalBench paper run failed' ($line -replace '^ERROR:\s*', '')
    }
}
catch {
    $exitCode = 1
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench paper run failed' "$_"
}
exit $exitCode
