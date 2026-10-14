"""Rule-based build failure classification.

Evidence is collected in a fixed priority order and the first matching rule
wins. When nothing matches, the result is ``UNKNOWN`` — the classifier never
guesses a cause it cannot support with evidence.

Two evidence sources are used:

1. **Pipeline stage state.** Jenkins' stage REST API is provided by a plugin that
   is not installed, so stage boundaries are recovered from the console log
   markers that declarative Pipeline always emits::

       [Pipeline] { (Maven Test + Coverage)
       ...
       Stage "Publish (placeholder)" skipped due to earlier failure(s)

2. **Console log text.** The first high-signal error line is captured as the
   excerpt so a human can verify the verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .models import Classification, FailureType

# --------------------------------------------------------------------------- stage parsing

_STAGE_START = re.compile(r"^\[Pipeline\]\s*\{\s*\((.*?)\)\s*$", re.MULTILINE)
_STAGE_SKIP = re.compile(
    r'^Stage\s+"(?P<name>[^"]+)"\s+skipped due to earlier failure', re.MULTILINE
)
# Any "skipped" notice inside a stage body means Jenkins entered the stage but
# never executed it.
_SKIPPED_BODY = re.compile(r"skipped due to earlier failure")
_STAGE_END = re.compile(r"^\[Pipeline\]\s*//\s*stage", re.MULTILINE)
_FINISHED = re.compile(r"^Finished:\s*(?P<result>\w+)", re.MULTILINE)


@dataclass
class LogFacts:
    """Everything the classifier and the report need from one console log."""

    stages: list[str]
    failed_stage: Optional[str]
    skipped_stages: list[str]
    finished_result: Optional[str]
    failed_stage_output: str = ""


# A stage body runs from "[Pipeline] { (Name)" to the matching "// stage".
_STAGE_BLOCK = re.compile(
    r"^\[Pipeline\]\s*\{\s*\((?P<name>.*?)\)\s*$\n(?P<body>.*?)^\[Pipeline\]\s*//\s*stage\s*$",
    re.MULTILINE | re.DOTALL,
)


def parse_log_facts(log: str) -> LogFacts:
    """Recover stage names, the failing stage, and that stage's output.

    Declarative Pipeline *enters* every remaining stage even after a failure and
    then skips its body, so the last stage header in the log is usually a skipped
    stage rather than the one that failed::

        [Pipeline] { (Maven Test)
        Stage "Maven Test" skipped due to earlier failure(s)

    The failing stage is therefore the last stage that actually ran, i.e. the one
    whose body contains more than the "skipped" notice.
    """
    if not log:
        return LogFacts([], None, [], None, "")

    stages: list[str] = []
    for name in _STAGE_START.findall(log):
        name = name.strip()
        # Declarative bookkeeping stages are noise, not build stages.
        if name.startswith("Declarative:"):
            continue
        if name not in stages:
            stages.append(name)

    skipped = [m.group("name").strip() for m in _STAGE_SKIP.finditer(log)]

    finished_match = _FINISHED.search(log)
    finished = finished_match.group("result") if finished_match else None

    failed_stage: Optional[str] = None
    failed_block = ""
    if finished in ("FAILURE", "ABORTED", "UNSTABLE"):
        finish_pos = finished_match.start() if finished_match else len(log)
        prefix = log[:finish_pos]

        # Collect every closed stage block, keyed by start offset so the last
        # stage that really executed can be selected.
        executed: list[tuple[int, str, str]] = []
        closed_ends: list[int] = []
        for match in _STAGE_BLOCK.finditer(prefix):
            name = match.group("name").strip()
            if name.startswith("Declarative:"):
                continue
            closed_ends.append(match.end())
            body = match.group("body")
            if _SKIPPED_BODY.search(body):
                continue  # Jenkins entered but never ran this stage
            executed.append((match.start(), name, body))

        headers = [
            (m.start(), m.group(1).strip())
            for m in _STAGE_START.finditer(prefix)
            if not m.group(1).startswith("Declarative:")
        ]

        # A stage header that never got its "// stage" marker is the stage the
        # build died inside — the strongest signal available.
        unclosed = [
            (pos, name)
            for pos, name in headers
            if not any(end > pos for end in closed_ends)
        ]

        if unclosed:
            pos, failed_stage = unclosed[-1]
            failed_block = prefix[pos:]
        elif executed:
            executed.sort(key=lambda item: item[0])
            _, failed_stage, failed_block = executed[-1]
        elif stages:
            # No closed block (the build died inside a stage that never closed).
            failed_stage = stages[-1]
            fallback = re.search(
                r"^\[Pipeline\]\s*\{\s*\(" + re.escape(failed_stage) + r"\)\s*$",
                prefix,
                re.MULTILINE,
            )
            if fallback:
                failed_block = prefix[fallback.end():]

    return LogFacts(stages, failed_stage, skipped, finished, failed_block)


def _tail(log: str, limit: int = 30_000) -> str:
    """Work on the tail: late output holds the fatal error, not the log header.

    The window is generous because Jenkins prints some errors (notably
    "No such DSL method ... found among steps") with a multi-kilobyte list of
    available steps, which would otherwise push the message out of a small window.
    """
    return log[-limit:] if len(log) > limit else log


def _first_match(log: str, patterns: Iterable[re.Pattern[str]]) -> Optional[re.Match]:
    best: Optional[re.Match] = None
    for pattern in patterns:
        match = pattern.search(log)
        if match and (best is None or match.start() < best.start()):
            best = match
    return best


def _line_at(log: str, position: int) -> str:
    start = log.rfind("\n", 0, position) + 1
    end = log.find("\n", position)
    if end == -1:
        end = len(log)
    return log[start:end].strip()


# --------------------------------------------------------------------------- rules

_RULE_GIT = [
    re.compile(r"detected dubious ownership in repository"),
    re.compile(r"fatal: could not read from remote repository"),
    re.compile(r"fatal: unable to access .*?(?:repository|remote)"),
    re.compile(r"fatal: repository '.*?' (?:not found|does not exist)"),
    re.compile(r"fatal: Authentication failed for"),
    re.compile(r"ERROR: Error cloning remote repo"),
    re.compile(r"hudson\.plugins\.git\.GitException"),
    re.compile(r"Permission denied \(publickey\)"),
]

_RULE_QUALITY_GATE = [
    re.compile(r"QUALITY GATE STATUS:\s*FAILED"),
    re.compile(r"Quality Gate.*?status.*?ERROR", re.IGNORECASE),
    re.compile(r"Failed to execute goal .*sonar-maven-plugin.*quality gate", re.IGNORECASE),
]

_RULE_UNIT_TEST = [
    re.compile(r"Tests run:.*?Failures:\s*[1-9]\d*"),
    re.compile(r"Tests run:.*?Errors:\s*[1-9]\d*"),
    re.compile(r"^\[ERROR\]\s+Tests run:.*?Failures: [1-9]", re.MULTILINE),
    re.compile(r"TEST FAILURE|SUREFIRE.*?There are test failures", re.IGNORECASE),
    re.compile(r"Failed to execute goal org\.apache\.maven\.plugins:maven-surefire-plugin"),
]

_RULE_MAVEN_COMPILE = [
    re.compile(r"COMPILATION ERROR"),
    re.compile(r"Failed to execute goal org\.apache\.maven\.plugins:maven-compiler-plugin"),
    re.compile(r"\[ERROR\]\s*/.*?\.java:\[\d+,\d+\]" ),
    re.compile(r"cannot find symbol", re.IGNORECASE),
    re.compile(r"invalid target release"),
    re.compile(r"Fatal error compiling"),
]

_RULE_ENVIRONMENT = [
    re.compile(r"Could not resolve dependencies for project"),
    re.compile(r"Could not transfer artifact"),
    re.compile(r"Could not find artifact"),
    re.compile(r"Non-resolvable parent POM"),
    re.compile(r"UnknownHostException"),
    re.compile(r"Connection refused"),
    re.compile(r"No such file or directory"),
    re.compile(r"\./?mvnw: \d+: Syntax error"),
    re.compile(r"\./?mvnw: not found|mvnw: Permission denied"),
    re.compile(r"script returned exit code (?:1|2|126|127)\b"),
    re.compile(r"command not found"),
    re.compile(r"docker: not found|Cannot connect to the Docker daemon"),
    re.compile(r"ERROR: No plugin found for prefix"),
    re.compile(r"invalid target release"),
    re.compile(r"out of memory|Java heap space", re.IGNORECASE),
    re.compile(r"Timeout has been exceeded"),
    # Pipeline/agent environment problems raised by Jenkins itself rather than by the
    # build tooling: an unavailable step, a missing configuration value, etc.
    re.compile(r"No such DSL method '[^']+' found among steps"),
    re.compile(r"Root URL isn't configured yet"),
    re.compile(r"MissingPropertyException|No such property:"),
    re.compile(r"java\.lang\.IllegalStateException"),
]

# Signals that a stage failed for a reason the tool cannot identify. When one of
# these is the *only* thing in the failing stage, the verdict is UNKNOWN.
_RULE_ENVIRONMENT_LOW_CONFIDENCE = [
    re.compile(r"script returned exit code (?:1|2|126|127)\b"),
]

# The failing stage narrows the search space considerably and is the preferred
# evidence when it is available.
_STAGE_GIT_HINTS = ("checkout", "clone", "scm", "git")
_STAGE_TEST_HINTS = ("test", "surefire", "unit")
_STAGE_GATE_HINTS = ("quality gate", "sonar")
_STAGE_COMPILE_HINTS = ("compile", "build", "package")


def _stage_hint(failed_stage: Optional[str]) -> Optional[FailureType]:
    if not failed_stage:
        return None
    name = failed_stage.lower()
    if any(h in name for h in _STAGE_GATE_HINTS):
        return FailureType.SONAR_QUALITY_GATE
    if any(h in name for h in _STAGE_TEST_HINTS):
        return FailureType.UNIT_TEST
    if any(h in name for h in _STAGE_GIT_HINTS):
        return FailureType.GIT_CHECKOUT
    if any(h in name for h in _STAGE_COMPILE_HINTS):
        return FailureType.MAVEN_COMPILE
    return None


def _has_error_signal(log: str) -> bool:
    """True when the log carries an actual error line, not just Pipeline chatter.

    Stage-name inference is only allowed on top of *some* real error evidence;
    otherwise a log containing nothing but ``[Pipeline]`` markers would be
    categorised purely from the stage label, which would be a guess.
    """
    if not log:
        return False
    if _ERROR_LINE.search(log):
        return True
    # Maven/Git failures occasionally print a bare "fatal:" line without a prefix.
    return any(
        pattern.search(log)
        for pattern in (
            re.compile(r"^\s*fatal:", re.MULTILINE),
            re.compile(r"^\s*(?:error|failed)\b", re.MULTILINE | re.IGNORECASE),
        )
    )


def classify_failure(
    log: str,
    failed_stage: Optional[str] = None,
    failed_stage_output: str = "",
) -> Classification:
    """Classify one failed build.

    Evidence is searched in the failing stage's own output first, because the end
    of a console log usually contains only Pipeline clean-up. When no stage output
    is available the whole log is used.

    ``log`` may be empty; the classifier then falls back to the stage name when
    there is at least some error output, and to ``UNKNOWN`` otherwise.
    """
    block = _tail(failed_stage_output) if failed_stage_output else ""
    tail = block if block else (_tail(log) if log else "")
    scope = "failing stage output" if block else "console log"

    # Jenkins prints a Groovy/Jenkins exception *after* the stage block is closed,
    # so a supplementary window over the end of the whole log is searched too.
    # It is reported separately so the evidence stays traceable.
    full_tail = _tail(log) if log else ""

    # --- 1. explicit, unambiguous console signatures -------------------------
    for failure_type, patterns in (
        (FailureType.SONAR_QUALITY_GATE, _RULE_QUALITY_GATE),
        (FailureType.GIT_CHECKOUT, _RULE_GIT),
        (FailureType.UNIT_TEST, _RULE_UNIT_TEST),
        (FailureType.MAVEN_COMPILE, _RULE_MAVEN_COMPILE),
        (FailureType.ENVIRONMENT, _RULE_ENVIRONMENT),
    ):
        match = _first_match(tail, patterns)
        used_scope = scope
        if not match and full_tail and full_tail != tail:
            match = _first_match(full_tail, patterns)
            used_scope = "end of console log"
        if match:
            haystack = tail if used_scope == scope else full_tail
            return Classification(
                failure_type=failure_type,
                evidence=f"{used_scope} matched /{match.re.pattern}/",
                excerpt=_line_at(haystack, match.start())[:400],
                stage=failed_stage,
                rule=match.re.pattern,
            )

    # --- 1b. weak signals: only meaningful together with the failing stage ---
    # A bare "[ERROR: script returned exit code N]" proves the shell failed but
    # says nothing about why, so it never decides a category on its own.
    weak = _first_match(tail, _RULE_ENVIRONMENT_LOW_CONFIDENCE)
    if weak and failed_stage and _stage_hint(failed_stage) is None:
        return Classification(
            failure_type=FailureType.ENVIRONMENT,
            evidence=(
                f"stage '{failed_stage}' ended with a non-zero shell exit code and no "
                f"recognised build-tool failure"
            ),
            excerpt=_line_at(tail, weak.start())[:400],
            stage=failed_stage,
            rule=weak.re.pattern,
        )

    # --- 2. narrow rules guided by the failing stage ------------------------
    if failed_stage:
        # A failed stage plus a Maven "Failed to execute goal" line naming that
        # concern is strong evidence even without the exact strings above.
        goal = re.search(r"Failed to execute goal ([^\s:]+)", tail)
        if goal:
            artifact = goal.group(1).lower()
            if "surefire" in artifact:
                ft = FailureType.UNIT_TEST
            elif "compiler" in artifact:
                ft = FailureType.MAVEN_COMPILE
            elif "sonar" in artifact:
                ft = FailureType.SONAR_QUALITY_GATE
            elif "deploy" in artifact or "install" in artifact:
                ft = FailureType.ENVIRONMENT
            else:
                ft = None
            if ft:
                return Classification(
                    failure_type=ft,
                    evidence=f"stage '{failed_stage}' failed at Maven goal {goal.group(1)}",
                    excerpt=_line_at(tail, goal.start())[:400],
                    stage=failed_stage,
                    rule="maven-goal-name",
                )

        hint = _stage_hint(failed_stage)
        if hint and _has_error_signal(tail):
            return Classification(
                failure_type=hint,
                evidence=(
                    f"no decisive error line in the log; inferred from the failing "
                    f"stage '{failed_stage}' plus error output"
                ),
                excerpt=_first_error_line(log) or "",
                stage=failed_stage,
                rule="failed-stage-name",
            )

    # --- 3. give up honestly -------------------------------------------------
    excerpt = _first_error_line(log) if log else ""
    evidence = (
        "no rule matched the available log output"
        if log
        else "console log unavailable and no stage information"
    )
    return Classification(
        failure_type=FailureType.UNKNOWN,
        evidence=evidence,
        excerpt=excerpt,
        stage=failed_stage,
        rule=None,
    )


_ERROR_LINE = re.compile(
    r"^(?:\[ERROR\]|ERROR:|fatal:|Exception|Caused by:|\[FATAL\])", re.MULTILINE
)


def _first_error_line(log: str, limit: int = 400) -> str:
    """First high-signal error line, preferring the tail of the log."""
    tail = _tail(log) if log else ""
    match = _ERROR_LINE.search(tail)
    if match:
        return _line_at(tail, match.start())[:limit]
    return ""
