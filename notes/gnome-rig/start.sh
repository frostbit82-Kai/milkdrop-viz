#!/bin/bash
# Headless GNOME Shell + Desktop Icons NG, isolated from the real session.
# Software rendering throughout: a GPU fault in here must never be able to
# reach the real desktop's compositor (see wallpaper_may_start's note).
#
#   bash notes/gnome-rig/start.sh &        then  notes/gnome-rig/app.sh ...
#
# Everything lives in $VIZ_RIG (default ~/.cache/viz-gnome-rig): its own HOME,
# its own dconf, its own D-Bus session. Under /home on purpose (snaps refuse a
# HOME anywhere else) and not under /tmp (quota, and wiped on reboot).
R="${VIZ_RIG:-$HOME/.cache/viz-gnome-rig}"
HERE="$(cd "$(dirname "$0")" && pwd)"
EXT="$R/home/.local/share/gnome-shell/extensions/rigprobe@local"
mkdir -p "$R/home/Desktop/Rig Folder" "$R/run" "$EXT" "$R/home/.config/BomsAIVisualizer"
chmod 700 "$R/run"
[ -e "$R/home/Desktop/notes.txt" ] || echo hello > "$R/home/Desktop/notes.txt"
cp "$HERE/rigprobe@local/"* "$EXT/"
rm -f "$R/ready" "$R/cmd"
exec env -i HOME="$R/home" USER="$USER" LOGNAME="$USER" PATH=/usr/local/bin:/usr/bin:/bin \
  XDG_RUNTIME_DIR="$R/run" LANG=en_US.UTF-8 RIG_DIR="$R" \
  XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=ubuntu:GNOME \
  LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
  dbus-run-session -- bash -c '
    gsettings set org.gnome.shell disable-user-extensions false
    gsettings set org.gnome.shell disable-extension-version-validation true
    gsettings set org.gnome.shell enabled-extensions "[\"ding@rastersoft.com\", \"rigprobe@local\"]"
    gsettings set org.gnome.shell welcome-dialog-last-shown-version "99"
    env | grep -E "^(DBUS_SESSION_BUS_ADDRESS)=" > "$RIG_DIR/bus.env"
    exec gnome-shell --headless --wayland --wayland-display wayland-rig --virtual-monitor 1920x1080
  '
