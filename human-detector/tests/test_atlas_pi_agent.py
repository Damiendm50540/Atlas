import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas_pi_agent import parse_cpu_counters


class CpuCounterTests(unittest.TestCase):
    def test_parse_cpu_counters_includes_idle_and_io_wait(self):
        total, idle = parse_cpu_counters("cpu 100 20 30 400 50 6 7 8 9 10")

        self.assertEqual(total, 621)
        self.assertEqual(idle, 450)

    def test_parse_cpu_counters_rejects_non_cpu_lines(self):
        with self.assertRaises(ValueError):
            parse_cpu_counters("cpu0 100 20 30 400")


if __name__ == "__main__":
    unittest.main()
