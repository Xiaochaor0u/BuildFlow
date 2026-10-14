"""Report rendering: a CSV detail file and a self-contained HTML dashboard.

Both outputs are generated from the standard library only. Text taken from
Jenkins (commit messages, log excerpts) is HTML-escaped before rendering, and no
credential is ever written into a report.
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Iterable, Optional

from .analysis import Analysis, failure_label
from .models import BuildRecord, BuildState, FailureType

CSV_COLUMNS = [
    "build_number",
    "state",
    "result_raw",
    "started_at",
    "finished_at",
    "duration_seconds",
    "commit",
    "branch",
    "failed_stage",
    "stages",
    "failure_type",
    "failure_type_label",
    "evidence",
    "error_excerpt",
    "rule",
    "log_available",
    "url",
]


def write_csv(analysis: Analysis, path: Path) -> Path:
    """One row per build, including the failure-classification detail."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in sorted(analysis.records, key=lambda r: r.number):
            writer.writerow(_csv_row(record))
    return path


def _csv_row(record: BuildRecord) -> dict:
    classification = record.classification
    failure_type = ""
    label = ""
    if record.state is BuildState.FAILURE and classification:
        failure_type = classification.failure_type.value
        label = failure_label(classification.failure_type)
    return {
        "build_number": record.number,
        "state": record.state.value,
        "result_raw": record.result_raw or "",
        "started_at": record.started_at or "",
        "finished_at": record.finished_at or "",
        "duration_seconds": record.duration_seconds if record.duration_seconds is not None else "",
        "commit": record.commit or "",
        "branch": record.branch or "",
        "failed_stage": record.failed_stage or "",
        "stages": " > ".join(record.stages),
        "failure_type": failure_type,
        "failure_type_label": label,
        "evidence": classification.evidence if classification else "",
        "error_excerpt": classification.excerpt if classification else "",
        "rule": (classification.rule or "") if classification else "",
        "log_available": "yes" if record.log_available else "no",
        "url": record.url,
    }


# --------------------------------------------------------------------------- HTML

_STATE_CLASS = {
    BuildState.SUCCESS: "ok",
    BuildState.FAILURE: "bad",
    BuildState.UNSTABLE: "warn",
    BuildState.ABORTED: "muted",
    BuildState.IN_PROGRESS: "info",
    BuildState.UNKNOWN: "muted",
}


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}m {rest:.0f}s"


def _sparkline(trend: list[dict], width: int = 720, height: int = 180) -> str:
    """Inline SVG line chart of build duration over time (no JS, no CDN)."""
    points = [t for t in trend if t.get("duration_seconds") is not None]
    if not points:
        return '<p class="muted">没有可用的耗时数据。</p>'
    if len(points) == 1:
        return (
            f'<p class="muted">只有 1 个已完成构建（#{points[0]["number"]}，'
            f'{_fmt_duration(points[0]["duration_seconds"])}），无法绘制趋势。</p>'
        )

    pad_l, pad_r, pad_t, pad_b = 52, 16, 16, 32
    values = [p["duration_seconds"] for p in points]
    low, high = min(values), max(values)
    if high == low:
        high = low + 1.0
    span = high - low

    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    step = plot_w / (len(points) - 1)

    coords = []
    for index, point in enumerate(points):
        x = pad_l + index * step
        ratio = (point["duration_seconds"] - low) / span
        y = pad_t + plot_h * (1 - ratio)
        coords.append((x, y, point))

    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in coords)

    dots = []
    for x, y, point in coords:
        cls = "dot-ok" if point["state"] == BuildState.SUCCESS.value else "dot-bad"
        title = html.escape(
            f'#{point["number"]} {point["state"]} '
            f'{_fmt_duration(point["duration_seconds"])} @ {point.get("finished_at") or ""}'
        )
        dots.append(
            f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="4">'
            f"<title>{title}</title></circle>"
        )

    grid = []
    for fraction in (0.0, 0.5, 1.0):
        y = pad_t + plot_h * fraction
        value = high - span * fraction
        grid.append(
            f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}"/>'
            f'<text class="axis" x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{_fmt_duration(value)}</text>"
        )

    labels = []
    label_every = max(1, len(points) // 12)
    for index, (x, _, point) in enumerate(coords):
        if index % label_every and index != len(coords) - 1:
            continue
        labels.append(
            f'<text class="axis" x="{x:.1f}" y="{height - 10}" text-anchor="middle">'
            f'#{point["number"]}</text>'
        )

    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="构建耗时趋势">'
        + "".join(grid)
        + f'<polyline class="line" points="{polyline}"/>'
        + "".join(dots)
        + "".join(labels)
        + "</svg>"
    )


