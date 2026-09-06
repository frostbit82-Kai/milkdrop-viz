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

# Qt comes from viz_common, which picks the binding (PySide6, or PyQt6 when
# this file is running inside Bomsai Player). Importing PyQt6 directly here
# would load a second Qt into the process.
from viz_common import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QAbstractItemView, QMessageBox, Qt,
    browser_qss,
)

logger = logging.getLogger(__name__)


def _stars(rating: int) -> str:
    r = max(0, min(10, int(rating)))
    return "★" * r + "☆" * (10 - r)


class PresetPlaylistWindow(QWidget):
    def __init__(self, host, on_shuffle=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._host = host
        self._on_shuffle = on_shuffle      # callback to toggle visual shuffle
        self.setWindowTitle("Preset Browser")
        self.resize(380, 580)
        # Built in viz_common so every themed surface shares one palette.
        self.setStyleSheet(browser_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._title = QLabel("—")
        self._title.setObjectName("title")
        self._title.setWordWrap(True)
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
        bar.addStretch(1)
        lay.addLayout(bar)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.itemDoubleClicked.connect(self._on_activate)
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
        self._list.clear()
        self._list.addItems([f"{name}    {r}/10" for name, r in entries])
        try:
            shuf = bool(self._host.is_shuffle())
        except Exception:
            shuf = False
        self._btn_shuffle.blockSignals(True)
        self._btn_shuffle.setChecked(shuf)
        self._btn_shuffle.blockSignals(False)
        self._highlight(self._host.current_order_pos())

    # ── Internal ───────────────────────────────────────────────────────

    def _highlight(self, pos: int):
        if 0 <= pos < self._list.count():
            self._list.setCurrentRow(pos)
            self._list.scrollToItem(self._list.item(pos),
                                    QAbstractItemView.ScrollHint.PositionAtCenter)
            self._title.setText(self._list.item(pos).text())

    def _on_activate(self, item: QListWidgetItem):
        self._host.jump_to_order(self._list.row(item))

    def _on_preset_changed(self, name: str, rating: int):
        self._current_row_text = f"{name}    {rating}/10"
        self._title.setText(f"{name}    {_stars(rating)}  {rating}/10")
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
