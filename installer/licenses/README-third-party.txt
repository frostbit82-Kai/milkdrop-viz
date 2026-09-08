Third-party components shipped with BomsAI Visualizer
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

FFmpeg — LGPL-3.0 (an LGPL build; no GPL components)
    FFMPEG-LGPL-3.0.txt
    Shipped as ffmpeg.exe beside the application and run as a separate
    process, never linked into it. It is an unmodified upstream build with
    no GPL components -- notably no libx264, which is why recorded clips use
    Media Foundation (h264_mf) for H.264. The build is packaged by
    https://github.com/BtbN/FFmpeg-Builds from unmodified FFmpeg sources at
    https://git.ffmpeg.org/ffmpeg.git ; replacing ffmpeg.exe with your own
    LGPL build of the same version is a matter of swapping that file.

SDL2 — zlib licence
PyOpenGL — BSD
PortAudio (via sounddevice) — MIT
NumPy — BSD

Presets and textures are NOT part of this package. They are the work of
their own authors and are downloaded separately.
