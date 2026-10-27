#!/usr/bin/env python3
"""Summarise and compare MangoHud benchmark logs.

    scripts/bench-mangohud.py summarize LOG.csv [LOG.csv ...]
    scripts/bench-mangohud.py compare BASELINE_DIR CANDIDATE_DIR [--markdown]

A log is the CSV MangoHud writes with ``autostart_log`` / ``log_duration`` / ``output_folder``
(see docs/benchmarks/README.md). The first line that has ``fps`` and ``frametime`` columns is the
header; the rows after it are the frames. Lines before it (system information) are ignored.

For ``compare``, each directory holds one sub-directory per game with one CSV per run:

    results/stock/cyberpunk/run1.csv  run2.csv  run3.csv
    results/axon/cyberpunk/run1.csv   ...

Numbers per game are the median of the runs. "1% low" is the average frame rate of the slowest
1% of frames (the stricter of the common definitions). A game with fewer than 3 runs, or whose
runs differ by more than 5%, is flagged: the comparison is not trustworthy.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_RUNS = 3
NOISY = 0.05  # runs of one game differing by more than this are flagged


@dataclass(frozen=True)
class Run:
    path: Path
    frames: int
    avg_fps: float
    low_1pct_fps: float


class LogError(ValueError):
    """The file is not a usable MangoHud log."""


def read_frametimes(path: Path) -> list[float]:
    """Frame times in milliseconds from one MangoHud CSV log."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise LogError(f"{path}: {exc}") from exc
    rows = list(csv.reader(text.splitlines()))
    header = next(
        (
            (i, [c.strip().lower() for c in row])
            for i, row in enumerate(rows)
            if "fps" in [c.strip().lower() for c in row]
            and "frametime" in [c.strip().lower() for c in row]
        ),
        None,
    )
    if header is None:
        raise LogError(f"{path}: no 'fps,frametime' header; is this a MangoHud log?")
    index, names = header
    column = names.index("frametime")
    frametimes = []
    for row in rows[index + 1 :]:
        try:
            value = float(row[column])
        except (ValueError, IndexError):
            continue
        if value > 0:
            frametimes.append(value)
    if not frametimes:
        raise LogError(f"{path}: no frames")
    return frametimes


def summarize_frametimes(frametimes: list[float]) -> tuple[float, float]:
    """(average fps, 1% low fps) for a list of frame times in milliseconds."""
    avg = 1000.0 * len(frametimes) / sum(frametimes)
    slowest = sorted(frametimes, reverse=True)[: max(1, len(frametimes) // 100)]
    low = 1000.0 * len(slowest) / sum(slowest)
    return avg, low


def summarize(path: Path) -> Run:
    frametimes = read_frametimes(path)
    avg, low = summarize_frametimes(frametimes)
    return Run(path, len(frametimes), avg, low)


@dataclass(frozen=True)
class GameResult:
    game: str
    runs: int
    avg_fps: float
    low_1pct_fps: float
    spread: float  # (max - min) / median of the average fps across runs
    warnings: tuple[str, ...]


def game_result(game: str, runs: list[Run]) -> GameResult:
    avgs = [r.avg_fps for r in runs]
    med = statistics.median(avgs)
    spread = (max(avgs) - min(avgs)) / med if med else 0.0
    warnings = []
    if len(runs) < MIN_RUNS:
        warnings.append(f"only {len(runs)} run(s), need {MIN_RUNS}")
    if spread > NOISY:
        warnings.append(f"runs differ by {spread:.0%}")
    return GameResult(
        game,
        len(runs),
        med,
        statistics.median(r.low_1pct_fps for r in runs),
        spread,
        tuple(warnings),
    )


def load_config(directory: Path) -> dict[str, GameResult]:
    """Results per game from ``directory/<game>/*.csv``."""
    results = {}
    for game_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        logs = sorted(game_dir.glob("*.csv"))
        if logs:
            results[game_dir.name] = game_result(game_dir.name, [summarize(p) for p in logs])
    if not results:
        raise LogError(f"{directory}: no <game>/*.csv logs found")
    return results


def change(old: float, new: float) -> float:
    return (new - old) / old if old else 0.0


def compare_table(base: dict[str, GameResult], cand: dict[str, GameResult], markdown: bool) -> str:
    lines = []
    head = [
        "Game",
        "Avg FPS before",
        "after",
        "change",
        "1% low before",
        "after",
        "change",
        "Notes",
    ]
    if markdown:
        lines += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    else:
        lines.append("  ".join(head))
    for game in sorted(set(base) | set(cand)):
        b, c = base.get(game), cand.get(game)
        if b is None or c is None:
            cells = [game, "-", "-", "-", "-", "-", "-", "missing from one side"]
        else:
            notes = "; ".join(
                [*(f"before: {w}" for w in b.warnings), *(f"after: {w}" for w in c.warnings)]
            )
            cells = [
                game,
                f"{b.avg_fps:.1f}",
                f"{c.avg_fps:.1f}",
                f"{change(b.avg_fps, c.avg_fps):+.1%}",
                f"{b.low_1pct_fps:.1f}",
                f"{c.low_1pct_fps:.1f}",
                f"{change(b.low_1pct_fps, c.low_1pct_fps):+.1%}",
                notes or "ok",
            ]
        lines.append(("| " + " | ".join(cells) + " |") if markdown else "  ".join(cells))
    return "\n".join(lines)


def say(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("summarize", help="average and 1% low of each log")
    one.add_argument("logs", nargs="+", type=Path)
    two = sub.add_parser("compare", help="compare two result folders")
    two.add_argument("baseline", type=Path)
    two.add_argument("candidate", type=Path)
    two.add_argument("--markdown", action="store_true", help="print a Markdown table")
    args = parser.parse_args(argv)
    try:
        if args.command == "summarize":
            for path in args.logs:
                run = summarize(path)
                say(
                    f"{path}: {run.frames} frames, avg {run.avg_fps:.1f} fps, 1% low {run.low_1pct_fps:.1f} fps"
                )
        else:
            say(
                compare_table(
                    load_config(args.baseline), load_config(args.candidate), args.markdown
                )
            )
    except LogError as exc:
        sys.stderr.write(f"bench-mangohud: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
