<#
Movie mode helper for the Alternative A2DP Driver. Runs ELEVATED from a scheduled task; the tray app only ever starts one of two
fixed tasks (movie / music), so there is no argument, request file or other input an unprivileged process can influence.

What it may do (and nothing else):
  * movie : save your current "Next" settings once (if not already in movie state), then write the movie changes below.
  * music : write back exactly the settings it saved before.
It touches ONE key: HKLM\...\AltA2DP\Parameters\Devices\Next\<the Technics earbuds>, only the allowlisted value names, only DWORDs,
and refuses any codec / sampling rate / channel mode that the earbuds' reported Capability key does not contain.
It never reads or writes the License, the service, or any other device (e.g. the other paired earbuds).

-DryRun reads and validates and prints what it WOULD write; it writes nothing (works without elevation).
#>
param(
  [Parameter(Mandatory = $true)][ValidateSet('movie', 'music')][string]$Profile,
  [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$DataDir  = 'C:\ProgramData\TechnicsAZ100Tool'

# Everything below trusts DataDir, so first make sure no ordinary user could have planted or changed anything in it.
$AdminSids   = @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')  # SYSTEM, Administrators, TrustedInstaller
$WriteRights = [Security.AccessControl.FileSystemRights]'Write, Delete, DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership'
function Get-UntrustedReason($dir) {
  if (-not (Test-Path -LiteralPath $dir)) { return $null }      # a missing folder is reported further down
  $items = @(Get-Item -LiteralPath $dir -Force) + @(Get-ChildItem -LiteralPath $dir -Force)
  foreach ($item in $items) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return "$($item.FullName) is a link" }
    $acl = Get-Acl -LiteralPath $item.FullName
    if ($AdminSids -notcontains $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value) { return "$($item.FullName) is not owned by an administrator" }
    foreach ($r in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
      if ($r.AccessControlType -eq 'Allow' -and ($r.FileSystemRights -band $WriteRights) -and $AdminSids -notcontains $r.IdentityReference.Value) {
        return "$($item.FullName) can be changed by $($r.IdentityReference.Value)"
      }
    }
  }
  $null
}
$untrusted = Get-UntrustedReason $DataDir
if ($untrusted) {                                               # write nothing at all, not even the result file
  Write-Host "refused: $untrusted. Run setup_movie_helper.ps1 again."
  exit 1
}

# The earbuds chosen at setup (device.txt, administrators write, users read) and nothing else
$Addr     = (Get-Content -LiteralPath (Join-Path $DataDir 'device.txt') -TotalCount 1 -ErrorAction SilentlyContinue)
$Addr     = if ($Addr -match '^\s*([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\s*$') { $Addr.Trim() } else { $null }
$Dev      = if ($Addr) { '0000' + ($Addr -replace ':', '').ToLower() } else { $null }
$Base     = 'HKLM:\SYSTEM\CurrentControlSet\Services\AltA2DP\Parameters\Devices'
$NextKey  = "$Base\Next\$Dev"
$CapKey   = "$Base\Capability\$Dev"
$Snapshot = Join-Path $DataDir 'music_snapshot.json'
$Result   = Join-Path $DataDir 'last_result.json'
$LogFile  = Join-Path $DataDir 'helper.log'

# Allowlist: the only value names this script will ever write (DWORD). VolumeLevel and everything else are never touched.
$Allowed = @('Codec', 'SbcChannelMode', 'SbcSamplingFrequency', 'SbcAllocationMethod', 'SbcSubbands', 'SbcBlockLength',
             'SbcMinimumBitpool', 'SbcMaximumBitpool', 'AbrEnable', 'AacChannelMode', 'AacSamplingFrequency', 'AacBitrate',
             'AacPeakBitrate', 'LdacChannelMode', 'LdacSamplingFrequency', 'LdacSampleFormat', 'LdacEqmid')

# The movie profile: switch the codec to SBC and leave every other value as it is.
# Codec values are a bitmask in the driver's own scheme: SBC = 1, AAC = 2, LDAC = 4. Verified live on this machine:
# LDAC (4) -> SBC (1) lowered the driver-reported delay from 3500 to 1500 (about 350 ms to 150 ms). An AAC request (2) was
# refused by the driver, which rewrote the setting to SBC on its own; AAC is not offered by this driver install and is
# deliberately not part of any profile here.
$MovieChanges = [ordered]@{ Codec = 1 }

# Capability masks that bound what may be written (value name -> capability value name holding the allowed bits)
$MaskedBy = @{ Codec = 'Codec'; AacChannelMode = 'AacChannelMode'; AacSamplingFrequency = 'AacSamplingFrequency'
               LdacChannelMode = 'LdacChannelMode'; LdacSamplingFrequency = 'LdacSamplingFrequency' }

