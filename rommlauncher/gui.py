"""The window (--gui), made with Tkinter from Python's standard library.

The Launcher does the work on a worker thread; its messages and questions reach the
window through GuiUI, which hands them to Tk's thread through a queue (GuiApp.calls).
"""

from __future__ import annotations

import math
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import paths
from . import system
from .config import roms_root
from .dualscreen import SCREEN_CHOICES, TOUCH_CONTROLS, screen_choices
from .games import (
    ALL_GAMES,
    LOCAL_GAMES,
    ROM_PLATFORMS,
    cover_file,
    fetch_cover,
    filter_library,
    game_facts,
    library_filters,
    local_game_ids,
)
from .launcher import Launcher
from .ports import (
    PLAYERS,
    PORTS,
    RETROARCH,
    Port,
    config_path,
    player_choices,
    port_for,
)
from .retroarch import FLATPAK_RETROARCH, core_id, retroarch_command, retroarch_installed
from .romm import LoginError, RommClient, RommError
from .system import APP_NAME, HAVE_KEYRING, IS_WINDOWS, KEYRING_SERVICE, keyring
from .ui import Quit, TerminalUI, fail, filter_options, human_size, set_ui

if TYPE_CHECKING:
    from .games import Game


try:  # for the GUI (--gui); part of Python, but a separate package on some Linux systems
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import filedialog, messagebox, ttk

    HAVE_TK = True
except Exception:
    tk = tkfont = filedialog = messagebox = ttk = None
    HAVE_TK = False


ANSI = re.compile(r"\x1b\[[0-9;]*m")
THEME = {
    "bg": "#111113", "rail": "#0b0b0d", "panel": "#17171a", "card": "#1f1f23", "chip": "#1f1f24",
    "hover": "#3a3a42", "line": "#232328", "entry": "#1f1f24",
    "text": "#ececf0", "soft": "#c4c4cc", "muted": "#8c8c96", "on_accent": "#ffffff",
    "accent": "#2d7ff9", "accent_hover": "#4a90ff",
    "ok": "#3ecf6e", "warn": "#f0b429", "fail": "#ff6b6b", "info": "#d8d8de",
}
PILL_COLORS = {  # fill, fill on hover, text (THEME keys; None = the background behind it)
    "primary": ("accent", "accent_hover", "on_accent"),
    "chip": ("chip", "hover", "soft"),
    "chip_on": ("text", "text", "bg"),
    "ghost": (None, "card", "soft"),
}
PLATFORM_COLORS = ["#6b2fb3", "#2b6cb0", "#b4531c", "#2f855a", "#a8326f", "#2c7a7b", "#8a5a14"]
PLATFORM_SHORT = {  # RomM's fs_slug → the short name on chips and cover tags
    "n64": "N64", "gb": "GB", "gbc": "GBC", "gba": "GBA", "nds": "DS", "nintendo-dsi": "DSi",
    "3ds": "3DS", "new-nintendo-3ds": "New 3DS", "nes": "NES", "snes": "SNES", "ngc": "GameCube",
    "wii": "Wii", "psx": "PS1", "ps2": "PS2", "psp": "PSP", "genesis": "Genesis", "megadrive": "Mega Drive",
}
LAYOUT_SHORT = {"main": "Main", "both": "Both", "corner": "Corner"}
PLAYER_TIPS = {**{p.key: f"{p.name}: {p.tip}" for p in PORTS.values()},
               RETROARCH: "RetroArch: the original game, in an emulator"}


