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
from pathlib import Path
from typing import TYPE_CHECKING

from . import paths
from . import system
from .config import roms_root
from .dualscreen import SCREEN_CHOICES, TOUCH_CONTROLS, screen_choices
from .games import (
    ALL_GAMES,
    cover_file,
    fetch_cover,
    filter_library,
    library_filters,
    local_game_ids,
)
from .launcher import Launcher
from .retroarch import FLATPAK_RETROARCH, core_id, retroarch_command, retroarch_installed
from .romm import LoginError, RommClient, RommError
from .system import APP_NAME, HAVE_KEYRING, IS_WINDOWS, KEYRING_SERVICE, keyring
from .ui import Quit, TerminalUI, fail, human_size, set_ui

if TYPE_CHECKING:
    from .games import Game


try:  # for the GUI (--gui); part of Python, but a separate package on some Linux systems
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk

    HAVE_TK = True
except Exception:
    tk = tkfont = messagebox = ttk = None
    HAVE_TK = False


ANSI = re.compile(r"\x1b\[[0-9;]*m")
THEME = {
    "bg": "#121214", "panel": "#19191c", "card": "#202024", "hover": "#34343b",
    "select": "#0078f2", "text": "#f2f2f2", "muted": "#9b9ba4", "entry": "#26262b",
    "accent": "#0078f2", "accent_hover": "#1a8cff",
    "ok": "#3ecf6e", "warn": "#f0b429", "fail": "#ff6b6b", "info": "#d8d8de",
}
PLATFORM_COLORS = ["#6b2fb3", "#2b6cb0", "#b4531c", "#2f855a", "#a8326f", "#2c7a7b", "#8a5a14"]


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
    """The window: a login screen, then the library (sidebar, cover grid, game details)."""

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
        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure("Horizontal.TProgressbar", troughcolor=THEME["entry"], background=THEME["accent"],
                        bordercolor=THEME["panel"], lightcolor=THEME["accent"], darkcolor=THEME["accent"])
        style.configure("Vertical.TScrollbar", troughcolor=THEME["bg"], background=THEME["card"],
                        bordercolor=THEME["bg"], arrowcolor=THEME["muted"], lightcolor=THEME["card"],
                        darkcolor=THEME["card"])
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.px = tk.PhotoImage(width=1, height=1)  # lets a text label be sized in pixels
        self.TILE_W, self.COVER_H = self.S(150), self.S(200)
        self.DETAIL_W, self.DETAIL_H = self.S(300), self.S(300)

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

    # ----- login ----------------------------------------------------------

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
        fields = []
        for text, var, show in (("RomM address (e.g. https://romm.example.com)", self.v_url, None),
                                ("Username", self.v_user, None), ("Password", self.v_pass, "•")):
            self.label(box, text, 9, fg=THEME["muted"]).pack(anchor="w")
            e = self.entry(box, var, show)
            e.pack(fill="x", ipady=self.S(6), pady=(self.S(3), self.S(12)))
            e.bind("<Return>", self.do_login)
            fields.append(e)
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
        empty = [e for e, v in zip(fields, (self.v_url, self.v_user, self.v_pass)) if not v.get()]
        (empty[0] if empty else self.login_btn).focus_set()

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
            cfg["romm_url"], cfg["username"] = url, user
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

    def log_out(self) -> None:
        if self.busy:
            return
        self.launcher.client = None
        self.games, self.selected = [], None
        self.show_login("Logged out.", "muted")

    # ----- library screen -------------------------------------------------

    def show_library(self) -> None:
        self._clear()
        outer = tk.Frame(self.root, bg=THEME["bg"])
        outer.pack(fill="both", expand=True)
        self.screen = outer

        top = tk.Frame(outer, bg=THEME["panel"], height=self.S(58))
        top.pack(side="top", fill="x")
        top.pack_propagate(False)
        self.label(top, APP_NAME, 14, "bold").pack(side="left", padx=self.S(20))
        self.v_search = tk.StringVar()
        self.v_search.trace_add("write", lambda *a: self.refresh_grid())
        self.label(top, "Search", 9, fg=THEME["muted"]).pack(side="left", padx=(self.S(24), self.S(8)))
        search = self.entry(top, self.v_search, width=30)
        search.pack(side="left", ipady=self.S(5))
        search.bind("<Escape>", lambda e: self.v_search.set(""))
        self.button(top, "Log out", self.log_out).pack(side="right", padx=(self.S(6), self.S(16)))
        self.button(top, "Settings", self.show_settings).pack(side="right", padx=self.S(6))
        self.button(top, "Refresh", self.load_library).pack(side="right", padx=self.S(6))
        cfg = self.launcher.config
        where = "offline" if not self.launcher.client else urllib.parse.urlsplit(cfg["romm_url"]).hostname
        self.label(top, f"{cfg.get('username', '')} · {where}", 9, fg=THEME["muted"]).pack(side="right", padx=self.S(12))

        bar = tk.Frame(outer, bg=THEME["panel"], height=self.S(34))
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.status = self.label(bar, "", 9, fg=THEME["muted"], anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=self.S(16))
        self.progress = ttk.Progressbar(bar, length=self.S(260), mode="determinate", maximum=100)

        body = tk.Frame(outer, bg=THEME["bg"])
        body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, bg=THEME["panel"], width=self.S(220))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.details = tk.Frame(body, bg=THEME["panel"], width=self.S(360))
        self.details.pack(side="right", fill="y")
        self.details.pack_propagate(False)
        self.detail_box = tk.Frame(self.details, bg=THEME["panel"])
        self.detail_box.pack(side="top", fill="both", expand=True, padx=self.S(20), pady=(self.S(18), 0))
        activity = tk.Frame(self.details, bg=THEME["panel"])
        activity.pack(side="bottom", fill="x", padx=self.S(20), pady=(0, self.S(14)))
        self.label(activity, "ACTIVITY", 8, "bold", fg=THEME["muted"]).pack(anchor="w")
        self.activity = tk.Text(activity, height=9, wrap="word", font=self.font(9), fg=THEME["info"],
                                bg=THEME["bg"], relief="flat", padx=self.S(8), pady=self.S(6),
                                highlightthickness=0, state="disabled", cursor="arrow")
        self.activity.pack(fill="x", pady=(self.S(4), 0))
        for kind in ("ok", "warn", "fail", "info", "muted"):
            self.activity.tag_configure(kind, foreground=THEME[kind])

        center = tk.Frame(body, bg=THEME["bg"])
        center.pack(side="left", fill="both", expand=True)
        self.heading = self.label(center, "", 16, "bold", anchor="w")
        self.heading.pack(fill="x", padx=self.S(24), pady=(self.S(18), self.S(8)))
        self.canvas = tk.Canvas(center, bg=THEME["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(center, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True, padx=(self.S(14), 0))
        self.grid_frame = tk.Frame(self.canvas, bg=THEME["bg"])
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._bind_wheel(False))
        self.columns = 0
        self.refresh_sidebar()
        self.show_details(None)

    def _bind_wheel(self, on: bool) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            if on:
                self.root.bind_all(seq, self._on_wheel)
            else:
                self.root.unbind_all(seq)

    def _on_wheel(self, event) -> None:
        if getattr(event, "num", None) in (4, 5):
            step = -3 if event.num == 4 else 3
        else:
            step = -int(event.delta / 40) if abs(event.delta) >= 40 else (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")

    def _on_canvas_resize(self, event) -> None:
        columns = max(1, (event.width - self.S(10)) // (self.TILE_W + self.S(26)))
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
                self.set_status("Couldn't load the library.")
                return
            self.games, self.local_ids = result
            if self.selected:
                self.selected = next((g for g in self.games if g.id == self.selected.id), None)
            self.refresh_sidebar()
            self.refresh_grid()
            self.show_details(self.selected)
            self.set_status(f"{len(self.games)} games · {len(self.local_ids)} on this computer")
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
        factor = max(1, math.ceil(max(image.width() / self.TILE_W, image.height() / self.COVER_H)))
        self.tile_images[game.id] = image.subsample(factor) if factor > 1 else image
        tile = self.tiles.get(game.id)
        if tile:
            tile[1].config(image=self.tile_images[game.id], text="", bg=THEME["bg"])
        if self.selected and self.selected.id == game.id:
            self.show_details(self.selected)

    def refresh_local(self) -> None:
        self.local_ids = local_game_ids(self.games, roms_root(self.launcher.config))
        self.refresh_sidebar()
        self.refresh_grid(keep_scroll=True)

    def refresh_sidebar(self) -> None:
        for w in self.sidebar.winfo_children():
            w.destroy()
        self.label(self.sidebar, "LIBRARY", 8, "bold", fg=THEME["muted"]).pack(
            anchor="w", padx=self.S(20), pady=(self.S(20), self.S(6)))
        platforms_started = False
        for key, text, count in library_filters(self.games, self.local_ids):
            if key.startswith("platform:") and not platforms_started:
                platforms_started = True
                self.label(self.sidebar, "PLATFORMS", 8, "bold", fg=THEME["muted"]).pack(
                    anchor="w", padx=self.S(20), pady=(self.S(18), self.S(6)))
            active = key == self.filter_key
            bg = THEME["card"] if active else THEME["panel"]
            row = tk.Frame(self.sidebar, bg=bg, cursor="hand2")
            row.pack(fill="x", padx=self.S(8))
            name = self.label(row, text, 10, "bold" if active else "normal", anchor="w", bg=bg)
            name.pack(side="left", padx=self.S(12), pady=self.S(7))
            num = self.label(row, str(count), 9, fg=THEME["muted"], bg=bg)
            num.pack(side="right", padx=self.S(12))
            for w in (row, name, num):
                w.bind("<Button-1>", lambda e, k=key: self.set_filter(k))

    def set_filter(self, key: str) -> None:
        self.filter_key = key
        self.refresh_sidebar()
        self.refresh_grid()

    def filter_title(self) -> str:
        for key, text, _ in library_filters(self.games, self.local_ids):
            if key == self.filter_key:
                return text
        return "All games"

    def refresh_grid(self, keep_scroll: bool = False) -> None:
        if not hasattr(self, "grid_frame"):
            return
        query = self.v_search.get()
        shown = filter_library(self.games, self.filter_key, query, self.local_ids)
        title = self.filter_title() + (f"  ·  “{query.strip()}”" if query.strip() else "")
        self.heading.config(text=f"{title}  ({len(shown)})")
        top = self.canvas.yview()[0] if keep_scroll else 0
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.tiles = {}
        columns = max(1, self.columns)
        for i, game in enumerate(shown):
            tile = self.make_tile(game)
            tile.grid(row=i // columns, column=i % columns, padx=self.S(9), pady=self.S(9), sticky="n")
        if not shown:
            text = "No games match your search." if query.strip() else (
                "No games here yet." if self.games else "Loading…")
            self.label(self.grid_frame, text, 11, fg=THEME["muted"], bg=THEME["bg"]).grid(
                padx=self.S(10), pady=self.S(30))
        self.grid_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(top)

    def platform_color(self, slug: str) -> str:
        return PLATFORM_COLORS[sum(map(ord, slug)) % len(PLATFORM_COLORS)]

    def make_tile(self, game: Game):
        selected = bool(self.selected and self.selected.id == game.id)
        frame = tk.Frame(self.grid_frame, bg=THEME["select"] if selected else THEME["card"],
                         padx=self.S(3), pady=self.S(3), cursor="hand2")
        inner = tk.Frame(frame, bg=THEME["card"])
        inner.pack()
        image = self.tile_images.get(game.id)
        if image:
            cover = tk.Label(inner, image=image, width=self.TILE_W, height=self.COVER_H, bg=THEME["bg"], bd=0)
        else:
            cover = tk.Label(inner, image=self.px, compound="center", width=self.TILE_W, height=self.COVER_H,
                             text=f"{game.platform_name}\n\n{game.name}", wraplength=self.TILE_W - self.S(20),
                             font=self.font(10, "bold"), fg=THEME["text"], bg=self.platform_color(game.platform),
                             bd=0)
        cover.pack()
        name = game.name if len(game.name) <= 44 else game.name[:43] + "…"
        title = self.label(inner, name, 9, "bold", anchor="nw", justify="left",
                           wraplength=self.TILE_W - self.S(8), height=2, width=1)
        title.pack(fill="x", padx=self.S(6), pady=(self.S(6), 0))
        local = game.id in self.local_ids
        badge = self.label(inner, "● On this computer" if local else "☁  In the cloud", 8,
                           fg=THEME["ok"] if local else THEME["muted"], anchor="w")
        badge.pack(fill="x", padx=self.S(6), pady=(self.S(2), self.S(6)))
        for w in (frame, inner, cover, title, badge):
            w.bind("<Button-1>", lambda e, g=game: self.select(g))
            w.bind("<Double-Button-1>", lambda e, g=game: self.play(g))
        frame.bind("<Enter>", lambda e: self._hover(game, True))
        frame.bind("<Leave>", lambda e: self._hover(game, False))
        self.tiles[game.id] = (frame, cover)
        return frame

    def _hover(self, game: Game, on: bool) -> None:
        tile = self.tiles.get(game.id)
        if tile and not (self.selected and self.selected.id == game.id):
            tile[0].config(bg=THEME["hover"] if on else THEME["card"])

    def select(self, game: Game) -> None:
        old = self.selected
        self.selected = game
        if old and old.id in self.tiles:
            self.tiles[old.id][0].config(bg=THEME["card"])
        if game.id in self.tiles:
            self.tiles[game.id][0].config(bg=THEME["select"])
        self.show_details(game)

    # ----- game details ---------------------------------------------------

    def show_details(self, game: Game | None) -> None:
        box = self.detail_box
        for w in box.winfo_children():
            w.destroy()
        if game is None:
            self.label(box, "Select a game", 12, fg=THEME["muted"]).pack(pady=self.S(60))
            self.label(box, "Double-click a cover to play.", 9, fg=THEME["muted"]).pack()
            return
        image = self.covers.get(game.id)
        if image:
            factor = max(1, math.ceil(max(image.width() / self.DETAIL_W, image.height() / self.DETAIL_H)))
            self._detail_image = image.subsample(factor) if factor > 1 else image
            tk.Label(box, image=self._detail_image, bg=THEME["panel"]).pack(pady=(0, self.S(12)))
        self.label(box, game.name, 15, "bold", wraplength=self.S(320), justify="left", anchor="w").pack(fill="x")
        self.label(box, game.platform_name, 10, fg=THEME["muted"], anchor="w").pack(fill="x", pady=(self.S(2), self.S(10)))

        local = game.id in self.local_ids
        size = game.rom.get("fs_size_bytes")
        if local:
            status, color = "● On this computer", THEME["ok"]
        else:
            status = "☁  In the cloud · downloads when you play" + (f" ({human_size(size)})" if size else "")
            color = THEME["muted"]
        self.label(box, status, 9, fg=color, anchor="w", wraplength=self.S(320), justify="left").pack(fill="x")
        entry = self.launcher.state.get(str(game.id), {})
        played = entry.get("last_played", "")
        self.label(box, f"Last played: {played.replace('T', ' ')[:16] if played else 'never'}", 9,
                   fg=THEME["muted"], anchor="w").pack(fill="x", pady=(self.S(2), 0))
        if entry.get("pending_upload"):
            self.label(box, "! The last save hasn't reached RomM yet", 9, fg=THEME["warn"], anchor="w").pack(fill="x")
        elif entry.get("synced_at"):
            self.label(box, f"Save synced: {entry['synced_at']}", 9, fg=THEME["muted"], anchor="w").pack(fill="x")

        choices = screen_choices(game)
        self.v_layout = tk.StringVar(value=entry.get("screen_layout") if entry.get("screen_layout") in choices else "both")
        if choices:
            self.label(box, "SCREEN LAYOUT", 8, "bold", fg=THEME["muted"], anchor="w").pack(
                fill="x", pady=(self.S(16), self.S(4)))
            for choice in choices:
                tk.Radiobutton(box, text=SCREEN_CHOICES[choice], value=choice, variable=self.v_layout,
                               font=self.font(9), fg=THEME["text"], bg=THEME["panel"], anchor="w",
                               activebackground=THEME["panel"], activeforeground=THEME["text"],
                               selectcolor=THEME["entry"], bd=0, highlightthickness=0,
                               wraplength=self.S(300), justify="left").pack(fill="x", pady=self.S(1))
            touch = TOUCH_CONTROLS.get(core_id(game.core or ""))
            if touch:
                self.label(box, touch["help"], 8, fg=THEME["muted"], anchor="w", wraplength=self.S(320),
                           justify="left").pack(fill="x", pady=(self.S(4), 0))

        playing = self.busy and getattr(self, "playing", None) is game
        play = self.button(box, "Playing…" if playing else "Play", lambda: self.play(game), primary=True, size=12)
        play.pack(fill="x", pady=(self.S(18), self.S(6)))
        if self.busy:
            play.config(state="disabled")
        row = tk.Frame(box, bg=THEME["panel"])
        row.pack(fill="x")
        if self.launcher.client and not local:
            b = self.button(row, "Download only", lambda: self.play(game, launch=False))
            b.pack(side="left", fill="x", expand=True, padx=(0, self.S(3)))
            if self.busy:
                b.config(state="disabled")
        if self.launcher.client and local and game.syncs_saves:
            b = self.button(row, "Upload save", lambda: self.upload_save(game))
            b.pack(side="left", fill="x", expand=True)
            if self.busy:
                b.config(state="disabled")

    def play(self, game: Game, launch: bool = True) -> None:
        if self.busy:
            return
        if self.selected is not game:
            self.select(game)
        choices = screen_choices(game)
        screen = self.v_layout.get() if choices else None
        self.busy, self.playing = True, game
        self.show_details(game)
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

        self.run_task(lambda: self.launcher.play(game, launch=launch, screen=screen), done)

    def upload_save(self, game: Game) -> None:
        if self.busy:
            return
        self.busy = True
        self.show_details(game)

        def done(result):
            self.busy = False
            if isinstance(result, BaseException) and not isinstance(result, Quit):
                self.log(str(result), "fail")
            self.show_details(self.selected)

        self.run_task(lambda: self.launcher.upload_now(game), done)

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
            self.set_status(line)

    def set_status(self, text: str) -> None:
        if hasattr(self, "status") and self.status.winfo_exists():
            self.status.config(text=text)

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
        """A question with one radio button per option; returns the chosen key, or None."""
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
            self.launcher.save_config()
            dlg.destroy()
            if not retroarch_installed(cfg["retroarch"]):
                self.log(f"RetroArch not found: {cfg['retroarch']}", "warn")
            self.refresh_local()

        row = tk.Frame(dlg, bg=THEME["panel"])
        row.pack(fill="x", pady=(self.S(16), 0))
        self.button(row, "Find RetroArch", detect).pack(side="left")
        self.button(row, "Cancel", dlg.destroy).pack(side="right")
        self.button(row, "Save", save, primary=True).pack(side="right", padx=self.S(8))
        self._run_dialog(dlg)

    def on_close(self) -> None:
        if self.busy and getattr(self, "playing", None) and not messagebox.askyesno(
            APP_NAME, "A game is still running. If you quit now, its save is uploaded the next "
                      "time you play it.\n\nQuit anyway?", parent=self.root, default="no"):
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
        gui.show_login()
        gui.root.mainloop()
    finally:
        set_ui(TerminalUI())
    return 0
