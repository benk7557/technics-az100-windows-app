<#
ONE-TIME ELEVATED SETUP for Movie mode. Run once; approve the single Windows prompt. After that the tray app never needs one.

What this does (and only this):
  1. Installs MovieModeHelper.ps1 into C:\Program Files\TechnicsAZ100Tool, with its data (the chosen earbuds, saved music settings,
     backups, a small log) in the data subfolder. Program Files is used because only administrators can create or change anything
     there, so no other account can plant or swap a file the elevated helper later reads or writes. Users can still read it.
  2. Backs up the driver's current settings for the Technics earbuds there (a .reg file, so everything can be put back).
  3. Registers two scheduled tasks that run the helper elevated for YOU only, with no triggers and no arguments:
       TechnicsAZ100-MovieMode-Movie   and   TechnicsAZ100-MovieMode-Music
Undo everything with remove_movie_helper.ps1.
The earbuds are found by name (EAH-AZ100) among the paired devices; pass -Address XX:XX:XX:XX:XX:XX if that finds none or several.
#>
param([string]$Address)
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'This setup must run elevated (it was started without administrator rights).'
}
$src      = Join-Path $PSScriptRoot 'MovieModeHelper.ps1'
$progDir  = 'C:\Program Files\TechnicsAZ100Tool'
$dataDir  = Join-Path $progDir 'data'
$helper   = Join-Path $progDir 'MovieModeHelper.ps1'
if (-not (Test-Path -LiteralPath $src)) { throw "missing $src" }

# 0. which earbuds
if (-not $Address) {
  $found = @(Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices' | Where-Object {
    $n = (Get-ItemProperty -LiteralPath $_.PSPath -Name Name -ErrorAction SilentlyContinue).Name
    $n -and $_.PSChildName.Length -eq 12 -and [Text.Encoding]::UTF8.GetString($n) -like '*EAH-AZ100*' })
  if ($found.Count -ne 1) { throw "found $($found.Count) paired AZ100 earbuds; run again with -Address XX:XX:XX:XX:XX:XX" }
  $Address = ($found[0].PSChildName -split '(..)' -ne '') -join ':'
}
if ($Address -notmatch '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$') { throw "not a Bluetooth address: $Address" }
$Address = $Address.ToUpper()
$dev = '0000' + ($Address -replace ':', '').ToLower()
Write-Host "Earbuds: $Address"

# 1. install the helper and its data folder (both inherit Program Files' rules: administrators and SYSTEM write, users read)
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
Copy-Item -LiteralPath $src -Destination $helper -Force
Set-Content -LiteralPath (Join-Path $dataDir 'device.txt') -Value $Address -Encoding ASCII
Write-Host "Installed $helper"

# 2. backup of the driver's current settings for this device
$backup = Join-Path $dataDir ("next_backup_{0:yyyyMMdd_HHmmss}.reg" -f (Get-Date))
& reg.exe export "HKLM\SYSTEM\CurrentControlSet\Services\AltA2DP\Parameters\Devices\Next\$dev" $backup /y | Out-Null
if (-not (Test-Path -LiteralPath $backup)) { throw 'could not back up the current settings; stopping before registering anything' }
Write-Host "Backed up current driver settings to $backup"

# 3. the two fixed tasks, elevated for this user, no triggers
$user = $id.Name
foreach ($p in 'movie', 'music') {
  $name = 'TechnicsAZ100-MovieMode-' + (Get-Culture).TextInfo.ToTitleCase($p)
  $arg  = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Profile {1}' -f $helper, $p
  $act  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
  $prin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
  $set  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable:$false
  Register-ScheduledTask -TaskName $name -Action $act -Principal $prin -Settings $set -Force -Description 'Movie mode helper for the Technics AZ100 tray tool. Starts only on demand.' | Out-Null
  Write-Host "Registered task $name (runs as $user, elevated, no triggers)"
}
if (Test-Path -LiteralPath 'C:\ProgramData\TechnicsAZ100Tool') {
  Write-Host 'Note: C:\ProgramData\TechnicsAZ100Tool is from an older version and is no longer used. Delete it once you no longer need its backups.'
}
Write-Host ''
Write-Host 'Done. Nothing has been changed yet. The tray app can now start these tasks without any prompt.'
Start-Sleep -Seconds 4
