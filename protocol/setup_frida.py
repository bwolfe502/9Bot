#!/usr/bin/env python3
"""
Frida server deployment script for Android emulators (BlueStacks / MuMu).

Automates:  prerequisite checks, architecture detection, Frida server download,
            deployment via ADB, server startup, and game-process verification.

Usage:
    python3 protocol/setup_frida.py [device_id]

If *device_id* is omitted the first connected ADB device is used.
"""

from __future__ import annotations

import io
import lzma
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# ANSI helpers (disabled on Windows unless the new terminal is detected)
# ---------------------------------------------------------------------------

_COLOR = (
    os.name != "nt"
    or "WT_SESSION" in os.environ
    or os.environ.get("TERM_PROGRAM") == "vscode"
)

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text

def green(t: str)  -> str: return _c("32", t)
def red(t: str)    -> str: return _c("31", t)
def yellow(t: str) -> str: return _c("33", t)
def cyan(t: str)   -> str: return _c("36", t)
def bold(t: str)   -> str: return _c("1", t)

OK   = green("[OK]")
FAIL = red("[FAIL]")
WARN = yellow("[WARN]")
INFO = cyan("[*]")

# ---------------------------------------------------------------------------
# Globals populated during the run
# ---------------------------------------------------------------------------

FRIDA_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "frida-server-cache")

KINGDOM_GUARD_PACKAGE = "com.tap4fun.odin.kingdomguard"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 15, check: bool = False,
         capture: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess, returning CompletedProcess."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
        check=check,
    )


def _adb(args: list[str], device: str | None = None,
         timeout: int = 15) -> subprocess.CompletedProcess:
    """Run an ADB command, optionally targeting a specific device."""
    cmd = ["adb"]
    if device:
        cmd += ["-s", device]
    cmd += args
    return _run(cmd, timeout=timeout)


def _adb_shell(cmd_str: str, device: str | None = None,
               timeout: int = 15) -> subprocess.CompletedProcess:
    """Shorthand for `adb [-s device] shell <cmd_str>`."""
    return _adb(["shell", cmd_str], device=device, timeout=timeout)


def _abort(msg: str, code: int = 1) -> None:
    print(f"\n{FAIL} {red(msg)}")
    sys.exit(code)

# ---------------------------------------------------------------------------
# Step 1 — Prerequisites
# ---------------------------------------------------------------------------

def check_frida_module() -> str:
    """Return the installed frida version, or abort."""
    print(f"\n{INFO} Checking Frida Python module …")
    try:
        import frida  # type: ignore[import-untyped]
        ver = frida.__version__
        print(f"  {OK} frida {ver}")
        return ver
    except ImportError:
        print(f"  {FAIL} frida module not found")
        print(yellow("  Fix: pip install frida frida-tools"))
        _abort("Python frida module is required.")
    return ""  # unreachable, keeps mypy happy


def check_adb() -> str:
    """Return the path to adb, or abort."""
    print(f"\n{INFO} Checking ADB …")
    adb_path = shutil.which("adb")
    if adb_path is None:
        print(f"  {FAIL} adb not found on PATH")
        if platform.system() == "Windows":
            print(yellow("  Fix: Install Android SDK Platform-Tools and add to PATH"))
        else:
            print(yellow("  Fix: brew install android-platform-tools  (macOS)"))
        _abort("adb is required.")
    # Quick version check
    r = _run(["adb", "version"])
    ver_line = (r.stdout or "").strip().splitlines()[0] if r.stdout else "unknown"
    print(f"  {OK} {ver_line}")
    return adb_path


def list_devices() -> list[str]:
    """Return list of connected ADB device IDs (online only)."""
    print(f"\n{INFO} Listing connected ADB devices …")
    r = _run(["adb", "devices"])
    if r.returncode != 0:
        _abort("adb devices failed.")
    devices: list[str] = []
    for line in (r.stdout or "").strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if devices:
        for d in devices:
            print(f"  {OK} {d}")
    else:
        print(f"  {FAIL} No devices found")
        print(yellow("  Fix: Start your emulator and ensure USB debugging / ADB is enabled"))
        _abort("At least one connected ADB device is required.")
    return devices

