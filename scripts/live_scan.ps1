<#
.SYNOPSIS
    The evening SignalBench scan (spec 05), for Task Scheduler.

.DESCRIPTION
    Runs `uv run --frozen signalbench scan` in -RepoRoot (the live-v1 worktree) and appends
    everything to <RepoRoot>\logs\scan-<yyyy-MM>.log (git-ignored).

    - Before the scan, `scan status --stale-after-days` (exit 3) shows a toast when the last ok
      scan is more than -StaleAfterDays days old. The check never stops the scan.
    - A scan that exits non-zero shows a toast with the first line of its error. The scan has
      already sent its warning to Telegram when it could; the toast shows even when Telegram
      is unreachable.
    - A `BOT STALE:` line (the bot's heartbeat is over an hour old) shows a toast too.

    The log, toast, and .env loading are shared (lib\SignalBench.ps1).
    Needs Windows PowerShell 5.1 (powershell.exe) for the toast.

.PARAMETER RepoRoot
    The checkout to run: the live-v1 worktree for the scheduled task. Default: this script's repo.

.PARAMETER EnvFile
    A `.env` file of KEY=value lines. Default: the main checkout's `.env`.

.PARAMETER CheckOnly
    Run the stale check and stop, without scanning (with -StaleAfterDays 0, to see the toast).

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = '',
    [int]$StaleAfterDays = 3,
    [switch]$CheckOnly,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$script:log = Join-Path $env:TEMP 'signalbench-live-scan.log'  # until RepoRoot\logs exists
. (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')

$exitCode = 1
try {
    $RepoRoot = Start-SignalBenchRun -RepoRoot $RepoRoot -Name 'scan' -EnvFile $EnvFile
    try {
        $status = Invoke-SignalBench @('scan', 'status', '--stale-after-days', "$StaleAfterDays")
        if ($status.Code -eq 3) {
            $stale = $status.Output | Where-Object { $_ -like 'STALE:*' } | Select-Object -First 1
            Show-Toast 'SignalBench scans are stale' "$stale"
        }
    }
    catch {
        Write-Log @("stale check error (the scan goes on): $_")
    }
    if ($CheckOnly) {
        exit 0
    }
    $run = Invoke-SignalBench @('scan')
    $exitCode = $run.Code
    $bot = $run.Output | Where-Object { $_ -like 'BOT STALE:*' } | Select-Object -First 1
    if ($bot) {
        Show-Toast 'SignalBench bot is down' ($bot -replace '^BOT STALE:\s*', '')
    }
    if ($run.Code -ne 0) {
        Show-Toast 'SignalBench scan failed' (Get-ErrorLine $run.Output $run.Code)
    }
}
catch {
    $exitCode = 1
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench scan failed' "$_"
}
exit $exitCode
