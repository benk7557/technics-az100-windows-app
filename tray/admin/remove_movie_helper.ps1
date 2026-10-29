<#
Removes the scheduled tasks and the helper that setup_movie_helper.ps1 installed. Run elevated. Your driver settings are NOT changed
by this script; if Movie mode left the driver on SBC, switch the codec back in the driver window, or double-click the newest
next_backup_*.reg in C:\Program Files\TechnicsAZ100Tool\data (kept) to put the saved settings back, then reconnect the earbuds.
#>
$ErrorActionPreference = 'Continue'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this elevated.' }
foreach ($n in 'TechnicsAZ100-MovieMode-Movie', 'TechnicsAZ100-MovieMode-Music') {
  if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $n -Confirm:$false; Write-Host "Removed task $n" }
}
$helper = 'C:\Program Files\TechnicsAZ100Tool\MovieModeHelper.ps1'
if (Test-Path -LiteralPath $helper) { Remove-Item -LiteralPath $helper -Force; Write-Host "Removed $helper" }
Write-Host 'Kept C:\Program Files\TechnicsAZ100Tool\data (your backups and saved music settings). Delete the folder by hand if you want.'
Start-Sleep -Seconds 3
