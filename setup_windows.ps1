# BomsAI Visualizer - one-shot setup for a fresh Windows install.
# Windows counterpart to setup.sh: installs the toolchain, builds libprojectM-4,
# creates the venv. winget handles elevation for the Build Tools install.
#
# Windows leaves the execution policy at Restricted, which refuses to load this
# file at all - "running scripts is disabled on this system". So run it as:
#
#     powershell.exe -ExecutionPolicy Bypass -File .\setup_windows.ps1
#
# or lift the gate once for your account, which is the saner setting on a dev
# box:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# Produces C:\dev\projectm-deploy containing the two DLLs the app needs:
#   projectM-4.dll  projectM-4-playlist.dll

$ErrorActionPreference = 'Stop'

$DevRoot     = 'C:\dev'
$Src         = "$DevRoot\projectm"
$Build       = "$Src\cmake-build"
$Install     = "$DevRoot\projectm-install"
$Deploy      = "$DevRoot\projectm-deploy"
$VcpkgRoot   = "$DevRoot\vcpkg"
# $PSScriptRoot is empty when this file is pasted into the console or dot-sourced
# instead of run as a file - which is exactly what someone tries after hitting the
# execution-policy error above. Without the fallback the venv lands in the drive
# root; without the check it lands in whatever directory they happened to be in.
$ScriptDir   = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
if (-not (Test-Path "$ScriptDir\requirements.txt")) {
    throw "requirements.txt not found in $ScriptDir. Run this as a file " +
          "(powershell.exe -ExecutionPolicy Bypass -File .\setup_windows.ps1), " +
          "or cd to the repository first."
}

# The libprojectM commit to build, shared with the Linux installer so the two
# platforms can never drift apart. Read rather than duplicated: a second copy
# of a SHA is a second thing to forget. installer/PROJECTM_COMMIT says why it
# is a commit and not a tag.
$PinFile = Join-Path $ScriptDir 'installer\PROJECTM_COMMIT'
if (-not (Test-Path $PinFile)) { throw "No $PinFile" }
$ProjectMCommit = (Get-Content $PinFile | Where-Object { $_ -notmatch '^\s*#' } |
                   ForEach-Object { $_.Trim() } | Where-Object { $_ } |
                   Select-Object -First 1)
if ($ProjectMCommit -notmatch '^[0-9a-f]{40}$') {
    throw "installer/PROJECTM_COMMIT does not hold a full commit SHA (got '$ProjectMCommit')."
}

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
Write-Output "==> [2/4] Building libprojectM-4 ($($ProjectMCommit.Substring(0,12)))..."
# A stamp beside the DLLs, not just their existence: the deploy directory
# survives a pin bump, and skipping on "a DLL is there" is how a stale
# libprojectM gets shipped while the build reports success.
$DeployStamp = "$Deploy\PROJECTM_COMMIT"
$DeployedAt = if (Test-Path $DeployStamp) { (Get-Content $DeployStamp -Raw).Trim() } else { '' }
if ((Test-Path "$Deploy\projectM-4.dll") -and $DeployedAt -eq $ProjectMCommit) {
    Write-Output '    Already built at this commit, skipping. Delete C:\dev\projectm-deploy to force a rebuild.'
} else {
    if ($DeployedAt -and $DeployedAt -ne $ProjectMCommit) {
        Write-Output "    Deployed DLLs are from $($DeployedAt.Substring(0,12)); rebuilding at the pin."
        Remove-Item -Recurse -Force $Deploy, $Build, $Install -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $VcpkgRoot)) {
        git clone https://github.com/microsoft/vcpkg.git $VcpkgRoot
        & "$VcpkgRoot\bootstrap-vcpkg.bat" -disableMetrics
    }

    # Fetched by SHA rather than cloned by tag: the pin is a commit on master
    # with no tag pointing at it, and --branch does not take a SHA. A shallow
    # fetch of one commit is what GitHub serves fastest; the full fetch is the
    # fallback for a host that refuses to serve an arbitrary SHA.
    if (-not (Test-Path "$Src\.git")) {
        New-Item -ItemType Directory -Force -Path $Src | Out-Null
        git -C $Src init -q
        git -C $Src remote add origin https://github.com/projectM-visualizer/projectm.git
    }
    if ((git -C $Src rev-parse HEAD 2>$null) -ne $ProjectMCommit) {
        # The manifest trim below leaves vcpkg.json modified in the source
        # tree, and checkout refuses to move over a dirty file -- so a pin
        # bump aborted on the tree the previous run had left behind. It is
        # rewritten a few lines further down either way.
        git -C $Src checkout -q -- vcpkg.json 2>$null
        git -C $Src fetch --depth 1 origin $ProjectMCommit
        if ($LASTEXITCODE -ne 0) {
            Write-Output '    Shallow fetch by SHA refused; fetching the full history.'
            git -C $Src fetch origin
            if ($LASTEXITCODE -ne 0) { throw 'Could not fetch libprojectM.' }
        }
        git -C $Src checkout -q --detach $ProjectMCommit
        if ($LASTEXITCODE -ne 0) { throw "Commit $ProjectMCommit not found after fetch." }
    }
    git -C $Src submodule update --init --recursive --depth 1
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

    # Two DLLs, not three: projectM loads OpenGL itself at this commit and
    # no longer links glew32.dll. GLEW stays in the vcpkg manifest because
    # projectM's CMake still asks find_package for it at configure time.
    New-Item -ItemType Directory -Force -Path $Deploy | Out-Null
    Copy-Item "$Install\bin\projectM-4.dll"          $Deploy
    Copy-Item "$Install\bin\projectM-4-playlist.dll" $Deploy
    # Last, so a build that died half way leaves no stamp claiming success.
    Set-Content -Path $DeployStamp -Encoding ascii -Value $ProjectMCommit
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
