"""
Throughput probe. Run it against a deployed stack to see where it saturates:

    python3 benchmark.py                          # localhost:6010
    python3 benchmark.py https://translate.example.com

It ramps concurrency and reports requests/second and latency at each step.
The number to watch is the point where throughput stops rising while the
median keeps climbing — that's the ceiling, and every caller past it is just
queueing. Sizing from it is a division: 3 req/s serves ~180 translations a
minute no matter how many people are logged in.

Texts are made unique per request so nothing measured here is a repeat.
"""

import json
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:6010").rstrip("/")

SAMPLES = [
    "Can we meet at the office tomorrow",
    "Please share the report when you get a chance",
    "I'll call you back in ten minutes",
    "The meeting got postponed to next week",
    "Did you get my message about the schedule",
]


def request(index):
    body = {
        "text": f"{SAMPLES[index % len(SAMPLES)]} ({index})",
        "source": "auto",
        "target": "hi",
    }
    call = urllib.request.Request(
        f"{BASE_URL}/translate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    try:
        urllib.request.urlopen(call, timeout=120).read()
        return time.time() - started, True
    except Exception:
        return time.time() - started, False


def measure(concurrency, count, offset):
    started = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(request, range(offset, offset + count)))
    wall = time.time() - started
    latencies = sorted(latency for latency, _ in results)
    succeeded = sum(ok for _, ok in results)
    print(
        f"  concurrency={concurrency:<3} n={count:<3} ok={succeeded}/{count}  "
        f"{count / wall:5.2f} req/s  "
        f"median={statistics.median(latencies):5.2f}s  "
        f"p95={latencies[int(len(latencies) * 0.95) - 1]:5.2f}s"
    )


if __name__ == "__main__":
    print(f"warming up {BASE_URL} ...")
    for i in range(2):
        request(900 + i)

    print("ramping concurrency:")
    offset = 0
    for concurrency, count in ((1, 8), (4, 16), (8, 24), (16, 48)):
        measure(concurrency, count, offset)
        offset += 1000
