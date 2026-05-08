from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from settings import load_settings  # noqa: E402

SETTINGS = load_settings()
SUITE_DIR = SETTINGS.outputs.root / "pdf_validation_suite"
RUNS_DIR = SETTINGS.outputs.routing_runs_dir


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def loose_text(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def compact_text(value: str) -> str:
    normalized = value.casefold()
    normalized = normalized.replace("\\_", "_")
    return "".join(ch for ch in normalized if ch.isalnum())


def latest_run_for(case_id: str, run_prefix: str) -> Path | None:
    pattern = f"{run_prefix}_{case_id}_*"
    candidates = [path for path in RUNS_DIR.glob(pattern) if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_warnings(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def anchor_present(anchor: str, safe_output: str) -> bool:
    if anchor in safe_output:
        return True
    normalized_anchor = norm_text(anchor)
    normalized_output = norm_text(safe_output)
    if normalized_anchor and normalized_anchor.casefold() in normalized_output.casefold():
        return True
    loose_anchor = loose_text(anchor)
    if loose_anchor and loose_anchor.casefold() in loose_text(safe_output).casefold():
        return True
    compact_anchor = compact_text(anchor)
    if compact_anchor and compact_anchor in compact_text(safe_output):
        return True
    return False


def summarize_case(case: dict[str, Any], run_dir: Path | None, run_prefix: str) -> dict[str, Any]:
    expected = list(case.get("expected_anchors") or [])
    base = {
        "case_id": case["case_id"],
        "filename": case["filename"],
        "description": case.get("description", ""),
        "tags": ",".join(case.get("tags", [])),
        "source_url": case.get("source_url", ""),
        "expected_pages": case.get("pages", ""),
        "run_id": "",
        "status": "missing_run",
        "page_count": "",
        "total_seconds": "",
        "seconds_per_page": "",
        "mode_page_counts": "",
        "safe_unknown_token_count": "",
        "safe_mask_count": "",
        "warning_counts": "",
        "warning_level_counts": "",
        "needs_retry_count": "",
        "anchors_total": len(expected),
        "anchors_found": 0,
        "anchor_recall": 0.0 if expected else 1.0,
        "missing_anchors": "",
        "run_dir": "",
    }
    if run_dir is None:
        return base

    metadata = read_json(run_dir / "metadata.json", {})
    safe_output_path = run_dir / "safe_output.md"
    safe_output = safe_output_path.read_text(encoding="utf-8") if safe_output_path.exists() else ""
    warnings = read_warnings(run_dir / "warnings.csv")
    missing = [anchor for anchor in expected if not anchor_present(anchor, safe_output)]
    found = len(expected) - len(missing)
    level_counts = Counter((row.get("level") or "") for row in warnings)
    warning_counts = metadata.get("warning_counts") or Counter((row.get("code") or "") for row in warnings)
    base.update(
        {
            "run_id": run_dir.name,
            "status": "ok" if metadata else "missing_metadata",
            "page_count": metadata.get("page_count", ""),
            "total_seconds": metadata.get("total_seconds", ""),
            "seconds_per_page": metadata.get("seconds_per_page", ""),
            "mode_page_counts": json.dumps(metadata.get("mode_page_counts", {}), ensure_ascii=False, sort_keys=True),
            "safe_unknown_token_count": metadata.get("safe_unknown_token_count", ""),
            "safe_mask_count": metadata.get("safe_mask_count", ""),
            "warning_counts": json.dumps(warning_counts, ensure_ascii=False, sort_keys=True),
            "warning_level_counts": json.dumps(metadata.get("warning_level_counts", dict(level_counts)), ensure_ascii=False, sort_keys=True),
            "needs_retry_count": level_counts.get("needs_retry", 0),
            "anchors_found": found,
            "anchor_recall": round(found / len(expected), 4) if expected else 1.0,
            "missing_anchors": " | ".join(missing[:20]),
            "run_dir": str(run_dir),
        }
    )
    return base


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "case_id",
        "filename",
        "status",
        "expected_pages",
        "page_count",
        "total_seconds",
        "seconds_per_page",
        "mode_page_counts",
        "safe_unknown_token_count",
        "safe_mask_count",
        "needs_retry_count",
        "anchors_total",
        "anchors_found",
        "anchor_recall",
        "warning_counts",
        "warning_level_counts",
        "missing_anchors",
        "tags",
        "source_url",
        "run_id",
        "run_dir",
        "description",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_markdown(rows: list[dict[str, Any]], path: Path, run_prefix: str) -> None:
    lines = [
        "# PDF validation suite result",
        "",
        f"- Run prefix: `{run_prefix}`",
        f"- Cases: {len(rows)}",
        f"- Runs found: {sum(1 for row in rows if row['status'] != 'missing_run')}",
        "",
        "## Summary",
        "",
        "| case | status | pages | modes | unknown | masks | needs_retry | anchor recall |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {case_id} | {status} | {page_count}/{expected_pages} | `{mode_page_counts}` | {safe_unknown_token_count} | {safe_mask_count} | {needs_retry_count} | {anchors_found}/{anchors_total} ({anchor_recall:.1%}) |".format(
                **row
            )
        )
    lines.extend(["", "## Missing Anchors", ""])
    for row in rows:
        missing = row.get("missing_anchors") or ""
        if not missing:
            continue
        lines.append(f"### {row['case_id']} {row['filename']}")
        lines.append("")
        for item in missing.split(" | "):
            lines.append(f"- `{item}`")
        lines.append("")
    lines.extend(["## Warnings", ""])
    for row in rows:
        if row.get("warning_counts") in ("{}", "", None):
            continue
        lines.append(f"- `{row['case_id']}` warning_counts={row['warning_counts']} levels={row['warning_level_counts']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=SUITE_DIR / "manifest.json")
    parser.add_argument("--run-prefix", default="validation_suite")
    parser.add_argument("--output-stem", default="validation_summary")
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Limit the summary to one case id. May be specified multiple times.",
    )
    args = parser.parse_args()

    manifest = read_json(args.manifest, {})
    requested_case_ids = {case_id.upper() for case_id in (args.case_ids or [])}
    rows = []
    for case in manifest.get("cases", []):
        if requested_case_ids and str(case["case_id"]).upper() not in requested_case_ids:
            continue
        run_dir = latest_run_for(str(case["case_id"]), args.run_prefix)
        rows.append(summarize_case(case, run_dir, args.run_prefix))

    SUITE_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = SUITE_DIR / f"{args.output_stem}.csv"
    md_path = SUITE_DIR / f"{args.output_stem}.md"
    write_csv(rows, csv_path)
    write_markdown(rows, md_path, args.run_prefix)
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    for row in rows:
        print(
            f"{row['case_id']} status={row['status']} recall={row['anchors_found']}/{row['anchors_total']} "
            f"unknown={row['safe_unknown_token_count']} masks={row['safe_mask_count']} retry={row['needs_retry_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
