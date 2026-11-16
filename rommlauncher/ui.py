"""Talking to the player: messages, questions and progress bars.

Everything goes through the `UI` object: the terminal (TerminalUI) by default, or the
window (gui.GuiUI) after `set_ui`. Launcher code calls say/ok/warn/fail, ask, ask_yes,
choose and progress_bar, never print() or input() for questions.
"""

from __future__ import annotations

import os
import sys
import time


_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.name != "nt"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def red(t: str) -> str:
    return _c("31", t)


class Quit(Exception):
    """The user asked to leave (q, Ctrl+C or Ctrl+D at a prompt)."""


class TerminalUI:
    """How the launcher talks to the player: here, the terminal. The GUI puts its own
    object with the same methods in place (set_ui), so the launcher's code is shared."""

    def say(self, text: str = "", kind: str = "info") -> None:
        mark = {"ok": green("✔"), "warn": yellow("!"), "fail": red("✖")}.get(kind)
        line = f"{mark} {text}" if mark else text
        print(f"  {line}" if line else "")

    def ask(self, prompt: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        end = " " if prompt.endswith(">") else ": "
        try:
            answer = input(f"  {prompt}{hint}{end}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise Quit
        return answer or default

    def ask_yes(self, prompt: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            answer = self.ask(f"{prompt} ({hint})").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False

    def choose(self, question: str, options: list[tuple[str, str]], prompt: str,
               default: str = "", cancel: bool = False, kind: str = "info", note: str = "") -> str | None:
        """Pick one of `options` ((key, label) pairs); returns its key, or None when
        `cancel` is allowed and the player types q."""
        print()
        if question:
            self.say(question, kind)
        for key, label in options:
            self.say(f"    {key}  {label}")
        if note:
            self.say(dim(f"    {note}"))
        keys = [key for key, _ in options]
        while True:
            answer = self.ask(prompt, default)
            if cancel and answer.lower() == "q":
                return None
            if answer in keys or answer.lower() in keys:
                return answer if answer in keys else answer.lower()

    def progress(self, label: str, total: int | None) -> "Progress":
        return Progress(label, total)


UI = TerminalUI()


def set_ui(ui) -> None:
    global UI
    UI = ui


def say(text: str = "") -> None:
    UI.say(text)


def ok(text: str) -> None:
    UI.say(text, "ok")


def warn(text: str) -> None:
    UI.say(text, "warn")


def fail(text: str) -> None:
    UI.say(text, "fail")


def ask(prompt: str, default: str = "") -> str:
    return UI.ask(prompt, default)


def ask_yes(prompt: str, default: bool = True) -> bool:
    return UI.ask_yes(prompt, default)


def choose(question: str, options: list[tuple[str, str]], prompt: str, **kw) -> str | None:
    return UI.choose(question, options, prompt, **kw)


def progress_bar(label: str, total: int | None):
    """A progress display: call it with the bytes done so far, then .finish()."""
    return UI.progress(label, total)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Progress:
    """A one-line progress bar: `Downloading  [█████░░░░░]  52%  16.6 / 32.0 MB`."""

    def __init__(self, label: str, total: int | None):
        self.label, self.total, self.last = label, total or None, 0.0

    def __call__(self, done: int) -> None:
        now = time.monotonic()
        if now - self.last < 0.1 and done != self.total:
            return
        self.last = now
        if self.total:
            frac = min(done / self.total, 1)
            bar = "█" * int(frac * 24) + "░" * (24 - int(frac * 24))
            line = f"{self.label}  [{bar}] {frac:4.0%}  {human_size(done)} / {human_size(self.total)}"
        else:
            line = f"{self.label}  {human_size(done)}"
        if sys.stdout.isatty():
            print(f"\r  {line}   ", end="", flush=True)

    def finish(self) -> None:
        if sys.stdout.isatty():
            print("\r" + " " * 78 + "\r", end="", flush=True)
