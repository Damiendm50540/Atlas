import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module


class CaptureOpenTests(unittest.TestCase):
    def test_open_capture_accepts_valid_local_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(5):
                gray = np.full((48, 64), 200 + (i % 2) * 20, dtype=np.uint8)
                color = cv2.merge([gray, gray, gray])
                writer.write(color)
            writer.release()

            cap, error = app_module.open_capture(str(path))
            self.assertIsNone(error)
            self.assertIsNotNone(cap)
            self.assertTrue(cap.isOpened())
            cap.release()

    def test_open_capture_rejects_invalid_url(self):
        cap, error = app_module.open_capture("rtsp://127.0.0.1:1/stream")
        self.assertIsNone(cap)
        self.assertIn("Impossible d'ouvrir le flux", error)


if __name__ == "__main__":
    unittest.main()
