import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module


def valid_agent_metrics(**overrides):
    metrics = {
        "hostname": "atlas-pi",
        "cpu_percent": 18.7,
        "memory_percent": 35.2,
        "memory_used": 352,
        "memory_total": 1000,
        "temperature_c": 47.5,
        "disk_percent": 62.3,
        "disk_used": 623,
        "disk_total": 1000,
        "timestamp": time.time(),
    }
    metrics.update(overrides)
    return app_module.AgentSystemMetrics(**metrics)


class SystemMetricsTests(unittest.TestCase):
    @patch.object(app_module, "_read_temperature_c", return_value=47.5)
    @patch.object(app_module.psutil, "disk_usage", return_value=SimpleNamespace(percent=62.3, used=623, total=1000))
    @patch.object(app_module.psutil, "virtual_memory", return_value=SimpleNamespace(percent=35.2, used=352, total=1000))
    @patch.object(app_module.psutil, "cpu_percent", return_value=18.7)
    def test_system_metrics_include_resources_and_timestamp(self, *_mocks):
        stats = app_module.read_system_stats()

        self.assertEqual(stats["cpu_percent"], 18.7)
        self.assertEqual(stats["memory_percent"], 35.2)
        self.assertEqual(stats["memory_used"], 352)
        self.assertEqual(stats["memory_total"], 1000)
        self.assertEqual(stats["temperature_c"], 47.5)
        self.assertEqual(stats["disk_percent"], 62.3)
        self.assertEqual(stats["disk_used"], 623)
        self.assertEqual(stats["disk_total"], 1000)
        self.assertIsInstance(stats["timestamp"], float)

    @patch.object(app_module.Path, "is_dir", return_value=False)
    @patch.object(app_module.psutil, "sensors_temperatures", create=True, return_value={})
    def test_temperature_is_unavailable_when_host_has_no_sensor(self, *_mocks):
        self.assertIsNone(app_module._read_temperature_c())

    def test_agent_requires_a_configured_token(self):
        with patch.dict(app_module.os.environ, {}, clear=False):
            app_module.os.environ.pop("ATLAS_AGENT_TOKEN", None)
            with self.assertRaises(app_module.HTTPException) as error:
                app_module.receive_agent_system_stats(valid_agent_metrics(), "Bearer secret")
        self.assertEqual(error.exception.status_code, 503)

    @patch.dict(app_module.os.environ, {"ATLAS_AGENT_TOKEN": "test-secret"})
    def test_agent_rejects_an_invalid_token(self):
        with self.assertRaises(app_module.HTTPException) as error:
            app_module.receive_agent_system_stats(valid_agent_metrics(), "Bearer wrong")
        self.assertEqual(error.exception.status_code, 401)

    @patch.dict(app_module.os.environ, {"ATLAS_AGENT_TOKEN": "test-secret"})
    def test_valid_agent_metrics_are_stored_and_returned_as_raspberry_pi(self):
        previous = app_module.AGENT_METRICS
        try:
            app_module.AGENT_METRICS = None
            response = app_module.receive_agent_system_stats(
                valid_agent_metrics(), "Bearer test-secret"
            )
            self.assertEqual(response, {"ok": True})
            stats = app_module.current_system_stats()
            self.assertEqual(stats["source"], "raspberry_pi")
            self.assertEqual(stats["hostname"], "atlas-pi")
            self.assertEqual(stats["memory_total"], 1000)
        finally:
            app_module.AGENT_METRICS = previous

    @patch.dict(app_module.os.environ, {"ATLAS_AGENT_TOKEN": "test-secret"})
    def test_agent_rejects_stale_metrics(self):
        with self.assertRaises(app_module.HTTPException) as error:
            app_module.receive_agent_system_stats(
                valid_agent_metrics(timestamp=time.time() - 121),
                "Bearer test-secret",
            )
        self.assertEqual(error.exception.status_code, 422)

    @patch.dict(app_module.os.environ, {"ATLAS_AGENT_TOKEN": "test-secret"})
    def test_agent_rejects_capacity_values_above_total(self):
        with self.assertRaises(app_module.HTTPException) as error:
            app_module.receive_agent_system_stats(
                valid_agent_metrics(memory_used=1001),
                "Bearer test-secret",
            )
        self.assertEqual(error.exception.status_code, 422)

    @patch.dict(app_module.os.environ, {"ATLAS_AGENT_TOKEN": "test-secret"})
    @patch.object(app_module, "read_system_stats", return_value={"cpu_percent": 0.0})
    def test_expired_agent_falls_back_to_local_metrics(self, _read_local):
        previous = app_module.AGENT_METRICS
        try:
            app_module.AGENT_METRICS = valid_agent_metrics(timestamp=time.time() - 21).model_dump()
            stats = app_module.current_system_stats()
        finally:
            app_module.AGENT_METRICS = previous
        self.assertEqual(stats["source"], "local")
        self.assertFalse(stats["agent_connected"])
        self.assertTrue(stats["agent_configured"])

    @patch.dict(app_module.os.environ, {"ATLAS_AGENT_TOKEN": "test-secret"})
    def test_agent_endpoint_accepts_token_without_a_web_session(self):
        previous = app_module.AGENT_METRICS
        try:
            app_module.AGENT_METRICS = None
            response = TestClient(app_module.app).post(
                "/api/system/agent",
                json=valid_agent_metrics().model_dump(),
                headers={"Authorization": "Bearer test-secret"},
            )
        finally:
            app_module.AGENT_METRICS = previous
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
