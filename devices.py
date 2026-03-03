import subprocess
import platform
import os

from config import adb_path, EMULATOR_PORTS
from botlog import get_logger

_log = get_logger("devices")

# ============================================================
# DEVICE DETECTION (cross-platform, multi-emulator)
# ============================================================

def auto_connect_emulators():
    """Try to adb-connect emulator ports so they show up in 'adb devices'.

    On Windows: inspects running emulator processes to discover their real ADB
    ports (BlueStacks assigns non-sequential ports like 5635 that aren't in
    any predictable range).  Only connects ports that ADB doesn't already see.

    On macOS/Linux: probes the well-known ports in EMULATOR_PORTS.
    """
    if platform.system() == "Windows":
        return _auto_connect_windows()

    return _auto_connect_by_ports()


def _auto_connect_windows():
    """Windows: find emulator ADB ports from running processes via psutil."""
    try:
        import psutil
    except ImportError:
        _log.debug("psutil not available — falling back to port scan")
        return _auto_connect_by_ports()

    # Collect ADB ports already known to the server
    existing = get_devices()
    known_ports = set()
    for d in existing:
        if d.startswith("emulator-"):
            try:
                known_ports.add(int(d.split("-")[1]) + 1)
            except (IndexError, ValueError):
                pass
        elif ":" in d:
            try:
                known_ports.add(int(d.split(":")[1]))
            except (IndexError, ValueError):
                pass

    # Process name patterns for supported emulators
    emu_names = ["hd-player", "bluestacks", "mumuplayer",
                 "mumuvmmheadless", "nemuheadless", "nemuplayer"]

    discovered_ports = set()
    for proc in psutil.process_iter(["pid", "name"]):
        pname = (proc.info["name"] or "").lower()
        if not any(n in pname for n in emu_names):
            continue
        try:
            for conn in proc.net_connections(kind="tcp4"):
                if conn.status == "LISTEN" and conn.laddr.ip == "127.0.0.1":
                    discovered_ports.add(conn.laddr.port)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

    new_ports = discovered_ports - known_ports
    if not new_ports:
        if existing:
            _log.debug("Auto-connect: all %d emulator(s) already visible", len(existing))
        else:
            _log.info("Auto-connect: no running emulator processes found")
        return []

    _log.info("Discovered %d new emulator port(s): %s", len(new_ports),
              ", ".join(str(p) for p in sorted(new_ports)))
    return _connect_ports(new_ports)


def _auto_connect_by_ports():
    """macOS/Linux: probe well-known emulator ports from EMULATOR_PORTS."""
    all_ports = set()
    for ports in EMULATOR_PORTS.values():
        all_ports.update(ports)
    return _connect_ports(all_ports)


def _connect_ports(ports):
    """Try ``adb connect`` on each port, return list of successfully connected addresses."""
    connected = []
    for port in sorted(ports):
        addr = f"127.0.0.1:{port}"
        try:
            result = subprocess.run(
                [adb_path, "connect", addr],
                capture_output=True, text=True, timeout=3
            )
            output = result.stdout.strip()
            if "connected" in output.lower():
                connected.append(addr)
                _log.debug("Connected: %s", addr)
        except (subprocess.TimeoutExpired, Exception):
            pass

    if connected:
        _log.info("Auto-connect found %d emulator(s)", len(connected))
    else:
        _log.info("Auto-connect: no emulators found on probed ports")
    return connected

