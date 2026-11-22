#!/usr/bin/env python3
"""Rizline 判定环检查器。

读取一个或多个 Rizline 谱面，遍历所有 Note（Hold 只检查头部），
在 Note 击打时刻寻找可见判定环。击打时物体都落在判定线上，跨线覆盖
按包含画布偏移的绝对 X 匹配；判定环可以来自 Note 自己所在的线，也可以
来自同一时刻经过同一绝对 X 的其他线。可选地，穿过 Note 的静止引导线
也可以当作判定环（--stationary-line）。
默认还会向后搜索一小段时间，用于识别"闪现环"。

判定规则：
- Note 自己线的环可见：正常。
- 其他线在 Note 的绝对 (X, Y) 处且环可见：正常。
- Note 时间点不可见，但搜索窗口内出现过可见环：只有"闪现环"，
  仍视为问题（除非指定 --accept-flash）。
- Note 时间点及搜索窗口内都不可见：完全没有判定环，视为问题。

用法见 README.md 或 `python ring_checker.py --help`。
"""

from __future__ import annotations

import argparse
import bisect
import copy
import csv
import glob
import io
import json
import math
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 导入 rizChartFunction（优先使用已安装包，其次使用同级的 pytool/src）
# ---------------------------------------------------------------------------

def _load_library():
    try:
        import rizChartFunction as rcf  # type: ignore
        return rcf
    except ImportError:
        pass

    here = Path(__file__).resolve()
    candidates: list[Path] = []
    env_src = os.environ.get("RIZCHARTFUNCTION_SRC")
    if env_src:
        candidates.append(Path(env_src))
    candidates.extend(
        [
            here.parent.parent / "rizChartFunction_Library" / "src",
            here.parent / "rizChartFunction_Library" / "src",
            Path.cwd() / "rizChartFunction_Library" / "src",
            here.parent.parent / "pytool" / "src",
            here.parent / "pytool" / "src",
            Path.cwd() / "pytool" / "src",
        ]
    )

    for candidate in candidates:
        if (candidate / "rizChartFunction").is_dir():
            sys.path.insert(0, str(candidate))
            import rizChartFunction as rcf  # type: ignore
            return rcf

    raise SystemExit(
        "Cannot import rizChartFunction.\n"
        "Install the library, or set RIZCHARTFUNCTION_SRC to its src directory."
    )


rcf = _load_library()


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

NOTE_TYPES = {0: "tap", 1: "drag", 2: "hold"}


@dataclass
class RingTimeline:
    """一条线的 judgeRingColor 时间轴。

    颜色节点的时间轴：
    - 第一个节点之前：保持第一个节点的 startColor；
    - 正好命中节点：使用最右侧节点的 startColor；
    - 两个节点之间：从左节点的 startColor 线性过渡到 endColor；
    - 最后一个节点到线结束：继续从 startColor 过渡到 endColor；
    - 线结束之后：保持 endColor。
    """

    nodes: Sequence[Mapping[str, Any]]
    line_end: float | None = None

    def __post_init__(self) -> None:
        # stable sort：同一时间的多个节点保持原顺序，最右侧仍然最后。
        ordered = sorted(self.nodes, key=lambda node: float(node["time"]))
        self.nodes = ordered
        self.times = [float(node["time"]) for node in ordered]
        self.start_colors = [
            _rgba_list(node.get("startColor")) or [0, 0, 0, 0]
            for node in ordered
        ]
        self.end_colors = [
            _rgba_list(node.get("endColor")) or [0, 0, 0, 0]
            for node in ordered
        ]
        self.start_alpha = [float(color[3]) for color in self.start_colors]
        self.end_alpha = [float(color[3]) for color in self.end_colors]

    @property
    def empty(self) -> bool:
        return not self.times

    def alpha_at(self, tick: float) -> float:
        """返回 tick 时刻的判定环 alpha（0-255）。"""
        if self.empty:
            return 0.0

        position = bisect.bisect_right(self.times, tick) - 1
        if position < 0:
            return self.start_alpha[0]
        if self.times[position] == tick:
            return self.start_alpha[position]
        if position == len(self.times) - 1:
            last_time = self.times[position]
            if (
                self.line_end is not None
                and self.line_end > last_time
                and tick > last_time
            ):
                if tick >= self.line_end:
                    return self.end_alpha[position]
                ratio = (tick - last_time) / (self.line_end - last_time)
                return _lerp(
                    self.start_alpha[position],
                    self.end_alpha[position],
                    ratio,
                )
            return self.start_alpha[position]

        start_time = self.times[position]
        end_time = self.times[position + 1]
        if end_time <= start_time:
            return self.start_alpha[position]

        ratio = (tick - start_time) / (end_time - start_time)
        return _lerp(self.start_alpha[position], self.end_alpha[position], ratio)

    def color_at(self, tick: float) -> list[int] | None:
        """返回 tick 时刻的判定环 RGBA（与 getRingColor() 的规则一致）。"""
        if self.empty:
            return None
        position = bisect.bisect_right(self.times, tick) - 1
        if position < 0:
            return list(self.start_colors[0])
        if self.times[position] == tick or position == len(self.times) - 1:
            return list(self.start_colors[position])
        start_time = self.times[position]
        end_time = self.times[position + 1]
        if end_time <= start_time:
            return list(self.start_colors[position])
        ratio = (tick - start_time) / (end_time - start_time)
        return _lerp_rgba(
            self.start_colors[position],
            self.end_colors[position],
            ratio,
        )

    def _segment_alpha(self, index: int, tick: float) -> float:
        start_time = self.times[index]
        end_time = self.times[index + 1]
        if end_time <= start_time:
            return self.start_alpha[index]
        ratio = (tick - start_time) / (end_time - start_time)
        ratio = min(1.0, max(0.0, ratio))
        return _lerp(self.start_alpha[index], self.end_alpha[index], ratio)

    def visible_in_interval(self, start: float, end: float, min_alpha: float) -> bool:
        """判断闭区间 [start, end] 内是否出现过 alpha >= min_alpha。

        会检查节点时刻的离散跳变，以及每个线性过渡段在区间内的最大值；
        因此即使只有一个很短的"闪现"，也能检测到。
        """
        if self.empty:
            return False
        if end < start:
            start, end = end, start

        # Note 时间点本身。
        if self.alpha_at(start) >= min_alpha:
            return True
        if end <= start:
            return False

        times = self.times

        # 第一个节点之前是常量。
        if start < times[0]:
            if self.start_alpha[0] >= min_alpha:
                return True

        # 区间内（含 end）的节点时刻：使用最右侧节点的 startColor。
        left_node = bisect.bisect_right(times, start)
        right_node = bisect.bisect_right(times, end)
        for index in range(left_node, right_node):
            if self.alpha_at(times[index]) >= min_alpha:
                return True

        # 与区间相交的每个过渡段：线性 alpha，最大值一定在两端。
        first_segment = max(0, bisect.bisect_right(times, start) - 1)
        stop_segment = min(len(times) - 1, bisect.bisect_left(times, end))
        for index in range(first_segment, stop_segment):
            start_time = times[index]
            end_time = times[index + 1]
            if end_time <= start_time:
                continue
            if end_time < start or start_time > end:
                continue
            lo = max(start, start_time)
            hi = min(end, end_time)
            if hi < lo:
                continue
            # hi 等于段右端时取的是 endColor 的极限，可捕获节点前的短暂闪现。
            if self._segment_alpha(index, lo) >= min_alpha:
                return True
            if self._segment_alpha(index, hi) >= min_alpha:
                return True

        # 最后一个节点到线结束：继续按 startColor -> endColor 过渡。
        last_time = times[-1]
        if self.line_end is not None and self.line_end > last_time:
            if end > last_time:
                lo = max(start, last_time)
                hi = min(end, self.line_end)
                if hi > lo:
                    span = self.line_end - last_time
                    alpha_lo = _lerp(
                        self.start_alpha[-1],
                        self.end_alpha[-1],
                        (lo - last_time) / span,
                    )
                    alpha_hi = _lerp(
                        self.start_alpha[-1],
                        self.end_alpha[-1],
                        (hi - last_time) / span,
                    )
                    if alpha_lo >= min_alpha or alpha_hi >= min_alpha:
                        return True
                if start <= self.line_end <= end:
                    if self.end_alpha[-1] >= min_alpha:
                        return True
                if end > self.line_end and self.end_alpha[-1] >= min_alpha:
                    return True
        else:
            # 没有可用的线结束时间时，最后一个节点之后保持 startColor。
            if end > last_time and self.start_alpha[-1] >= min_alpha:
                return True

        return False


