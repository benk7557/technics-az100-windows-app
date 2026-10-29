<#
Removes everything setup_movie_helper.ps1 created. Run elevated. Your driver settings are NOT changed by this script; if Movie mode
left the driver on AAC, switch the codec back in the driver window, or double-click the newest next_backup_*.reg in
C:\ProgramData\TechnicsAZ100Tool (kept) to put the saved settings back, then reconnect the earbuds.
#>
$ErrorActionPreference = 'Continue'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this elevated.' }
foreach ($n in 'TechnicsAZ100-MovieMode-Movie', 'TechnicsAZ100-MovieMode-Music') {
  if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $n -Confirm:$false; Write-Host "Removed task $n" }
}
if (Test-Path 'C:\Program Files\TechnicsAZ100Tool') { Remove-Item -Recurse -Force 'C:\Program Files\TechnicsAZ100Tool'; Write-Host 'Removed C:\Program Files\TechnicsAZ100Tool' }
Write-Host 'Kept C:\ProgramData\TechnicsAZ100Tool (your backups and saved music settings). Delete it by hand if you want.'
Start-Sleep -Seconds 3