def get_devices():
    """Get list of all connected ADB devices, with duplicates removed.

    ADB can show the same emulator twice — e.g. ``emulator-5554`` (auto-registered)
    and ``127.0.0.1:5555`` (from ``adb connect``).  The convention is that
    ``emulator-N`` uses ADB port ``N+1``, so we drop any ``127.0.0.1:<port>``
    entry whose port matches an existing ``emulator-<port-1>`` entry.
    """
    try:
        result = subprocess.run([adb_path, "devices"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split('\n')[1:]  # Skip "List of devices attached"
        raw = [line.split()[0] for line in lines if line.strip() and 'device' in line]

        # Build set of ADB ports claimed by emulator-N entries (port = N+1)
        emulator_ports = set()
        for d in raw:
            if d.startswith("emulator-"):
                try:
                    emulator_ports.add(int(d.split("-")[1]) + 1)
                except (IndexError, ValueError):
                    pass

        # Filter out 127.0.0.1:<port> duplicates
        devices = []
        for d in raw:
            if ":" in d and d.startswith("127.0.0.1:"):
                try:
                    port = int(d.split(":")[1])
                    if port in emulator_ports:
                        _log.debug("Dropping duplicate %s (same as emulator-%d)", d, port - 1)
                        continue
                except (IndexError, ValueError):
                    pass
            devices.append(d)

        _log.debug("Found %d device(s): %s", len(devices), ", ".join(devices) if devices else "(none)")
        return devices
    except Exception as e:
        _log.error("Failed to get devices: %s", e)
        return []

def get_emulator_instances():
    """Get mapping of device IDs to friendly display names.

    On Windows: maps ADB devices to emulator window titles via process
                inspection (supports BlueStacks and MuMu Player).
    On macOS:   reads BlueStacks config for instance display names.
    On Linux:   uses ADB device IDs as display names (no mapping).
    """
    devices = get_devices()

    if platform.system() == "Windows":
        return _get_emulator_instances_windows(devices)

    if platform.system() == "Darwin":
        return _get_emulator_instances_macos(devices)

    # Linux — no window mapping, just use device IDs
    _log.debug("Found devices: %s", devices)
    return {device: device for device in devices}

# ============================================================
# macOS: emulator instance name mapping
# ============================================================

_BLUESTACKS_CONF_MAC = "/Users/Shared/Library/Application Support/BlueStacks/bluestacks.conf"
_BLUESTACKS_CONF_WIN = r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf"
_BLUESTACKS_EXE_WIN = r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"


def _get_bluestacks_conf_path():
    """Return the platform-appropriate BlueStacks config path."""
    if platform.system() == "Windows":
        return _BLUESTACKS_CONF_WIN
    return _BLUESTACKS_CONF_MAC


def _get_emulator_instances_macos(devices):
    """Map ADB devices to emulator display names on macOS.

    Reads BlueStacks ``bluestacks.conf`` to extract instance display names and
    ADB ports, then matches them against connected ADB device IDs.
    """
    if not devices:
        return {}

    device_map = {d: d for d in devices}

    try:
        conf = _parse_bluestacks_conf()
    except Exception as e:
        _log.debug("Could not read BlueStacks config: %s", e)
        return device_map

    if not conf:
        return device_map

    # Build port (int) → display_name from config
    port_to_name = {}
    for instance_id, info in conf.items():
        port_str = info.get("adb_port")
        name = info.get("display_name")
        if port_str and name:
            try:
                port_to_name[int(port_str)] = name
            except ValueError:
                pass

    if not port_to_name:
        return device_map

    # Match device IDs to display names by port
    for device in devices:
        port = _extract_port(device)
        if port and port in port_to_name:
            device_map[device] = port_to_name[port]

    mapped = {d: n for d, n in device_map.items() if n != d}
    if mapped:
        _log.debug("macOS instance mapping: %s",
                   ", ".join(f"{d} -> {n}" for d, n in mapped.items()))

    return device_map


def _parse_bluestacks_conf(path=None):
    """Parse BlueStacks config file, returning {instance_id: {key: value}}.

    Config lines look like:
        bst.instance.Tiramisu64_1.display_name="Nine"
        bst.instance.Tiramisu64_1.adb_port="5565"
    """
    if path is None:
        path = _get_bluestacks_conf_path()

    if not os.path.isfile(path):
        return {}

    instances = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("bst.instance."):
                continue
            # bst.instance.<id>.<key>="<value>"
            rest = line[len("bst.instance."):]
            parts = rest.split(".", 1)
            if len(parts) != 2:
                continue
            instance_id = parts[0]
            kv = parts[1]
            if "=" not in kv:
                continue
            key, val = kv.split("=", 1)
            val = val.strip('"')
            if instance_id not in instances:
                instances[instance_id] = {}
            instances[instance_id][key] = val

    return instances


def _extract_port(device):
    """Extract port number from a device ID string."""
    if ":" in device:
        try:
            return int(device.split(":")[1])
        except (IndexError, ValueError):
            pass
    elif device.startswith("emulator-"):
        try:
            return int(device.split("-")[1]) + 1
        except (IndexError, ValueError):
            pass
    return None


# ============================================================
# WINDOWS-ONLY: emulator window name mapping
# ============================================================

def _get_emulator_instances_windows(devices):
    """Map ADB devices to emulator window names via network connections.

    For each emulator window PID, checks which port it LISTENs on, then
    matches that port to ADB device IDs (both ``127.0.0.1:port`` and
    ``emulator-N`` where port = N+1).
    """
    try:
        import win32gui
        import win32process
        import psutil
    except ImportError:
        _log.warning("pywin32/psutil not installed — using device IDs")
        return {d: d for d in devices}

    try:
        emulator_windows = {}

        EMULATOR_PROCESS_NAMES = [
            "hd-player",       # BlueStacks
            "bluestacks",      # BlueStacks (alt)
            "mumuplayer",      # MuMu Player
            "mumuvmmheadless", # MuMu Player 12 VM
            "nemuheadless",    # MuMu/Nemu older
            "nemuplayer",      # MuMu/Nemu older
        ]

        def enum_callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    try:
                        process = psutil.Process(pid)
                        process_name = process.name().lower()
                        if any(name in process_name for name in EMULATOR_PROCESS_NAMES):
                            window_text = win32gui.GetWindowText(hwnd)
                            if window_text:
                                results[pid] = {
                                    "name": window_text,
                                    "process": process_name
                                }
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                except Exception:
                    pass

        win32gui.EnumWindows(enum_callback, emulator_windows)

        # Build PID → ADB port mapping from network connections
        pid_to_port = {}
        for pid in emulator_windows:
            try:
                proc = psutil.Process(pid)
                for conn in proc.net_connections(kind="tcp4"):
                    if conn.status == "LISTEN" and conn.laddr.ip == "127.0.0.1":
                        pid_to_port[pid] = conn.laddr.port
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Build device → expected ADB port
        device_ports = {}
        for device in devices:
            if ":" in device:
                try:
                    device_ports[device] = int(device.split(":")[1])
                except (IndexError, ValueError):
                    pass
            elif device.startswith("emulator-"):
                try:
                    device_ports[device] = int(device.split("-")[1]) + 1
                except (IndexError, ValueError):
                    pass

        # Match devices to windows via port
        device_map = {}
        for device in devices:
            port = device_ports.get(device)
            if port:
                for pid, listen_port in pid_to_port.items():
                    if listen_port == port:
                        device_map[device] = emulator_windows[pid]["name"]
                        break
            if device not in device_map:
                device_map[device] = device

        mapped = {d: n for d, n in device_map.items() if n != d}
        if mapped:
            _log.debug("Window mapping: %s",
                       ", ".join(f"{d} -> {n}" for d, n in mapped.items()))

        return device_map

    except Exception as e:
        _log.error("Failed to get emulator instances: %s", e)
        return {d: d for d in devices}


# ============================================================
# BLUESTACKS INSTANCE CONTROL (Windows)
# ============================================================

def get_bluestacks_config():
    """Return {instance_name: {adb_port, display_name, ...}} from bluestacks.conf.

    Returns {} if file not found or not on a supported platform.
    """
    try:
        return _parse_bluestacks_conf()
    except Exception as e:
        _log.debug("Could not read BlueStacks config: %s", e)
        return {}


def get_bluestacks_running():
    """Return {instance_name: pid} for currently running HD-Player.exe processes.

    Parses ``--instance`` from command line args. Returns {} on non-Windows
    or if psutil is not available.
    """
    if platform.system() != "Windows":
        return {}
    try:
        import psutil
    except ImportError:
        return {}

    running = {}
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        pname = (proc.info["name"] or "").lower()
        if "hd-player" not in pname:
            continue
        try:
            cmdline = proc.info.get("cmdline") or []
            for i, arg in enumerate(cmdline):
                if arg == "--instance" and i + 1 < len(cmdline):
                    running[cmdline[i + 1]] = proc.info["pid"]
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return running


def start_bluestacks_instance(instance_name):
    """Start a BlueStacks instance with the game auto-launched.

    Returns the Popen object (non-blocking), or None on failure.
    """
    if platform.system() != "Windows":
        _log.warning("BlueStacks start only supported on Windows")
        return None

    if not os.path.isfile(_BLUESTACKS_EXE_WIN):
        _log.error("HD-Player.exe not found at %s", _BLUESTACKS_EXE_WIN)
        return None

    # Verify instance exists in config
    conf = get_bluestacks_config()
    if instance_name not in conf:
        _log.error("BlueStacks instance '%s' not found in config", instance_name)
        return None

    cmd = [
        _BLUESTACKS_EXE_WIN,
        "--instance", instance_name,
    ]
    _log.info("Starting BlueStacks instance '%s'", instance_name)
    try:
        proc = subprocess.Popen(cmd)
        return proc
    except Exception as e:
        _log.error("Failed to start BlueStacks instance '%s': %s", instance_name, e)
        return None


def stop_bluestacks_instance(instance_name):
    """Stop a running BlueStacks instance by killing its process.

    Returns True if the process was killed, False otherwise.
    """
    if platform.system() != "Windows":
        return False

    running = get_bluestacks_running()
    pid = running.get(instance_name)
    if pid is None:
        _log.warning("Instance '%s' is not running", instance_name)
        return False

    _log.info("Stopping BlueStacks instance '%s' (PID %d)", instance_name, pid)
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception as e:
        _log.error("Failed to kill PID %d: %s", pid, e)
        return False


def get_instance_for_device(device_id):
    """Map an ADB device ID to a BlueStacks instance name using port matching.

    Returns the instance name, or None if no match found.
    """
    port = _extract_port(device_id)
    if port is None:
        return None

    conf = get_bluestacks_config()
    for instance_name, info in conf.items():
        adb_port = info.get("adb_port")
        if adb_port:
            try:
                if int(adb_port) == port:
                    return instance_name
            except ValueError:
                pass
    return None


def get_offline_instances():
    """Return list of BlueStacks instances that are configured but not connected to ADB.

    Each entry is {instance, display_name, adb_port, device_id}.
    ``device_id`` is the synthetic ADB address (``127.0.0.1:<port>``).
    """
    conf = get_bluestacks_config()
    if not conf:
        return []

    # Ports currently visible to ADB
    connected = get_devices()
    connected_ports = set()
    for d in connected:
        p = _extract_port(d)
        if p is not None:
            connected_ports.add(p)

    offline = []
    for instance_name, info in conf.items():
        adb_port = info.get("adb_port")
        display_name = info.get("display_name", instance_name)
        if not adb_port:
            continue
        try:
            port_int = int(adb_port)
        except ValueError:
            continue
        if port_int in connected_ports:
            continue
        offline.append({
            "instance": instance_name,
            "display_name": display_name,
            "adb_port": port_int,
            "device_id": f"127.0.0.1:{port_int}",
        })
    return offline