def _bar_chart(counts: dict, total: int) -> str:
    if not counts:
        return '<p class="muted">没有已分类的失败构建。</p>'
    rows = []
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].value))
    for failure_type, count in ordered:
        share = (count * 100.0 / total) if total else 0.0
        rows.append(
            '<div class="bar-row">'
            f'<div class="bar-label">{html.escape(failure_label(failure_type))}'
            f'<span class="muted"> ({failure_type.value})</span></div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{share:.1f}%"></div></div>'
            f'<div class="bar-value">{count} · {share:.1f}%</div>'
            "</div>"
        )
    return "".join(rows)


def _build_rows(records: Iterable[BuildRecord]) -> str:
    rows = []
    for record in sorted(records, key=lambda r: r.number, reverse=True):
        cls = _STATE_CLASS.get(record.state, "muted")
        if record.state is BuildState.FAILURE and record.classification:
            failure = (
                f"{html.escape(failure_label(record.classification.failure_type))}"
                f'<div class="muted small">{html.escape(record.classification.evidence)}</div>'
                f'<div class="excerpt">{html.escape(record.classification.excerpt[:300])}</div>'
            )
        elif record.state is BuildState.FAILURE:
            failure = '<span class="muted">未分类</span>'
        else:
            failure = '<span class="muted">-</span>'

        link = (
            f'<a href="{html.escape(record.url)}" target="_blank" rel="noreferrer">'
            f"#{record.number}</a>"
            if record.url
            else f"#{record.number}"
        )
        rows.append(
            "<tr>"
            f"<td>{link}</td>"
            f'<td><span class="pill {cls}">{record.state.value}</span></td>'
            f"<td>{html.escape(record.started_at or '-')}</td>"
            f"<td>{_fmt_duration(record.duration_seconds)}</td>"
            f'<td class="mono">{html.escape(record.short_commit or "-")}</td>'
            f"<td>{html.escape(record.failed_stage or '-')}</td>"
            f"<td>{failure}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_html(analysis: Analysis, meta: dict) -> str:
    """Render the dashboard. ``meta`` carries provenance information."""
    rate = analysis.success_rate_percent
    rate_text = f"{rate}%" if rate is not None else "n/a"

    source_badge = "真实 Jenkins 数据" if meta.get("live") else "演示数据 (DEMO)"
    source_class = "ok" if meta.get("live") else "warn"

    trend_note = {
        "slower": "构建耗时呈上升趋势",
        "faster": "构建耗时呈下降趋势",
        "stable": "构建耗时基本平稳",
    }.get(analysis.trend_direction, "趋势数据不足")

    slope_text = (
        f"{analysis.trend_slope:+.2f} 秒/小时" if analysis.trend_slope is not None else "n/a"
    )

    summary_rows = [
        ("构建总次数", str(analysis.total_builds)),
        ("已完成构建", str(analysis.completed_builds)),
        ("成功次数", str(analysis.successes)),
        ("失败次数", str(analysis.failures)),
        ("不稳定次数 (UNSTABLE)", str(analysis.unstable)),
        ("构建成功率", rate_text),
        ("平均耗时（已完成）", _fmt_duration(analysis.duration.avg_seconds)),
        ("耗时中位数", _fmt_duration(analysis.duration.median_seconds)),
        ("最短耗时", _fmt_duration(analysis.duration.min_seconds)),
        ("最长耗时", _fmt_duration(analysis.duration.max_seconds)),
    ]
    excluded_rows = [
        ("进行中 (IN_PROGRESS)", analysis.in_progress),
        ("已取消 (ABORTED)", analysis.aborted),
        ("状态未知", analysis.unknown_state),
    ]

    summary_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{html.escape(k)}</div>'
        f'<div class="kpi-value">{html.escape(v)}</div></div>'
        for k, v in summary_rows
    )
    excluded_html = "".join(
        f'<div class="kpi small-kpi"><div class="kpi-label">{html.escape(k)}</div>'
        f'<div class="kpi-value">{v}</div></div>'
        for k, v in excluded_rows
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BuildFlow 构建效能报告 · {html.escape(str(meta.get('job', '')))}</title>
<style>
  :root {{ --ok:#1a7f37; --bad:#c62828; --warn:#b26a00; --muted:#6b7280; --line:#e5e7eb; --info:#0b5fa5; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:24px; font:14px/1.55 "Segoe UI",-apple-system,Roboto,"Microsoft YaHei",sans-serif;
         color:#111827; background:#f8fafc; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  h2 {{ font-size:15px; margin:28px 0 10px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
  .sub {{ color:var(--muted); font-size:12px; margin-bottom:14px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }}
  .kpi {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
  .kpi-label {{ color:var(--muted); font-size:12px; }}
  .kpi-value {{ font-size:20px; font-weight:600; margin-top:2px; }}
  .small-kpi .kpi-value {{ font-size:16px; }}
  .muted {{ color:var(--muted); }}
  .small {{ font-size:12px; }}
  .pill {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; font-weight:600;
           border:1px solid transparent; }}
  .pill.ok {{ color:var(--ok); background:#e8f5ec; border-color:#bfe3c9; }}
  .pill.bad {{ color:var(--bad); background:#fdecec; border-color:#f5c6c6; }}
  .pill.warn {{ color:var(--warn); background:#fff5e6; border-color:#f0dcb4; }}
  .pill.info {{ color:var(--info); background:#e8f1fa; border-color:#c3dcf0; }}
  .pill.muted {{ color:var(--muted); background:#f1f3f5; border-color:#e2e6ea; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600; }}
  .badge.ok {{ color:var(--ok); background:#e8f5ec; }}
  .badge.warn {{ color:var(--warn); background:#fff5e6; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; font-size:13px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:#f1f5f9; font-weight:600; font-size:12px; color:#374151; }}
  .mono {{ font-family:Consolas,"Courier New",monospace; font-size:12px; }}
  .excerpt {{ font-family:Consolas,"Courier New",monospace; font-size:11px; color:#7f1d1d;
              background:#fdf2f2; border-left:3px solid #f5c6c6; padding:4px 6px; margin-top:4px;
              white-space:pre-wrap; word-break:break-all; }}
  .chart {{ width:100%; height:auto; }}
  .chart .line {{ fill:none; stroke:#2563eb; stroke-width:2; }}
  .chart .grid {{ stroke:var(--line); stroke-width:1; }}
  .chart .axis {{ fill:var(--muted); font-size:10px; }}
  .chart .dot-ok {{ fill:var(--ok); }}
  .chart .dot-bad {{ fill:var(--bad); }}
  .bar-row {{ display:grid; grid-template-columns:230px 1fr 110px; gap:10px; align-items:center;
              margin-bottom:8px; }}
  .bar-track {{ background:#eef2f7; border-radius:6px; height:16px; overflow:hidden; }}
  .bar-fill {{ background:#c62828; height:100%; }}
  .bar-value {{ font-size:12px; color:var(--muted); text-align:right; }}
  .note {{ font-size:12px; color:var(--muted); margin-top:8px; }}
  .coldstart {{ font-size:12px; color:#7a4a00; background:#fff8e8; border-left:3px solid #f0dcb4;
                padding:8px 10px; margin-top:10px; border-radius:0 6px 6px 0; }}
  @media (max-width:720px) {{ .bar-row {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
  <h1>BuildFlow 构建效能报告</h1>
  <div class="sub">
    Job: <strong>{html.escape(str(meta.get('job', '-')))}</strong> ·
    数据来源: <span class="badge {source_class}">{html.escape(source_badge)}</span> ·
    生成时间: {html.escape(str(meta.get('generated_at', '-')))} ·
    构建范围: {html.escape(str(meta.get('range', '-')))} ·
    读取构建数: {analysis.total_builds} / 限制 {html.escape(str(meta.get('limit', '-')))} ·
    Jenkins: {html.escape(str(meta.get('jenkins_url', '-')))}
  </div>

  {f'<div class="card" style="border-color:#f0dcb4;background:#fffdf7"><strong>注意：</strong>当前报告基于演示数据（DEMO），并非真实 Jenkins 构建历史。</div>' if not meta.get('live') else ''}

  <h2>总体指标</h2>
  <div class="kpis">{summary_html}</div>
  <p class="note">成功率口径：成功次数 ÷（成功 + 失败 + 不稳定），
  进行中与已取消的构建已排除，不计入成功率。</p>
  <div class="kpis" style="margin-top:10px">{excluded_html}</div>

  <h2>构建耗时趋势</h2>
  <div class="card">
    {_sparkline(analysis.trend)}
    <p class="note">{html.escape(trend_note)} · 线性斜率 {html.escape(slope_text)}
    （绿点=成功，红点=失败）</p>
    {f'<p class="coldstart"><strong>冷启动说明：</strong>{html.escape(analysis.cold_start_note)}</p>' if analysis.cold_start_outlier else ''}
  </div>

  <h2>失败类型分布</h2>
  <div class="card">
    {_bar_chart(analysis.failure_counts, sum(analysis.failure_counts.values()))}
    <p class="note">共 {analysis.classified_failures} 次失败已分类；
    {analysis.unclassified_failures} 次未分类。分类依据见下方明细。</p>
  </div>

  <h2>构建明细</h2>
  <div class="card" style="padding:0;overflow:auto">
    <table>
      <thead><tr>
        <th>构建</th><th>状态</th><th>完成时间</th><th>耗时</th>
        <th>Commit</th><th>失败阶段</th><th>故障分类与依据</th>
      </tr></thead>
      <tbody>{_build_rows(analysis.records)}</tbody>
    </table>
  </div>

  <h2>分类规则</h2>
  <div class="card">
    <p class="note">分类器按固定优先级匹配控制台日志与失败阶段；证据不足时输出
    <code>UNKNOWN</code>，不做猜测。规则集覆盖：
    Git 拉取失败 / Maven 编译失败 / 单元测试失败 / SonarQube 质量门禁失败 /
    环境或依赖问题 / 未知原因。</p>
  </div>
</body>
</html>
"""


def write_html(analysis: Analysis, meta: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(analysis, meta), encoding="utf-8")
    return path


def write_json(analysis: Analysis, meta: dict, path: Path) -> Path:
    """Machine-readable dump, handy for regression tests and diffing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "summary": {
            "total_builds": analysis.total_builds,
            "completed_builds": analysis.completed_builds,
            "successes": analysis.successes,
            "failures": analysis.failures,
            "unstable": analysis.unstable,
            "aborted": analysis.aborted,
            "in_progress": analysis.in_progress,
            "success_rate_percent": analysis.success_rate_percent,
            "avg_duration_seconds": analysis.duration.avg_seconds,
            "median_duration_seconds": analysis.duration.median_seconds,
            "trend_direction": analysis.trend_direction,
            "trend_slope_seconds_per_hour": analysis.trend_slope,
            "cold_start_outlier": analysis.cold_start_outlier,
            "cold_start_note": analysis.cold_start_note,
        },
        "failure_mix": {
            ft.value: count for ft, count in analysis.failure_counts.items()
        },
        "builds": [_csv_row(r) for r in sorted(analysis.records, key=lambda r: r.number)],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
