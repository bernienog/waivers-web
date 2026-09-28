"""Paced HTTP for Sleeper: min gap + jitter on EVERY outbound call.

Public reads are anonymous but shared infra — burst-parallel fetches
(/api/leagues x4 leagues) look like a hammer. All traffic in
sleeper_public / sleeper_private goes through here so we stay polite
by construction. Slower is fine; correctness first.
"""

import random
import threading
import time

import requests

MIN_GAP = 0.15  # seconds between calls, before jitter
JITTER = 0.25  # extra random 0..JITTER per call

_lock = threading.Lock()
_last = [0.0]

# Contador de llamadas reales a Sleeper (para auditoría perf, sin
# comportamiento). Se expone en /api/health. Solo crece, jamás bloquea.
_COUNTS = {"get": 0, "post": 0}


def get_counts():
    with _lock:
        return dict(_COUNTS)


def _wait():
    with _lock:
        gap = MIN_GAP + random.uniform(0, JITTER) - (time.monotonic() - _last[0])
        if gap > 0:
            time.sleep(gap)
        _last[0] = time.monotonic()


def sleeper_get(url, **kw):
    _wait()
    with _lock:
        _COUNTS["get"] += 1
    return requests.get(url, **kw)


def sleeper_post(url, **kw):
    _wait()
    with _lock:
        _COUNTS["post"] += 1
    return requests.post(url, **kw)