function Log($m) {
  if ($DryRun) { Write-Host "[dry-run] $m"; return }
  Add-Content -LiteralPath $LogFile -Value ("{0:s} {1}" -f (Get-Date), $m)
}
function Save-Result([bool]$ok, [string]$msg) {
  if ($DryRun) { Write-Host "[dry-run] result: ok=$ok $msg"; return }
  $o = [ordered]@{ time = (Get-Date).ToString('s'); profile = $Profile; ok = $ok; message = $msg }
  ($o | ConvertTo-Json) | Set-Content -LiteralPath $Result -Encoding UTF8
}
function Get-DWord($key, $name) { [uint32](Get-ItemProperty -LiteralPath $key -Name $name -ErrorAction Stop).$name }

function Test-Allowed([hashtable]$values, $cap) {
  foreach ($name in $values.Keys) {
    if ($Allowed -notcontains $name) { throw "refused: '$name' is not an allowlisted value" }
    $v = [uint32]$values[$name]
    if ($MaskedBy.ContainsKey($name)) {
      $mask = Get-DWord $CapKey $MaskedBy[$name]
      if (($v -band (-bnot $mask)) -ne 0 -or $v -eq 0) { throw "refused: $name=0x{0:x} is not within the earbuds' capability 0x{1:x}" -f $v, $mask }
    }
  }
}

try {
  if (-not $DryRun) {
    if (-not (Test-Path -LiteralPath $DataDir)) { throw "setup has not been run (missing $DataDir)" }
  }
  if (-not $Dev) { throw "no earbuds recorded in $DataDir\device.txt (run setup_movie_helper.ps1)" }
  if (-not (Test-Path -LiteralPath $NextKey)) { throw "settings key for the earbuds not found" }
  if (-not (Test-Path -LiteralPath $CapKey))  { throw "capability key for the earbuds not found" }

  $current = [ordered]@{}
  foreach ($n in $Allowed) {
    $p = Get-ItemProperty -LiteralPath $NextKey -Name $n -ErrorAction SilentlyContinue
    if ($null -ne $p) { $current[$n] = [uint32]$p.$n }
  }

  if ($Profile -eq 'movie') {
    $target = @{}
    foreach ($k in $MovieChanges.Keys) { $target[$k] = $MovieChanges[$k] }
    Test-Allowed $target $null
    $alreadyMovie = $true
    foreach ($k in $target.Keys) { if ($current[$k] -ne [uint32]$target[$k]) { $alreadyMovie = $false } }

    if (-not $alreadyMovie) {
      # remember what the music state was, then change; an existing snapshot is only replaced when we are leaving a music state
      if ($DryRun) { Write-Host "[dry-run] would save snapshot of $($current.Count) values: $(($current.GetEnumerator() | ForEach-Object { '{0}=0x{1:x}' -f $_.Key, $_.Value }) -join ', ')" }
      else { ($current | ConvertTo-Json) | Set-Content -LiteralPath $Snapshot -Encoding UTF8; Log "snapshot saved" }
    }
    foreach ($k in $target.Keys) {
      if ($DryRun) { Write-Host ("[dry-run] would set {0}: 0x{1:x} -> 0x{2:x}" -f $k, $current[$k], [uint32]$target[$k]) }
      else { Set-ItemProperty -LiteralPath $NextKey -Name $k -Value ([int][uint32]$target[$k]) -Type DWord; Log ("set {0} = {1}" -f $k, $target[$k]) }
    }
    if (-not $DryRun) {
      foreach ($k in $target.Keys) { if ((Get-DWord $NextKey $k) -ne [uint32]$target[$k]) { throw "read-back mismatch on $k" } }
    }
    Save-Result $true $(if ($alreadyMovie) { 'already in movie state' } else { 'movie settings written' })
  }
  else {
    if ($DryRun -and -not (Test-Path -LiteralPath $Snapshot)) { Write-Host '[dry-run] no snapshot yet: music would be refused until movie has been applied once'; Save-Result $false 'no saved music settings'; return }
    if (-not (Test-Path -LiteralPath $Snapshot)) { throw 'no saved music settings (movie has never been applied)' }
    $raw = Get-Content -LiteralPath $Snapshot -Raw | ConvertFrom-Json
    $restore = @{}
    foreach ($prop in $raw.PSObject.Properties) { $restore[$prop.Name] = [uint32]$prop.Value }
    Test-Allowed $restore $null
    foreach ($k in $restore.Keys) {
      if ($DryRun) { Write-Host ("[dry-run] would restore {0}: 0x{1:x} -> 0x{2:x}" -f $k, $current[$k], $restore[$k]) }
      else { Set-ItemProperty -LiteralPath $NextKey -Name $k -Value ([int]$restore[$k]) -Type DWord }
    }
    if (-not $DryRun) {
      foreach ($k in $restore.Keys) { if ((Get-DWord $NextKey $k) -ne $restore[$k]) { throw "read-back mismatch on $k" } }
      Log 'music settings restored'
    }
    Save-Result $true 'music settings restored'
  }
}
catch {
  Log ("FAILED: " + $_.Exception.Message)
  Save-Result $false $_.Exception.Message
  if (-not $DryRun) { exit 1 } else { Write-Host ("[dry-run] FAILED: " + $_.Exception.Message) }
}