# ---------------------------------------------------------------------------
# Step 2 — Detect emulator
# ---------------------------------------------------------------------------

def _getprop(prop: str, device: str) -> str:
    r = _adb_shell(f"getprop {prop}", device=device)
    return (r.stdout or "").strip()


def detect_emulator(device: str) -> dict:
    """Gather architecture, SDK level, model, and emulator type."""
    print(f"\n{INFO} Detecting emulator properties ({device}) …")

    abi = _getprop("ro.product.cpu.abi", device)
    sdk = _getprop("ro.build.version.sdk", device)
    model = _getprop("ro.product.model", device)
    brand = _getprop("ro.product.brand", device)
    manufacturer = _getprop("ro.product.manufacturer", device)
    board = _getprop("ro.product.board", device)
    release = _getprop("ro.build.version.release", device)

    # Detect emulator type heuristically
    fingerprint = " ".join([model, brand, manufacturer, board]).lower()
    emulator_type = "Unknown"
    if "bluestacks" in fingerprint or "bst" in fingerprint:
        emulator_type = "BlueStacks"
    elif "mumu" in fingerprint or "nemu" in fingerprint or "nox" in fingerprint:
        emulator_type = "MuMu"
    elif "samsung" in fingerprint:
        emulator_type = "Samsung (physical?)"
    elif "google" in fingerprint or "pixel" in fingerprint:
        emulator_type = "Google Emulator"

    # Map ABI to Frida arch name
    arch_map = {
        "arm64-v8a": "arm64",
        "armeabi-v7a": "arm",
        "x86_64": "x86_64",
        "x86": "x86",
    }
    frida_arch = arch_map.get(abi, abi)

    info = {
        "abi": abi,
        "frida_arch": frida_arch,
        "sdk": sdk,
        "android_version": release,
        "model": model,
        "brand": brand,
        "manufacturer": manufacturer,
        "emulator_type": emulator_type,
    }

    print(f"  Architecture : {abi} → Frida arch {cyan(frida_arch)}")
    print(f"  Android      : {release} (API {sdk})")
    print(f"  Model        : {model} ({brand}/{manufacturer})")
    print(f"  Emulator     : {cyan(emulator_type)}")

    if not abi:
        _abort("Could not determine device architecture.  Is the device fully booted?")
    if frida_arch not in ("arm64", "arm", "x86_64", "x86"):
        _abort(f"Unsupported architecture: {abi}")

    return info

# ---------------------------------------------------------------------------
# Step 3 — Root access
# ---------------------------------------------------------------------------

def check_root(device: str, emulator_type: str) -> None:
    """Verify we can execute commands as root on the device."""
    print(f"\n{INFO} Checking root access …")

    # Method 1: adb root
    r = _adb(["root"], device=device, timeout=10)
    adb_root_ok = r.returncode == 0 and "cannot" not in (r.stdout or "").lower()
    if adb_root_ok:
        # adb root restarts adbd — give it a moment and re-check connectivity
        time.sleep(2)
        _adb(["wait-for-device"], device=device, timeout=10)
        print(f"  {OK} adb root succeeded")
        return

    # Method 2: su -c id
    r = _adb_shell("su -c id", device=device, timeout=10)
    su_out = (r.stdout or "").strip()
    if "uid=0" in su_out:
        print(f"  {OK} su -c id → root")
        return

    # Method 3: whoami
    r = _adb_shell("whoami", device=device, timeout=10)
    who = (r.stdout or "").strip()
    if who == "root":
        print(f"  {OK} whoami → root")
        return

    # --- Root not available — loud error ---
    print(f"  {FAIL} Root access is {bold(red('NOT AVAILABLE'))}")
    print()
    print(red("=" * 60))
    print(red("  ROOT ACCESS IS REQUIRED FOR FRIDA SERVER"))
    print(red("=" * 60))
    print()

    if emulator_type == "BlueStacks":
        print(yellow("  BlueStacks root instructions:"))
        print("    1. Open BlueStacks Settings > Advanced")
        print("    2. Enable Android Debug Bridge (ADB)")
        print("    3. Enable root access in Settings > Advanced")
        print("    4. Restart BlueStacks")
    elif emulator_type == "MuMu":
        print(yellow("  MuMu root instructions:"))
        print("    1. Open MuMu settings > Other")
        print("    2. Root permission > Enable")
        print("    3. Restart MuMu")
    else:
        print(yellow("  Generic instructions:"))
        print("    Enable root/superuser access in your emulator settings.")
        print("    Most emulators have a toggle in Settings or Preferences.")
    print()
    _abort("Cannot continue without root access.")

