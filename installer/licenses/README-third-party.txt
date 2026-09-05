Third-party components shipped with MilkDrop Visualizer
=======================================================

projectM 4 — LGPL-2.1
    projectM-LGPL-2.1.txt, projectM-COPYING.txt
    Loaded at runtime as a separate shared library (libprojectM-4.so.4 /
    projectM-4.dll) and never linked statically. Replacing it with your own
    build of the same version is a matter of swapping that file.

Qt 6 and PySide6 (Qt for Python) — LGPL-3.0
    Qt-PySide6-LGPL-3.0.txt, and Qt-PySide6-GPL-3.0.txt, which the LGPL
    refers to.
    The Qt libraries ship as separate shared libraries beside the
    application, so they can be replaced with a compatible build.
    Sources: https://download.qt.io/  and  https://download.qt.io/official_releases/QtForPython/

SDL2 — zlib licence
PyOpenGL — BSD
PortAudio (via sounddevice) — MIT
NumPy — BSD

Presets and textures are NOT part of this package. They are the work of
their own authors and are downloaded separately.
