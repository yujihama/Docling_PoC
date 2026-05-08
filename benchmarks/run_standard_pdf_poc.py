from __future__ import annotations

import argparse
import csv
import html
import json
import re
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.poc_common import POC_RUNS_DIR, count_pages, export_tables  # noqa: E402
from benchmarks.run_poc_matrix import (  # noqa: E402
    MatrixConfig,
    build_standard_configs,
    build_standard_converter,
)


RESULT_FIELDS = [
    "run_id",
    "case_id",
    "filename",
    "pipeline",
    "config_id",
    "status",
    "error",
    "expected_pages",
    "detected_pages",
    "file_size_mb",
    "detected_tables",
    "chunk_pages",
    "chunk_count",
    "workers",
    "markdown_chars",
    "table_text_chars",
    "convert_seconds",
    "total_seconds",
    "seconds_per_page",
    "relative_markdown_recall",
    "relative_markdown_hits",
    "relative_markdown_total",
    "relative_table_recall",
    "relative_table_hits",
    "relative_table_total",
    "relative_table_count_ratio",
    "relative_markdown_chars_ratio",
    "relative_overall",
    "do_table_structure",
    "tableformer_mode",
    "do_cell_matching",
    "do_ocr",
    "force_full_page_ocr",
    "force_backend_text",
    "images_scale",
    "generate_page_images",
    "batch_profile",
    "ocr_batch_size",
    "layout_batch_size",
    "table_batch_size",
    "settings_json",
]


def pdf_page_count(pdf_path: Path) -> int | None:
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(pdf_path))
        try:
            return len(document)
        finally:
            document.close()
    except Exception:
        return None


def pdf_chunks(
    *,
    pdf_path: Path,
    chunks_dir: Path,
    chunk_pages: int,
) -> list[tuple[int, int, Path]]:
    if chunk_pages <= 0:
        return [(1, pdf_page_count(pdf_path) or 0, pdf_path)]

    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise RuntimeError("pypdfium2 is required for PDF chunking.") from exc

    source = pdfium.PdfDocument(str(pdf_path))
    try:
        page_count = len(source)
        if page_count <= chunk_pages:
            return [(1, page_count, pdf_path)]

        chunks_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[tuple[int, int, Path]] = []
        for start_index in range(0, page_count, chunk_pages):
            end_index = min(start_index + chunk_pages, page_count)
            chunk_path = (
                chunks_dir
                / f"{pdf_path.stem}_p{start_index + 1:04d}_{end_index:04d}.pdf"
            )
            if not chunk_path.exists():
                target = pdfium.PdfDocument.new()
                try:
                    target.import_pages(source, pages=list(range(start_index, end_index)))
                    target.save(chunk_path)
                finally:
                    target.close()
            chunks.append((start_index + 1, end_index, chunk_path))
        return chunks
    finally:
        source.close()


def export_chunk(
    *,
    converter: Any,
    chunk_path: Path,
    start_page: int,
    end_page: int,
) -> tuple[str, list[dict[str, Any]], str, int | None]:
    result = converter.convert(chunk_path)
    document = result.document
    markdown = document.export_to_markdown()
    tables, table_text = export_tables(document)
    adjusted_tables: list[dict[str, Any]] = []
    for table in tables:
        adjusted = dict(table)
        adjusted["source_start_page"] = start_page
        adjusted["source_end_page"] = end_page
        adjusted_tables.append(adjusted)
    header = f"\n\n<!-- source_pages: {start_page}-{end_page} -->\n\n"
    return header + markdown, adjusted_tables, table_text, count_pages(document)


_WORKER_CONVERTER: Any | None = None
_WORKER_SETTINGS: dict[str, Any] | None = None


def init_worker(settings: dict[str, Any]) -> None:
    global _WORKER_CONVERTER, _WORKER_SETTINGS
    _WORKER_SETTINGS = settings
    _WORKER_CONVERTER = build_standard_converter(settings)


