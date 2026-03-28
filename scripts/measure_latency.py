"""
LAUNCH-003 — Measure latency Railway -> Hetzner -> IBKR.

Seuils:
  < 100ms -> OK for everything
  100-200ms -> OK for FX + futures swing, borderline intraday
  > 200ms -> Migration worker to Hetzner REQUIRED

Usage:
    python scripts/measure_latency.py --host <hetzner-ip> [--count 1000]
"""
import argparse
import logging
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def measure_ping_latency(host: str, count: int = 100) -> dict:
    """Measure ping latency to a host.

    Returns:
        {host, count, avg_ms, p50_ms, p95_ms, p99_ms, min_ms, max_ms, jitter_ms, packet_loss_pct}
    """
    latencies = []
    lost = 0

    for i in range(count):
        try:
            start = time.perf_counter()
            # Use ping command (cross-platform)
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", host],
                capture_output=True, text=True, timeout=5,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            if result.returncode == 0:
                # Parse actual ping time from output
                for line in result.stdout.split("\n"):
                    if "time=" in line:
                        time_str = line.split("time=")[1].split()[0]
                        elapsed_ms = float(time_str)
                        break
                latencies.append(elapsed_ms)
            else:
                lost += 1
        except (subprocess.TimeoutExpired, Exception):
            lost += 1

        if (i + 1) % 100 == 0:
            print(f"  Progress: {i+1}/{count} pings sent...")

    if not latencies:
        return {"host": host, "error": "All pings lost", "packet_loss_pct": 100.0}

    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95)
    p99_idx = int(len(sorted_lat) * 0.99)

    return {
        "host": host,
        "count": count,
        "successful": len(latencies),
        "avg_ms": round(statistics.mean(latencies), 2),
        "p50_ms": round(statistics.median(latencies), 2),
        "p95_ms": round(sorted_lat[min(p95_idx, len(sorted_lat)-1)], 2),
        "p99_ms": round(sorted_lat[min(p99_idx, len(sorted_lat)-1)], 2),
        "min_ms": round(min(latencies), 2),
        "max_ms": round(max(latencies), 2),
        "jitter_ms": round(statistics.stdev(latencies), 2) if len(latencies) > 1 else 0,
        "packet_loss_pct": round(lost / count * 100, 2),
    }


def assess_latency(results: dict) -> dict:
    """Assess latency results against thresholds.

    Returns:
        {verdict, details, recommendation}
    """
    p95 = results.get("p95_ms", 999)

    if p95 < 100:
        return {
            "verdict": "OK",
            "details": f"P95 latency {p95}ms < 100ms threshold",
            "recommendation": "All strategies can run from Railway",
        }
    elif p95 < 200:
        return {
            "verdict": "ACCEPTABLE",
            "details": f"P95 latency {p95}ms: OK for FX swing + futures, borderline for intraday",
            "recommendation": "Monitor slippage on intraday strategies. Plan Hetzner migration for Phase 2.",
        }
    else:
        return {
            "verdict": "MIGRATION_REQUIRED",
            "details": f"P95 latency {p95}ms > 200ms threshold",
            "recommendation": "Migrate worker to Hetzner BEFORE enabling futures intraday",
        }


def main():
    parser = argparse.ArgumentParser(description="Measure latency to Hetzner/IBKR")
    parser.add_argument("--host", required=True, help="Target host IP or hostname")
    parser.add_argument("--count", type=int, default=100, help="Number of pings (default: 100)")
    parser.add_argument("--output", type=str, help="Save report to file")
    args = parser.parse_args()

    print(f"Measuring latency to {args.host} ({args.count} pings)...")
    print()

    results = measure_ping_latency(args.host, args.count)

    if "error" in results:
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    assessment = assess_latency(results)

    # Print report
    print("=" * 50)
    print(f"LATENCY REPORT — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)
    print(f"Host:         {results['host']}")
    print(f"Pings:        {results['successful']}/{results['count']}")
    print(f"Packet loss:  {results['packet_loss_pct']}%")
    print(f"Average:      {results['avg_ms']}ms")
    print(f"Median (P50): {results['p50_ms']}ms")
    print(f"P95:          {results['p95_ms']}ms")
    print(f"P99:          {results['p99_ms']}ms")
    print(f"Min/Max:      {results['min_ms']}ms / {results['max_ms']}ms")
    print(f"Jitter:       {results['jitter_ms']}ms")
    print()
    print(f"VERDICT:      {assessment['verdict']}")
    print(f"Details:      {assessment['details']}")
    print(f"Action:       {assessment['recommendation']}")

    if args.output:
        import json
        report = {**results, **assessment, "timestamp": datetime.now(timezone.utc).isoformat()}
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport saved to {args.output}")


if __name__ == "__main__":
    main()
