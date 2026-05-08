from __future__ import annotations

import csv
import json
import re
import sys
import time
import argparse
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.metrics import (  # noqa: E402
    calc_detection_metrics,
    compute_case_confidence_score,
    shape_presence_rates,
    table_shapes,
)
from docling_openai_vlm import (  # noqa: E402
    build_openai_vlm_converter,
    check_openai_chat_access,
)
from settings import load_settings, settings_to_safe_dict  # noqa: E402


SETTINGS = load_settings()
SAFE_SETTINGS = settings_to_safe_dict(SETTINGS)
OUT_DIR = SETTINGS.outputs.benchmark_dir
PDF_DIR = OUT_DIR / "pdfs"
RESULTS_DIR = OUT_DIR / "results_openai_vlm"
EXTRACTED_DIR = RESULTS_DIR / "extracted"
GT_PATH = OUT_DIR / "ground_truth.json"
JSON_PATH = RESULTS_DIR / "openai_vlm_results.json"
CSV_PATH = RESULTS_DIR / "openai_vlm_results.csv"
REPORT_PATH = RESULTS_DIR / "openai_vlm_report.md"


def model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")


def scale_slug(scale: float) -> str:
    return str(scale).replace(".", "_")


def configure_results_dir(results_dir: Path) -> None:
    global RESULTS_DIR, EXTRACTED_DIR, JSON_PATH, CSV_PATH, REPORT_PATH

    RESULTS_DIR = results_dir
    EXTRACTED_DIR = RESULTS_DIR / "extracted"
    JSON_PATH = RESULTS_DIR / "openai_vlm_results.json"
    CSV_PATH = RESULTS_DIR / "openai_vlm_results.csv"
    REPORT_PATH = RESULTS_DIR / "openai_vlm_report.md"


def normalize(value: Any) -> str:
    text = str(value).lower().replace("\u00a0", " ")
    return re.sub(r"\s+", "", text)


def recall(expected: list[str], observed_text: str) -> tuple[float, int, int, list[str]]:
    expected_norm = {normalize(item): item for item in expected if str(item).strip()}
    observed_norm = normalize(observed_text)
    hits: list[str] = []
    misses: list[str] = []
    for normed, original in expected_norm.items():
        if normed in observed_norm:
            hits.append(original)
        else:
            misses.append(original)
    total = len(expected_norm)
    score = len(hits) / total if total else 1.0
    return score, len(hits), total, misses[:20]


def export_tables(document: Any) -> tuple[list[dict[str, Any]], str]:
    tables: list[dict[str, Any]] = []
    chunks: list[str] = []
    for index, table in enumerate(getattr(document, "tables", []), start=1):
        dataframe = table.export_to_dataframe(doc=document)
        csv_text = dataframe.to_csv(index=False)
        html_text = table.export_to_html(doc=document)
        tables.append(
            {
                "index": index,
                "rows": int(len(dataframe)),
                "columns": int(len(dataframe.columns)),
                "headers": [str(column) for column in dataframe.columns],
                "csv": csv_text,
                "html": html_text,
            }
        )
        chunks.extend([csv_text, html_text])
    return tables, "\n".join(chunks)


