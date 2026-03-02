"""
Training Data Collector — metadata-only JSONL logs with selective image capture.

Logs every template match, OCR read, and screen detection decision as a compact
JSON line. Saves screenshots only for interesting cases (near-misses, low-confidence
OCR, unknown screens). All data is opt-in via ``collect_training_data`` setting.

Key exports:
    log_template       — log a template match decision
    log_ocr            — log an OCR read result
    log_screen         — log screen detection scores
    save_training_image — save a screenshot for an interesting case
    get_training_stats — return current session stats for debug page
"""

import json
import os
import time
from datetime import datetime
from threading import Lock

import cv2

from botlog import SCRIPT_DIR

TRAINING_DIR = os.path.join(SCRIPT_DIR, "training_data")
IMAGES_DIR = os.path.join(TRAINING_DIR, "images")

_MAX_JSONL_FILES = 10
_MAX_IMAGES = 200

_lock = Lock()
_file = None          # open file handle for current session JSONL
_file_path = None     # path to current session JSONL
_entry_count = 0      # entries written this session
_image_count = 0      # images saved this session
_enabled = False      # master toggle, set via configure()


def configure(enabled):
    """Set the master toggle. Called from startup when settings are loaded."""
    global _enabled
    _enabled = enabled


def _ensure_dirs():
    """Create training_data/ and training_data/images/ if needed."""
    os.makedirs(IMAGES_DIR, exist_ok=True)


def _get_file():
    """Return the open JSONL file handle for this session, creating if needed."""
    global _file, _file_path
    if _file is not None and not _file.closed:
        return _file
    _ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _file_path = os.path.join(TRAINING_DIR, f"td_{timestamp}.jsonl")
    _file = open(_file_path, "a", encoding="utf-8")
    _cleanup_old_jsonl()
    return _file


def _cleanup_old_jsonl():
    """Keep only the newest _MAX_JSONL_FILES session files."""
    try:
        files = sorted(
            [f for f in os.listdir(TRAINING_DIR)
             if f.startswith("td_") and f.endswith(".jsonl")],
            key=lambda f: os.path.getmtime(os.path.join(TRAINING_DIR, f)),
        )
        while len(files) > _MAX_JSONL_FILES:
            oldest = files.pop(0)
            os.remove(os.path.join(TRAINING_DIR, oldest))
    except Exception:
        pass


def _cleanup_images():
    """Keep only the newest _MAX_IMAGES training images."""
    try:
        files = sorted(
            [f for f in os.listdir(IMAGES_DIR) if f.endswith(".jpg")],
            key=lambda f: os.path.getmtime(os.path.join(IMAGES_DIR, f)),
        )
        while len(files) > _MAX_IMAGES:
            oldest = files.pop(0)
            os.remove(os.path.join(IMAGES_DIR, oldest))
    except Exception:
        pass


def _write(entry):
    """Write a JSON entry to the session JSONL file."""
    global _entry_count
    if not _enabled:
        return
    try:
        with _lock:
            f = _get_file()
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            f.flush()
            _entry_count += 1
    except Exception:
        pass


def log_template(device, template, confidence, matched, position=None, region=None):
    """Log a template match decision from find_image()."""
    entry = {
        "ts": round(time.time(), 3),
        "type": "template",
        "dev": device,
        "tpl": template,
        "conf": round(confidence * 100),
        "hit": matched,
    }
    if position:
        entry["pos"] = list(position)
    if region:
        entry["rgn"] = list(region)
    _write(entry)


def log_ocr(device, region, text, avg_conf, min_conf):
    """Log an OCR read result from read_text()."""
    entry = {
        "ts": round(time.time(), 3),
        "type": "ocr",
        "dev": device,
        "text": text,
        "avg_c": round(avg_conf * 100),
        "min_c": round(min_conf * 100),
    }
    if region:
        entry["rgn"] = list(region)
    _write(entry)


def log_screen(device, scores, best_match, matched):
    """Log screen detection scores from check_screen()."""
    # Convert float scores to int percentages for compactness
    int_scores = {k: round(v * 100) for k, v in scores.items()}
    entry = {
        "ts": round(time.time(), 3),
        "type": "screen",
        "dev": device,
        "scores": int_scores,
        "best": best_match,
        "hit": matched,
    }
    _write(entry)


def save_training_image(device, category, screen, metadata=None):
    """Save a screenshot as JPEG for an interesting case.

    Args:
        device: ADB device ID.
        category: e.g. 'near_miss', 'ocr_low', 'unknown_screen', 'region_drift'.
        screen: CV2 image (BGR numpy array).
        metadata: optional dict of extra context to include in sidecar JSON.

    Returns the saved filepath, or None on error.
    """
    global _image_count
    if not _enabled or screen is None:
        return None
    try:
        _ensure_dirs()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:21]
        safe_device = device.replace(":", "_")
        filename = f"{timestamp}_{safe_device}_{category}.jpg"
        filepath = os.path.join(IMAGES_DIR, filename)
        cv2.imwrite(filepath, screen, [cv2.IMWRITE_JPEG_QUALITY, 70])
        _image_count += 1
        _cleanup_images()

        # Write metadata sidecar
        if metadata:
            sidecar = {
                "ts": round(time.time(), 3),
                "dev": device,
                "cat": category,
                "img": filename,
                **metadata,
            }
            _write(sidecar)

        return filepath
    except Exception:
        return None


def get_training_stats():
    """Return current session stats for the debug page."""
    jsonl_count = 0
    jsonl_size = 0
    image_count = 0
    image_size = 0

    try:
        if os.path.isdir(TRAINING_DIR):
            for f in os.listdir(TRAINING_DIR):
                if f.endswith(".jsonl"):
                    fpath = os.path.join(TRAINING_DIR, f)
                    jsonl_count += 1
                    jsonl_size += os.path.getsize(fpath)
    except Exception:
        pass

    try:
        if os.path.isdir(IMAGES_DIR):
            for f in os.listdir(IMAGES_DIR):
                if f.endswith(".jpg"):
                    fpath = os.path.join(IMAGES_DIR, f)
                    image_count += 1
                    image_size += os.path.getsize(fpath)
    except Exception:
        pass

    total_size = jsonl_size + image_size
    if total_size > 1_048_576:
        size_str = f"{total_size / 1_048_576:.1f} MB"
    else:
        size_str = f"{total_size / 1024:.0f} KB"

    return {
        "enabled": _enabled,
        "session_entries": _entry_count,
        "session_images": _image_count,
        "total_jsonl_files": jsonl_count,
        "total_images": image_count,
        "total_size": size_str,
    }


def shutdown():
    """Close the JSONL file handle. Called from startup.shutdown()."""
    global _file
    with _lock:
        if _file is not None and not _file.closed:
            _file.close()
            _file = None