def friendly_time(text: str, now: datetime | None = None) -> str:
    """'2026-11-16T20:05:13' → 'Today, 20:05', 'Yesterday, 20:05', 'Monday, 20:05' or '3 Nov 2026'."""
    try:
        when = datetime.strptime(text.replace("T", " ")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return text or "Never"
    days = ((now or datetime.now()).date() - when.date()).days
    if days == 0:
        return f"Today, {when:%H:%M}"
    if days == 1:
        return f"Yesterday, {when:%H:%M}"
    if 1 < days < 7:
        return f"{when:%A}, {when:%H:%M}"
    return f"{when.day} {when:%b %Y}"


class GuiUI:
    """TerminalUI's methods, as windows. Called from the worker thread."""

    def __init__(self, gui: "GuiApp"):
        self.gui = gui

    def _on_main(self, fn, *args, wait: bool = False):
        if threading.current_thread() is threading.main_thread():
            return fn(*args)
        box: dict = {}
        done = threading.Event()

        def run():
            try:
                box["value"] = fn(*args)
            except BaseException as exc:  # handed back to the worker
                box["error"] = exc
            finally:
                done.set()

        self.gui.calls.put(run)
        if not wait:
            return None
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def say(self, text: str = "", kind: str = "info") -> None:
        self._on_main(self.gui.log, ANSI.sub("", text), kind)

    def ask(self, prompt: str, default: str = "") -> str:
        answer = self._on_main(self.gui.ask_text, ANSI.sub("", prompt), default, wait=True)
        if answer is None:
            raise Quit
        return answer or default

    def ask_yes(self, prompt: str, default: bool = True) -> bool:
        return self._on_main(self.gui.ask_yes_no, ANSI.sub("", prompt), default, wait=True)

    def choose(self, question, options, prompt, default="", cancel=False, kind="info", note=""):
        return self._on_main(self.gui.ask_choice, ANSI.sub("", question or prompt), options,
                             default, cancel, note, wait=True)

    def progress(self, label: str, total: int | None) -> "GuiProgress":
        return GuiProgress(self, label, total)


class GuiProgress:
    """The status bar's progress bar, with the same calls as Progress."""

    def __init__(self, ui: GuiUI, label: str, total: int | None):
        self.ui, self.label, self.total, self.last = ui, label, total or None, 0.0
        ui._on_main(ui.gui.show_progress, label, self.total, 0)

    def __call__(self, done: int) -> None:
        now = time.monotonic()
        if now - self.last < 0.1 and done != self.total:
            return
        self.last = now
        self.ui._on_main(self.ui.gui.show_progress, self.label, self.total, done)

    def finish(self) -> None:
        self.ui._on_main(self.ui.gui.show_progress, None, None, 0)


class GuiApp:
    """The window: a first-start choice (RomM or not), a login screen, then the library (icon rail, filter chips and cover grid,
    game details on the right, a status bar that opens the activity log)."""

    def __init__(self):
        self.root = tk.Tk()
        self.calls: queue.Queue = queue.Queue()
        self.launcher = Launcher()
        self.games: list[Game] = []
        self.local_ids: set[int] = set()
        self.filter_key = ALL_GAMES
        self.selected: Game | None = None
        self.busy = False
        self.covers: dict[int, "tk.PhotoImage"] = {}       # full size, as downloaded
        self.tile_images: dict[int, "tk.PhotoImage"] = {}  # fitted to a tile
        self.tiles: dict[int, tuple] = {}
        self.columns = 0
        self.screen = None
        self._setup_window()
        self.root.after(50, self._pump)

    # ----- plumbing -------------------------------------------------------

    def _pump(self) -> None:
        """Run what worker threads asked for, on Tk's thread."""
        while True:
            try:
                fn = self.calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except tk.TclError:
                pass  # a widget that was closed meanwhile
        self.root.after(50, self._pump)

    def run_task(self, work, done=None) -> None:
        """work() on a worker thread; done(result, or the exception) back on Tk's thread."""
        def target():
            try:
                result = work()
            except BaseException as exc:
                result = exc
            if done:
                self.calls.put(lambda: done(result))
        threading.Thread(target=target, daemon=True).start()

    def S(self, n: float) -> int:
        return int(n * self.scale)

    def _setup_window(self) -> None:
        r = self.root
        r.title(APP_NAME)
        self.scale = max(1.0, r.winfo_fpixels("1i") / 96)
        r.geometry(f"{self.S(1240)}x{self.S(780)}")
        r.minsize(self.S(900), self.S(560))
        r.configure(bg=THEME["bg"])
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.font = lambda size, weight="normal": (family, size, weight)
        self.icon_family = "Segoe UI Symbol" if IS_WINDOWS else family  # has ▦ ⟳ ⚙ ⎋ ⌕
        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure("Horizontal.TProgressbar", troughcolor=THEME["entry"], background=THEME["accent"],
                        bordercolor=THEME["bg"], lightcolor=THEME["accent"], darkcolor=THEME["accent"],
                        thickness=self.S(6))
        style.configure("Vertical.TScrollbar", troughcolor=THEME["bg"], background=THEME["card"],
                        bordercolor=THEME["bg"], arrowcolor=THEME["bg"], lightcolor=THEME["card"],
                        darkcolor=THEME["card"], gripcount=0, arrowsize=self.S(10))
        style.map("Vertical.TScrollbar", background=[("active", THEME["hover"])])
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.px = tk.PhotoImage(width=1, height=1)  # lets a text label be sized in pixels
        self.TILE_W = self.COVER_H = self.S(204)  # RomM's small covers: 204 px wide, at most 204 tall
        self.DETAIL_H = self.S(220)

    def _clear(self) -> None:
        if self.screen is not None:
            self.screen.destroy()
        self.screen = None

    def label(self, parent, text="", size=10, weight="normal", fg=None, **kw):
        return tk.Label(parent, text=text, font=self.font(size, weight), fg=fg or THEME["text"],
                        bg=kw.pop("bg", parent["bg"]), **kw)

    def button(self, parent, text, command, primary=False, size=10):
        return tk.Button(
            parent, text=text, command=command, font=self.font(size, "bold" if primary else "normal"),
            fg=THEME["text"], bg=THEME["accent"] if primary else THEME["card"],
            activebackground=THEME["accent_hover"] if primary else THEME["hover"],
            activeforeground=THEME["text"], disabledforeground=THEME["muted"],
            relief="flat", bd=0, padx=self.S(16), pady=self.S(8), cursor="hand2",
        )

    def entry(self, parent, var, show=None, width=34):
        return tk.Entry(parent, textvariable=var, show=show or "", width=width, font=self.font(11),
                        fg=THEME["text"], bg=THEME["entry"], insertbackground=THEME["text"],
                        relief="flat", highlightthickness=1, highlightbackground=THEME["entry"],
                        highlightcolor=THEME["accent"])

    def check(self, parent, text, var):
        return tk.Checkbutton(parent, text=text, variable=var, font=self.font(9), fg=THEME["text"],
                              bg=parent["bg"], activebackground=parent["bg"], activeforeground=THEME["text"],
                              selectcolor=THEME["entry"], bd=0, highlightthickness=0)

    # ----- first start, login ---------------------------------------------

    def start(self) -> None:
        """The first screen: on the very first start, RomM or not (show_welcome); without
        RomM, the library straight away; else the login."""
        if not paths.CONFIG_FILE.exists():
            self.show_welcome()
        elif not self.launcher.uses_romm:
            self.after_login()
        else:
            self.show_login()

    def show_welcome(self) -> None:
        """First start: connect to a RomM server, or keep everything on this computer."""
        self._clear()
        outer = tk.Frame(self.root, bg=THEME["bg"])
        outer.pack(fill="both", expand=True)
        self.screen = outer
        box = tk.Frame(outer, bg=THEME["panel"], padx=self.S(36), pady=self.S(30))
        box.place(relx=0.5, rely=0.5, anchor="center")
        self.label(box, APP_NAME, 20, "bold").pack(anchor="w")
        self.label(box, "Do you want to connect to a RomM server?", 11,
                   fg=THEME["muted"]).pack(anchor="w", pady=(0, self.S(18)))
        self.welcome_buttons = {}
        for key, title, text, command in (
            ("romm", "Connect to RomM", "Play the games in your RomM library. Saves are kept in RomM, "
                                        "so they follow you to other computers.", self.show_login),
            ("local", "Play without RomM", f"Play games from a folder on this computer "
                                           f"({roms_root(self.launcher.config)}). Everything stays here.",
             self.use_local),
        ):
            b = self.button(box, title, command, primary=key == "romm", size=11)
            b.pack(fill="x", pady=(self.S(6), self.S(2)))
            self.label(box, text, 9, fg=THEME["muted"], wraplength=self.S(360), justify="left",
                       anchor="w").pack(fill="x", pady=(0, self.S(10)))
            self.welcome_buttons[key] = b
        self.label(box, "You can change this later.", 8, fg=THEME["muted"]).pack(anchor="w", pady=(self.S(4), 0))
        self.welcome_buttons["romm"].focus_set()

    def use_local(self) -> None:
        """Play without RomM: the library is the ROM folder, and saves stay on this computer."""
        self.launcher.use_romm(False)
        try:
            roms_root(self.launcher.config).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # shown as an empty library; Settings can point elsewhere
        self.after_login()

    def show_login(self, message: str = "", kind: str = "fail") -> None:
        """Ask for the RomM address, username and password, like the command line does."""
        self._clear()
        cfg = self.launcher.config
        outer = tk.Frame(self.root, bg=THEME["bg"])
        outer.pack(fill="both", expand=True)
        self.screen = outer
        box = tk.Frame(outer, bg=THEME["panel"], padx=self.S(36), pady=self.S(30))
        box.place(relx=0.5, rely=0.5, anchor="center")
        self.label(box, APP_NAME, 20, "bold").pack(anchor="w")
        self.label(box, "Sign in to your RomM", 11, fg=THEME["muted"]).pack(anchor="w", pady=(0, self.S(18)))
        stored = ""
        if HAVE_KEYRING and cfg.get("username"):
            try:
                stored = keyring.get_password(KEYRING_SERVICE, cfg["username"]) or ""
            except Exception:  # noqa: BLE001
                stored = ""
        self.v_url = tk.StringVar(value=cfg.get("romm_url", ""))
        self.v_user = tk.StringVar(value=cfg.get("username", ""))
        self.v_pass = tk.StringVar(value=stored)
        self.v_remember = tk.BooleanVar(value=bool(stored))
        # After the first login the address isn't asked any more: it's shown under the box,
        # and a click on it edits it.
        ask_url = not cfg.get("romm_url")
        spec = [("Username", self.v_user, None), ("Password", self.v_pass, "•")]
        if ask_url:
            spec.insert(0, ("RomM address (e.g. https://romm.example.com)", self.v_url, None))
        fields = []
        for text, var, show in spec:
            self.label(box, text, 9, fg=THEME["muted"]).pack(anchor="w")
            e = self.entry(box, var, show)
            e.pack(fill="x", ipady=self.S(6), pady=(self.S(3), self.S(12)))
            e.bind("<Return>", self.do_login)
            fields.append((e, var))
        if HAVE_KEYRING:
            self.check(box, "Remember the password (system keyring)", self.v_remember).pack(anchor="w")
        else:
            self.label(box, "The password is kept in memory only, never on disk.", 8,
                       fg=THEME["muted"]).pack(anchor="w")
        self.login_msg = self.label(box, message, 9, fg=THEME[kind], wraplength=self.S(340), justify="left")
        self.login_msg.pack(anchor="w", pady=(self.S(10), self.S(6)))
        buttons = tk.Frame(box, bg=THEME["panel"])
        buttons.pack(fill="x", pady=(self.S(6), 0))
        self.login_btn = self.button(buttons, "Log in", self.do_login, primary=True, size=11)
        self.login_btn.pack(side="left")
        if paths.LIBRARY_CACHE.exists():
            self.button(buttons, "Play offline", self.play_offline).pack(side="left", padx=self.S(10))
        local = self.label(box, "No RomM? Play games from a folder on this computer  →", 9,
                           fg=THEME["muted"], cursor="hand2")
        local.pack(anchor="w", pady=(self.S(16), 0))
        local.bind("<Enter>", lambda e: local.config(fg=THEME["text"]))
        local.bind("<Leave>", lambda e: local.config(fg=THEME["muted"]))
        local.bind("<Button-1>", lambda e: self.use_local())
        self.tooltip(local, "Saves stay on this computer; nothing is sent to RomM. You can connect later.")
        self.local_link = local
        self.address_line = None
        if not ask_url:
            self.address_line = tk.Frame(outer, bg=THEME["bg"])
            self.address_line.place(relx=0.5, rely=1.0, y=-self.S(28), anchor="s")
            self.show_address()
        empty = [e for e, var in fields if not var.get()]
        (empty[0] if empty else self.login_btn).focus_set()

    def show_address(self) -> None:
        """The RomM address under the login box, as quiet text; a click edits it."""
        line = self.address_line
        for w in line.winfo_children():
            w.destroy()
        parts = urllib.parse.urlsplit(self.v_url.get())
        shown = (parts.netloc + parts.path.rstrip("/")) or self.v_url.get()
        text = self.label(line, f"RomM server   {shown}   ✎", 9, fg=THEME["muted"], cursor="hand2")
        text.pack()
        text.bind("<Enter>", lambda e: text.config(fg=THEME["text"]))
        text.bind("<Leave>", lambda e: text.config(fg=THEME["muted"]))
        text.bind("<Button-1>", lambda e: self.edit_address())
        self.tooltip(text, "Click to change the RomM address")

    def edit_address(self) -> None:
        """Turn the address under the login box into a text field: Enter keeps it, Escape undoes."""
        line, before = self.address_line, self.v_url.get()
        for w in line.winfo_children():
            w.destroy()
        e = self.entry(line, self.v_url, width=44)
        e.pack(ipady=self.S(4))
        e.focus_set()
        e.select_range(0, "end")
        e.icursor("end")

        done = []

        def finish(keep: bool):
            if done or not e.winfo_exists():  # Return/Escape, then FocusOut as the field goes
                return
            done.append(keep)
            if not keep or not self.v_url.get().strip():
                self.v_url.set(before)
            self.show_address()

        self.finish_address_edit = finish
        e.bind("<Return>", lambda ev: (finish(True), self.do_login()))
        e.bind("<Escape>", lambda ev: finish(False))
        e.bind("<FocusOut>", lambda ev: finish(True))

    def do_login(self, event=None) -> None:
        url = self.v_url.get().strip().rstrip("/")
        user, password = self.v_user.get().strip(), self.v_pass.get()
        if not (url and user and password):
            self.login_msg.config(text="Fill in the RomM address, username and password.", fg=THEME["fail"])
            return
        if not re.match(r"^https?://", url):
            url = "https://" + url
        remember = self.v_remember.get()
        self.login_msg.config(text="Signing in…", fg=THEME["muted"])
        self.login_btn.config(state="disabled")

        def work():
            client = RommClient(url, user, password, timeout=15)
            client.check_login()
            return client

        def done(result):
            if isinstance(result, BaseException):
                self.login_btn.config(state="normal")
                text = str(result) if isinstance(result, RommError) else f"Something went wrong: {result}"
                if isinstance(result, RommError) and not isinstance(result, LoginError) and paths.LIBRARY_CACHE.exists():
                    text += "\n\nYou can still play the games on this computer: Play offline."
                self.login_msg.config(text=text, fg=THEME["fail"])
                return
            cfg = self.launcher.config
            cfg["romm_url"], cfg["username"], cfg["use_romm"] = url, user, True
            if HAVE_KEYRING:
                try:
                    if remember:
                        keyring.set_password(KEYRING_SERVICE, user, password)
                    else:
                        keyring.delete_password(KEYRING_SERVICE, user)
                except Exception:  # noqa: BLE001
                    pass
            self.launcher.client, self.launcher.offline = result, False
            self.launcher.save_config()
            self.after_login()

        self.run_task(work, done)

    def play_offline(self) -> None:
        self.launcher.client, self.launcher.offline = None, True
        self.after_login()

    def after_login(self) -> None:
        self.show_library()
        if self.launcher.needs_retroarch_setup():
            if self.launcher.auto_setup_retroarch():
                self.log(f"RetroArch: {' '.join(retroarch_command(self.launcher.config['retroarch']))}", "ok")
            else:
                self.log("RetroArch wasn't found. Set where it is in Settings.", "warn")
        self.load_library()

    def connect_romm(self) -> None:
        """From the library without RomM: go to the login (logging in switches to RomM)."""
        if self.busy:
            return
        self.games, self.selected = [], None
        self.show_login("Log in to use your RomM library. Until you do, nothing changes.", "muted")

    def log_out(self) -> None:
        if self.busy:
            return
        self.launcher.client = None
        self.games, self.selected = [], None
        self.show_login("Logged out.", "muted")

    # ----- library screen -------------------------------------------------

    def show_library(self) -> None:
        """The library: icon rail, title + search, filter chips and a cover grid, details on the
        right, status below."""
        self._clear()
        outer = tk.Frame(self.root, bg=THEME["bg"])
        outer.pack(fill="both", expand=True)
        self.screen = outer
        self._build_rail(outer)

        # The details panel scrolls, for games with a long description.
        self.details = tk.Frame(outer, bg=THEME["panel"], width=self.S(304))
        self.details.pack(side="right", fill="y")
        self.details.pack_propagate(False)
        self.details_canvas = tk.Canvas(self.details, bg=THEME["panel"], highlightthickness=0)
        self.details_canvas.pack(fill="both", expand=True)
        self.detail_box = tk.Frame(self.details_canvas, bg=THEME["panel"])
        self.details_canvas.create_window(self.S(24), self.S(24), window=self.detail_box, anchor="nw",
                                          width=self.S(256))
        self.detail_box.bind("<Configure>", lambda e: self.details_canvas.configure(
            scrollregion=(0, 0, self.S(304), e.height + self.S(48))))
        self._wheel_area(self.details_canvas)

        center = tk.Frame(outer, bg=THEME["bg"])
        center.pack(side="left", fill="both", expand=True)
        head = tk.Frame(center, bg=THEME["bg"])
        head.pack(fill="x", padx=(self.S(30), self.S(26)), pady=(self.S(24), 0))
        self.label(head, "Library", 22, "bold").pack(side="left")
        self.heading = self.label(head, "", 10, fg=THEME["muted"])
        self.heading.pack(side="left", padx=(self.S(14), 0), pady=(self.S(10), 0))
        self.v_search = tk.StringVar()
        self.v_search.trace_add("write", lambda *a: self.refresh_grid())
        self._build_search(head).pack(side="right")
        if self.launcher.client or not self.launcher.uses_romm:
            self.add_button = self.pill(head, "+  Add game", self.add_files, kind="chip", size=9, height=self.S(34))
            self.add_button.pack(side="right", padx=(0, self.S(10)))
            self.tooltip(self.add_button, "Add game files you own to RomM" if self.launcher.uses_romm
                         else f"Copy game files you own into {roms_root(self.launcher.config)}")

        self.chip_row = tk.Frame(center, bg=THEME["bg"], height=self.S(30))
        self.chip_row.pack(fill="x", padx=(self.S(30), self.S(26)), pady=(self.S(16), self.S(12)))
        self.chip_row.bind("<Configure>", self._flow_chips)
        self.chips: dict = {}

        bar = tk.Frame(center, bg=THEME["bg"], height=self.S(36))
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(center, bg=THEME["line"], height=1).pack(side="bottom", fill="x")
        self.status = self.label(bar, "", 9, fg=THEME["muted"], anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=self.S(30))
        self.activity_toggle = self.label(bar, "Activity  ▴", 9, fg=THEME["muted"], cursor="hand2")
        self.activity_toggle.pack(side="right", padx=self.S(22))
        self.activity_toggle.bind("<Button-1>", self.toggle_activity)
        self.activity_toggle.bind("<Enter>", lambda e: self.activity_toggle.config(fg=THEME["text"]))
        self.activity_toggle.bind("<Leave>", lambda e: self.activity_toggle.config(fg=THEME["muted"]))
        self.progress = ttk.Progressbar(bar, length=self.S(200), mode="determinate", maximum=100)

        self.activity_open = False
        self.activity_box = tk.Frame(center, bg=THEME["panel"])
        self.activity = tk.Text(self.activity_box, height=8, wrap="word", font=self.font(9), fg=THEME["info"],
                                bg=THEME["panel"], relief="flat", padx=self.S(4), pady=self.S(4),
                                highlightthickness=0, state="disabled", cursor="arrow")
        self.activity.pack(fill="x", padx=self.S(26), pady=self.S(10))
        for kind in ("ok", "warn", "fail", "info", "muted"):
            self.activity.tag_configure(kind, foreground=THEME[kind])

        self.grid_area = tk.Frame(center, bg=THEME["bg"])
        self.grid_area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.grid_area, bg=THEME["bg"], highlightthickness=0)
        self.scroll = ttk.Scrollbar(self.grid_area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(self.S(20), 0))
        self.grid_frame = tk.Frame(self.canvas, bg=THEME["bg"])
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self._wheel_area(self.canvas)
        self.columns = 0
        self.refresh_chips()
        self.show_details(None)

    def _build_rail(self, parent) -> None:
        """The thin bar on the left: library, refresh, settings; you and log out at the bottom."""
        rail = tk.Frame(parent, bg=THEME["rail"], width=self.S(60))
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        icons = (("▦", "Library", lambda: self.set_filter(ALL_GAMES), True),
                 ("⟳", "Refresh the library", self.load_library, False),
                 ("⚙", "Settings", self.show_settings, False))
        for i, (glyph, tip, command, active) in enumerate(icons):
            icon = self.rail_icon(rail, glyph, tip, command)
            icon.set_active(active)
            icon.pack(pady=(self.S(18) if i == 0 else self.S(6), 0))
        cfg = self.launcher.config
        if not self.launcher.uses_romm:
            where = f"without RomM · games in {roms_root(cfg)}"
            user = os.environ.get("USERNAME") or os.environ.get("USER") or "?"
        else:
            where = "offline" if not self.launcher.client else urllib.parse.urlsplit(cfg.get("romm_url", "")).hostname
            user = cfg.get("username", "") or "?"
        size = self.S(34)
        avatar = tk.Canvas(rail, width=size, height=size, bg=THEME["rail"], highlightthickness=0)
        avatar.create_oval(1, 1, size - 1, size - 1, fill=THEME["chip"], outline=THEME["chip"])
        avatar.create_text(size / 2, size / 2, text=user[:1].upper(), fill=THEME["text"], font=self.font(11, "bold"))
        avatar.pack(side="bottom", pady=(self.S(8), self.S(18)))
        self.tooltip(avatar, f"{user} · {where}")
        if self.launcher.uses_romm:
            self.rail_icon(rail, "⎋", "Log out", self.log_out).pack(side="bottom")
        else:
            self.rail_icon(rail, "☁", "Connect to a RomM server", self.connect_romm).pack(side="bottom")

    def rail_icon(self, parent, glyph, tip, command):
        """An icon button for the rail; .set_active(on) marks the screen that's showing."""
        icon = tk.Label(parent, image=self.px, compound="center", text=glyph, width=self.S(40), height=self.S(40),
                        font=(self.icon_family, 15), fg=THEME["muted"], bg=THEME["rail"], cursor="hand2", bd=0)
        icon.active = False

        def paint(hover=False):
            on = hover or icon.active
            icon.config(fg=THEME["text"] if on else THEME["muted"], bg=THEME["chip"] if on else THEME["rail"])

        def set_active(on: bool):
            icon.active = on
            paint()

        icon.set_active = set_active
        icon.bind("<Button-1>", lambda e: command())
        icon.bind("<Enter>", lambda e: paint(True), add="+")
        icon.bind("<Leave>", lambda e: paint(), add="+")
        self.tooltip(icon, tip)
        return icon

    def _build_search(self, parent):
        """A rounded search field; Ctrl+F jumps to it, Escape clears it."""
        w, h = self.S(250), self.S(34)
        box = tk.Canvas(parent, width=w, height=h, bg=parent["bg"], highlightthickness=0)
        self.round_rect(box, 0, 0, w - 1, h - 1, h // 2, fill=THEME["entry"], outline=THEME["entry"])
        box.create_text(self.S(16), h / 2, text="⌕", fill=THEME["muted"], font=(self.icon_family, 12), anchor="w")
        self.search = tk.Entry(box, textvariable=self.v_search, font=self.font(10), fg=THEME["text"],
                               bg=THEME["entry"], insertbackground=THEME["text"], relief="flat", bd=0,
                               highlightthickness=0)
        box.create_window(self.S(38), h / 2, window=self.search, anchor="w", width=w - self.S(54))
        hint = self.label(box, "Search", 10, fg=THEME["muted"], bg=THEME["entry"], cursor="xterm")
        hint.bind("<Button-1>", lambda e: self.search.focus_set())

        def update_hint(*args):
            if self.v_search.get() or self.root.focus_get() is self.search:
                hint.place_forget()
            else:
                hint.place(in_=self.search, x=0, rely=0.5, anchor="w")

        self.v_search.trace_add("write", update_hint)
        self.search.bind("<FocusIn>", update_hint)
        self.search.bind("<FocusOut>", update_hint)
        self.search.bind("<Escape>", lambda e: (self.v_search.set(""), self.root.focus_set()))
        self.root.bind("<Control-f>", lambda e: self.search.focus_set() if self.search.winfo_exists() else None)
        update_hint()
        return box

    def _on_scroll(self, first, last) -> None:
        """The scrollbar shows only when there's something to scroll."""
        self.scroll.set(first, last)
        needed = float(first) > 0 or float(last) < 1
        if needed and not self.scroll.winfo_ismapped():
            self.scroll.pack(side="right", fill="y", before=self.canvas)
        elif not needed and self.scroll.winfo_ismapped():
            self.scroll.pack_forget()

    def _wheel_area(self, canvas) -> None:
        """The mouse wheel scrolls `canvas` while the pointer is over it (or anything on it)."""
        canvas.bind("<Enter>", lambda e: self._bind_wheel(canvas))
        canvas.bind("<Leave>", lambda e: None if self._inside(canvas, e) else self._bind_wheel(None))

    def _bind_wheel(self, canvas) -> None:
        self.wheel_canvas = canvas
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            if canvas is not None:
                self.root.bind_all(seq, self._on_wheel)
            else:
                self.root.unbind_all(seq)

    def _on_wheel(self, event) -> None:
        canvas = getattr(self, "wheel_canvas", None)
        if canvas is None or not canvas.winfo_exists() or tuple(canvas.yview()) == (0.0, 1.0):
            return  # gone, or everything already fits
        if getattr(event, "num", None) in (4, 5):
            step = -3 if event.num == 4 else 3
        else:
            step = -int(event.delta / 40) if abs(event.delta) >= 40 else (-1 if event.delta > 0 else 1)
        canvas.yview_scroll(step, "units")

    def _on_canvas_resize(self, event) -> None:
        columns = max(1, (event.width - self.S(10)) // (self.TILE_W + self.S(24)))
        if columns != self.columns:
            self.columns = columns
            self.refresh_grid(keep_scroll=True)

    def load_library(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.set_status("Loading your library…")

        def work():
            games = self.launcher.library()
            if self.launcher.client:
                self.launcher.retry_pending(games)
            return games, local_game_ids(games, roms_root(self.launcher.config))

        def done(result):
            self.busy = False
            if isinstance(result, BaseException):
                self.log(f"Couldn't load the library: {result}", "fail")
                self.set_status("Couldn't load the library.", "fail")
                return
            self.games, self.local_ids = result
            if self.selected:
                self.selected = next((g for g in self.games if g.id == self.selected.id), None)
            if self.selected is None:  # start on the game played last
                played = [g for g in self.games if self.launcher.last_played(g)]
                self.selected = max(played, key=self.launcher.last_played, default=None)
            self.refresh_chips()
            self.refresh_grid()
            self.show_details(self.selected)
            if self.launcher.uses_romm:
                self.set_status(f"{len(self.games)} games · {len(self.local_ids)} installed")
            else:
                self.set_status(f"{len(self.games)} games in {roms_root(self.launcher.config)}")
            self.load_covers()

        self.run_task(work, done)

    def load_covers(self) -> None:
        """Covers already downloaded show at once; the rest come from RomM in the background."""
        games, client = list(self.games), self.launcher.client

        def work():
            for g in games:
                if g.id in self.covers:
                    continue
                try:
                    path = fetch_cover(client, g) if client else (cover_file(g) if cover_file(g).exists() else None)
                except (RommError, OSError):
                    path = None
                if path:
                    self.calls.put(lambda g=g, p=path: self.set_cover(g, p))

        threading.Thread(target=work, daemon=True).start()

    def set_cover(self, game: Game, path: Path) -> None:
        try:
            image = tk.PhotoImage(file=str(path))
        except tk.TclError:
            return
        self.covers[game.id] = image
        fitted = self.tile_images[game.id] = self.fit_image(image, self.TILE_W, self.COVER_H)
        tile = self.tiles.get(game.id)
        if tile:
            tile[1].config(image=fitted, text="", width=fitted.width(), height=fitted.height(), bg=THEME["bg"])
        if self.selected and self.selected.id == game.id:
            self.show_details(self.selected)

    def fit_image(self, image, width: int, height: int):
        """Scale image so all of it fits in width x height. Tk alone can only zoom and
        subsample by whole numbers, so the scale is the closest fraction a/b at or below it."""
        iw, ih = image.width(), image.height()
        if not (iw and ih):
            return image
        target = min(width / iw, height / ih)
        a, b = max(((a, b) for b in range(1, 9) for a in range(1, 9) if a / b <= target),
                   key=lambda ab: ab[0] / ab[1], default=(1, 8))
        if a > 1:
            image = image.zoom(a)
        if b > 1:
            image = image.subsample(b)
        return image

    def refresh_local(self) -> None:
        self.local_ids = local_game_ids(self.games, roms_root(self.launcher.config))
        self.refresh_chips()
        self.refresh_grid(keep_scroll=True)

    def refresh_chips(self) -> None:
        """One chip per filter: All, Installed, then each platform, with how many games are in it."""
        for w in self.chip_row.winfo_children():
            w.destroy()
        self.chips = {}
        for key, text, count in library_filters(self.games, self.local_ids, show_local=self.launcher.uses_romm):
            if key == ALL_GAMES:
                name = "All"
            elif key == LOCAL_GAMES:
                name = "Installed"
            else:
                name = PLATFORM_SHORT.get(key.split(":", 1)[1], text)
            chip = self.pill(self.chip_row, name, lambda k=key: self.set_filter(k), count=count, size=9,
                             kind="chip_on" if key == self.filter_key else "chip", height=self.S(30))
            if name != text:
                self.tooltip(chip, text)
            self.chips[key] = chip
        self._flow_chips()

    def _flow_chips(self, event=None) -> None:
        """Lay the chips out left to right, wrapping onto a new line when the window is narrow."""
        width = self.chip_row.winfo_width()
        if width <= 1:
            width = self.S(600)
        x = y = height = 0
        gap = self.S(8)
        for chip in self.chips.values():
            w, h = int(chip["width"]), int(chip["height"])
            if x and x + w > width:
                x, y = 0, y + h + gap
            chip.place(x=x, y=y)
            x += w + gap
            height = y + h
        if height and int(self.chip_row["height"]) != height:
            self.chip_row.configure(height=height)

    def set_filter(self, key: str) -> None:
        self.filter_key = key
        self.refresh_chips()
        self.refresh_grid()

    def refresh_grid(self, keep_scroll: bool = False) -> None:
        if not hasattr(self, "grid_frame"):
            return
        query = self.v_search.get()
        shown = filter_library(self.games, self.filter_key, query, self.local_ids)
        if query.strip():
            self.heading.config(text=f"{len(shown)} found for “{query.strip()}”")
        else:
            self.heading.config(text=f"{len(shown)} games" if self.games else "")
        top = self.canvas.yview()[0] if keep_scroll else 0
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.tiles = {}
        columns = max(1, self.columns)
        for i, game in enumerate(shown):
            tile = self.make_tile(game)
            tile.grid(row=i // columns, column=i % columns, padx=self.S(10), pady=(self.S(4), self.S(18)), sticky="n")
        if not shown:
            text = "Nothing matches your search." if query.strip() else (
                "No games here yet." if self.games else "Loading…")
            if not self.games and not self.launcher.uses_romm and not self.busy:
                folder = roms_root(self.launcher.config)
                text = (f"No games yet. Put them in a folder per platform in {folder}\n"
                        f"({folder / 'n64'}, {folder / 'gba'}, …), or use + Add game.")
            self.label(self.grid_frame, text, 11, fg=THEME["muted"], bg=THEME["bg"]).grid(
                padx=self.S(10), pady=self.S(30))
        self.grid_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(top)

    def platform_color(self, slug: str) -> str:
        return PLATFORM_COLORS[sum(map(ord, slug)) % len(PLATFORM_COLORS)]

    def _tile(self, key, name, platform, platform_name, image, status, status_color, selected, on_click, on_double):
        """A cover in a fixed box with its platform in the corner, the name and a status line below."""
        tile = tk.Frame(self.grid_frame, bg=THEME["bg"], cursor="hand2")
        ring = tk.Frame(tile, bg=THEME["accent"] if selected else THEME["bg"], padx=self.S(2), pady=self.S(2))
        ring.pack(anchor="w")
        # A fixed box the size of the biggest cover; each cover sits in its middle, whole,
        # and the rest of the box is see-through (only outlined on hover or when selected).
        box = tk.Frame(ring, bg=THEME["bg"], width=self.TILE_W, height=self.COVER_H)
        box.pack()
        box.pack_propagate(False)
        if image:
            cover = tk.Label(box, image=image, bg=THEME["bg"], bd=0)
        else:
            cover = tk.Label(box, image=self.px, compound="center", width=self.TILE_W, height=self.COVER_H * 9 // 10,
                             text=name, wraplength=self.TILE_W - self.S(28), font=self.font(11, "bold"),
                             fg="#ffffff", bg=self.platform_color(platform), bd=0)
        cover.pack(expand=True)  # centred, so the box shows above and below it
        # A sibling placed over the cover, not a child of it: Windows repaints a label's
        # image over its children.
        tag = tk.Label(box, text=PLATFORM_SHORT.get(platform, platform_name), font=self.font(8, "bold"),
                       fg=THEME["text"], bg=THEME["rail"], padx=self.S(5), pady=0)
        tag.place(in_=cover, relx=1.0, x=-self.S(6), y=self.S(6), anchor="ne")
        tag.lift()
        title = self.label(tile, self.fit_text(name, self.font(10, "bold"), self.TILE_W), 10, "bold", anchor="w")
        title.pack(fill="x", padx=self.S(2), pady=(self.S(8), 0))
        line = self.label(tile, status, 9, fg=status_color, anchor="w")
        line.pack(fill="x", padx=self.S(2))
        for w in (tile, ring, box, cover, tag, title, line):
            w.bind("<Button-1>", lambda e: on_click())
            if on_double:
                w.bind("<Double-Button-1>", lambda e: on_double())
        tile.bind("<Enter>", lambda e: self._hover(key, True))
        tile.bind("<Leave>", lambda e: self._hover(key, self._inside(tile, e)))
        self.tiles[key] = (ring, cover)
        return tile

    def make_tile(self, game: Game):
        local = game.id in self.local_ids
        return self._tile(game.id, game.name, game.platform, game.platform_name, self.tile_images.get(game.id),
                          "●  Installed" if local else "☁  In the cloud", THEME["ok"] if local else THEME["muted"],
                          bool(self.selected and self.selected.id == game.id),
                          lambda: self.select(game), lambda: self.play(game))

    def _inside(self, widget, event) -> bool:
        """Whether the pointer is still over widget or one of its children (Leave fires for those too)."""
        under = widget.winfo_containing(event.x_root, event.y_root)
        return under is not None and str(under).startswith(str(widget))

    def _selected_key(self):
        return self.selected.id if self.selected else None

    def _hover(self, key, on: bool) -> None:
        tile = self.tiles.get(key)
        if tile and key != self._selected_key():
            tile[0].config(bg=THEME["hover"] if on else THEME["bg"])

    def _mark(self, old_key, new_key) -> None:
        if old_key in self.tiles:
            self.tiles[old_key][0].config(bg=THEME["bg"])
        if new_key in self.tiles:
            self.tiles[new_key][0].config(bg=THEME["accent"])

    def select(self, game: Game) -> None:
        old = self.selected.id if self.selected else None
        self.selected = game
        self._mark(old, game.id)
        self.show_details(game)

    # ----- details panel --------------------------------------------------

    def _clear_details(self):
        for w in self.detail_box.winfo_children():
            w.destroy()
        self.details_canvas.yview_moveto(0)
        self.play_button = None
        return self.detail_box

    def _info_rows(self, box, rows) -> None:
        tk.Frame(box, bg=THEME["line"], height=1).pack(fill="x", pady=self.S(18))
        info = tk.Frame(box, bg=THEME["panel"])
        info.pack(fill="x")
        for i, (name, value, color) in enumerate(rows):
            self.label(info, name, 9, fg=THEME["muted"], anchor="nw").grid(row=i, column=0, sticky="nw", pady=self.S(2))
            self.label(info, value, 9, fg=color, anchor="w", wraplength=self.S(170), justify="left").grid(
                row=i, column=1, sticky="w", padx=(self.S(16), 0), pady=self.S(2))

    def _about(self, box, text: str) -> None:
        self.label(box, "About", 9, fg=THEME["muted"], anchor="w").pack(fill="x", pady=(self.S(18), self.S(4)))
        self.label(box, text, 9, fg=THEME["soft"], anchor="w", justify="left", wraplength=self.S(256)).pack(fill="x")

    def show_details(self, game: Game | None) -> None:
        box = self._clear_details()
        if game is None:
            self.label(box, "Select a game", 12, fg=THEME["muted"]).pack(pady=(self.S(80), self.S(4)))
            self.label(box, "Double-click a cover to play.", 9, fg=THEME["muted"]).pack()
            return
        width = self.S(256)
        image = self.covers.get(game.id)
        if image:
            self._detail_image = self.fit_image(image, width, self.DETAIL_H)
            tk.Label(box, image=self._detail_image, bg=THEME["panel"], bd=0).pack(anchor="w", pady=(0, self.S(16)))
        self.label(box, game.name, 15, "bold", wraplength=width, justify="left", anchor="w").pack(fill="x")
        size = game.rom.get("fs_size_bytes")
        meta = game.platform_name + (f"  ·  {human_size(size)}" if size else "")
        self.label(box, meta, 9, fg=THEME["muted"], anchor="w").pack(fill="x", pady=(self.S(2), self.S(16)))

        local = game.id in self.local_ids
        playing = self.busy and getattr(self, "playing", None) is game
        self.play_button = self.pill(box, "Playing…" if playing else "▶  Play", lambda: self.play(game),
                                     kind="primary", width=width, height=self.S(44), radius=self.S(8), size=11)
        self.play_button.pack(anchor="w")
        self.play_button.set_enabled(not self.busy)
        if not local and self.launcher.client:
            note = "Downloads from RomM first" + (f" ({human_size(size)})" if size else "")
            self.label(box, note, 8, fg=THEME["muted"], anchor="w").pack(fill="x", pady=(self.S(6), 0))

        players = player_choices(game)
        self.v_player = tk.StringVar(value=self.launcher.default_player(game))
        if players:
            self.label(box, "Play with", 9, fg=THEME["muted"], anchor="w").pack(fill="x", pady=(self.S(18), self.S(6)))
            self.player_row = tk.Frame(box, bg=THEME["panel"])
            self.player_row.pack(anchor="w")
            self._player_chips(players)
            note = "Each keeps its own save."
            port = port_for(game)
            if not port.installed(self.launcher.config):
                note += f" {port.name} isn't set up yet: Play offers to download it (or Settings)."
            self.label(box, note, 8, fg=THEME["muted"], anchor="w", wraplength=width,
                       justify="left").pack(fill="x", pady=(self.S(6), 0))

        entry = self.launcher.state.get(str(game.id), {})
        choices = screen_choices(game)
        self.v_layout = tk.StringVar(value=entry.get("screen_layout") if entry.get("screen_layout") in choices else "both")
        if choices:
            self.label(box, "Screens", 9, fg=THEME["muted"], anchor="w").pack(fill="x", pady=(self.S(18), self.S(6)))
            self.layout_row = tk.Frame(box, bg=THEME["panel"])
            self.layout_row.pack(anchor="w")
            self._layout_chips(choices)
            touch = TOUCH_CONTROLS.get(core_id(game.core or ""))
            if touch:
                self.label(box, touch["help"], 8, fg=THEME["muted"], anchor="w", wraplength=width,
                           justify="left").pack(fill="x", pady=(self.S(8), 0))

        actions = []
        if self.launcher.client and not local:
            actions.append(("Download only", lambda: self.play(game, launch=False)))
        if self.launcher.client and local and game.syncs_saves:
            actions.append(("Upload save", lambda: self.upload_save(game)))
        if actions:
            row = tk.Frame(box, bg=THEME["panel"])
            row.pack(anchor="w", pady=(self.S(14), 0))
            for text, command in actions:
                b = self.pill(row, text, command, kind="ghost", size=9, height=self.S(30))
                b.pack(side="left", padx=(0, self.S(6)))
                b.set_enabled(not self.busy)

        played = entry.get("last_played", "")
        if not self.launcher.uses_romm:
            save, color = "On this computer", THEME["text"]
        elif entry.get("pending_upload"):
            save, color = "Waiting to upload to RomM", THEME["warn"]
        elif not game.syncs_saves:
            save, color = "Not synced for this platform", THEME["muted"]
        elif entry.get("synced_at"):
            save, color = f"Synced {friendly_time(entry['synced_at']).lower()}", THEME["text"]
        else:
            save, color = "Not synced yet", THEME["muted"]
        rows = [("Status", "Installed" if local else "In the cloud", THEME["ok"] if local else THEME["text"]),
                ("Last played", friendly_time(played), THEME["text"]),
                ("Save", save, color)]
        rows += [(name, value, THEME["text"]) for name, value in game_facts(game.rom)]
        self._info_rows(box, rows)
        if game.rom.get("summary"):
            self._about(box, game.rom["summary"].strip())

    def _layout_chips(self, choices) -> None:
        for w in self.layout_row.winfo_children():
            w.destroy()
        for choice in choices:
            chip = self.pill(self.layout_row, LAYOUT_SHORT.get(choice, choice), lambda c=choice: self.set_layout(c, choices),
                             kind="chip_on" if self.v_layout.get() == choice else "chip", size=9, height=self.S(28))
            chip.pack(side="left", padx=(0, self.S(6)))
            self.tooltip(chip, SCREEN_CHOICES[choice])

    def set_layout(self, choice: str, choices) -> None:
        self.v_layout.set(choice)
        self._layout_chips(choices)

    def _player_chips(self, players) -> None:
        for w in self.player_row.winfo_children():
            w.destroy()
        for player in players:
            chip = self.pill(self.player_row, PLAYERS[player], lambda p=player: self.set_player(p, players),
                             kind="chip_on" if self.v_player.get() == player else "chip", size=9, height=self.S(28))
            chip.pack(side="left", padx=(0, self.S(6)))
            self.tooltip(chip, PLAYER_TIPS[player])

    def set_player(self, player: str, players) -> None:
        self.v_player.set(player)
        self._player_chips(players)

    def filetypes(self, port: Port) -> list[tuple[str, str]]:
        return [(port.name, port.exe if IS_WINDOWS else port.linux_patterns()), ("All files", "*.*")]

    def pick_port(self, port: Port) -> str | None:
        """A PC port isn't set up (asked on Tk's thread, before playing): "download" to have
        the launcher download it, "picked" when its program was picked, None to cancel."""
        answer = messagebox.askyesnocancel(
            APP_NAME, f"{port.name} isn't set up on this computer yet.\n\n"
            "Yes: download it for you, from its GitHub page, into the launcher's folder.\n"
            f"No: pick the {port.exe if IS_WINDOWS else port.linux_label} you already have.", parent=self.root)
        if answer is None:
            return None
        if answer:
            return "download"
        path = filedialog.askopenfilename(parent=self.root, title=f"Where is {port.name}?",
                                          filetypes=self.filetypes(port))
        if not path:
            return None
        cfg = self.launcher.config
        cfg.setdefault(system.OS_KEY, {})
        cfg[port.key] = config_path(Path(path))
        self.launcher.save_config()
        self.log(f"{port.name}: {path}  (saves: {port.saves_dir(cfg)})", "ok")
        return "picked"

    def play(self, game: Game, launch: bool = True) -> None:
        if self.busy:
            return
        if self.selected is not game:
            self.select(game)
        choices = screen_choices(game)
        screen = self.v_layout.get() if choices else None
        player = self.v_player.get() if player_choices(game) else None
        port = PORTS.get(player)
        setup = None
        if port and launch and not port.installed(self.launcher.config):
            setup = self.pick_port(port)
            if setup is None:
                return
        self.busy, self.playing = True, game
        self.show_details(self.selected)
        self.log("")
        self.log(f"▶ {game.name}", "info")

        def done(result):
            self.busy, self.playing = False, None
            if isinstance(result, Quit):
                self.log("Cancelled.", "warn")
            elif isinstance(result, RommError):
                self.log(str(result), "fail")
            elif isinstance(result, BaseException):
                self.log(f"Something went wrong: {result!r}", "fail")
            self.refresh_local()
            self.show_details(self.selected)
            self.set_status("Ready.")

        def task():
            if setup == "download" and not self.launcher.download_port(port):
                return
            self.launcher.play(game, launch=launch, screen=screen, player=player)

        self.run_task(task, done)

    def upload_save(self, game: Game) -> None:
        if self.busy:
            return
        self.busy = True
        self.show_details(self.selected)

        def done(result):
            self.busy = False
            if isinstance(result, BaseException) and not isinstance(result, Quit):
                self.log(str(result), "fail")
            self.show_details(self.selected)

        self.run_task(lambda: self.launcher.upload_now(game), done)

    # ----- adding games ---------------------------------------------------

    def add_files(self) -> None:
        """Pick game files you own and add them to RomM, or without RomM copy them into the ROM
        folder (the launcher asks which platform)."""
        if self.busy or (self.launcher.uses_romm and not self.launcher.client):
            return
        files = filedialog.askopenfilenames(
            parent=self.root, title="Add games you own" + (" to RomM" if self.launcher.uses_romm else ""),
            filetypes=[("Game files", " ".join(f"*{ext}" for ext in sorted(ROM_PLATFORMS)) + " *.zip *.7z"),
                       ("All files", "*.*")])
        if not files:
            return
        self.busy = True
        self.log("")

        def work():
            return [self.launcher.add_rom(Path(f)) for f in files]

        def done(result):
            self.busy = False
            self._after_adding(result)

        self.run_task(work, done)

    def _after_adding(self, result) -> None:
        if isinstance(result, Quit):
            self.log("Cancelled.", "warn")
        elif isinstance(result, (RommError, OSError)):
            self.log(str(result), "fail")
        elif isinstance(result, BaseException):
            self.log(f"Something went wrong: {result!r}", "fail")
        self.load_library()

    # ----- drawing helpers ------------------------------------------------

    def round_rect(self, canvas, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        points = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                  x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
        return canvas.create_polygon(points, smooth=True, **kw)

    def pill(self, parent, text, command=None, kind="chip", count=None, width=None, height=None,
             radius=None, size=10):
        """A rounded button drawn on a canvas (Tk's own buttons can't be rounded).

        kind: "primary" (blue), "chip" (grey), "chip_on" (light, the chosen one) or "ghost"
        (no fill until hovered). The canvas gets .text, .invoke() and .set_enabled(on)."""
        bg = parent["bg"]
        font = self.font(size, "bold" if kind == "primary" else "normal")
        measure = tkfont.Font(font=font).measure
        count_text = f"   {count}" if count is not None else ""
        height = height or self.S(30)
        width = width or measure(text) + measure(count_text) + 2 * self.S(14)
        c = tk.Canvas(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        fill, hover, fg = (THEME[k] if k else bg for k in PILL_COLORS[kind])
        shape = self.round_rect(c, 0, 0, width - 1, height - 1, height // 2 if radius is None else radius,
                                fill=fill, outline=fill)
        x = (width - measure(text) - measure(count_text)) / 2
        label = c.create_text(x, height / 2, text=text, fill=fg, font=font, anchor="w")
        if count_text:
            c.create_text(x + measure(text), height / 2, text=count_text, font=font, anchor="w",
                          fill=THEME["muted"] if kind != "chip_on" else THEME["chip"])
        c.text, c.enabled = text, True

        def paint(color, text_color=None):
            c.itemconfig(shape, fill=color, outline=color)
            c.itemconfig(label, fill=text_color or fg)

        def set_enabled(on: bool):
            c.enabled = on
            c.config(cursor="hand2" if on else "")
            paint(fill, None) if on else paint(THEME["card"] if kind == "primary" else fill, THEME["muted"])

        def invoke():
            if c.enabled and command:
                command()

        c.invoke, c.set_enabled = invoke, set_enabled
        c.bind("<Button-1>", lambda e: invoke())
        c.bind("<Enter>", lambda e: c.enabled and paint(hover), add="+")
        c.bind("<Leave>", lambda e: c.enabled and paint(fill), add="+")
        return c

    def fit_text(self, text: str, font, width: int) -> str:
        """text, cut with … so it fits in width pixels."""
        measure = tkfont.Font(font=font).measure
        if measure(text) <= width:
            return text
        while text and measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    def tooltip(self, widget, text: str) -> None:
        """A small label next to widget while the mouse rests on it."""
        tip: dict = {}

        def show():
            tip.pop("job", None)
            if "win" in tip or not widget.winfo_exists():
                return
            win = tk.Toplevel(self.root, bg=THEME["line"])
            win.overrideredirect(True)
            tk.Label(win, text=text, font=self.font(9), fg=THEME["text"], bg=THEME["card"],
                     padx=self.S(8), pady=self.S(4)).pack(padx=1, pady=1)
            win.update_idletasks()
            x = widget.winfo_rootx() + widget.winfo_width() + self.S(8)
            y = widget.winfo_rooty() + (widget.winfo_height() - win.winfo_reqheight()) // 2
            if x + win.winfo_reqwidth() > self.root.winfo_rootx() + self.root.winfo_width():
                x = widget.winfo_rootx() + (widget.winfo_width() - win.winfo_reqwidth()) // 2
                y = widget.winfo_rooty() + widget.winfo_height() + self.S(6)
            win.geometry(f"+{x}+{y}")
            tip["win"] = win

        def enter(event):
            if "job" not in tip:
                tip["job"] = widget.after(450, show)

        def leave(event):
            if "job" in tip:
                widget.after_cancel(tip.pop("job"))
            if "win" in tip:
                tip.pop("win").destroy()

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")
        widget.bind("<Button-1>", leave, add="+")

    # ----- messages, progress, questions ----------------------------------

    def log(self, text: str, kind: str = "info") -> None:
        if not hasattr(self, "activity") or not self.activity.winfo_exists():
            return
        mark = {"ok": "✔ ", "warn": "! ", "fail": "✖ "}.get(kind, "")
        line = (mark + text.strip()) if text.strip() else ""
        self.activity.config(state="normal")
        self.activity.insert("end", line + "\n", kind if kind in THEME else "info")
        lines = int(self.activity.index("end-1c").split(".")[0])
        if lines > 300:
            self.activity.delete("1.0", f"{lines - 300}.0")
        self.activity.see("end")
        self.activity.config(state="disabled")
        if line:
            self.set_status(line, kind)
        if kind == "fail" and not self.activity_open:  # never let a failure pass unseen
            self.toggle_activity()

    def toggle_activity(self, event=None) -> None:
        """Show or hide the activity log above the status bar."""
        self.activity_open = not self.activity_open
        if self.activity_open:
            self.activity_box.pack(side="bottom", fill="x", before=self.grid_area)
            self.activity.see("end")
        else:
            self.activity_box.pack_forget()
        self.activity_toggle.config(text="Activity  ▾" if self.activity_open else "Activity  ▴")

    def set_status(self, text: str, kind: str = "muted") -> None:
        if hasattr(self, "status") and self.status.winfo_exists():
            color = THEME[kind] if kind in ("ok", "warn", "fail") else THEME["muted"]
            self.status.config(text=text, fg=color)

    def show_progress(self, label: str | None, total: int | None, done: int) -> None:
        if not hasattr(self, "progress") or not self.progress.winfo_exists():
            return
        if label is None:
            self.progress.pack_forget()
            return
        if not self.progress.winfo_ismapped():
            self.progress.pack(side="right", padx=self.S(16))
        if total:
            self.progress.config(mode="determinate", value=min(100, done * 100 / total))
            self.set_status(f"{label}  {human_size(done)} / {human_size(total)}")
        else:
            self.progress.config(mode="indeterminate")
            self.progress.step(4)
            self.set_status(f"{label}  {human_size(done)}")

    def ask_yes_no(self, prompt: str, default: bool = True) -> bool:
        return messagebox.askyesno(APP_NAME, prompt, parent=self.root, default="yes" if default else "no")

    def _dialog(self, title: str):
        dlg = tk.Toplevel(self.root, bg=THEME["panel"], padx=self.S(24), pady=self.S(20))
        dlg.title(title)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        return dlg

    def _run_dialog(self, dlg) -> None:
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 3
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.grab_set()
        dlg.focus_set()
        self.root.wait_window(dlg)

    def ask_text(self, prompt: str, default: str = "") -> str | None:
        dlg = self._dialog(APP_NAME)
        result: dict = {}
        self.label(dlg, prompt, 10, wraplength=self.S(420), justify="left").pack(anchor="w")
        var = tk.StringVar(value=default)
        e = self.entry(dlg, var)
        e.pack(fill="x", ipady=self.S(5), pady=self.S(12))
        e.focus_set()

        def finish(value):
            result["value"] = value
            dlg.destroy()

        e.bind("<Return>", lambda ev: finish(var.get()))
        row = tk.Frame(dlg, bg=THEME["panel"])
        row.pack(anchor="e")
        self.button(row, "OK", lambda: finish(var.get()), primary=True).pack(side="left")
        self.button(row, "Cancel", lambda: finish(None)).pack(side="left", padx=(self.S(8), 0))
        self._run_dialog(dlg)
        return result.get("value")

    def ask_choice(self, question, options, default="", cancel=False, note=""):
        """A question with one radio button per option (a searchable list when there are
        many); returns the chosen key, or None."""
        if len(options) > self.RADIO_LIMIT:
            return self.ask_from_list(question, options, default, cancel, note)
        dlg = self._dialog(APP_NAME)
        keys = [k for k, _ in options]
        var = tk.StringVar(value=default if default in keys else keys[0])
        result = {"value": None if cancel else var.get()}
        self.label(dlg, question, 11, "bold", wraplength=self.S(460), justify="left").pack(anchor="w")
        for key, text in options:
            tk.Radiobutton(dlg, text=text.strip(), value=key, variable=var, font=self.font(10),
                           fg=THEME["text"], bg=THEME["panel"], activebackground=THEME["panel"],
                           activeforeground=THEME["text"], selectcolor=THEME["entry"], anchor="w",
                           bd=0, highlightthickness=0).pack(fill="x", pady=self.S(2))
        if note:
            self.label(dlg, note, 9, fg=THEME["muted"]).pack(anchor="w", pady=(self.S(6), 0))

        def finish(value):
            result["value"] = value
            dlg.destroy()

        row = tk.Frame(dlg, bg=THEME["panel"])
        row.pack(anchor="e", pady=(self.S(14), 0))
        self.button(row, "OK", lambda: finish(var.get()), primary=True).pack(side="left")
        if cancel:
            self.button(row, "Cancel", lambda: finish(None)).pack(side="left", padx=(self.S(8), 0))
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(None if cancel else var.get()))
        self._run_dialog(dlg)
        return result["value"]

    RADIO_LIMIT = 12  # more options than this get a searchable list

    def ask_from_list(self, question, options, default="", cancel=False, note=""):
        """A long choice as a search field over a list: type to narrow it down, then
        double-click, or Enter for the highlighted one. Returns the key, or None."""
        dlg = self._dialog(APP_NAME)
        result = {"value": None if cancel else (default or options[0][0])}
        self.label(dlg, question, 11, "bold", wraplength=self.S(460), justify="left").pack(anchor="w")
        if note:
            self.label(dlg, note, 9, fg=THEME["muted"], wraplength=self.S(460), justify="left").pack(
                anchor="w", pady=(self.S(4), 0))
        text = tk.StringVar()
        search = self.entry(dlg, text, width=40)
        search.pack(fill="x", ipady=self.S(5), pady=(self.S(12), self.S(6)))
        frame = tk.Frame(dlg, bg=THEME["panel"])
        frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(frame, height=14, font=self.font(10), fg=THEME["text"], bg=THEME["entry"],
                             selectbackground=THEME["accent"], selectforeground=THEME["on_accent"],
                             activestyle="none", relief="flat", bd=0, highlightthickness=0, exportselection=False)
        bar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        shown: list[tuple[str, str]] = []

        def refill(*args):
            current = shown[listbox.curselection()[0]][0] if shown and listbox.curselection() else default
            shown[:] = filter_options(options, text.get())
            listbox.delete(0, "end")
            for _, label in shown:
                listbox.insert("end", label)
            keys = [k for k, _ in shown]
            index = keys.index(current) if current in keys else (0 if shown else None)
            if index is not None:
                listbox.selection_set(index)
                listbox.see(index)

        def finish(ok: bool):
            if ok:
                if not listbox.curselection():
                    return
                result["value"] = shown[listbox.curselection()[0]][0]
            elif cancel:
                result["value"] = None
            dlg.destroy()

        def move(step: int):
            if shown:
                index = min(max((listbox.curselection() or (0,))[0] + step, 0), len(shown) - 1)
                listbox.selection_clear(0, "end")
                listbox.selection_set(index)
                listbox.see(index)
            return "break"

        text.trace_add("write", refill)
        search.bind("<Return>", lambda e: finish(True))
        search.bind("<Down>", lambda e: move(1))
        search.bind("<Up>", lambda e: move(-1))
        listbox.bind("<Double-Button-1>", lambda e: finish(True))
        listbox.bind("<Return>", lambda e: finish(True))
        dlg.bind("<Escape>", lambda e: finish(False))
        row = tk.Frame(dlg, bg=THEME["panel"])
        row.pack(anchor="e", pady=(self.S(14), 0))
        self.button(row, "OK", lambda: finish(True), primary=True).pack(side="left")
        if cancel:
            self.button(row, "Cancel", lambda: finish(False)).pack(side="left", padx=(self.S(8), 0))
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        refill()
        self.list_dialog = (text, listbox, shown, finish)  # for the tests
        dlg.after(50, search.focus_set)
        self._run_dialog(dlg)
        return result["value"]

    # ----- settings -------------------------------------------------------

    def show_settings(self) -> None:
        if self.busy:
            return
        cfg = self.launcher.config
        dlg = self._dialog("Settings")
        self.label(dlg, "Settings", 15, "bold").pack(anchor="w", pady=(0, self.S(12)))
        here = cfg.get(system.OS_KEY) if isinstance(cfg.get(system.OS_KEY), dict) else {}
        hint = "" if IS_WINDOWS else '  ("flatpak" for RetroArch from Flathub)'
        values = {
            "retroarch": tk.StringVar(value=cfg["retroarch"]),
            "cores_dir": tk.StringVar(value=cfg["cores_dir"]),
            "saves_dir": tk.StringVar(value=cfg["saves_dir"]),
            "roms_dir": tk.StringVar(value=here.get("roms_dir") or cfg.get("roms_dir") or "roms"),
        }
        for key, text in (("retroarch", f"RetroArch program{hint}"), ("cores_dir", "RetroArch cores folder"),
                          ("saves_dir", "RetroArch saves folder"),
                          ("roms_dir", "Folder for downloaded ROMs (relative = inside the launcher's folder)")):
            self.label(dlg, text, 9, fg=THEME["muted"]).pack(anchor="w")
            self.entry(dlg, values[key], width=60).pack(fill="x", ipady=self.S(4), pady=(self.S(2), self.S(10)))
        auto = tk.BooleanVar(value=cfg.get("auto_install_cores", True))
        pointer = tk.BooleanVar(value=cfg.get("show_touch_pointer", True))
        self.check(dlg, "Download missing cores automatically (from libretro)", auto).pack(anchor="w")
        self.check(dlg, "Show the controller's touch pointer (DS/3DS)", pointer).pack(anchor="w", pady=(self.S(4), 0))

        # The PC ports: where each one is, or have the launcher download it.
        port_vars, self.port_entries, self.port_notes, self.port_downloads = {}, {}, {}, {}

        def port_field(port: Port) -> None:
            self.label(dlg, f"{port.name} ({port.game} as a PC game)", 9,
                       fg=THEME["muted"]).pack(anchor="w", pady=(self.S(18), 0))
            var = port_vars[port.key] = tk.StringVar(value=cfg.get(port.key, ""))
            row = tk.Frame(dlg, bg=THEME["panel"])
            row.pack(fill="x", pady=(self.S(2), self.S(4)))
            self.port_entries[port.key] = self.entry(row, var, width=48)
            self.port_entries[port.key].pack(side="left", fill="x", expand=True, ipady=self.S(4))
            self.port_notes[port.key] = self.label(dlg, self.port_status(port, cfg), 8, fg=THEME["muted"],
                                                   anchor="w", wraplength=self.S(520), justify="left")
            self.port_notes[port.key].pack(fill="x")

            def browse():
                path = filedialog.askopenfilename(parent=dlg, title=f"Where is {port.name}?",
                                                  filetypes=self.filetypes(port))
                if path:
                    var.set(config_path(Path(path)))

            def download():
                dlg.destroy()
                self.download_port(port)

            self.port_downloads[port.key] = None
            if not port.installed(cfg):
                self.port_downloads[port.key] = self.button(row, "Download", download)
                self.port_downloads[port.key].pack(side="right", padx=(self.S(8), 0))
            self.button(row, "Browse…", browse).pack(side="right", padx=(self.S(8), 0))

        for port in PORTS.values():
            port_field(port)

        def detect():
            self.launcher.auto_setup_retroarch()
            for key in ("retroarch", "cores_dir", "saves_dir"):
                values[key].set(cfg[key])

        def save():
            cfg.setdefault(system.OS_KEY, {})
            ra = values["retroarch"].get().strip()
            cfg["retroarch"] = FLATPAK_RETROARCH if ra.lower() == "flatpak" else ra
            cfg["cores_dir"] = values["cores_dir"].get().strip()
            cfg["saves_dir"] = values["saves_dir"].get().strip()
            roms = values["roms_dir"].get().strip() or "roms"
            if Path(roms).expanduser().is_absolute():
                cfg[system.OS_KEY]["roms_dir"] = roms
            else:
                cfg[system.OS_KEY].pop("roms_dir", None)
                cfg["roms_dir"] = roms
            cfg["auto_install_cores"], cfg["show_touch_pointer"] = auto.get(), pointer.get()
            for port in PORTS.values():
                text = port_vars[port.key].get().strip().strip('"')
                if text:
                    program = Path(text).expanduser()
                    cfg[port.key] = config_path(program if program.is_absolute() else paths.APP_HOME / program)
                else:  # cleared: this port isn't used on this computer
                    cfg.pop(port.key, None)
                    cfg[system.OS_KEY].pop(port.key, None)
            self.launcher.save_config()
            dlg.destroy()
            if not retroarch_installed(cfg["retroarch"]):
                self.log(f"RetroArch not found: {cfg['retroarch']}", "warn")
            for port in PORTS.values():
                if cfg.get(port.key) and not port.installed(cfg):
                    self.log(f"{port.name} not found: {cfg[port.key]}", "warn")
            self.refresh_local()
            self.show_details(self.selected)

        row = tk.Frame(dlg, bg=THEME["panel"])
        row.pack(fill="x", pady=(self.S(16), 0))
        self.button(row, "Find RetroArch", detect).pack(side="left")
        self.button(row, "Cancel", dlg.destroy).pack(side="right")
        self.button(row, "Save", save, primary=True).pack(side="right", padx=self.S(8))
        self._run_dialog(dlg)

    @staticmethod
    def port_status(port: Port, cfg: dict) -> str:
        """One line about a PC port for the settings page."""
        if port.installed(cfg):
            return f"Ready. Its settings and saves are in {port.data_dir(cfg)}"
        if cfg.get(port.key):
            return f"Not found at {port.program(cfg)}. Browse to it, or clear the field and Download."
        return (f"Not set up. Download installs the latest version from its GitHub page into "
                f"{port.install_dir()}, checked against GitHub's checksum.")

    def download_port(self, port: Port) -> None:
        """Have the launcher download a PC port (on a worker thread; progress in the status bar)."""
        if self.busy:
            return
        self.busy = True
        self.show_details(self.selected)
        self.log("")
        self.log(port.name, "info")

        def done(result):
            self.busy = False
            if isinstance(result, BaseException) and not isinstance(result, Quit):
                self.log(f"Something went wrong: {result!r}", "fail")
            self.show_details(self.selected)
            self.set_status("Ready." if result is True else f"{port.name} wasn't installed.",
                            "muted" if result is True else "warn")

        self.run_task(lambda: self.launcher.download_port(port), done)

    def on_close(self) -> None:
        later = (" If you quit now, its save is uploaded the next time you play it."
                 if self.launcher.uses_romm else "")
        if self.busy and getattr(self, "playing", None) and not messagebox.askyesno(
            APP_NAME, f"A game is still running.{later}\n\nQuit anyway?", parent=self.root, default="no"):
            return
        self.root.destroy()


def run_gui() -> int:
    """Start the window (romm_launcher.py --gui)."""
    if not HAVE_TK:
        fail("The window needs Tkinter (on Debian/Ubuntu: sudo apt install python3-tk).")
        return 1
    for name in ("stdout", "stderr"):  # pythonw has no console
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    if IS_WINDOWS:
        try:  # sharp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    try:
        gui = GuiApp()
    except tk.TclError as exc:
        fail(f"Couldn't open a window ({exc}).")
        return 1
    set_ui(GuiUI(gui))
    try:
        gui.start()
        gui.root.mainloop()
    finally:
        set_ui(TerminalUI())
    return 0
