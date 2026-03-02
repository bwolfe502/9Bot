"""9Bot startup & shutdown — shared initialization for all entry points.

Used by both ``run_web.py`` (web-only) and ``main.py`` (legacy tkinter GUI).
"""

import io
import os
import sys
import json
import base64
import hashlib
import hmac
import logging
import platform
import subprocess
import threading

import config
from config import (running_tasks, set_min_troops, set_auto_heal,
                    set_auto_restore_ap, set_ap_restore_options,
                    set_territory_config, set_eg_rally_own, set_titan_rally_own,
                    set_gather_options, set_tower_quest_enabled,
                    set_protocol_enabled)
from settings import load_settings, save_settings

# Relay server connection details (obfuscated, not plaintext in source)
_RELAY_URL_B64 = "d3NzOi8vMTQ1My5saWZlL3dzL3R1bm5lbA=="
_RELAY_SECRET_B64 = "MEpRR2l2bmJDMkNEUHlaS3dFVW5Qc1FrbGlWZ0phMXVZbmZ3MktOcHpYTQ=="


def get_relay_config(settings):
    """Compute relay configuration, auto-deriving from the license key.

    Returns ``(relay_url, relay_secret, bot_name)`` when relay should be
    active, or ``None`` when it should be disabled.
    """
    if not settings.get("remote_access", True):
        return None

    try:
        from license import get_license_key
        key = get_license_key()
    except Exception:
        key = None

    if not key:
        return None

    bot_name = hashlib.sha256(key.encode()).hexdigest()[:10]
    relay_url = base64.b64decode(_RELAY_URL_B64).decode()
    relay_secret = base64.b64decode(_RELAY_SECRET_B64).decode()
    return relay_url, relay_secret, bot_name


def device_hash(device_id):
    """Short URL-safe hash of a device ID (doesn't expose IP/port)."""
    return hashlib.sha256(device_id.encode()).hexdigest()[:8]


def _get_license_key():
    try:
        from license import get_license_key
        return get_license_key()
    except Exception:
        return None


def generate_device_token(device_id):
    """Deterministic per-device token derived from the license key.

    Returns a 16-char hex string, or ``None`` if no license key is available.
    """
    key = _get_license_key()
    if not key:
        return None
    return hashlib.sha256(f"{key}:{device_id}".encode()).hexdigest()[:16]


def generate_device_ro_token(device_id):
    """Deterministic read-only token for a device.

    Returns a 16-char hex string, or ``None`` if no license key is available.
    """
    key = _get_license_key()
    if not key:
        return None
    return hashlib.sha256(f"{key}:ro:{device_id}".encode()).hexdigest()[:16]


def validate_device_token(device_id, token):
    """Validate a device token using constant-time comparison.

    Returns ``"full"``, ``"readonly"``, or ``None`` (invalid).
    """
    full = generate_device_token(device_id)
    if full and hmac.compare_digest(token, full):
        return "full"
    ro = generate_device_ro_token(device_id)
    if ro and hmac.compare_digest(token, ro):
        return "readonly"
    return None


# ---------------------------------------------------------------------------
# Protocol interceptor lifecycle
# ---------------------------------------------------------------------------

_interceptor_thread = None
_protocol_bus = None
_game_state = None


