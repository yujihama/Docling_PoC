from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from settings import load_settings  # noqa: E402

SETTINGS = load_settings()
SUITE_DIR = SETTINGS.outputs.root / "pdf_stress_suite"
RUNS_DIR = SETTINGS.outputs.routing_runs_dir
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def compact_text(value: str) -> str:
    normalized = re.sub(r"\s+", "", value.casefold())
    return "".join(ch for ch in normalized if ch.isalnum())


def anchor_present(anchor: str, safe_output: str) -> bool:
    if anchor in safe_output:
        return True
    normalized_anchor = re.sub(r"\s+", " ", anchor).strip().casefold()
    normalized_output = re.sub(r"\s+", " ", safe_output).strip().casefold()
    if normalized_anchor and normalized_anchor in normalized_output:
        return True
    compact_anchor = compact_text(anchor)
    return bool(compact_anchor and compact_anchor in compact_text(safe_output))


def read_warnings(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_run(run_id: str) -> Path | None:
    path = RUNS_DIR / run_id
    return path if path.exists() else None


def run_case(
    *,
    case: dict[str, Any],
    run_prefix: str,
    manifest_dir: Path,
    options: argparse.Namespace,
) -> tuple[Path | None, str]:
    pdf_path = manifest_dir / "pdfs" / str(case["filename"])
    run_id = f"{run_prefix}_{case['case_id']}"
    run_dir = RUNS_DIR / run_id
    if options.resume and (run_dir / "metadata.json").exists():
        return run_dir, "skipped_existing"

    cmd = [
        str(PYTHON),
        str(ROOT / "run_routed_pdf.py"),
        "--pdf",
        str(pdf_path),
        "--run-id",
        run_id,
        "--output-root",
        str(SETTINGS.outputs.root),
        "--compare-mode",
        options.compare_mode,
        "--model",
        options.model,
        "--secondary-model",
        options.secondary_model,
        "--reasoning-effort",
        options.reasoning_effort,
        "--use-coordinate-table-reconstruction",
        "--max-parallel-table-groups",
        str(options.max_parallel_table_groups),
    ]
    if options.enable_table_vlm_fallback:
        cmd.append("--enable-table-vlm-fallback")
    if options.disable_embedded_visual_append:
        cmd.append("--disable-embedded-visual-append")
    if options.disable_parallel_reconcile_candidates:
        cmd.append("--disable-parallel-reconcile-candidates")

    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=options.timeout_per_case,
    )
    status = "ok" if completed.returncode == 0 else f"failed_exit_{completed.returncode}"
    log_path = SUITE_DIR / f"{run_id}.log"
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        return run_dir if run_dir.exists() else None, status
    return run_dir, status


