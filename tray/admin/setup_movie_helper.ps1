<#
ONE-TIME ELEVATED SETUP for Movie mode. Run once; approve the single Windows prompt. After that the tray app never needs one.

What this does (and only this):
  1. Creates C:\ProgramData\TechnicsAZ100Tool for the saved music settings and a small log (administrators write, users read).
  2. Backs up the driver's current settings for the Technics earbuds there (a .reg file, so everything can be put back).
  3. Installs MovieModeHelper.ps1 into C:\Program Files\TechnicsAZ100Tool (only administrators can change it).
  4. Registers two scheduled tasks that run the helper elevated for YOU only, with no triggers and no arguments:
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
$dataDir  = 'C:\ProgramData\TechnicsAZ100Tool'
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

# 1. data folder first, before anything is written into it: administrators and SYSTEM write, users only read.
# Any user can create folders in ProgramData, so a folder that is already there must be an administrator's and not a link.
$adminSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
if (Test-Path -LiteralPath $dataDir) {
  if ((Get-Item -LiteralPath $dataDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "$dataDir is a link; delete it and run setup again" }
  $owner = (Get-Acl -LiteralPath $dataDir).GetOwner([Security.Principal.SecurityIdentifier]).Value
  if (@('S-1-5-18', 'S-1-5-32-544') -notcontains $owner) {
    throw "$dataDir already exists and is owned by $owner, not by an administrator. Check what is in it, delete it, and run setup again."
  }
  foreach ($f in Get-ChildItem -LiteralPath $dataDir -Force) {
    if ($f.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "$($f.FullName) is a link; delete it and run setup again" }
  }
} else {
  New-Item -ItemType Directory -Path $dataDir | Out-Null
}
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner($adminSid)
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in 'S-1-5-18', 'S-1-5-32-544') {
  $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
}
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')), 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
Set-Acl -LiteralPath $dataDir -AclObject $acl
foreach ($f in Get-ChildItem -LiteralPath $dataDir -Force) {     # files from an earlier setup: administrators' and inherited rules only
  & icacls.exe $f.FullName /setowner '*S-1-5-32-544' /C /Q | Out-Null
  & icacls.exe $f.FullName /reset /C /Q | Out-Null
}
Set-Content -LiteralPath (Join-Path $dataDir 'device.txt') -Value $Address -Encoding ASCII
Write-Host "Prepared $dataDir"

# 2. backup of the driver's current settings for this device
$backup = Join-Path $dataDir ("next_backup_{0:yyyyMMdd_HHmmss}.reg" -f (Get-Date))
& reg.exe export "HKLM\SYSTEM\CurrentControlSet\Services\AltA2DP\Parameters\Devices\Next\$dev" $backup /y | Out-Null
if (-not (Test-Path -LiteralPath $backup)) { throw 'could not back up the current settings; stopping before changing anything' }
Write-Host "Backed up current driver settings to $backup"

# 3. install the helper into a folder only administrators can write
New-Item -ItemType Directory -Force -Path $progDir | Out-Null
Copy-Item -LiteralPath $src -Destination $helper -Force
Write-Host "Installed $helper"

# 4. the two fixed tasks, elevated for this user, no triggers
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
Write-Host ''
Write-Host 'Done. Nothing has been changed yet. The tray app can now start these tasks without any prompt.'
Start-Sleep -Seconds 4
