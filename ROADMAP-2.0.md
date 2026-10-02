# Version 2.0 — Preset Studio

The 1.x line is a player: it finds presets, plays them, and puts them on your
desktop. 2.0 is the release where you can *change* one and keep the result.

Scope was set by reading MilkDrop 3's feature list against what libprojectM 4.2
actually exposes, and against what this app already ships. Most of MilkDrop 3's
headline list is either already here or lives inside libprojectM's renderer,
where we are not going.

**VERSION is 2.0.0 as of 2026-09-09** — 2.1.0 as of 2026-09-13, for three
features that arrived after a few days of using 2.0.0, and **2.1.5 as of
2026-09-15**; see PORTING.md for every number in between and what moved it. The
gate for 2.0 was the whole of section A, and the highlighter was the last of
it. The Inno Setup `AppId` GUID does not change
with it — that GUID is what makes the installer an upgrade rather than a second
copy, and it never changes, 2.0 or not.

**Section A is done as of 2026-09-09** — A1 `20c81a4`, A2 `86bd51f`,
A3 and A4 `1501131`, A5 `7362428`. B, C and D are all optional to the version
number; the release is whatever ships alongside them. Still to do before the
bump: run it on Mint, then match Windows.

## Already shipped — not in scope

Listed because they read like MilkDrop 3 features and are not work:

| MilkDrop 3 calls it | We have | Where |
| --- | --- | --- |
| MilkPanel sliders | Tweak panel, with `preset_driven_keys` so a slider that the preset overwrites every frame is not offered | `test_tweak.py` |
| Playlists, favourites | Collections — a named list of stems that narrows search, sort, shuffle and delete-0★ | `test_collections.py` |
| Screen number | Display picker | `test_display_picker.py` |
| Borderless | Borderless (`b`) | `toggle_borderless` |
| Audio device incl. mic | Device picker over three backends: parec, sounddevice, WASAPI loopback | `AudioCapture` |
| Sprites | Sprites, from pictures the user drops in | `test_sprites.py` |
| Transitions | Soft-cut blending, beat-driven hard cuts | `projectm_set_soft_cut_duration` |

## Out of scope, deliberately

**Double-presets (`.milk2`).** MilkDrop 3 renders two presets at once, composited
by one of 23 patterns. libprojectM has no concept of it: its transition is A to B
and then B alone. The route exists — two instances, two FBOs via
`projectm_opengl_render_frame_fbo`, our own composite pass — at 2x GPU cost and a
private file format to reverse-engineer. Not worth it. MilkDrop 3 keeps this one.

**Anything inside libprojectM's renderer.** `get_fft()` in shaders, 16 shapes and
waves instead of 4, q1–q64, 500-point shapes, mouse variables, a shader cache.
Each means forking libprojectM and owning the merge conflicts forever. The
AlmaLinux work already showed how much the *build* of that library costs us; the
source is not somewhere to start living.

---

## A. Preset Studio — done

The plumbing is done. `preset_text()`, `preview_preset(text)`, `save_preset_as()`
and `revert_preset()` already exist and already work — the tweak panel drives
them with sliders. Everything below is a new face on calls that ship today.

### A1. Bind the preset-failure callback — done, and it was half the story

`projectm_set_preset_switch_failed_event_callback` hands back a message and a
filename. Bind it the way the log callback is bound at `viz_common.py:1390` —
same lifetime trap, the C library keeps the pointer.

Load-bearing, not polish. `_do_load_preset_data` currently logs a byte count and
nothing else, so a preset that fails to compile leaves the previous frame on
screen forever. That is survivable when a slider moved 0.01. It is not survivable
when someone is typing shader code: an editor with no error line is not an
editor.

Surfaces as: the failure text under the editor, and the toast that already exists
for a libprojectM too old to load from a string.

**What the real run found:** the callback covers preset *parse* failures only.
A preset that parses and whose shader will not compile is logged instead, and
projectM renders on with that stage dropped — which is the error somebody
editing shader code hits most. Both are now kept, and the log one is read back
three frames later because it is not written until the preset is drawn.

