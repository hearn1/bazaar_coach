"""
tracker.py — Unified Bazaar tracker runner.

Starts the Player.log watcher in-process and, by default, launches the
Mono capture pipeline in a background subprocess. Decisions are scored live
as RunState records them; run completion closes the run and flushes writes.

Graceful shutdown is triggered by:
  - Ctrl+C (SIGINT) / Task Manager (SIGTERM)
  - Overlay "Quit" button (PyWebView)
  - HTTP POST to /api/control/shutdown (Flask endpoint)

All paths use a shared shutdown_event to coordinate ordered teardown
of subprocesses, threads, and disk I/O.

Usage:
    python tracker.py
    python tracker.py --no-mono
    python tracker.py --no-overlay
"""

import argparse
import atexit
import datetime
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import app_paths
import content_manifest
import db
import settings
from version import APP_VERSION


REPO_DIR = app_paths.repo_dir()
LOGS_DIR = app_paths.logs_dir()
DEFAULT_WEB_PORT = 5555

# Shared shutdown event — set by signal handler, overlay button, or Flask endpoint
shutdown_event = threading.Event()


class TeeStream:
    def __init__(self, *streams):
        self.streams = tuple(stream for stream in streams if stream is not None)
        self.encoding = next(
            (getattr(stream, "encoding", None) for stream in self.streams if getattr(stream, "encoding", None)),
            "utf-8",
        )

    def write(self, data):
        for stream in self.streams:
            write = getattr(stream, "write", None)
            if write:
                write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            flush = getattr(stream, "flush", None)
            if flush:
                flush()

    def isatty(self):
        primary = self.streams[0] if self.streams else None
        return bool(primary and hasattr(primary, "isatty") and primary.isatty())


def start_session_logging():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"tracker_{ts}.log"
    log_handle = open(log_path, "w", encoding="utf-8", newline="")

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeStream(original_stdout, log_handle)
    sys.stderr = TeeStream(original_stderr, log_handle)
    print(f"[Tracker] Session log: {log_path}")
    return log_handle, original_stdout, original_stderr


def _pump_process_output(proc: subprocess.Popen):
    try:
        for line in proc.stdout:
            print(f"[Mono] {line.rstrip()}")
    finally:
        code = proc.wait()
        print(f"[Mono] capture_mono exited with code {code}")


