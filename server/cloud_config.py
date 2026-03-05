"""Cloud-specific configuration and defaults for hosted 9Bot instances.

When ``CLOUD_MODE=1`` is set, this module provides sensible defaults
for server-side operation (remote access always on, auto-upload enabled,
no interactive prompts).

Environment variables:
    CLOUD_MODE          -- "1" to enable cloud mode
    NINEBOT_LICENSE_KEY -- License key (replaces interactive prompt)
    NINEBOT_INSTANCE_ID -- Unique instance identifier (e.g. "vm1-inst2")
    NINEBOT_PORT        -- Web dashboard port (default 8080)
    NINEBOT_AGENT_SECRET -- Shared secret for VM agent authentication
"""

import os


def is_cloud_mode():
    """Return True if running in cloud mode."""
    return os.environ.get("CLOUD_MODE") == "1"


# Defaults that override settings.json in cloud mode
CLOUD_DEFAULTS = {
    "remote_access": True,          # always accessible via relay
    "auto_upload_logs": True,       # auto-upload bug reports for monitoring
    "upload_interval_hours": 6,     # more frequent uploads for cloud monitoring
    "verbose_logging": False,       # keep logs manageable
    "collect_training_data": False,  # save disk on cloud VMs
}


def get_instance_id():
    """Return the cloud instance ID, or None if not in cloud mode."""
    if not is_cloud_mode():
        return None
    return os.environ.get("NINEBOT_INSTANCE_ID", "unknown")


def get_port():
    """Return the web dashboard port for this instance."""
    try:
        return int(os.environ.get("NINEBOT_PORT", "8080"))
    except (ValueError, TypeError):
        return 8080


def apply_cloud_defaults(settings):
    """Merge cloud defaults into settings dict (only sets missing keys).

    Called during ``initialize()`` in cloud mode. Does NOT overwrite
    user-set values — only fills in keys that aren't already present.

    Returns the modified settings dict.
    """
    if not is_cloud_mode():
        return settings
    for key, value in CLOUD_DEFAULTS.items():
        if key not in settings:
            settings[key] = value
    return settings
