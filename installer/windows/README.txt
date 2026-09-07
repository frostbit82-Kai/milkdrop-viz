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
    space             hard cut to the next preset
    r                 random preset
    f  or  F11        fullscreen        (double-click also works)
    b                 borderless window
    F9                start / stop recording a clip
    escape            quit
    + / -             rate the current preset up or down
    h                 hard cuts on/off
    m                 shuffle the playlist order on / off
    scroll lock       lock the current preset

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


RECORDING
---------

F9 starts and stops a recording; the right-click menu offers MP4 or GIF and a
shortcut to the folder they land in. Clips are silent -- only the visuals are
captured -- and are written to:

    %LOCALAPPDATA%\BomsAIVisualizer\clips

Recording needs ffmpeg. If it is not on your PATH, put ffmpeg.exe in the app
folder beside BomsAIVisualizer.exe and the app will find it there first.


FULLSCREEN AND MULTIPLE MONITORS
--------------------------------

With more than one monitor connected, the right-click menu gains a Fullscreen
display picker. "Follow the window" is the default and behaves as before.


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
behind your icons. Windows hands all input on the desktop to Explorer, so while
this is on the visualiser cannot be clicked -- a tray icon appears instead, and
clicking it opens the same menu. Use it to change preset, rate, or leave
wallpaper mode.

With a monitor chosen under Fullscreen display it fills that one; otherwise it
spans every monitor.


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
