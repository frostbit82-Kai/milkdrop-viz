# Wallpaper mode: icons with Caps Lock on, keys from the desktop, new default keys

Working notes, 15 Nov 2026 (Linux box). Kept so a context reset loses nothing.

## What the user asked (15 Nov 2026)

1. "desktop mode here in Ubuntu hides the folder, but shows under opened windows"
2. Key bindings should work in desktop (wallpaper) mode with Caps Lock on when
   focus is on the desktop -- the way the right-click menu already does.
3. New DEFAULT key bindings = the user's own keymaps.json (table below).
4. Then rebuild the Linux installer; the user rebuilds on Windows afterwards.
   User said: the real desktop may be used if need be, and a notes folder in
   the project is welcome.

## New defaults (from ~/.config/BomsAIVisualizer/keymaps.json, 26 Oct 2026)

hard_cut_next h (was SPACE) | random j (was r) | toggle_hard_cuts c (was h)
rate_up UP (was +) | rate_down DOWN (was -) | toggle_mode r (was m)
snapshot s (was c). Everything else unchanged:
next RIGHT, prev LEFT, lock SCROLLLOCK, fullscreen f, fullscreen_alt F11,
quit ESCAPE, toggle_borderless b, toggle_on_top t, fullscreen_mouse DBLCLICK,
record F9, toggle_wallpaper d, exclude x, toggle_dual 2, toggle_visual1 1,
crossfade_back [, crossfade_fwd ], swap_focus TAB.

## Findings

- Installed build here is 2.1.13. Journal of the user's run (20:08): wallpaper
  on, then `RCLICK` printed -- which `_right_click_is_ours` only allows with
  Caps Lock ON. So the "hidden folder" was the Caps-Lock-on state, which on
  Linux is BY DESIGN the visualiser held over the icon layer.
- Rig run of the installed 2.1.13, Caps Lock off, GNOME 50.1 + mutter
  50.1-0ubuntu2.5 + DING: icons visible over the visuals. No regression.
- So the fix is a design change: icons stay visible with Caps Lock on too
  (Windows already does this with its mouse hook).
- Side bug found by the rig: `import sounddevice` raises PortAudioError (plain
  Exception) when no sound server is reachable; the guard caught only
  ImportError/OSError and a source run died at import. Guard widened.

## The headless GNOME rig (new; nothing touches the real desktop)

Lives in ~/.cache/viz-gnome-rig (delete when done; recipe is below).
- start.sh: `env -i HOME=<rig>/home XDG_RUNTIME_DIR=<rig>/run ... LIBGL_ALWAYS_SOFTWARE=1
  dbus-run-session -- gnome-shell --headless --wayland --wayland-display wayland-rig
  --virtual-monitor 1920x1080`, after gsettings (rig dconf) enables
  ding@rastersoft.com and a probe extension.
- Probe extension rigprobe@local (in the rig HOME): polls <rig>/cmd for
  `stack`, `shot <png>`, `move x y`, `click x y 1|3`, `key <keyval>`; uses
  Shell.Screenshot and Clutter virtual input devices. Answers in files.
- app.sh src|frozen: runs the app inside it (DISPLAY/XAUTHORITY read off the
  rig's Xwayland command line, bus from bus.env). `src` runs a COPY of the
  tracked files so the repo's config.json is never written.
- rigrun.py: replaces viz_common.caps_lock_on with "does <rig>/caps exist",
  because the real LED is the user's keyboard.
- `global.get_window_actors()` does not list DING's window (DING hides it).

## Rig results so far (15 Nov, stopped here: usage limit)

- Source build, Caps ON (rig seam): icons hidden, visuals under the top bar.
  That is the user's report exactly. Shots were s1.png (Caps off, icons
  visible) and s2.png (Caps on, icons gone) in the rig folder.
- Caps ON, left-click the desktop, then RIGHT twice: two new "Now playing"
  lines, and the shell reports the visualiser window focused. So on Linux the
  key bindings ALREADY work with Caps Lock on once the desktop is clicked.
  Windows is where they cannot (the wallpaper never takes the keyboard).

## Plan (nothing below is built yet)

1. Linux: keep the GL window under the icons in BOTH Caps states, always
   click-through. With Caps on, map a second, fully transparent ARGB X11
   window (SDL window via SDL_HINT_VIDEO_X11_WINDOW_VISUALID, desktop type,
   top of the desktop layer, same 0.1 s blind hold with _X11_ABOVE) that takes
   the clicks and the keyboard; hide it with Caps off. Only where compositing
   is certain (Wayland session, or a compositing WM); else keep today's raise.
   Icons are then visible but inert while Caps is on.
