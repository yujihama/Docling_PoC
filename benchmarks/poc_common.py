from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
from settings import load_settings  # noqa: E402

SETTINGS = load_settings()
BENCHMARK_OUT_DIR = SETTINGS.outputs.benchmark_dir
PDF_DIR = BENCHMARK_OUT_DIR / "pdfs"
GT_PATH = BENCHMARK_OUT_DIR / "ground_truth.json"
POC_RUNS_DIR = SETTINGS.outputs.poc_runs_dir
METRIC_FORMULA = (
    "0.35 * text_anchor_recall + 0.55 * table_cell_recall + "
    "0.10 * table_detection_ratio"
)


from benchmarks.metrics import (  # noqa: E402
    calc_detection_metrics,
    compute_case_confidence_score,
    shape_presence_rates,
    table_shapes,
)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def scale_slug(value: float) -> str:
    return str(value).replace(".", "_")


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


def load_cases(
    case_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    payload = json.loads(GT_PATH.read_text(encoding="utf-8"))
    cases = [dict(case) for case in payload["cases"]]
    if case_ids:
        wanted = {case_id.strip() for case_id in case_ids if case_id.strip()}
        cases = [case for case in cases if case["case_id"] in wanted]
    if limit is not None:
        cases = cases[:limit]
    return cases


def count_pages(document: Any) -> int | None:
    pages = getattr(document, "pages", None)
    if pages is None:
        return None
    try:
        return len(pages)
    except TypeError:
        return None


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


def summarize_usage(events: list[dict[str, Any]]) -> dict[str, int]:
    prompt_tokens = sum(int(event.get("prompt_tokens") or 0) for event in events)
    completion_tokens = sum(
        int(event.get("completion_tokens") or 0) for event in events
    )
    total_tokens = sum(int(event.get("total_tokens") or 0) for event in events)
    return {
        "request_count": len(events),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def evaluate_document(
    document: Any,
    case: dict[str, Any],
    markdown: str,
    tables: list[dict[str, Any]],
    table_text: str,
) -> dict[str, Any]:
    expected_text = list(case.get("expected_text_anchors", []))
    expected_cells = list(case.get("expected_table_cells", []))
    expected_tables = int(case.get("expected_tables", 0))

    text_score, text_hits, text_total, text_misses = recall(expected_text, markdown)
    table_score, table_hits, table_total, table_misses = recall(
        expected_cells, table_text
    )
    detected_tables = len(tables)
    table_detection_ratio = (
        min(detected_tables / expected_tables, 1.0) if expected_tables else 1.0
    )
    overall = (0.35 * text_score) + (0.55 * table_score) + (
        0.10 * table_detection_ratio
    )
    detection_metrics = calc_detection_metrics(expected_tables, detected_tables)
    shape_metrics = shape_presence_rates(expected_tables, table_shapes(tables))
    case_confidence_score = compute_case_confidence_score(
        overall,
        detection_metrics["table_detection_f1"],
        detection_metrics["over_detection_penalty"],
    )
    low_confidence = (
        len(markdown.strip()) < 200
        or detection_metrics["over_detection_penalty"] > 0.25
        or detection_metrics["table_detection_recall"] < 0.8
        or case_confidence_score < 0.75
    )
    return {
        "detected_pages": count_pages(document),
        "expected_tables": expected_tables,
        "detected_tables": detected_tables,
        "text_anchor_recall": round(text_score, 4),
        "text_anchor_hits": text_hits,
        "text_anchor_total": text_total,
        "table_cell_recall": round(table_score, 4),
        "table_cell_hits": table_hits,
        "table_cell_total": table_total,
        "table_detection_ratio": round(table_detection_ratio, 4),
        "table_detection_precision": round(
            detection_metrics["table_detection_precision"], 4
        ),
        "table_detection_f1": round(detection_metrics["table_detection_f1"], 4),
        "row_count_present_rate": round(shape_metrics["row_count_present_rate"], 4),
        "column_count_present_rate": round(
            shape_metrics["column_count_present_rate"], 4
        ),
        "table_structure_present_rate": round(
            shape_metrics["table_structure_present_rate"], 4
        ),
        "structured_table_cell_recall": round(table_score, 4),
        "duplicate_table_rate": round(detection_metrics["duplicate_table_rate"], 4),
        "over_detection_penalty": round(
            detection_metrics["over_detection_penalty"], 4
        ),
        "case_confidence_score": round(case_confidence_score, 4),
        "low_confidence": low_confidence,
        "overall_score": round(overall, 4),
        "markdown_chars": len(markdown),
        "sample_missing_text_anchors": text_misses,
        "sample_missing_table_cells": table_misses,
    }


def benchmark_case(
    converter: Any,
    case: dict[str, Any],
    extracted_dir: Path,
    *,
    fail_on_empty_markdown: bool = False,
    usage_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pdf_path = PDF_DIR / case["filename"]
    started = time.perf_counter()
    usage_summary = summarize_usage(usage_events or [])
    try:
        result = converter.convert(pdf_path)
        converted_at = time.perf_counter()
        document = result.document
        markdown = document.export_to_markdown()
        tables, table_text = export_tables(document)
        if fail_on_empty_markdown and not markdown.strip():
            raise RuntimeError("Conversion returned empty Markdown.")

        case_extract_dir = extracted_dir / case["case_id"]
        case_extract_dir.mkdir(parents=True, exist_ok=True)
        (case_extract_dir / "markdown.md").write_text(markdown, encoding="utf-8")
        (case_extract_dir / "tables.json").write_text(
            json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        finished = time.perf_counter()
        metrics = evaluate_document(document, case, markdown, tables, table_text)
        expected_pages = int(case.get("pages", 0))
        return {
            "case_id": case["case_id"],
            "filename": case["filename"],
            "description": case.get("description", ""),
            "tags": case.get("tags", []),
            "status": "ok",
            "error": "",
            "expected_pages": expected_pages,
            "file_size_mb": round(pdf_path.stat().st_size / 1024 / 1024, 3),
            "convert_seconds": round(converted_at - started, 3),
            "total_seconds": round(finished - started, 3),
            "seconds_per_page": (
                round((finished - started) / expected_pages, 3)
                if expected_pages
                else None
            ),
            **metrics,
            **usage_summary,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        expected_pages = int(case.get("pages", 0))
        expected_text = list(case.get("expected_text_anchors", []))
        expected_cells = list(case.get("expected_table_cells", []))
        expected_tables = int(case.get("expected_tables", 0))
        return {
            "case_id": case["case_id"],
            "filename": case["filename"],
            "description": case.get("description", ""),
            "tags": case.get("tags", []),
            "status": "failed",
            "error": str(exc),
            "expected_pages": expected_pages,
            "detected_pages": None,
            "file_size_mb": (
                round(pdf_path.stat().st_size / 1024 / 1024, 3)
                if pdf_path.exists()
                else 0.0
            ),
            "expected_tables": expected_tables,
            "detected_tables": 0,
            "text_anchor_recall": 0.0,
            "text_anchor_hits": 0,
            "text_anchor_total": len(expected_text),
            "table_cell_recall": 0.0,
            "table_cell_hits": 0,
            "table_cell_total": len(expected_cells),
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
            "convert_seconds": round(elapsed, 3),
            "total_seconds": round(elapsed, 3),
            "seconds_per_page": round(elapsed / expected_pages, 3)
            if expected_pages
            else None,
            "markdown_chars": 0,
            "sample_missing_text_anchors": expected_text[:20],
            "sample_missing_table_cells": expected_cells[:20],
            **usage_summary,
        }


RESULT_FIELDS = [
    "run_id",
    "phase",
    "pipeline",
    "config_id",
    "case_id",
    "filename",
    "status",
    "expected_pages",
    "detected_pages",
    "expected_tables",
    "detected_tables",
    "text_anchor_recall",
    "table_cell_recall",
    "table_detection_ratio",
    "table_detection_precision",
    "table_detection_f1",
    "case_confidence_score",
    "low_confidence",
    "overall_score",
    "convert_seconds",
    "total_seconds",
    "seconds_per_page",
    "markdown_chars",
    "request_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated_cost_usd",
    "error",
]


def write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in RESULT_FIELDS})
