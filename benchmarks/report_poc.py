from __future__ import annotations

import argparse
import csv
import html
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.poc_common import POC_RUNS_DIR  # noqa: E402


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def ok_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("status") == "ok"]


def group_rows(
    rows: list[dict[str, Any]],
    key_fields: list[str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        grouped[key].append(row)

    summaries: list[dict[str, Any]] = []
    for key, group in grouped.items():
        successful = ok_rows(group)
        pages = sum(int(row.get("expected_pages") or 0) for row in successful)
        total_seconds = sum(float(row.get("total_seconds") or 0.0) for row in successful)
        summary = {field: value for field, value in zip(key_fields, key)}
        summary.update(
            {
                "runs": len(group),
                "success": len(successful),
                "pages": pages,
                "mean_overall": round(
                    mean([float(row.get("overall_score") or 0.0) for row in successful]),
                    4,
                ),
                "mean_text_recall": round(
                    mean(
                        [
                            float(row.get("text_anchor_recall") or 0.0)
                            for row in successful
                        ]
                    ),
                    4,
                ),
                "mean_table_recall": round(
                    mean(
                        [
                            float(row.get("table_cell_recall") or 0.0)
                            for row in successful
                        ]
                    ),
                    4,
                ),
                "mean_detection_f1": round(
                    mean(
                        [
                            float(row.get("table_detection_f1") or 0.0)
                            for row in successful
                        ]
                    ),
                    4,
                ),
                "total_seconds": round(total_seconds, 3),
                "seconds_per_page": round(total_seconds / pages, 3) if pages else 0.0,
                "estimated_cost_usd": round(
                    sum(float(row.get("estimated_cost_usd") or 0.0) for row in group),
                    6,
                ),
            }
        )
        summaries.append(summary)
    return summaries


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def summarize_by_case(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    case_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ok_rows(rows):
        case_groups[str(row.get("case_id"))].append(row)

    summaries: list[dict[str, Any]] = []
    for case_id, group in sorted(case_groups.items()):
        best_accuracy = max(group, key=lambda row: float(row.get("overall_score") or 0.0))
        best_speed = min(group, key=lambda row: float(row.get("seconds_per_page") or 999999.0))
        summaries.append(
            {
                "case_id": case_id,
                "filename": best_accuracy.get("filename"),
                "tags": ", ".join(best_accuracy.get("tags", [])),
                "best_accuracy_config": best_accuracy.get("config_id"),
                "best_accuracy_pipeline": best_accuracy.get("pipeline"),
                "best_accuracy_overall": best_accuracy.get("overall_score"),
                "best_accuracy_seconds_per_page": best_accuracy.get("seconds_per_page"),
                "fastest_config": best_speed.get("config_id"),
                "fastest_pipeline": best_speed.get("pipeline"),
                "fastest_overall": best_speed.get("overall_score"),
                "fastest_seconds_per_page": best_speed.get("seconds_per_page"),
            }
        )
    return summaries


def summarize_by_tag(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ok_rows(rows):
        for tag in row.get("tags", []):
            grouped[(str(tag), str(row.get("pipeline")))].append(row)

    summaries: list[dict[str, Any]] = []
    for (tag, pipeline), group in sorted(grouped.items()):
        pages = sum(int(row.get("expected_pages") or 0) for row in group)
        total_seconds = sum(float(row.get("total_seconds") or 0.0) for row in group)
        summaries.append(
            {
                "tag": tag,
                "pipeline": pipeline,
                "runs": len(group),
                "pages": pages,
                "mean_overall": round(
                    mean([float(row.get("overall_score") or 0.0) for row in group]), 4
                ),
                "seconds_per_page": round(total_seconds / pages, 3) if pages else 0.0,
            }
        )
    return summaries


def build_hybrid_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = ok_rows(rows)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in successful:
        by_case[str(row.get("case_id"))].append(row)

    selected: list[dict[str, Any]] = []
    for case_id, group in by_case.items():
        standard_rows = [row for row in group if row.get("pipeline") == "standard"]
        vlm_rows = [row for row in group if row.get("pipeline") == "vlm"]
        if not standard_rows:
            continue
        baseline = next(
            (
                row
                for row in standard_rows
                if row.get("config_id") == "std_baseline_accurate"
            ),
            max(standard_rows, key=lambda row: float(row.get("overall_score") or 0.0)),
        )
        choice = baseline
        if baseline.get("low_confidence") and vlm_rows:
            best_vlm = max(vlm_rows, key=lambda row: float(row.get("overall_score") or 0.0))
            if float(best_vlm.get("overall_score") or 0.0) >= float(
                baseline.get("overall_score") or 0.0
            ):
                choice = best_vlm
        selected.append(
            {
                "case_id": case_id,
                "selected_pipeline": choice.get("pipeline"),
                "selected_config": choice.get("config_id"),
                "overall_score": choice.get("overall_score"),
                "seconds_per_page": choice.get("seconds_per_page"),
                "baseline_low_confidence": baseline.get("low_confidence"),
            }
        )

    return {
        "selected": selected,
        "case_count": len(selected),
        "mean_overall": round(
            mean([float(row.get("overall_score") or 0.0) for row in selected]), 4
        ),
        "mean_seconds_per_page": round(
            mean([float(row.get("seconds_per_page") or 0.0) for row in selected]), 3
        ),
        "vlm_selected_cases": [
            row["case_id"] for row in selected if row["selected_pipeline"] == "vlm"
        ],
    }


def markdown_table(rows: list[dict[str, Any]], fields: list[str]) -> list[str]:
    if not rows:
        return ["_No data._"]
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    for row in rows:
        values = [str(row.get(field, "")).replace("|", "\\|") for field in fields]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def write_html_report(path: Path, markdown: str) -> None:
    body = html.escape(markdown)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Docling PoC Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 32px auto; line-height: 1.5; }}
    pre {{ white-space: pre-wrap; background: #f7f7f7; padding: 20px; border: 1px solid #ddd; }}
  </style>
</head>
<body>
<pre>{body}</pre>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def build_report(run_dir: Path) -> int:
    results_path = run_dir / "results.json"
    if not results_path.exists():
        print(f"Missing results file: {results_path}", file=sys.stderr)
        return 1

    payload = json.loads(results_path.read_text(encoding="utf-8"))
    rows = list(payload.get("results", []))
    config_summary = sorted(
        group_rows(rows, ["pipeline", "config_id"]),
        key=lambda row: (
            -float(row.get("mean_overall") or 0.0),
            float(row.get("seconds_per_page") or 999999.0),
        ),
    )
    model_summary = sorted(
        group_rows(
            [
                {
                    **row,
                    "model": row.get("settings", {}).get("model"),
                    "response_format": row.get("settings", {}).get("response_format"),
                    "scale": row.get("settings", {}).get("scale"),
                    "reasoning_effort": row.get("settings", {}).get(
                        "reasoning_effort"
                    ),
                    "prompt_variant": row.get("settings", {}).get("prompt_variant"),
                }
                for row in rows
                if row.get("pipeline") == "vlm"
            ],
            ["model", "response_format", "scale", "reasoning_effort", "prompt_variant"],
        ),
        key=lambda row: (
            -float(row.get("mean_overall") or 0.0),
            float(row.get("seconds_per_page") or 999999.0),
        ),
    )
    case_summary = summarize_by_case(rows)
    tag_summary = summarize_by_tag(rows)
    hybrid = build_hybrid_summary(rows)

    write_csv(run_dir / "summary_by_config.csv", config_summary)
    write_csv(run_dir / "summary_by_vlm_setting.csv", model_summary)
    write_csv(run_dir / "summary_by_case.csv", case_summary)
    write_csv(run_dir / "summary_by_tag.csv", tag_summary)
    (run_dir / "hybrid_summary.json").write_text(
        json.dumps(hybrid, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    successful = ok_rows(rows)
    measured_pipelines = sorted({str(row.get("pipeline")) for row in rows})
    measured_phases = sorted({str(row.get("phase")) for row in rows})
    total_pages = sum(int(row.get("expected_pages") or 0) for row in successful)
    total_seconds = sum(float(row.get("total_seconds") or 0.0) for row in successful)
    total_cost = sum(float(row.get("estimated_cost_usd") or 0.0) for row in rows)
    budget = payload.get("budget_estimate") or {}

    lines = [
        "# Docling/OpenAI VLM PoC Report",
        "",
        "## Summary",
        "",
        f"- Run ID: `{payload.get('run_id')}`",
        f"- Latest command: matrix `{payload.get('matrix')}` / phase `{payload.get('phase')}`",
        f"- Measured pipelines: {', '.join(measured_pipelines)}",
        f"- Measured phases: {', '.join(measured_phases)}",
        f"- Rows: {len(rows)} ({len(successful)} successful)",
        f"- Successful pages measured: {total_pages}",
        f"- Total measured time: {total_seconds:.3f} sec",
        f"- Estimated OpenAI cost from measured rows: ${total_cost:.4f}",
        f"- Fixed VLM max completion tokens: {payload.get('fixed_max_completion_tokens')}",
        "",
        "## Budget Gate",
        "",
        f"- Budget: ${payload.get('budget_usd')}",
        f"- Estimate status: `{budget.get('status', 'not_available')}`",
        f"- Estimated full VLM cost: ${budget.get('estimated_full_cost_usd', 0.0)}",
        f"- Estimated additional VLM cost: ${budget.get('estimated_additional_cost_usd', 0.0)}",
        f"- Full phase recommendation: `{budget.get('full_phase_recommendation', 'not_available')}`",
        "",
        "## Best Configurations",
        "",
        *markdown_table(
            config_summary[:12],
            [
                "pipeline",
                "config_id",
                "success",
                "mean_overall",
                "mean_table_recall",
                "mean_detection_f1",
                "seconds_per_page",
                "estimated_cost_usd",
            ],
        ),
        "",
        "## VLM Setting Summary",
        "",
        *markdown_table(
            model_summary[:20],
            [
                "model",
                "response_format",
                "scale",
                "reasoning_effort",
                "prompt_variant",
                "success",
                "mean_overall",
                "seconds_per_page",
                "estimated_cost_usd",
            ],
        ),
        "",
        "## Case-Level Fit",
        "",
        *markdown_table(
            case_summary,
            [
                "case_id",
                "tags",
                "best_accuracy_pipeline",
                "best_accuracy_config",
                "best_accuracy_overall",
                "best_accuracy_seconds_per_page",
                "fastest_pipeline",
                "fastest_config",
                "fastest_seconds_per_page",
            ],
        ),
        "",
        "## Tag-Level Tendencies",
        "",
        *markdown_table(
            tag_summary,
            ["tag", "pipeline", "runs", "mean_overall", "seconds_per_page"],
        ),
        "",
        "## Hybrid Selection",
        "",
        f"- Cases selected: {hybrid['case_count']}",
        f"- Mean overall: {hybrid['mean_overall']}",
        f"- Mean seconds/page: {hybrid['mean_seconds_per_page']}",
        f"- VLM selected cases: {', '.join(hybrid['vlm_selected_cases']) or 'none'}",
        "",
        "## Output Files",
        "",
        "- `results.json` / `results.csv`: raw run rows",
        "- `summary_by_config.csv`: pipeline/config aggregate",
        "- `summary_by_vlm_setting.csv`: VLM parameter aggregate",
        "- `summary_by_case.csv`: best and fastest setting by case",
        "- `summary_by_tag.csv`: tag-level tendency summary",
        "- `hybrid_summary.json`: case-level hybrid selection",
    ]
    markdown = "\n".join(lines)
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    write_html_report(run_dir / "report.html", markdown)
    print(f"Wrote {run_dir / 'report.md'}")
    print(f"Wrote {run_dir / 'report.html'}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a PoC report for a run.")
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(build_report(POC_RUNS_DIR / args.run_id))
