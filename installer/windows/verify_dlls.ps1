# Verifies the vendored Windows projectM DLLs before they ship.
#
# Run this after re-vendoring installer\vendor\windows (a projectM upgrade, or a
# rebuild via setup_windows.ps1). It answers the two questions that have bitten
# this project:
#
#   1. Does every symbol the app binds actually exist in the DLLs? Some projectM
#      builds omit projectm_playlist_add_presets, and a missing symbol shows up
#      only as a crash or an empty playlist at runtime, never at build time.
#   2. What else must ship alongside? Anything outside the system set and the
#      DLLs here has to be vendored too, or the installed app dies on a machine
#      that is not this one.
#
# The required-symbol list is derived from viz_common.py, so it cannot go stale.
#
#     powershell.exe -ExecutionPolicy Bypass -File .\installer\windows\verify_dlls.ps1

$ErrorActionPreference = 'Stop'

$Repo   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Vendor = Join-Path $Repo 'installer\vendor\windows'
$Source = Join-Path $Repo 'viz_common.py'

$vsw = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$vsPath = & $vsw -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
$dumpbin = Get-ChildItem -Path (Join-Path $vsPath 'VC\Tools\MSVC') -Recurse -Filter dumpbin.exe |
           Where-Object { $_.FullName -match '\\Hostx64\\x64\\' } | Select-Object -First 1
if (-not $dumpbin) { throw "dumpbin not found - install the VC++ build tools" }

$dlls = Get-ChildItem -Path $Vendor -Filter *.dll
if (-not $dlls) { throw "no DLLs in $Vendor" }
Write-Output "=== vendored DLLs ==="
$dlls | ForEach-Object { Write-Output ("  {0,-28} {1:N0} bytes" -f $_.Name, $_.Length) }

# ctypes binds a symbol by assigning its signature: "C.projectm_create.restype".
# Anything else matching projectm_* in that file is a Python name, not a symbol.
$required = Select-String -Path $Source -Pattern '\b[A-Z]\.(projectm_[a-z0-9_]+)\.(?:restype|argtypes)' -AllMatches |
            ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } |
            Sort-Object -Unique

$exports = @{}
foreach ($d in $dlls) {
    foreach ($line in (& $dumpbin.FullName /exports $d.FullName)) {
        if ($line -match '^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s+(\S+)') { $exports[$matches[1]] = $d.Name }
    }
}

Write-Output ""
Write-Output "=== required symbols ($($required.Count) bound by viz_common.py, $($exports.Count) exported in total) ==="
$missing = @()
foreach ($sym in $required) {
    if ($exports.ContainsKey($sym)) { Write-Output ("  OK      {0,-48} {1}" -f $sym, $exports[$sym]) }
    else                            { Write-Output ("  MISSING {0}" -f $sym); $missing += $sym }
}

Write-Output ""
Write-Output "=== runtime dependencies (anything unfamiliar must be vendored too) ==="
foreach ($d in $dlls) {
    Write-Output "--- $($d.Name) ---"
    (& $dumpbin.FullName /dependents $d.FullName) |
        Where-Object { $_ -match '^\s+\S+\.dll$' } | ForEach-Object { Write-Output "    $($_.Trim())" }
}

Write-Output ""
if ($missing.Count) { throw "$($missing.Count) missing: $($missing -join ', ')" }
Write-Output "RESULT: all $($required.Count) required symbols present."
