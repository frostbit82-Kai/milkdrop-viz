# Build the Windows release installer. Developer-side script -- end users run
# the Setup .exe it produces.
#
#   powershell -ExecutionPolicy Bypass -File installer\build_windows.ps1
#
# Output: installer\dist\BomsAIVisualizer-<version>-win64-setup.exe
#         installer\dist\BomsAIVisualizer-<version>-win64\   (the portable tree)
#
# -SkipInstaller stops after freezing, which is what you want when iterating on
# the app rather than on packaging.
param([switch]$SkipInstaller)

$ErrorActionPreference = 'Stop'

$Here    = $PSScriptRoot
$Repo    = Split-Path $Here -Parent
$Version = (Get-Content (Join-Path $Repo 'VERSION') -Raw).Trim()
$Name    = "BomsAIVisualizer-$Version-win64"
$Build   = Join-Path $Here 'build'
$Out     = Join-Path $Here 'dist'

# libprojectM and the GLEW it links are the one dependency that cannot be
# rebuilt on demand -- see the Linux script for why. Known-good copies are
# vendored; PROJECTM_LIB_DIR overrides them when a newer build exists.
if (-not $env:PROJECTM_LIB_DIR) { $env:PROJECTM_LIB_DIR = Join-Path $Here 'vendor\windows' }
foreach ($dll in 'projectM-4.dll', 'projectM-4-playlist.dll', 'glew32.dll') {
    if (-not (Test-Path (Join-Path $env:PROJECTM_LIB_DIR $dll))) {
        throw "No $dll in $env:PROJECTM_LIB_DIR (build one with setup_windows.ps1)"
    }
}

$Venv   = if ($env:VENV) { $env:VENV } else { Join-Path $Repo '.venv' }
$Python = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "No venv at $Venv -- run: setup_windows.ps1" }
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { & $Python -m pip install -q pyinstaller }

Write-Host "==> [1/3] Freezing the app"
Remove-Item $Build, (Join-Path $Out $Name) -Recurse -Force -EA SilentlyContinue
$env:MILKDROP_REPO = $Repo
& $Python -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $Build 'dist') --workpath (Join-Path $Build 'work') `
    (Join-Path $Here 'bomsai-visualizer.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "==> [2/3] Assembling the package"
$Stage = Join-Path $Out $Name
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item (Join-Path $Build 'dist\BomsAIVisualizer') (Join-Path $Stage 'app') -Recurse
# A Windows screen saver is an ordinary executable with a .scr extension, and
# this one is a PyInstaller onedir build, so the copy has to sit beside
# _internal rather than in System32. Windows launches it as "...scr /s"; main.py
# reads that and the other screen-saver arguments before argparse runs.
Copy-Item (Join-Path $Stage 'app\BomsAIVisualizer.exe') `
          (Join-Path $Stage 'app\BomsAIVisualizer.scr')
Copy-Item (Join-Path $Here 'windows\README.txt') $Stage
New-Item -ItemType Directory -Force -Path (Join-Path $Stage 'licenses') | Out-Null
Copy-Item (Join-Path $Here 'licenses\*') (Join-Path $Stage 'licenses')
Set-Content (Join-Path $Stage 'VERSION') $Version -Encoding ascii

if ($SkipInstaller) {
    Write-Host "`nBuilt $Stage  (portable tree; installer skipped)"
    return
}

Write-Host "==> [3/3] Building the installer"
$Iscc = @('C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
          'C:\Program Files\Inno Setup 6\ISCC.exe',
          # winget's default is a per-user install, which lands here
          (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')) |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    throw "Inno Setup 6 not found. Install it with:  winget install -e --id JRSoftware.InnoSetup"
}
& $Iscc "/DAppVersion=$Version" "/DStageDir=$Stage" "/DOutDir=$Out" "/DOutName=$Name-setup" `
        (Join-Path $Here 'windows\bomsai-visualizer.iss') | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$Setup = Join-Path $Out "$Name-setup.exe"
$Mb    = [math]::Round((Get-Item $Setup).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $Setup  ($Mb MB)"
Write-Host "Portable tree left at $Stage"
