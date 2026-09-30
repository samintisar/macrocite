<#
.SYNOPSIS
    Shared by the scheduled SignalBench scripts (paper_nightly.ps1, live_scan.ps1, live_bot.ps1):
    the log, the Windows toast, the .env loading, and `uv run --frozen signalbench`.

.DESCRIPTION
    Dot-source it from a script: . (Join-Path $PSScriptRoot 'lib\SignalBench.ps1')
    The script sets $script:log to a fallback log file first, and may have a -NoToast switch.

    The scheduled tasks run a git worktree checked out at a tag, so the code that runs changes
    only by a deliberate checkout. `.env` is not tracked, so the worktree has none: the
    variables in -EnvFile (the main checkout's `.env` by default) are loaded into the process.
    A variable already set in the environment wins, as it does for pydantic-settings. Values are
    never printed or logged.

    Needs Windows PowerShell 5.1 (powershell.exe): the toast uses the built-in WinRT notification
    API, which PowerShell 7 cannot load. No modules are installed.
#>

function Write-Log([string[]]$Lines) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    $text = @($Lines | ForEach-Object { "$stamp  $_" })
    try {
        Add-Content -Path $script:log -Encoding UTF8 -Value $text
    }
    catch {
        $fallback = Join-Path $env:TEMP 'signalbench-scripts.log'
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

function Start-SignalBenchRun([string]$RepoRoot, [string]$Name, [string]$EnvFile) {
    # Log to <RepoRoot>\logs\<Name>-<yyyy-MM>.log (git-ignored), run from RepoRoot, load the env
    # file, and find uv. Returns the resolved RepoRoot.
    $root = (Resolve-Path -LiteralPath $RepoRoot).Path
    $logDir = Join-Path $root 'logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $script:log = Join-Path $logDir ('{0}-{1:yyyy-MM}.log' -f $Name, (Get-Date))
    Set-Location -LiteralPath $root
    $env:PYTHONUTF8 = '1'
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    if (-not $EnvFile) {
        $EnvFile = Get-MainEnvFile $root
    }
    Write-Log @("repo: $root")
    Import-EnvFile $EnvFile
    $script:uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $script:uv) {
        throw 'uv is not on PATH'
    }
    $root
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

function Get-ErrorLine([string[]]$Output, [int]$Code) {
    # The first "ERROR: ..." line without its prefix, else the last line printed, else the code.
    $line = $Output | Where-Object { $_ -like 'ERROR:*' } | Select-Object -First 1
    if (-not $line) {
        $line = $Output | Where-Object { $_.Trim() } | Select-Object -Last 1
    }
    if (-not $line) {
        $line = "exit code $Code"
    }
    $line -replace '^ERROR:\s*', ''
}
