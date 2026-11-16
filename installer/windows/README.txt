BomsAI Visualizer
===================

A MilkDrop preset visualizer that reacts to whatever your speakers are already
playing -- any player, any browser tab, no plugin.


PRESETS AND TEXTURES ARE NOT INCLUDED
-------------------------------------

They are other people's work, so this package does not redistribute them. Both
are free downloads and the app asks where you put them.

Presets -- the "Cream of the Crop" pack, 9,795 presets curated by ISOSCELES and
the default set for projectM since 2022:

    https://github.com/projectM-visualizer/presets-cream-of-the-crop

Textures -- many of the best presets sample image files, and without them those
presets fall back to plain waveforms. Preset packs that use textures normally
ship them alongside; the full NestDrop package from ISOSCELES, linked from the
preset repository above, is the usual source.

Put each somewhere permanent, then in the app:

    right-click the window  ->  Settings  ->  Preset directory / Texture directory

Until the preset directory is set the window opens and stays idle. That is the
app waiting for presets, not a crash.


CONTROLS
--------

    right-click       menu (settings, preset browser, ratings)
    left / right      previous / next preset
    h                 hard cut to the next preset
    j                 random preset
    f  or  F11        fullscreen        (double-click also works)
    b                 borderless window
    t                 keep the window above other windows
    F9                start / stop recording a clip
    escape            quit
    up / down         rate the current preset up or down
    x                 exclude this one (rate it 0) and move on
    s                 snapshot this preset for the browser
    d                 desktop wallpaper mode on / off
    1                 visual 1 on / off
    2                 visual 2 on / off
    [  /  ]           crossfade towards visual 1 / visual 2
    tab               with two showing, adjust the other one
    c                 hard cuts on/off
    r                 shuffle the playlist order on / off
    scroll lock       lock the current preset

Every one of these can be changed: right-click -> Key bindings. A set you have
already saved there is kept as it is when you upgrade.

A preset rated 0 stars is dropped from the rotation. Settings and ratings are
kept in %APPDATA%\BomsAIVisualizer and survive uninstalling and upgrading.


FINDING PRESETS
---------------

The preset browser (right-click -> Preset browser) has a search box. Type any
part of a name and the list narrows to what matches; the window title shows how
many. Sorting, rating and the shuffle buttons all keep working on the results.

Presets you have watched for more than a few seconds get a thumbnail, captured
from the frame that was on screen, and it appears beside the name in the list.
There is no batch pass over the library on purpose: ten thousand presets at a
second each is two hours, and the pictures worth having are of the presets you
actually sat through. A fresh install has none and fills in as you use it.


TWO AT ONCE
-----------

The right-click menu has a switch for each of the two visuals and a
crossfader under them. One of the two is always on -- both off is a black
window -- so whichever is left alone greys out.

The fader runs from visual 1 alone, through both of them mixed, to visual 2
alone; "[" and "]" move it a step at a time. A visual the fader has taken all
the way out is not drawn at all, so parking it at one end costs no more than
running one visual.

Tab switches which of the two everything else acts on -- next, previous, the
browser, the rating -- so you can pick a preset for each independently. The
Quality row in Settings follows the same choice, and visual 2 starts on Low,
because the pair costs about twice one of them.

With more than one monitor, right-click -> Send visual to gives either visual
a screen of its own instead of sharing one window. The fader stops mixing
when they are apart -- there is nothing to mix -- and each monitor shows its
visual whole. The picture is letterboxed rather than stretched, so it matches
the shape of the main window; make that window fullscreen and the bars go.


CHANGING A PRESET
-----------------

The panel button at the right of the preset browser's toolbar opens four tabs
for the preset on screen:

    Tweak       sliders over the preset's own numbers -- zoom, rotation,
                warp, brightness. A slider is greyed when the preset's code
                overwrites that value every frame, which about half of them do.
    Code        the preset's text, with search and replace, a slider for any
                number you select, a per-frame line you can add, a colour
                shift, and a mash-up that takes another preset's shaders,
                motion, or shapes and waves.
    Textures    swap a picture the preset samples for one of your own.
    Overlay     burn a GIF or a video into the preset so its own warp takes
                hold of it, and lay your own pictures over the top.

Nothing here writes to your library. Changes are live only until Save as...,
which writes a new preset beside the original and leaves it alone; Revert puts
the preset back the way it came.


RECORDING
---------

F9 starts and stops a recording; the right-click menu offers MP4 or GIF and a
shortcut to the folder they land in. An MP4 carries the sound that drove it,
captured off the same output the visuals are reacting to. Clips are written to:

    %LOCALAPPDATA%\BomsAIVisualizer\clips

Recording needs ffmpeg, and a copy ships with the app -- there is nothing to
install. If you would rather use your own, put ffmpeg.exe in the app folder
beside BomsAIVisualizer.exe; that is the first place the app looks.


FULLSCREEN AND MULTIPLE MONITORS
--------------------------------

With more than one monitor connected, the right-click menu gains a Fullscreen
display picker. The default, "Follow the window", fills whichever monitor the
window is on when you go fullscreen; naming one instead always uses that one,
wherever the window happens to be.


SCREEN SAVER
------------

The installer offers to set BomsAI Visualizer as your screen saver. To change
it later, use Settings -> Personalisation -> Lock screen -> Screen saver, or
tick the box again by re-running the installer.

Any key or a real mouse movement ends it. Small movements are ignored, so a
mouse resting on a soft surface does not wake it.


DESKTOP WALLPAPER
-----------------

Right-click -> Set as desktop wallpaper draws the visuals on the desktop,
behind your icons. Press d for the same thing. Windows hands all input on the
desktop to Explorer, so your icons and the desktop's own right-click menu go
on working as they always did.

Caps Lock hands the desktop to the visualiser. While it is on, a right-click
on the desktop opens this app's menu instead of Explorer's, and once you have
clicked the desktop the keys above work as well: next, previous, rate, d to
leave. Your icons carry on working. Keys that are not bound to anything here
still go to Explorer, and typing in another window is never affected. A tray
icon appears too, and clicking it opens the same menu.

On one monitor that is one click. On more than one, the entry opens a list:
the whole desktop, or the screen you pick -- and picking a screen while it is
already on moves it there rather than starting again. The tray menu carries
the same list, so you can move it without leaving the mode.


THEME
-----

Settings -> Theme is Dark, Light, or Follow system. It covers the whole
interface: the settings and key-binding dialogs, the right-click menu, the
preset browser, and the title bar and border around the visualiser window.
Dark is the default, because the visuals themselves are usually dark.

"Follow system" tracks the Windows app-theme setting and changes with it the
next time a menu or dialog is opened.


AUDIO
-----

Capture is WASAPI loopback: it records an output device, so it hears everything
that device is playing without any virtual cable or "Stereo Mix". With no device
chosen it follows the Windows default output and moves with it.

To pick a specific one:  right-click  ->  Audio source, or Settings ->
"Output to capture". The same menu carries an input sensitivity slider for
material that is quieter or louder than the visuals expect.


IF IT DOES NOT START
--------------------

The release build is windowed and prints nothing, so there is no log to read.
The two usual causes are an OpenGL driver too old for projectM (it needs
OpenGL 3.3) and a preset directory that has not been set yet.


LICENSES
--------

See the licenses folder, starting with README-third-party.txt.

This app renders through projectM (LGPL-2.1) and uses Qt 6 with PySide6
(LGPL-3.0). Both ship as separate shared libraries beside the application
rather than linked into it, so either can be replaced with a compatible
build of your own.

Presets and textures are not included and are not ours to license -- they
belong to the people who made them.