def launch_capture_mono(process_name: str = "TheBazaar.exe") -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    if app_paths.is_packaged():
        cmd = [
            sys.executable,
            "--capture-mono-worker",
            "--db",
            "--wait",
            "--process",
            process_name,
        ]
    else:
        cmd = [
            sys.executable,
            "-u",
            str(REPO_DIR / "capture_mono.py"),
            "--db",
            "--wait",
            "--process",
            process_name,
        ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    threading.Thread(
        target=_pump_process_output,
        args=(proc,),
        daemon=True,
    ).start()
    return proc


def wait_for_web_server(port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_tracker_watcher(args):
    import watcher

    watcher.run_watcher(
        log_path=Path(args.log) if args.log else None,
    )


def print_startup_versions():
    """Print support/version diagnostics once per tracker startup."""
    print(f"[Tracker] App version: {APP_VERSION}")
    print(f"[Tracker] DB schema version: {db.get_schema_version()} (expected {db.SCHEMA_VERSION})")
    print(f"[Tracker] Settings schema version: {settings.SCHEMA_VERSION}")
    print(f"[Tracker] Content manifest: {content_manifest.summarize_manifest()}")


def _install_signal_handlers():
    """
    Install handlers for SIGINT and SIGTERM to gracefully shutdown.
    
    Both signal types set the shutdown_event, which is the single point
    of truth for the main thread to initiate ordered teardown.
    """
    def _signal_handler(signum, frame):
        print(f"\n[Tracker] Received signal {signum} — initiating shutdown.")
        shutdown_event.set()
        # If overlay is running, destroy the window to release webview.start()
        try:
            import webview
            if webview.windows:
                webview.windows[0].destroy()
        except Exception as e:
            print(f"[Tracker] Webview destroy failed during signal handler: {e}")
    
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


def _shutdown(mono_proc, log_handle, original_stdout, original_stderr):
    """
    Perform ordered teardown of all subprocesses, threads, and I/O.
    
    Teardown order:
    1. Stop accepting new Mono subprocess output
    2. Terminate Mono subprocess with escalating force
    3. Watcher thread exits naturally (daemon=True handles auto-cleanup)
    4. Flask/waitress stops (daemon=True handles auto-cleanup)
    5. Flush database writer queue
    6. Save settings to disk
    7. Close session log
    8. Restore stdout/stderr
    """
    # Step 1-2: Terminate Mono subprocess with escalating force
    if mono_proc and mono_proc.poll() is None:
        print("[Tracker] Stopping capture_mono...")
        mono_proc.terminate()
        try:
            mono_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("[Tracker] Mono subprocess did not respond to SIGTERM, force-killing...")
            mono_proc.kill()
            mono_proc.wait()
    
    # Step 3-4: Watcher and Flask are daemon threads, will exit automatically
    # Brief sleep to let in-flight requests complete before we shut down
    time.sleep(0.25)
    
    # Step 5: Drain database writer queue
    db.close_shared_conn()
    
    # Step 6: Save settings to disk
    settings.save()
    
    # Step 7-8: Restore streams and close log
    print("[Tracker] Shutdown complete.")
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    log_handle.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--capture-mono-worker":
        import capture_mono
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        raise SystemExit(capture_mono.main())
    if len(sys.argv) > 1 and sys.argv[1] in {"doctor", "export-diagnostics"}:
        import doctor
        raise SystemExit(doctor.main(sys.argv[1:]))
    if len(sys.argv) > 1 and sys.argv[1] == "refresh-content":
        import card_cache

        refresh_parser = argparse.ArgumentParser(description="Refresh Bazaar static content cache")
        refresh_parser.add_argument("command", nargs="?")
        refresh_parser.add_argument("--no-versioned-cache", action="store_true",
                                    help="Do not write raw static files to a versioned cache folder")
        refresh_args = refresh_parser.parse_args(sys.argv[1:])
        db.init_db()
        summary = card_cache.refresh_cache(versioned=not refresh_args.no_versioned_cache)
        card_cache.print_refresh_summary(summary)
        print(f"[Tracker] Content manifest: {content_manifest.summarize_manifest()}")
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "refresh-images":
        import refresh_images
        raise SystemExit(refresh_images.main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "refresh-builds":
        import refresh_builds
        raise SystemExit(refresh_builds.main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "catalog-coverage":
        import json as _json
        import argparse as _argparse
        from card_cache import catalog_coverage_report

        _cc_parser = _argparse.ArgumentParser(description="Catalog coverage report")
        _cc_parser.add_argument("command")
        _cc_parser.add_argument("--output", type=str, default=None,
                                help="Write JSON to this path instead of stdout")
        _cc_parser.add_argument("--hero", type=str, default=None,
                                help="Filter unscored decisions by hero name")
        _cc_args = _cc_parser.parse_args(sys.argv[1:])
        db.init_db()
        report = {
            "unscored_decisions": db.unscored_decisions_report(hero=_cc_args.hero),
            "catalog_coverage": catalog_coverage_report(),
        }
        output = _json.dumps(report, indent=2, default=str)
        if _cc_args.output:
            Path(_cc_args.output).write_text(output, encoding="utf-8")
            print(f"Wrote {_cc_args.output}")
        else:
            print(output)
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "check-updates":
        import update_checker
        raise SystemExit(update_checker.main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in {"setup", "setup-status"}:
        import json
        import first_run

        setup_parser = argparse.ArgumentParser(description="Bazaar Tracker first-run setup")
        setup_parser.add_argument("command", choices=["setup", "setup-status"])
        setup_parser.add_argument("--force", action="store_true", help="Run setup even if it is already completed")
        setup_parser.add_argument("--refresh-content", choices=["auto", "always", "never"], default="auto")
        setup_parser.add_argument("--refresh-images", action="store_true", help="Run local Unity image extraction during setup")
        setup_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
        setup_args = setup_parser.parse_args(sys.argv[1:])
        settings.load()
        if setup_args.command == "setup-status":
            report = first_run.setup_status()
        else:
            report = first_run.run_setup(
                force=setup_args.force,
                refresh_content=setup_args.refresh_content,
                refresh_images_enabled=setup_args.refresh_images,
            )
        if setup_args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            first_run.print_setup_report(report)
        if setup_args.command == "setup-status":
            raise SystemExit(0)
        raise SystemExit(0 if report.get("completed", False) else 1)

    log_handle, original_stdout, original_stderr = start_session_logging()
    
    parser = argparse.ArgumentParser(
        description="Unified Bazaar tracker runner (watcher + Flask dashboard + overlay + mono capture)"
    )
    parser.add_argument("--log", type=str, default=None,
                        help="Path to Player.log (auto-detected if omitted)")
    parser.add_argument("--no-mono", action="store_true",
                        help="Do not launch capture_mono in a subprocess")
    parser.add_argument("--mono-process", type=str, default="TheBazaar.exe",
                        help="Process name for capture_mono to attach to")
    parser.add_argument("--no-overlay", action="store_true",
                        help="Do not launch the overlay window")
    parser.add_argument("--no-refresh-builds", action="store_true",
                        help="Do not refresh build catalogs when the web server starts")
    args = parser.parse_args()

    mono_proc = None
    should_launch_overlay = not args.no_overlay
    should_launch_mono = not args.no_mono
    
    # Install signal handlers for graceful shutdown
    _install_signal_handlers()
    
    # Ensure settings are saved even if an exception occurs
    atexit.register(settings.save)

    try:
        # Load settings (populates cache from disk)
        settings.load()
        import first_run
        setup_report = first_run.run_setup(refresh_content="never", refresh_images_enabled=False)
        if setup_report.get("steps"):
            first_run.print_setup_report(setup_report)
        db.init_db()
        _retention_days = int(settings.get("tracker.db_retention_days", 0) or 0)
        if _retention_days >= 90:
            summary = db.prune_old_runs(_retention_days)
            print(f"[Retention] Deleted {summary['deleted_runs']} runs older than {_retention_days}d (decisions={summary['deleted_decisions']}, combats={summary['deleted_combats']})")
        print_startup_versions()
        
        from web.server import start_web_server, set_shutdown_callback
        start_web_server(
            port=DEFAULT_WEB_PORT,
            db_path=app_paths.db_path(),
            background=True,
            auto_refresh_builds=not args.no_refresh_builds,
        )
        
        # Register the shutdown callback so Flask endpoint can trigger shutdown
        set_shutdown_callback(lambda: shutdown_event.set())

        if should_launch_overlay:
            wait_for_web_server(port=DEFAULT_WEB_PORT)
            import overlay

        if should_launch_mono:
            print("[Tracker] Launching capture_mono in the background...")
            mono_proc = launch_capture_mono(process_name=args.mono_process)

        if should_launch_overlay:
            # PyWebView must own the main thread on this setup, so run the
            # watcher in the background and let the overlay block on the main
            # thread for the life of the process.
            threading.Thread(
                target=run_tracker_watcher,
                args=(args,),
                daemon=True,
                name="watcher",
            ).start()
            overlay.launch_overlay(port=DEFAULT_WEB_PORT)
        else:
            # Headless mode: watcher in background, main thread waits for shutdown
            threading.Thread(
                target=run_tracker_watcher,
                args=(args,),
                daemon=True,
                name="watcher",
            ).start()
            shutdown_event.wait()
    except KeyboardInterrupt:
        print("\n[Tracker] Stopped.")
    finally:
        _shutdown(mono_proc, log_handle, original_stdout, original_stderr)


if __name__ == "__main__":
    main()