# ---------------------------------------------------------------------------
# Step 4 — Download Frida server
# ---------------------------------------------------------------------------

def download_frida_server(version: str, arch: str) -> str:
    """Download and extract frida-server, returning the path to the binary.

    Caches downloads in FRIDA_CACHE_DIR to avoid re-downloading.
    """
    print(f"\n{INFO} Preparing Frida server {version} for {arch} …")

    os.makedirs(FRIDA_CACHE_DIR, exist_ok=True)

    binary_name = f"frida-server-{version}-android-{arch}"
    cached_bin = os.path.join(FRIDA_CACHE_DIR, binary_name)

    if os.path.isfile(cached_bin):
        print(f"  {OK} Cached binary found: {cached_bin}")
        return cached_bin

    xz_name = f"{binary_name}.xz"
    url = (
        f"https://github.com/frida/frida/releases/download/"
        f"{version}/{xz_name}"
    )
    xz_path = os.path.join(FRIDA_CACHE_DIR, xz_name)

    print(f"  Downloading {url} …")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "setup_frida/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            buf = io.BytesIO()
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                buf.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    mb = downloaded / (1024 * 1024)
                    print(f"\r  Downloading … {mb:.1f} MB ({pct}%)", end="", flush=True)
            print()  # newline after progress
            data = buf.getvalue()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"  {FAIL} Release not found (404)")
            print(yellow(f"  URL: {url}"))
            print(yellow("  Possible cause: frida-tools version mismatch.  "
                         "Try: pip install --upgrade frida frida-tools"))
            _abort("Frida server binary not found for this version/arch.")
        raise
    except Exception as exc:
        _abort(f"Download failed: {exc}")

    # Write .xz then decompress
    with open(xz_path, "wb") as f:
        f.write(data)
    print(f"  {OK} Downloaded {len(data) / (1024*1024):.1f} MB")

    print("  Extracting .xz …")
    try:
        with lzma.open(xz_path) as xz_f:
            with open(cached_bin, "wb") as out_f:
                shutil.copyfileobj(xz_f, out_f)
    except lzma.LZMAError as exc:
        _abort(f"Extraction failed (corrupt .xz?): {exc}")

    # Cleanup the .xz
    try:
        os.remove(xz_path)
    except OSError:
        pass

    size_mb = os.path.getsize(cached_bin) / (1024 * 1024)
    print(f"  {OK} Extracted → {cached_bin} ({size_mb:.1f} MB)")
    return cached_bin

# ---------------------------------------------------------------------------
# Step 5 — Deploy Frida server
# ---------------------------------------------------------------------------

