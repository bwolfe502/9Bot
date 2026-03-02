"""Tests for device detection (devices.py)."""

import subprocess
import pytest
from unittest.mock import patch, MagicMock

from devices import (auto_connect_emulators, get_devices, get_emulator_instances,
                     _auto_connect_by_ports, _connect_ports,
                     _get_emulator_instances_macos, _parse_bluestacks_conf,
                     _extract_port)


# ============================================================
# auto_connect_emulators / _connect_ports / _auto_connect_by_ports
# ============================================================

class TestConnectPorts:
    """Tests for _connect_ports (shared by Windows and non-Windows paths)."""

    @pytest.mark.parametrize("output", [
        "connected to 127.0.0.1:7555",
        "already connected to 127.0.0.1:7555",
    ])
    @patch("devices.subprocess.run")
    def test_successful_connection(self, mock_run, output):
        """Both 'connected to' and 'already connected to' are success."""
        mock_run.return_value = MagicMock(stdout=output)
        result = _connect_ports({7555})
        assert "127.0.0.1:7555" in result

    @patch("devices.subprocess.run")
    def test_timeout_skipped(self, mock_run):
        """Ports that time out are silently skipped."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="adb", timeout=3)
        result = _connect_ports({7555})
        assert result == []


class TestAutoConnectByPorts:
    """Tests for _auto_connect_by_ports (macOS/Linux path)."""

    @patch("devices.EMULATOR_PORTS", {"mumu": [7555, 7556]})
    @patch("devices.subprocess.run")
    def test_probes_known_ports(self, mock_run):
        mock_run.return_value = MagicMock(stdout="connected to 127.0.0.1:7555")
        result = _auto_connect_by_ports()
        assert "127.0.0.1:7555" in result


class TestAutoConnectEmulators:
    """Tests for auto_connect_emulators dispatch logic."""

    @patch("devices.platform.system", return_value="Linux")
    @patch("devices.EMULATOR_PORTS", {"mumu": [7555]})
    @patch("devices.subprocess.run")
    def test_non_windows_probes_ports(self, mock_run, _mock_sys):
        """On non-Windows, probes known emulator ports."""
        mock_run.return_value = MagicMock(stdout="connected to 127.0.0.1:7555")
        result = auto_connect_emulators()
        assert "127.0.0.1:7555" in result

    @patch("devices.platform.system", return_value="Windows")
    @patch("devices._auto_connect_windows")
    def test_windows_delegates(self, mock_win, _mock_sys):
        """On Windows, delegates to _auto_connect_windows."""
        mock_win.return_value = ["127.0.0.1:5635"]
        result = auto_connect_emulators()
        assert result == ["127.0.0.1:5635"]
        mock_win.assert_called_once()


# ============================================================
# get_devices
# ============================================================

class TestGetDevices:
    @patch("devices.subprocess.run")
    def test_single_device(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="List of devices attached\n127.0.0.1:7555\tdevice\n"
        )
        result = get_devices()
        assert result == ["127.0.0.1:7555"]

    @patch("devices.subprocess.run")
    def test_multiple_devices(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="List of devices attached\n127.0.0.1:7555\tdevice\n127.0.0.1:5555\tdevice\n"
        )
        result = get_devices()
        assert len(result) == 2

    @patch("devices.subprocess.run")
    def test_duplicate_emulator_and_ip_deduplicated(self, mock_run):
        """emulator-5554 and 127.0.0.1:5555 are the same device (port 5554+1),
        so get_devices() keeps only the emulator-N form."""
        mock_run.return_value = MagicMock(
            stdout="List of devices attached\nemulator-5554\tdevice\n127.0.0.1:5555\tdevice\n"
        )
        result = get_devices()
        assert result == ["emulator-5554"]

    @patch("devices.subprocess.run")
    def test_non_overlapping_ip_kept(self, mock_run):
        """127.0.0.1:<port> entries that don't overlap with emulator-N are kept."""
        mock_run.return_value = MagicMock(
            stdout="List of devices attached\nemulator-5554\tdevice\n127.0.0.1:7555\tdevice\n"
        )
        result = get_devices()
        assert result == ["emulator-5554", "127.0.0.1:7555"]

    @patch("devices.subprocess.run")
    def test_empty(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="List of devices attached\n"
        )
        result = get_devices()
        assert result == []

    @patch("devices.subprocess.run")
    def test_subprocess_failure(self, mock_run):
        mock_run.side_effect = Exception("ADB not found")
        result = get_devices()
        assert result == []


# ============================================================
# get_emulator_instances
# ============================================================

