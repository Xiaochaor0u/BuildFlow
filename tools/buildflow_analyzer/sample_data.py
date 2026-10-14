"""Synthetic build history for demonstrating the reports.

These records are **not** real Jenkins data. They exist only so the reports can
be shown and the classifier exercised without a live server. Any report built
from them is labelled ``DEMO`` in both the HTML header and the console output.
"""

from __future__ import annotations

import time

from .classifier import classify_failure, parse_log_facts
from .models import BuildRecord, BuildState

# Each entry: (build number, state, duration seconds, console log tail)
# The logs are written to trigger one specific classifier rule each, so the demo
# also documents what each rule looks for.
_SAMPLES: list[tuple[int, BuildState, float, str]] = [
    (
        1,
        BuildState.SUCCESS,
        41.0,
        "[Pipeline] { (Maven Test + Coverage)\n[INFO] Tests run: 6, Failures: 0\n"
        "[Pipeline] { (SonarQube Quality Gate)\nQUALITY GATE STATUS: PASSED\nFinished: SUCCESS\n",
    ),
    (
        2,
        BuildState.FAILURE,
        6.5,
        "[Pipeline] { (Checkout)\n"
        "fatal: detected dubious ownership in repository at '/git/origin.git'\n"
        "ERROR: Error cloning remote repo 'origin'\nFinished: FAILURE\n",
    ),
    (
        3,
        BuildState.SUCCESS,
        505.0,
        "[Pipeline] { (Maven Test + Coverage)\nBUILD SUCCESS\nFinished: SUCCESS\n",
    ),
    (
        4,
        BuildState.SUCCESS,
        40.0,
        "[Pipeline] { (Maven Test + Coverage)\nBUILD SUCCESS\nFinished: SUCCESS\n",
    ),
    (
        5,
        BuildState.FAILURE,
        21.0,
        "[Pipeline] { (Maven Test + Coverage)\n+ ./mvnw -B clean test\n"
        "./mvnw: 56: Syntax error: word unexpected (expecting \"in\")\n"
        "ERROR: script returned exit code 2\nFinished: FAILURE\n",
    ),
    (
        6,
        BuildState.SUCCESS,
        165.0,
        "[Pipeline] { (Maven Test + Coverage)\nTests run: 10, Failures: 0\nFinished: SUCCESS\n",
    ),
    (
        7,
        BuildState.FAILURE,
        70.0,
        "[Pipeline] { (SonarQube Quality Gate)\n"
        "[INFO] ------------- Check Quality Gate status\n"
        "[ERROR] Failed to execute goal org.sonarsource.scanner.maven:sonar-maven-plugin:4.0.0.4121:sonar"
        " on project devops-ci-cd-pipeline: QUALITY GATE STATUS: FAILED\n"
        'Stage "Publish (placeholder)" skipped due to earlier failure(s)\nFinished: FAILURE\n',
    ),
    (
        8,
        BuildState.FAILURE,
        33.0,
        "[Pipeline] { (Maven Test + Coverage)\n[INFO] Compiling 5 source files\n"
        "[ERROR] COMPILATION ERROR :\n"
        "[ERROR] /workspace/src/main/java/com/devops/demo/service/CustomerService.java:[18,9]"
        " cannot find symbol\nFinished: FAILURE\n",
    ),
    (
        9,
        BuildState.FAILURE,
        52.0,
        "[Pipeline] { (Maven Test + Coverage)\n[INFO] Running com.devops.demo.service.CustomerServiceTest\n"
        "[ERROR] Tests run: 2, Failures: 1, Errors: 0, Skipped: 0\n"
        "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.2.5:test\n"
        "Finished: FAILURE\n",
    ),
    (
        10,
        BuildState.SUCCESS,
        68.0,
        "[Pipeline] { (SonarQube Quality Gate)\nQUALITY GATE STATUS: PASSED\nFinished: SUCCESS\n",
    ),
]


def demo_records() -> list[BuildRecord]:
    """Build a small synthetic history ending "now"."""
    now_ms = int(time.time() * 1000)
    records: list[BuildRecord] = []
    count = len(_SAMPLES)
    for index, (number, state, duration, log) in enumerate(_SAMPLES):
        facts = parse_log_facts(log)
        record = BuildRecord(
            number=number,
            state=state,
            result_raw=state.value if state is not BuildState.IN_PROGRESS else None,
            # Spread the builds an hour apart so the trend chart is meaningful.
            timestamp_ms=now_ms - (count - index) * 3_600_000,
            duration_ms=int(duration * 1000),
            url="",
            commit=f"demo{index:02d}c0ffee1234567890abcdef1234567890abcd",
            branch="refs/remotes/origin/main",
            stages=facts.stages,
            failed_stage=facts.failed_stage,
            log_available=True,
            note="demo record - not real Jenkins data",
        )
        if state is BuildState.FAILURE:
            record.classification = classify_failure(log, facts.failed_stage)
        records.append(record)
    return records