2. Windows: a WH_KEYBOARD_LL hook beside _DesktopRightClick, only while Caps
   is on and the foreground window is Progman/WorkerW; swallow only keys that
   are bound, never VK_CAPITAL, never with Ctrl/Alt/Win held; queue them for
   the render thread's _handle_key. Has to be proven on Windows.
3. DEFAULT_KEYMAPS to the table above; test_keymaps.py asserts snapshot == "c";
   grep README/guide/store/site for the old keys.
4. Bump VERSION, run the 26 suites, build the Linux package, record it in
   PORTING.md, push. Uncommitted right now: the sounddevice import guard in
   viz_common.py (suites not re-run yet).
5. Rig recipe above; ~/.cache/viz-gnome-rig can be rebuilt from it.

## Prototype result (15 Nov, later): the transparent catcher works on GNOME

`proto_catcher.py` in the rig: an SDL window made with
`SDL_VIDEO_X11_WINDOW_VISUALID` = the 32-bit TrueColor visual
(XMatchVisualInfo; 0x70 here), hidden, desktop type + the usual states set
before the map, XSetWindowBackground(0) + XClearWindow, then
`_NET_RESTACK_WINDOW` Above every 0.1 s. Over the installed 2.1.13 running as
the wallpaper (Caps off, so under the icons):
- screenshot: visuals AND icons both visible through it;
- injected right-click, left-click on an icon, `j` and Right all arrived at
  the catcher (SDL BUTTON 3 / BUTTON 1 / KEY 106 / KEY SDLK_RIGHT); the icon
  did not react; the shell says the catcher has focus after the click.
- mutter reports the catcher in layer 1 (BOTTOM) and the visualiser in layer
  0 (DESKTOP). Both are type DESKTOP. Above the icons, under every window.

Rig launcher traps: Xwayland starts on first use, so before that the X
sockets belong to gnome-shell (`ss -xlp`, pid of the rig shell) and after it
to the rig's Xwayland (`pgrep -a Xwayland | grep viz-gnome-rig`); mutter's
auth file has wildcard entries with no display number in them.

## Being built now

Linux: `_wp_catch` (the catcher) opened by `_set_wallpaper_front(True)` where
compositing is certain, GL window stays click-through under the icons in both
Caps states; legacy raise kept where it is not. Windows: keyboard hook beside
the mouse hook (`on_key`), keys queued to the render loop. Then the defaults.

## Built and measured in the rig (15 Nov, evening) -- source tree, not yet committed

Code is in viz_common.py: `x11_argb_visual`, `x11_is_composited`,
`x11_set_cardinal`, `win_vk_to_sdl`, the keyboard half of
`_DesktopRightClick` (`on_key`), and on Renderer `_open_wallpaper_catcher`,
`_drop_wallpaper_catcher`, `_hold_wallpaper_catcher`, `_x11_window_of`,
`_double_clicked`, `_desktop_key`, `_hook_keys`; DEFAULT_KEYMAPS changed.
The 2.1.13 wallpaper suite still passes unchanged.

Rig, GNOME 50.1 + DING, source build, Caps via the rig seam:
- Caps ON: icons and visuals both visible (s5/s7); stack = visualiser layer 0,
  "BomsAI Visualizer — desktop" (the catcher) layer 1.
- Right-click: `RCLICK` logged and the visualiser's menu on screen (s6).
- Click desktop, then Right and j: two new "Now playing" lines.
- Double-click on an icon: toast "Caps Lock is on — turn it off to use the
  desktop icons" (s7); the icon does nothing.
- Caps OFF: catcher gone from the stack; right-click gives DING's own menu
  (s8), no RCLICK.
- Typing in another window (zenity entry) while Caps goes ON: the catcher
  maps and the other window KEEPS the keyboard -- typed a, Caps on, j, Right,
  b -> the entry held "ajb", no preset change. (`_NET_WM_USER_TIME` 0 is set
  before the map for this.)
- Click desktop + d with Caps on: leaves wallpaper mode, catcher dropped,
  window back at 800x637. d again in the window: back in, catcher reopened.

Still to do: tests (test_wallpaper.py, test_keymaps.py -- the "c" examples
assume the old defaults), docs with old default keys, VERSION 2.1.14,
all suites, Linux package, CLAUDE.md/PORTING.md/ROADMAP, push. Windows hook
is written and UNPROVEN: the Windows session must prove it.