def summarize_case(case: dict[str, Any], run_dir: Path | None, run_status: str) -> dict[str, Any]:
    expected = list(case.get("expected_anchors") or [])
    row: dict[str, Any] = {
        "case_id": case["case_id"],
        "filename": case["filename"],
        "description": case.get("description", ""),
        "tags": ",".join(case.get("tags", [])),
        "source_url": case.get("source_url", ""),
        "expected_pages": case.get("pages", ""),
        "run_status": run_status,
        "run_id": run_dir.name if run_dir else "",
        "page_count": "",
        "total_seconds": "",
        "seconds_per_page": "",
        "mode_page_counts": "",
        "warning_counts": "",
        "warning_level_counts": "",
        "safe_unknown_token_count": "",
        "safe_mask_count": "",
        "embedded_visual_page_count": "",
        "anchors_total": len(expected),
        "anchors_found": 0,
        "anchor_recall": 0.0 if expected else 1.0,
        "missing_anchors": "",
        "tables": "",
        "coordinate_segments": "",
        "run_dir": str(run_dir) if run_dir else "",
    }
    if run_dir is None:
        return row

    metadata = read_json(run_dir / "metadata.json", {})
    safe_output_path = run_dir / "safe_output.md"
    safe_output = safe_output_path.read_text(encoding="utf-8") if safe_output_path.exists() else ""
    warnings = read_warnings(run_dir / "warnings.csv")
    tables = read_json(run_dir / "tables.json", [])
    segments = read_json(run_dir / "segments.json", [])
    missing = [anchor for anchor in expected if not anchor_present(str(anchor), safe_output)]
    level_counts = Counter(row.get("level") or "" for row in warnings)
    warning_counts = metadata.get("warning_counts") or Counter(row.get("code") or "" for row in warnings)
    coordinate_segments = []
    for segment in segments:
        diagnostics = (segment.get("diagnostics") or {}).get("coordinate_diagnostics") or {}
        if diagnostics:
            coordinate_segments.append(
                {
                    "page": segment.get("start_page"),
                    "method": diagnostics.get("method"),
                    "rows": diagnostics.get("trimmed_rows") or diagnostics.get("rows"),
                    "columns": diagnostics.get("trimmed_columns") or diagnostics.get("columns"),
                    "span_coverage": diagnostics.get("span_coverage"),
                    "hmerge": diagnostics.get("horizontal_merged_region_count"),
                    "vmerge": diagnostics.get("vertical_merged_region_count"),
                }
            )
    row.update(
        {
            "page_count": metadata.get("page_count", ""),
            "total_seconds": metadata.get("total_seconds", ""),
            "seconds_per_page": metadata.get("seconds_per_page", ""),
            "mode_page_counts": json.dumps(metadata.get("mode_page_counts", {}), ensure_ascii=False, sort_keys=True),
            "warning_counts": json.dumps(warning_counts, ensure_ascii=False, sort_keys=True),
            "warning_level_counts": json.dumps(metadata.get("warning_level_counts", dict(level_counts)), ensure_ascii=False, sort_keys=True),
            "safe_unknown_token_count": metadata.get("safe_unknown_token_count", ""),
            "safe_mask_count": metadata.get("safe_mask_count", ""),
            "embedded_visual_page_count": metadata.get("embedded_visual_page_count", ""),
            "anchors_found": len(expected) - len(missing),
            "anchor_recall": round((len(expected) - len(missing)) / len(expected), 4) if expected else 1.0,
            "missing_anchors": " | ".join(str(anchor) for anchor in missing[:25]),
            "tables": ";".join(f"{table.get('rows')}x{table.get('columns')}" for table in tables[:12]),
            "coordinate_segments": json.dumps(coordinate_segments[:20], ensure_ascii=False),
        }
    )
    return row


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "case_id",
        "filename",
        "run_status",
        "expected_pages",
        "page_count",
        "total_seconds",
        "seconds_per_page",
        "mode_page_counts",
        "warning_counts",
        "warning_level_counts",
        "safe_unknown_token_count",
        "safe_mask_count",
        "embedded_visual_page_count",
        "anchors_total",
        "anchors_found",
        "anchor_recall",
        "missing_anchors",
        "tables",
        "coordinate_segments",
        "tags",
        "source_url",
        "run_id",
        "run_dir",
        "description",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_markdown(rows: list[dict[str, Any]], path: Path, run_prefix: str) -> None:
    total = len(rows)
    ok = sum(1 for row in rows if str(row.get("run_status")) in {"ok", "skipped_existing"})
    full_recall = sum(1 for row in rows if float(row.get("anchor_recall") or 0.0) >= 1.0)
    needs_retry = [
        row
        for row in rows
        if "needs_retry" in str(row.get("warning_level_counts"))
        or float(row.get("anchor_recall") or 0.0) < 1.0
        or int(row.get("safe_unknown_token_count") or 0) > 0
    ]
    lines = [
        "# PDF stress suite result",
        "",
        f"- Run prefix: `{run_prefix}`",
        f"- Cases: {total}",
        f"- Successful runs: {ok}/{total}",
        f"- Full anchor recall: {full_recall}/{total}",
        f"- Needs review: {len(needs_retry)}",
        "",
        "## Summary",
        "",
        "| case | status | pages | modes | tables | unknown | masks | anchor recall | warnings |",
        "|---|---:|---:|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {case_id} | {run_status} | {page_count}/{expected_pages} | `{mode_page_counts}` | `{tables}` | {safe_unknown_token_count} | {safe_mask_count} | {anchors_found}/{anchors_total} ({anchor_recall:.1%}) | `{warning_counts}` |".format(
                **row
            )
        )
    lines.extend(["", "## Needs Review", ""])
    if not needs_retry:
        lines.append("No cases met the review criteria.")
    for row in needs_retry:
        lines.append(f"- `{row['case_id']}` recall={row['anchors_found']}/{row['anchors_total']} unknown={row['safe_unknown_token_count']} masks={row['safe_mask_count']} warnings={row['warning_counts']} missing=`{row['missing_anchors']}`")
    lines.extend(["", "## Coordinate Diagnostics", ""])
    for row in rows:
        if row.get("coordinate_segments") and row.get("coordinate_segments") != "[]":
            lines.append(f"- `{row['case_id']}` {row['coordinate_segments']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=SUITE_DIR / "manifest.json")
    parser.add_argument("--run-prefix", default=f"stress_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--model", default=SETTINGS.models.table_vlm)
    parser.add_argument("--secondary-model", default=SETTINGS.models.secondary)
    parser.add_argument("--compare-mode", choices=["ocr-vlm", "vlm-vlm"], default="ocr-vlm")
    parser.add_argument("--reasoning-effort", default=SETTINGS.vlm.reasoning_effort)
    parser.add_argument("--enable-table-vlm-fallback", action="store_true")
    parser.add_argument("--disable-embedded-visual-append", action="store_true")
    parser.add_argument("--disable-parallel-reconcile-candidates", action="store_true")
    parser.add_argument(
        "--max-parallel-table-groups",
        type=int,
        default=SETTINGS.routing.max_parallel_table_groups,
    )
    parser.add_argument("--timeout-per-case", type=int, default=420)
    args = parser.parse_args()

    manifest = read_json(args.manifest, {})
    manifest_dir = args.manifest.resolve().parent
    requested = {case_id.upper() for case_id in (args.case_ids or [])}
    cases = [
        case
        for case in manifest.get("cases", [])
        if not requested or str(case.get("case_id")).upper() in requested
    ]
    SUITE_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['case_id']} {case['filename']}", flush=True)
        try:
            run_dir, status = run_case(case=case, run_prefix=args.run_prefix, manifest_dir=manifest_dir, options=args)
        except subprocess.TimeoutExpired:
            run_dir, status = latest_run(f"{args.run_prefix}_{case['case_id']}"), "timeout"
        except Exception as exc:
            run_dir, status = latest_run(f"{args.run_prefix}_{case['case_id']}"), f"failed:{exc}"
        rows.append(summarize_case(case, run_dir, status))

    output_stem = f"{args.run_prefix}_summary"
    json_path = SUITE_DIR / f"{output_stem}.json"
    csv_path = SUITE_DIR / f"{output_stem}.csv"
    md_path = SUITE_DIR / f"{output_stem}.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(rows, csv_path)
    write_markdown(rows, md_path, args.run_prefix)
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
