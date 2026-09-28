"""Drive Borderlands 2 from Python: launch, menus, captures, save-and-quit, quit.

Everything the unattended verify loop needs to get the game from "not running" to "standing in
Sanctuary holding the test weapon" and back out again, using only the inputs the game accepts
(Enter / Escape / arrows / F-keys -- F15) plus one mouse click to dismiss the legal screen.

Sequence knowledge (measured at 1920x1080):

==========================================  ===========================================
step                                        how
==========================================  ===========================================
launch                                      kill any ``Launcher.exe`` (F10), start
                                            ``Binaries\\Win32\\Borderlands2.exe`` directly
                                            with Steam running; wait for
                                            ``pyunrealsdk ... loaded`` in ``unrealsdk.log``
intro / legal screen                        one left click, then Escape
"Creating online session" spinner           clears ~25 s after the SDK loads
MAIN MENU                                   CONTINUE is highlighted; Enter loads the
                                            last character (40-60 s)
pause menu (Escape in game)                 RESUME / NETWORK MODE / SOCIAL / OPTIONS /
                                            ACHIEVEMENTS / MODS / QUIT
save and quit                               Escape, Down x6 (QUIT), Enter, Up, Enter
quit from the main menu                     Down x9 (QUIT), Enter, Up, Enter
==========================================  ===========================================

CLI self-test (the game must already be running and in a map)::

    python -m bl2_verify.game_driver --probe            # pause menu -> capture -> resume
    python -m bl2_verify.game_driver --probe --mode vk  # same with virtual-key events
    python -m bl2_verify.game_driver --menu-probe       # launch, capture the main menu, quit
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

if __package__ in (None, ""):  # allow `python src/bl2_verify/game_driver.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bl2_verify import winput  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
EXE = GAME / "Binaries" / "Win32" / "Borderlands2.exe"
SDK_LOG = GAME / "Binaries" / "Win32" / "Plugins" / "unrealsdk.log"
CAPTURE_PS1 = Path(__file__).resolve().parent / "capture.ps1"
SCRATCH = REPO / "scratch"
DRIVER_LOG = SCRATCH / "driver.log"
_SKEW = timedelta(seconds=10)  # tolerance when matching log lines against the launch time

# Main-menu pixel probe (1920x1080, calibrated 2026-09-19 from scratch/cal_menu_final.png).
# The main menu draws the *highlighted* entry in Borderlands yellow and CONTINUE is highlighted
# when the menu opens, so "yellow pixels in the CONTINUE box" is a clean menu detector: ~3,800
# on the main menu, exactly 0 on the legal screen, the "Press any key" title, the attract movie
# and the "Creating online session" spinner.
CONTINUE_BOX = (140, 125, 330, 185)
CONTINUE_MIN_YELLOW = 500
# Bottom entry of the main menu (QUIT), highlighted after walking down. Sanity log only, and
# deliberately a tall box: the entry count -- and so the y of the last row -- changes between
# a cold main menu (11 rows, QUIT at y~770) and the menu you return to after a save-and-quit
# (QUIT at y~710). That variability is exactly why the walk down is unconditional.
QUIT_BOX = (140, 660, 360, 810)
# Neither the main menu nor the pause menu wraps around, so "press Down more times than there
# are entries" always lands on the last one (QUIT) whatever the entry count is.
MENU_DOWNS = 15
PAUSE_DOWNS = 10


class DriverError(RuntimeError):
    pass


# BL2 runs exclusive fullscreen and **minimises itself the moment it loses the foreground**.
# Every helper process we spawn (powershell for the capture, tasklist, taskkill) would otherwise
# flash a console window, steal focus, and drop the game to the taskbar mid-run -- the captures
# then show the desktop instead of the game. CREATE_NO_WINDOW keeps them invisible.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class GameDriver:
    """One game process, driven through its menus."""

    def __init__(
        self,
        exe: Path = EXE,
        sdk_log: Path = SDK_LOG,
        log_path: Path = DRIVER_LOG,
        key_mode: str = "scancode",
        echo: bool = True,
    ) -> None:
        self.exe = Path(exe)
        self.sdk_log = Path(sdk_log)
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo
        self.proc: subprocess.Popen[bytes] | None = None
        self.pid: int | None = None
        self.launch_time: float = 0.0
        winput.set_mode(key_mode)

    # ------------------------------------------------------------------ logging
    def log(self, msg: str) -> None:
        line = f"{_now()} +{time.time() - self.launch_time:7.1f}s {msg}" if self.launch_time \
            else f"{_now()}         {msg}"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.echo:
            print(line, flush=True)

    # ------------------------------------------------------------------ process
    @staticmethod
    def _tasklist(image: str) -> list[int]:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=30, check=False,
                creationflags=NO_WINDOW).stdout
        except Exception:  # noqa: BLE001
            return []
        pids = []
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == image.lower():
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    continue
        return pids

    def is_running(self) -> bool:
        """Is the game alive?

        Deliberately *not* ``tasklist``: this is polled every couple of seconds for minutes at a
        time while the game is in exclusive fullscreen, and spawning a process that often is
        exactly what knocks the game off the screen. ``OpenProcess`` + ``GetExitCodeProcess``
        answers the same question without creating anything.
        """
        if self.pid is None:
            return bool(self._tasklist(self.exe.name))
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)

    def kill(self) -> None:
        for pid in self._tasklist(self.exe.name):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                           check=False, creationflags=NO_WINDOW)
        self.log("taskkill Borderlands2.exe")

    # ------------------------------------------------------------------ launch
    def launch(self, timeout: float = 180.0) -> int:
        """Start the game and wait until pyunrealsdk has initialised in a *fresh* log."""
        for pid in self._tasklist("Launcher.exe"):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                           check=False, creationflags=NO_WINDOW)
            print(f"killed stray Launcher.exe pid {pid}")
        if self._tasklist(self.exe.name):
            raise DriverError("Borderlands2.exe is already running; quit it first")
        if not self.exe.exists():
            raise DriverError(f"missing exe: {self.exe}")
        self.launch_time = time.time()
        launch_utc = datetime.now(timezone.utc)
        self.proc = subprocess.Popen([str(self.exe)], cwd=str(self.exe.parent))
        self.pid = self.proc.pid
        self.log(f"launched {self.exe.name} pid={self.pid} key_mode={winput.get_mode()}")
        line = self.wait_log("pyunrealsdk", timeout=timeout, since=launch_utc, extra="loaded")
        self.log(f"sdk up: {line.strip()[-80:]}")
        # The game may re-exec through Steam; re-resolve the pid from the image name.
        pids = self._tasklist(self.exe.name)
        if pids and self.pid not in pids:
            self.log(f"pid {self.pid} is gone, adopting {pids[0]}")
            self.pid = pids[0]
        return self.pid

    @staticmethod
    def _log_time(line: str) -> datetime | None:
        try:
            return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def wait_log(self, pattern: str, timeout: float = 120.0,
                 since: datetime | None = None, extra: str | None = None) -> str:
        """Wait for a line in ``unrealsdk.log`` containing ``pattern`` (and ``extra``).

        ``since`` restricts the match to lines whose UTC timestamp is at or after that moment,
        which is what makes this safe across launches -- the log is truncated on start, but a
        slow rewrite could otherwise hand us the previous session's line.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = self.sdk_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for line in text.splitlines():
                if pattern not in line or (extra is not None and extra not in line):
                    continue
                if since is not None:
                    ts = self._log_time(line)
                    if ts is None or ts < since - _SKEW:
                        continue
                return line
            time.sleep(1.0)
        raise DriverError(f"timeout after {timeout}s waiting for {pattern!r} in {self.sdk_log}")

    # ------------------------------------------------------------------ input helpers
    def hwnd(self) -> int | None:
        wins = winput.find_windows(pid=self.pid) if self.pid else []
        if not wins:
            wins = winput.find_windows(title_contains="Borderlands")
        return wins[0] if wins else None

    def focus(self, quiet: bool = False) -> bool:
        """Bring the game forward and verify it actually got the foreground.

        Everything else depends on this. Synthetic input goes to whatever window has the
        foreground, and BL2 stops presenting frames when it loses it, so an unfocused game
        means both dropped keystrokes and captures of the previous frame (or of the desktop).
        """
        h = self.hwnd()
        ok = bool(h) and winput.focus(h)
        if not quiet or not ok:
            self.log(f"focus -> {ok}")
        if not ok:
            # Name the window that owns the foreground instead: a system error dialog
            # ("OneDrive.exe - Application Error", F27) parked over the game is invisible
            # in this log otherwise and every later step fails for the same reason.
            try:
                fg = winput.user32.GetForegroundWindow()
                self.log(f"foreground is {winput.window_title(fg)!r} (pid {winput.window_pid(fg)})"
                         if fg else "no foreground window")
            except Exception as ex:  # noqa: BLE001
                self.log(f"foreground title unavailable: {ex!r}")
        return ok

    def ensure_focus(self, settle: float = 0.4) -> bool:
        """Focus only if the game is not already in front; cheap enough to call everywhere."""
        h = self.hwnd()
        if h and winput.is_active(h):
            return True
        ok = self.focus()
        time.sleep(settle)
        return ok

    def _game_in_front(self) -> bool:
        """Focus the game, then confirm it really is the active window before any input.

        Keys and clicks go to whatever window is in front (SendInput has no target), so a
        failed focus must never be followed by a key: it would land in the user's browser.
        """
        self.ensure_focus()
        h = self.hwnd()
        return bool(h) and winput.is_active(h)

    def press(self, key: str, after: float = 0.4) -> None:
        if not self._game_in_front():
            self.log(f"key {key} NOT SENT: the game is not in front")
            time.sleep(after)
            return
        winput.press(key)
        self.log(f"key {key}")
        time.sleep(after)

    def press_seq(self, keys: Sequence[str], delay: float = 0.4) -> None:
        if not self._game_in_front():
            self.log(f"keys {' '.join(keys)} NOT SENT: the game is not in front")
            return
        winput.press_seq(keys, delay=delay)
        self.log(f"keys {' '.join(keys)}")

    # ------------------------------------------------------------------ menus
    def skip_intro(self, timeout: float = 180.0) -> bool:
        """Get from process start to the main menu, whatever attract screen is in the way.

        The path is longer than "click and press Escape": legal/logo movies, then a **Press any
        key** title screen, then (if the player waits) an attract cinematic, then the "Creating
        online session" spinner, and only then the menu. Rather than time each one, this loops:
        probe for the menu, and when it is not up yet push the screen along with Escape (skips a
        movie) and Enter (dismisses the title screen). The probe runs before every Enter, so the
        Enter that would otherwise trigger CONTINUE is never sent at the menu.
        """
        deadline = time.time() + timeout
        self.focus()
        time.sleep(2.0)
        winput.click(960, 540)  # dismisses the legal screen
        self.log("click 960,540 (intro)")
        time.sleep(2.0)
        first = True
        while time.time() < deadline:
            ok, n = self.probe_main_menu(SCRATCH / "menu_probe.png")
            self.log(f"intro: menu probe yellow={n} -> {ok}")
            if ok:
                self.log(f"main menu reached after {time.time() - self.launch_time:.0f}s")
                return True
            self.press("esc", after=2.5)
            ok, n = self.probe_main_menu(SCRATCH / "menu_probe.png")
            if ok:
                self.log(f"main menu reached after {time.time() - self.launch_time:.0f}s")
                return True
            if first or n == 0:
                self.press("enter", after=4.0)  # "Press any key"
                first = False
            time.sleep(3.0)
        raise DriverError(f"never reached the main menu within {timeout}s")

    def capture(self, path: str | os.PathLike[str], focus: bool = True,
                settle: float = 1.2) -> Path:
        """Screen-grab to PNG via ``capture.ps1``.

        The game must hold the foreground for this to be worth anything: unfocused, BL2 stops
        presenting and the grab returns a stale frame (six byte-identical PNGs is the signature)
        or the desktop behind it. So focus first, then give it a moment to draw.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        h = self.hwnd() if focus else None
        for attempt in range(5):
            if h is not None:
                self.ensure_focus()
                time.sleep(settle)
                if not winput.is_active(h):
                    self.log(f"capture {path.name}: game not on screen before the grab, retrying")
                    continue
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(CAPTURE_PS1), "-Out", str(path)],
                capture_output=True, text=True, timeout=120, check=False, creationflags=NO_WINDOW)
            if res.returncode != 0 or not path.exists():
                raise DriverError(f"capture failed: {res.returncode} {res.stdout} {res.stderr}")
            if h is not None and not winput.is_active(h):
                # The game minimised itself *during* the grab: the PNG holds the desktop, not
                # the game. Take it again.
                self.log(f"capture {path.name}: game left the screen during the grab, retrying")
                continue
            self.log(f"capture {path.name} ({res.stdout.strip()})")
            return path
        raise DriverError(f"capture {path}: the game would not stay in the foreground")

    @staticmethod
    def yellow_pixels(shot: Path, box: tuple[int, int, int, int]) -> int:
        """Count Borderlands-yellow pixels (the menu highlight colour) inside ``box``.

        ``box`` is calibrated at 1920x1080 and rescaled to the shot's own resolution
        (F31): at 2560x1440 the literal box lands above the menu entries, counts zero
        yellow forever, and every probe times out while the game is in fact fine.
        """
        from PIL import Image
        from bl2_verify.compare import scale_box
        with Image.open(shot) as im:
            im = im.convert("RGB")
            raw = im.crop(scale_box(box, im.size)).tobytes()
        return sum(1 for i in range(0, len(raw), 3)
                   if raw[i] > 170 and raw[i + 1] > 120 and raw[i + 2] < 110
                   and raw[i] - raw[i + 2] > 80)

    def capture_burst(self, directory: str | os.PathLike[str], stem: str,
                      count: int = 5, interval: float = 0.35) -> list[Path]:
        """Take ``count`` captures a fraction of a second apart: ``<stem>.png``, ``<stem>_f2.png``...

        One frame is not a reproducible measurement of a living character (see
        ``compare.best_pair``); a short burst samples several phases of the idle animation, and
        the first frame stays the run's canonical capture.
        """
        directory = Path(directory)
        shots = []
        for i in range(count):
            name = f"{stem}.png" if i == 0 else f"{stem}_f{i + 1}.png"
            shots.append(self.capture(directory / name, settle=0.0 if i else 1.2))
            if i + 1 < count:
                time.sleep(interval)
        self.log(f"burst {stem}: {len(shots)} frames")
        return shots

    def probe_main_menu(self, shot: Path | None = None) -> tuple[bool, int]:
        """Pixel probe: is the highlighted CONTINUE label drawn where the main menu puts it?

        Returns ``(looks_like_menu, yellow_pixel_count)``. Pillow only; if it is missing the
        probe degrades to "unknown" (True) and the caller falls back to the timer.
        """
        shot = shot or (SCRATCH / "menu_probe.png")
        try:
            self.capture(shot)
        except DriverError as ex:
            self.log(f"menu probe: {ex}")
            return False, -1
        try:
            n = self.yellow_pixels(shot, CONTINUE_BOX)
        except ImportError:  # pragma: no cover - Pillow is present in this repo
            return True, -1
        return n >= CONTINUE_MIN_YELLOW, n

    def wait_main_menu(self, timeout: float = 180.0, min_wait: float = 22.0,
                       probe: bool = True, shot: Path | None = None) -> bool:
        """Wait for the main menu: fixed settle time, then (optionally) the CONTINUE probe.

        Returns True when the probe confirmed the menu, False when it fell back to the timer.
        """
        time.sleep(min_wait)
        if not probe:
            self.log(f"main menu assumed after {min_wait}s")
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok, yellow = self.probe_main_menu(shot)
            self.log(f"menu probe: yellow={yellow} in {CONTINUE_BOX} -> {ok}")
            if ok:
                return True
            time.sleep(5.0)
        self.log("menu probe never confirmed; continuing on the timer")
        return False

    def continue_game(self, settle: float = 3.0) -> None:
        """Press Enter on CONTINUE, having first made sure CONTINUE is what is highlighted.

        Pressing Enter blind at the main menu is how you end up in NEW GAME or the quit dialog.
        The probe reads the CONTINUE label's own highlight colour, so it doubles as "CONTINUE is
        the selected entry"; when it is not, walking Up more times than there are entries puts
        the highlight back on the top entry (the menu does not wrap).
        """
        self.focus()
        ok, n = self.probe_main_menu(SCRATCH / "continue_probe.png")
        if not ok:
            self.log(f"CONTINUE not highlighted (yellow={n}); walking back to the top entry")
            self.press_seq(["up"] * MENU_DOWNS, delay=0.3)
            ok, n = self.probe_main_menu(SCRATCH / "continue_probe.png")
            self.log(f"after walking up: yellow={n} -> {ok}")
        self.press("enter", after=settle)
        self.log(f"CONTINUE pressed (highlight confirmed={ok})")

    def save_and_quit(self, wait_menu: float = 25.0) -> None:
        """In game: Escape -> Down to QUIT -> Enter -> Up (Save and Quit) -> Enter."""
        self.focus()
        self.press("esc", after=2.5)
        self.press_seq(["down"] * PAUSE_DOWNS, delay=0.35)
        time.sleep(0.5)
        self.press("enter", after=1.5)
        self.press("up", after=0.8)
        self.press("enter", after=2.0)
        self.log("save and quit issued")
        self.wait_main_menu(min_wait=wait_menu, timeout=120.0)

    def quit_game(self, timeout: float = 90.0) -> bool:
        """Quit to the desktop: Down to QUIT -> Enter -> Up (Yes) -> Enter, then wait for exit.

        This is also the loop's cleanup path, so it may be called from anywhere -- including
        mid-game after a failure. The main-menu probe decides: when the menu is not up, save and
        quit out of the map first (the save is restored by ``save_guard`` afterwards anyway).
        Returns True for a clean menu-driven exit, False when it had to taskkill.
        """
        try:
            self.focus()
            ok, n = self.probe_main_menu(SCRATCH / "quit_probe.png")
            if not ok:
                self.log(f"quit_game: not at the main menu (yellow={n}); leaving the map first")
                self.save_and_quit()
            for attempt in range(2):
                self.press_seq(["down"] * MENU_DOWNS, delay=0.35)
                time.sleep(0.5)
                try:  # sanity log: the entry count varies, the walk to the bottom does not
                    shot = self.capture(SCRATCH / "quit_probe.png")
                    self.log(f"quit probe: yellow={self.yellow_pixels(shot, QUIT_BOX)} "
                             f"in {QUIT_BOX} (attempt {attempt + 1})")
                except Exception as ex:  # noqa: BLE001
                    self.log(f"quit probe skipped: {ex!r}")
                self.press("enter", after=1.5)
                self.press("up", after=0.8)
                self.press("enter", after=2.0)
                self.log(f"QUIT issued (attempt {attempt + 1})")
                deadline = time.time() + timeout / 2
                while time.time() < deadline:
                    if not self.is_running():
                        self.log("process exited")
                        return True
                    time.sleep(2.0)
        except Exception as ex:  # noqa: BLE001
            self.log(f"quit keys failed: {ex!r}")
        self.log("still running after QUIT; falling back to taskkill")
        self.kill()
        time.sleep(3.0)
        return False

    # ------------------------------------------------------------------ status file
    def wait_status(self, path: str | os.PathLike[str],
                    predicate: Callable[[list[dict[str, Any]]], bool],
                    timeout: float = 180.0, since: float | None = None,
                    poll: float = 2.0, what: str = "predicate") -> list[dict[str, Any]]:
        """Poll a mod status JSON until ``predicate(records)`` holds.

        ``since`` drops records written before that wall-clock time (every record the M2 mod
        writes carries ``t``), so a stale file from a previous run can never satisfy the wait.
        """
        path = Path(path)
        deadline = time.time() + timeout
        last = -1
        while time.time() < deadline:
            recs = read_status(path, since=since)
            if len(recs) != last:
                self.log(f"status {path.name}: {len(recs)} record(s) waiting for {what}")
                last = len(recs)
            if recs and predicate(recs):
                self.log(f"status {path.name}: {what} satisfied ({len(recs)} records)")
                return recs
            if not self.is_running():
                raise DriverError(f"game exited while waiting for {what}")
            time.sleep(poll)
        raise DriverError(f"timeout after {timeout}s waiting for {what} in {path}")


def read_status(path: str | os.PathLike[str], since: float | None = None) -> list[dict[str, Any]]:
    """Read a mod status JSON, tolerating a half-written file, optionally filtered by ``t``."""
    try:
        with open(path, encoding="utf-8") as f:
            recs = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(recs, list):
        return []
    if since is not None:
        recs = [r for r in recs if isinstance(r, dict) and float(r.get("t", 0)) >= since]
    return recs


# ---------------------------------------------------------------------------- CLI
def _probe(args: argparse.Namespace) -> int:
    """Self-test against a running game: open the pause menu, capture, close it."""
    d = GameDriver(key_mode=args.mode)
    pids = d._tasklist(EXE.name)
    if not pids:
        print("Borderlands2.exe is not running; start it and load a character first")
        return 2
    d.pid = pids[0]
    d.log(f"--probe mode={args.mode} pid={d.pid}")
    if not d.focus():
        d.log("WARNING: could not focus the game window")
    before = SCRATCH / f"probe_{args.mode}_before.png"
    after = SCRATCH / f"probe_{args.mode}_pause.png"
    closed = SCRATCH / f"probe_{args.mode}_closed.png"
    d.capture(before)
    d.press("esc", after=2.0)
    d.capture(after)
    d.press("esc", after=2.0)
    d.capture(closed)
    try:
        from PIL import Image, ImageChops
        with Image.open(before) as a, Image.open(after) as b, Image.open(closed) as c:
            ab = ImageChops.difference(a.convert("L"), b.convert("L"))
            ac = ImageChops.difference(a.convert("L"), c.convert("L"))
            mean_ab = sum(i * n for i, n in enumerate(ab.histogram())) / (ab.width * ab.height)
            mean_ac = sum(i * n for i, n in enumerate(ac.histogram())) / (ac.width * ac.height)
        d.log(f"pause-menu delta={mean_ab:.2f} resumed-delta={mean_ac:.2f}")
        ok = mean_ab > 2.0
        print(f"key injection ({args.mode}): {'WORKS' if ok else 'NO EFFECT'} "
              f"(escape changed the frame by {mean_ab:.2f} mean levels; after resume {mean_ac:.2f})")
        return 0 if ok else 1
    except ImportError:
        print(f"captures written: {before}, {after}, {closed}")
        return 0


def _menu_probe(args: argparse.Namespace) -> int:
    """Launch, dismiss the intro, capture the main menu a few times, quit. Calibration aid."""
    d = GameDriver(key_mode=args.mode)
    d.launch()
    d.skip_intro()
    for i in range(args.shots):
        shot = SCRATCH / f"menu_probe_{i}.png"
        ok, bright = d.probe_main_menu(shot)
        d.log(f"shot {i}: bright={bright} menu={ok}")
        time.sleep(args.interval)
    if args.quit:
        d.quit_game()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bl2_verify.game_driver", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true", help="pause-menu self-test (game must be running)")
    p.add_argument("--menu-probe", action="store_true", help="launch and capture the main menu")
    p.add_argument("--mode", default="scancode", choices=list(winput.MODES))
    p.add_argument("--shots", type=int, default=4)
    p.add_argument("--interval", type=float, default=6.0)
    p.add_argument("--quit", action="store_true", help="quit the game at the end of --menu-probe")
    args = p.parse_args(argv)
    if args.menu_probe:
        return _menu_probe(args)
    if args.probe:
        return _probe(args)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