def deploy_frida_server(local_bin: str, device: str) -> None:
    """Push frida-server to the device and set permissions."""
    remote_path = "/data/local/tmp/frida-server"
    print(f"\n{INFO} Deploying Frida server to {device} …")

    print(f"  Pushing {os.path.basename(local_bin)} → {remote_path} …")
    r = _adb(["push", local_bin, remote_path], device=device, timeout=120)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        _abort(f"adb push failed: {err}")
    print(f"  {OK} Pushed")

    # chmod via su (in case adbd is not running as root)
    r = _adb_shell(f"chmod 755 {remote_path}", device=device)
    if r.returncode != 0:
        # Retry with su
        _adb_shell(f"su -c 'chmod 755 {remote_path}'", device=device)
    print(f"  {OK} Permissions set (755)")

# ---------------------------------------------------------------------------
# Step 6 — Start Frida server
# ---------------------------------------------------------------------------

def _get_frida_pid(device: str) -> str | None:
    """Return PID of running frida-server, or None."""
    r = _adb_shell("pidof frida-server", device=device)
    pid = (r.stdout or "").strip()
    return pid if pid else None


def start_frida_server(device: str) -> str:
    """Start frida-server on the device, returning its PID."""
    remote_path = "/data/local/tmp/frida-server"
    print(f"\n{INFO} Starting Frida server …")

    existing_pid = _get_frida_pid(device)
    if existing_pid:
        print(f"  {WARN} Frida server already running (PID {existing_pid})")
        print("  Restarting …")
        _adb_shell(f"kill -9 {existing_pid}", device=device)
        time.sleep(1)
        # Retry with su if kill failed
        if _get_frida_pid(device):
            _adb_shell(f"su -c 'kill -9 {existing_pid}'", device=device)
            time.sleep(1)

    # Start in background.  Try su first, fall back to direct.
    _adb_shell(f"su -c '{remote_path} -D &'", device=device, timeout=5)
    time.sleep(2)

    pid = _get_frida_pid(device)
    if not pid:
        # Fallback: try without su (adbd may already be root)
        _adb_shell(f"{remote_path} -D &", device=device, timeout=5)
        time.sleep(2)
        pid = _get_frida_pid(device)

    if not pid:
        _abort("Frida server failed to start.  Check root access and SELinux status.\n"
               "  Try manually: adb shell su -c '/data/local/tmp/frida-server -D &'")

    print(f"  {OK} Frida server running (PID {pid})")

    # Test connectivity via the Frida Python API
    print("  Testing Frida connectivity …")
    try:
        import frida  # type: ignore[import-untyped]

        # For TCP devices use frida.get_device_manager().add_remote_device()
        # For local/USB try get_usb_device first, fall back to remote
        dev = None
        try:
            dev = frida.get_usb_device(timeout=5)
        except Exception:
            pass

        if dev is None:
            # TCP emulator: try connecting via the ADB forwarded port
            try:
                mgr = frida.get_device_manager()
                host = device.split(":")[0] if ":" in device else "127.0.0.1"
                dev = mgr.add_remote_device(f"{host}:27042")
            except Exception:
                pass

        if dev is None:
            # Last resort: enumerate all devices and find one that works
            try:
                for d in frida.enumerate_devices():
                    if d.type in ("usb", "remote", "local"):
                        try:
                            d.enumerate_processes()
                            dev = d
                            break
                        except Exception:
                            continue
            except Exception:
                pass

        if dev is not None:
            procs = dev.enumerate_processes()
            print(f"  {OK} Frida API connected — {len(procs)} processes visible")
        else:
            print(f"  {WARN} Could not connect via Frida API (server is running, "
                  "but Python binding couldn't reach it)")
            print(yellow("  This may still work — try: frida-ps -U"))
    except Exception as exc:
        print(f"  {WARN} Frida API test raised: {exc}")
        print(yellow("  The server is running; API test is informational only."))

    return pid

# ---------------------------------------------------------------------------
# Step 7 — Verify game process
# ---------------------------------------------------------------------------

