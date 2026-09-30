<#
.SYNOPSIS
    The SignalBench Telegram bot (spec 05), for Task Scheduler.

.DESCRIPTION
    Runs `uv run --frozen signalbench bot run` in -RepoRoot (the live-v1 worktree) and appends
    its output, line by line as it comes, to <RepoRoot>\logs\bot-<yyyy-MM>.log (git-ignored).
    When the bot exits with an error, a toast shows its last line and the bot is started again
    after -RestartSeconds (Task Scheduler's restart-on-failure is the second line of defence).
    A clean exit (0) ends the script.

    The log, toast, and .env loading are shared (lib\SignalBench.ps1).
    Needs Windows PowerShell 5.1 (powershell.exe) for the toast.

.PARAMETER RepoRoot
    The checkout to run: the live-v1 worktree for the scheduled task. Default: this script's repo.

.PARAMETER EnvFile
    A `.env` file of KEY=value lines. Default: the main checkout's `.env`.

.PARAMETER Once
    Do not restart after an error (for checking the script).

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = '',
    [int]$RestartSeconds = 60,
    [switch]$Once,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$script:log = Join-Path $env:TEMP 'signalbench-live-bot.log'  # until RepoRoot\logs exists
. (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')

$exitCode = 1
try {
    $RepoRoot = Start-SignalBenchRun -RepoRoot $RepoRoot -Name 'bot' -EnvFile $EnvFile
    while ($true) {
        Write-Log @('> signalbench bot run')
        $last = ''
        $ErrorActionPreference = 'Continue'  # native stderr is log output, not a script error
        & $script:uv run --frozen signalbench bot run 2>&1 | ForEach-Object {
            $line = "$_"
            if ($line.Trim()) { $last = $line }
            Write-Log @($line)
        }
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        Write-Log @("bot exited (exit $code)")
        $exitCode = $code
        if ($code -eq 0) { break }
        $why = if ($last) { $last -replace '^ERROR:\s*', '' } else { "exit code $code" }
        Show-Toast 'SignalBench bot stopped' $why
        if ($Once) { break }
        Start-Sleep -Seconds $RestartSeconds
    }
}
catch {
    $exitCode = 1
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench bot stopped' "$_"
}
exit $exitCode
