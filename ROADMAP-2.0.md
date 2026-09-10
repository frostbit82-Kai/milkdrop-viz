# Version 2.0 — Preset Studio

The 1.x line is a player: it finds presets, plays them, and puts them on your
desktop. 2.0 is the release where you can *change* one and keep the result.

Scope was set by reading MilkDrop 3's feature list against what libprojectM 4.2
actually exposes, and against what this app already ships. Most of MilkDrop 3's
headline list is either already here or lives inside libprojectM's renderer,
where we are not going.

VERSION stays 1.7.0 until the whole of section A ships. The Inno Setup `AppId`
GUID never changes, 2.0 or not — it is what makes the installer an upgrade
rather than a second copy.

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

## B. Video as a source

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

### C1. Always on top
Genuinely absent. MilkDrop 3's F7. Small, and it pairs with borderless for the
"park it in a corner" use.

### C2. Span multiple monitors
The display picker chooses *one* display. Spanning is new: size the window to the
union of the chosen displays, and skip SDL's fullscreen, which is per-display by
definition. Same class of problem as the wallpaper work — the window manager gets
a vote. Expect it to be a bigger job than it sounds on X11, and check Windows
first where it is nearly free.

---

## D. Verification debt

Not features. Things believed to work that nobody has run.

- **Microphone capture** on all three backends. The pickers list inputs; nobody
  has spoken into one.
- **Bluetooth output**, whose monitor source appears and disappears with the
  device — the parec backend resolves the default sink's monitor once.

---

## Order

A1, then A2. Those two are the release. A3 and A4 are cheap once the editor is
there; A5 only if the splitter's round-trip test comes out clean. C1 any time.
B after A, because it is the one item that can cost frame rate. C2 last, or never.

D can be done from a chair with a microphone and is worth doing before any of it.
