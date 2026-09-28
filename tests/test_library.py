import sys
import time

from mopidy_goodies.library import Scanner, local_enabled, scan_command


def _wait(scanner, timeout=10):
    deadline = time.time() + timeout
    while scanner.status()["running"] and time.time() < deadline:
        time.sleep(0.05)
    return scanner.status()


def _fake(script):
    return lambda force=False: [sys.executable, "-c", script]


def test_scan_command_forwards_config_options(tmp_path):
    exe = tmp_path / "mopidy"
    exe.write_text("")
    cmd = scan_command(
        [str(exe), "--config", "/a.conf", "-o", "local/enabled=true", "--option=x/y=z", "-q"],
        force=True,
    )
    assert cmd == [
        sys.executable, str(exe),
        "--config", "/a.conf", "-o", "local/enabled=true", "--option=x/y=z",
        "-v", "local", "scan", "--force",
    ]


def test_scanner_parses_progress():
    scanner = Scanner(_fake(
        "print('Removing 2 missing tracks');"
        "print('Found 30 tracks which need to be updated');"
        "print('  INFO  Scanned 10 of 30 files in 1.0s, ~2s left');"
        "print('Scanned 30 of 30 files in 3.0s.')"
    ))
    assert scanner.start()
    status = _wait(scanner)
    assert status["exit_code"] == 0
    assert (status["removed"], status["scanned"], status["to_scan"]) == (2, 30, 30)
    assert status["error"] is None
    assert status["finished_at"] >= status["started_at"]


def test_scanner_rejects_concurrent_scan():
    scanner = Scanner(_fake("import time; time.sleep(0.5)"))
    assert scanner.start()
    assert not scanner.start()
    assert _wait(scanner)["exit_code"] == 0


def test_scanner_reports_failure_output():
    scanner = Scanner(_fake("import sys; print('boom'); sys.exit(3)"))
    scanner.start()
    status = _wait(scanner)
    assert status["exit_code"] == 3
    assert "boom" in status["error"]


def test_scanner_reports_missing_executable():
    scanner = Scanner(lambda force=False: ["/nonexistent/mopidy"])
    assert scanner.start()
    status = scanner.status()
    assert not status["running"]
    assert status["error"]


def test_local_enabled():
    assert local_enabled({"local": {"enabled": True}})
    assert not local_enabled({"local": {"enabled": False}})
    assert not local_enabled({})