@dataclass
class LineInfo:
    """一条线的空间与判定环信息。"""

    index: int
    start_time: float
    end_time: float
    point_times: list[float]
    point_canvases: list[int]
    point_floors: list[float | None]
    point_xs: list[float]
    point_colors: list[list[int]]
    point_alphas: list[float]
    canvas_x_ranges: dict[int, tuple[float, float]]
    canvas_floor_ranges: dict[int, tuple[float, float]]
    max_alpha: float
    ring: RingTimeline
    notes: Sequence[Mapping[str, Any]]
    full_notes: Sequence[Mapping[str, Any]]

    def is_active(self, tick: float) -> bool:
        return self.start_time <= tick <= self.end_time

    def canvas_at(self, tick: float) -> int | None:
        if not self.point_times:
            return None
        if tick < self.point_times[0] or tick > self.point_times[-1]:
            return None
        position = bisect.bisect_right(self.point_times, tick) - 1
        if position < 0:
            position = 0
        if position >= len(self.point_times):
            position = len(self.point_times) - 1
        return self.point_canvases[position]

    def point_floor_at(self, tick: float) -> float | None:
        """线节点 floorPosition 的后备插值，仅在画布高度不可用时使用。"""
        if not self.point_times:
            return None
        position = bisect.bisect_right(self.point_times, tick) - 1
        if position < 0:
            position = 0
        if position >= len(self.point_times):
            position = len(self.point_times) - 1
        if self.point_times[position] == tick:
            return self.point_floors[position]
        if position >= len(self.point_times) - 1:
            return self.point_floors[position]
        start_floor = self.point_floors[position]
        end_floor = self.point_floors[position + 1]
        if start_floor is None or end_floor is None:
            return start_floor if start_floor is not None else end_floor
        span = self.point_times[position + 1] - self.point_times[position]
        if span <= 0.0:
            return start_floor
        ratio = (tick - self.point_times[position]) / span
        return start_floor + (end_floor - start_floor) * ratio


@dataclass
class NoteIssue:
    tick: float
    seconds: float | None
    line_index: int
    note_index: int
    note_type: str
    reason: str  # "missing" / "flash_after" / "flash_before" / "flash_both"
    alpha_at_note: float
    ring_line: int | None = None
    note_x: float | None = None
    note_y: float | None = None

    @property
    def reason_text(self) -> str:
        return {
            "missing": "missing ring",
            "flash_after": "flash only (after)",
            "flash_before": "flash only (before)",
            "flash_both": "flash only (both)",
        }.get(self.reason, self.reason)


@dataclass
class ChartReport:
    path: Path
    lines: int = 0
    notes_checked: int = 0
    issues: list[NoteIssue] = field(default_factory=list)
    flash_ignored: int = 0
    ok_same_line: int = 0
    ok_other_line: int = 0
    ok_stationary_line: int = 0
    ok_range_search: int = 0
    timeline_note: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def missing_count(self) -> int:
        return sum(1 for issue in self.issues if issue.reason == "missing")

    @property
    def flash_count(self) -> int:
        return len(self.issues) - self.missing_count

    @property
    def ok_count(self) -> int:
        return self.notes_checked - len(self.issues) - self.flash_ignored


@dataclass
class Options:
    window_after: float = 0.1
    window_before: float = 0.0
    min_alpha: float = 1.0
    accept_flash: bool = False
    base_bpm: float | None = None
    x_tolerance: float = 1e-2
    y_tolerance: float = 1e-2
    background_tolerance: float = 2.0
    stationary_line: bool = False
    stationary_window: float = 0.1
    stationary_tolerance: float = 1e-3
    stationary_speed_tolerance: float = 1e-2
    stationary_min_alpha: float = 1.0
    range_search: bool = False
    range_x: float = 5e-2
    range_y: float = 1e-1
    range_samples: int = 4
    range_min_samples: int = 4


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