def _setup_gadget_forward(port=27042):
    """Ensure ADB forward for Frida Gadget port on all connected devices."""
    import subprocess
    from botlog import get_logger
    log = get_logger("startup")
    try:
        result = subprocess.run(
            [config.adb_path, "devices"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                dev_id = parts[0]
                subprocess.run(
                    [config.adb_path, "-s", dev_id, "forward",
                     f"tcp:{port}", f"tcp:{port}"],
                    capture_output=True, timeout=5,
                )
                log.info("ADB forward tcp:%d set for %s", port, dev_id)
    except Exception:
        log.warning("Failed to set ADB forward for Frida Gadget", exc_info=True)


def _start_protocol():
    """Start the protocol interceptor if not already running."""
    global _interceptor_thread, _protocol_bus, _game_state
    if _interceptor_thread is not None:
        return
    try:
        from protocol.events import EventBus
        from protocol.interceptor import InterceptorThread
        from protocol.game_state import GameState
    except ImportError:
        from botlog import get_logger
        get_logger("startup").warning("Protocol package not available — skipping")
        return
    _protocol_bus = EventBus()
    _game_state = GameState("default", _protocol_bus)
    _interceptor_thread = InterceptorThread(
        event_bus=_protocol_bus, pre_connect=_setup_gadget_forward,
    )
    _interceptor_thread.start()
    from botlog import get_logger
    get_logger("startup").info("Protocol interceptor started")


def _stop_protocol():
    """Stop the protocol interceptor if running."""
    global _interceptor_thread, _protocol_bus, _game_state
    if _interceptor_thread is not None:
        _interceptor_thread.stop()
        _interceptor_thread = None
        _protocol_bus = None
        _game_state = None
        from botlog import get_logger
        get_logger("startup").info("Protocol interceptor stopped")


def get_protocol_ap():
    """Return (current, max) AP from protocol, or None if unavailable/stale."""
    state = _game_state
    if state is None:
        return None
    if not state.is_fresh("ap", max_age_s=10.0):
        return None
    return state.ap


def apply_settings(settings):
    """Push settings values into config globals.

    Called on startup and whenever settings are saved (from any UI).
    """
    set_auto_heal(settings.get("auto_heal", True))
    set_auto_restore_ap(settings.get("auto_restore_ap", False))
    set_ap_restore_options(
        settings.get("ap_use_free", True),
        settings.get("ap_use_potions", True),
        settings.get("ap_allow_large_potions", True),
        settings.get("ap_use_gems", False),
        settings.get("ap_gem_limit", 0),
    )
    set_min_troops(settings.get("min_troops", 0))
    set_eg_rally_own(settings.get("eg_rally_own", True))
    set_titan_rally_own(settings.get("titan_rally_own", True))
    set_territory_config(settings.get("my_team", "yellow"))
    config.MITHRIL_INTERVAL = settings.get("mithril_interval", 19)
    for dev_id, ts in settings.get("last_mithril_time", {}).items():
        try:
            config.LAST_MITHRIL_TIME[dev_id] = float(ts)
        except (ValueError, TypeError):
            pass
    from botlog import set_console_verbose
    set_console_verbose(settings.get("verbose_logging", False))
    import training
    training.configure(settings.get("collect_training_data", False))
    set_gather_options(
        settings.get("gather_enabled", True),
        settings.get("gather_mine_level", 4),
        settings.get("gather_max_troops", 3),
    )
    set_tower_quest_enabled(settings.get("tower_quest_enabled", False))
    set_protocol_enabled(settings.get("protocol_enabled", False))
    if config.PROTOCOL_ENABLED:
        _start_protocol()
    else:
        _stop_protocol()
    for dev_id, count in settings.get("device_troops", {}).items():
        try:
            config.DEVICE_TOTAL_TROOPS[dev_id] = int(count)
        except (ValueError, TypeError):
            config.DEVICE_TOTAL_TROOPS[dev_id] = 5

    # Per-device setting overrides
    config.clear_device_overrides()
    for dev_id, overrides in settings.get("device_settings", {}).items():
        config.set_device_overrides(dev_id, overrides)


def initialize():
    """One-time app startup: logging, settings, devices, OCR warmup.

    Returns the loaded settings dict.
    """
    from botlog import setup_logging, get_logger
    setup_logging()
    config.log_adb_path()

    log = get_logger("startup")

    # Compatibility bridge: capture print() calls to legacy log file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    _log_path = os.path.join(script_dir, "9bot.log")
    _log_file = open(_log_path, "w", encoding="utf-8")

    class _Tee:
        """Write to both the original stream and a log file."""
        def __init__(self, stream, logf):
            self._stream = stream
            self._log = logf
        def write(self, data):
            self._stream.write(data)
            try:
                self._log.write(data)
                self._log.flush()
            except Exception:
                pass
        def flush(self):
            self._stream.flush()
            try:
                self._log.flush()
            except Exception:
                pass

    sys.stdout = _Tee(sys.stdout, _log_file)
    sys.stderr = _Tee(sys.stderr, _log_file)

    # License check (skipped for git clones / dev mode)
    if not os.path.isdir(os.path.join(script_dir, ".git")):
        from license import validate_license
        validate_license()
    else:
        log.info("Git repo detected — skipping license check (developer mode).")

    # Auto-update check
    from updater import check_and_update
    if check_and_update():
        log.info("Update installed — restarting...")
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except OSError as e:
            log.error("Failed to restart after update: %s", e)

    # Load and apply settings
    settings = load_settings()
    apply_settings(settings)

    # Connect emulators
    from devices import auto_connect_emulators
    auto_connect_emulators()

    # Pre-initialize OCR engine in background thread
    from vision import warmup_ocr
    threading.Thread(target=warmup_ocr, daemon=True).start()

    log.info("9Bot initialized.")
    return settings


def shutdown():
    """Graceful shutdown: stop tasks, save stats, disconnect ADB, flush logs."""
    from botlog import get_logger

    log = get_logger("startup")
    log.info("Shutting down...")

    # Stop all running tasks
    try:
        config.auto_occupy_running = False
        config.MITHRIL_ENABLED_DEVICES.clear()
        config.MITHRIL_DEPLOY_TIME.clear()
        for key in list(running_tasks.keys()):
            from runners import stop_task
            stop_task(key)
        config.DEVICE_STATUS.clear()
        log.info("=== ALL TASKS STOPPED ===")
    except Exception as e:
        print(f"Failed to stop tasks: {e}")

    # Stop relay tunnel if running
    try:
        from tunnel import stop_tunnel
        stop_tunnel()
    except Exception:
        pass

    # Stop protocol interceptor
    try:
        _stop_protocol()
    except Exception:
        pass

    # Persist mithril timers so they survive restarts
    try:
        if config.LAST_MITHRIL_TIME:
            settings = load_settings()
            settings["last_mithril_time"] = dict(config.LAST_MITHRIL_TIME)
            save_settings(settings)
            log.info("Mithril timers saved (%d devices)", len(config.LAST_MITHRIL_TIME))
    except Exception as e:
        print(f"Failed to save mithril timers: {e}")

    # Close training data file
    try:
        import training
        training.shutdown()
    except Exception:
        pass

    # Save session stats
    try:
        from botlog import stats
        stats.save()
        log.info("Session stats saved")
        summary = stats.summary()
        if summary:
            log.info("Session stats:\n%s", summary)
    except Exception as e:
        print(f"Failed to save stats: {e}")

    # Flush all log handlers
    try:
        logging.shutdown()
    except Exception:
        pass

    # Disconnect ADB devices
    try:
        from devices import get_devices
        for d in get_devices():
            try:
                subprocess.run([config.adb_path, "disconnect", d],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2)
            except Exception:
                pass
    except Exception:
        pass


def create_bug_report_zip(clear_debug=True, notes=None):
    """Create a bug report zip file in memory and return the bytes.

    Args:
        clear_debug: If True (default), remove debug screenshots after zipping.
            Pass False for periodic auto-uploads to keep debug files intact.
        notes: Optional user notes string to include as ``notes.txt`` in the zip.

    Returns ``(zip_bytes, filename)`` tuple.
    """
    import io
    import zipfile
    from datetime import datetime
    from botlog import stats, SCRIPT_DIR, LOG_DIR, STATS_DIR, BOT_VERSION

    buf = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"9bot_bugreport_{timestamp}.zip"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Logs (current + rotated backups)
        for suffix in ["", ".1", ".2", ".3"]:
            logfile = os.path.join(LOG_DIR, f"9bot.log{suffix}")
            if os.path.isfile(logfile):
                zf.write(logfile, f"logs/9bot.log{suffix}")

        # Debug screenshots (failures, click trails, general debug)
        debug_dir = os.path.join(SCRIPT_DIR, "debug")
        for subdir in ["failures", "clicks", ""]:
            scan_dir = os.path.join(debug_dir, subdir) if subdir else debug_dir
            zip_prefix = f"debug/{subdir}/" if subdir else "debug/"
            if os.path.isdir(scan_dir):
                for f in os.listdir(scan_dir):
                    fpath = os.path.join(scan_dir, f)
                    if os.path.isfile(fpath) and f.endswith(".png"):
                        zf.write(fpath, zip_prefix + f)

        # Training data (JSONL logs + selective images)
        training_dir = os.path.join(SCRIPT_DIR, "training_data")
        if os.path.isdir(training_dir):
            for f in os.listdir(training_dir):
                fpath = os.path.join(training_dir, f)
                if os.path.isfile(fpath) and f.endswith(".jsonl"):
                    zf.write(fpath, f"training_data/{f}")
            images_dir = os.path.join(training_dir, "images")
            if os.path.isdir(images_dir):
                for f in os.listdir(images_dir):
                    fpath = os.path.join(images_dir, f)
                    if os.path.isfile(fpath) and f.endswith(".jpg"):
                        zf.write(fpath, f"training_data/images/{f}")

        # Session stats
        if os.path.isdir(STATS_DIR):
            for f in os.listdir(STATS_DIR):
                if f.endswith(".json"):
                    zf.write(os.path.join(STATS_DIR, f), f"stats/{f}")

        # Settings (redact secrets)
        settings_path = os.path.join(SCRIPT_DIR, "settings.json")
        if os.path.isfile(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as sf:
                    safe_settings = json.load(sf)
                for key in ("relay_secret",):
                    if key in safe_settings and safe_settings[key]:
                        safe_settings[key] = "***REDACTED***"
                zf.writestr("settings.json", json.dumps(safe_settings, indent=2))
            except Exception:
                zf.write(settings_path, "settings.json")

        # User notes (if provided)
        if notes and notes.strip():
            zf.writestr("notes.txt", notes.strip())

        # System info report
        try:
            from devices import get_devices
            device_list = get_devices()
        except Exception:
            device_list = ["(could not detect)"]

        cpu_cores = os.cpu_count() or "unknown"
        cpu_arch = platform.machine()
        ram_gb = _get_ram_gb()

        info_lines = [
            "9Bot Bug Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "=== System ===",
            f"Version: {BOT_VERSION}",
            f"Python: {sys.version}",
            f"OS: {platform.system()} {platform.release()} ({platform.version()})",
            f"CPU: {cpu_arch}, {cpu_cores} cores",
            f"RAM: {ram_gb}",
            f"ADB: {config.adb_path}",
            f"Devices: {', '.join(device_list) if device_list else '(none)'}",
            "",
            "=== Session Summary ===",
            stats.summary(),
        ]
        zf.writestr("report_info.txt", "\n".join(info_lines))

    buf.seek(0)
    zip_bytes = buf.getvalue()

    if clear_debug:
        _clear_debug_files(SCRIPT_DIR)

    return zip_bytes, filename


def _clear_debug_files(script_dir):
    """Remove debug screenshots, click trails, and training data after export."""
    for subdir in ["debug/failures", "debug/clicks", "debug"]:
        dirpath = os.path.join(script_dir, subdir)
        if not os.path.isdir(dirpath):
            continue
        for f in os.listdir(dirpath):
            fpath = os.path.join(dirpath, f)
            if os.path.isfile(fpath) and f.endswith(".png"):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
    # Clear training data (JSONL + images)
    for subdir, ext in [("training_data", ".jsonl"), ("training_data/images", ".jpg")]:
        dirpath = os.path.join(script_dir, subdir)
        if not os.path.isdir(dirpath):
            continue
        for f in os.listdir(dirpath):
            fpath = os.path.join(dirpath, f)
            if os.path.isfile(fpath) and f.endswith(ext):
                try:
                    os.remove(fpath)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Bug report auto-upload
# ---------------------------------------------------------------------------

_upload_thread = None
_upload_stop = threading.Event()
_last_upload_time = None      # datetime or None
_last_upload_error = None     # str or None
_upload_interval_hours = 24

# Manual upload progress tracking (for UI polling)
_upload_progress = {"phase": "idle", "percent": 0, "message": ""}
_manual_upload_thread = None


class _ProgressReader:
    """File-like wrapper that tracks read progress for requests uploads."""

    def __init__(self, data, callback):
        self._buf = io.BytesIO(data)
        self._total = len(data)
        self._callback = callback

    def read(self, size=-1):
        chunk = self._buf.read(size)
        if chunk:
            self._callback(self._buf.tell(), self._total)
        return chunk

    def __len__(self):
        return self._total


def upload_bug_report(settings=None, notes=None):
    """Upload a bug report ZIP to the relay server.

    Args:
        settings: Settings dict (loaded from file if None).
        notes: Optional user notes to include in the zip.

    Returns ``(success, message)`` tuple.
    """
    global _last_upload_time, _last_upload_error
    if settings is None:
        settings = load_settings()
    relay_cfg = get_relay_config(settings)
    if not relay_cfg:
        _upload_progress.update(phase="idle", percent=0, message="")
        return False, "Relay not configured (no license or remote access disabled)"

    relay_url, relay_secret, bot_name = relay_cfg
    host = relay_url.replace("wss://", "").replace("ws://", "").split("/")[0]
    upload_url = f"https://{host}/_upload?bot={bot_name}"

    _upload_progress.update(phase="zipping", percent=0, message="Generating report...")
    zip_bytes, filename = create_bug_report_zip(clear_debug=False, notes=notes)

    _upload_progress.update(phase="uploading", percent=0, message="Uploading...")

    def _on_progress(sent, total):
        pct = int(sent * 100 / total) if total else 0
        _upload_progress.update(percent=pct, message=f"Uploading... {pct}%")

    # Build multipart body manually so we can track upload progress
    boundary = "----9BotUploadBoundary"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    body = prefix + zip_bytes + suffix

    import requests as _req
    try:
        resp = _req.post(
            upload_url,
            data=_ProgressReader(body, _on_progress),
            headers={
                "Authorization": f"Bearer {relay_secret}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            timeout=300,
        )
    except Exception as e:
        _last_upload_error = str(e)
        _upload_progress.update(phase="error", percent=0, message=str(e))
        return False, f"Upload failed: {e}"

    if resp.status_code == 200:
        from datetime import datetime
        _last_upload_time = datetime.now()
        _last_upload_error = None
        _upload_progress.update(phase="done", percent=100, message="Uploaded!")
        return True, "Upload successful"

    _last_upload_error = f"HTTP {resp.status_code}"
    _upload_progress.update(phase="error", percent=0,
                            message=f"HTTP {resp.status_code}")
    return False, f"Upload failed: HTTP {resp.status_code}"


def start_manual_upload(notes=None):
    """Start a manual upload in a background thread. Returns immediately."""
    global _manual_upload_thread
    if _manual_upload_thread is not None and _manual_upload_thread.is_alive():
        return  # already running
    _upload_progress.update(phase="starting", percent=0, message="Starting...")

    def _run():
        try:
            upload_bug_report(notes=notes)
        except Exception as e:
            _upload_progress.update(phase="error", percent=0, message=str(e))

    _manual_upload_thread = threading.Thread(target=_run, daemon=True,
                                             name="manual-upload")
    _manual_upload_thread.start()


def get_upload_progress():
    """Return current manual upload progress dict."""
    return dict(_upload_progress)


def start_auto_upload(settings):
    """Start periodic bug report upload in a background thread."""
    global _upload_thread, _upload_interval_hours
    if _upload_thread is not None and _upload_thread.is_alive():
        return
    _upload_stop.clear()
    _upload_interval_hours = max(1, settings.get("upload_interval_hours", 24))

    def _loop():
        from botlog import get_logger
        log = get_logger("auto_upload")
        log.info("Auto-upload started (every %dh)", _upload_interval_hours)
        while not _upload_stop.is_set():
            _upload_stop.wait(_upload_interval_hours * 3600)
            if _upload_stop.is_set():
                break
            try:
                ok, msg = upload_bug_report(settings)
                if ok:
                    log.info("Auto-upload: %s", msg)
                else:
                    log.warning("Auto-upload: %s", msg)
            except Exception as e:
                log.warning("Auto-upload error: %s", e)
        log.info("Auto-upload stopped")

    _upload_thread = threading.Thread(target=_loop, daemon=True, name="auto-upload")
    _upload_thread.start()


def stop_auto_upload():
    """Stop the periodic upload thread."""
    global _upload_thread
    _upload_stop.set()
    if _upload_thread is not None:
        _upload_thread.join(timeout=2)
        _upload_thread = None


def upload_status():
    """Return dict describing auto-upload state."""
    from datetime import datetime
    enabled = (_upload_thread is not None and _upload_thread.is_alive())
    result = {
        "enabled": enabled,
        "interval_hours": _upload_interval_hours,
        "last_upload": _last_upload_time.isoformat() if _last_upload_time else None,
        "error": _last_upload_error,
    }
    if enabled and _last_upload_time:
        next_dt = _last_upload_time.timestamp() + _upload_interval_hours * 3600
        result["next_upload_in_s"] = max(0, int(next_dt - datetime.now().timestamp()))
    return result


def _get_ram_gb():
    """Get total system RAM in human-readable format. Cross-platform."""
    try:
        if platform.system() == "Windows":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return f"{mem.ullTotalPhys / (1024**3):.1f} GB"
        elif platform.system() == "Darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                    capture_output=True, text=True, timeout=5)
            return f"{int(result.stdout.strip()) / (1024**3):.1f} GB"
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return f"{kb / (1024**2):.1f} GB"
    except Exception:
        pass
    return "unknown"
