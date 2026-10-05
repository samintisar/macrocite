<#
.SYNOPSIS
    The two Task Scheduler tasks of the live strategy (spec 05): prints the exact commands, and
    runs them only with -Register (the owner's go-ahead: a lasting change on this PC).

.DESCRIPTION
    - "SignalBench live bot": scripts\live_bot.ps1 at log-on, restarted every minute on
      failure, with no time limit.
    - "SignalBench live scan": scripts\live_scan.ps1 daily at the scan time, again two hours
      later, and again before the next open (the retries), plus at log-on with a 5-minute
      delay for catch-up.

    The scan time is the earliest local time of day that is 17:00 New York or later on every
    day of the 12 months from -From, taken from Windows' time-zone data. On a PC in British
    Columbia (which stops changing clocks in November 2026) that is 15:00: 18:00 New York in
    summer, 17:00 in winter. A scan before 16:15 New York would target the previous session and
    lose that night's entries. -At overrides it.

    The retries: the scan time + 2 hours, and the earliest local time that is 07:30 New York or
    later every day (05:30 in British Columbia: 08:30 New York in summer, 07:30 in winter), which
    is still before the 09:30 open. A retry of a target already scanned only sends rows left
    unsent; one after a failed run finishes it, exit alerts first.

    Both tasks run the worktree pinned to the live-v1 tag, as the owner, only while logged on
    (the toasts need the desktop).

.PARAMETER Worktree
    The live-v1 worktree the tasks run.

.PARAMETER EnvFile
    The main checkout's .env (the worktree has none).

.PARAMETER From
    The first day of the 12 months the scan time must cover. Default: today.

.PARAMETER At
    The scan time, HH:mm local, instead of the computed one.

.PARAMETER Register
    Run the printed commands. Without it, nothing is registered.
#>
param(
    [string]$Worktree = "$env:USERPROFILE\Documents\GitHub\macrocite-live",
    [string]$EnvFile = "$env:USERPROFILE\Documents\GitHub\macrocite\.env",
    [datetime]$From = (Get-Date),
    [string]$At = '',
    [switch]$Register
)

$ErrorActionPreference = 'Stop'

function Get-ScanTime([datetime]$Start, [double]$NewYorkHour = 17) {
    # The latest local time of day at which $NewYorkHour (17: 17:00) New York falls, over 366
    # days, rounded up to the minute. (Assumes it is the same local day, true west of New York.)
    $newYork = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
    $latest = [TimeSpan]::Zero
    for ($i = 0; $i -lt 366; $i++) {
        $five = [datetime]::SpecifyKind($Start.Date.AddDays($i).AddHours($NewYorkHour), 'Unspecified')
        $utc = [TimeZoneInfo]::ConvertTimeToUtc($five, $newYork)
        $local = [TimeZoneInfo]::ConvertTimeFromUtc($utc, [TimeZoneInfo]::Local)
        if ($local.TimeOfDay -gt $latest) { $latest = $local.TimeOfDay }
    }
    [TimeSpan]::FromMinutes([math]::Ceiling($latest.TotalMinutes))
}

$time = if ($At) { [TimeSpan]::Parse($At) } else { Get-ScanTime $From }
$clock = '{0:hh\:mm}' -f $time
$retry = $time.Add([TimeSpan]::FromHours(2))
if ($retry.TotalHours -ge 24) { throw "the scan time $clock + 2 hours passes midnight; pass an earlier -At" }
$retryClock = '{0:hh\:mm}' -f $retry
$morningClock = '{0:hh\:mm}' -f (Get-ScanTime $From 7.5)
$user = "$env:USERDOMAIN\$env:USERNAME"
$common = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File'
$scanArgs = "$common `"$Worktree\scripts\live_scan.ps1`" -RepoRoot `"$Worktree`" -EnvFile `"$EnvFile`""
$botArgs = "$common `"$Worktree\scripts\live_bot.ps1`" -RepoRoot `"$Worktree`" -EnvFile `"$EnvFile`""

$commands = @"
`$scanAction = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory '$Worktree' -Argument '$scanArgs'
`$scanDaily = New-ScheduledTaskTrigger -Daily -At '$clock'
`$scanRetry = New-ScheduledTaskTrigger -Daily -At '$retryClock'
`$scanMorning = New-ScheduledTaskTrigger -Daily -At '$morningClock'
`$scanLogOn = New-ScheduledTaskTrigger -AtLogOn -User '$user'
`$scanLogOn.Delay = 'PT5M'
`$scanSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName 'SignalBench live scan' -Action `$scanAction -Trigger @(`$scanDaily, `$scanRetry, `$scanMorning, `$scanLogOn) -Settings `$scanSettings -Description 'Evening scan of v2-none-cash (spec 05): the live-v1 worktree, scripts\live_scan.ps1'
`$botAction = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory '$Worktree' -Argument '$botArgs'
`$botLogOn = New-ScheduledTaskTrigger -AtLogOn -User '$user'
`$botSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'SignalBench live bot' -Action `$botAction -Trigger `$botLogOn -Settings `$botSettings -Description 'Telegram bot (spec 05): the live-v1 worktree, scripts\live_bot.ps1'
"@

Write-Output "Scan time: $clock local, the earliest time that is 17:00 New York or later every day from $('{0:yyyy-MM-dd}' -f $From) for 12 months ($([TimeZoneInfo]::Local.Id))."
Write-Output "Retries: $retryClock local (2 hours later) and $morningClock local (07:30 New York or later, before the open)."
Write-Output 'Commands:'
Write-Output $commands
if (-not $Register) {
    Write-Output 'Nothing registered. Rerun with -Register to run the commands above.'
    exit 0
}
Invoke-Expression $commands
Write-Output 'Registered. Check: Get-ScheduledTask -TaskName "SignalBench live *" | Get-ScheduledTaskInfo'
