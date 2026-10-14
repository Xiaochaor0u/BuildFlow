"""Data model and the failure taxonomy.

``UNKNOWN`` is a first-class outcome: when the available evidence does not
support any specific category, the classifier says so rather than inventing a
cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FailureType(str, Enum):
    """Rule-based failure categories."""

    GIT_CHECKOUT = "GIT_CHECKOUT"
    MAVEN_COMPILE = "MAVEN_COMPILE"
    UNIT_TEST = "UNIT_TEST"
    SONAR_QUALITY_GATE = "SONAR_QUALITY_GATE"
    ENVIRONMENT = "ENVIRONMENT"
    UNKNOWN = "UNKNOWN"


FAILURE_LABELS = {
    FailureType.GIT_CHECKOUT: "Git 拉取失败",
    FailureType.MAVEN_COMPILE: "Maven 编译失败",
    FailureType.UNIT_TEST: "单元测试失败",
    FailureType.SONAR_QUALITY_GATE: "SonarQube 质量门禁失败",
    FailureType.ENVIRONMENT: "环境或依赖问题",
    FailureType.UNKNOWN: "未知原因",
}


class BuildState(str, Enum):
    """Normalised build outcome.

    ``IN_PROGRESS`` and ``ABORTED`` are kept apart from success/failure so they
    cannot distort the success-rate denominator.
    """

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ABORTED = "ABORTED"
    IN_PROGRESS = "IN_PROGRESS"
    UNSTABLE = "UNSTABLE"
    UNKNOWN = "UNKNOWN"

    @property
    def is_completed(self) -> bool:
        return self in (BuildState.SUCCESS, BuildState.FAILURE, BuildState.UNSTABLE)

    @property
    def counts_toward_rate(self) -> bool:
        """Only completed, non-aborted builds enter the success-rate calculation."""
        return self.is_completed


@dataclass
class Classification:
    """Outcome of applying the rule set to one failed build."""

    failure_type: FailureType
    evidence: str
    excerpt: str
    stage: Optional[str] = None
    rule: Optional[str] = None


@dataclass
class BuildRecord:
    """One Jenkins build, normalised for analysis."""

    number: int
    state: BuildState
    result_raw: Optional[str] = None
    timestamp_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    url: str = ""
    commit: Optional[str] = None
    branch: Optional[str] = None
    failed_stage: Optional[str] = None
    stages: list = field(default_factory=list)
    classification: Optional[Classification] = None
    log_available: bool = False
    note: str = ""

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.duration_ms is None:
            return None
        return round(self.duration_ms / 1000.0, 2)

    @property
    def short_commit(self) -> str:
        return (self.commit or "")[:10]

    @property
    def finished_at(self) -> Optional[str]:
        """Wall-clock completion time as ``YYYY-mm-dd HH:MM:SS`` (local time)."""
        if self.timestamp_ms is None or self.duration_ms is None:
            return None
        import datetime as _dt

        end = _dt.datetime.fromtimestamp(
            (self.timestamp_ms + self.duration_ms) / 1000.0
        )
        return end.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def started_at(self) -> Optional[str]:
        if self.timestamp_ms is None:
            return None
        import datetime as _dt

        return _dt.datetime.fromtimestamp(self.timestamp_ms / 1000.0).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
