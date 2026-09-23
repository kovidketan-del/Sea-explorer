"""Desktop control window for Sea Explorer.

The window opens without a phone. ADB work and gameplay run on workers so the
connection controls, checkpoint list, and Stop button stay responsive.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from checkpoint_state import CHECKPOINTS, CheckpointStore
from connection_manager import ConnectionManager, DeviceConnectionError
from sea_explorer_bot import (
    ADB,
    BotError,
    ROOT,
    STATE_PATH,
    StopRequested,
    load_config,
    log,
    SeaExplorerBot,
)


SETTINGS_PATH = ROOT / "ui_settings.json"
CHECKPOINT_PATH = ROOT / "checkpoint_state.json"
LOG_PATH = ROOT / "sea_explorer.log"
ICON_PATH = ROOT / "sea_explorer.ico"

COLORS = {
    "bg": "#071923",
    "panel": "#0F2A37",
    "panel2": "#143544",
    "field": "#0A222D",
    "line": "#285361",
    "text": "#F1FAFC",
    "muted": "#A2BEC6",
    "aqua": "#59DEC7",
    "aqua_dark": "#143D40",
    "gold": "#F7CD79",
    "gold_dark": "#554529",
    "red": "#F39C9F",
    "red_dark": "#493039",
}


def _read_settings() -> dict:
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict) -> None:
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, SETTINGS_PATH)


def _find_scrcpy(configured: str = "") -> str:
    candidates: list[Path] = []
    if configured:
        candidate = Path(os.path.expandvars(configured)).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError("Configured scrcpy.exe was not found. Check Settings.")
        return str(candidate.resolve())
    found = shutil.which("scrcpy")
    if found:
        candidates.append(Path(found))
    candidates += [
        ROOT / "scrcpy.exe",
        ROOT.parent / "scrcpy" / "scrcpy.exe",
        Path.home() / "scrcpy" / "scrcpy.exe",
    ]
    candidates.extend(sorted(Path.home().glob("scrcpy-win64-*/scrcpy.exe"), reverse=True))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    raise FileNotFoundError("scrcpy.exe was not found. Set its path in Settings.")


class SeaExplorerWindow:
    def __init__(self, root: tk.Tk, *, autostart: bool = False):
        self.root = root
        self.root.title("Sea Explorer · Control Deck")
        if ICON_PATH.is_file():
            try:
                self.root.iconbitmap(default=str(ICON_PATH))
            except tk.TclError:
                pass
        self.root.configure(bg=COLORS["bg"])
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        self.mirror_width = 340 if screen_w >= 1000 else max(210, min(300, int(screen_w * 0.32)))
        width = min(990, max(360, screen_w - self.mirror_width - 60))
        height = max(420, min(900, screen_h - 115))
        self.compact = width < 880
        origin = 12 if self.compact else 24
        self.root.geometry(f"{width}x{height}+{origin}+{origin}")
        self.root.minsize(min(width, 360 if self.compact else 880), min(height, 420))

        self.settings = _read_settings()
        self.mode = tk.StringVar(value=self.settings.get("mode", "usb"))
        if self.mode.get() not in {"usb", "wireless"}:
            self.mode.set("usb")
        self.usb_serial = tk.StringVar(value=self.settings.get("usb_serial", ""))
        self.wireless_address = tk.StringVar(value=self.settings.get("wireless_address", ""))
        self.pair_address = tk.StringVar()
        self.pair_code = tk.StringVar()
        self.events: queue.Queue[tuple] = queue.Queue()
        self.checkpoints = CheckpointStore(CHECKPOINT_PATH)
        self.connected_serial: str | None = None
        self.connected_mode: str | None = None
        self.busy = False
        self.running = False
        self.closing = False
        self.worker: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.card_widgets: dict[int, tuple[tk.Label, tk.Button]] = {}
        self.log_position = LOG_PATH.stat().st_size if LOG_PATH.exists() else 0
        self._last_progress_read = 0.0
        self.usb_serial.trace_add("write", lambda *_: self._update_controls())
        self.wireless_address.trace_add("write", lambda *_: self._update_controls())

        self._set_ttk_style()
        self._build()
        self._update_checkpoints()
        self._update_progress()
        self._update_controls()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(180, self._periodic)
        if autostart:
            self.root.after(400, self._autostart)

    def _set_ttk_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Sea.TCombobox",
            fieldbackground=COLORS["field"],
            background=COLORS["panel2"],
            foreground=COLORS["text"],
            bordercolor=COLORS["line"],
            arrowcolor=COLORS["aqua"],
            padding=5,
        )
        style.map(
            "Sea.TCombobox",
            fieldbackground=[("readonly", COLORS["field"])],
            foreground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "Sea.Vertical.TScrollbar",
            background=COLORS["panel2"],
            troughcolor=COLORS["field"],
            bordercolor=COLORS["field"],
            arrowcolor=COLORS["muted"],
            lightcolor=COLORS["panel2"],
            darkcolor=COLORS["panel2"],
            width=9,
        )

    def _label(self, parent, text, *, size=10, color="text", weight="normal", **kwargs):
        return tk.Label(
            parent,
            text=text,
            bg=parent.cget("bg"),
            fg=COLORS.get(color, color),
            font=("Segoe UI", size, weight),
            anchor="w",
            **kwargs,
        )

    def _button(self, parent, text, command, *, kind="quiet", width=None):
        schemes = {
            "quiet": (COLORS["panel2"], COLORS["text"], COLORS["line"]),
            "aqua": (COLORS["aqua"], COLORS["bg"], COLORS["aqua"]),
            "gold": (COLORS["gold"], COLORS["bg"], COLORS["gold"]),
            "danger": (COLORS["red_dark"], COLORS["red"], COLORS["red"]),
        }
        bg, fg, active = schemes[kind]
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=COLORS["bg"],
            disabledforeground=COLORS["muted"],
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Segoe UI Semibold", 10),
            padx=12,
            pady=9,
        )

    def _entry(self, parent, variable, *, show=None):
        return tk.Entry(
            parent,
            textvariable=variable,
            show=show,
            bg=COLORS["field"],
            fg=COLORS["text"],
            insertbackground=COLORS["aqua"],
            selectbackground=COLORS["aqua_dark"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["aqua"],
            font=("Segoe UI", 10),
        )

    def _panel(self, parent, *, padding=14):
        outer = tk.Frame(parent, bg=COLORS["line"])
        inner = tk.Frame(outer, bg=COLORS["panel"], padx=padding, pady=padding)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def _build(self):
        margin = 12 if self.compact else 24
        header = tk.Frame(self.root, bg=COLORS["bg"], padx=margin, pady=11 if self.compact else 13)
        header.pack(fill="x")
        mark = tk.Canvas(header, width=45, height=45, bg=COLORS["bg"], highlightthickness=0)
        mark.pack(side="left", padx=(0, 13))
        mark.create_oval(2, 2, 43, 43, fill=COLORS["aqua_dark"], outline=COLORS["aqua"], width=2)
        mark.create_text(22, 22, text="≈", fill=COLORS["aqua"], font=("Segoe UI Symbol", 27, "bold"))
        title_wrap = tk.Frame(header, bg=COLORS["bg"])
        title_wrap.pack(side="left", fill="x", expand=True)
        self._label(title_wrap, "SEA EXPLORER", size=15 if self.compact else 20, weight="bold").pack(anchor="w")
        if not self.compact:
            self._label(title_wrap, "Control deck  /  depth run", size=10, color="muted").pack(anchor="w")
        self.settings_btn = self._button(header, "⚙" if self.compact else "⚙  Settings", self._open_settings)
        self.settings_btn.pack(side="right", padx=(10, 0))
        self.connection_pill = self._label(header, "●  OFFLINE" if self.compact else "●  DISCONNECTED", size=9, color="gold", weight="bold")
        self.connection_pill.pack(side="right", padx=(8, 8))
        self.header_stop_btn = self._button(header, "■  Stop", self._stop_bot, kind="danger")
        self.header_stop_btn.configure(pady=5)

        metrics = tk.Frame(self.root, bg=COLORS["bg"], padx=margin)
        metrics.pack(fill="x", pady=(0, 9))
        for index in range(3):
            metrics.grid_columnconfigure(index, weight=1, uniform="metrics")
        self.oxygen_value, self.oxygen_note = self._metric(metrics, 0, "OXYGEN" if self.compact else "OXYGEN TARGET", "—", "of 2,000" if self.compact else "Saved upgrade estimate")
        self.runs_value, self.runs_note = self._metric(metrics, 1, "RUNS" if self.compact else "RUNS COMPLETED", "—", "Completed" if self.compact else "From bot progress")
        self.checkpoint_value, self.checkpoint_note = self._metric(metrics, 2, "CHECKPOINTS" if self.compact else "DEPTH CHECKPOINTS", "0 / 8", "Next 300 m" if self.compact else "Manually confirmed")

        body = tk.Frame(self.root, bg=COLORS["bg"], padx=margin)
        body.pack(fill="both", expand=True, pady=(0, 13))
        body.grid_propagate(False)
        if self.compact:
            body.grid_columnconfigure(0, weight=1)
            body.grid_rowconfigure(1, weight=1)
            tab_bar = tk.Frame(body, bg=COLORS["bg"])
            tab_bar.grid(row=0, column=0, sticky="ew", pady=(0, 7))
            self.control_tab = self._button(tab_bar, "Connection & dive", lambda: self._compact_page("control"))
            self.control_tab.configure(pady=5)
            self.control_tab.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self.checkpoint_tab = self._button(tab_bar, "Checkpoints", lambda: self._compact_page("checkpoints"))
            self.checkpoint_tab.configure(pady=5)
            self.checkpoint_tab.pack(side="left", fill="x", expand=True)
        else:
            body.grid_columnconfigure(0, minsize=345, weight=4)
            body.grid_columnconfigure(1, minsize=500, weight=6)
            body.grid_rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=COLORS["bg"])
        left.grid(row=1 if self.compact else 0, column=0, sticky="nsew", padx=(0, 0 if self.compact else 12))
        left_scroll = ttk.Scrollbar(left, orient="vertical", style="Sea.Vertical.TScrollbar")
        left_scroll.pack(side="right", fill="y")
        self.left_canvas = tk.Canvas(left, bg=COLORS["bg"], highlightthickness=0)
        self.left_canvas.pack(side="left", fill="both", expand=True)
        self.left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.configure(command=self.left_canvas.yview)
        left_inner = tk.Frame(self.left_canvas, bg=COLORS["bg"])
        left_window = self.left_canvas.create_window(0, 0, anchor="nw", window=left_inner)
        left_inner.bind("<Configure>", lambda _: self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all")))
        self.left_canvas.bind("<Configure>", lambda e: self.left_canvas.itemconfigure(left_window, width=e.width))
        self.left_canvas.bind("<MouseWheel>", self._scroll_left)
        left_inner.bind("<MouseWheel>", self._scroll_left)
        right = tk.Frame(body, bg=COLORS["bg"])
        right.grid(row=1 if self.compact else 0, column=0 if self.compact else 1, sticky="nsew")
        self.left_area = left
        self.right_area = right

        self._build_connection(left_inner)
        self._build_controls(left_inner)
        self._build_activity(left_inner)
        self._build_checkpoints(right)
        if self.compact:
            self._compact_page("control")

        footer = tk.Frame(self.root, bg=COLORS["bg"], padx=margin, pady=10)
        footer.pack(fill="x")
        if self.compact:
            self.footer_action_btn = self._button(footer, "▶  Start", self._start_bot, kind="gold")
            self.footer_action_btn.configure(pady=5)
        self.status = self._label(
            footer, "Choose USB or Wireless, then connect a device.", size=9, color="muted"
        )
        self.status.pack(side="left", fill="x", expand=True)
        if not self.compact:
            self._label(footer, "LOCAL CONTROL  ·  SEA EXPLORER", size=8, color="muted").pack(side="right")

    def _compact_page(self, page: str):
        if not self.compact:
            return
        if page == "checkpoints":
            self.left_area.grid_remove()
            self.right_area.grid()
        else:
            self.right_area.grid_remove()
            self.left_area.grid()
        self.control_tab.configure(bg=COLORS["aqua_dark"] if page == "control" else COLORS["panel2"])
        self.checkpoint_tab.configure(bg=COLORS["aqua_dark"] if page == "checkpoints" else COLORS["panel2"])

    def _metric(self, parent, column, caption, value, note):
        outer, inner = self._panel(parent, padding=8 if self.compact else 11)
        outer.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0))
        self._label(inner, caption, size=8, color="muted", weight="bold").pack(anchor="w")
        val = self._label(inner, value, size=13 if self.compact else 18, color="aqua" if column == 0 else "text", weight="bold")
        val.pack(anchor="w")
        sub = self._label(inner, note, size=8, color="muted")
        sub.pack(anchor="w")
        return val, sub

    def _build_connection(self, parent):
        outer, panel = self._panel(parent)
        outer.pack(fill="x", pady=(0, 11))
        top = tk.Frame(panel, bg=COLORS["panel"])
        top.pack(fill="x", pady=(0, 7))
        self._label(top, "Connection", size=14, weight="bold").pack(side="left")
        self._label(top, "01 / SETUP", size=8, color="muted", weight="bold").pack(side="right")

        tabs = tk.Frame(panel, bg=COLORS["field"])
        tabs.pack(fill="x", pady=(0, 9))
        self.usb_tab = self._button(tabs, "USB cable", lambda: self._switch_mode("usb"))
        self.usb_tab.configure(pady=5)
        self.usb_tab.pack(side="left", fill="x", expand=True, padx=2, pady=2)
        self.wireless_tab = self._button(tabs, "Wireless", lambda: self._switch_mode("wireless"))
        self.wireless_tab.configure(pady=5)
        self.wireless_tab.pack(side="left", fill="x", expand=True, padx=2, pady=2)
        self.connection_fields = tk.Frame(panel, bg=COLORS["panel"])
        self.connection_fields.pack(fill="x")
        self._render_connection_fields()

    def _switch_mode(self, mode: str):
        if self.running or self.busy:
            return
        self.mode.set(mode)
        self.settings["mode"] = mode
        self._persist_settings()
        self._render_connection_fields()
        self._update_controls()

    def _render_connection_fields(self):
        for child in self.connection_fields.winfo_children():
            child.destroy()
        active = COLORS["aqua_dark"]
        self.usb_tab.configure(bg=active if self.mode.get() == "usb" else COLORS["panel2"])
        self.wireless_tab.configure(bg=active if self.mode.get() == "wireless" else COLORS["panel2"])
        box = self.connection_fields
        if self.mode.get() == "usb":
            self._label(box, "AUTHORIZED USB DEVICE", size=8, color="muted", weight="bold").pack(anchor="w", pady=(0, 5))
            row = tk.Frame(box, bg=COLORS["panel"])
            row.pack(fill="x")
            self.usb_combo = ttk.Combobox(row, textvariable=self.usb_serial, state="readonly", style="Sea.TCombobox")
            self.usb_combo.pack(side="left", fill="x", expand=True)
            self.refresh_btn = self._button(row, "Refresh", self._refresh_usb)
            self.refresh_btn.pack(side="left", padx=(7, 0))
            self.connect_btn = self._button(box, "Connect USB device  →", self._connect_usb, kind="aqua")
            self.connect_btn.configure(pady=6)
            self.connect_btn.pack(fill="x", pady=(7, 0))
            self._label(box, "Unlock the phone and allow USB debugging first.", size=8, color="muted").pack(anchor="w", pady=(5, 0))
        else:
            self._label(box, "CONNECT ADDRESS  ·  IP:PORT", size=8, color="muted", weight="bold").pack(anchor="w", pady=(0, 5))
            self._entry(box, self.wireless_address).pack(fill="x", ipady=7)
            self.connect_btn = self._button(box, "Connect wirelessly  →", self._connect_wireless, kind="aqua")
            self.connect_btn.configure(pady=6)
            self.connect_btn.pack(fill="x", pady=(8, 11))
            self._label(box, "FIRST-TIME PAIRING  ·  ANDROID WIRELESS DEBUGGING", size=8, color="muted", weight="bold").pack(anchor="w", pady=(0, 5))
            pair_row = tk.Frame(box, bg=COLORS["panel"])
            pair_row.pack(fill="x")
            self._entry(pair_row, self.pair_address).pack(side="left", fill="x", expand=True, ipady=6)
            code = self._entry(pair_row, self.pair_code, show="●")
            code.configure(width=8)
            code.pack(side="left", padx=(6, 0), ipady=6)
            self.pair_btn = self._button(box, "Pair using six-digit code", self._pair_wireless)
            self.pair_btn.pack(fill="x", pady=(7, 0))
            self._label(box, "Pairing and connection can use different ports.", size=8, color="muted").pack(anchor="w", pady=(7, 0))
        self._update_controls()

    def _build_controls(self, parent):
        outer, panel = self._panel(parent)
        outer.pack(fill="x", pady=(0, 11))
        top = tk.Frame(panel, bg=COLORS["panel"])
        top.pack(fill="x", pady=(0, 4))
        self._label(top, "Dive control", size=14, weight="bold").pack(side="left")
        self._label(top, "02 / RUN", size=8, color="muted", weight="bold").pack(side="right")
        self.run_summary = self._label(panel, "Connect a device to begin.", size=9, color="muted", wraplength=300, justify="left")
        self.run_summary.pack(anchor="w", pady=(0, 6))
        buttons = tk.Frame(panel, bg=COLORS["panel"])
        buttons.pack(fill="x")
        self.start_btn = self._button(buttons, "▶  Start dive", self._start_bot, kind="gold")
        self.start_btn.configure(pady=6)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.stop_btn = self._button(buttons, "■  Stop", self._stop_bot, kind="danger")
        self.stop_btn.configure(pady=6)
        self.stop_btn.pack(side="left", fill="x", expand=True)

    def _build_activity(self, parent):
        outer, panel = self._panel(parent, padding=10)
        outer.pack(fill="both", expand=True)
        top = tk.Frame(panel, bg=COLORS["panel"])
        top.pack(fill="x", pady=(0, 6))
        self._label(top, "Activity", size=13, weight="bold").pack(side="left")
        self._label(top, "LIVE LOG", size=8, color="muted", weight="bold").pack(side="right")
        self.log_text = tk.Text(
            panel,
            bg=COLORS["field"],
            fg=COLORS["muted"],
            insertbackground=COLORS["aqua"],
            wrap="word",
            relief="flat",
            borderwidth=0,
            font=("Consolas", 8),
            padx=9,
            pady=8,
            height=4,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)
        self._append_activity("Ready. Connect a device when available.")

    def _build_checkpoints(self, parent):
        outer, panel = self._panel(parent, padding=17)
        outer.pack(fill="both", expand=True)
        heading = tk.Frame(panel, bg=COLORS["panel"])
        heading.pack(fill="x")
        heading_left = tk.Frame(heading, bg=COLORS["panel"])
        heading_left.pack(side="left", fill="x", expand=True)
        self._label(heading_left, "Depth checkpoints", size=16, weight="bold").pack(anchor="w")
        self._label(heading_left, "Mark a target after the game confirms it.", size=9, color="muted").pack(anchor="w", pady=(2, 0))
        self.jump_btn = self._button(heading, "Jump to next ↓", self._jump_to_next)
        self.jump_btn.pack(side="right")
        accent_line = tk.Frame(panel, bg=COLORS["line"], height=1)
        accent_line.pack(fill="x", pady=(13, 10))
        canvas_frame = tk.Frame(panel, bg=COLORS["panel"])
        canvas_frame.pack(fill="both", expand=True)
        self.cards_canvas = tk.Canvas(canvas_frame, bg=COLORS["panel"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            canvas_frame, orient="vertical", command=self.cards_canvas.yview,
            style="Sea.Vertical.TScrollbar",
        )
        scrollbar.pack(side="right", fill="y")
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        self.cards_canvas.configure(yscrollcommand=scrollbar.set)
        self.cards_inner = tk.Frame(self.cards_canvas, bg=COLORS["panel"])
        self.canvas_window = self.cards_canvas.create_window(0, 0, anchor="nw", window=self.cards_inner)
        self.cards_inner.bind("<Configure>", lambda _: self.cards_canvas.configure(scrollregion=self.cards_canvas.bbox("all")))
        self.cards_canvas.bind("<Configure>", lambda e: self.cards_canvas.itemconfigure(self.canvas_window, width=e.width))
        self.cards_canvas.bind("<MouseWheel>", self._scroll_cards)
        self.cards_inner.bind("<MouseWheel>", self._scroll_cards)
        for number, depth, coins in CHECKPOINTS:
            self._checkpoint_card(number, depth, coins)

    def _checkpoint_card(self, number: int, depth: int, coins: int):
        border = tk.Frame(self.cards_inner, bg=COLORS["line"])
        border.pack(fill="x", pady=(0, 8), padx=(0, 5))
        card = tk.Frame(border, bg=COLORS["panel2"], padx=13, pady=11)
        card.pack(fill="x", padx=1, pady=1)
        number_tag = tk.Label(
            card,
            text=f"{number:02d}",
            bg=COLORS["aqua_dark"],
            fg=COLORS["aqua"],
            font=("Segoe UI Semibold", 15),
            width=3,
            height=2,
        )
        number_tag.pack(side="left", padx=(0, 12))
        content = tk.Frame(card, bg=COLORS["panel2"])
        content.pack(side="left", fill="x", expand=True)
        self._label(content, f"Reach {depth:,} meter", size=12, weight="bold").pack(anchor="w")
        self._label(content, f"◈  {coins:,} Coins", size=10, color="gold", weight="bold").pack(anchor="w", pady=(4, 0))
        button = self._button(card, "Mark reached", lambda n=number: self._toggle_checkpoint(n))
        button.configure(font=("Segoe UI", 9), padx=7, pady=7)
        button.pack(side="right", padx=(8, 0))
        self.card_widgets[number] = (number_tag, button)
        for widget in (border, card, number_tag, content, button):
            widget.bind("<MouseWheel>", self._scroll_cards, add="+")

    def _scroll_cards(self, event):
        self.cards_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _scroll_left(self, event):
        self.left_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _jump_to_next(self):
        next_item = self.checkpoints.next_checkpoint
        if next_item is None:
            return
        index = [row[0] for row in CHECKPOINTS].index(next_item[0])
        self.cards_canvas.update_idletasks()
        card_height = self.cards_inner.winfo_height() / len(CHECKPOINTS)
        total = max(1, self.cards_inner.winfo_height())
        self.cards_canvas.yview_moveto(min(1.0, index * card_height / total))

    def _toggle_checkpoint(self, number: int):
        try:
            reached = not self.checkpoints.is_reached(number)
            self.checkpoints.set_reached(number, reached)
            self._update_checkpoints()
            self._append_activity(f"Checkpoint {number} {'marked reached' if reached else 'cleared'}.")
        except OSError as exc:
            self._set_status(f"Could not save checkpoint: {exc}", error=True)

    def _update_checkpoints(self):
        for number, _depth, _coins in CHECKPOINTS:
            badge, button = self.card_widgets[number]
            reached = self.checkpoints.is_reached(number)
            badge.configure(
                text="✓" if reached else f"{number:02d}",
                bg=COLORS["aqua_dark"] if reached else COLORS["field"],
                fg=COLORS["aqua"] if reached else COLORS["muted"],
            )
            button.configure(
                text="✓  Reached" if reached else "Mark reached",
                bg=COLORS["aqua_dark"] if reached else COLORS["field"],
                fg=COLORS["aqua"] if reached else COLORS["text"],
            )
        count = self.checkpoints.completed_count
        self.checkpoint_value.configure(text=f"{count} / {len(CHECKPOINTS)}")
        next_item = self.checkpoints.next_checkpoint
        self.checkpoint_note.configure(
            text=(f"Next {next_item[1]:,} m" if self.compact else f"Next: {next_item[1]:,} m · {next_item[2]:,} coins")
            if next_item else "All reached"
        )

    def _update_progress(self):
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            oxygen = int(data.get("oxygen_level_estimate", 0))
            runs = int(data.get("runs_completed", 0))
            goal = int(load_config().get("goal_oxygen_level", 2000))
            self.oxygen_value.configure(text=f"{oxygen:,}" if self.compact else f"{oxygen:,} / {goal:,}")
            self.runs_value.configure(text=f"{runs:,}")
            self.oxygen_note.configure(
                text=f"of {goal:,}" if self.compact
                else f"{min(100, round(oxygen / max(1, goal) * 100))}% of target · saved estimate"
            )
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _append_activity(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line.rstrip() + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 160:
            self.log_text.delete("1.0", "30.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, message: str, *, error=False):
        self.status.configure(text=message, fg=COLORS["red"] if error else COLORS["muted"])
        self._append_activity(message)

    def _update_controls(self):
        if not hasattr(self, "start_btn"):
            return
        selected = self.usb_serial.get() if self.mode.get() == "usb" else self.wireless_address.get().strip()
        usable = (
            self.connected_serial is not None
            and self.connected_mode == self.mode.get()
            and self.connected_serial == selected
        )
        self.start_btn.configure(state="normal" if usable and not self.running and not self.busy and not self.closing else "disabled")
        self.stop_btn.configure(state="normal" if self.running else "disabled")
        if self.compact and hasattr(self, "footer_action_btn"):
            if self.running:
                self.footer_action_btn.configure(text="■  Stop", command=self._stop_bot, bg=COLORS["red_dark"], fg=COLORS["red"])
            elif usable and not self.busy and not self.closing:
                self.footer_action_btn.configure(text="▶  Start", command=self._start_bot, bg=COLORS["gold"], fg=COLORS["bg"])
            if self.running or (usable and not self.busy and not self.closing):
                if not self.footer_action_btn.winfo_manager():
                    self.footer_action_btn.pack(side="right", padx=(7, 0), before=self.status)
            elif self.footer_action_btn.winfo_manager():
                self.footer_action_btn.pack_forget()
        if self.running and not self.header_stop_btn.winfo_manager():
            self.header_stop_btn.pack(side="right", padx=(0, 7), before=self.connection_pill)
        elif not self.running and self.header_stop_btn.winfo_manager():
            self.header_stop_btn.pack_forget()
        self.usb_tab.configure(state="disabled" if self.running or self.busy or self.closing else "normal")
        self.wireless_tab.configure(state="disabled" if self.running or self.busy or self.closing else "normal")
        self.settings_btn.configure(state="disabled" if self.running or self.busy or self.closing else "normal")
        if hasattr(self, "connect_btn"):
            self.connect_btn.configure(state="disabled" if self.running or self.busy or self.closing else "normal")
        for name in ("refresh_btn", "pair_btn"):
            if hasattr(self, name):
                widget = getattr(self, name)
                if widget.winfo_exists():
                    widget.configure(state="disabled" if self.running or self.busy or self.closing else "normal")
        if self.running:
            self.connection_pill.configure(text="●  LIVE" if self.compact else "●  RUNNING", fg=COLORS["aqua"])
            self.run_summary.configure(text="ADB Direct mode is running. No scrcpy mirror is required.")
        elif usable:
            self.connection_pill.configure(text=f"●  {self.connected_mode.upper()}" if self.compact else f"●  {self.connected_mode.upper()} READY", fg=COLORS["aqua"])
            self.run_summary.configure(text=f"Ready on {self.connected_serial}. Start controls the phone directly through ADB.")
        elif self.connected_serial:
            self.connection_pill.configure(text="●  OFFLINE" if self.compact else "●  SELECTED MODE NOT CONNECTED", fg=COLORS["gold"])
            self.run_summary.configure(text="Connect a device in the selected mode to begin.")
        else:
            self.connection_pill.configure(text="●  OFFLINE" if self.compact else "●  DISCONNECTED", fg=COLORS["gold"])
            self.run_summary.configure(text="Connect a device to begin.")

    def _adb_path(self) -> str:
        configured = str(self.settings.get("adb_path", "") or "")
        if not configured:
            try:
                configured = str(load_config().get("adb_path", "") or "")
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        if configured:
            candidate = Path(os.path.expandvars(configured)).expanduser()
            if candidate.is_dir():
                candidate = candidate / ("adb.exe" if os.name == "nt" else "adb")
            if not candidate.is_file():
                raise BotError("Configured ADB executable was not found. Check Settings.")
        return ADB._find_adb(configured)

    def _dispatch(self, work, success):
        if self.running or self.busy:
            return
        self.busy = True
        self._update_controls()

        def worker():
            try:
                result = work()
                self.events.put(("task_ok", success, result))
            except (BotError, DeviceConnectionError, OSError, ValueError) as exc:
                self.events.put(("task_error", str(exc)))
            except Exception as exc:
                self.events.put(("task_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_usb(self):
        self._set_status("Looking for authorized USB devices…")
        self._dispatch(lambda: ConnectionManager(self._adb_path()).usb_devices(), self._usb_refreshed)

    def _usb_refreshed(self, serials: list[str]):
        if hasattr(self, "usb_combo") and self.usb_combo.winfo_exists():
            self.usb_combo.configure(values=serials)
        if self.usb_serial.get() not in serials:
            self.usb_serial.set(serials[0] if serials else "")
        if self.connected_mode == "usb" and self.connected_serial not in serials:
            self.connected_serial = None
            self.connected_mode = None
        self._set_status(
            f"Found {len(serials)} authorized USB device{'s' if len(serials) != 1 else ''}."
            if serials else "No authorized USB device found. Connect and unlock the phone, then refresh."
        )

    def _connect_usb(self):
        serial = self.usb_serial.get().strip()
        if not serial:
            self._set_status("Select a USB device. Use Refresh to find it.", error=True)
            return
        if self.connected_mode == "usb":
            self.connected_serial = None
            self.connected_mode = None
        self._set_status(f"Checking USB device {serial}…")
        self._dispatch(lambda: ConnectionManager(self._adb_path()).connect_usb(serial), lambda result: self._connected("usb", result))

    def _connect_wireless(self):
        address = self.wireless_address.get().strip()
        if self.connected_mode == "wireless":
            self.connected_serial = None
            self.connected_mode = None
        self._set_status("Connecting to wireless ADB…")
        self._dispatch(
            lambda: ConnectionManager(self._adb_path()).connect_wireless(address),
            lambda result: self._connected("wireless", result),
        )

    def _pair_wireless(self):
        address = self.pair_address.get().strip()
        code = self.pair_code.get()
        self.pair_code.set("")
        self._set_status("Pairing with Android Wireless debugging…")
        self._dispatch(
            lambda: ConnectionManager(self._adb_path()).pair_wireless(address, code),
            lambda _: self._set_status("Paired. Enter the separate connection IP:port above, then connect."),
        )

    def _connected(self, mode: str, serial: str):
        self.connected_mode = mode
        self.connected_serial = serial
        self.settings["mode"] = mode
        if mode == "usb":
            self.settings["usb_serial"] = serial
        if mode == "wireless":
            self.settings["wireless_address"] = self.wireless_address.get().strip()
        self._persist_settings()
        self._set_status(f"Connected over {mode.upper()}: {serial}")
        self._update_controls()

    def _autostart(self):
        """Connect only the remembered target, then start a normal controlled run."""
        if self.closing or self.running or self.busy:
            return
        if self.mode.get() == "wireless":
            address = self.wireless_address.get().strip()
            if not address:
                self._set_status("Enter the wireless connection IP:port, then press Connect.", error=True)
                return
            self._set_status(f"Reconnecting wireless device {address}…")
            self._dispatch(
                lambda: ConnectionManager(self._adb_path()).connect_wireless(address),
                lambda serial: self._autostart_connected("wireless", serial),
            )
            return

        remembered = self.usb_serial.get().strip()
        self._set_status("Finding the selected USB device…")

        def connect_usb():
            manager = ConnectionManager(self._adb_path())
            serials = manager.usb_devices()
            if remembered:
                selected = remembered
            elif len(serials) == 1:
                selected = serials[0]
            elif not serials:
                raise DeviceConnectionError("No authorized USB phone found. Connect it, then press Refresh.")
            else:
                raise DeviceConnectionError("Multiple USB devices found. Select one in the dashboard.")
            return serials, manager.connect_usb(selected)

        def connected(result):
            serials, serial = result
            if hasattr(self, "usb_combo") and self.usb_combo.winfo_exists():
                self.usb_combo.configure(values=serials)
            self.usb_serial.set(serial)
            self._autostart_connected("usb", serial)

        self._dispatch(connect_usb, connected)

    def _autostart_connected(self, mode: str, serial: str):
        self._connected(mode, serial)
        if not self.closing:
            self.root.after(120, self._start_bot)

    def _persist_settings(self):
        try:
            _save_settings(self.settings)
        except OSError as exc:
            self._set_status(f"Could not save window settings: {exc}", error=True)

    def _open_settings(self):
        if self.running or self.busy:
            self._set_status("Finish the current operation before changing tool paths.", error=True)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Sea Explorer · Settings")
        if ICON_PATH.is_file():
            try:
                dialog.iconbitmap(default=str(ICON_PATH))
            except tk.TclError:
                pass
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.geometry("590x270")
        panel = tk.Frame(dialog, bg=COLORS["bg"], padx=22, pady=20)
        panel.pack(fill="both", expand=True)
        self._label(panel, "Tool locations", size=16, weight="bold").pack(anchor="w")
        self._label(panel, "ADB is required. scrcpy is kept only for legacy builds and is not used by ADB Direct mode.", size=9, color="muted").pack(anchor="w", pady=(3, 15))
        adb_var = tk.StringVar(value=str(self.settings.get("adb_path", "")))
        scrcpy_var = tk.StringVar(value=str(self.settings.get("scrcpy_path", "")))
        for caption, variable in (("ADB EXECUTABLE", adb_var), ("SCRCPY EXECUTABLE (LEGACY / OPTIONAL)", scrcpy_var)):
            self._label(panel, caption, size=8, color="muted", weight="bold").pack(anchor="w", pady=(0, 5))
            row = tk.Frame(panel, bg=COLORS["bg"])
            row.pack(fill="x", pady=(0, 11))
            self._entry(row, variable).pack(side="left", fill="x", expand=True, ipady=6)
            self._button(row, "Browse", lambda v=variable: self._browse_executable(v, dialog)).pack(side="left", padx=(7, 0))

        def save():
            self.settings["adb_path"] = adb_var.get().strip()
            self.settings["scrcpy_path"] = scrcpy_var.get().strip()
            self._persist_settings()
            self.connected_serial = None
            self.connected_mode = None
            self._update_controls()
            self._set_status("Tool paths saved. Reconnect the device before starting.")
            dialog.destroy()

        self._button(panel, "Save settings", save, kind="aqua").pack(anchor="e")
        dialog.grab_set()

    def _browse_executable(self, variable: tk.StringVar, parent: tk.Misc):
        path = filedialog.askopenfilename(
            parent=parent,
            title="Choose executable",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            variable.set(path)

    def _start_bot(self):
        if self.running or self.busy or self.connected_mode != self.mode.get() or not self.connected_serial:
            return
        selected = self.usb_serial.get() if self.mode.get() == "usb" else self.wireless_address.get().strip()
        if selected != self.connected_serial:
            self._set_status("Reconnect the selected device before starting.", error=True)
            return
        try:
            config = load_config()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._set_status(str(exc), error=True)
            return
        serial = self.connected_serial
        mode = self.connected_mode
        config["adb_path"] = str(self.settings.get("adb_path") or config.get("adb_path", ""))
        self.stop_event = threading.Event()
        self.running = True
        self._update_controls()
        self._set_status("Starting ADB Direct control…")
        self.worker = threading.Thread(
            target=self._bot_worker,
            args=(config, serial, mode, self.stop_event),
            daemon=True,
        )
        self.worker.start()

    def _bot_worker(self, config, serial, mode, stop_event):
        outcome = "Dive ended."
        try:
            self.events.put(("bot_started",))
            SeaExplorerBot(
                config,
                serial=serial,
                transport=mode,
                stop_event=stop_event,
            ).run()
        except StopRequested:
            outcome = "Stopped."
        except Exception as exc:
            if stop_event.is_set():
                outcome = "Stopped."
            else:
                log(f"ERROR {type(exc).__name__}: {exc}")
                outcome = f"Bot stopped: {exc}"
        finally:
            stop_event.set()
            self.events.put(("bot_done", outcome))

    def _stop_bot(self):
        if self.stop_event is not None:
            self.stop_event.set()
            self._set_status("Stop requested. Releasing the held touch…")

    def _on_close(self):
        if self.running or self.busy:
            self.closing = True
            if self.running:
                self._stop_bot()
                self._set_status("Waiting for the bot to release touch before closing…")
            else:
                self._set_status("Waiting for the connection operation before closing…")
        else:
            self.root.destroy()

    def _periodic(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "task_ok":
                    self.busy = False
                    event[1](event[2])
                    self._update_controls()
                elif event[0] == "task_error":
                    self.busy = False
                    self._set_status(event[1], error=True)
                    self._update_controls()
                elif event[0] == "bot_started":
                    self._set_status("Dive running in ADB Direct mode.")
                elif event[0] == "bot_done":
                    self.running = False
                    self.worker = None
                    self.stop_event = None
                    self._set_status(event[1], error=event[1].startswith("Bot stopped:"))
                    self._update_controls()
                    self._update_progress()
        except queue.Empty:
            pass
        try:
            with LOG_PATH.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(self.log_position)
                lines = stream.readlines()
                self.log_position = stream.tell()
            for line in lines[-25:]:
                self._append_activity(line)
        except OSError:
            pass
        if time.monotonic() - self._last_progress_read > 1.0:
            self._last_progress_read = time.monotonic()
            self._update_progress()
        if self.closing and not self.running and not self.busy:
            self.root.destroy()
        elif self.root.winfo_exists():
            self.root.after(250, self._periodic)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sea Explorer desktop dashboard")
    parser.add_argument("--autostart", action="store_true", help="connect the saved device and start the bot")
    args = parser.parse_args()
    root = tk.Tk()
    SeaExplorerWindow(root, autostart=args.autostart)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
