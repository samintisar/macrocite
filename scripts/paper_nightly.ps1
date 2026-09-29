<#
.SYNOPSIS
    The nightly SignalBench paper run (spec 07), for Task Scheduler.

.DESCRIPTION
    Runs `uv run signalbench paper run` from the repo root and appends everything to
    logs\paper-<yyyy-MM>.log (git-ignored). Shows a Windows toast when this run fails, and
    another when no paper run has succeeded for more than -StaleAfterDays days (checked
    before this run, so it catches a task that has not been running).

    Needs Windows PowerShell 5.1 (powershell.exe): the toast uses the built-in WinRT
    notification API, which PowerShell 7 cannot load. No modules are installed.

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [int]$StaleAfterDays = 3,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logDir = Join-Path $repo 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('paper-{0:yyyy-MM}.log' -f (Get-Date))
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Log([string[]]$Lines) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    Add-Content -Path $log -Encoding UTF8 -Value ($Lines | ForEach-Object { "$stamp  $_" })
}

function Show-Toast([string]$Title, [string]$Message) {
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

function Invoke-SignalBench([string[]]$Arguments) {
    # Native stderr must not stop the script: take every line, stdout and stderr, as text.
    $ErrorActionPreference = 'Continue'
    $output = @(& $script:uv run signalbench @Arguments 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    Write-Log (@("> signalbench $($Arguments -join ' ')  (exit $code)") + $output)
    [pscustomobject]@{ Code = $code; Output = $output }
}

try {
    $script:uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $script:uv) {
        throw 'uv is not on PATH'
    }
    $status = Invoke-SignalBench @('paper', 'status', '--stale-after-days', "$StaleAfterDays")
    if ($status.Code -eq 3) {
        $stale = $status.Output | Where-Object { $_ -like 'STALE:*' } | Select-Object -First 1
        Show-Toast 'SignalBench paper runs are stale' "$stale"
    }
    $run = Invoke-SignalBench @('paper', 'run')
    if ($run.Code -ne 0) {
        $line = $run.Output | Where-Object { $_ -like 'ERROR:*' } | Select-Object -First 1
        if (-not $line) {
            $line = $run.Output | Where-Object { $_.Trim() } | Select-Object -Last 1
        }
        if (-not $line) {
            $line = "exit code $($run.Code)"
        }
        Show-Toast 'SignalBench paper run failed' ($line -replace '^ERROR:\s*', '')
        exit 1
    }
    exit 0
}
catch {
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench paper run failed' "$_"
    exit 1
}