class BackgroundTimeline:
    """谱面主题背景色时间轴（主题的第一个颜色）。"""

    def __init__(self, chart: dict) -> None:
        self.normal: list[int] | None = None
        self.intervals: list[tuple[float, float, int, list[int]]] = []

        themes = rcf.getTheme(chart, 0, -1)
        challenges = rcf.getChallengeTime(chart, 0, -1)
        if not isinstance(themes, list):
            raw_themes = chart.get("themes")
            themes = raw_themes if isinstance(raw_themes, list) else []
        if not isinstance(challenges, list):
            raw_challenges = chart.get("challengeTimes")
            challenges = raw_challenges if isinstance(raw_challenges, list) else []

        if themes:
            first = themes[0]
            colors = first.get("colorsList") if isinstance(first, Mapping) else None
            if isinstance(colors, list) and colors:
                self.normal = _rgba_list(colors[0])

        for index, challenge in enumerate(challenges):
            theme_index = index + 1
            if theme_index >= len(themes):
                break
            theme = themes[theme_index]
            colors = theme.get("colorsList") if isinstance(theme, Mapping) else None
            if not isinstance(colors, list) or not colors:
                continue
            color = _rgba_list(colors[0])
            if color is None:
                continue
            try:
                start = float(challenge["start"])
                end = float(challenge["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end):
                continue
            if end < start:
                continue
            self.intervals.append((start, end, index, color))

    def color_at(self, tick: float) -> list[int] | None:
        best_key: tuple[float, int] | None = None
        best_color: list[int] | None = None
        for start, end, index, color in self.intervals:
            if start <= tick <= end:
                key = (start, index)
                if best_key is None or key > best_key:
                    best_key = key
                    best_color = color
        return best_color if best_color is not None else self.normal


def _ring_color_at(
    chart: dict,
    info: LineInfo,
    tick: float,
) -> list[int] | None:
    if tick > 0.0:
        color = rcf.getRingColor(chart, info.index, tick)
        rgba = _rgba_list(color) if color is not None else None
        if rgba is not None:
            return rgba
    return info.ring.color_at(tick)


def _ring_color_visible(
    color: Sequence[int] | None,
    tick: float,
    background: BackgroundTimeline,
    options: Options,
) -> bool:
    if color is None or color[3] < options.min_alpha:
        return False
    background_color = background.color_at(tick)
    if background_color is not None and _rgb_matches(
        color,
        background_color,
        options.background_tolerance,
    ):
        return False
    return True


def _ring_visible_in_interval(
    info: LineInfo,
    start: float,
    end: float,
    background: BackgroundTimeline,
    options: Options,
) -> bool:
    """窗口内是否存在非透明且非背景色的判定环颜色。"""
    if end < start:
        start, end = end, start
    boundaries = {start, end}
    for node_time in info.ring.times:
        if start < node_time < end:
            boundaries.add(node_time)
    points = sorted(boundaries)
    samples: list[float] = []
    for index in range(len(points)):
        samples.append(points[index])
        if index + 1 < len(points):
            left = points[index]
            right = points[index + 1]
            for ratio in (0.25, 0.5, 0.75):
                samples.append(left + (right - left) * ratio)
    for sample in samples:
        color = info.ring.color_at(sample)
        if _ring_color_visible(color, sample, background, options):
            return True
    return False


class CanvasFrame:
    """某个 tick 下各画布的缓存值，避免逐段重复调用查询函数。"""

    def __init__(self, chart: dict, tick: float) -> None:
        self.chart = chart
        self.tick = tick
        self._x: dict[int, float | None] = {}
        self._floor: dict[int, float | None] = {}
        self._speed: dict[int, float | None] = {}

    def x(self, canvas: int) -> float | None:
        if canvas not in self._x:
            self._x[canvas] = rcf.getCanvasPositionAt(
                self.chart,
                self.tick,
                index=canvas,
            )
        return self._x[canvas]

    def floor(self, canvas: int) -> float | None:
        if canvas not in self._floor:
            self._floor[canvas] = rcf.getFloorPosition(
                self.chart,
                self.tick,
                canvasindex=canvas,
            )
        return self._floor[canvas]

    def speed(self, canvas: int) -> float | None:
        if canvas not in self._speed:
            self._speed[canvas] = rcf.getCanvasSpeedAt(
                self.chart,
                canvas,
                self.tick,
            )
        return self._speed[canvas]


def _rgba_list(color: Any) -> list[int] | None:
    if isinstance(color, Mapping):
        values = [
            color.get("r", 0),
            color.get("g", 0),
            color.get("b", 0),
            color.get("a", 255),
        ]
    elif isinstance(color, (list, tuple)) and len(color) >= 4:
        values = list(color[:4])
    else:
        return None

    result: list[int] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        result.append(int(max(0.0, min(255.0, round(number)))))
    return result


def _alpha(color: Any) -> float:
    rgba = _rgba_list(color)
    return 0.0 if rgba is None else float(rgba[3])


def _lerp_rgba(
    start: Sequence[int],
    end: Sequence[int],
    ratio: float,
) -> list[int]:
    ratio = min(1.0, max(0.0, ratio))
    return [
        int(round(start[index] + (end[index] - start[index]) * ratio))
        for index in range(4)
    ]


def _rgb_matches(
    color: Sequence[int],
    background: Sequence[int],
    tolerance: float,
) -> bool:
    return all(
        abs(int(color[index]) - int(background[index])) <= tolerance
        for index in range(3)
    )


def _lerp(start: float, end: float, ratio: float) -> float:
    return start + (end - start) * ratio


def _format_clock(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "-"
    total_ms = max(0, int(round(seconds * 1000.0)))
    minutes, rem_ms = divmod(total_ms, 60_000)
    secs, millis = divmod(rem_ms, 1000)
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


class _SimpleTimeline:
    """库时间换算失败时的后备 BPM 时间轴（只读谱面字段）。"""

    def __init__(self, chart: dict) -> None:
        try:
            base_bpm = float(chart.get("bPM", 0.0))
        except (TypeError, ValueError):
            base_bpm = 0.0
        self.valid = math.isfinite(base_bpm) and base_bpm > 0.0
        self.base_bpm = base_bpm
        self.times: list[float] = []
        self.values: list[float] = []
        self.seconds: list[float] = []

        shifts = chart.get("bpmShifts")
        if isinstance(shifts, list):
            for shift in shifts:
                if not isinstance(shift, Mapping):
                    continue
                try:
                    tick = float(shift.get("time"))
                    value = float(shift.get("value"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(tick) and tick >= 0.0 and math.isfinite(value) and value > 0.0:
                    self.times.append(tick)
                    self.values.append(value)

        if not self.valid:
            return
        if not self.times:
            self.times = [0.0]
            self.values = [1.0]
        elif self.times[0] > 0.0:
            # 没有 tick 0 数据时，用第一个节点的 BPM 向前延伸。
            self.times.insert(0, 0.0)
            self.values.insert(0, self.values[0])

        self.seconds = [0.0]
        for index in range(len(self.times) - 1):
            span = self.times[index + 1] - self.times[index]
            rate = 60.0 / (self.base_bpm * self.values[index])
            self.seconds.append(self.seconds[-1] + max(0.0, span) * rate)

    def seconds_at(self, tick: float) -> float | None:
        if not self.valid:
            return None
        tick = max(0.0, float(tick))
        position = bisect.bisect_right(self.times, tick) - 1
        if position < 0:
            position = 0
        if position >= len(self.times):
            position = len(self.times) - 1
        rate = 60.0 / (self.base_bpm * self.values[position])
        return self.seconds[position] + (tick - self.times[position]) * rate

    def tick_at(self, seconds: float) -> float | None:
        if not self.valid:
            return None
        seconds = max(0.0, float(seconds))
        position = bisect.bisect_right(self.seconds, seconds) - 1
        if position < 0:
            position = 0
        if position >= len(self.times):
            position = len(self.times) - 1
        rate = 60.0 / (self.base_bpm * self.values[position])
        return self.times[position] + (seconds - self.seconds[position]) / rate


class ChartTime:
    """带缓存的时间换算；优先使用 rizChartFunction，失败时使用后备时间轴。"""

    def __init__(self, chart: dict) -> None:
        self.chart = chart
        self._seconds_cache: dict[float, float | None] = {}
        self._tick_cache: dict[float, float | None] = {}
        self._fallback = _SimpleTimeline(chart)

    def seconds(self, tick: float) -> float | None:
        key = float(tick)
        if key not in self._seconds_cache:
            value = rcf.getSecondsAt(self.chart, key)
            if value is None:
                value = self._fallback.seconds_at(key)
            self._seconds_cache[key] = None if value is None else float(value)
        return self._seconds_cache[key]

    def tick(self, seconds: float) -> float | None:
        key = float(seconds)
        if key not in self._tick_cache:
            value = rcf.getTickAt(self.chart, key)
            if value is None:
                value = self._fallback.tick_at(key)
            self._tick_cache[key] = None if value is None else float(value)
        return self._tick_cache[key]


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

def _prepare_timeline(chart: dict) -> str | None:
    """确保 BPM 时间轴从 tick 0 开始，返回提示信息或错误。"""
    shifts = rcf.getBpmShift(chart, 0, -1)
    if shifts is None:
        return "invalid BPM shift structure; using the fallback BPM timeline"
    if not shifts:
        return "no BPM shifts; using base BPM only"

    first = shifts[0]
    try:
        first_time = float(first["time"])
    except (KeyError, TypeError, ValueError):
        return "the first BPM shift has an invalid structure"

    if abs(first_time) <= 1e-12:
        return None

    value = first.get("value", 1.0)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 1.0
    if not math.isfinite(value) or value <= 0.0:
        value = 1.0

    inserted = rcf.addBpmShift(
        chart,
        value,
        0.0,
        isDelayCalculate=True,
        isMultiply=True,
    )
    if inserted is None:
        return f"first BPM shift is at tick {first_time:g}; failed to add a tick-0 shift"
    return (
        f"first BPM shift is at tick {first_time:g}; "
        "added a temporary tick-0 shift in memory"
    )


def _line_floor_at(chart: dict, info: LineInfo, tick: float) -> float | None:
    """候选线在 tick 时刻的 Y 坐标（当前画布的 floorPosition）。"""
    canvas = info.canvas_at(tick)
    if canvas is not None:
        value = rcf.getFloorPosition(chart, tick, canvasindex=canvas)
        if value is not None:
            return float(value)
    return info.point_floor_at(tick)


def _floor_stable_on_side(
    chart: dict,
    info: LineInfo,
    tick: float,
    direction: int,
    window: float,
    tolerance: float,
) -> bool:
    """向前或向后检查线高度是否近似不变。"""
    if window <= 0.0:
        return False
    if direction > 0:
        start = tick
        end = min(tick + window, info.end_time)
    else:
        start = max(tick - window, info.start_time)
        end = tick
    if end - start <= 1e-12:
        return False

    samples: list[float] = []
    for ratio in (0.0, 0.5, 1.0):
        sample_tick = start + (end - start) * ratio
        value = _line_floor_at(chart, info, sample_tick)
        if value is None:
            return False
        samples.append(value)
    return max(samples) - min(samples) <= tolerance


def _point_floor_stable_on_side(
    info: LineInfo,
    tick: float,
    direction: int,
    window: float,
    tolerance: float,
) -> bool:
    """检查线节点自身的 floorPosition 在前后短时间内是否近似不变。"""
    if window <= 0.0:
        return False
    if direction > 0:
        start = tick
        end = min(tick + window, info.end_time)
    else:
        start = max(tick - window, info.start_time)
        end = tick
    if end - start <= 1e-12:
        return False

    samples: list[float] = []
    for ratio in (0.0, 0.5, 1.0):
        sample_tick = start + (end - start) * ratio
        value = info.point_floor_at(sample_tick)
        if value is None:
            return False
        samples.append(value)
    return max(samples) - min(samples) <= tolerance


def _line_may_cover(
    info: LineInfo,
    note_x: float,
    frame: CanvasFrame,
    options: Options,
) -> bool:
    """用整条线的 X/Y 包围盒快速排除不可能覆盖 Note 的线。"""
    if info.max_alpha < options.stationary_min_alpha:
        return False

    x_low = math.inf
    x_high = -math.inf
    for canvas, (low, high) in info.canvas_x_ranges.items():
        canvas_x = frame.x(canvas)
        if canvas_x is None:
            continue
        x_low = min(x_low, low + canvas_x)
        x_high = max(x_high, high + canvas_x)
    if x_low > note_x + options.x_tolerance:
        return False
    if x_high < note_x - options.x_tolerance:
        return False

    y_low = math.inf
    y_high = -math.inf
    for canvas, (low, high) in info.canvas_floor_ranges.items():
        scroll = frame.floor(canvas)
        if scroll is None:
            continue
        y_low = min(y_low, low - scroll)
        y_high = max(y_high, high - scroll)
    if y_low > options.y_tolerance:
        return False
    if y_high < -options.y_tolerance:
        return False

    return True


def _stationary_segment_covers(
    chart: dict,
    info: LineInfo,
    tick: float,
    note_x: float,
    frame: CanvasFrame,
    background: BackgroundTimeline,
    options: Options,
    require_low_speed: bool,
) -> tuple[bool, bool]:
    """检查线的某个屏幕段是否在判定线上穿过 Note。

    返回 ``(是否覆盖, 是否存在几何上覆盖但速度不为 0 的段)``。
    """
    geometry_hit = False
    for index in range(len(info.point_xs) - 1):
        start_floor = info.point_floors[index]
        end_floor = info.point_floors[index + 1]
        if start_floor is None or end_floor is None:
            continue

        start_canvas = info.point_canvases[index]
        end_canvas = info.point_canvases[index + 1]
        start_canvas_x = frame.x(start_canvas)
        end_canvas_x = frame.x(end_canvas)
        if start_canvas_x is None or end_canvas_x is None:
            continue

        start_scroll = frame.floor(start_canvas)
        end_scroll = frame.floor(end_canvas)
        if start_scroll is None or end_scroll is None:
            continue

        start_x = info.point_xs[index] + start_canvas_x
        end_x = info.point_xs[index + 1] + end_canvas_x
        if note_x < min(start_x, end_x) - options.x_tolerance:
            continue
        if note_x > max(start_x, end_x) + options.x_tolerance:
            continue

        start_screen_y = start_floor - start_scroll
        end_screen_y = end_floor - end_scroll
        start_alpha = info.point_alphas[index]
        end_alpha = info.point_alphas[index + 1]

        if abs(end_x - start_x) <= 1e-12:
            y_ok = (
                min(start_screen_y, end_screen_y) - options.y_tolerance
                <= 0.0
                <= max(start_screen_y, end_screen_y) + options.y_tolerance
            )
            alpha_ok = max(start_alpha, end_alpha) >= options.stationary_min_alpha
            color_ratio = 1.0
        else:
            ratio = (note_x - start_x) / (end_x - start_x)
            ratio = min(1.0, max(0.0, ratio))
            screen_y_at_note = (
                start_screen_y + (end_screen_y - start_screen_y) * ratio
            )
            y_ok = abs(screen_y_at_note) <= options.y_tolerance
            alpha = start_alpha + (end_alpha - start_alpha) * ratio
            alpha_ok = alpha >= options.stationary_min_alpha
            color_ratio = ratio

        if not (y_ok and alpha_ok):
            continue

        # 线颜色本身透明，或经过 lineColor 覆盖后等于主题背景色时，
        # 该线视觉上不可见，不能作为静止判定线。
        base_color = _lerp_rgba(
            info.point_colors[index],
            info.point_colors[index + 1],
            color_ratio,
        )
        if base_color[3] < options.stationary_min_alpha:
            continue
        cover_color = rcf.getCoverColor(chart, info.index, tick)
        if cover_color is None:
            mixed_color = base_color
        else:
            mixed = rcf.mixColor(base_color, cover_color)
            mixed_rgba = _rgba_list(mixed) if mixed is not None else None
            mixed_color = mixed_rgba if mixed_rgba is not None else base_color
        background_color = background.color_at(tick)
        if (
            background_color is not None
            and _rgb_matches(
                mixed_color,
                background_color,
                options.background_tolerance,
            )
        ):
            continue

        geometry_hit = True

        if require_low_speed:
            speeds: list[float] = []
            for canvas in {start_canvas, end_canvas}:
                speed = frame.speed(canvas)
                if speed is not None:
                    speeds.append(abs(speed))
            if not speeds or max(speeds) > options.stationary_speed_tolerance:
                continue

        return True, True

    return False, geometry_hit


def _stationary_line_covers(
    chart: dict,
    info: LineInfo,
    tick: float,
    note_x: float,
    frame: CanvasFrame,
    background: BackgroundTimeline,
    stationary_span_ticks: float,
    options: Options,
) -> bool:
    """判断线在 Note 时刻是否静止且穿过 Note 的击打位置。"""
    if not _line_may_cover(info, note_x, frame, options):
        return False

    # 优先按画布速度判断：速度为 0 或很小时，floorPosition 基本不变。
    covered, geometry_hit = _stationary_segment_covers(
        chart,
        info,
        tick,
        note_x,
        frame,
        background,
        options,
        require_low_speed=True,
    )
    if covered:
        return True
    if not geometry_hit:
        return False

    # 速度不是严格 0 时，再按前后 floorPosition 采样判断。
    stable = (
        _floor_stable_on_side(
            chart,
            info,
            tick,
            +1,
            stationary_span_ticks,
            options.stationary_tolerance,
        )
        or _floor_stable_on_side(
            chart,
            info,
            tick,
            -1,
            stationary_span_ticks,
            options.stationary_tolerance,
        )
        or _point_floor_stable_on_side(
            info,
            tick,
            +1,
            stationary_span_ticks,
            options.stationary_tolerance,
        )
        or _point_floor_stable_on_side(
            info,
            tick,
            -1,
            stationary_span_ticks,
            options.stationary_tolerance,
        )
    )
    if not stable:
        return False
    covered, _ = _stationary_segment_covers(
        chart,
        info,
        tick,
        note_x,
        frame,
        background,
        options,
        require_low_speed=False,
    )
    return covered


def _find_stationary_cover(
    chart: dict,
    line_infos: Sequence[LineInfo],
    tick: float,
    note_x: float,
    background: BackgroundTimeline,
    stationary_span_ticks: float,
    options: Options,
) -> int | None:
    frame = CanvasFrame(chart, tick)
    for info in line_infos:
        # 注意：这里不要求 line 的时间范围包含 tick。
        # 当画布速度为 0 时，一个位于未来/过去时间的水平线在当前时刻
        # 仍可能停在判定线上，因此不能先用 is_active() 排除。
        if _stationary_line_covers(
            chart,
            info,
            tick,
            note_x,
            frame,
            background,
            stationary_span_ticks,
            options,
        ):
            return info.index
    return None


def _line_sample_visible(
    start_color: Sequence[int],
    end_color: Sequence[int],
    ratio: float,
    cover_color: Sequence[int] | None,
    background: BackgroundTimeline,
    tick: float,
    options: Options,
) -> bool:
    base_color = _lerp_rgba(start_color, end_color, ratio)
    if base_color[3] < options.stationary_min_alpha:
        return False
    if cover_color is None:
        mixed_color = base_color
    else:
        mixed = rcf.mixColor(base_color, cover_color)
        mixed_rgba = _rgba_list(mixed) if mixed is not None else None
        mixed_color = mixed_rgba if mixed_rgba is not None else base_color
    background_color = background.color_at(tick)
    if background_color is not None and _rgb_matches(
        mixed_color,
        background_color,
        options.background_tolerance,
    ):
        return False
    return True


def _range_search_cover(
    chart: dict,
    line_infos: Sequence[LineInfo],
    tick: float,
    note_x: float,
    note_y: float,
    background: BackgroundTimeline,
    options: Options,
) -> int | None:
    """搜索 Note 周围的可见线，并检查整条线的形心是否靠近 Note。"""
    frame = CanvasFrame(chart, tick)
    x_low = note_x - options.range_x
    x_high = note_x + options.range_x
    y_low = note_y - options.range_y
    y_high = note_y + options.range_y

    combined_samples: list[tuple[float, float]] = []
    for info in line_infos:
        if info.max_alpha < options.stationary_min_alpha:
            continue

        line_x_low = math.inf
        line_x_high = -math.inf
        for canvas, (low, high) in info.canvas_x_ranges.items():
            canvas_x = frame.x(canvas)
            if canvas_x is None:
                continue
            line_x_low = min(line_x_low, low + canvas_x)
            line_x_high = max(line_x_high, high + canvas_x)
        if line_x_low > x_high or line_x_high < x_low:
            continue

        line_y_low = math.inf
        line_y_high = -math.inf
        for low, high in info.canvas_floor_ranges.values():
            line_y_low = min(line_y_low, low)
            line_y_high = max(line_y_high, high)
        if line_y_low > y_high or line_y_high < y_low:
            continue

        cover_color = rcf.getCoverColor(chart, info.index, tick)
        samples: list[tuple[float, float]] = []
        inside_count = 0
        step_count = options.range_samples
        for index in range(len(info.point_xs) - 1):
            start_floor = info.point_floors[index]
            end_floor = info.point_floors[index + 1]
            if start_floor is None or end_floor is None:
                continue
            start_canvas_x = frame.x(info.point_canvases[index])
            end_canvas_x = frame.x(info.point_canvases[index + 1])
            if start_canvas_x is None or end_canvas_x is None:
                continue
            start_x = info.point_xs[index] + start_canvas_x
            end_x = info.point_xs[index + 1] + end_canvas_x
            start_color = info.point_colors[index]
            end_color = info.point_colors[index + 1]

            for step in range(step_count + 1):
                ratio = step / step_count
                x = start_x + (end_x - start_x) * ratio
                y = start_floor + (end_floor - start_floor) * ratio
                if not _line_sample_visible(
                    start_color,
                    end_color,
                    ratio,
                    cover_color,
                    background,
                    tick,
                    options,
                ):
                    continue
                samples.append((x, y))
                if x_low <= x <= x_high and y_low <= y <= y_high:
                    inside_count += 1

        if inside_count == 0 or len(samples) < options.range_min_samples:
            continue

        centroid_x = sum(item[0] for item in samples) / len(samples)
        centroid_y = sum(item[1] for item in samples) / len(samples)
        if (
            abs(centroid_x - note_x) <= options.range_x
            and abs(centroid_y - note_y) <= options.range_y
        ):
            return info.index
        combined_samples.extend(samples)

    if len(combined_samples) >= options.range_min_samples:
        centroid_x = sum(item[0] for item in combined_samples) / len(combined_samples)
        centroid_y = sum(item[1] for item in combined_samples) / len(combined_samples)
        if (
            abs(centroid_x - note_x) <= options.range_x
            and abs(centroid_y - note_y) <= options.range_y
        ):
            return -1
    return None


def _note_floor_at(
    chart: dict,
    info: LineInfo,
    note_index: int,
    tick: float,
) -> float | None:
    """Note 头部的 Y 坐标，优先使用谱面中保存的 floorPosition。"""
    if 0 <= note_index < len(info.full_notes):
        full_note = info.full_notes[note_index]
        if isinstance(full_note, Mapping):
            value = full_note.get("floorPosition")
            try:
                if value is not None:
                    number = float(value)
                    if math.isfinite(number):
                        return number
            except (TypeError, ValueError):
                pass

    canvas = rcf.getNoteCanvas(chart, info.index, note_index)
    if canvas is not None:
        value = rcf.getFloorPosition(chart, tick, canvasindex=canvas)
        if value is not None:
            return float(value)
    return info.point_floor_at(tick)


def _build_line_infos(
    chart: dict,
    line_count: int,
    report: ChartReport,
) -> list[LineInfo]:
    infos: list[LineInfo] = []
    full_lines = chart.get("lines")
    if not isinstance(full_lines, list):
        return infos

    for line_index in range(line_count):
        line = rcf.getLine(chart, line_index)
        if line is None:
            report.warnings.append(f"failed to read line {line_index}; skipped")
            continue
        if line_index >= len(full_lines) or not isinstance(full_lines[line_index], Mapping):
            report.warnings.append(f"line {line_index} has invalid raw data; skipped")
            continue

        full_line = full_lines[line_index]
        full_points = full_line.get("linePoints")
        if not isinstance(full_points, list) or not full_points:
            continue

        point_times: list[float] = []
        point_canvases: list[int] = []
        point_floors: list[float | None] = []
        point_xs: list[float] = []
        point_colors: list[list[int]] = []
        point_alphas: list[float] = []
        valid = True
        for point in full_points:
            if not isinstance(point, Mapping):
                valid = False
                break
            try:
                time = float(point["time"])
                canvas = int(point["canvasIndex"])
                x_position = float(point["xPosition"])
            except (KeyError, TypeError, ValueError):
                valid = False
                break
            floor_value = point.get("floorPosition")
            try:
                floor = float(floor_value) if floor_value is not None else None
                if floor is not None and not math.isfinite(floor):
                    floor = None
            except (TypeError, ValueError):
                floor = None
            color = _rgba_list(point.get("color")) or [0, 0, 0, 0]
            point_times.append(time)
            point_canvases.append(canvas)
            point_floors.append(floor)
            point_xs.append(x_position)
            point_colors.append(color)
            point_alphas.append(float(color[3]))

        if not valid or not point_times:
            report.warnings.append(f"line {line_index} has invalid line points; skipped")
            continue

        line_end = point_times[-1]
        ring_nodes = line.get("judgeRingColor") or []
        notes = line.get("notes") or []
        full_notes = full_line.get("notes")
        if not isinstance(full_notes, list):
            full_notes = []

        canvas_x_ranges: dict[int, tuple[float, float]] = {}
        canvas_floor_ranges: dict[int, tuple[float, float]] = {}
        for x_position, floor, canvas in zip(
            point_xs,
            point_floors,
            point_canvases,
        ):
            old_x = canvas_x_ranges.get(canvas)
            if old_x is None:
                canvas_x_ranges[canvas] = (x_position, x_position)
            else:
                canvas_x_ranges[canvas] = (
                    min(old_x[0], x_position),
                    max(old_x[1], x_position),
                )
            if floor is None:
                continue
            old_floor = canvas_floor_ranges.get(canvas)
            if old_floor is None:
                canvas_floor_ranges[canvas] = (floor, floor)
            else:
                canvas_floor_ranges[canvas] = (
                    min(old_floor[0], floor),
                    max(old_floor[1], floor),
                )

        infos.append(
            LineInfo(
                index=line_index,
                start_time=point_times[0],
                end_time=line_end,
                point_times=point_times,
                point_canvases=point_canvases,
                point_floors=point_floors,
                point_xs=point_xs,
                point_colors=point_colors,
                point_alphas=point_alphas,
                canvas_x_ranges=canvas_x_ranges,
                canvas_floor_ranges=canvas_floor_ranges,
                max_alpha=max(point_alphas, default=0.0),
                ring=RingTimeline(ring_nodes, line_end=line_end),
                notes=notes,
                full_notes=full_notes,
            )
        )

    return infos


def analyze_chart(chart: dict, path: Path, options: Options) -> ChartReport:
    report = ChartReport(path=path)

    # 分析和规范化都在副本上进行，避免影响调用方或写回文件。
    chart = copy.deepcopy(chart)

    timeline_note = _prepare_timeline(chart)
    report.timeline_note = timeline_note

    # 有些官方/编辑器样本的 linePoints 时间不是非递减的，load() 不会排序。
    # 先排序并重算高度，后面的 getLine()/getLinePosition() 才能读取这些线。
    refresh_result = rcf.refresh(chart, mode="all", type="all")
    if refresh_result is None:
        report.warnings.append(
            "refresh() failed; some arrays with invalid order may be skipped"
        )

    time_map = ChartTime(chart)
    background = BackgroundTimeline(chart)

    line_count = rcf.getLineCount(chart)
    if line_count is None:
        report.error = "invalid 'lines' structure"
        return report
    report.lines = line_count
    line_infos = _build_line_infos(chart, line_count, report)

    for info in line_infos:
        for note_index, note in enumerate(info.notes):
            try:
                tick = float(note["time"])
            except (KeyError, TypeError, ValueError):
                report.warnings.append(
                    f"line {info.index} note {note_index}: invalid time; skipped"
                )
                continue

            report.notes_checked += 1
            note_type = NOTE_TYPES.get(note.get("type"), str(note.get("type")))
            seconds = time_map.seconds(tick)
            own_ring_color = _ring_color_at(chart, info, tick)
            alpha_at_note = float(own_ring_color[3]) if own_ring_color else 0.0

            # 自己线上的判定环可见时，Note 一定在环上。
            if _ring_color_visible(
                own_ring_color,
                tick,
                background,
                options,
            ):
                report.ok_same_line += 1
                continue

            end_tick: float | None = None
            start_tick: float | None = None
            if options.window_after > 0.0 and seconds is not None:
                end_tick = time_map.tick(seconds + options.window_after)
            if options.window_before > 0.0 and seconds is not None:
                start_tick = time_map.tick(max(0.0, seconds - options.window_before))

            own_flash_after = (
                end_tick is not None
                and _ring_visible_in_interval(
                    info,
                    tick,
                    end_tick,
                    background,
                    options,
                )
            )
            own_flash_before = (
                start_tick is not None
                and _ring_visible_in_interval(
                    info,
                    start_tick,
                    tick,
                    background,
                    options,
                )
            )

            # 其他线是否在 Note 的绝对 (X, Y) 处提供可见判定环。
            note_x = rcf.getNotePosition(
                chart,
                info.index,
                note_index,
                isAbsolutePosition=True,
            )
            note_y = _note_floor_at(chart, info, note_index, tick)
            position_ready = note_x is not None and note_y is not None

            visible_other = False
            flash_candidates: list[tuple[int, bool, bool]] = []

            if own_flash_after or own_flash_before:
                flash_candidates.append(
                    (info.index, bool(own_flash_after), bool(own_flash_before))
                )

            if not position_ready:
                report.warnings.append(
                    f"line {info.index} note {note_index}: "
                    "absolute position unavailable; only its own line was checked"
                )
            else:
                for candidate in line_infos:
                    if candidate.index == info.index:
                        continue
                    if not candidate.is_active(tick):
                        continue

                    candidate_ring_color = _ring_color_at(
                        chart,
                        candidate,
                        tick,
                    )
                    at_visible = _ring_color_visible(
                        candidate_ring_color,
                        tick,
                        background,
                        options,
                    )
                    flash_after = False
                    flash_before = False

                    if not at_visible and end_tick is not None:
                        flash_after = _ring_visible_in_interval(
                            candidate,
                            tick,
                            end_tick,
                            background,
                            options,
                        )
                    if not at_visible and not flash_after and start_tick is not None:
                        flash_before = _ring_visible_in_interval(
                            candidate,
                            start_tick,
                            tick,
                            background,
                            options,
                        )
                    if not at_visible and not flash_after and not flash_before:
                        continue

                    line_x = rcf.getLinePosition(
                        chart,
                        candidate.index,
                        tick,
                        isAbsolutePosition=True,
                    )
                    if line_x is None:
                        continue
                    # 击打时刻，所有仍在当前时刻的判定环都落在判定线上；
                    # 跨线覆盖只需要比较包含画布偏移的绝对 X。
                    if abs(line_x - note_x) > options.x_tolerance:
                        continue

                    if at_visible:
                        visible_other = True
                        break

                    flash_candidates.append(
                        (candidate.index, bool(flash_after), bool(flash_before))
                    )

            if visible_other:
                report.ok_other_line += 1
                continue

            if options.stationary_line and position_ready:
                stationary_span_ticks = 0.0
                if seconds is not None and options.stationary_window > 0.0:
                    stationary_end = time_map.tick(
                        seconds + options.stationary_window
                    )
                    if stationary_end is not None:
                        stationary_span_ticks = abs(stationary_end - tick)
                stationary_line = _find_stationary_cover(
                    chart,
                    line_infos,
                    tick,
                    note_x,
                    background,
                    stationary_span_ticks,
                    options,
                )
                if stationary_line is not None:
                    report.ok_stationary_line += 1
                    continue

            if options.range_search and position_ready:
                range_line = _range_search_cover(
                    chart,
                    line_infos,
                    tick,
                    note_x,
                    note_y,
                    background,
                    options,
                )
                if range_line is not None:
                    report.ok_range_search += 1
                    continue

            if flash_candidates:
                ring_line, flash_after, flash_before = flash_candidates[0]
                if options.accept_flash:
                    report.flash_ignored += 1
                    continue
                if flash_after and flash_before:
                    reason = "flash_both"
                elif flash_after:
                    reason = "flash_after"
                else:
                    reason = "flash_before"
                report.issues.append(
                    NoteIssue(
                        tick=tick,
                        seconds=seconds,
                        line_index=info.index,
                        note_index=note_index,
                        note_type=note_type,
                        reason=reason,
                        alpha_at_note=alpha_at_note,
                        ring_line=ring_line,
                        note_x=note_x,
                        note_y=note_y,
                    )
                )
                continue

            report.issues.append(
                NoteIssue(
                    tick=tick,
                    seconds=seconds,
                    line_index=info.index,
                    note_index=note_index,
                    note_type=note_type,
                    reason="missing",
                    alpha_at_note=alpha_at_note,
                    ring_line=None,
                    note_x=note_x,
                    note_y=note_y,
                )
            )

    report.issues.sort(
        key=lambda issue: (
            issue.seconds if issue.seconds is not None else float("inf"),
            issue.tick,
            issue.line_index,
            issue.note_index,
        )
    )
    return report


# ---------------------------------------------------------------------------
# 输入路径
# ---------------------------------------------------------------------------

def _is_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _looks_like_chart_file(path: Path) -> bool:
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


def resolve_paths(inputs: Iterable[str]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path.absolute())
        if key not in seen:
            seen.add(key)
            resolved.append(path)

    for raw in inputs:
        if _is_glob(raw):
            for name in sorted(glob.glob(raw, recursive=True)):
                path = Path(name)
                if path.is_file():
                    add(path)
            continue

        path = Path(raw).expanduser()
        if path.is_file():
            add(path)
            continue
        if path.is_dir():
            # 同时支持 Chart_EZ / mr_Chart_EZ / challenge/1001_Chart
            # 以及带 .json 后缀的同类文件。
            candidates = [
                item
                for item in path.rglob("*")
                if item.is_file()
                and _looks_like_chart_file(item)
                and "auto-save" not in item.name.lower()
                and not any(
                    part.lower() == "rizline editor auto-save"
                    for part in item.parts
                )
            ]
            for item in sorted(candidates, key=lambda p: str(p).lower()):
                add(item)
            continue
        print(f"warning: path does not exist or is not a file/directory: {raw}", file=sys.stderr)

    return resolved


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _report_rows(report: ChartReport) -> list[dict[str, Any]]:
    return [
        {
            "tick": issue.tick,
            "seconds": issue.seconds,
            "time": _format_clock(issue.seconds),
            "line": issue.line_index,
            "note": issue.note_index,
            "type": issue.note_type,
            "reason": issue.reason,
            "reason_text": issue.reason_text,
            "alpha_at_note": issue.alpha_at_note,
            "ring_line": issue.ring_line,
            "note_x": issue.note_x,
            "note_y": issue.note_y,
        }
        for issue in report.issues
    ]


def render_text(reports: Sequence[ChartReport], options: Options) -> str:
    out: list[str] = []
    for report_index, report in enumerate(reports):
        if report_index:
            out.append("")
        out.append(f"=== {report.path} ===")
        if report.error:
            out.append(f"  error: {report.error}")
            continue

        if report.timeline_note:
            out.append(f"  timeline: {report.timeline_note}")
        out.append(
            f"  check: min_alpha={options.min_alpha:g} | "
            f"window_after={options.window_after:g}s | "
            f"window_before={options.window_before:g}s | "
            f"tolerance=({options.x_tolerance:g}, {options.y_tolerance:g}) | "
            f"background_tolerance={options.background_tolerance:g} | "
            f"accept_flash={options.accept_flash}"
        )
        if options.stationary_line:
            out.append(
                f"  stationary line: enabled | window={options.stationary_window:g}s | "
                f"floor_tolerance={options.stationary_tolerance:g} | "
                f"speed_tolerance={options.stationary_speed_tolerance:g} | "
                f"min_alpha={options.stationary_min_alpha:g}"
            )
        if options.range_search:
            out.append(
                f"  range search: enabled | x={options.range_x:g} | "
                f"y={options.range_y:g} | samples={options.range_samples} | "
                f"min_samples={options.range_min_samples}"
            )
        out.append(
            f"  lines: {report.lines} | notes: {report.notes_checked} | "
            f"missing ring: {report.missing_count} | flash: {report.flash_count} | "
            f"ok: {report.ok_count} (same line: {report.ok_same_line}, "
            f"other line: {report.ok_other_line}, "
            f"stationary line: {report.ok_stationary_line}, "
            f"range search: {report.ok_range_search})"
        )
        if options.accept_flash and report.flash_ignored:
            out.append(f"  flash rings ignored by --accept-flash: {report.flash_ignored}")

        for warning in report.warnings:
            out.append(f"  warning: {warning}")

        if not report.issues:
            out.append("  problem notes: none")
            continue

        out.append("  problem notes:")
        out.append(
            "    tick             time(s)      mm:ss.mmm    line   note   type   "
            "ring   reason"
        )
        for issue in report.issues:
            seconds = "-" if issue.seconds is None else f"{issue.seconds:.6f}"
            ring_line = "-" if issue.ring_line is None else str(issue.ring_line)
            out.append(
                f"    {issue.tick:<15.6f} {seconds:<12} "
                f"{_format_clock(issue.seconds):<11} "
                f"{issue.line_index:<6} {issue.note_index:<6} "
                f"{issue.note_type:<6} {ring_line:<6} {issue.reason_text}"
            )
    return "\n".join(out)


def render_times(reports: Sequence[ChartReport]) -> str:
    out: list[str] = []
    for report in reports:
        out.append(f"=== {report.path} ===")
        if report.error:
            out.append(f"# error: {report.error}")
            continue
        if not report.issues:
            out.append("# no problem notes")
            continue
        for issue in report.issues:
            if issue.seconds is not None:
                out.append(f"{issue.seconds:.6f}")
            else:
                out.append(f"tick:{issue.tick:g}")
    return "\n".join(out)


def render_json(reports: Sequence[ChartReport], options: Options) -> str:
    payload = []
    for report in reports:
        payload.append(
            {
                "path": str(report.path),
                "ok": report.error is None,
                "error": report.error,
                "lines": report.lines,
                "notes_checked": report.notes_checked,
                "missing": report.missing_count,
                "flash": report.flash_count,
                "ok_notes": report.ok_count,
                "ok_same_line": report.ok_same_line,
                "ok_other_line": report.ok_other_line,
                "ok_stationary_line": report.ok_stationary_line,
                "ok_range_search": report.ok_range_search,
                "flash_ignored": report.flash_ignored,
                "timeline": report.timeline_note,
                "warnings": report.warnings,
                "issues": _report_rows(report),
            }
        )
    return json.dumps(
        {
            "window_after": options.window_after,
            "window_before": options.window_before,
            "min_alpha": options.min_alpha,
            "x_tolerance": options.x_tolerance,
            "y_tolerance": options.y_tolerance,
            "background_tolerance": options.background_tolerance,
            "stationary_line": options.stationary_line,
            "stationary_window": options.stationary_window,
            "stationary_tolerance": options.stationary_tolerance,
            "stationary_speed_tolerance": options.stationary_speed_tolerance,
            "stationary_min_alpha": options.stationary_min_alpha,
            "range_search": options.range_search,
            "range_x": options.range_x,
            "range_y": options.range_y,
            "range_samples": options.range_samples,
            "range_min_samples": options.range_min_samples,
            "accept_flash": options.accept_flash,
            "charts": payload,
        },
        ensure_ascii=False,
        indent=2,
    )


def render_csv(reports: Sequence[ChartReport]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "chart",
            "tick",
            "seconds",
            "time",
            "line",
            "note",
            "type",
            "reason",
            "alpha_at_note",
            "ring_line",
            "note_x",
            "note_y",
        ]
    )
    for report in reports:
        if report.error:
            writer.writerow(
                [str(report.path), "", "", "", "", "", "", "error:" + report.error, "", "", "", ""]
            )
            continue
        for issue in report.issues:
            writer.writerow(
                [
                    str(report.path),
                    f"{issue.tick:.6f}",
                    "" if issue.seconds is None else f"{issue.seconds:.6f}",
                    _format_clock(issue.seconds),
                    issue.line_index,
                    issue.note_index,
                    issue.note_type,
                    issue.reason,
                    f"{issue.alpha_at_note:g}",
                    "" if issue.ring_line is None else issue.ring_line,
                    "" if issue.note_x is None else f"{issue.note_x:.6f}",
                    "" if issue.note_y is None else f"{issue.note_y:.6f}",
                ]
            )
    return buffer.getvalue().rstrip("\n")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rizline judge-ring checker: report notes with no visible judge ring "
            "at their hit position. At hit time objects sit on the judge line, so "
            "cross-line coverage is matched by absolute X (canvas offset included). "
            "Rings from any line can cover a note; Hold notes are checked at the head only."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Chart file(s), directory/directories, or glob pattern(s).",
    )
    parser.add_argument(
        "--window",
        "--window-after",
        dest="window_after",
        type=float,
        default=0.1,
        metavar="SEC",
        help="look-ahead window after each note in seconds (default: 0.1; 0 = exact instant only).",
    )
    parser.add_argument(
        "--window-before",
        type=float,
        default=0.0,
        metavar="SEC",
        help="look-back window before each note in seconds (default: 0).",
    )
    parser.add_argument(
        "--min-alpha",
        type=float,
        default=1.0,
        metavar="A",
        help="minimum alpha (0-255) treated as a visible ring (default: 1).",
    )
    parser.add_argument(
        "--x-tolerance",
        type=float,
        default=1e-2,
        metavar="DX",
        help="absolute-X tolerance for another line's ring (default: 0.01).",
    )
    parser.add_argument(
        "--y-tolerance",
        type=float,
        default=1e-2,
        metavar="DY",
        help="screen-Y tolerance for a stationary line (default: 0.01).",
    )
    parser.add_argument(
        "--background-tolerance",
        type=float,
        default=2.0,
        metavar="RGB",
        help=(
            "per-channel RGB tolerance for treating a ring/line color as the "
            "theme background (default: 2)."
        ),
    )
    parser.add_argument(
        "--stationary-line",
        action="store_true",
        help=(
            "also treat a nearly stationary guide line crossing the note's "
            "absolute position as a visible judge ring."
        ),
    )
    parser.add_argument(
        "--stationary-window",
        type=float,
        default=0.1,
        metavar="SEC",
        help="time window used to test line floor stability (default: 0.1).",
    )
    parser.add_argument(
        "--stationary-tolerance",
        type=float,
        default=1e-3,
        metavar="DY",
        help="allowed floorPosition change for a stationary line (default: 0.001).",
    )
    parser.add_argument(
        "--stationary-speed-tolerance",
        type=float,
        default=1e-2,
        metavar="V",
        help=(
            "canvas speed magnitude treated as stationary "
            "(default: 0.01; a chart using 0.001 is well within this)."
        ),
    )
    parser.add_argument(
        "--stationary-min-alpha",
        type=float,
        default=1.0,
        metavar="A",
        help="minimum line color alpha for a stationary line (default: 1).",
    )
    parser.add_argument(
        "--range-search",
        action="store_true",
        help=(
            "also accept visible line geometry whose centroid lies near the "
            "note inside the configured X/floor rectangle."
        ),
    )
    parser.add_argument(
        "--range-x",
        type=float,
        default=5e-2,
        metavar="DX",
        help="half width of the X search rectangle (default: 0.05).",
    )
    parser.add_argument(
        "--range-y",
        type=float,
        default=1e-1,
        metavar="DY",
        help="half height of the floorPosition search rectangle (default: 0.1).",
    )
    parser.add_argument(
        "--range-samples",
        type=int,
        default=4,
        metavar="N",
        help="samples per line segment for centroid search (default: 4).",
    )
    parser.add_argument(
        "--range-min-samples",
        type=int,
        default=4,
        metavar="N",
        help="minimum visible samples for a centroid result (default: 4).",
    )
    parser.add_argument(
        "--accept-flash",
        action="store_true",
        help="treat a ring that only flashes inside the window as OK.",
    )
    parser.add_argument(
        "--bpm",
        "--base-bpm",
        dest="base_bpm",
        type=float,
        default=None,
        metavar="BPM",
        help="base BPM override passed to rcf.load().",
    )
    parser.add_argument(
        "--format",
        choices=("text", "times", "json", "csv"),
        default="text",
        help="output format (default: text); 'times' prints only problem-note seconds.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        metavar="FILE",
        help="write output to a UTF-8 file instead of stdout.",
    )
    parser.add_argument(
        "--fail-on-problem",
        action="store_true",
        help="exit with code 1 when any problem note is found.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.window_after < 0 or args.window_before < 0:
        print("error: search windows must not be negative.", file=sys.stderr)
        return 2
    if not (0.0 <= args.min_alpha <= 255.0):
        print("error: --min-alpha must be between 0 and 255.", file=sys.stderr)
        return 2
    if (
        not math.isfinite(args.x_tolerance)
        or not math.isfinite(args.y_tolerance)
        or not math.isfinite(args.background_tolerance)
        or args.x_tolerance < 0.0
        or args.y_tolerance < 0.0
        or not (0.0 <= args.background_tolerance <= 255.0)
    ):
        print(
            "error: tolerances must be finite and non-negative "
            "(background tolerance 0-255).",
            file=sys.stderr,
        )
        return 2
    if (
        not math.isfinite(args.stationary_window)
        or not math.isfinite(args.stationary_tolerance)
        or not math.isfinite(args.stationary_speed_tolerance)
        or not math.isfinite(args.stationary_min_alpha)
        or args.stationary_window < 0.0
        or args.stationary_tolerance < 0.0
        or args.stationary_speed_tolerance < 0.0
        or not (0.0 <= args.stationary_min_alpha <= 255.0)
    ):
        print(
            "error: stationary-line parameters must be finite and non-negative "
            "(min alpha 0-255).",
            file=sys.stderr,
        )
        return 2
    if (
        not math.isfinite(args.range_x)
        or not math.isfinite(args.range_y)
        or args.range_x < 0.0
        or args.range_y < 0.0
    ):
        print("error: --range-x/--range-y must be non-negative.", file=sys.stderr)
        return 2
    if args.range_samples < 1 or args.range_min_samples < 1:
        print(
            "error: --range-samples/--range-min-samples must be positive.",
            file=sys.stderr,
        )
        return 2
    if args.base_bpm is not None and (
        not math.isfinite(args.base_bpm) or args.base_bpm <= 0.0
    ):
        print("error: --bpm must be a positive number.", file=sys.stderr)
        return 2

    paths = resolve_paths(args.paths)
    if not paths:
        print("error: no chart files found.", file=sys.stderr)
        return 2

    options = Options(
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

    reports: list[ChartReport] = []
    loaded = 0
    for path in paths:
        try:
            chart = rcf.load(str(path), basebpm=args.base_bpm)
        except Exception as exc:  # noqa: BLE001 - one bad chart must not stop the batch
            report = ChartReport(path=path, error=f"load exception: {exc}")
            reports.append(report)
            print(f"load failed: {path}: {exc}", file=sys.stderr)
            continue

        if chart is None:
            report = ChartReport(path=path, error="rcf.load() returned None")
            reports.append(report)
            print(f"load failed: {path}", file=sys.stderr)
            continue

        loaded += 1
        try:
            report = analyze_chart(chart, path, options)
        except Exception as exc:  # noqa: BLE001
            report = ChartReport(path=path, error=f"analysis exception: {exc}")
            print(f"analysis failed: {path}: {exc}", file=sys.stderr)
        reports.append(report)

    if args.format == "json":
        text = render_json(reports, options)
    elif args.format == "csv":
        text = render_csv(reports)
    elif args.format == "times":
        text = render_times(reports)
    else:
        text = render_text(reports, options)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.output}", file=sys.stderr)
    else:
        print(text)

    if loaded == 0:
        return 2
    if args.fail_on_problem and any(report.issues for report in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
