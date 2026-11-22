#!/usr/bin/env python3
r"""批量运行判定环检查器，并把结果镜像到输出目录。

示例：
    python batch_check.py "E:\RizChartTool\officalcharts" ^
        "E:\RizChartTool\officalcharts-result" --stationary-line

源文件 ``<song>/Chart_IN`` 会生成：
    <output>/<song>/Chart_IN-result.txt
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ring_checker as rc  # noqa: E402
import rizChartFunction as rcf  # noqa: E402


def is_chart_file(path: Path) -> bool:
    """识别官方仓库中的谱面文件。

    常见名称（可带或不带 .json）：
    - Chart_EZ / Chart_HD / Chart_IN / Chart_AT / Chart_SP
    - mr_Chart_EZ（部分曲目的另一套谱面）
    - challenge/1001_Chart
    """
    name = path.name.lower()
    if path.suffix.lower() == ".json":
        return (
            "chart_" in name
            or name.endswith("_chart.json")
            or name.startswith("chart")
        )
    if path.suffix:
        return False
    return "chart_" in name or name.endswith("_chart") or name.startswith("chart")


def discover_charts(source: Path) -> list[Path]:
    if source.is_file():
        return [source]

    def is_auto_save(path: Path) -> bool:
        if "auto-save" in path.name.lower():
            return True
        return any(
            part.lower() == "rizline editor auto-save"
            for part in path.parts
        )

    charts = [
        path
        for path in source.rglob("*")
        if path.is_file() and is_chart_file(path) and not is_auto_save(path)
    ]
    charts.sort(key=lambda path: str(path.relative_to(source)).casefold())
    return charts


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-run the Rizline judge-ring checker over a chart tree."
    )
    parser.add_argument("source", type=Path, help="Chart file or source directory.")
    parser.add_argument("output", type=Path, help="Result directory.")
    parser.add_argument("--window", "--window-after", dest="window_after", type=float, default=0.1)
    parser.add_argument("--window-before", type=float, default=0.0)
    parser.add_argument("--min-alpha", type=float, default=1.0)
    parser.add_argument("--x-tolerance", type=float, default=1e-2)
    parser.add_argument("--y-tolerance", type=float, default=1e-2)
    parser.add_argument("--background-tolerance", type=float, default=2.0)
    parser.add_argument("--stationary-line", action="store_true")
    parser.add_argument("--stationary-window", type=float, default=0.1)
    parser.add_argument("--stationary-tolerance", type=float, default=1e-3)
    parser.add_argument("--stationary-speed-tolerance", type=float, default=1e-2)
    parser.add_argument("--stationary-min-alpha", type=float, default=1.0)
    parser.add_argument("--range-search", action="store_true")
    parser.add_argument("--range-x", type=float, default=5e-2)
    parser.add_argument("--range-y", type=float, default=1e-1)
    parser.add_argument("--range-samples", type=int, default=4)
    parser.add_argument("--range-min-samples", type=int, default=4)
    parser.add_argument("--accept-flash", action="store_true")
    parser.add_argument("--bpm", "--base-bpm", dest="base_bpm", type=float, default=None)
    return parser.parse_args(argv)


def validate(args: argparse.Namespace) -> str | None:
    numeric = [
        args.window_after,
        args.window_before,
        args.x_tolerance,
        args.y_tolerance,
        args.background_tolerance,
        args.range_x,
        args.range_y,
        args.stationary_window,
        args.stationary_tolerance,
        args.stationary_speed_tolerance,
        args.stationary_min_alpha,
    ]
    if any(not math.isfinite(value) for value in numeric):
        return "all numeric options must be finite"
    if args.window_after < 0.0 or args.window_before < 0.0:
        return "search windows must be non-negative"
    if args.x_tolerance < 0.0 or args.y_tolerance < 0.0:
        return "tolerances must be non-negative"
    if not (0.0 <= args.background_tolerance <= 255.0):
        return "--background-tolerance must be between 0 and 255"
    if args.range_x < 0.0 or args.range_y < 0.0:
        return "--range-x/--range-y must be non-negative"
    if args.range_samples < 1 or args.range_min_samples < 1:
        return "--range-samples/--range-min-samples must be positive"
    if not (0.0 <= args.min_alpha <= 255.0):
        return "--min-alpha must be between 0 and 255"
    if (
        args.stationary_window < 0.0
        or args.stationary_tolerance < 0.0
        or args.stationary_speed_tolerance < 0.0
    ):
        return "stationary window/tolerances must be non-negative"
    if not (0.0 <= args.stationary_min_alpha <= 255.0):
        return "--stationary-min-alpha must be between 0 and 255"
    if args.base_bpm is not None and (
        not math.isfinite(args.base_bpm) or args.base_bpm <= 0.0
    ):
        return "--bpm must be a positive number"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    error = validate(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not args.source.exists():
        print(f"error: source does not exist: {args.source}", file=sys.stderr)
        return 2

    options = rc.Options(
        window_after=args.window_after,
        window_before=args.window_before,
        min_alpha=args.min_alpha,
        accept_flash=args.accept_flash,
        base_bpm=args.base_bpm,
        x_tolerance=args.x_tolerance,
        y_tolerance=args.y_tolerance,
        background_tolerance=args.background_tolerance,
        stationary_line=args.stationary_line,
        stationary_window=args.stationary_window,
        stationary_tolerance=args.stationary_tolerance,
        stationary_speed_tolerance=args.stationary_speed_tolerance,
        stationary_min_alpha=args.stationary_min_alpha,
        range_search=args.range_search,
        range_x=args.range_x,
        range_y=args.range_y,
        range_samples=args.range_samples,
        range_min_samples=args.range_min_samples,
    )

    charts = discover_charts(args.source)
    if not charts:
        print("error: no chart files found", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"charts: {len(charts)}")
    print(f"source: {args.source}")
    print(f"output: {args.output}")
    print(
        "settings: "
        f"stationary_line={options.stationary_line}, "
        f"window_after={options.window_after}, "
        f"window_before={options.window_before}, "
        f"min_alpha={options.min_alpha}, "
        f"tolerance=({options.x_tolerance}, {options.y_tolerance}), "
        f"background_tolerance={options.background_tolerance}, "
        f"stationary=({options.stationary_window}, "
        f"{options.stationary_tolerance}, {options.stationary_speed_tolerance}, "
        f"{options.stationary_min_alpha}), "
        f"range=({options.range_search}, {options.range_x}, {options.range_y}, "
        f"{options.range_samples}, {options.range_min_samples})"
    )

    rows: list[dict[str, object]] = []
    totals = {
        "lines": 0,
        "notes": 0,
        "missing": 0,
        "flash": 0,
        "ok_same_line": 0,
        "ok_other_line": 0,
        "ok_stationary_line": 0,
        "ok_range_search": 0,
        "flash_ignored": 0,
    }
    started = time.time()
    source_root = args.source if args.source.is_dir() else args.source.parent

    for index, chart_path in enumerate(charts, 1):
        relative = chart_path.relative_to(source_root)
        result_stem = (
            relative.name[:-5]
            if relative.name.lower().endswith(".json")
            else relative.name
        )
        result_path = args.output / relative.parent / f"{result_stem}-result.txt"
        result_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            chart = rcf.load(str(chart_path), basebpm=args.base_bpm)
        except Exception as exc:  # noqa: BLE001 - keep batch running
            report = rc.ChartReport(path=relative, error=f"load exception: {exc}")
        else:
            if chart is None:
                report = rc.ChartReport(path=relative, error="rcf.load() returned None")
            else:
                try:
                    report = rc.analyze_chart(chart, relative, options)
                except Exception as exc:  # noqa: BLE001
                    report = rc.ChartReport(path=relative, error=f"analysis exception: {exc}")

        try:
            text = rc.render_text([report], options)
            result_path.write_text(text + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"write failed: {relative}: {exc}", file=sys.stderr)

        rows.append(
            {
                "chart": str(relative),
                "result": str(result_path.relative_to(args.output)),
                "lines": report.lines,
                "notes": report.notes_checked,
                "missing": report.missing_count,
                "flash": report.flash_count,
                "ok_same_line": report.ok_same_line,
                "ok_other_line": report.ok_other_line,
                "ok_stationary_line": report.ok_stationary_line,
                "ok_range_search": report.ok_range_search,
                "flash_ignored": report.flash_ignored,
                "error": report.error,
            }
        )
        if report.error is None:
            totals["lines"] += report.lines
            totals["notes"] += report.notes_checked
            totals["missing"] += report.missing_count
            totals["flash"] += report.flash_count
            totals["ok_same_line"] += report.ok_same_line
            totals["ok_other_line"] += report.ok_other_line
            totals["ok_stationary_line"] += report.ok_stationary_line
            totals["ok_range_search"] += report.ok_range_search
            totals["flash_ignored"] += report.flash_ignored

        if index % 25 == 0 or index == len(charts):
            elapsed = time.time() - started
            print(
                f"progress: {index}/{len(charts)} | "
                f"missing={totals['missing']} flash={totals['flash']} | "
                f"{elapsed:.1f}s",
                flush=True,
            )

    summary = {
        "source": str(args.source),
        "output": str(args.output),
        "settings": asdict(options),
        "totals": totals,
        "charts": rows,
    }
    (args.output / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (args.output / "_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    elapsed = time.time() - started
    print(
        f"done: {len(charts)} charts, {totals['notes']} notes, "
        f"{totals['missing']} missing, {totals['flash']} flash, "
        f"{totals['ok_stationary_line']} stationary-line covered, "
        f"{elapsed:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
