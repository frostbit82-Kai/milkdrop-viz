#!/bin/bash
# Run something inside the rig session started by start.sh.
#
#   app.sh src [args]      the visualiser from a COPY of this checkout's tracked
#                          files (so the repo's own config.json is never written),
#                          with Caps Lock read from the file $VIZ_RIG/caps
#   app.sh frozen [args]   an installed build: $VIZ_FROZEN, or the one in ~/.local
#   app.sh env cmd...      any command, with the rig's display, bus and HOME
R="${VIZ_RIG:-$HOME/.cache/viz-gnome-rig}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
FROZEN="${VIZ_FROZEN:-$HOME/.local/share/BomsAIVisualizer/BomsAIVisualizer}"
# mutter's auth file has wildcard entries, so the display number is not in it.
XA=$(ls "$R"/run/.mutter-Xwaylandauth.* | head -1)
RP=""; for p in $(pgrep -x gnome-shell); do
  tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -q "^RIG_DIR=$R$" && RP=$p; done
# Xwayland starts on first use. Once it is up the X sockets are its own;
# before that they are still the shell's.
DPY=$(pgrep -a Xwayland | grep -F -- "$R/run/" | awk '{print $3}' | head -1)
[ -n "$DPY" ] || DPY=:$(ss -xlp 2>/dev/null | grep "X11-unix" | grep "pid=$RP," |
       awk '{print $5}' | sed -n 's#^/tmp/.X11-unix/X##p' | sort -n | head -1)
. "$R/bus.env"
mode=$1; shift
E=(env -i HOME="$R/home" USER="$USER" LOGNAME="$USER" PATH=/usr/local/bin:/usr/bin:/bin
   XDG_RUNTIME_DIR="$R/run" LANG=en_US.UTF-8 RIG_DIR="$R" DISPLAY="$DPY" XAUTHORITY="$XA"
   WAYLAND_DISPLAY=wayland-rig XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=ubuntu:GNOME
   DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"
   LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe PYTHONUNBUFFERED=1)
# The rig's own settings, for either build: eight presets out of the user's
# folder would do, but any folder of .milk files named in $VIZ_RIG_PRESETS works.
# seizure_warning_ack is set because nobody is there to click it, and this
# config belongs to the rig, not to anybody's real profile.
CFG="{\"preset_dir\": \"${VIZ_RIG_PRESETS:-$R/presets}\", \"texture_dir\": \"\", \"fps\": 30,
 \"width\": 800, \"height\": 600, \"quality\": \"low\", \"mesh_x\": 32, \"mesh_y\": 24,
 \"pause_when_idle\": \"never\", \"dual_enabled\": false, \"auto_thumbnails\": false,
 \"seizure_warning_ack\": true, \"preset_duration\": 60}"
case "$mode" in
  env)    exec "${E[@]}" "$@" ;;
  frozen) echo "$CFG" > "$R/home/.config/BomsAIVisualizer/config.json"
          exec "${E[@]}" "$FROZEN" "$@" ;;
  src)    mkdir -p "$R/src"
          ( cd "$REPO" && git ls-files -z '*.py' VERSION assets | tar --null -T - -cf - ) |
            tar -xf - -C "$R/src"
          echo "$CFG" > "$R/src/config.json"
          exec "${E[@]}" "$REPO/.venv/bin/python" "$HERE/rigrun.py" "$R/src" "$@" ;;
  *)      echo "usage: app.sh src|frozen|env ..." >&2; exit 2 ;;
esac