def verify_game_process(device: str) -> dict | None:
    """Check if Kingdom Guard is running and attempt a test attach."""
    print(f"\n{INFO} Checking for Kingdom Guard process …")

    r = _adb_shell(f"pidof {KINGDOM_GUARD_PACKAGE}", device=device)
    pid_str = (r.stdout or "").strip()

    if not pid_str:
        print(f"  {WARN} Kingdom Guard ({KINGDOM_GUARD_PACKAGE}) is not running")
        print(yellow("  Launch the game before running interception scripts."))
        return None

    # pidof may return multiple PIDs; take the first
    pid = pid_str.split()[0]
    print(f"  {OK} Found {KINGDOM_GUARD_PACKAGE} (PID {pid})")

    # Test attachment via Frida API
    try:
        import frida  # type: ignore[import-untyped]
        dev = None
        try:
            dev = frida.get_usb_device(timeout=5)
        except Exception:
            try:
                mgr = frida.get_device_manager()
                host = device.split(":")[0] if ":" in device else "127.0.0.1"
                dev = mgr.add_remote_device(f"{host}:27042")
            except Exception:
                pass

        if dev is not None:
            print("  Test-attaching to game process …")
            session = dev.attach(int(pid))
            session.detach()
            print(f"  {OK} Test attach/detach succeeded")
        else:
            print(f"  {WARN} Skipping test attach (no Frida device handle)")
    except Exception as exc:
        print(f"  {WARN} Test attach failed: {exc}")
        print(yellow("  This may be normal if the game uses anti-tamper. "
                     "Spawning may work better than attaching."))

    return {"package": KINGDOM_GUARD_PACKAGE, "pid": pid}

# ---------------------------------------------------------------------------
# Step 8 — Summary
# ---------------------------------------------------------------------------

def print_summary(frida_version: str, device: str, emu_info: dict,
                  server_pid: str, game_info: dict | None) -> None:
    print()
    print(green("=" * 50))
    print(green("  Setup Complete!"))
    print(green("=" * 50))
    print(f"  Frida version  : {frida_version}")
    print(f"  Device         : {device} ({emu_info['emulator_type']}, "
          f"{emu_info['frida_arch']}, Android {emu_info['android_version']})")
    print(f"  Frida server   : running (PID {server_pid})")
    if game_info:
        print(f"  Game process   : {game_info['package']} (PID {game_info['pid']})")
    else:
        print(f"  Game process   : {yellow('not running')}")
    print(f"  Ready for protocol interception!")
    print()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(bold("Frida Server Setup for Android Emulator"))
    print(bold("=" * 42))

    # Parse optional device_id argument
    target_device: str | None = None
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target_device = sys.argv[1]

    # Step 1 — Prerequisites
    frida_version = check_frida_module()
    check_adb()
    devices = list_devices()

    if target_device:
        if target_device not in devices:
            _abort(f"Specified device {target_device} is not connected.\n"
                   f"  Connected: {', '.join(devices)}")
        device = target_device
    else:
        device = devices[0]
        if len(devices) > 1:
            print(f"\n  {WARN} Multiple devices detected — using first: {cyan(device)}")
            print(yellow(f"  To use a different device: python3 {sys.argv[0]} <device_id>"))

    # Step 2 — Detect emulator
    emu_info = detect_emulator(device)

    # Step 3 — Root access
    check_root(device, emu_info["emulator_type"])

    # Step 4 — Download
    local_bin = download_frida_server(frida_version, emu_info["frida_arch"])

    # Step 5 — Deploy
    deploy_frida_server(local_bin, device)

    # Step 6 — Start
    server_pid = start_frida_server(device)

    # Step 7 — Game process
    game_info = verify_game_process(device)

    # Step 8 — Summary
    print_summary(frida_version, device, emu_info, server_pid, game_info)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{yellow('Interrupted.')}")
        sys.exit(130)
    except subprocess.TimeoutExpired:
        _abort("An ADB command timed out. Is the emulator responsive?")
