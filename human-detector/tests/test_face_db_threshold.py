import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import face_db


def test_default_recognition_threshold_is_reasonable():
    assert 0.45 <= face_db.THRESHOLD <= 0.65, (
        f"threshold not in reasonable range: {face_db.THRESHOLD}"
    )


if __name__ == "__main__":
    test_default_recognition_threshold_is_reasonable()
    print("threshold-ok")
