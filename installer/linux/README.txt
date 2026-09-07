BomsAI Visualizer
===================

A MilkDrop preset visualizer that reacts to whatever your speakers are already
playing — any player, any browser tab, no plugin.


INSTALL
-------

    ./install.sh

Installs to ~/.local, no root needed. It adds an applications-menu entry and a
`bomsai-viz` command, then starts the app once to check it runs.

To install somewhere else:   PREFIX=/opt ./install.sh    (may need sudo)
To remove:                   ./uninstall.sh


PRESETS AND TEXTURES ARE NOT INCLUDED
-------------------------------------

They are other people's work, so this package does not redistribute them. Both
are free downloads and the app asks where you put them.

Presets — the "Cream of the Crop" pack, 9,795 presets curated by ISOSCELES and
the default set for projectM since 2022:

    https://github.com/projectM-visualizer/presets-cream-of-the-crop

Textures — many of the best presets sample image files, and without them those
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
    space             hard cut to the next preset
    r                 random preset
    f  or  F11        fullscreen        (double-click also works)
    F9                start / stop recording a clip
    escape            quit
    + / -             rate the current preset up or down
    h                 hard cuts on/off
    m                 shuffle the playlist order on / off
    scroll lock       lock the current preset

A preset rated 0 stars is dropped from the rotation. Ratings and settings are
kept in ~/.config/BomsAIVisualizer and survive uninstalling.


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


RECORDING
---------

F9 starts and stops a recording; the right-click menu offers MP4 or GIF and a
shortcut to the folder they land in. Clips are silent — only the visuals are
captured — and are written to:

    ~/.local/share/BomsAIVisualizer/clips

Recording needs ffmpeg. Install it from your package manager (`ffmpeg` on
Debian and Ubuntu, `ffmpeg` on Fedora and Arch), or drop the binary beside the
application.


FULLSCREEN AND MULTIPLE MONITORS
--------------------------------

With more than one monitor connected, the right-click menu gains a Fullscreen
display picker. "Follow the window" is the default and behaves as before.


SCREEN SAVER
------------

`bomsai-viz --screensaver` runs fullscreen and quits on any key or real mouse
movement, which is the contract xscreensaver and similar expect. There is no
automatic integration — desktops differ too much — so wire it in yourself if
you want it.

Desktop wallpaper mode is Windows only: X11 desktops each place the wallpaper
differently and Wayland gives an application no way to draw behind the icons.


THEME
-----

Settings -> Theme is Dark, Light, or Follow system, and covers the settings and
key-binding dialogs, the right-click menu and the preset browser. Dark is the
default, because the visuals themselves are usually dark.

"Follow system" reads the desktop's colour-scheme preference through the
freedesktop portal, the same one GTK and Qt applications use.

The window border is not included: on Linux the window manager draws it, from
the desktop's own theme, and no application can override that.


AUDIO
-----

Capture follows your default output, so it picks up whatever is playing. Change
it under Settings -> Audio device.

Best results come from `parec` (package `pulseaudio-utils`); install.sh warns if
it is missing and the app falls back to PortAudio, which is bundled.


IF IT DOES NOT START
--------------------

Run `bomsai-viz` in a terminal — it prints why. The two usual causes are a
missing OpenGL driver and a preset directory that has not been set yet.


LICENSES
--------

See the licenses/ directory, starting with README-third-party.txt.

This app renders through projectM (LGPL-2.1) and uses Qt 6 with PySide6
(LGPL-3.0). Both ship as separate shared libraries beside the application
rather than linked into it, so either can be replaced with a compatible
build of your own.

Presets and textures are not included and are not ours to license — they
belong to the people who made them.
