"""Trigger ``mopidy local scan`` over HTTP.

mopidy-local only rescans the media dir from the CLI; ``core.library.refresh``
is a no-op for it. We run the same CLI command as a child process, reusing the
``--config``/``--option`` arguments the running Mopidy was started with so the
scan sees the same ``[local]`` settings. mopidy-local reads its SQLite library
on every request, so results show up to clients as soon as the scan finishes —
no Mopidy restart needed.
"""
import collections
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

_FOUND = re.compile(r"Found (\d+) tracks which need to be updated")
_SCANNED = re.compile(r"Scanned (\d+) of (\d+) files")
_REMOVING = re.compile(r"Removing (\d+) missing tracks")

# Global CLI options worth forwarding to the child (they carry config).
_VALUE_OPTS = ("--config", "--option", "-o")


def scan_command(argv=None, force=False):
    """Build the ``mopidy … local scan`` argv from the running process' argv."""
    argv = list(sys.argv if argv is None else argv)
    script = argv[0] if argv and os.path.isfile(argv[0]) else shutil.which("mopidy")
    if not script:
        raise FileNotFoundError("can't locate the mopidy executable")

    forwarded = []
    args = iter(argv[1:])
    for arg in args:
        if arg in _VALUE_OPTS:
            value = next(args, None)
            if value is not None:
                forwarded += [arg, value]
        elif arg.startswith(("--config=", "--option=")):
            forwarded.append(arg)

    # -v so mopidy-local logs its INFO progress lines, which we parse.
    cmd = [sys.executable, script, *forwarded, "-v", "local", "scan"]
    if force:
        cmd.append("--force")
    return cmd


class Scanner:
    """Runs at most one scan at a time and tracks its progress."""

    def __init__(self, command_factory=scan_command):
        self._command_factory = command_factory
        self._lock = threading.Lock()
        self._state = self._idle_state()

    @staticmethod
    def _idle_state():
        return {
            "running": False,
            "force": False,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "to_scan": None,
            "scanned": 0,
            "removed": None,
            "error": None,
        }

    def status(self):
        with self._lock:
            return dict(self._state)

    def start(self, force=False):
        """Start a scan. Returns False if one is already running."""
        with self._lock:
            if self._state["running"]:
                return False
            self._state = self._idle_state()
            self._state.update(running=True, force=force, started_at=int(time.time()))
            try:
                proc = subprocess.Popen(
                    self._command_factory(force=force),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    errors="replace",
                    # Mopidy's console logger wraps at the terminal width; a wide
                    # "terminal" keeps each message on one line so we can parse it.
                    env={**os.environ, "COLUMNS": "1000"},
                )
            except OSError as e:
                logger.error("Library scan failed to start: %s", e)
                self._state.update(
                    running=False, finished_at=int(time.time()), error=str(e)
                )
                return True
        logger.info("Library scan started (force=%s)", force)
        threading.Thread(
            target=self._follow, args=(proc,), name="GoodiesLibraryScan", daemon=True
        ).start()
        return True

    def _follow(self, proc):
        tail = collections.deque(maxlen=20)
        for line in proc.stdout:
            tail.append(line.rstrip())
            self._parse(line)
        code = proc.wait()
        with self._lock:
            self._state.update(
                running=False, finished_at=int(time.time()), exit_code=code
            )
            if code != 0:
                self._state["error"] = "\n".join(tail)
        logger.info("Library scan finished (exit code %s)", code)

    def _parse(self, line):
        with self._lock:
            if m := _FOUND.search(line):
                self._state["to_scan"] = int(m[1])
            elif m := _SCANNED.search(line):
                self._state["scanned"] = int(m[1])
                self._state["to_scan"] = int(m[2])
            elif m := _REMOVING.search(line):
                self._state["removed"] = int(m[1])


SCANNER = Scanner()


def local_enabled(config):
    """True when mopidy-local is installed and enabled on this server."""
    try:
        return bool(config["local"]["enabled"])
    except (KeyError, TypeError):
        return False
