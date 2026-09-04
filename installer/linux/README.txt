MilkDrop Visualizer
===================

A MilkDrop preset visualizer that reacts to whatever your speakers are already
playing — any player, any browser tab, no plugin.


INSTALL
-------

    ./install.sh

Installs to ~/.local, no root needed. It adds an applications-menu entry and a
`milkdrop-viz` command, then starts the app once to check it runs.

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
    escape            quit
    + / -             rate the current preset up or down
    h                 hard cuts on/off
    m                 switch preset mode
    scroll lock       lock the current preset

A preset rated 0 stars is dropped from the rotation. Ratings and settings are
kept in ~/.config/MilkDropVisualizer and survive uninstalling.


AUDIO
-----

Capture follows your default output, so it picks up whatever is playing. Change
it under Settings -> Audio device.

Best results come from `parec` (package `pulseaudio-utils`); install.sh warns if
it is missing and the app falls back to PortAudio, which is bundled.


IF IT DOES NOT START
--------------------

Run `milkdrop-viz` in a terminal — it prints why. The two usual causes are a
missing OpenGL driver and a preset directory that has not been set yet.


LICENSES
--------

See the licenses/ directory. This app renders through projectM, which is
LGPL-2.1 and ships here as a separate shared library you are free to replace.