def convert_chunk_worker(task: tuple[int, int, int, int, str]) -> dict[str, Any]:
    global _WORKER_CONVERTER
    chunk_index, chunk_count, start_page, end_page, chunk_path = task
    started = time.perf_counter()
    try:
        if _WORKER_CONVERTER is None:
            if _WORKER_SETTINGS is None:
                raise RuntimeError("Worker converter settings were not initialized.")
            _WORKER_CONVERTER = build_standard_converter(_WORKER_SETTINGS)
        markdown, tables, table_text, detected_pages = export_chunk(
            converter=_WORKER_CONVERTER,
            chunk_path=Path(chunk_path),
            start_page=start_page,
            end_page=end_page,
        )
        return {
            "status": "ok",
            "error": "",
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "start_page": start_page,
            "end_page": end_page,
            "markdown": markdown,
            "tables": tables,
            "table_text": table_text,
            "detected_pages": detected_pages,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": str(exc),
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "start_page": start_page,
            "end_page": end_page,
            "markdown": "",
            "tables": [],
            "table_text": "",
            "detected_pages": None,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }


def convert_chunks(
    *,
    settings: dict[str, Any],
    chunks: list[tuple[int, int, Path]],
    workers: int,
) -> list[dict[str, Any]]:
    tasks = [
        (index, len(chunks), start_page, end_page, str(chunk_path))
        for index, (start_page, end_page, chunk_path) in enumerate(chunks, start=1)
    ]
    if workers <= 1:
        converter = build_standard_converter(settings)
        results: list[dict[str, Any]] = []
        for chunk_index, chunk_count, start_page, end_page, chunk_path in tasks:
            print(
                f"    chunk {chunk_index}/{chunk_count} pages {start_page}-{end_page}",
                flush=True,
            )
            started = time.perf_counter()
            try:
                markdown, tables, table_text, detected_pages = export_chunk(
                    converter=converter,
                    chunk_path=Path(chunk_path),
                    start_page=start_page,
                    end_page=end_page,
                )
                results.append(
                    {
                        "status": "ok",
                        "error": "",
                        "chunk_index": chunk_index,
                        "chunk_count": chunk_count,
                        "start_page": start_page,
                        "end_page": end_page,
                        "markdown": markdown,
                        "tables": tables,
                        "table_text": table_text,
                        "detected_pages": detected_pages,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "status": "failed",
                        "error": str(exc),
                        "chunk_index": chunk_index,
                        "chunk_count": chunk_count,
                        "start_page": start_page,
                        "end_page": end_page,
                        "markdown": "",
                        "tables": [],
                        "table_text": "",
                        "detected_pages": None,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                )
        return results

    print(
        f"    running {len(tasks)} chunks with {workers} worker processes",
        flush=True,
    )
    results = []
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=init_worker,
        initargs=(settings,),
    ) as executor:
        future_to_task = {
            executor.submit(convert_chunk_worker, task): task for task in tasks
        }
        for future in as_completed(future_to_task):
            result = future.result()
            results.append(result)
            print(
                f"    chunk {result['chunk_index']}/{result['chunk_count']} "
                f"pages {result['start_page']}-{result['end_page']} "
                f"{result['status']} {result['elapsed_seconds']}s",
                flush=True,
            )
    return sorted(results, key=lambda item: int(item["chunk_index"]))


def normalize_text(value: str) -> str:
    text = value.lower().replace("\u00a0", " ")
    text = re.sub(r"[\s`*_#>\-|:;,./\\()\[\]{}]+", "", text)
    return text


def build_line_snippets(text: str, *, min_chars: int, max_snippets: int) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = normalize_text(line)
        if len(normalized) < min_chars:
            continue
        if len(normalized) > 160:
            parts = [normalized[index : index + 120] for index in range(0, len(normalized), 120)]
        else:
            parts = [normalized]
        for part in parts:
            if len(part) < min_chars or part in seen:
                continue
            seen.add(part)
            snippets.append(part)
            if len(snippets) >= max_snippets:
                return snippets
    return snippets


