# MilkDrop Visualizer - one-shot setup for a fresh Windows install.
# Windows counterpart to setup.sh: installs the toolchain, builds libprojectM-4,
# creates the venv. Run from an ordinary PowerShell prompt; winget handles
# elevation for the Build Tools install.
#
# Produces C:\dev\projectm-deploy containing the three DLLs the app needs:
#   projectM-4.dll  projectM-4-playlist.dll  glew32.dll

$ErrorActionPreference = 'Stop'

$ProjectMTag = 'v4.1.0'   # matches the lib version projectm_wrapper.py expects
$DevRoot     = 'C:\dev'
$Src         = "$DevRoot\projectm"
$Build       = "$Src\cmake-build"
$Install     = "$DevRoot\projectm-install"
$Deploy      = "$DevRoot\projectm-deploy"
$VcpkgRoot   = "$DevRoot\vcpkg"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path

New-Item -ItemType Directory -Force -Path $DevRoot | Out-Null

# --- IPv6 workaround -------------------------------------------------------
# Some hosts advertise IPv6 without it working; curl then resolves AAAA records
# that never connect and downloads fail ~7 times in 8, with a misleading
# "Could not resolve hostname". Forcing IPv4 for the tools we drive is scoped to
# this build and needs no admin rights or system network changes. Harmless where
# IPv6 does work. VCPKG_KEEP_ENV_VARS is required because vcpkg sanitises the
# environment for port builds, so CURL_HOME would not otherwise reach them.
$CurlHome = "$DevRoot\curlhome"
New-Item -ItemType Directory -Force -Path $CurlHome | Out-Null
Set-Content -Path "$CurlHome\_curlrc" -Value '--ipv4' -Encoding ascii
$env:CURL_HOME           = $CurlHome
$env:VCPKG_KEEP_ENV_VARS = 'CURL_HOME'

# --- [1/4] Toolchain -------------------------------------------------------
Write-Output '==> [1/4] Checking toolchain...'
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$vsPath = if (Test-Path $vswhere) {
    & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
} else { $null }

if (-not $vsPath) {
    Write-Output '    Installing VS 2022 Build Tools (Desktop C++). This takes a while.'
    winget install --id Microsoft.VisualStudio.2022.BuildTools --exact `
        --accept-package-agreements --accept-source-agreements --disable-interactivity `
        --override '--quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
if (-not $vsPath) { throw 'Visual Studio Build Tools not found after install.' }
Write-Output "    VS: $vsPath"

$cmake = Join-Path $vsPath 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
if (-not (Test-Path $cmake)) { $cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source }
if (-not $cmake) { throw 'No cmake.exe found.' }
Write-Output "    cmake: $cmake"

# --- [2/4] Build libprojectM-4 --------------------------------------------
Write-Output "==> [2/4] Building libprojectM-4 ($ProjectMTag)..."
if (Test-Path "$Deploy\projectM-4.dll") {
    Write-Output '    Already built, skipping. Delete C:\dev\projectm-deploy to force a rebuild.'
} else {
    if (-not (Test-Path $VcpkgRoot)) {
        git clone https://github.com/microsoft/vcpkg.git $VcpkgRoot
        & "$VcpkgRoot\bootstrap-vcpkg.bat" -disableMetrics
    }

    if (-not (Test-Path $Src)) {
        git clone --depth 1 --branch $ProjectMTag --recurse-submodules --shallow-submodules `
            https://github.com/projectM-visualizer/projectm.git $Src
    }
    if (-not (Test-Path "$Src\vendor\projectm-eval\CMakeLists.txt")) {
        throw "Submodule vendor/projectm-eval is empty. Re-run 'git submodule update --init --recursive' in $Src."
    }

    # projectM's manifest lists gtest and sdl2, which upstream CI needs for the
    # test suite and the SDL developer UI. We build neither, and pulling them in
    # only adds download surface, so trim the manifest to the one dependency
    # Windows genuinely requires: find_package(GLEW REQUIRED). Everything else
    # (glm, SOIL2, hlslparser, projectm-eval) is vendored in the source tree.
    if (-not (Test-Path "$Src\vcpkg.json.upstream")) {
        Copy-Item "$Src\vcpkg.json" "$Src\vcpkg.json.upstream"
    }
    Set-Content -Path "$Src\vcpkg.json" -Encoding ascii -Value @'
{
    "dependencies": [ "glew" ]
}
'@

    & $cmake -G 'Visual Studio 17 2022' -A 'x64' -S $Src -B $Build `
        "-DCMAKE_TOOLCHAIN_FILE=$VcpkgRoot\scripts\buildsystems\vcpkg.cmake" `
        '-DVCPKG_TARGET_TRIPLET=x64-windows' `
        "-DCMAKE_INSTALL_PREFIX=$Install" `
        '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded$<$<CONFIG:Debug>:Debug>DLL' `
        '-DBUILD_SHARED_LIBS=ON' `
        '-DENABLE_PLAYLIST=ON' `
        '-DENABLE_SDL_UI=OFF' `
        '-DBUILD_TESTING=OFF'
    if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed.' }

    & $cmake --build $Build --config Release --parallel
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }

    & $cmake --install $Build --config Release
    if ($LASTEXITCODE -ne 0) { throw 'Install failed.' }

    # projectM-4.dll links glew32.dll, so it has to travel with them.
    New-Item -ItemType Directory -Force -Path $Deploy | Out-Null
    Copy-Item "$Install\bin\projectM-4.dll"          $Deploy
    Copy-Item "$Install\bin\projectM-4-playlist.dll" $Deploy
    Copy-Item "$Build\vcpkg_installed\x64-windows\bin\glew32.dll" $Deploy
}
Get-ChildItem $Deploy -Filter *.dll | ForEach-Object { Write-Output "    $($_.Name)" }

# --- [3/4] Virtual environment --------------------------------------------
Write-Output '==> [3/4] Creating Python virtual environment...'
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py -or $py -match 'WindowsApps') {
    Write-Output '    Installing Python 3.14...'
    winget install --id Python.Python.3.14 --exact `
        --accept-package-agreements --accept-source-agreements --disable-interactivity --scope user
    $py = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
}
Write-Output "    python: $py"

if (Test-Path "$ScriptDir\.venv") { Remove-Item -Recurse -Force "$ScriptDir\.venv" }
& $py -m venv "$ScriptDir\.venv"
& "$ScriptDir\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$ScriptDir\.venv\Scripts\pip.exe" install -r "$ScriptDir\requirements.txt"

# --- [4/4] Done ------------------------------------------------------------
Write-Output '==> [4/4] Done.'
Write-Output ''
Write-Output "Put $Deploy on PATH, or copy its three DLLs beside the app:"
Write-Output "    `$env:PATH = '$Deploy' + ';' + `$env:PATH"
Write-Output ''
Write-Output 'Presets and textures are not bundled. Download a pack and point'
Write-Output 'Settings -> Preset directory / Texture directory at it.'
