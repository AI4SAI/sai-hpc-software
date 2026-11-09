param(
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$Distribution = 'Ubuntu',
    [string]$WindowsUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an Administrator PowerShell window as the Windows user who owns the WSL distribution.'
}
$name = 'SAI HPC WSL runner'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne 'Start Ubuntu WSL for the SAI build relay; Linux systemd manages the runner.') {
    throw 'Refusing to replace an unrelated scheduled task.'
}
$action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\wsl.exe" `
    -Argument "--distribution $Distribution --user sai --exec /usr/bin/sleep infinity"
$triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $WindowsUser))
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $WindowsUser -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Description 'Start Ubuntu WSL for the SAI build relay; Linux systemd manages the runner.' `
    -Action $action -Trigger $triggers -Principal $taskPrincipal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $name
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State, Principal, Triggers, Settings | Format-List
Write-Output 'Task installed and started. Verify it stays Running and that the GitHub runner is online.'
