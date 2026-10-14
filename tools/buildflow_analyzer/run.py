"""Command-line entry point for the BuildFlow build analyzer.

Examples
--------
Read the real Jenkins history and write reports::

    py -3 tools/buildflow_analyzer/run.py --limit 50

Point at another job or server without touching code::

    $env:JENKINS_URL  = "http://127.0.0.1:8080"
    $env:JENKINS_USER = "admin"
    $env:JENKINS_TOKEN = "..."
    py -3 tools/buildflow_analyzer/run.py --job another-job --limit 100 --outdir out

Classify a saved console log (offline, no Jenkins needed)::

    py -3 tools/buildflow_analyzer/run.py --classify-log failure.log

Write a clearly-labelled DEMO report instead of reading Jenkins::

    py -3 tools/buildflow_analyzer/run.py --demo
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Allow `py -3 tools/buildflow_analyzer/run.py` without installing a package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from buildflow_analyzer import __version__
    from buildflow_analyzer.analysis import analyse
    from buildflow_analyzer.classifier import classify_failure, parse_log_facts
    from buildflow_analyzer.jenkins_client import (
        Config,
        JenkinsClient,
        JenkinsError,
        extract_commit,
    )
    from buildflow_analyzer.models import BuildRecord, BuildState, FailureType
    from buildflow_analyzer.reporting import write_csv, write_html, write_json
    from buildflow_analyzer.sample_data import demo_records
else:  # pragma: no cover - package-style import
    from . import __version__
    from .analysis import analyse
    from .classifier import classify_failure, parse_log_facts
    from .jenkins_client import Config, JenkinsClient, JenkinsError, extract_commit
    from .models import BuildRecord, BuildState, FailureType
    from .reporting import write_csv, write_html, write_json
    from .sample_data import demo_records


_RESULT_TO_STATE = {
    "SUCCESS": BuildState.SUCCESS,
    "FAILURE": BuildState.FAILURE,
    "ABORTED": BuildState.ABORTED,
    "UNSTABLE": BuildState.UNSTABLE,
    None: BuildState.IN_PROGRESS,
    "": BuildState.IN_PROGRESS,
}


def _state_from(result, building: bool) -> BuildState:
    if building:
        return BuildState.IN_PROGRESS
    if result is None:
        return BuildState.IN_PROGRESS
    return _RESULT_TO_STATE.get(str(result).upper(), BuildState.UNKNOWN)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buildflow-analyzer",
        description=(
            "读取 Jenkins 真实构建历史，做规则式故障分类与研发效能统计，"
            "输出 CSV 与 HTML 报告。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "认证信息从环境变量读取，不写入任何文件：\n"
            "  JENKINS_URL / JENKINS_USER / JENKINS_TOKEN / JENKINS_JOB / JENKINS_BUILD_LIMIT\n"
        ),
    )
    parser.add_argument("--url", help="Jenkins 地址，如 http://127.0.0.1:8080（默认取 JENKINS_URL）")
    parser.add_argument("--job", help="Job 名称，如 buildflow-ci（默认取 JENKINS_JOB）")
    parser.add_argument("--user", help="Jenkins 用户名（默认取 JENKINS_USER）")
    parser.add_argument("--token", help="API Token（默认取 JENKINS_TOKEN，建议用环境变量避免留痕）")
    parser.add_argument("--limit", type=int, help="读取最近多少个构建（默认取 JENKINS_BUILD_LIMIT 或 50）")
    parser.add_argument("--timeout", type=int, default=30, help="单次请求超时秒数（默认 30）")
    parser.add_argument("--outdir", default="reports", help="报告输出目录（默认 reports）")
    parser.add_argument("--no-logs", action="store_true", help="不读取控制台日志（更快，但分类精度下降）")
    parser.add_argument("--insecure", action="store_true", help="跳过 TLS 证书校验")
    parser.add_argument("--demo", action="store_true", help="使用演示数据生成报告（报告会明确标注为 DEMO）")
    parser.add_argument("--classify-log", metavar="FILE", help="离线分类一个控制台日志文件，不访问 Jenkins")
    parser.add_argument("--version", action="version", version=f"buildflow-analyzer {__version__}")
    return parser


def cmd_classify_log(path: Path) -> int:
    if not path.is_file():
        print(f"[ERROR] 日志文件不存在: {path}", file=sys.stderr)
        return 2
    log = path.read_text(encoding="utf-8", errors="replace")
    facts = parse_log_facts(log)
    classification = classify_failure(log, facts.failed_stage, facts.failed_stage_output)
    print(f"日志文件      : {path}")
    print(f"识别阶段      : {', '.join(facts.stages) or '(无)'}")
    print(f"失败阶段      : {facts.failed_stage or '(未识别)'}")
    print(f"被跳过阶段    : {', '.join(facts.skipped_stages) or '(无)'}")
    print(f"Finished      : {facts.finished_result or '(未识别)'}")
    print(f"故障类型      : {classification.failure_type.value}")
    print(f"判断依据      : {classification.evidence}")
    print(f"命中规则      : {classification.rule or '(无)'}")
    if classification.excerpt:
        print(f"错误摘要      : {classification.excerpt}")
    return 0


def collect(client: JenkinsClient, *, fetch_logs: bool) -> list[BuildRecord]:
    """Read build history and classify every failure."""
    records: list[BuildRecord] = []
    summaries = client.builds()
    total = len(summaries)
    for index, summary in enumerate(summaries, start=1):
        number = summary.get("number")
        if number is None:
            continue
        building = bool(summary.get("building"))
        state = _state_from(summary.get("result"), building)

        record = BuildRecord(
            number=int(number),
            state=state,
            result_raw=summary.get("result"),
            timestamp_ms=summary.get("timestamp"),
            duration_ms=summary.get("duration"),
            url=summary.get("url") or "",
        )

        # Commit info lives in the build detail; it is cheap, so fetch it always.
        try:
            detail = client.build_detail(record.number)
            commit, branch = extract_commit(detail)
            record.commit = commit
            record.branch = branch
        except JenkinsError as exc:
            record.note = f"detail unavailable: {exc}"

        if state is BuildState.FAILURE:
            log = ""
            if fetch_logs:
                try:
                    log = client.console_text(record.number)
                    record.log_available = bool(log)
                except JenkinsError as exc:
                    record.note = (record.note + " | " if record.note else "") + f"log unavailable: {exc}"
            facts = parse_log_facts(log)
            record.stages = facts.stages
            record.failed_stage = facts.failed_stage
            record.classification = classify_failure(
                log, facts.failed_stage, facts.failed_stage_output
            )
        elif fetch_logs:
            # Stage list is useful context even for successful builds.
            try:
                log = client.console_text(record.number)
                record.log_available = bool(log)
                record.stages = parse_log_facts(log).stages
            except JenkinsError:
                pass

        records.append(record)
        marker = ""
        if record.classification:
            marker = f" -> {record.classification.failure_type.value}"
        print(f"  [{index}/{total}] #{record.number:<4} {record.state.value:<11}{marker}")

    return records


def _meta(*, live: bool, job: str, url: str, limit: int, records: list[BuildRecord]) -> dict:
    numbers = [r.number for r in records]
    if numbers:
        build_range = f"#{min(numbers)} - #{max(numbers)}"
    else:
        build_range = "(无构建)"
    return {
        "live": live,
        "job": job,
        "jenkins_url": url,
        "limit": limit,
        "range": build_range,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tool_version": __version__,
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.classify_log:
        return cmd_classify_log(Path(args.classify_log))

    outdir = Path(args.outdir)

    # ---------------------------------------------------------------- demo mode
    if args.demo:
        records = demo_records()
        analysis = analyse(records)
        meta = _meta(live=False, job="demo-project", url="(demo)", limit=len(records), records=records)
        csv_path = write_csv(analysis, outdir / "buildflow-builds.csv")
        html_path = write_html(analysis, meta, outdir / "buildflow-report.html")
        json_path = write_json(analysis, meta, outdir / "buildflow-summary.json")
        print("[DEMO] 使用演示数据生成报告（非真实 Jenkins 数据）")
        print(f"  CSV : {csv_path}")
        print(f"  HTML: {html_path}")
        print(f"  JSON: {json_path}")
        print(f"  构建数={analysis.total_builds} 成功率={analysis.success_rate_percent}%")
        return 0

    # ---------------------------------------------------------------- live mode
    try:
        config = Config.from_env(
            url=args.url,
            job=args.job,
            user=args.user,
            token=args.token,
            limit=args.limit,
            timeout=args.timeout,
            verify_tls=not args.insecure,
            fetch_logs=not args.no_logs,
        )
    except JenkinsError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print(f"[INFO] 读取 Jenkins: {config.describe()}")
    client = JenkinsClient(config)
    try:
        records = collect(client, fetch_logs=config.fetch_logs)
    except JenkinsError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not records:
        print("[WARN] 未读取到任何构建记录。")

    analysis = analyse(records)
    meta = _meta(live=True, job=config.job, url=config.url, limit=config.limit, records=records)

    csv_path = write_csv(analysis, outdir / "buildflow-builds.csv")
    html_path = write_html(analysis, meta, outdir / "buildflow-report.html")
    json_path = write_json(analysis, meta, outdir / "buildflow-summary.json")

    print()
    print(f"构建总次数      : {analysis.total_builds}")
    print(f"已完成构建      : {analysis.completed_builds}")
    print(f"成功 / 失败     : {analysis.successes} / {analysis.failures}")
    rate = analysis.success_rate_percent
    print(f"构建成功率      : {rate}%" if rate is not None else "构建成功率      : n/a")
    print(f"平均耗时        : {analysis.duration.avg_seconds}s")
    print(f"耗时趋势        : {analysis.trend_direction} ({analysis.trend_slope} 秒/小时)")
    print(f"排除项          : 进行中={analysis.in_progress} 已取消={analysis.aborted} 未知={analysis.unknown_state}")
    if analysis.failure_counts:
        print("失败分类        :")
        for failure_type, count in analysis.failure_counts.most_common():
            print(f"  {failure_type.value:<20} {count} ({analysis.failure_shares.get(failure_type)}%)")
    if analysis.unclassified_failures:
        print(f"未分类失败      : {analysis.unclassified_failures}")
    print()
    print(f"CSV  : {csv_path}")
    print(f"HTML : {html_path}")
    print(f"JSON : {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
