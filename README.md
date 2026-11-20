# BomsAI Visualizer

A MilkDrop preset visualizer for Windows and Linux, built on
[projectM 4](https://github.com/projectM-visualizer/projectm). It listens to
whatever your speakers are playing, so any player, game or browser tab drives
the picture. Then it helps you find the handful of presets worth keeping, out
of the ten thousand in a typical pack.

- Rate any preset 0–10 with one key, sort by rating, shuffle only the good ones
- Named collections, thumbnails, search, backup and restore
- Record clips with sound, a second visual with a crossfader, desktop wallpaper mode
- Live sliders, your own textures and overlays (still or moving), and a code editor for preset equations

**Free and open source under the [MIT licence](LICENSE).** Downloads, screenshots
and the full user guide are at
[bomsaisoftware.com/software/milkdrop-visualizer](https://bomsaisoftware.com/software/milkdrop-visualizer).
Ready-made installers are on the
[Releases](https://github.com/frostbit82-Kai/milkdrop-viz/releases/latest) page.

## Running from source

    bash setup.sh && bash run.sh                                  # Ubuntu
    powershell.exe -ExecutionPolicy Bypass -File .\setup_windows.ps1    # Windows

The Windows script ends by printing the PATH line its DLLs need; then run
`python main.py` from the venv. Building the installers is covered in [installer/README.md](installer/README.md).
No presets or textures ship with it; *Get presets* in the right-click menu
downloads packs from the places their authors published them.

Third-party components (projectM, Qt / PySide6, FFmpeg, SDL2 and others) keep
their own licences. See [installer/licenses/](installer/licenses/README-third-party.txt).
