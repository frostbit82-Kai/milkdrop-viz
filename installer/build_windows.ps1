# Build the Windows release installer. Developer-side script -- end users run
# the Setup .exe it produces.
#
#   powershell -ExecutionPolicy Bypass -File installer\build_windows.ps1
#
# Output: installer\dist\BomsAIVisualizer-<version>-win64-setup.exe
#         installer\dist\BomsAIVisualizer-<version>-win64\   (the portable tree)
#
# -SkipInstaller stops after freezing, which is what you want when iterating on
# the app rather than on packaging. -NoFfmpeg leaves the recorder's ffmpeg out,
# which halves the installer while iterating.
param([switch]$SkipInstaller, [switch]$NoFfmpeg)

$ErrorActionPreference = 'Stop'

$Here    = $PSScriptRoot
$Repo    = Split-Path $Here -Parent
$Version = (Get-Content (Join-Path $Repo 'VERSION') -Raw).Trim()
$Name    = "BomsAIVisualizer-$Version-win64"

# The EULA names the version it applies to in prose, and nothing reads it back:
# the version half moves with every bump, the terms date deliberately does not,
# and a line that has drifted looks exactly like one that has not. Fail here
# rather than ship a licence that talks about the version before this one.
$Eula = Join-Path $Repo 'installer\licenses\EULA.txt'
if (-not (Select-String -Path $Eula -SimpleMatch "Applies to version $Version " -Quiet)) {
    throw "installer\licenses\EULA.txt does not say ""Applies to version $Version"""
}
$Build   = Join-Path $Here 'build'
$Out     = Join-Path $Here 'dist'

# libprojectM and the GLEW it links are the one dependency that cannot be
# rebuilt on demand -- see the Linux script for why. Known-good copies are
# vendored; PROJECTM_LIB_DIR overrides them when a newer build exists.
if (-not $env:PROJECTM_LIB_DIR) { $env:PROJECTM_LIB_DIR = Join-Path $Here 'vendor\windows' }
foreach ($dll in 'projectM-4.dll', 'projectM-4-playlist.dll') {
    if (-not (Test-Path (Join-Path $env:PROJECTM_LIB_DIR $dll))) {
        throw "No $dll in $env:PROJECTM_LIB_DIR (build one with setup_windows.ps1)"
    }
}

# ffmpeg is bundled so that recording works on a machine that has never heard
# of ffmpeg -- which is nearly all of them. It MUST be an LGPL build: libx264
# is GPL and cannot ship inside a paid closed-source application, and
# video_codec_args() already falls back to h264_mf, which LGPL Windows builds
# carry and which is hardware accelerated. That is checked below rather than
# trusted, because the GPL and LGPL archives differ by one word in the name.
$FfmpegUrl = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip'
$Ffmpeg = if ($env:FFMPEG_EXE) { $env:FFMPEG_EXE } else { Join-Path $Here 'vendor\windows\ffmpeg.exe' }
if (-not $NoFfmpeg) {
    if (-not (Test-Path $Ffmpeg)) {
        Write-Host "==> Fetching an LGPL ffmpeg (once; ~140 MB)"
        $zip = Join-Path $env:TEMP 'ffmpeg-lgpl.zip'
        $tmp = Join-Path $env:TEMP 'ffmpeg-lgpl'
        # IPv6 on this box advertises without working, and the resulting
        # failure looks like DNS -- see the trap in CLAUDE.md.
        curl.exe -4 -sL --retry 3 -o $zip $FfmpegUrl
        if ($LASTEXITCODE -ne 0) {
            throw "Could not download ffmpeg. Put an LGPL ffmpeg.exe at $Ffmpeg, or pass -NoFfmpeg."
        }
        Remove-Item $tmp -Recurse -Force -EA SilentlyContinue
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $found = Get-ChildItem $tmp -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
        if (-not $found) { throw "No ffmpeg.exe inside $zip" }
        New-Item -ItemType Directory -Force -Path (Split-Path $Ffmpeg -Parent) | Out-Null
        Copy-Item $found.FullName $Ffmpeg
        Remove-Item $zip, $tmp -Recurse -Force -EA SilentlyContinue
    }
    # The licence check. A GPL build carries libx264; an LGPL one does not.
    $encoders = & $Ffmpeg -hide_banner -loglevel error -encoders 2>$null | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$Ffmpeg would not run" }
    if ($encoders -match 'libx264') {
        throw "$Ffmpeg is a GPL build (it has libx264). Only an LGPL ffmpeg may ship in this app."
    }
    if ($encoders -notmatch 'h264_mf') {
        throw "$Ffmpeg has neither libx264 nor h264_mf, so recorded clips would fall back to mpeg4."
    }
    Write-Host "    ffmpeg: $Ffmpeg (LGPL, h264_mf present)"
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
# Beside the exe, which is the first place find_ffmpeg() looks in a frozen
# build -- ahead of PATH, so the bundled copy wins over whatever the host has.
if (-not $NoFfmpeg) {
    Copy-Item $Ffmpeg (Join-Path $Stage 'app\ffmpeg.exe')
}
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