class TestGetEmulatorInstances:
    @patch("devices.get_devices")
    @patch("devices.platform.system")
    def test_linux_returns_device_ids(self, mock_platform, mock_get_devices):
        """On Linux, returns device IDs as display names (no mapping)."""
        mock_platform.return_value = "Linux"
        mock_get_devices.return_value = ["127.0.0.1:7555"]
        result = get_emulator_instances()
        assert result == {"127.0.0.1:7555": "127.0.0.1:7555"}

    @patch("devices._get_emulator_instances_macos")
    @patch("devices.get_devices")
    @patch("devices.platform.system")
    def test_macos_delegates(self, mock_platform, mock_get_devices, mock_mac_func):
        """On macOS, delegates to _get_emulator_instances_macos."""
        mock_platform.return_value = "Darwin"
        mock_get_devices.return_value = ["127.0.0.1:5565"]
        mock_mac_func.return_value = {"127.0.0.1:5565": "Nine"}
        result = get_emulator_instances()
        assert result == {"127.0.0.1:5565": "Nine"}
        mock_mac_func.assert_called_once_with(["127.0.0.1:5565"])

    @patch("devices.get_devices")
    @patch("devices.platform.system")
    def test_empty_device_list(self, mock_platform, mock_get_devices):
        mock_platform.return_value = "Linux"
        mock_get_devices.return_value = []
        result = get_emulator_instances()
        assert result == {}

    @patch("devices._get_emulator_instances_windows")
    @patch("devices.get_devices")
    @patch("devices.platform.system")
    def test_windows_delegates(self, mock_platform, mock_get_devices, mock_win_func):
        """On Windows, delegates to _get_emulator_instances_windows."""
        mock_platform.return_value = "Windows"
        mock_get_devices.return_value = ["127.0.0.1:7555"]
        mock_win_func.return_value = {"127.0.0.1:7555": "MuMu Player 1"}
        result = get_emulator_instances()
        assert result == {"127.0.0.1:7555": "MuMu Player 1"}
        mock_win_func.assert_called_once_with(["127.0.0.1:7555"])


# ============================================================
# macOS instance mapping
# ============================================================

_SAMPLE_CONF = """\
bst.version="5.21.755.7538"
bst.installed_images="Tiramisu64"
bst.instance.Tiramisu64.adb_port="5555"
bst.instance.Tiramisu64.display_name="Plop"
bst.instance.Tiramisu64_1.adb_port="5565"
bst.instance.Tiramisu64_1.display_name="Nine"
"""


class TestExtractPort:
    def test_ip_port(self):
        assert _extract_port("127.0.0.1:5565") == 5565

    def test_emulator_format(self):
        assert _extract_port("emulator-5554") == 5555

    def test_invalid(self):
        assert _extract_port("unknown") is None


class TestParseBluestacksConf:
    @patch("builtins.open", create=True)
    @patch("os.path.isfile", return_value=True)
    def test_parses_instances(self, _mock_isfile, mock_open):
        from io import StringIO
        mock_open.return_value.__enter__ = lambda s: StringIO(_SAMPLE_CONF)
        mock_open.return_value.__exit__ = lambda s, *a: None
        result = _parse_bluestacks_conf()
        assert result["Tiramisu64"]["display_name"] == "Plop"
        assert result["Tiramisu64"]["adb_port"] == "5555"
        assert result["Tiramisu64_1"]["display_name"] == "Nine"
        assert result["Tiramisu64_1"]["adb_port"] == "5565"

    @patch("os.path.isfile", return_value=False)
    def test_missing_conf_returns_empty(self, _mock_isfile):
        result = _parse_bluestacks_conf()
        assert result == {}


class TestGetEmulatorInstancesMacOS:
    @patch("devices._parse_bluestacks_conf")
    def test_maps_port_to_display_name(self, mock_conf):
        mock_conf.return_value = {
            "Tiramisu64": {"adb_port": "5555", "display_name": "Plop"},
            "Tiramisu64_1": {"adb_port": "5565", "display_name": "Nine"},
        }
        result = _get_emulator_instances_macos(["127.0.0.1:5565"])
        assert result == {"127.0.0.1:5565": "Nine"}

    @patch("devices._parse_bluestacks_conf")
    def test_unmatched_device_keeps_id(self, mock_conf):
        mock_conf.return_value = {
            "Tiramisu64": {"adb_port": "5555", "display_name": "Plop"},
        }
        result = _get_emulator_instances_macos(["127.0.0.1:7555"])
        assert result == {"127.0.0.1:7555": "127.0.0.1:7555"}

    @patch("devices._parse_bluestacks_conf")
    def test_no_conf_returns_device_ids(self, mock_conf):
        mock_conf.return_value = {}
        result = _get_emulator_instances_macos(["127.0.0.1:5565"])
        assert result == {"127.0.0.1:5565": "127.0.0.1:5565"}

    def test_empty_devices(self):
        result = _get_emulator_instances_macos([])
        assert result == {}

    @patch("devices._parse_bluestacks_conf")
    def test_multiple_instances(self, mock_conf):
        mock_conf.return_value = {
            "Tiramisu64": {"adb_port": "5555", "display_name": "Plop"},
            "Tiramisu64_1": {"adb_port": "5565", "display_name": "Nine"},
        }
        result = _get_emulator_instances_macos(
            ["127.0.0.1:5555", "127.0.0.1:5565"]
        )
        assert result == {
            "127.0.0.1:5555": "Plop",
            "127.0.0.1:5565": "Nine",
        }

    @patch("devices._parse_bluestacks_conf")
    def test_conf_exception_falls_back(self, mock_conf):
        mock_conf.side_effect = IOError("disk error")
        result = _get_emulator_instances_macos(["127.0.0.1:5565"])
        assert result == {"127.0.0.1:5565": "127.0.0.1:5565"}