**What Mint found, 9 Sep 2026:** the log half never reached the editor. A comp
shader with a bad type gives `[FinalComposite] Error compiling composite warp
shader code` at level 4 — WARN — and the capture kept only level ≥ 5, so
`_LAST_PM_ERROR` stayed empty and the editor showed nothing for exactly the
failure this item exists to surface. The timing was never the problem, and
400 ms was always long enough.

Checked against the 4.2.0 source afterwards, because neither machine could see
the whole shape on its own: libprojectM picks the level by **stage**, not by
severity. Warp-shader failures are `LOG_ERROR` (`PerPixelMesh.cpp:61,80`),
composite ones are `LOG_WARN` (`FinalComposite.cpp:49,87`), and the per-pixel,
per-frame, shape and wave failures are thrown rather than logged and arrive
through the failed-preset callback instead. Breaking a warp shader here
reported at ERROR and looked fine; breaking a comp shader on Mint reported at
WARN and vanished. Matched on the message text as well as the level now, and
not on the whole of WARN, because the rest of that level is noise the editor
must not present as a failure — most of it `[TextureManager] Failed to find
requested texture`, which a preset pack triggers constantly and which stops
nothing being drawn. `test_code_editor.py` covers both directions and both
levels.

### A2. Shader and preset code editor — done

A monospace editor beside the existing tweak sliders, on the same panel.

- Loads `preset_text()`. Ctrl+Enter calls `preview_preset()`. Ctrl+S is
  `save_preset_as()`, which already refuses to overwrite the original.
- Syntax highlight for `.milk`: section headers, `per_frame_N=`, HLSL in the
  warp and comp blocks. `QSyntaxHighlighter`, no dependency.
- Errors from A1 under the text, with the filename stripped.
- Undo is Qt's own. Revert is the existing `revert_preset()`.

Ship the highlighter last — a plain `QPlainTextEdit` that compiles on Ctrl+Enter
is already the feature.

**The highlighter shipped afterwards**, once Mint reported it as the only part
of section A missing. `MilkHighlighter` colours the key, the numbers, the
shader and equation keywords, and `//` comments, in both themes. The keyword
rule runs only where `preset_bin` says the value is code — otherwise every
preset whose title contains `sin` or `min` gets a function-coloured word in
the middle of its name. Formats are built per panel rather than cached on the
class, so a theme change is not left painting in the old palette.

### A3. Colour randomiser — done, as a hue shift

MilkDrop 3's `c` key. Rewrite the float3/float4 literals in the comp and warp
shader text, push through `preview_preset()`, keep the previous text for a
one-step undo (their Shift+C).

Text-only, no engine involvement. Refuse to touch anything outside the shader
blocks — the existing tweak code already holds that line and the reason is in
`test_tweak.py`'s docstring: a `.milk` file is somebody else's work.

### A4. Search and replace, Q-variable injection — done

Falls out of A2 for nothing. A find/replace bar over the editor buffer, and a
button that inserts a `q1=` line into the right section.

### A5. Mashup — done

Swap named sections between two presets — MilkDrop 3's `a`/`m`/`z` keys, minus
the deep-mashup mode they themselves call unstable.

Needs a section splitter that round-trips a `.milk` file byte-identically when
nothing is swapped. That test is the feature: if the splitter loses a line, it
loses somebody's preset. `preset_driven_keys` already parses these files, so the
shape is known.

Last in the section because it is the only part with a parser in it.

---

## B. Video as a source — **done**, `b186f9c` and `6cdfc41`

Shipped in three places rather than one, all off the same decode: burnt into
the preset (`projectm_opengl_burn_texture`, as planned below), laid over the
render as a sprite, and answered for by name when a preset asks for a texture
libprojectM's own scanner cannot read. GIF, animated WebP and video in all
three. The per-frame upload cost that the plan below calls the whole risk did
not bite — `LIVE_MAX_TEXTURES` caps how many run at once, and the frames are
uploaded with `glTexSubImage2D` into a texture projectM is already holding
rather than a new one each time. The rest of this section is the plan as it
was written; CLAUDE.md has what was actually learned, including the
`@warning` in projectM's public header that says the opposite of what the
call does.

