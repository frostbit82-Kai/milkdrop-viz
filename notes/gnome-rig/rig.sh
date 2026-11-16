#!/bin/bash
# Talk to the probe extension in the rig session.
#
#   rig.sh shot out.png      screenshot of the rig's whole screen
#   rig.sh stack             its windows, bottom first: title, type, layer, focus
#   rig.sh click X Y [1|3]   move there and click (left, or 3 for right)
#   rig.sh key KEYVAL        press and release a key (X keysym: 106 j, 65363 Right,
#                            65307 Escape, 100 d, 65293 Return)
#   rig.sh caps on|off       the rig's Caps Lock: a file for a source run and a
#                            stand-in LED for a frozen one (the real LED is the user's)
#   rig.sh stop              end the rig: its app, then its shell. By PID.
R="${VIZ_RIG:-$HOME/.cache/viz-gnome-rig}"
ask() { rm -f "$R/$1"; shift; echo "$*" > "$R/cmd"; }
await() { for _ in $(seq 1 50); do [ -e "$R/$1" ] && return 0; sleep 0.1; done; return 1; }
mine() { tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | grep -q "^RIG_DIR=$R$"; }
case "$1" in
  shot)  ask shot.done "shot $(realpath -m "$2")"; await shot.done ;;
  stack) ask stack.json stack; await stack.json && python3 -c '
import json, sys
for w in json.load(open(sys.argv[1])):
    print(" ", w["title"], "| type", w["type"], "| layer", w["layer"],
          "| focus", w["focus"], w["rect"])' "$R/stack.json" ;;
  click) ask done "click $2 $3 ${4:-1}"; await done ;;
  key)   ask done "key $2"; await done ;;
  caps)  mkdir -p "$R/leds/input0::capslock"      # the frozen build's light
         if [ "$2" = on ]; then touch "$R/caps"; echo 1; else rm -f "$R/caps"; echo 0; fi \
           > "$R/leds/input0::capslock/brightness" ;;
  stop)  for p in $(pgrep -f rigrun.py) $(pgrep -x BomsAIVisualize) $(pgrep -x gnome-shell); do
           mine "$p" && kill "$p"; done ;;
  *)     sed -n "2,11p" "$0" >&2; exit 2 ;;
esac
