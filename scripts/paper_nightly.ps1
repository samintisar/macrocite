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
    `git -C <worktree> checkout <new tag>`. The log, toast, and .env loading are shared with
    the live scripts in lib\SignalBench.ps1: the variables in -EnvFile are loaded into this
    process first, a variable already set in the environment wins, and values are never logged.

    Needs Windows PowerShell 5.1 (powershell.exe) for the toast. No modules are installed.

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
. (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')

$exitCode = 1
try {
    $RepoRoot = Start-SignalBenchRun -RepoRoot $RepoRoot -Name 'paper' -EnvFile $EnvFile
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
        Show-Toast 'SignalBench paper run failed' (Get-ErrorLine $run.Output $run.Code)
    }
}
catch {
    $exitCode = 1
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench paper run failed' "$_"
}
exit $exitCode
