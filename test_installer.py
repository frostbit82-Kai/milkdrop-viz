#!/usr/bin/env python3
"""The Linux installer must never delete what the user made.

Found 16 Nov 2026, while installing 2.1.14: `install.sh` put the app in
`~/.local/share/BomsAIVisualizer` and replaced that folder wholesale on every
upgrade -- and `user_data_dir()` is that same folder on Linux. So an upgrade,
and an uninstall, deleted every recorded clip, every thumbnail, every preset
pack fetched through Get presets, the collections and the saved shuffle order,
while the README and the uninstall launcher both promised they were kept. The
docstring on `user_data_dir` had said "never the install directory" all along.

The app lives under `lib` now and its folder is still replaced wholesale; the
data folder is nobody's to delete. These checks run the real scripts against a
scratch `HOME`, with a stand-in for the app that answers `--help`.

Linux only: the Windows installer is Inno Setup, which installs under Program
Files while the data goes to %LOCALAPPDATA%, so the two were never one folder.
Run: python test_installer.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: The scripts under test. Overridable so the checks can be shown to fail
#: against the scripts as they were before the fix.
SCRIPTS = os.environ.get("INSTALLER_DIR") or os.path.join(HERE, "installer",
                                                          "linux")

results = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


#: Everything the app writes under user_data_dir(), one file of each.
USER_DATA = {
    "clips/party.mp4": b"a clip somebody recorded",
    "thumbs/abc123.png": b"a thumbnail of a preset they sat through",
    "presets/Cream of the Crop/Dancer/a.milk": b"a pack fetched in the app",
    "sprites/logo.png": b"an overlay picture",
    "texture-overrides/clouds.jpg": b"a texture stand-in",
    "shuffle.json": b'{"order": []}',
    "collections.json": b'{"Chill": ["a"]}',
}


def _write(path, data, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if mode:
        os.chmod(path, mode)


class World:
    """A scratch HOME, a package to install from, and the scripts' own env."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="viz-installer-")
        self.home = os.path.join(self.root, "home")
        self.prefix = os.path.join(self.home, ".local")
        self.data = os.path.join(self.prefix, "share", "BomsAIVisualizer")
        self.config = os.path.join(self.home, ".config")
        os.makedirs(self.home)
        self.pkg = self.package("2.1.99")

    def package(self, version):
        """What the tarball holds: the real scripts around a stand-in app."""
        pkg = os.path.join(self.root, f"pkg-{version}")
        os.makedirs(pkg)
        for name in os.listdir(SCRIPTS):
            shutil.copy(os.path.join(SCRIPTS, name), os.path.join(pkg, name))
        _write(os.path.join(pkg, "VERSION"), version.encode() + b"\n")
        _write(os.path.join(pkg, "licenses", "EULA.txt"), b"terms\n")
        _write(os.path.join(pkg, "app", "BomsAIVisualizer"),
               f"#!/bin/sh\necho 'usage: BomsAIVisualizer {version}'\n".encode(),
               0o755)
        _write(os.path.join(pkg, "app", "_internal", "VERSION"),
               version.encode() + b"\n")
        return pkg

    def env(self):
        # Nothing of the real user's: not their HOME, not their XDG folders.
        return {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C.UTF-8"}

    def run(self, script, pkg=None):
        r = subprocess.run(["bash", os.path.join(pkg or self.pkg, script)],
                           env=self.env(), capture_output=True, text=True,
                           timeout=120)
        assert r.returncode == 0, f"{script} failed:\n{r.stdout}\n{r.stderr}"
        return r.stdout + r.stderr

    def seed_user_data(self):
        for rel, data in USER_DATA.items():
            _write(os.path.join(self.data, rel), data)

    def seed_old_install(self):
        """The layout every release up to 2.1.14 left behind: the app inside
        the data folder."""
        _write(os.path.join(self.data, "BomsAIVisualizer"),
               b"#!/bin/sh\necho old\n", 0o755)
        _write(os.path.join(self.data, "_internal", "VERSION"), b"2.1.13\n")
        _write(os.path.join(self.data, "licenses", "EULA.txt"), b"old terms\n")
        _write(os.path.join(self.data, "README.txt"), b"old readme\n")
        _write(os.path.join(self.prefix, "share", "applications",
                            "bomsai-visualizer.desktop"),
               f"[Desktop Entry]\nExec={self.data}/BomsAIVisualizer\n".encode())
        os.makedirs(os.path.join(self.prefix, "bin"), exist_ok=True)
        os.symlink(os.path.join(self.data, "BomsAIVisualizer"),
                   os.path.join(self.prefix, "bin", "bomsai-viz"))

    def lost(self):
        """Which of the user's files are gone or changed."""
        out = []
        for rel, data in USER_DATA.items():
            p = os.path.join(self.data, rel)
            if not os.path.isfile(p):
                out.append(rel)
            elif open(p, "rb").read() != data:
                out.append(rel + " (changed)")
        return out

    def exe(self):
        """Where the launcher on PATH really points."""
        return os.path.realpath(os.path.join(self.prefix, "bin", "bomsai-viz"))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _world(fn):
    def run():
        if not sys.platform.startswith("linux"):
            return
        w = World()
        try:
            fn(w)
        finally:
            w.close()
    run.__name__ = fn.__name__
    return run


@_world
def an_upgrade_keeps_everything_the_user_made(w):
    """The report this suite exists for. Clips, thumbnails, fetched packs,
    sprites, texture stand-ins, the shuffle and the collections all sat in
    the folder the installer deleted."""
    w.seed_old_install()
    w.seed_user_data()
    w.run("install.sh")
    assert not w.lost(), f"the upgrade deleted: {w.lost()}"


@_world
def the_next_upgrade_keeps_it_too(w):
    """Not only the move out of the old layout: every install after it."""
    w.run("install.sh")
    w.seed_user_data()
    w.run("install.sh", w.package("2.2.0"))
    assert not w.lost(), f"the second install deleted: {w.lost()}"


@_world
def uninstalling_keeps_it_too(w):
    """The uninstall launcher says "Ratings, collections and settings are
    left alone". Collections live in the data folder."""
    w.run("install.sh")
    w.seed_user_data()
    out = w.run("uninstall.sh")
    assert not w.lost(), f"the uninstall deleted: {w.lost()}"
    assert w.data in out, "did not say where the user's files were left"


@_world
def uninstalling_an_old_install_keeps_it_too(w):
    """The uninstaller that ships with a new package, run over an install
    made by an old one."""
    w.seed_old_install()
    w.seed_user_data()
    w.run("uninstall.sh")
    assert not w.lost(), f"the uninstall deleted: {w.lost()}"
    for name in ("BomsAIVisualizer", "_internal", "licenses", "README.txt"):
        assert not os.path.exists(os.path.join(w.data, name)), \
            f"left the old app's {name} behind"


@_world
def the_app_is_not_installed_into_the_data_folder(w):
    """The invariant itself, asked of the two things that have to agree:
    where the script puts the app, and where the app keeps its data."""
    import viz_common
    w.run("install.sh")
    saved = {k: os.environ.get(k) for k in ("HOME", "XDG_DATA_HOME")}
    os.environ["HOME"] = w.home
    os.environ.pop("XDG_DATA_HOME", None)
    try:
        data = os.path.realpath(viz_common.user_data_dir())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    app = os.path.dirname(w.exe())
    assert data == os.path.realpath(w.data), (data, w.data)
    assert os.path.commonpath([app, data]) not in (app, data), \
        f"the app is at {app} and its data at {data}: one is inside the other"


@_world
def an_upgrade_from_the_old_layout_leaves_no_second_copy(w):
    """The old build is taken out of the data folder by name -- its four
    entries and nothing else -- and everything that pointed at it follows."""
    w.seed_old_install()
    w.seed_user_data()
    w.run("install.sh")
    for name in ("BomsAIVisualizer", "_internal", "licenses", "README.txt"):
        assert not os.path.exists(os.path.join(w.data, name)), \
            f"the old app's {name} is still in the data folder"
    exe = w.exe()
    assert os.path.isfile(exe) and os.access(exe, os.X_OK), exe
    assert not exe.startswith(w.data + os.sep), f"still launching {exe}"
    out = subprocess.run([exe, "--help"], capture_output=True, text=True).stdout
    assert "2.1.99" in out, f"the launcher runs the old build: {out!r}"
    desktop = open(os.path.join(w.prefix, "share", "applications",
                                "bomsai-visualizer.desktop")).read()
    assert f"Exec={exe}" in desktop, desktop


@_world
def the_apps_own_folder_is_still_replaced_wholesale(w):
    """What the old `rm -rf` was for: a file an older build shipped and a
    newer one does not must not survive into the new install."""
    w.run("install.sh")
    stale = os.path.join(os.path.dirname(w.exe()), "_internal", "gone-in-2.2")
    _write(stale, b"a library the next version dropped")
    w.run("install.sh", w.package("2.2.0"))
    assert not os.path.exists(stale), "an old build's file outlived the upgrade"
    v = open(os.path.join(os.path.dirname(w.exe()), "_internal",
                          "VERSION")).read().strip()
    assert v == "2.2.0", v


@_world
def a_login_entry_follows_the_app_to_its_new_place(w):
    """Settings -> At login writes the executable's full path into
    ~/.config/autostart. Left alone, the move turns "start at login" into an
    entry that silently starts nothing."""
    w.seed_old_install()
    entry = os.path.join(w.config, "autostart", "BomsAIVisualizer.desktop")
    _write(entry, ("[Desktop Entry]\nType=Application\nName=BomsAI Visualizer\n"
                   f"Exec={w.data}/BomsAIVisualizer --wallpaper\n"
                   "Terminal=false\n").encode())
    theirs = os.path.join(w.config, "autostart", "something-else.desktop")
    _write(theirs, f"[Desktop Entry]\nExec={w.data}/BomsAIVisualizer\n".encode())
    w.run("install.sh")
    text = open(entry).read()
    assert f"Exec={w.exe()} --wallpaper\n" in text, text
    assert "Name=BomsAI Visualizer\n" in text and "Terminal=false\n" in text
    assert open(theirs).read().endswith(f"Exec={w.data}/BomsAIVisualizer\n"), \
        "rewrote a login entry that is not this app's"


@_world
def a_login_entry_for_a_source_checkout_is_left_alone(w):
    entry = os.path.join(w.config, "autostart", "BomsAIVisualizer.desktop")
    line = "Exec=/usr/bin/python3 /home/dev/milkdrop-viz/main.py\n"
    _write(entry, ("[Desktop Entry]\n" + line).encode())
    w.run("install.sh")
    assert open(entry).read().endswith(line), open(entry).read()


@_world
def a_data_folder_with_nothing_of_ours_in_it_is_not_touched(w):
    """No old install there, only the user's files -- and one that happens to
    share a name with something an install used to leave."""
    w.seed_user_data()
    _write(os.path.join(w.data, "README.txt"), b"my own notes")
    w.run("install.sh")
    assert not w.lost(), w.lost()
    mine = os.path.join(w.data, "README.txt")
    assert os.path.isfile(mine) and open(mine, "rb").read() == \
        b"my own notes", "deleted a file because of its name alone"


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("check", "_")) \
            and getattr(fn, "__module__", None) == "__main__":
        results.append(check(name, fn))

print("all installer checks passed" if all(results) else "FAILURES")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
