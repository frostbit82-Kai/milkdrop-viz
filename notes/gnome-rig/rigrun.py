"""Rig only: run the visualiser's main.py with Caps Lock read from a file.

The real Caps Lock LED belongs to the user's keyboard; the rig's is the file
<rig>/caps (present = on).
"""
import os, runpy, sys
src = sys.argv[1]
sys.argv = [os.path.join(src, "main.py"), *sys.argv[2:]]
sys.path.insert(0, src)
os.chdir(src)
import viz_common
_caps = os.path.join(os.environ["RIG_DIR"], "caps")
viz_common.caps_lock_on = lambda *a, **k: os.path.exists(_caps)
runpy.run_path(sys.argv[0], run_name="__main__")
