"""Synthetic keyboard/mouse input for driving Borderlands 2 (``SendInput`` via ctypes).

Why this exists: BL2's Scaleform menus read the raw keyboard, so the only inputs that reach
them are Enter/Escape/arrows/F-keys (F15 in ``docs/FAILURE_MODES.md``) -- letters and digits are
swallowed. That is enough to drive every menu we need, provided the events look like real
hardware events.

Two encodings are supported:

``scancode`` (default)
    ``KEYEVENTF_SCANCODE`` events carrying the PS/2 set-1 scan code, with ``KEYEVENTF_EXTENDEDKEY``
    for the E0-prefixed keys (arrows, keypad Enter, right Ctrl/Alt...). This is what real keyboards
    produce and what DirectInput / raw-input consumers such as BL2 read. **Verified working against
    the live game** (2026-09-19): Escape opens the pause menu, arrows move the highlight, Enter
    activates. Use this.

``vk``
    Virtual-key events (``wVk`` set, ``wScan`` 0). Kept as a fallback and for the ``--probe``
    comparison; the OS fills in a scan code for these, so they usually work too, but a game that
    reads ``wScan`` directly from the raw-input packet sees zero.

Everything here is Windows-only and stdlib-only (ctypes).
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- win32 plumbing
if sys.platform != "win32":  # pragma: no cover - the whole module is Windows-only
    raise ImportError("bl2_verify.winput requires Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000

SM_CXSCREEN = 0
SM_CYSCREEN = 1

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

# --------------------------------------------------------------------------- key table
# name -> (set-1 scan code, extended?, virtual-key code)
KEYS: dict[str, tuple[int, bool, int]] = {
    "esc": (0x01, False, 0x1B),
    "escape": (0x01, False, 0x1B),
    "enter": (0x1C, False, 0x0D),
    "return": (0x1C, False, 0x0D),
    "space": (0x39, False, 0x20),
    "tab": (0x0F, False, 0x09),
    "backspace": (0x0E, False, 0x08),
    "up": (0x48, True, 0x26),
    "down": (0x50, True, 0x28),
    "left": (0x4B, True, 0x25),
    "right": (0x4D, True, 0x27),
    "lalt": (0x38, False, 0xA4),
}
for _i in range(1, 11):  # F1..F10 are 0x3B..0x44
    KEYS[f"f{_i}"] = (0x3A + _i, False, 0x6F + _i)
KEYS["f11"] = (0x57, False, 0x7A)
KEYS["f12"] = (0x58, False, 0x7B)

MODES = ("scancode", "vk")
_mode = os.environ.get("BL2_WINPUT_MODE", "scancode").lower()
if _mode not in MODES:
    _mode = "scancode"


def set_mode(mode: str) -> None:
    """Choose the event encoding: ``"scancode"`` (default, verified) or ``"vk"``."""
    global _mode
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    _mode = mode


def get_mode() -> str:
    return _mode


def _key_inputs(name: str, up: bool) -> INPUT:
    try:
        scan, extended, vk = KEYS[name.lower()]
    except KeyError:
        raise KeyError(f"unknown key {name!r}; known: {sorted(KEYS)}") from None
    flags = KEYEVENTF_KEYUP if up else 0
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if _mode == "scancode":
        ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags | KEYEVENTF_SCANCODE, time=0, dwExtraInfo=0)
    else:
        ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = ki
    return inp


def _send(inputs: Sequence[INPUT]) -> int:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput sent {sent}/{len(inputs)}: {ctypes.get_last_error()}")
    return sent


def key_down(name: str) -> None:
    _send([_key_inputs(name, up=False)])


def key_up(name: str) -> None:
    _send([_key_inputs(name, up=True)])


def press(key: str, hold: float = 0.06) -> None:
    """Press and release one key. ``hold`` is the down-time in seconds."""
    key_down(key)
    time.sleep(hold)
    key_up(key)


def press_seq(keys: Iterable[str], delay: float = 0.35, hold: float = 0.06) -> None:
    """Press a list of keys in order, sleeping ``delay`` between them.

    Menu navigation needs the delay: BL2's menus animate the highlight and drop inputs that
    arrive while the animation is running. 0.3-0.4 s is comfortable.
    """
    for k in keys:
        press(k, hold=hold)
        time.sleep(delay)


def move_mouse(x: int, y: int) -> None:
    sw = user32.GetSystemMetrics(SM_CXSCREEN) or 1920
    sh = user32.GetSystemMetrics(SM_CYSCREEN) or 1080
    nx = int(x * 65535 / max(sw - 1, 1))
    ny = int(y * 65535 / max(sh - 1, 1))
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, time=0, dwExtraInfo=0)
    _send([inp])


def click(x: int | None = None, y: int | None = None, delay: float = 0.08) -> None:
    """Left-click, optionally moving to absolute screen coordinates first."""
    if x is not None and y is not None:
        move_mouse(x, y)
        time.sleep(delay)
    down = INPUT()
    down.type = INPUT_MOUSE
    down.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTDOWN, time=0, dwExtraInfo=0)
    up = INPUT()
    up.type = INPUT_MOUSE
    up.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTUP, time=0, dwExtraInfo=0)
    _send([down])
    time.sleep(delay)
    _send([up])


def mouse_button(button: str = "left", hold: float = 0.1) -> None:
    """Press and release a mouse button where the cursor is (in game: fire / aim)."""
    flags = {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
             "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)}[button]
    for flag in flags:
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)
        _send([inp])
        if flag == flags[0]:
            time.sleep(hold)


# --------------------------------------------------------------------------- window focus
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_windows(pid: int | None = None, title_contains: str | None = None) -> list[int]:
    """Top-level visible windows belonging to ``pid`` and/or whose title contains a string."""
    found: list[int] = []

    def cb(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if pid is not None:
            wpid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value != pid:
                return True
        if title_contains is not None and title_contains.lower() not in window_title(hwnd).lower():
            return True
        found.append(int(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def foreground_pid() -> int:
    hwnd = user32.GetForegroundWindow()
    wpid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
    return int(wpid.value)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


user32.WindowFromPoint.argtypes = (POINT,)
user32.WindowFromPoint.restype = wintypes.HWND


def window_pid(hwnd: int) -> int:
    wpid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
    return int(wpid.value)


def is_foreground(hwnd: int) -> bool:
    return bool(user32.GetForegroundWindow() == hwnd)


def window_at(x: int, y: int) -> int:
    """The top-level window covering a screen point (child windows resolved to their root)."""
    hwnd = user32.WindowFromPoint(POINT(x, y))
    return int(user32.GetAncestor(hwnd, 2) or hwnd or 0)  # GA_ROOT


def is_on_screen(hwnd: int, point: tuple[int, int] = (960, 540)) -> bool:
    """True when the process owning ``hwnd`` really owns the pixel at ``point``.

    ``GetForegroundWindow`` is not enough. BL2 runs exclusive fullscreen and **minimises itself
    whenever another process takes the activation** -- and Windows leaves that minimised window
    as the "foreground" one, parked at (-32000, -32000). In that state key injection still
    reports success and screen captures return whatever is on the desktop instead. So ask the
    screen: who owns the middle pixel?

    The owner is compared by *process*, not by handle, because the game swaps between its main
    window and a ``D3DProxyWindow`` of its own around fullscreen mode switches.
    """
    if user32.IsIconic(hwnd):
        return False
    owner = window_at(*point)
    return bool(owner) and window_pid(owner) == window_pid(hwnd)


def is_active(hwnd: int) -> bool:
    """The only state worth acting on: the game owns the keyboard **and** owns the screen.

    Both tests are by process id: around a fullscreen mode switch the foreground window is the
    game's own ``D3DProxyWindow`` rather than its main window, which is still a perfectly good
    state to type into and capture.
    """
    pid = window_pid(hwnd)
    fg = user32.GetForegroundWindow()
    return bool(fg) and window_pid(fg) == pid and is_on_screen(hwnd)


def focus(hwnd: int, attempts: int = 8) -> bool:
    """Bring ``hwnd`` forward and **verify** it: foreground, not minimised, actually on screen.

    Two Windows behaviours make the naive version useless here:

    * ``SetForegroundWindow`` returns success while doing nothing, whenever the calling process
      does not own the foreground (the foreground lock only flashes the taskbar button).
    * BL2 runs exclusive fullscreen and minimises itself the instant something else activates.
      Windows leaves that minimised window as the *foreground* window, parked off-screen, so
      ``GetForegroundWindow`` agrees with us while the user is looking at the desktop.

    Restoring from minimised is a fullscreen mode switch and takes a second or so, hence the
    generous sleeps. Strategies rotate: plain activation, an Alt tap (which lifts the foreground
    lock for this thread), ``AttachThreadInput`` to the current foreground thread, and
    ``SwitchToThisWindow``, the call Alt-Tab uses.
    """
    for attempt in range(attempts):
        if is_active(hwnd):
            return True
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE; fullscreen mode switch, give it time
            time.sleep(1.5)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            continue
        strategy = attempt % 4
        if strategy == 0:
            user32.SetForegroundWindow(hwnd)
        elif strategy == 1:
            try:
                key_down("lalt")
                key_up("lalt")
            except OSError:
                pass
            user32.SetForegroundWindow(hwnd)
        elif strategy == 2:
            fg = user32.GetForegroundWindow()
            cur = kernel32.GetCurrentThreadId()
            other = user32.GetWindowThreadProcessId(fg, None)
            if other and user32.AttachThreadInput(cur, other, True):
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(cur, other, False)
        else:
            user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.8)
    return is_active(hwnd)


def focus_pid(pid: int, timeout: float = 20.0) -> bool:
    """Focus the first visible top-level window owned by ``pid``, verifying it came forward."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd in find_windows(pid=pid):
            if focus(hwnd):
                time.sleep(0.4)
                return True
        time.sleep(0.5)
    return False


__all__ = [
    "KEYS", "click", "find_windows", "focus", "focus_pid", "foreground_pid", "get_mode",
    "is_active", "is_foreground", "is_on_screen", "window_at", "window_pid",
    "key_down", "key_up", "mouse_button", "move_mouse", "press", "press_seq", "set_mode", "window_title",
]