def build_table_cell_snippets(
    tables: list[dict[str, Any]],
    table_text: str,
    *,
    max_snippets: int,
) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = normalize_text(str(value))
        if len(text) < 2 or text in seen:
            return
        seen.add(text)
        snippets.append(text)

    for table in tables:
        for header in table.get("headers", []):
            add(header)
        for line in str(table.get("csv", "")).splitlines():
            for cell in line.split(","):
                add(cell)
                if len(snippets) >= max_snippets:
                    return snippets

    if not snippets:
        for line in table_text.splitlines():
            normalized = normalize_text(line)
            if len(normalized) >= 6 and normalized not in seen:
                seen.add(normalized)
                snippets.append(normalized)
                if len(snippets) >= max_snippets:
                    break
    return snippets


def recall(snippets: list[str], observed_text: str) -> tuple[float, int, int]:
    if not snippets:
        return 1.0, 0, 0
    observed = normalize_text(observed_text)
    hits = sum(1 for snippet in snippets if snippet in observed)
    return hits / len(snippets), hits, len(snippets)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in RESULT_FIELDS})


def config_from_row(row: dict[str, Any]) -> MatrixConfig:
    settings = json.loads(str(row["settings_json"]))
    return MatrixConfig("standard", str(row["config_id"]), settings)


def load_existing_rows(results_path: Path) -> list[dict[str, Any]]:
    if not results_path.exists():
        return []
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    return list(payload.get("results", []))


def convert_pdf(
    *,
    config: MatrixConfig,
    pdf_path: Path,
    run_dir: Path,
    run_id: str,
    case_id: str,
    expected_pages: int | None,
    chunk_pages: int,
    workers: int,
) -> dict[str, Any]:
    extracted_dir = run_dir / "extracted" / "standard" / config.config_id / case_id
    started = time.perf_counter()
    try:
        chunks = pdf_chunks(
            pdf_path=pdf_path,
            chunks_dir=run_dir / "chunks" / f"{pdf_path.stem}_p{chunk_pages}",
            chunk_pages=chunk_pages,
        )
        chunk_results = convert_chunks(
            settings=config.settings,
            chunks=chunks,
            workers=workers,
        )
        failed_chunks = [
            chunk for chunk in chunk_results if chunk.get("status") != "ok"
        ]
        if failed_chunks:
            sample = "; ".join(
                (
                    f"pages {chunk['start_page']}-{chunk['end_page']}: "
                    f"{chunk['error']}"
                )
                for chunk in failed_chunks[:5]
            )
            raise RuntimeError(
                f"{len(failed_chunks)} chunk(s) failed for {config.config_id}: {sample}"
            )
        markdown_parts: list[str] = []
        tables: list[dict[str, Any]] = []
        table_text_parts: list[str] = []
        detected_pages_total = 0
        for chunk in chunk_results:
            markdown_parts.append(str(chunk["markdown"]))
            table_text_parts.append(str(chunk["table_text"]))
            for table in chunk["tables"]:
                adjusted = dict(table)
                adjusted["index"] = len(tables) + 1
                tables.append(adjusted)
            detected_pages_total += chunk["detected_pages"] or (
                int(chunk["end_page"]) - int(chunk["start_page"]) + 1
            )
        converted_at = time.perf_counter()
        markdown = "\n".join(markdown_parts)
        table_text = "\n".join(table_text_parts)
        extracted_dir.mkdir(parents=True, exist_ok=True)
        (extracted_dir / "markdown.md").write_text(markdown, encoding="utf-8")
        write_json(extracted_dir / "tables.json", tables)
        (extracted_dir / "table_text.txt").write_text(table_text, encoding="utf-8")
        finished = time.perf_counter()
        page_denominator = expected_pages or detected_pages_total
        return {
            "run_id": run_id,
            "case_id": case_id,
            "filename": pdf_path.name,
            "pipeline": "standard",
            "config_id": config.config_id,
            "status": "ok",
            "error": "",
            "expected_pages": expected_pages,
            "detected_pages": detected_pages_total,
            "file_size_mb": round(pdf_path.stat().st_size / 1024 / 1024, 3),
            "detected_tables": len(tables),
            "chunk_pages": chunk_pages,
            "chunk_count": len(chunks),
            "workers": workers,
            "markdown_chars": len(markdown),
            "table_text_chars": len(table_text),
            "convert_seconds": round(converted_at - started, 3),
            "total_seconds": round(finished - started, 3),
            "seconds_per_page": (
                round((finished - started) / page_denominator, 3)
                if page_denominator
                else None
            ),
            "settings_json": json.dumps(config.settings, ensure_ascii=False, sort_keys=True),
            **config.settings,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "run_id": run_id,
            "case_id": case_id,
            "filename": pdf_path.name,
            "pipeline": "standard",
            "config_id": config.config_id,
            "status": "failed",
            "error": str(exc),
            "expected_pages": expected_pages,
            "detected_pages": None,
            "file_size_mb": round(pdf_path.stat().st_size / 1024 / 1024, 3),
            "detected_tables": 0,
            "chunk_pages": chunk_pages,
            "chunk_count": None,
            "workers": workers,
            "markdown_chars": 0,
            "table_text_chars": 0,
            "convert_seconds": round(elapsed, 3),
            "total_seconds": round(elapsed, 3),
            "seconds_per_page": round(elapsed / expected_pages, 3)
            if expected_pages
            else None,
            "settings_json": json.dumps(config.settings, ensure_ascii=False, sort_keys=True),
            **config.settings,
        }


