"""Presentation helpers: pacing, a fake shell prompt, and code display.

A demo is a piece of writing that happens to execute. These helpers keep the
narration out of the scenario code and make the pacing a setting rather than
a pile of sleeps, so the same script is both the thing that gets recorded and
something CI can run at zero delay.
"""

from __future__ import annotations

import os
import sys
import time

# A recorder reads this through a pipe, and Python block-buffers a pipe. The
# pauses below would then all land at once and the recording would have no
# timing at all, so the stream is reconfigured to flush per line.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

SPEED = float(os.environ.get("DEMO_SPEED", "1.0"))
"""Multiplier on every pause. ``DEMO_SPEED=0`` runs flat out, for tests."""

WIDTH = 78

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

_COLOUR = sys.stdout.isatty() or os.environ.get("DEMO_COLOUR") == "1"


def _paint(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}" if _COLOUR else text


def pause(seconds: float = 1.0) -> None:
    if SPEED > 0:
        time.sleep(seconds * SPEED)


def say(text: str = "", *, after: float = 0.4) -> None:
    """One line of narration."""
    print(text)
    pause(after)


def beat(title: str, subtitle: str = "") -> None:
    """A section break, so the viewer knows where they are."""
    print()
    print(_paint("─" * WIDTH, DIM))
    print(_paint(title, BOLD))
    if subtitle:
        print(_paint(subtitle, DIM))
    print(_paint("─" * WIDTH, DIM))
    print()
    pause(0.8)


def shell(command: str, *, after: float = 0.6) -> None:
    """Show a command as if someone typed it."""
    print(f"{_paint('$', GREEN)} {_paint(command, BOLD)}")
    pause(after)


def code(source: str, *, after: float = 1.2) -> None:
    """Show a snippet the viewer is meant to read."""
    for line in source.strip("\n").splitlines():
        print(f"  {_paint(line, CYAN)}")
    print()
    pause(after)


def note(text: str, *, after: float = 0.8) -> None:
    """An aside. Dimmed, because it is commentary rather than output."""
    print(_paint(f"  {text}", DIM))
    pause(after)


def good(text: str) -> None:
    print(_paint(f"  {text}", GREEN))
    pause(0.4)


def warn(text: str) -> None:
    print(_paint(f"  {text}", YELLOW))
    pause(0.4)


def bad(text: str) -> None:
    print(_paint(f"  {text}", RED))
    pause(0.4)


def out(text: str = "", *, after: float = 0.15) -> None:
    """Program output, indented so it reads as a response rather than prose."""
    print(f"  {text}")
    pause(after)


def answer(text: str, *, after: float = 0.8) -> None:
    """A model's reply, wrapped so it does not disappear off the side.

    Terminal recordings are read at whatever width the recorder chose, so a
    150 character sentence wraps mid-word and looks like a bug. Wrapping it
    here keeps the demo legible wherever it is played back.
    """
    import textwrap

    for line in textwrap.wrap(text, width=WIDTH - 4) or [""]:
        print(f"  {line}")
    pause(after)
