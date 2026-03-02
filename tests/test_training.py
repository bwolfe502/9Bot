"""Tests for the training data collector module."""

import json
import os
import shutil
import tempfile

import pytest
from unittest.mock import patch

import training


@pytest.fixture(autouse=True)
def _isolated_training(tmp_path):
    """Redirect training output to a temp directory and reset state."""
    orig_dir = training.TRAINING_DIR
    orig_img = training.IMAGES_DIR
    orig_file = training._file
    orig_path = training._file_path
    orig_count = training._entry_count
    orig_img_count = training._image_count
    orig_enabled = training._enabled

    training.TRAINING_DIR = str(tmp_path / "training_data")
    training.IMAGES_DIR = str(tmp_path / "training_data" / "images")
    training._file = None
    training._file_path = None
    training._entry_count = 0
    training._image_count = 0
    training._enabled = False

    yield tmp_path

    # Clean up file handle
    if training._file is not None and not training._file.closed:
        training._file.close()

    training.TRAINING_DIR = orig_dir
    training.IMAGES_DIR = orig_img
    training._file = orig_file
    training._file_path = orig_path
    training._entry_count = orig_count
    training._image_count = orig_img_count
    training._enabled = orig_enabled


class TestConfigure:
    def test_configure_enables(self):
        training.configure(True)
        assert training._enabled is True

    def test_configure_disables(self):
        training.configure(True)
        training.configure(False)
        assert training._enabled is False


class TestLogTemplate:
    def test_writes_nothing_when_disabled(self, _isolated_training):
        training.configure(False)
        training.log_template("dev1", "depart.png", 0.95, True, (500, 800))
        assert training._entry_count == 0

    def test_writes_entry_when_enabled(self, _isolated_training):
        training.configure(True)
        training.log_template("dev1", "depart.png", 0.95, True, (500, 800), (0, 700, 1080, 1600))
        assert training._entry_count == 1

        # Read back the JSONL
        jsonl_files = [f for f in os.listdir(training.TRAINING_DIR) if f.endswith(".jsonl")]
        assert len(jsonl_files) == 1
        with open(os.path.join(training.TRAINING_DIR, jsonl_files[0])) as f:
            entry = json.loads(f.readline())
        assert entry["type"] == "template"
        assert entry["dev"] == "dev1"
        assert entry["tpl"] == "depart.png"
        assert entry["conf"] == 95
        assert entry["hit"] is True
        assert entry["pos"] == [500, 800]
        assert entry["rgn"] == [0, 700, 1080, 1600]

    def test_miss_entry_no_position(self, _isolated_training):
        training.configure(True)
        training.log_template("dev1", "attack.png", 0.45, False)
        jsonl_files = [f for f in os.listdir(training.TRAINING_DIR) if f.endswith(".jsonl")]
        with open(os.path.join(training.TRAINING_DIR, jsonl_files[0])) as f:
            entry = json.loads(f.readline())
        assert entry["hit"] is False
        assert "pos" not in entry


class TestLogOcr:
    def test_writes_ocr_entry(self, _isolated_training):
        training.configure(True)
        training.log_ocr("dev1", (0, 590, 1080, 1820), "Defeat Titans(14/15)", 0.92, 0.88)
        assert training._entry_count == 1

        jsonl_files = [f for f in os.listdir(training.TRAINING_DIR) if f.endswith(".jsonl")]
        with open(os.path.join(training.TRAINING_DIR, jsonl_files[0])) as f:
            entry = json.loads(f.readline())
        assert entry["type"] == "ocr"
        assert entry["text"] == "Defeat Titans(14/15)"
        assert entry["avg_c"] == 92
        assert entry["min_c"] == 88


class TestLogScreen:
    def test_writes_screen_entry(self, _isolated_training):
        training.configure(True)
        scores = {"map_screen": 0.95, "battle_list": 0.12, "war_screen": 0.08}
        training.log_screen("dev1", scores, "map_screen", True)
        assert training._entry_count == 1

        jsonl_files = [f for f in os.listdir(training.TRAINING_DIR) if f.endswith(".jsonl")]
        with open(os.path.join(training.TRAINING_DIR, jsonl_files[0])) as f:
            entry = json.loads(f.readline())
        assert entry["type"] == "screen"
        assert entry["scores"]["map_screen"] == 95
        assert entry["scores"]["battle_list"] == 12
        assert entry["best"] == "map_screen"
        assert entry["hit"] is True


class TestSaveTrainingImage:
    def test_saves_jpeg_when_enabled(self, _isolated_training):
        import numpy as np
        training.configure(True)
        fake_screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        path = training.save_training_image("dev1", "near_miss", fake_screen,
                                            {"tpl": "depart.png", "conf": 75})
        assert path is not None
        assert path.endswith(".jpg")
        assert os.path.isfile(path)
        assert training._image_count == 1

    def test_returns_none_when_disabled(self, _isolated_training):
        import numpy as np
        training.configure(False)
        fake_screen = np.zeros((100, 100, 3), dtype=np.uint8)
        path = training.save_training_image("dev1", "near_miss", fake_screen)
        assert path is None

    def test_returns_none_for_none_screen(self, _isolated_training):
        training.configure(True)
        path = training.save_training_image("dev1", "near_miss", None)
        assert path is None

    def test_image_cap_enforced(self, _isolated_training):
        import numpy as np
        training.configure(True)
        # Override cap to something small for testing
        orig_max = training._MAX_IMAGES
        training._MAX_IMAGES = 5
        try:
            fake = np.zeros((10, 10, 3), dtype=np.uint8)
            for i in range(8):
                training.save_training_image(f"dev{i}", "test", fake)
            images = [f for f in os.listdir(training.IMAGES_DIR) if f.endswith(".jpg")]
            assert len(images) <= 5
        finally:
            training._MAX_IMAGES = orig_max


class TestJsonlCap:
    def test_old_jsonl_files_pruned(self, _isolated_training):
        training.configure(True)
        orig_max = training._MAX_JSONL_FILES
        training._MAX_JSONL_FILES = 3
        try:
            os.makedirs(training.TRAINING_DIR, exist_ok=True)
            # Create 5 fake JSONL files
            import time
            for i in range(5):
                path = os.path.join(training.TRAINING_DIR, f"td_fake_{i}.jsonl")
                with open(path, "w") as f:
                    f.write("{}\n")
                time.sleep(0.01)  # Ensure different mtimes
            training._cleanup_old_jsonl()
            files = [f for f in os.listdir(training.TRAINING_DIR) if f.endswith(".jsonl")]
            assert len(files) <= 3
        finally:
            training._MAX_JSONL_FILES = orig_max


class TestGetTrainingStats:
    def test_returns_stats_dict(self, _isolated_training):
        training.configure(True)
        stats = training.get_training_stats()
        assert stats["enabled"] is True
        assert stats["session_entries"] == 0
        assert stats["session_images"] == 0
        assert "total_size" in stats

    def test_counts_after_writes(self, _isolated_training):
        training.configure(True)
        training.log_template("dev1", "x.png", 0.9, True)
        training.log_template("dev1", "y.png", 0.5, False)
        stats = training.get_training_stats()
        assert stats["session_entries"] == 2
        assert stats["total_jsonl_files"] == 1


class TestShutdown:
    def test_closes_file_handle(self, _isolated_training):
        training.configure(True)
        training.log_template("dev1", "x.png", 0.9, True)
        assert training._file is not None
        training.shutdown()
        assert training._file is None
