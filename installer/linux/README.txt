BomsAI Visualizer
===================

A MilkDrop preset visualizer that reacts to whatever your speakers are already
playing — any player, any browser tab, no plugin.


INSTALL
-------

Double-click "Install BomsAI Visualizer". A terminal opens, it installs, and it
waits for you to press Enter before closing.

The first time, your desktop will probably ask whether to trust the launcher --
that is the desktop protecting you from files that arrive pretending to be
something else, and it happens to every archive that ships one:

    GNOME Files    right-click the launcher -> Allow Launching, then double-click
    Thunar / KDE   double-click, then choose Trust and Launch / Execute

If you would rather not, or you are on a machine with no desktop at all, the
launcher only runs the script sitting next to it:

    ./install.sh

Installs to ~/.local, no root needed. It adds an applications-menu entry and a
`bomsai-viz` command, then starts the app once to check it runs.

To install somewhere else:   PREFIX=/opt ./install.sh    (may need sudo)
To remove:                   double-click "Uninstall BomsAI Visualizer",
                             or run ./uninstall.sh

Keep this folder. The uninstaller lives in it, not in the installed copy.


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

Until the preset directory is set, the window plays the one preset that does
ship with it — the BomsAI logo, drawn by the same engine everything else uses.
That is the app waiting for presets, not a crash.


CONTROLS
--------

    right-click       menu (settings, preset browser, ratings)
    left / right      previous / next preset
    space             hard cut to the next preset
    r                 random preset
    f  or  F11        fullscreen        (double-click also works)
    b                 borderless window
    t                 keep the window above other windows
    F9                start / stop recording a clip
    escape            quit
    + / -             rate the current preset up or down
    x                 exclude this one (rate it 0) and move on
    c                 snapshot this preset for the browser
    d                 desktop wallpaper mode on / off (X11 sessions)
    1                 visual 1 on / off
    2                 visual 2 on / off
    [  /  ]           crossfade towards visual 1 / visual 2
    tab               with two showing, adjust the other one
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
shortcut to the folder they land in. A clip carries the sound that drove it —
captured off the same output the visuals are reacting to — and lands in:

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


DESKTOP WALLPAPER
-----------------

Right-click -> Set as desktop wallpaper draws the visuals on the desktop,
behind your icons. Press d for the same thing. The window sits below
everything, so it cannot be clicked while it is there -- a tray icon appears
instead, and clicking it opens the same menu. Use it to change preset, rate,
or leave wallpaper mode.

With a monitor chosen under Fullscreen display it fills that one; otherwise it
spans every monitor. Settings -> At login can start it this way.

This needs an X11 session. Wayland gives an application no way to draw behind
the desktop icons -- there is no such layer in the protocol, and running under
XWayland does not add one. If your login screen offers a session ending in "on
Xorg", picking that gets you wallpaper mode; the menu entry says so and stays
greyed out otherwise. Everything else in the app works the same either way.

How your desktop draws its icons decides what you get:

  Cinnamon, and anything else that draws icons on a see-through layer of
  their own, is the one this was built for. The visuals become the wallpaper
  and your icons stay on top of them, exactly as on Windows.

  XFCE paints the wallpaper and the icons on the same window, so there is no
  layer to slip underneath. Wallpaper mode still fills the screen, but your
  desktop icons are covered while it is on. The app says so when it happens
  rather than leaving you to notice. Turning off desktop icons in the XFCE
  settings, if you do not use them, gets you the rest of the feature.

Either way this only shows up if your desktop has compositing switched on,
which it is by default nearly everywhere.


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