def benchmark_case(converter: Any, case: dict[str, Any]) -> dict[str, Any]:
    pdf_path = PDF_DIR / case["filename"]
    started = time.perf_counter()
    try:
        result = converter.convert(pdf_path)
        document = result.document
        markdown = document.export_to_markdown()
        tables, table_text = export_tables(document)
        if not markdown.strip():
            raise RuntimeError(
                "VLM provider returned empty Markdown. The API call likely failed and "
                "Docling continued with an empty page response."
            )

        case_extract_dir = EXTRACTED_DIR / case["case_id"]
        case_extract_dir.mkdir(parents=True, exist_ok=True)
        (case_extract_dir / "markdown.md").write_text(markdown, encoding="utf-8")
        (case_extract_dir / "tables.json").write_text(
            json.dumps(tables, indent=2), encoding="utf-8"
        )

        text_score, text_hits, text_total, text_misses = recall(
            case["expected_text_anchors"], markdown
        )
        table_score, table_hits, table_total, table_misses = recall(
            case["expected_table_cells"], table_text
        )
        expected_tables = int(case["expected_tables"])
        detected_tables = len(tables)
        table_detection_ratio = (
            min(detected_tables / expected_tables, 1.0) if expected_tables else 1.0
        )
        overall = (0.35 * text_score) + (0.55 * table_score) + (
            0.10 * table_detection_ratio
        )
        detection_metrics = calc_detection_metrics(expected_tables, detected_tables)
        shape_metrics = shape_presence_rates(expected_tables, table_shapes(tables))
        structured_table_cell_recall = table_score
        case_confidence_score = compute_case_confidence_score(overall, detection_metrics["table_detection_f1"], detection_metrics["over_detection_penalty"])
        low_confidence = (
            len(markdown.strip()) < 200
            or detection_metrics["over_detection_penalty"] > 0.25
            or detection_metrics["table_detection_recall"] < 0.8
            or case_confidence_score < 0.75
        )
        return {
            "case_id": case["case_id"],
            "filename": case["filename"],
            "status": "ok",
            "error": "",
            "expected_pages": int(case["pages"]),
            "expected_tables": expected_tables,
            "detected_tables": detected_tables,
            "text_anchor_recall": round(text_score, 4),
            "text_anchor_hits": text_hits,
            "text_anchor_total": text_total,
            "table_cell_recall": round(table_score, 4),
            "table_cell_hits": table_hits,
            "table_cell_total": table_total,
            "table_detection_ratio": round(table_detection_ratio, 4),
            "table_detection_precision": round(detection_metrics["table_detection_precision"], 4),
            "table_detection_f1": round(detection_metrics["table_detection_f1"], 4),
            "row_count_present_rate": round(shape_metrics["row_count_present_rate"], 4),
            "column_count_present_rate": round(shape_metrics["column_count_present_rate"], 4),
            "table_structure_present_rate": round(shape_metrics["table_structure_present_rate"], 4),
            "structured_table_cell_recall": round(structured_table_cell_recall, 4),
            "duplicate_table_rate": round(detection_metrics["duplicate_table_rate"], 4),
            "over_detection_penalty": round(detection_metrics["over_detection_penalty"], 4),
            "case_confidence_score": round(case_confidence_score, 4),
            "low_confidence": low_confidence,
            "overall_score": round(overall, 4),
            "total_seconds": round(time.perf_counter() - started, 3),
            "seconds_per_page": round((time.perf_counter() - started) / int(case["pages"]), 3),
            "markdown_chars": len(markdown),
            "sample_missing_text_anchors": text_misses,
            "sample_missing_table_cells": table_misses,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "case_id": case["case_id"],
            "filename": case["filename"],
            "status": "failed",
            "error": str(exc),
            "expected_pages": int(case["pages"]),
            "expected_tables": int(case["expected_tables"]),
            "detected_tables": 0,
            "text_anchor_recall": 0.0,
            "text_anchor_hits": 0,
            "text_anchor_total": len(case["expected_text_anchors"]),
            "table_cell_recall": 0.0,
            "table_cell_hits": 0,
            "table_cell_total": len(case["expected_table_cells"]),
            "table_detection_ratio": 0.0,
            "table_detection_precision": 0.0,
            "table_detection_f1": 0.0,
            "row_count_present_rate": 0.0,
            "column_count_present_rate": 0.0,
            "table_structure_present_rate": 0.0,
            "structured_table_cell_recall": 0.0,
            "duplicate_table_rate": 0.0,
            "over_detection_penalty": 1.0,
            "case_confidence_score": 0.0,
            "low_confidence": True,
            "overall_score": 0.0,
            "total_seconds": round(elapsed, 3),
            "seconds_per_page": round(elapsed / int(case["pages"]), 3),
            "markdown_chars": 0,
            "sample_missing_text_anchors": case["expected_text_anchors"][:20],
            "sample_missing_table_cells": case["expected_table_cells"][:20],
        }


