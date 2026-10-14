"""Rule-level tests for the failure classifier and the stage parser.

Run with::

    py -3 -m unittest discover -s tools/buildflow_analyzer/tests -v

These are deliberately narrow: they pin the classification contract (including
the "say UNKNOWN rather than guess" rule) without re-testing the Java project.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from buildflow_analyzer.analysis import analyse  # noqa: E402
from buildflow_analyzer.classifier import classify_failure, parse_log_facts  # noqa: E402
from buildflow_analyzer.jenkins_client import extract_commit  # noqa: E402
from buildflow_analyzer.models import BuildRecord, BuildState, FailureType  # noqa: E402
from buildflow_analyzer.sample_data import demo_records  # noqa: E402


class TestStageParsing(unittest.TestCase):
    def test_extracts_stages_and_skips_bookkeeping(self):
        log = (
            "[Pipeline] { (Declarative: Checkout SCM)\n"
            "[Pipeline] { (Checkout)\n"
            "[Pipeline] // stage\n"
            "[Pipeline] { (Maven Test + Coverage)\n"
            "[Pipeline] // stage\n"
            'Stage "Publish (placeholder)" skipped due to earlier failure(s)\n'
            "Finished: FAILURE\n"
        )
        facts = parse_log_facts(log)
        self.assertEqual(facts.stages, ["Checkout", "Maven Test + Coverage"])
        self.assertEqual(facts.failed_stage, "Maven Test + Coverage")
        self.assertEqual(facts.skipped_stages, ["Publish (placeholder)"])
        self.assertEqual(facts.finished_result, "FAILURE")

    def test_no_stage_information_is_not_an_error(self):
        facts = parse_log_facts("some unrelated text\nFinished: FAILURE\n")
        self.assertEqual(facts.stages, [])
        self.assertIsNone(facts.failed_stage)

    def test_skipped_trailing_stage_is_not_reported_as_the_failure(self):
        """Declarative Pipeline enters every later stage, then skips its body."""
        log = (
            "[Pipeline] { (Checkout)\n"
            "[Pipeline] // stage\n"
            "[Pipeline] { (SonarQube Quality Gate)\n"
            "[INFO] Check Quality Gate status\n"
            "[ERROR] QUALITY GATE STATUS: FAILED\n"
            "[Pipeline] // stage\n"
            "[Pipeline] { (Publish (placeholder))\n"
            'Stage "Publish (placeholder)" skipped due to earlier failure(s)\n'
            "[Pipeline] // stage\n"
            "Finished: FAILURE\n"
        )
        facts = parse_log_facts(log)
        self.assertEqual(facts.failed_stage, "SonarQube Quality Gate")
        self.assertIn("QUALITY GATE STATUS: FAILED", facts.failed_stage_output)
        # The classification must follow the real failing stage.
        result = classify_failure(log, facts.failed_stage, facts.failed_stage_output)
        self.assertEqual(result.failure_type, FailureType.SONAR_QUALITY_GATE)

    def test_unclosed_final_stage_is_used_when_build_dies_inside_it(self):
        log = (
            "[Pipeline] { (Checkout)\n"
            "[Pipeline] // stage\n"
            "[Pipeline] { (Maven Compile)\n"
            "[ERROR] COMPILATION ERROR :\n"
            "Finished: FAILURE\n"
        )
        facts = parse_log_facts(log)
        self.assertEqual(facts.failed_stage, "Maven Compile")
        self.assertIn("COMPILATION ERROR", facts.failed_stage_output)


class TestClassifier(unittest.TestCase):
    def test_git_checkout_failure(self):
        log = (
            "[Pipeline] { (Checkout)\n"
            "fatal: detected dubious ownership in repository at '/git/origin.git'\n"
            "ERROR: Error cloning remote repo 'origin'\nFinished: FAILURE\n"
        )
        result = classify_failure(log, "Checkout")
        self.assertEqual(result.failure_type, FailureType.GIT_CHECKOUT)
        self.assertIn("dubious ownership", result.excerpt)

    def test_quality_gate_failure(self):
        log = (
            "[Pipeline] { (SonarQube Quality Gate)\n"
            "[ERROR] Failed to execute goal org.sonarsource.scanner.maven:sonar-maven-plugin:4.0.0.4121:sonar"
            " on project x: QUALITY GATE STATUS: FAILED\nFinished: FAILURE\n"
        )
        result = classify_failure(log, "SonarQube Quality Gate")
        self.assertEqual(result.failure_type, FailureType.SONAR_QUALITY_GATE)

    def test_unit_test_failure(self):
        log = (
            "[Pipeline] { (Maven Test + Coverage)\n"
            "[ERROR] Tests run: 2, Failures: 1, Errors: 0, Skipped: 0\n"
            "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.2.5:test\n"
            "Finished: FAILURE\n"
        )
        result = classify_failure(log, "Maven Test + Coverage")
        self.assertEqual(result.failure_type, FailureType.UNIT_TEST)

    def test_compile_failure(self):
        log = (
            "[Pipeline] { (Maven Test + Coverage)\n"
            "[ERROR] COMPILATION ERROR :\n"
            "[ERROR] /w/src/main/java/A.java:[18,9] cannot find symbol\nFinished: FAILURE\n"
        )
        result = classify_failure(log, "Maven Test + Coverage")
        self.assertEqual(result.failure_type, FailureType.MAVEN_COMPILE)

    def test_environment_failure(self):
        log = (
            "[Pipeline] { (Maven Test + Coverage)\n"
            "[ERROR] Could not resolve dependencies for project com.devops:x:jar:1.0\n"
            "Finished: FAILURE\n"
        )
        result = classify_failure(log, "Maven Test + Coverage")
        self.assertEqual(result.failure_type, FailureType.ENVIRONMENT)

    def test_unknown_when_evidence_is_insufficient(self):
        log = "[Pipeline] { (Checkout)\nFinished: FAILURE\n"
        result = classify_failure(log, "Checkout")
        self.assertEqual(result.failure_type, FailureType.UNKNOWN)
        self.assertIn("no rule matched", result.evidence)

    def test_unknown_without_stage_or_log(self):
        result = classify_failure("", None)
        self.assertEqual(result.failure_type, FailureType.UNKNOWN)
        self.assertIn("unavailable", result.evidence)


class TestAnalysis(unittest.TestCase):
    def test_in_progress_and_aborted_are_excluded_from_success_rate(self):
        records = [
            BuildRecord(number=1, state=BuildState.SUCCESS, timestamp_ms=1000, duration_ms=10_000),
            BuildRecord(number=2, state=BuildState.FAILURE, timestamp_ms=2000, duration_ms=20_000),
            BuildRecord(number=3, state=BuildState.IN_PROGRESS, timestamp_ms=3000, duration_ms=None),
            BuildRecord(number=4, state=BuildState.ABORTED, timestamp_ms=4000, duration_ms=5_000),
        ]
        analysis = analyse(records)
        self.assertEqual(analysis.total_builds, 4)
        self.assertEqual(analysis.successes, 1)
        self.assertEqual(analysis.failures, 1)
        self.assertEqual(analysis.in_progress, 1)
        self.assertEqual(analysis.aborted, 1)
        # 1 success / (1 success + 1 failure) = 50%, NOT 25%.
        self.assertEqual(analysis.success_rate_percent, 50.0)
        # Only completed builds contribute to the average: (10 + 20) / 2 = 15.
        self.assertEqual(analysis.duration.avg_seconds, 15.0)

    def test_failure_mix_uses_classified_failures(self):
        records = [
            BuildRecord(number=1, state=BuildState.FAILURE, timestamp_ms=1000, duration_ms=1000),
            BuildRecord(number=2, state=BuildState.FAILURE, timestamp_ms=2000, duration_ms=1000),
            BuildRecord(number=3, state=BuildState.SUCCESS, timestamp_ms=3000, duration_ms=1000),
        ]
        records[0].classification = classify_failure("QUALITY GATE STATUS: FAILED", "Quality Gate")
        records[1].classification = classify_failure("", None)
        analysis = analyse(records)
        self.assertEqual(analysis.failure_counts[FailureType.SONAR_QUALITY_GATE], 1)
        self.assertEqual(analysis.unclassified_failures, 1)

    def test_trend_direction_detects_growth(self):
        records = [
            BuildRecord(number=i, state=BuildState.SUCCESS,
                        timestamp_ms=1_000_000 + i * 3_600_000, duration_ms=i * 1000)
            for i in range(1, 5)
        ]
        analysis = analyse(records)
        self.assertEqual(analysis.trend_direction, "slower")
        self.assertIsNotNone(analysis.trend_slope)

    def test_cold_start_build_is_flagged_and_explained(self):
        # #1 and #2 end almost immediately (they failed early); #3 is the first
        # build that actually ran the whole pipeline and paid for the downloads.
        durations = [0.4, 27.0, 505.0, 40.0, 21.0, 70.0, 76.0]
        records = [
            BuildRecord(number=i + 1, state=BuildState.SUCCESS,
                        timestamp_ms=1_000_000 + i * 3_600_000,
                        duration_ms=int(d * 1000))
            for i, d in enumerate(durations)
        ]
        analysis = analyse(records)
        self.assertTrue(analysis.cold_start_outlier)
        self.assertIn("#3", analysis.cold_start_note)
        self.assertIn("冷启动", analysis.cold_start_note)

    def test_no_cold_start_flag_when_durations_are_comparable(self):
        records = [
            BuildRecord(number=i, state=BuildState.SUCCESS,
                        timestamp_ms=1_000_000 + i * 3_600_000, duration_ms=70_000)
            for i in range(1, 6)
        ]
        analysis = analyse(records)
        self.assertFalse(analysis.cold_start_outlier)
        self.assertEqual(analysis.cold_start_note, "")


class TestCommitExtraction(unittest.TestCase):
    def test_reads_sha_and_branch_from_git_action(self):
        detail = {
            "actions": [
                {"_class": "hudson.model.CauseAction"},
                {
                    "_class": "hudson.plugins.git.util.BuildData",
                    "lastBuiltRevision": {
                        "SHA1": "abc123",
                        "branch": [{"name": "refs/remotes/origin/main"}],
                    },
                },
            ]
        }
        self.assertEqual(extract_commit(detail), ("abc123", "refs/remotes/origin/main"))

    def test_returns_none_when_absent(self):
        self.assertEqual(extract_commit({"actions": []}), (None, None))


class TestDemoData(unittest.TestCase):
    def test_every_demo_failure_is_classified(self):
        records = demo_records()
        failures = [r for r in records if r.state is BuildState.FAILURE]
        self.assertTrue(failures)
        for record in failures:
            self.assertIsNotNone(record.classification)
            self.assertIsNot(record.classification.failure_type, FailureType.UNKNOWN)
            self.assertTrue(record.classification.evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
