"""Research-efficiency statistics over a list of :class:`BuildRecord`.

Metric definitions are explicit so the numbers can be audited:

* **total_builds** — every build read from Jenkins.
* **completed_builds** — builds that reached SUCCESS / FAILURE / UNSTABLE.
* **success_rate** — ``successes / (successes + failures)``.
  In-progress and aborted builds are *excluded* from both the numerator and the
  denominator, because they were never given a chance to succeed. They are
  reported separately instead.
* **average_duration** — arithmetic mean of completed builds only, so a
  still-running build cannot drag the figure down.
* **failure_mix** — share of each failure type among classified failures.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    FAILURE_LABELS,
    BuildRecord,
    BuildState,
    FailureType,
)


@dataclass
class DurationStat:
    count: int = 0
    total_seconds: float = 0.0
    avg_seconds: Optional[float] = None
    min_seconds: Optional[float] = None
    max_seconds: Optional[float] = None
    median_seconds: Optional[float] = None


@dataclass
class Analysis:
    """Everything the reports render."""

    records: list[BuildRecord] = field(default_factory=list)
    total_builds: int = 0
    successes: int = 0
    failures: int = 0
    unstable: int = 0
    aborted: int = 0
    in_progress: int = 0
    unknown_state: int = 0
    completed_builds: int = 0
    success_rate: Optional[float] = None
    duration: DurationStat = field(default_factory=DurationStat)
    failure_counts: Counter = field(default_factory=Counter)
    failure_shares: dict = field(default_factory=dict)
    trend: list = field(default_factory=list)
    trend_slope: Optional[float] = None
    trend_direction: str = "n/a"
    classified_failures: int = 0
    unclassified_failures: int = 0
    # Cold-start artefacts: the first build usually pays for downloading Maven and
    # every dependency, so its duration is not comparable with later builds.
    cold_start_outlier: bool = False
    cold_start_note: str = ""

    @property
    def success_rate_percent(self) -> Optional[float]:
        return None if self.success_rate is None else round(self.success_rate * 100, 1)


def _duration_stat(values: list[float]) -> DurationStat:
    if not values:
        return DurationStat()
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2
    return DurationStat(
        count=len(ordered),
        total_seconds=round(sum(ordered), 2),
        avg_seconds=round(sum(ordered) / len(ordered), 2),
        min_seconds=round(ordered[0], 2),
        max_seconds=round(ordered[-1], 2),
        median_seconds=round(median, 2),
    )


def _linear_slope(points: list[tuple[float, float]]) -> Optional[float]:
    """Least-squares slope of y against x; ``None`` when under-determined."""
    n = len(points)
    if n < 2:
        return None
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    denom = sum((p[0] - mean_x) ** 2 for p in points)
    if denom == 0:
        return None
    numer = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
    return numer / denom


def analyse(records: list[BuildRecord]) -> Analysis:
    """Compute all statistics for the given build records."""
    result = Analysis(records=list(records))
    result.total_builds = len(records)

    durations: list[float] = []
    for record in records:
        state = record.state
        if state is BuildState.SUCCESS:
            result.successes += 1
        elif state is BuildState.FAILURE:
            result.failures += 1
        elif state is BuildState.UNSTABLE:
            result.unstable += 1
        elif state is BuildState.ABORTED:
            result.aborted += 1
        elif state is BuildState.IN_PROGRESS:
            result.in_progress += 1
        else:
            result.unknown_state += 1

        if state.counts_toward_rate:
            result.completed_builds += 1
            if record.duration_seconds is not None:
                durations.append(record.duration_seconds)

    decisive = result.successes + result.failures + result.unstable
    if decisive:
        result.success_rate = (result.successes + result.unstable) / decisive

    result.duration = _duration_stat(durations)

    # Failure mix is computed over failures the classifier could actually name.
    # An explicit UNKNOWN verdict counts as *unclassified*: it carries no cause.
    for record in records:
        if record.state is not BuildState.FAILURE:
            continue
        classification = record.classification
        if (
            classification is not None
            and classification.failure_type is not FailureType.UNKNOWN
        ):
            result.failure_counts[classification.failure_type] += 1
            result.classified_failures += 1
        else:
            result.unclassified_failures += 1

    total_failures = sum(result.failure_counts.values())
    if total_failures:
        result.failure_shares = {
            ft: round(count * 100.0 / total_failures, 1)
            for ft, count in result.failure_counts.items()
        }

    # Chronological trend (oldest -> newest) over completed builds.
    ordered = sorted(
        (r for r in records if r.timestamp_ms is not None),
        key=lambda r: r.timestamp_ms,
    )
    trend_points: list[tuple[float, float]] = []
    for record in ordered:
        if record.duration_seconds is None or not record.state.counts_toward_rate:
            continue
        trend_points.append((float(record.timestamp_ms), record.duration_seconds))
        result.trend.append(
            {
                "number": record.number,
                "duration_seconds": record.duration_seconds,
                "state": record.state.value,
                "finished_at": record.finished_at,
            }
        )

    slope = _linear_slope(trend_points)
    if slope is not None:
        # Seconds per hour reads better than seconds per millisecond.
        result.trend_slope = round(slope * 3_600_000, 3)
        if abs(result.trend_slope) < 0.5:
            result.trend_direction = "stable"
        elif result.trend_slope > 0:
            result.trend_direction = "slower"
        else:
            result.trend_direction = "faster"

    _detect_cold_start(result)
    return result


def _detect_cold_start(
    analysis: Analysis, factor: float = 2.0, min_seconds: float = 30.0
) -> None:
    """Flag a build far slower than the rest, typically the first real pipeline run.

    The first run that executes the full pipeline downloads Maven and every
    dependency, so it can be many times slower than later runs and it skews the
    trend slope. Detection is duration-based rather than position-based, because
    early *failed* builds finish almost instantly and would otherwise be picked as
    "the first build".
    """
    completed = [
        record
        for record in analysis.records
        if record.state.counts_toward_rate and record.duration_seconds is not None
    ]
    if len(completed) < 3:
        return

    # A build only qualifies if it actually ran long enough to be a full pipeline
    # execution; the skipped/failed fast builds are not candidates.
    candidates = [r for r in completed if r.duration_seconds >= min_seconds]
    if len(candidates) < 3:
        return

    slowest = max(candidates, key=lambda r: r.duration_seconds)
    others = sorted(
        r.duration_seconds for r in completed if r.number != slowest.number
    )
    if not others:
        return
    mid = len(others) // 2
    median = others[mid] if len(others) % 2 else (others[mid - 1] + others[mid]) / 2
    if median <= 0 or slowest.duration_seconds < median * factor:
        return

    ratio = slowest.duration_seconds / median
    analysis.cold_start_outlier = True
    analysis.cold_start_note = (
        f"构建 #{slowest.number} 耗时 {slowest.duration_seconds:.1f}s，"
        f"约为其余已完成构建中位数（{median:.1f}s）的 {ratio:.1f} 倍。"
        "该次通常是首次完整执行流水线，包含 Maven 与全部依赖的首次下载（冷启动），"
        "与其他构建不具可比性，会明显放大耗时趋势斜率，请结合本说明解读趋势。"
    )


def failure_label(failure_type: FailureType) -> str:
    return FAILURE_LABELS.get(failure_type, failure_type.value)