## State at the docs stage (16 Nov, early)

Done in the tree, uncommitted: code, 26 suites green (new checks mutation-
tested), both READMEs, CLAUDE.md, ROADMAP-2.0.md, PORTING.md 2.1.14 entry,
VERSION 2.1.14, the rig saved as notes/gnome-rig/ (start.sh, app.sh, rig.sh).

Still to do, in order:
1. Commit + push (fix commit, then the version).
2. `bash installer/build_linux_docker.sh` -> dist tarball; verify (GLIBC
   floor, VERSION, `--help` in stock ubuntu:24.04, frozen build in the rig
   Caps off); record size + sha256 in PORTING.md; delete the 2.1.13 dist.
3. `python check_sync.py --fix`, commit in ~/Projects/Bomsai Player
   ("Take across the visualizer: ...").
4. Install 2.1.14 on this box? (user's ~/.local install is 2.1.13) -- ask or
   leave: the user said they'd do Windows; say how.
5. Site (bomsai-software, user deploys): visualizer-guide.astro `keys` table
   (lines ~35-55) + prose kbd mentions (Space/R/M/+/-/C) + the "On Linux,
   Caps Lock brings the visuals up in front of the icons" paragraph + the
   wallpaper bullet; apps.js line ~238 and the key-bindings screenshot notes
   (~310-338). Linux key-bindings shot shows old defaults -> retake; the
   Windows one is the Windows session's.
6. Memory: bomsai-milkdrop-port.md / MEMORY.md line for 2.1.14.

## 16 Nov: committed, building

Commits (local, not pushed yet): 47a139f (the work) and 437c2d4 (2.1.14:
VERSION, EULA line, ROADMAP, PORTING). The first container build stopped on
its own check -- "EULA.txt does not say Applies to version 2.1.14" -- so the
version bump is VERSION *and* installer/licenses/EULA.txt line 4, as it was
for 2.1.13.

Measured in the rig: SDL's visual hint set back to "" really is unset -- a
window made with it is depth 32, the plain and the GL window made after it
are both depth 24. So visual 2's window cannot inherit the catcher's visual.

A frozen build can have Caps Lock on in the rig after all: `app.sh frozen`
runs it under `bwrap --dev-bind / / --bind <rig>/leds /sys/class/leds`, and
`rig.sh caps on|off` writes that stand-in LED as well as the source run's file.

Site (bomsai-software, NOT committed/deployed yet): guide key table + prose
keys + Caps Lock paragraphs + apps.js wallpaper line are edited in the tree.
Still wanted there: the Linux key-bindings.webp retaken on the new defaults.

## DONE -- 16 Nov 2026

- milkdrop-viz pushed: 47a139f (work), 437c2d4 (2.1.14), 38be25a (package
  record). Package: installer/dist/BomsAIVisualizer-2.1.14-linux-x86_64.tar.gz,
  89,973,634 bytes, sha256 d09c39a9...ab0a3d6e, GLIBC_2.34. PORTING.md has
  the verification and the list Windows must prove before building.
- Bomsai Player: d01b27b takes viz_common.py across (check_sync identical).
- Site (bomsai-software): 8664968 pushed, NOT deployed -- guide keys, Caps
  Lock text, Linux key-bindings.webp retaken. Deploy is the user's, after
  Windows has built 2.1.14; win-key-bindings.webp is Windows' to retake.
- 2.1.14 installed on this Ubuntu box (~/.local/share/BomsAIVisualizer);
  the user's keymaps.json untouched.
- Rig stopped, Xvfb :77 stopped, every ~/.cache/viz-* scratch folder deleted.
  The rig rebuilds from notes/gnome-rig/ (it needs a folder of .milk files:
  $VIZ_RIG_PRESETS, or copy a few into ~/.cache/viz-gnome-rig/presets).

## Open after this

- The user's own check on the real desktop: wallpaper mode, Caps Lock on ->
  folder icons still visible, right-click menu, keys after a desktop click.
- Windows: pull, prove the keyboard hook (PORTING.md list), build, retake
  win-key-bindings.webp. Mint: Cinnamon pass with Caps Lock on.
- FOUND, NOT FIXED: install.sh `rm -rf`s ~/.local/share/BomsAIVisualizer,
  which is also user_data_dir() on Linux -- clips, thumbs and sprites are
  wiped by every upgrade and by uninstall. Flagged as its own task.
