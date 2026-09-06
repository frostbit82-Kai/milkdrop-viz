MilkDrop Visualizer
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
    escape            quit
    + / -             rate the current preset up or down
    h                 hard cuts on/off
    m                 shuffle the playlist order on / off
    scroll lock       lock the current preset

A preset rated 0 stars is dropped from the rotation. Settings and ratings are
kept in %APPDATA%\MilkDropVisualizer and survive uninstalling and upgrading.


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
