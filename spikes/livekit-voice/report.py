"""Summarise runs/latency.jsonl: p50/p95 per stage, grouped by client label and LLM.

uv run report.py
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

STAGES = ("e2e", "transcription_delay", "end_of_turn_delay", "llm_ttft", "tts_ttfb")


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


def main() -> None:
    path = Path(__file__).parent / "runs" / "latency.jsonl"
    if not path.exists():
        raise SystemExit(f"no data yet: {path}")

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for line in path.read_text().splitlines():
        t = json.loads(line)
        if t.get("interrupted"):
            continue
        key = (
            t.get("dispatch", {}).get("client", "unknown"),
            t.get("llm_model") or "echo",
            t.get("tts_model") or "?",
        )
        groups[key].append(t)

    for (client, model, tts), turns in sorted(groups.items()):
        print(f"\n{client} · {model} · {tts} · {len(turns)} turns")
        print(f"  {'stage':<20}{'p50 ms':>8}{'p95 ms':>8}{'mean ms':>9}")
        for stage in STAGES:
            xs = [t[stage] for t in turns if t.get(stage) is not None]
            if xs:
                p50, p95, mean = (v * 1000 for v in (pct(xs, 0.5), pct(xs, 0.95), statistics.mean(xs)))
                print(f"  {stage:<20}{p50:>8.0f}{p95:>8.0f}{mean:>9.0f}")


if __name__ == "__main__":
    main()
