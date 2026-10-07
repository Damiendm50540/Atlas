#!/usr/bin/env python3
"""Lightweight Linux agent that sends Raspberry Pi metrics to the ATLAS backend."""

import hashlib
import hmac
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

INTERVAL_SECONDS = 5
PROC_STAT = Path("/proc/stat")
PROC_MEMINFO = Path("/proc/meminfo")
THERMAL_DIR = Path("/sys/class/thermal")


def parse_cpu_counters(line):
    fields = line.split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("Ligne CPU /proc/stat invalide")
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values[:8]), idle


class CpuSampler:
    def __init__(self):
        self.previous = None

    def sample(self):
        with PROC_STAT.open(encoding="ascii") as proc_stat:
            total, idle = parse_cpu_counters(proc_stat.readline())
        current = total, idle
        previous, self.previous = self.previous, current
        if previous is None:
            return None
        total_delta = total - previous[0]
        idle_delta = idle - previous[1]
        if total_delta <= 0:
            return None
        return round(max(0.0, min(100.0, 100 * (total_delta - idle_delta) / total_delta)), 1)


def read_memory():
    values = {}
    with PROC_MEMINFO.open(encoding="ascii") as meminfo:
        for line in meminfo:
            key, value = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(value.split()[0]) * 1024
    total = values["MemTotal"]
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)
    return used, total, round(used * 100 / total, 1)


def read_temperature():
    preferred = []
    other = []
    for temp_path in sorted(THERMAL_DIR.glob("thermal_zone*/temp")):
        try:
            value = float(temp_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
        temperature = value / 1000 if abs(value) >= 1000 else value
        try:
            label = (temp_path.parent / "type").read_text(encoding="ascii").strip().lower()
        except OSError:
            label = ""
        (preferred if any(word in label for word in ("cpu", "core", "soc", "thermal")) else other).append(
            temperature
        )
    readings = preferred or other
    return round(readings[0], 1) if readings else None


def collect_metrics(cpu_sampler):
    memory_used, memory_total, memory_percent = read_memory()
    disk = shutil.disk_usage("/")
    return {
        "hostname": socket.gethostname()[:63] or "raspberry-pi",
        "cpu_percent": cpu_sampler.sample(),
        "memory_percent": memory_percent,
        "memory_used": memory_used,
        "memory_total": memory_total,
        "temperature_c": read_temperature(),
        "disk_percent": round(disk.used * 100 / disk.total, 1),
        "disk_used": disk.used,
        "disk_total": disk.total,
        "timestamp": time.time(),
    }


def signature_headers(token, payload, request_timestamp):
    signed_message = request_timestamp.encode("ascii") + b"\n" + payload
    signature = hmac.new(
        token.encode("utf-8"), signed_message, hashlib.sha256
    ).hexdigest()
    return {
        "X-Atlas-Timestamp": request_timestamp,
        "X-Atlas-Signature": signature,
        "Content-Type": "application/json",
    }


def main():
    backend_url = os.environ.get("ATLAS_URL", "").strip().rstrip("/")
    token = os.environ.get("ATLAS_AGENT_TOKEN", "").strip()
    if not backend_url or not token:
        print("ATLAS_URL et ATLAS_AGENT_TOKEN doivent être configurés.", file=sys.stderr)
        return 2

    endpoint = f"{backend_url}/api/system/agent"
    cpu_sampler = CpuSampler()
    last_error_log = 0.0
    while True:
        try:
            payload = json.dumps(collect_metrics(cpu_sampler)).encode("utf-8")
            request_timestamp = str(int(time.time()))
            request = Request(
                endpoint,
                data=payload,
                method="POST",
                headers=signature_headers(token, payload, request_timestamp),
            )
            with urlopen(request, timeout=4) as response:
                if response.status != 200:
                    raise RuntimeError(f"Le serveur ATLAS a répondu HTTP {response.status}")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as error:
            now = time.monotonic()
            if now - last_error_log >= 60:
                print(f"Envoi des métriques à ATLAS impossible : {error}", file=sys.stderr, flush=True)
                last_error_log = now
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
