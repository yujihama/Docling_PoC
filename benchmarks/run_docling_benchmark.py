from __future__ import annotations

import csv
import json
import re
import statistics
import time
from pathlib import Path
from typing import Any

from docling.document_converter import DocumentConverter

from benchmarks.metrics import (
    calc_detection_metrics,
    compute_page_level_score,
    shape_match_rates,
    table_shapes,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "docling_benchmark"
PDF_DIR = OUT_DIR / "pdfs"
RESULTS_DIR = OUT_DIR / "results"
EXTRACTED_DIR = RESULTS_DIR / "extracted"
GT_PATH = OUT_DIR / "ground_truth.json"
JSON_PATH = RESULTS_DIR / "benchmark_results.json"
CSV_PATH = RESULTS_DIR / "benchmark_results.csv"
REPORT_PATH = RESULTS_DIR / "benchmark_report.md"


def normalize(value: Any) -> str:
    text = str(value).lower()
    text = text.replace("\u00a0", " ")
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
                "csv": csv_text,
                "html": html_text,
            }
        )
        chunks.extend([csv_text, html_text])
    return tables, "\n".join(chunks)


def count_pages(document: Any) -> int | None:
    pages = getattr(document, "pages", None)
    if pages is None:
        return None
    try:
        return len(pages)
    except TypeError:
        return None


def benchmark_case(converter: DocumentConverter, case: dict[str, Any]) -> dict[str, Any]:
    pdf_path = PDF_DIR / case["filename"]
    started = time.perf_counter()
    result = converter.convert(pdf_path)
    converted_at = time.perf_counter()
    document = result.document
    markdown = document.export_to_markdown()
    tables, table_text = export_tables(document)
    finished = time.perf_counter()

    case_extract_dir = EXTRACTED_DIR / case["case_id"]
    case_extract_dir.mkdir(parents=True, exist_ok=True)
    (case_extract_dir / "markdown.md").write_text(markdown, encoding="utf-8")
    (case_extract_dir / "tables.json").write_text(json.dumps(tables, indent=2), encoding="utf-8")

    text_score, text_hits, text_total, text_misses = recall(case["expected_text_anchors"], markdown)
    table_score, table_hits, table_total, table_misses = recall(case["expected_table_cells"], table_text)
    expected_tables = int(case["expected_tables"])
    detected_tables = len(tables)
    table_detection_ratio = min(detected_tables / expected_tables, 1.0) if expected_tables else 1.0
    overall = (0.35 * text_score) + (0.55 * table_score) + (0.10 * table_detection_ratio)
    detection_metrics = calc_detection_metrics(expected_tables, detected_tables)
    shape_metrics = shape_match_rates(expected_tables, table_shapes(tables))
    structured_table_cell_recall = table_score
    page_level_score = compute_page_level_score(overall, detection_metrics["table_detection_f1"], detection_metrics["over_detection_penalty"])
    low_confidence = (
        len(markdown.strip()) < 200
        or detection_metrics["over_detection_penalty"] > 0.25
        or detection_metrics["table_detection_recall"] < 0.8
        or page_level_score < 0.75
    )
    convert_seconds = converted_at - started
    total_seconds = finished - started
    pages = int(case["pages"])
    return {
        "case_id": case["case_id"],
        "filename": case["filename"],
        "description": case["description"],
        "tags": case["tags"],
        "expected_pages": pages,
        "detected_pages": count_pages(document),
        "file_size_mb": round(pdf_path.stat().st_size / 1024 / 1024, 3),
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
        "row_count_match_rate": round(shape_metrics["row_count_match_rate"], 4),
        "column_count_match_rate": round(shape_metrics["column_count_match_rate"], 4),
        "header_match_rate": round(shape_metrics["header_match_rate"], 4),
        "structured_table_cell_recall": round(structured_table_cell_recall, 4),
        "duplicate_table_rate": round(detection_metrics["duplicate_table_rate"], 4),
        "over_detection_penalty": round(detection_metrics["over_detection_penalty"], 4),
        "page_level_score": round(page_level_score, 4),
        "low_confidence": low_confidence,
        "overall_score": round(overall, 4),
        "convert_seconds": round(convert_seconds, 3),
        "total_seconds": round(total_seconds, 3),
        "seconds_per_page": round(total_seconds / pages, 3) if pages else None,
        "markdown_chars": len(markdown),
        "sample_missing_text_anchors": text_misses,
        "sample_missing_table_cells": table_misses,
    }