def read_extraction(run_dir: Path, config_id: str, case_id: str) -> tuple[str, list[dict[str, Any]], str]:
    extracted_dir = run_dir / "extracted" / "standard" / config_id / case_id
    markdown = (extracted_dir / "markdown.md").read_text(encoding="utf-8")
    tables = json.loads((extracted_dir / "tables.json").read_text(encoding="utf-8"))
    table_text_path = extracted_dir / "table_text.txt"
    if table_text_path.exists():
        table_text = table_text_path.read_text(encoding="utf-8")
    else:
        table_text = "\n".join(str(table.get("csv", "")) for table in tables)
    return markdown, tables, table_text


def apply_relative_metrics(
    *,
    rows: list[dict[str, Any]],
    run_dir: Path,
    baseline_config_id: str,
    case_id: str,
) -> None:
    baseline = next(
        (
            row
            for row in rows
            if row["config_id"] == baseline_config_id and row["status"] == "ok"
        ),
        None,
    )
    if baseline is None:
        for row in rows:
            row.update(
                {
                    "relative_markdown_recall": 0.0,
                    "relative_markdown_hits": 0,
                    "relative_markdown_total": 0,
                    "relative_table_recall": 0.0,
                    "relative_table_hits": 0,
                    "relative_table_total": 0,
                    "relative_table_count_ratio": 0.0,
                    "relative_markdown_chars_ratio": 0.0,
                    "relative_overall": 0.0,
                }
            )
        return

    baseline_markdown, baseline_tables, baseline_table_text = read_extraction(
        run_dir,
        baseline_config_id,
        case_id,
    )
    markdown_snippets = build_line_snippets(
        baseline_markdown,
        min_chars=16,
        max_snippets=2500,
    )
    table_snippets = build_table_cell_snippets(
        baseline_tables,
        baseline_table_text,
        max_snippets=5000,
    )
    baseline_table_count = int(baseline.get("detected_tables") or 0)
    baseline_markdown_chars = int(baseline.get("markdown_chars") or 0)

    for row in rows:
        if row["status"] != "ok":
            row.update(
                {
                    "relative_markdown_recall": 0.0,
                    "relative_markdown_hits": 0,
                    "relative_markdown_total": len(markdown_snippets),
                    "relative_table_recall": 0.0,
                    "relative_table_hits": 0,
                    "relative_table_total": len(table_snippets),
                    "relative_table_count_ratio": 0.0,
                    "relative_markdown_chars_ratio": 0.0,
                    "relative_overall": 0.0,
                }
            )
            continue

        markdown, _tables, table_text = read_extraction(
            run_dir,
            str(row["config_id"]),
            case_id,
        )
        markdown_recall, markdown_hits, markdown_total = recall(
            markdown_snippets,
            markdown,
        )
        table_recall, table_hits, table_total = recall(
            table_snippets,
            table_text + "\n" + markdown,
        )
        table_count_ratio = (
            min((int(row.get("detected_tables") or 0) / baseline_table_count), 1.0)
            if baseline_table_count
            else 1.0
        )
        markdown_chars_ratio = (
            min((int(row.get("markdown_chars") or 0) / baseline_markdown_chars), 1.0)
            if baseline_markdown_chars
            else 1.0
        )
        relative_overall = (
            (0.45 * markdown_recall)
            + (0.35 * table_recall)
            + (0.10 * table_count_ratio)
            + (0.10 * markdown_chars_ratio)
        )
        row.update(
            {
                "relative_markdown_recall": round(markdown_recall, 4),
                "relative_markdown_hits": markdown_hits,
                "relative_markdown_total": markdown_total,
                "relative_table_recall": round(table_recall, 4),
                "relative_table_hits": table_hits,
                "relative_table_total": table_total,
                "relative_table_count_ratio": round(table_count_ratio, 4),
                "relative_markdown_chars_ratio": round(markdown_chars_ratio, 4),
                "relative_overall": round(relative_overall, 4),
            }
        )