`projectm_opengl_burn_texture` draws any GL texture onto the active preset's
main texture — which is the motion feedback buffer, so the preset then warps and
echoes the video rather than sitting behind it. That is the effect worth having,
and MilkDrop 3 has nothing like it.

Confirmed present in both vendored libraries (4.2.0): the Linux `.so` and the
Windows `.dll` both export it, as they do the sprite calls.

- Decode with the ffmpeg already bundled. It must stay an **LGPL** build.
- Upload each frame to a GL texture on the render thread, burn, move on.
- The call takes left, top, width and height and nothing else. No alpha, no blend
  mode. Negative width or height flips. Position and scale is the whole control
  surface; anything more is a shader we would have to write.

The risk is entirely the per-frame upload. Measure with `BOMSAI_FPS=1` before and
after — that switch exists because it caught a 27% regression nothing else saw.
If a 1080p source costs real frames, downscale on decode and say so in the UI.

---

## C. Window and display

### C1. Always on top — **done**
`SDL_SetWindowAlwaysOnTop`, on the `t` key and in the right-click menu, session
only like borderless is. Parking as the desktop drops it first: X11 rewrites
`_NET_WM_STATE` wholesale and Windows restores a saved exstyle, so nothing
carries it back out and a window that still claimed to be on top would be lying.

### C2. Span multiple monitors — **answered a different way in 2.1.7**
The original idea was one visual stretched across the union of two displays.
What shipped instead is **a visual on each**: "Send visual to" gives visual 2
a borderless SDL window of its own on a chosen monitor, one GL context serving
both windows. That is the thing people with two screens actually asked for,
and it sidesteps the part of this entry that predicted trouble — there is no
union rectangle to compute and no fight with SDL's per-display fullscreen.

Spanning *one* visual across two screens is still not built, and is still the
harder job for the reason given here: the window manager gets a vote. Nobody
has asked for it. Leave it until somebody does.

Two window-manager traps were paid for on the way and are in CLAUDE.md: a
reparenting WM ignores `SDL_CreateWindow`'s position for a borderless window,
and SDL drops the obvious correction because it matches its own cache.

---

## D. Verification debt

Not features. Things believed to work that nobody has run.

- **Bluetooth output — verified 2026-09-09**, against a real speaker rather than
  a stand-in. The picker lists it by its own name ("Monitor of Game Day Party"),
  capture pinned to it reads the tone back, and on Auto the follow watchdog moves
  onto it when it takes the default output and back off when it gives it up:
  `default output changed to 'bluez_output…monitor' — reopening capture`. The
  worry in the earlier draft — that the monitor is resolved once — was wrong;
  `_follow_tick` re-asks the backend class every `FOLLOW_POLL_S`.
  A pinned source that vanishes and returns also recovers, because parec
  reconnects to a re-created source of the same name by itself.
  Still unverified: a speaker with a **microphone**, which this one has not got —
  its card offers `a2dp-sink` and `headset-output`, no source at all.
- **Microphone capture**, half done. The Linux path opens the machine's real
  analog input (not a monitor) and delivers chunks continuously; at full input
  gain the noise floor reads 0.000536, so samples are genuinely arriving. What
  nobody has done is speak into one and watch the visual react, and the Windows
  and sounddevice backends have not been tried at all.

---

## Order

A1, then A2. Those two are the release. A3 and A4 are cheap once the editor is
there; A5 only if the splitter's round-trip test comes out clean. C1 done.
B after A, because it is the one item that can cost frame rate. C2 last, or never.

**Everything on this page is done except three things**, as of 2026-09-16:

