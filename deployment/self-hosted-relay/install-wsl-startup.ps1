param(
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$Distribution = 'Ubuntu',
    [string]$WindowsUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [switch]$LogonOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
$arguments = "--distribution $Distribution --user sai --exec /usr/bin/sleep infinity"
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$name = 'SAI HPC WSL runner'
if ($LogonOnly) {
    $powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $startup = '"' + $powershell + '" -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath ''' +
        $wsl + ''' -ArgumentList ''' + $arguments + ''' -WindowStyle Hidden"'
    New-Item -Path $runKey -Force | Out-Null
    $old = (Get-ItemProperty $runKey -Name $name -ErrorAction SilentlyContinue).$name
    if ($old -and -not $old.Contains($arguments)) { throw 'Unrelated login startup entry exists.' }
    New-ItemProperty -Path $runKey -Name $name -Value $startup -PropertyType String -Force | Out-Null
    $running = @(Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($arguments)
    })
    if (-not $running.Count) {
        $process = Start-Process -FilePath $wsl -ArgumentList $arguments -WindowStyle Hidden -PassThru
        Write-Output "Started hidden WSL keepalive process $($process.Id)."
    }
    Write-Output 'Windows login startup configured. Unattended boot still requires the Administrator installation.'
    return
}
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an Administrator PowerShell window as the Windows user who owns the WSL distribution.'
}
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne 'Start Ubuntu WSL for the SAI build relay; Linux systemd manages the runner.') {
    throw 'Refusing to replace an unrelated scheduled task.'
}
$action = New-ScheduledTaskAction -Execute $wsl -Argument $arguments
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