def sorted_ok_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [row for row in rows if row["status"] == "ok"],
        key=lambda row: (
            -(float(row.get("relative_overall") or 0.0)),
            float(row.get("seconds_per_page") or 999999.0),
        ),
    )


def speedup(base: dict[str, Any], row: dict[str, Any]) -> float | None:
    base_spp = base.get("seconds_per_page")
    row_spp = row.get("seconds_per_page")
    if not base_spp or not row_spp:
        return None
    return float(base_spp) / float(row_spp)


def write_markdown_report(
    *,
    path: Path,
    run_id: str,
    case_id: str,
    pdf_path: Path,
    rows: list[dict[str, Any]],
    baseline_config_id: str,
    depth: str,
    chunk_pages: int,
    workers: int,
) -> None:
    ok_rows = sorted_ok_rows(rows)
    failed_rows = [row for row in rows if row["status"] != "ok"]
    chunk_page_values = sorted(
        {
            str(row.get("chunk_pages"))
            for row in rows
            if row.get("chunk_pages") is not None
        }
    )
    worker_values = sorted(
        {str(row.get("workers")) for row in rows if row.get("workers") is not None}
    )
    baseline = next(
        (row for row in rows if row["config_id"] == baseline_config_id),
        ok_rows[0] if ok_rows else None,
    )
    fastest = min(
        ok_rows,
        key=lambda row: float(row.get("seconds_per_page") or 999999.0),
        default=None,
    )
    high_quality = [
        row for row in ok_rows if float(row.get("relative_overall") or 0.0) >= 0.95
    ]
    fastest_high_quality = min(
        high_quality,
        key=lambda row: float(row.get("seconds_per_page") or 999999.0),
        default=None,
    )
    total_seconds = sum(float(row.get("total_seconds") or 0.0) for row in rows)
    sec_values = [float(row["seconds_per_page"]) for row in ok_rows if row.get("seconds_per_page")]
    median_spp = statistics.median(sec_values) if sec_values else None

    lines = [
        f"# case13 Standard Docling PoC ({run_id})",
        "",
        "## Scope",
        f"- PDF: `{pdf_path}`",
        f"- Case ID: `{case_id}`",
        f"- Standard config depth: `{depth}`",
        f"- Page chunk size(s): `{', '.join(chunk_page_values) or chunk_pages}` pages",
        f"- Worker process setting(s): `{', '.join(worker_values) or workers}`",
        f"- Baseline for relative accuracy: `{baseline_config_id}`",
        "- Accuracy note: case13 has no registered ground truth. Accuracy values here are relative retention metrics against the baseline extraction, not human-verified absolute accuracy.",
        "",
        "## Summary",
        f"- Configs attempted: {len(rows)}",
        f"- Successful configs: {len(ok_rows)}",
        f"- Failed configs: {len(failed_rows)}",
        f"- Total measured time: {total_seconds / 60:.2f} min",
    ]
    if median_spp is not None:
        lines.append(f"- Median seconds/page: {median_spp:.3f}")
    if baseline:
        lines.append(
            f"- Baseline speed: {baseline.get('seconds_per_page')} sec/page, "
            f"tables={baseline.get('detected_tables')}, "
            f"markdown_chars={baseline.get('markdown_chars')}"
        )
    if fastest:
        factor = speedup(baseline, fastest) if baseline else None
        suffix = f" ({factor:.2f}x vs baseline)" if factor else ""
        lines.append(
            f"- Fastest config: `{fastest['config_id']}` at "
            f"{fastest.get('seconds_per_page')} sec/page{suffix}"
        )
    if fastest_high_quality:
        factor = speedup(baseline, fastest_high_quality) if baseline else None
        suffix = f" ({factor:.2f}x vs baseline)" if factor else ""
        lines.append(
            f"- Fastest high-retention config (relative_overall >= 0.95): "
            f"`{fastest_high_quality['config_id']}` at "
            f"{fastest_high_quality.get('seconds_per_page')} sec/page{suffix}"
        )

    lines.extend(
        [
            "",
            "## Ranking",
            "",
            "| Rank | Config | Relative overall | Markdown recall | Table recall | Tables | Sec/page | Total sec | Workers | Notes |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for rank, row in enumerate(ok_rows, start=1):
        notes: list[str] = []
        if not row.get("do_table_structure"):
            notes.append("table_structure=false")
        if row.get("tableformer_mode") == "fast":
            notes.append("TableFormer fast")
        if not row.get("do_cell_matching"):
            notes.append("cell_matching=false")
        if not row.get("do_ocr"):
            notes.append("ocr=false")
        if row.get("force_full_page_ocr"):
            notes.append("full_page_ocr")
        if row.get("force_backend_text"):
            notes.append("backend_text")
        if float(row.get("images_scale") or 1.0) != 1.0:
            notes.append(f"scale={row.get('images_scale')}")
        if row.get("batch_profile") != "default":
            notes.append(f"batch={row.get('batch_profile')}")
        lines.append(
            f"| {rank} | `{row['config_id']}` | "
            f"{float(row.get('relative_overall') or 0.0):.4f} | "
            f"{float(row.get('relative_markdown_recall') or 0.0):.4f} | "
            f"{float(row.get('relative_table_recall') or 0.0):.4f} | "
            f"{row.get('detected_tables')} | "
            f"{row.get('seconds_per_page')} | "
            f"{row.get('total_seconds')} | "
            f"{row.get('workers')} | "
            f"{', '.join(notes)} |"
        )

    if failed_rows:
        lines.extend(["", "## Failures", ""])
        for row in failed_rows:
            lines.append(f"- `{row['config_id']}`: {row.get('error')}")

    lines.extend(
        [
            "",
            "## Interpretation Guide",
            "- `relative_markdown_recall` samples baseline markdown lines/chunks and checks whether each appears in the candidate extraction.",
            "- `relative_table_recall` samples baseline table headers/cells and checks whether each appears in candidate tables or markdown.",
            "- `relative_overall = 0.45*markdown + 0.35*table + 0.10*table_count_ratio + 0.10*markdown_chars_ratio`.",
            "- For true absolute accuracy, add human-reviewed expected anchors/table cells for case13 and re-run the regular benchmark scorer.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html_report(md_path: Path, html_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    escaped = html.escape(text)
    html_text = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>case13 Standard Docling PoC</title>"
        "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:1180px;"
        "margin:32px auto;line-height:1.5;color:#222}"
        "pre{white-space:pre-wrap;background:#f6f8fa;padding:16px;"
        "border:1px solid #d0d7de;border-radius:6px}</style></head>"
        f"<body><pre>{escaped}</pre></body></html>"
    )
    html_path.write_text(html_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Standard Docling parameter PoC for one large PDF.",
    )
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--case-id", default="C13")
    parser.add_argument("--run-id")
    parser.add_argument("--depth", choices=["focused", "full"], default="focused")
    parser.add_argument("--baseline-config", default="std_baseline_accurate")
    parser.add_argument("--config-id", action="append", default=[])
    parser.add_argument(
        "--chunk-pages",
        type=int,
        default=8,
        help="Convert the PDF in page chunks to keep large documents measurable.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel worker processes for page chunks.",
    )
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf
    if not pdf_path.is_absolute():
        pdf_path = ROOT / pdf_path
    pdf_path = pdf_path.resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    run_id = args.run_id or f"{args.case_id.lower()}_standard_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = POC_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.json"
    expected_pages = pdf_page_count(pdf_path)

    configs = build_standard_configs(args.depth)
    if args.config_id:
        wanted = set(args.config_id)
        configs = [config for config in configs if config.config_id in wanted]
        missing = wanted - {config.config_id for config in configs}
        if missing:
            raise ValueError(f"Unknown config_id(s): {', '.join(sorted(missing))}")

    configs.sort(key=lambda config: 0 if config.config_id == args.baseline_config else 1)
    rows = load_existing_rows(results_path) if args.resume else []
    completed = {row["config_id"] for row in rows if row.get("status") == "ok"}

    metadata = {
        "run_id": run_id,
        "case_id": args.case_id,
        "pdf_path": str(pdf_path),
        "depth": args.depth,
        "baseline_config": args.baseline_config,
        "expected_pages": expected_pages,
        "chunk_pages": args.chunk_pages,
        "workers": args.workers,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config_count": len(configs),
        "relative_metric_note": (
            "No ground truth is registered for this PDF; metrics are relative "
            "to the baseline Standard extraction."
        ),
    }
    write_json(run_dir / "metadata.json", metadata)

    for index, config in enumerate(configs, start=1):
        if args.resume and config.config_id in completed:
            print(f"[{index}/{len(configs)}] skip {config.config_id} (resume)", flush=True)
            continue
        print(f"[{index}/{len(configs)}] start {config.config_id}", flush=True)
        row = convert_pdf(
            config=config,
            pdf_path=pdf_path,
            run_dir=run_dir,
            run_id=run_id,
            case_id=args.case_id,
            expected_pages=expected_pages,
            chunk_pages=args.chunk_pages,
            workers=args.workers,
        )
        rows = [existing for existing in rows if existing["config_id"] != config.config_id]
        rows.append(row)
        apply_relative_metrics(
            rows=rows,
            run_dir=run_dir,
            baseline_config_id=args.baseline_config,
            case_id=args.case_id,
        )
        write_json(results_path, {"metadata": metadata, "results": rows})
        write_csv(run_dir / "results.csv", rows)
        write_markdown_report(
            path=run_dir / "report.md",
            run_id=run_id,
            case_id=args.case_id,
            pdf_path=pdf_path,
            rows=rows,
            baseline_config_id=args.baseline_config,
            depth=args.depth,
            chunk_pages=args.chunk_pages,
            workers=args.workers,
        )
        write_html_report(run_dir / "report.md", run_dir / "report.html")
        print(
            f"[{index}/{len(configs)}] done {config.config_id}: "
            f"status={row['status']} sec/page={row.get('seconds_per_page')} "
            f"relative_overall={row.get('relative_overall')}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
