"""
preset_browser.py — undocked window listing the visualizer's presets in play
order, with sort / rated-shuffle / delete-0★ / reset controls.

Adapted from the Bomsai Player PresetPlaylistWindow to drive the standalone
milkdrop-viz Renderer (see viz_common.Renderer), which exposes the same host
interface: preset_changed, is_rated/set_rated, is_shuffle/toggle_shuffle,
get_preset_playlist, current_order_pos, jump_to_order, sort_order,
reset_all_ratings, delete_zero_rated.
"""
import logging
import os

# Qt comes from viz_common, which picks the binding (PySide6, or PyQt6 when
# this file is running inside Bomsai Player). Importing PyQt6 directly here
# would load a second Qt into the process.
from viz_common import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QAbstractItemView, QMessageBox, QSizePolicy,
    QLineEdit, QTimer, QIcon, QSize, Qt, browser_qss, thumb_path,
    QSlider, QTabWidget, QScrollArea, QFileDialog, QInputDialog, QFrame,
    PRESET_TWEAKS, preset_scalar, preset_driven_keys, patch_preset,
    preset_textures, texture_override_path, theme_colour,
    QComboBox, SPRITE_MOTIONS, SPRITE_BLENDS, MAX_SPRITES,
)

logger = logging.getLogger(__name__)


def _stars(rating: int) -> str:
    r = max(0, min(10, int(rating)))
    return "★" * r + "☆" * (10 - r)