- **C2, spanning multiple monitors.** Still "last, or never".
- **D's microphone.** Needs a person and a headset; nothing here can stand in
  for it.
- **The key-bindings dialog opens too short.** It comes up 512px tall and shows
  9 of its 24 rows, so everything added since 2.1.7 -- Visual 1, Visual 2, both
  Crossfade keys and Adjust the other visual -- is below the fold on first open.
  Found 16 Sep 2026 while shooting it for the site: the window had to be dragged
  out by hand before the picture was worth taking, and a buyer will not know to
  do that. `KeymapDialog` already measures its own WIDTH from the column hints
  (see the long comment at the `resize` call); it passes `self.height()` through
  unchanged, which is the `setMinimumHeight(480)` it was given. Give the height
  the same treatment -- rows x row height + header + chrome, capped against the
  screen -- rather than picking another number that a later row will outgrow.

D's Bluetooth half is done. What is left of it needs a chair and a microphone.

**Asked for on 2 Oct 2026 and built the same day, on Linux.** Six changes,
from a round of use on other machines; all on Linux and in the rig, **none on
Windows yet** -- that pass is the user's, and the list of what only Windows
can prove is below.

- Wallpaper mode waits for startup to settle before switching
  (`wallpaper_may_start`), which is what waiting by hand did. The report was a
  freeze, sometimes taking the desktop with it; the journal showed an amdgpu
  page fault, a GPU reset and gnome-shell aborting, 16 s after launch.
- Under Wayland it gets below Desktop Icons NG, which X cannot see
  (`x11_stack_is_partial`). Was: visuals over the icons, under every window.
- Caps Lock picks whose menu a right-click on the desktop opens.
- The right-click menu takes user-named categories (Customize menu), each a
  submenu on hover, for screens where the full menu ran to two columns.
- Rows you press repeatedly -- next, previous, random, sorts, lock, collection
  and audio picks -- leave the menu open; rows that open a window or change
  the window still close it.
- A shuffled playlist, and the place in it, survive closing the app.

**Windows still has to prove:**

- **The desktop right-click hook (`_DesktopRightClick`).** A low-level mouse
  hook on a thread of its own, installed only while Caps Lock is on in
  wallpaper mode. Unrun on any machine. Check that the menu opens over the
  desktop with Caps Lock on, that Explorer's menu is untouched with it off,
  that a right-click inside any other application still reaches that
  application with Caps Lock on, and that the mouse does not lag.
- **`GetKeyState(VK_CAPITAL)` from the render thread.** The toggle bit is
  per-thread input state on paper, and the render thread's window never has
  the keyboard in wallpaper mode. Background Caps Lock indicators rely on it
  working, which is the evidence -- not a measurement.
- **The icons on other Windows 11 builds.** The WorkerW-child route was
  measured on 26200. Wallpaper apps that broke on 24H2's "raised desktop"
  (Lively, kirie, rexpaper) moved to Microsoft's guidance instead: a layered
  child of Progman, z-ordered directly below SHELLDLL_DefView, with the
  WorkerW pushed to the bottom and the order re-asserted because Explorer
  changes it. If a machine shows the visuals over the icons, that is the
  change to make; nothing was changed here without a report.
- **Linux, still: the X11 Caps Lock mode hides the icons while it is on.** It
  works by holding the visualiser over the icon layer, because under Wayland
  an X client cannot take a click away from a Wayland window -- there is no
  other way to receive it there. On Windows the hook leaves the icons alone.

**Mint is done, 9 Sep 2026.** Section A and C1 were exercised on real hardware
— Mint 21.3, Cinnamon 6.0.5, AMD Pitcairn, the 9,795-preset pack, audio off a
Bluetooth sink. A1 failed and is fixed (above). A2's editor, A3's hue shift
(10/10 achromatic triples survive, 6/6 coloured rotate), A4's find-and-replace
(18 `sin(` to 0, `cos(` 2 to 20) and per-frame insert, A5's mashup (shaders
merged, compiled clean) and C1 all behave. Windows is next.