def write_csv(rows: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "filename",
        "expected_pages",
        "file_size_mb",
        "expected_tables",
        "detected_tables",
        "text_anchor_recall",
        "table_cell_recall",
        "table_detection_ratio",
        "table_detection_precision",
        "table_detection_f1",
        "row_count_match_rate",
        "column_count_match_rate",
        "header_match_rate",
        "structured_table_cell_recall",
        "duplicate_table_rate",
        "over_detection_penalty",
        "page_level_score",
        "low_confidence",
        "overall_score",
        "convert_seconds",
        "total_seconds",
        "seconds_per_page",
        "markdown_chars",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def write_report(rows: list[dict[str, Any]]) -> None:
    total_pages = sum(int(row["expected_pages"]) for row in rows)
    total_time = sum(float(row["total_seconds"]) for row in rows)
    mean_overall = statistics.mean(float(row["overall_score"]) for row in rows)
    lines = [
        "# Docling Benchmark Report",
        "",
        "Synthetic PDFs were generated with deterministic ground truth strings.",
        "Accuracy is a string-recall benchmark, not a semantic human-evaluation score.",
        "",
        "## Summary",
        "",
        f"- PDFs: {len(rows)}",
        f"- Total pages: {total_pages}",
        f"- Total measured time: {total_time:.3f} sec",
        f"- Mean overall score: {mean_overall:.3f}",
        "",
        "## Results",
        "",
        "| Case | Pages | Tags | Tables expected/detected | Text recall | Table recall | Overall | Total sec | Sec/page |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {case_id} | {pages} | {tags} | {expected_tables}/{detected_tables} | {text:.3f} | {table:.3f} | {overall:.3f} | {seconds:.3f} | {spp:.3f} |".format(
                case_id=row["case_id"],
                pages=row["expected_pages"],
                tags=", ".join(row["tags"]),
                expected_tables=row["expected_tables"],
                detected_tables=row["detected_tables"],
                text=float(row["text_anchor_recall"]),
                table=float(row["table_cell_recall"]),
                overall=float(row["overall_score"]),
                seconds=float(row["total_seconds"]),
                spp=float(row["seconds_per_page"]),
            )
        )
    lines.extend(["", "## Notes", ""])
    for row in rows:
        lines.append(f"### {row['case_id']} - {row['description']}")
        lines.append(f"- File: `outputs/docling_benchmark/pdfs/{row['filename']}`")
        if row["sample_missing_text_anchors"]:
            lines.append(f"- Missing text anchors sample: `{', '.join(row['sample_missing_text_anchors'][:5])}`")
        else:
            lines.append("- Missing text anchors sample: none")
        if row["sample_missing_table_cells"]:
            lines.append(f"- Missing table cells sample: `{', '.join(row['sample_missing_table_cells'][:5])}`")
        else:
            lines.append("- Missing table cells sample: none")
        lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    ground_truth = json.loads(GT_PATH.read_text(encoding="utf-8"))
    converter = DocumentConverter()
    rows: list[dict[str, Any]] = []
    for case in ground_truth["cases"]:
        print(f"Benchmarking {case['case_id']} {case['filename']} ...", flush=True)
        row = benchmark_case(converter, case)
        rows.append(row)
        print(
            f"  time={row['total_seconds']:.3f}s overall={row['overall_score']:.3f} "
            f"text={row['text_anchor_recall']:.3f} table={row['table_cell_recall']:.3f}",
            flush=True,
        )
    payload = {
        "generated_at": "2026-05-02",
        "metric": "0.35 * text_anchor_recall + 0.55 * table_cell_recall + 0.10 * table_detection_ratio",
        "extended_metrics": ["table_detection_precision", "table_detection_f1", "row_count_match_rate", "column_count_match_rate", "header_match_rate", "structured_table_cell_recall", "duplicate_table_rate", "page_level_score", "over_detection_penalty", "low_confidence"],
        "results": rows,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_csv(rows)
    write_report(rows)
    print(f"Wrote {JSON_PATH}")
    print(f"Wrote {CSV_PATH}")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