def write_outputs(rows: list[dict[str, Any]], model: str, settings: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": date.today().isoformat(),
        "model": model,
        "settings": settings,
        "metric": "0.35 * text_anchor_recall + 0.55 * table_cell_recall + 0.10 * table_detection_ratio",
        "extended_metrics": ["table_detection_precision", "table_detection_f1", "row_count_present_rate", "column_count_present_rate", "table_structure_present_rate", "structured_table_cell_recall", "duplicate_table_rate", "case_confidence_score", "over_detection_penalty", "low_confidence"],
        "results": rows,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    fields = [
        "case_id",
        "filename",
        "status",
        "expected_pages",
        "expected_tables",
        "detected_tables",
        "text_anchor_recall",
        "table_cell_recall",
        "table_detection_ratio",
        "table_detection_precision",
        "table_detection_f1",
        "row_count_present_rate",
        "column_count_present_rate",
        "table_structure_present_rate",
        "structured_table_cell_recall",
        "duplicate_table_rate",
        "over_detection_penalty",
        "case_confidence_score",
        "low_confidence",
        "overall_score",
        "total_seconds",
        "seconds_per_page",
        "markdown_chars",
        "error",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# Provider VLM Benchmark Report",
        "",
        f"- Model: `{model}`",
        f"- Settings: `{json.dumps(settings, ensure_ascii=False)}`",
        f"- Cases attempted: {len(rows)}",
        f"- Successful: {sum(1 for row in rows if row['status'] == 'ok')}",
        f"- Failed: {sum(1 for row in rows if row['status'] != 'ok')}",
        f"- Total time: {sum(float(row['total_seconds']) for row in rows):.3f} sec",
        "",
        "| Case | Status | Pages | Tables expected/detected | Text recall | Table recall | Detection F1 | Confidence | Low confidence | Overall | Total sec | Error |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {case_id} | {status} | {pages} | {expected_tables}/{detected_tables} | {text:.3f} | {table:.3f} | {detection_f1:.3f} | {confidence:.3f} | {low_confidence} | {overall:.3f} | {seconds:.3f} | {error} |".format(
                case_id=row["case_id"],
                status=row["status"],
                pages=row["expected_pages"],
                expected_tables=row["expected_tables"],
                detected_tables=row["detected_tables"],
                text=float(row["text_anchor_recall"]),
                table=float(row["table_cell_recall"]),
                detection_f1=float(row["table_detection_f1"]),
                confidence=float(row["case_confidence_score"]),
                low_confidence=str(row["low_confidence"]).lower(),
                overall=float(row["overall_score"]),
                seconds=float(row["total_seconds"]),
                error=str(row["error"]).replace("|", "\\|")[:240],
            )
        )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Docling provider VLM benchmark against generated PDFs."
    )
    parser.add_argument("--model", default=SETTINGS.models.primary)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults under the configured benchmark output root.",
    )
    parser.add_argument(
        "--max-completion-tokens",
        type=int,
        default=SETTINGS.vlm.max_completion_tokens,
    )
    parser.add_argument(
        "--reasoning-effort",
        default=SETTINGS.vlm.reasoning_effort,
        choices=["none", "low", "medium", "high", "xhigh"],
    )
    parser.add_argument("--timeout-seconds", type=float, default=SETTINGS.vlm.timeout_seconds)
    parser.add_argument("--scale", type=float, default=SETTINGS.vlm.scale)
    parser.add_argument(
        "--image-detail",
        default=SETTINGS.vlm.image_detail or "auto",
        choices=["auto", "low", "high"],
    )
    parser.add_argument(
        "--response-format",
        choices=["markdown", "html"],
        default="markdown",
        help="Format requested from the VLM before Docling reparses it.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional number of benchmark cases to run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model = args.model
    results_dir = args.results_dir or OUT_DIR / (
        f"results_openai_vlm_{model_slug(model)}_"
        f"{args.response_format}_scale_{scale_slug(args.scale)}"
    )
    configure_results_dir(results_dir)

    settings = {
        "max_completion_tokens": args.max_completion_tokens,
        "reasoning_effort": args.reasoning_effort,
        "timeout_seconds": args.timeout_seconds,
        "scale": args.scale,
        "response_format": args.response_format,
        "image_detail": args.image_detail,
        "config_sources": list(SETTINGS.config_sources),
        "resolved_settings": SAFE_SETTINGS,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    ground_truth = json.loads(GT_PATH.read_text(encoding="utf-8"))
    cases = ground_truth["cases"]
    if args.limit is not None:
        cases = cases[: args.limit]

    try:
        check_openai_chat_access(
            model,
            reasoning_effort=args.reasoning_effort,
            timeout_seconds=min(args.timeout_seconds, 60),
            chat_completions_url=SETTINGS.openai.chat_completions_url,
            provider=SETTINGS.provider.name,
            api_key=SETTINGS.provider.api_key,
            azure_endpoint=SETTINGS.provider.azure_endpoint,
            azure_deployment=SETTINGS.provider.azure_deployment,
            azure_api_version=SETTINGS.provider.azure_api_version,
            max_retries=SETTINGS.openai.max_retries,
            initial_backoff_seconds=SETTINGS.openai.initial_backoff_seconds,
        )
    except Exception as exc:
        row = {
            "case_id": "PRE",
            "filename": "",
            "status": "failed",
            "error": str(exc),
            "expected_pages": 0,
            "expected_tables": 0,
            "detected_tables": 0,
            "text_anchor_recall": 0.0,
            "text_anchor_hits": 0,
            "text_anchor_total": 0,
            "table_cell_recall": 0.0,
            "table_cell_hits": 0,
            "table_cell_total": 0,
            "table_detection_ratio": 0.0,
            "table_detection_precision": 0.0,
            "table_detection_f1": 0.0,
            "row_count_present_rate": 0.0,
            "column_count_present_rate": 0.0,
            "table_structure_present_rate": 0.0,
            "structured_table_cell_recall": 0.0,
            "duplicate_table_rate": 0.0,
            "over_detection_penalty": 1.0,
            "case_confidence_score": 0.0,
            "low_confidence": True,
            "overall_score": 0.0,
            "total_seconds": 0.0,
            "seconds_per_page": 0.0,
            "markdown_chars": 0,
            "sample_missing_text_anchors": [],
            "sample_missing_table_cells": [],
        }
        write_outputs([row], model, settings)
        print(f"Provider preflight failed: {exc}", flush=True)
        print(f"Wrote {JSON_PATH}")
        print(f"Wrote {CSV_PATH}")
        print(f"Wrote {REPORT_PATH}")
        return 1

    converter = build_openai_vlm_converter(
        model,
        max_completion_tokens=args.max_completion_tokens,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout_seconds,
        scale=args.scale,
        response_format=args.response_format,
        image_detail=args.image_detail,
        provider=SETTINGS.provider.name,
        api_key=SETTINGS.provider.api_key,
        chat_completions_url=SETTINGS.provider.chat_completions_url,
        azure_endpoint=SETTINGS.provider.azure_endpoint,
        azure_deployment=SETTINGS.provider.azure_deployment,
        azure_api_version=SETTINGS.provider.azure_api_version,
        max_retries=SETTINGS.openai.max_retries,
        initial_backoff_seconds=SETTINGS.openai.initial_backoff_seconds,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        print(f"Benchmarking provider VLM {case['case_id']} {case['filename']} ...", flush=True)
        row = benchmark_case(converter, case)
        rows.append(row)
        print(
            f"  status={row['status']} time={row['total_seconds']:.3f}s "
            f"overall={row['overall_score']:.3f}",
            flush=True,
        )
        if row["status"] != "ok":
            print(f"  error={row['error']}", flush=True)
            break
    write_outputs(rows, model, settings)
    print(f"Wrote {JSON_PATH}")
    print(f"Wrote {CSV_PATH}")
    print(f"Wrote {REPORT_PATH}")
    return 0 if rows and all(row["status"] == "ok" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