class TweakPanel(QWidget):
    """The drop-down under the browser toolbar: edit the preset on screen.

    Hidden until the toolbar's slider button is pressed, so the browser looks
    exactly as it always has until somebody goes looking. Tabbed because there
    are already two unrelated kinds of edit -- the numbers in the preset, and
    the pictures it samples -- and a third would otherwise have nowhere to go.

    Nothing here writes to the preset file. Every change is applied by handing
    projectM the patched text to play (`projectm_load_preset_data`), so the
    original is untouched until Save as… is pressed, and Revert is a reload.
    """

    #: Slider resolution. The underlying values are floats over ranges as
    #: narrow as 0.1, and a QSlider is integers.
    STEPS = 1000
    #: Debounce before the preset is rebuilt and reloaded. Each reload
    #: recompiles the preset's shaders, so one per drag step is far too many.
    APPLY_MS = 120

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._base = ""          # the preset file's text, never patched
        self._values = {}        # ini key -> current slider value
        self._rows = {}          # ini key -> (slider, value label)
        self._stale = True       # the preset changed while we were hidden

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 6)
        lay.setSpacing(6)

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs)

        # Tab 1 — the numbers.
        self._tweak_body = QWidget()
        self._tweak_lay = QVBoxLayout(self._tweak_body)
        self._tweak_lay.setContentsMargins(4, 4, 4, 4)
        self._tweak_lay.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._tweak_body)
        scroll.setMinimumHeight(190)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tabs.addTab(scroll, "Tweak")

        # Tab 2 — the pictures.
        self._tex_body = QWidget()
        self._tex_lay = QVBoxLayout(self._tex_body)
        self._tex_lay.setContentsMargins(4, 4, 4, 4)
        self._tex_lay.setSpacing(4)
        tex_scroll = QScrollArea()
        tex_scroll.setWidgetResizable(True)
        tex_scroll.setWidget(self._tex_body)
        tex_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tabs.addTab(tex_scroll, "Textures")

        # Tab 3 — pictures laid over the render, rather than inside a preset.
        self._spr_body = QWidget()
        self._spr_lay = QVBoxLayout(self._spr_body)
        self._spr_lay.setContentsMargins(4, 4, 4, 4)
        self._spr_lay.setSpacing(4)
        spr_scroll = QScrollArea()
        spr_scroll.setWidgetResizable(True)
        spr_scroll.setWidget(self._spr_body)
        spr_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._spr_tab = self._tabs.addTab(spr_scroll, "Overlay")

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(6)
        foot.addStretch(1)
        btn_revert = QPushButton("Revert")
        btn_revert.setToolTip("Put the preset back the way it is on disk")
        btn_revert.clicked.connect(self._revert)
        foot.addWidget(btn_revert)
        btn_save = QPushButton("Save as\u2026")
        btn_save.setToolTip("Write this as a new preset beside the original")
        btn_save.clicked.connect(self._save_as)
        foot.addWidget(btn_save)
        lay.addLayout(foot)

        # Revert and Save as\u2026 act on the preset. They mean nothing on the
        # Overlay tab, where nothing being edited belongs to a preset at all,
        # and a Save that quietly ignored the picture on screen would be worse
        # than no button.
        self._foot = (btn_revert, btn_save)
        self._tabs.currentChanged.connect(self._tab_changed)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.APPLY_MS)
        self._timer.timeout.connect(self._apply)

        self._spr_timer = QTimer(self)
        self._spr_timer.setSingleShot(True)
        self._spr_timer.setInterval(self.APPLY_MS)
        self._spr_timer.timeout.connect(self._sprite_flush)

        # Last: the sliders it builds are wired to the timer above.
        self._build_sprites()

    # ── loading ────────────────────────────────────────────────────────

    def mark_stale(self):
        """The preset changed. Rebuilt on show if we are not visible."""
        self._stale = True
        if self.isVisible():
            self.reload()

    def showEvent(self, e):
        if self._stale:
            self.reload()
        super().showEvent(e)

    def reload(self):
        self._stale = False
        try:
            self._base = self._host.preset_text()
        except Exception:
            self._base = ""
        self._values.clear()
        self._build_tweaks()
        self._build_textures()

    def _build_tweaks(self):
        self._rows.clear()
        _clear_layout(self._tweak_lay)

        if not getattr(self._host, "can_tweak", lambda: False)():
            self._tweak_lay.addWidget(_hint(
                "This build of projectM cannot play an edited preset, so the "
                "sliders are unavailable. Everything else still works."))
            self._tweak_lay.addStretch(1)
            return
        if not self._base:
            self._tweak_lay.addWidget(_hint(
                "No preset file to read. Presets loaded from somewhere other "
                "than a folder cannot be tweaked."))
            self._tweak_lay.addStretch(1)
            return

        driven = preset_driven_keys(self._base)
        if driven:
            # In this tab, not the shared footer: it explains the greyed
            # sliders, and under the Textures tab it said nothing about
            # anything on screen.
            self._tweak_lay.addWidget(_hint(
                "Greyed sliders are animated by the preset's own code, which "
                "overwrites the file's value every frame."))
        for key, _var, label, lo, hi, decimals in PRESET_TWEAKS:
            value = preset_scalar(self._base, key)
            if value is None:
                continue        # this preset does not carry the setting
            value = max(lo, min(hi, value))
            self._values[key] = value
            self._tweak_lay.addLayout(
                self._slider_row(key, label, lo, hi, decimals, value,
                                 key in driven))
        if not self._rows and not driven:
            self._tweak_lay.addWidget(_hint(
                "This preset carries none of the settings these sliders "
                "adjust."))
        self._tweak_lay.addStretch(1)

    def _slider_row(self, key, label, lo, hi, decimals, value, is_driven):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        name = QLabel(label)
        # Fixed, not Ignored: an Ignored label gives up all its width to the
        # slider beside it, and every name collapsed to a single letter --
        # "Z", "R", "W". A short slider is fine; an unreadable label is not.
        name.setFixedWidth(96)
        name.setStyleSheet("font-size:12px;")
        row.addWidget(name)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, self.STEPS)
        slider.setValue(int(round((value - lo) / (hi - lo) * self.STEPS)))
        slider.setMinimumWidth(60)
        row.addWidget(slider, 1)

        shown = QLabel(f"{value:.{decimals}f}")
        shown.setFixedWidth(44)
        shown.setStyleSheet("font-size:11px;")
        row.addWidget(shown)

        if is_driven:
            # Not hidden: the preset having its own opinion about zoom is
            # worth seeing, and it is true of about half of them.
            slider.setEnabled(False)
            name.setStyleSheet(f"font-size:12px;color:{theme_colour('muted')};")
            tip = (f"{label} is animated by this preset's own per-frame code, "
                   "which overwrites the file's value every frame. Changing "
                   "it here would have no effect.")
            for w in (name, slider, shown):
                w.setToolTip(tip)
        else:
            def changed(i, k=key, l=lo, h=hi, d=decimals, lbl=shown):
                v = l + (h - l) * i / self.STEPS
                self._values[k] = v
                lbl.setText(f"{v:.{d}f}")
                self._timer.start()
            slider.valueChanged.connect(changed)
            self._rows[key] = (slider, shown)
        return row

    # ── textures ───────────────────────────────────────────────────────

    def _build_textures(self):
        _clear_layout(self._tex_lay)
        names = preset_textures(self._base) if self._base else []
        if not names:
            self._tex_lay.addWidget(_hint(
                "This preset draws only from projectM's own buffers and "
                "noise, so there is no picture in it to replace."))
            self._tex_lay.addStretch(1)
            return
        self._tex_lay.addWidget(_hint(
            "Swap any of these for a picture of your own. The texture pack is "
            "never written to \u2014 the replacement sits in front of it, and "
            "Restore takes it away again."))
        for name in names:
            self._tex_lay.addLayout(self._texture_row(name))
        self._tex_lay.addStretch(1)

    def _texture_row(self, name):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        override = texture_override_path(name)
        label = QLabel(f"{name}  \u2190  {os.path.basename(override)}"
                       if override else name)
        label.setStyleSheet("font-size:12px;")
        label.setToolTip(override or "Stock texture")
        row.addWidget(label, 1)

        pick = QPushButton("Replace\u2026")
        pick.clicked.connect(lambda _=False, n=name: self._replace(n))
        row.addWidget(pick)

        undo = QPushButton("Restore")
        undo.setEnabled(bool(override))
        undo.clicked.connect(lambda _=False, n=name: self._restore(n))
        row.addWidget(undo)
        return row

    def _replace(self, name):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Use a picture as “{name}”", os.path.expanduser("~"),
            "Images (*.jpg *.jpeg *.png *.tga *.bmp *.dds *.dib)")
        if not path:
            return
        why = self._host.swap_texture(name, path)
        if why:
            QMessageBox.warning(self, "Replace texture", why)
        self._rebuild_later(self._build_textures)

    def _restore(self, name):
        self._host.restore_texture(name)
        self._rebuild_later(self._build_textures)

    # ── overlay ────────────────────────────────────────────────────────
    # Not part of any preset, so this tab is built once and rebuilt only when
    # a sprite changes -- never on reload(), which fires on every preset
    # change and would drop the selection mid-adjustment.

    def _rebuild_later(self, fn, then=None):
        """Rebuild a tab after the current signal has finished emitting.

        Rebuilding replaces every widget in the tab, including the button or
        combo whose signal called us. Doing that inline orphans a live sender
        mid-emit and the binding frees it under itself -- which shows up as
        the whole application vanishing with no Python traceback at all,
        because there is no exception involved. Measured: changing a sprite's
        movement killed the app every time.
        """
        def run():
            fn()
            if then is not None:
                then()
        QTimer.singleShot(0, run)

    def _tab_changed(self, index):
        preset_tab = index != self._spr_tab
        for w in self._foot:
            w.setVisible(preset_tab)

    def _build_sprites(self):
        _clear_layout(self._spr_lay)
        if not getattr(self._host, "can_sprites", lambda: False)():
            self._spr_lay.addWidget(_hint(
                "This needs a newer libprojectM than the one loaded. "
                "Everything else here still works."))
            self._spr_lay.addStretch(1)
            return

        self._spr_lay.addWidget(_hint(
            "Lay your own picture over whatever is playing — a logo, a photo, "
            "anything. It stays put while the presets change underneath, and "
            "it is still there next time you open the app."))

        self._spr_list = QListWidget()
        self._spr_list.setMaximumHeight(96)
        self._spr_list.currentRowChanged.connect(self._sprite_selected)
        self._spr_lay.addWidget(self._spr_list)

        entries = self._host.sprites()
        labels = {k: l for k, l, _c in SPRITE_MOTIONS}
        for e in entries:
            name = e.get("image", "")[4:] or e.get("image", "")   # drop spr_
            self._spr_list.addItem(
                f"{name}   ·   {labels.get(e.get('motion'), 'Still')}")

        btns = QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(6)
        add = QPushButton("Add a picture…")
        add.setEnabled(len(entries) < MAX_SPRITES)
        add.clicked.connect(self._add_sprite)
        btns.addWidget(add)
        self._spr_del = QPushButton("Remove")
        self._spr_del.clicked.connect(self._remove_sprite)
        btns.addWidget(self._spr_del)
        clear = QPushButton("Remove all")
        clear.setEnabled(bool(entries))
        clear.clicked.connect(self._clear_sprites)
        btns.addWidget(clear)
        btns.addStretch(1)
        self._spr_lay.addLayout(btns)

        self._spr_motion = QComboBox()
        for _k, label, _c in SPRITE_MOTIONS:
            self._spr_motion.addItem(label)
        self._spr_motion.currentIndexChanged.connect(
            lambda i: self._sprite_set(motion=SPRITE_MOTIONS[i][0]))
        self._spr_lay.addLayout(self._labelled("Movement", self._spr_motion))

        self._spr_blend = QComboBox()
        for _k, label, _b in SPRITE_BLENDS:
            self._spr_blend.addItem(label)
        self._spr_blend.setToolTip(
            "Additive lights the picture up with the preset showing through, "
            "instead of covering it")
        self._spr_blend.currentIndexChanged.connect(
            lambda i: self._sprite_set(blend=SPRITE_BLENDS[i][0]))
        self._spr_lay.addLayout(self._labelled("Blending", self._spr_blend))

        self._spr_size = self._sprite_slider(5, 200, "size", 100.0)
        self._spr_lay.addLayout(self._labelled("Size", self._spr_size))
        self._spr_alpha = self._sprite_slider(0, 100, "alpha", 100.0)
        self._spr_lay.addLayout(self._labelled("Opacity", self._spr_alpha))

        self._spr_lay.addStretch(1)
        if entries:
            self._spr_list.setCurrentRow(0)
        else:
            self._sprite_selected(-1)

    def _labelled(self, text, widget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        name = QLabel(text)
        name.setFixedWidth(96)
        name.setStyleSheet("font-size:12px;")
        row.addWidget(name)
        row.addWidget(widget, 1)
        return row

    def _sprite_slider(self, lo, hi, field, scale):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        # Debounced through the same timer the tweak sliders use: each change
        # destroys and recreates the sprite, and one of those per pixel of
        # drag is pointless work on the render thread.
        s.valueChanged.connect(
            lambda v, f=field, sc=scale: self._sprite_pending(f, v / sc))
        return s

    def _sprite_pending(self, field, value):
        if getattr(self, "_spr_loading", False):
            return
        self._spr_pending = dict(getattr(self, "_spr_pending", {}))
        self._spr_pending[field] = value
        self._spr_timer.start()

    def _sprite_flush(self):
        pending = getattr(self, "_spr_pending", None)
        if pending:
            self._spr_pending = {}
            self._sprite_set(**pending)

    def _sprite_row(self) -> int:
        lst = getattr(self, "_spr_list", None)
        return lst.currentRow() if lst is not None else -1

    def _sprite_selected(self, row):
        entries = self._host.sprites()
        on = 0 <= row < len(entries)
        for w in (getattr(self, "_spr_motion", None),
                  getattr(self, "_spr_blend", None),
                  getattr(self, "_spr_size", None),
                  getattr(self, "_spr_alpha", None),
                  getattr(self, "_spr_del", None)):
            if w is not None:
                w.setEnabled(on)
        if not on:
            return
        e = entries[row]
        # Guarded, or setting the controls to match the selection would read
        # as the user editing it and write the previous sprite's values over
        # the one just clicked.
        self._spr_loading = True
        try:
            keys = [k for k, _l, _c in SPRITE_MOTIONS]
            blends = [k for k, _l, _b in SPRITE_BLENDS]
            self._spr_motion.setCurrentIndex(
                keys.index(e.get("motion", "still"))
                if e.get("motion", "still") in keys else 0)
            self._spr_blend.setCurrentIndex(
                blends.index(e.get("blend", "normal"))
                if e.get("blend", "normal") in blends else 0)
            self._spr_size.setValue(int(round(float(e.get("size", 0.5)) * 100)))
            self._spr_alpha.setValue(int(round(float(e.get("alpha", 1.0)) * 100)))
        finally:
            self._spr_loading = False

    def _sprite_set(self, **fields):
        if getattr(self, "_spr_loading", False):
            return
        row = self._sprite_row()
        if row < 0:
            return
        self._host.update_sprite(row, **fields)
        if "motion" in fields:
            # The list row shows the movement, so it has to be redrawn.
            self._rebuild_later(self._build_sprites,
                                lambda: self._spr_list.setCurrentRow(row))

    def _add_sprite(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Lay a picture over the visuals", os.path.expanduser("~"),
            "Images (*.jpg *.jpeg *.png *.tga *.bmp *.dds *.dib)")
        if not path:
            return
        why = self._host.add_sprite(path)
        if why:
            QMessageBox.warning(self, "Add a picture", why)
            return
        self._rebuild_later(
            self._build_sprites,
            lambda: self._spr_list.setCurrentRow(self._spr_list.count() - 1))

    def _remove_sprite(self):
        row = self._sprite_row()
        if row >= 0 and self._host.remove_sprite(row):
            self._rebuild_later(self._build_sprites)

    def _clear_sprites(self):
        if self._host.sprites() and QMessageBox.question(
                self, "Remove all",
                "Take every picture off the render? The copies this app made "
                "are deleted; your originals are untouched.") \
                == QMessageBox.StandardButton.Yes:
            self._host.clear_sprites()
            self._rebuild_later(self._build_sprites)

    # ── applying ───────────────────────────────────────────────────────

    def current_text(self) -> str:
        return patch_preset(self._base, self._values) if self._base else ""

    def _apply(self):
        text = self.current_text()
        if text:
            self._host.preview_preset(text)

    def _revert(self):
        self.reload()
        try:
            self._host.revert_preset()
        except Exception:
            pass

    def _save_as(self):
        text = self.current_text()
        if not text:
            return
        suggested = os.path.splitext(
            os.path.basename(self._host.current_preset_path()))[0] + " (mine)"
        name, ok = QInputDialog.getText(
            self, "Save preset as", "Name for the new preset:", text=suggested)
        if not ok:
            return
        why = self._host.save_preset_as(text, name)
        if why:
            QMessageBox.warning(self, "Save preset", why)


def _clear_layout(layout):
    """Empty a layout, widgets and nested layouts alike."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{theme_colour('muted')};font-size:11px;")
    return lbl


class PresetPlaylistWindow(QWidget):
    #: Thumbnail size in the list. 16:9, so a widescreen capture is not
    #: letterboxed into it.
    ICON_SIZE = QSize(96, 54)

    def __init__(self, host, on_shuffle=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._host = host
        self._on_shuffle = on_shuffle      # callback to toggle visual shuffle
        self.setWindowTitle("Preset Browser")
        # 380 was too narrow for the toolbar even before the tweak button was
        # added -- every label was truncated to three or four characters at the
        # default size. Measured against the eight buttons it now carries.
        self.resize(470, 620)
        # Built in viz_common so every themed surface shares one palette.
        self.setStyleSheet(browser_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._title = QLabel("—")
        self._title.setObjectName("title")
        # Deliberately NOT word-wrapped. A wrapping title grows to two lines on
        # a long preset name and shoves the toolbar -- and every list row --
        # down by a line, so buttons move out from under the pointer as presets
        # rotate. The name is elided to fit instead; the full text is the
        # tooltip, and the list row below always carries it in full.
        self._title.setWordWrap(False)
        # Ignored width, so the label takes whatever the layout gives it and
        # never asks for more. Without this a non-wrapping QLabel reports the
        # full text width as its size hint and *widens the window* to fit a long
        # preset name -- which changes the elision budget, which changes the
        # text, which changes the hint again. The window would settle at a
        # different width per preset and the elision was wrong every other one.
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Fixed)
        self._title.setMinimumWidth(0)
        self._title_parts = None       # (name, rating), rendered on resize too
        lay.addWidget(self._title)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(6)
        self._btn_shuffle = QPushButton("🔀  Shuffle")
        self._btn_shuffle.setCheckable(True)
        self._btn_shuffle.setToolTip("List shuffle — shuffled order, shows all presets")
        self._btn_shuffle.clicked.connect(self._toggle_shuffle)
        bar.addWidget(self._btn_shuffle)

        self._btn_rated = QPushButton("🎯  Rated")
        self._btn_rated.setCheckable(True)
        self._btn_rated.setToolTip("Rated shuffle — play higher-rated presets more often")
        self._btn_rated.clicked.connect(self._toggle_rated)
        bar.addWidget(self._btn_rated)

        btn_reset = QPushButton("Reset ★")
        btn_reset.setToolTip("Reset all preset ratings to default")
        btn_reset.clicked.connect(self._reset_ratings)
        bar.addWidget(btn_reset)

        btn_delzero = QPushButton("🗑 0★")
        btn_delzero.setToolTip("Delete 0-star presets from disk")
        btn_delzero.clicked.connect(self._delete_zero)
        bar.addWidget(btn_delzero)

        btn_sort_name = QPushButton("A→Z")
        btn_sort_name.setToolTip("Sort presets by name")
        btn_sort_name.clicked.connect(lambda: self._sort("name"))
        bar.addWidget(btn_sort_name)

        btn_sort_rating = QPushButton("★ Rating")
        btn_sort_rating.setToolTip(
            "Sort by rating (highest first; name breaks ties)")
        btn_sort_rating.clicked.connect(lambda: self._sort("rating"))
        bar.addWidget(btn_sort_rating)

        btn_refresh = QPushButton("↻")
        btn_refresh.setToolTip("Refresh list")
        btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(btn_refresh)

        # The drop-down. Collapsed by default, so the browser is unchanged
        # for anyone who never opens it.
        self._btn_tweak = QPushButton("🎛")
        self._btn_tweak.setFixedWidth(34)
        self._btn_tweak.setCheckable(True)
        self._btn_tweak.setToolTip(
            "Tweak the visual on screen — sliders, and swap its textures "
            "for your own pictures")
        self._btn_tweak.toggled.connect(self._toggle_tweaks)
        bar.addWidget(self._btn_tweak)

        bar.addStretch(1)
        lay.addLayout(bar)

        self._tweaks = TweakPanel(host)
        self._tweaks.hide()
        lay.addWidget(self._tweaks)

        self._search = QLineEdit()
        self._search.setObjectName("search")
        self._search.setPlaceholderText("Search presets by name…")
        self._search.setClearButtonEnabled(True)
        # Debounced, not live. A library is ~10,000 rows and hiding every one
        # of them takes long enough to swallow the next keystroke, so typing a
        # word would land one letter behind. 150ms is under the gap between
        # keystrokes while typing and shorter than the pause after finishing.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filter)
        self._search.textChanged.connect(self._filter_timer.start)
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        # Zero until the first thumbnail turns up. A fresh install has an
        # empty cache, and a non-zero icon size there is a 96px empty gutter
        # down a list of ten thousand rows.
        self._list.setIconSize(QSize(0, 0))
        self._list.itemDoubleClicked.connect(self._on_activate)
        #: Rows whose thumbnail has already been looked for. Rows scroll into
        #: view far more often than they change, and a miss is as worth
        #: remembering as a hit -- most presets have no thumbnail yet.
        self._iconed = set()
        self._list.verticalScrollBar().valueChanged.connect(
            self._load_visible_icons)
        lay.addWidget(self._list, stretch=1)

        if hasattr(host, "preset_changed"):
            try:
                host.preset_changed.connect(self._on_preset_changed)
            except Exception:
                pass
        if hasattr(host, "playlist_changed"):
            try:
                host.playlist_changed.connect(self.refresh)
            except Exception:
                pass

        self.refresh()

    # ── Public ─────────────────────────────────────────────────────────

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)

    def refresh(self):
        """Rebuild the list from the host's current play order."""
        try:
            rated = bool(self._host.is_rated())
        except Exception:
            rated = False
        self._btn_rated.blockSignals(True)
        self._btn_rated.setChecked(rated)
        self._btn_rated.blockSignals(False)

        if rated:
            # Rated shuffle is random — there's no meaningful list order, so
            # just show the current preset.
            self._list.clear()
            self._list.addItem(getattr(self, "_current_row_text", "Rated shuffle"))
            self._btn_shuffle.setChecked(False)
            return

        entries = self._host.get_preset_playlist()
        self._entries = entries        # _highlight needs the rating, not row text
        self._list.clear()
        self._list.addItems([f"{name}    {r}/10" for name, r in entries])
        try:
            shuf = bool(self._host.is_shuffle())
        except Exception:
            shuf = False
        self._btn_shuffle.blockSignals(True)
        self._btn_shuffle.setChecked(shuf)
        self._btn_shuffle.blockSignals(False)
        self._iconed.clear()          # rows were just replaced
        self._apply_filter()
        self._highlight(self._host.current_order_pos())

    # ── Internal ───────────────────────────────────────────────────────

    def _set_title(self, name: str, rating: int):
        self._title_parts = (name, int(rating))
        self._render_title()

    def _render_title(self):
        """Fit 'name  ★★★★★☆☆☆☆☆  7/10' onto one line, eliding only the name.

        The rating is the half that must survive, so the suffix is measured
        first and the name gets whatever is left.
        """
        if not self._title_parts:
            return
        name, rating = self._title_parts
        suffix = f"    {_stars(rating)}  {rating}/10"
        fm = self._title.fontMetrics()
        # contentsRect() accounts for the stylesheet's padding; width() does not.
        box = self._title.contentsRect().width() or (self._title.width() - 20)
        # The 8px is slack, not superstition: reserving exactly the measured
        # suffix width left one pixel spare and the final glyph still clipped,
        # and Windows renders this line in a different face whose star glyphs
        # measure differently again.
        avail = max(40, box - fm.horizontalAdvance(suffix) - 8)
        elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, avail)
        self._title.setText(elided + suffix)
        self._title.setToolTip(name)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render_title()      # the elision budget just changed

    def _apply_filter(self):
        """Hide the rows that do not match the search box.

        Hiding rather than rebuilding the list, so a row's index stays equal to
        its playlist position. Every other method here -- jump_to_order,
        _highlight, the double-click handler -- indexes by that position, and a
        filtered rebuild would have needed a row/position map in all of them.
        """
        q = self._search.text().strip().lower()
        shown = 0
        for i in range(self._list.count()):
            hit = (not q) or q in self._list.item(i).text().lower()
            self._list.setRowHidden(i, not hit)
            shown += hit
        total = self._list.count()
        self.setWindowTitle("Preset Browser" if shown == total
                            else f"Preset Browser — {shown} of {total}")
        self._load_visible_icons()      # a different set of rows is on screen

    def _load_visible_icons(self):
        """Give the rows on screen their thumbnails, once each.

        Building ten thousand QIcons up front costs seconds and most of them
        are never looked at, so they load as rows scroll into view. Presets
        with no thumbnail yet simply have none: the cache fills in as they are
        watched, which is the point of it.
        """
        view = self._list.viewport().rect()
        first = self._list.indexAt(view.topLeft()).row()
        if first < 0:
            return
        last = self._list.indexAt(view.bottomLeft()).row()
        if last < 0:
            last = self._list.count() - 1
        entries = getattr(self, "_entries", ())
        for i in range(first, min(last + 2, self._list.count())):
            if i in self._iconed or i >= len(entries):
                continue
            self._iconed.add(i)
            path = thumb_path(entries[i][0])
            if os.path.isfile(path):
                self._list.item(i).setIcon(QIcon(path))
                if self._list.iconSize().width() == 0:
                    self._list.setIconSize(self.ICON_SIZE)
                    self._make_room_for_icons()

    def _make_room_for_icons(self):
        """Give the rows the height a thumbnail needs.

        uniformItemSizes is on because measuring ten thousand rows one at a
        time costs half a second every time the list is rebuilt, and it means
        the view measures once and reuses that height for every row. Raising
        the icon size afterwards does not re-measure -- nor does setting an
        item's size hint, nor clearing and refilling the list -- so if the row
        at the top has no thumbnail, which is the normal case, every thumbnail
        below it was drawn at 54px into a 26px row and spilled over its
        neighbours. A stylesheet minimum is the one thing that does get
        through, and it survives a rebuild, so it is set once and left.
        """
        self._list.setStyleSheet(
            "QListWidget::item { min-height: %dpx; }" % self.ICON_SIZE.height())

    def _highlight(self, pos: int):
        if 0 <= pos < self._list.count():
            self._list.setCurrentRow(pos)
            # Not while it is filtered out: scrollToItem on a hidden row jumps
            # the view to wherever it would have been, which reads as the list
            # losing your place every time the preset rotates mid-search.
            if not self._list.isRowHidden(pos):
                self._list.scrollToItem(
                    self._list.item(pos),
                    QAbstractItemView.ScrollHint.PositionAtCenter)
            entries = getattr(self, "_entries", None)
            if entries and 0 <= pos < len(entries):
                self._set_title(*entries[pos])

    def _on_activate(self, item: QListWidgetItem):
        self._host.jump_to_order(self._list.row(item))

    def _on_preset_changed(self, name: str, rating: int):
        self._current_row_text = f"{name}    {rating}/10"
        self._set_title(name, rating)
        # The panel edits whatever is on screen, so it has to follow. It
        # rebuilds lazily when hidden, which is most of the time.
        self._tweaks.mark_stale()
        try:
            if self._host.is_rated():
                self._list.clear()
                self._list.addItem(self._current_row_text)
                return
        except Exception:
            pass
        # Order length may have changed (reshuffle / preset reload)
        try:
            entries_len = len(self._host.get_preset_playlist())
        except Exception:
            entries_len = self._list.count()
        if entries_len != self._list.count():
            self.refresh()
            return
        self._highlight(self._host.current_order_pos())

    def _toggle_tweaks(self, on: bool):
        self._tweaks.setVisible(on)
        if on:
            # The list gives up the room rather than the window growing: the
            # browser sits beside the visuals and a window that jumps taller
            # when a panel opens lands on top of them.
            self._tweaks.reload()

    def _sort(self, by: str):
        try:
            self._host.sort_order(by)
        except Exception:
            pass
        self.refresh()

    def _toggle_shuffle(self, _checked=False):
        try:                                  # list shuffle and rated are exclusive
            self._host.set_rated(False)
        except Exception:
            pass
        if self._on_shuffle is not None:
            self._on_shuffle()
        else:
            try:
                self._host.toggle_shuffle()
            except Exception:
                pass
        self.refresh()

    def _toggle_rated(self, _checked=False):
        try:
            self._host.set_rated(self._btn_rated.isChecked())
        except Exception:
            pass
        self.refresh()

    def _reset_ratings(self):
        if QMessageBox.question(
                self, "Reset ratings",
                "Reset ALL preset ratings to the default?"
                ) == QMessageBox.StandardButton.Yes:
            self._host.reset_all_ratings()
            self.refresh()

    def _delete_zero(self):
        if QMessageBox.question(
                self, "Delete 0-star presets",
                "Permanently DELETE all 0-star presets from disk?\n"
                "This cannot be undone."
                ) == QMessageBox.StandardButton.Yes:
            n = self._host.delete_zero_rated()
            QMessageBox.information(self, "Deleted",
                                    f"Deleted {n} preset file(s) from disk.")
            self.refresh()
