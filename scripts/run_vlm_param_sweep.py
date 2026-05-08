from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docling_openai_vlm import (  # noqa: E402
    build_openai_vlm_converter,
    clear_vlm_usage_events,
    get_vlm_usage_events,
)
from settings import load_settings  # noqa: E402

SETTINGS = load_settings()
DEFAULT_SWEEP_MODELS = ",".join(
    dict.fromkeys([SETTINGS.models.table_vlm, SETTINGS.models.large_table_vlm])
)
DEFAULT_SWEEP_PROMPT_VARIANTS = ",".join(
    dict.fromkeys([SETTINGS.vlm.prompt_variant, SETTINGS.routing.table_vlm_prompt_variant])
)


UNKNOWN_MARKER = "[[読み取り不明]]"


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a VLM parameter sweep on a PDF page range.")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--start-page", required=True, type=int)
    parser.add_argument("--end-page", required=True, type=int)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--models", default=DEFAULT_SWEEP_MODELS)
    parser.add_argument("--prompt-variants", default=DEFAULT_SWEEP_PROMPT_VARIANTS)
    parser.add_argument("--reasoning-efforts", default=SETTINGS.vlm.reasoning_effort)
    parser.add_argument("--image-details", default=SETTINGS.vlm.image_detail or "auto")
    parser.add_argument("--response-format", default=SETTINGS.vlm.response_format, choices=["markdown", "html"])
    parser.add_argument("--scale", type=float, default=SETTINGS.vlm.scale)
    parser.add_argument("--max-completion-tokens", type=int, default=SETTINGS.vlm.max_completion_tokens)
    parser.add_argument("--timeout-seconds", type=float, default=SETTINGS.vlm.timeout_seconds)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    return parser.parse_args()


def collect_tables(document: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(getattr(document, "tables", []), start=1):
        dataframe = table.export_to_dataframe(doc=document)
        tables.append(
            {
                "index": index,
                "rows": int(len(dataframe)),
                "columns": int(len(dataframe.columns)),
                "headers": [str(column) for column in dataframe.columns],
                "csv": dataframe.to_csv(index=False),
                "html": table.export_to_html(doc=document),
            }
        )
    return tables


def summarize_run(
    *,
    pdf: Path,
    start_page: int,
    end_page: int,
    model: str,
    prompt_variant: str,
    reasoning_effort: str,
    image_detail: str,
    response_format: str,
    scale: float,
    elapsed: float,
    markdown: str,
    tables: list[dict[str, Any]],
    usage_events: list[dict[str, Any]],
    status: str = "ok",
    error: str = "",
) -> dict[str, Any]:
    adjustment_lines = [
        line
        for line in markdown.splitlines()
        if "adjustment" in line.casefold() or UNKNOWN_MARKER in line
    ]
    zero_row_tables = sum(1 for table in tables if int(table["rows"]) == 0)
    one_row_tables = sum(1 for table in tables if int(table["rows"]) == 1)
    table_shapes = ";".join(
        f'{table["rows"]}x{table["columns"]}' for table in tables
    )
    return {
        "status": status,
        "error": error,
        "pdf": str(pdf),
        "pages": f"{start_page}-{end_page}",
        "mode": "OPENAI_VLM_SWEEP",
        "model": model,
        "response_format": response_format,
        "prompt_variant": prompt_variant,
        "reasoning_effort": reasoning_effort,
        "image_detail": image_detail,
        "scale": scale,
        "elapsed_seconds": elapsed,
        "markdown_chars": len(markdown),
        "unknown_marker_count": markdown.count(UNKNOWN_MARKER),
        "table_count": len(tables),
        "zero_row_table_count": zero_row_tables,
        "one_row_table_count": one_row_tables,
        "table_shapes": table_shapes,
        "adjustment_pool_mentions": len(adjustment_lines),
        "adjustment_pool_unknown": any(UNKNOWN_MARKER in line for line in adjustment_lines),
        "adjustment_pool_lines": adjustment_lines,
        "usage_events": usage_events,
        "tables": [
            {
                "index": table["index"],
                "rows": table["rows"],
                "columns": table["columns"],
                "headers": table["headers"],
            }
            for table in tables
        ],
    }


def write_run_outputs(
    run_dir: Path,
    markdown: str,
    tables: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "vlm_p30_32.md").write_text(markdown, encoding="utf-8")
    (run_dir / "tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_sweep_outputs(out_dir: Path, rows: list[dict[str, Any]]) -> None:
    (out_dir / "sweep_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fieldnames = [
        "status",
        "error",
        "model",
        "prompt_variant",
        "reasoning_effort",
        "image_detail",
        "elapsed_seconds",
        "markdown_chars",
        "unknown_marker_count",
        "table_count",
        "zero_row_table_count",
        "one_row_table_count",
        "table_shapes",
        "adjustment_pool_mentions",
        "adjustment_pool_unknown",
    ]
    with (out_dir / "sweep_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def main() -> int:
    args = parse_args()
    pdf = args.pdf if args.pdf.is_absolute() else (ROOT / args.pdf).resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or (
        SETTINGS.outputs.routing_runs_dir
        / f"vlm_sweep_{pdf.stem}_p{args.start_page}_{args.end_page}_{timestamp}"
    )
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    models = parse_csv_list(args.models)
    prompt_variants = parse_csv_list(args.prompt_variants)
    reasoning_efforts = parse_csv_list(args.reasoning_efforts)
    image_details = parse_csv_list(args.image_details)

    summary_path = out_dir / "sweep_summary.json"
    if args.resume and summary_path.exists():
        rows = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        rows: list[dict[str, Any]] = []
    completed_keys = {
        (
            row.get("model"),
            row.get("prompt_variant"),
            row.get("reasoning_effort"),
            row.get("image_detail"),
        )
        for row in rows
    }
    for model in models:
        for prompt_variant in prompt_variants:
            for reasoning_effort in reasoning_efforts:
                for image_detail in image_details:
                    key = (model, prompt_variant, reasoning_effort, image_detail)
                    run_name = (
                        f"{slug(model)}__{slug(prompt_variant)}__"
                        f"reasoning_{slug(reasoning_effort)}__detail_{slug(image_detail)}"
                    )
                    run_dir = out_dir / run_name
                    if args.resume and key in completed_keys:
                        print(f"Skipping {run_name}", flush=True)
                        continue
                    print(f"Running {run_name}", flush=True)
                    try:
                        clear_vlm_usage_events()
                        converter = build_openai_vlm_converter(
                            model=model,
                            max_completion_tokens=args.max_completion_tokens,
                            reasoning_effort=reasoning_effort,
                            timeout_seconds=args.timeout_seconds,
                            scale=args.scale,
                            response_format=args.response_format,
                            prompt_variant=prompt_variant,
                            image_detail=image_detail,
                            provider=SETTINGS.provider.name,
                            api_key=SETTINGS.provider.api_key,
                            chat_completions_url=SETTINGS.provider.chat_completions_url,
                            azure_endpoint=SETTINGS.provider.azure_endpoint,
                            azure_deployment=SETTINGS.provider.azure_deployment,
                            azure_api_version=SETTINGS.provider.azure_api_version,
                            max_retries=SETTINGS.openai.max_retries,
                            initial_backoff_seconds=SETTINGS.openai.initial_backoff_seconds,
                        )
                        started = time.perf_counter()
                        result = converter.convert(
                            pdf,
                            page_range=(args.start_page, args.end_page),
                        )
                        elapsed = round(time.perf_counter() - started, 3)
                        document = result.document
                        markdown = document.export_to_markdown()
                        tables = collect_tables(document)
                        summary = summarize_run(
                            pdf=pdf,
                            start_page=args.start_page,
                            end_page=args.end_page,
                            model=model,
                            prompt_variant=prompt_variant,
                            reasoning_effort=reasoning_effort,
                            image_detail=image_detail,
                            response_format=args.response_format,
                            scale=args.scale,
                            elapsed=elapsed,
                            markdown=markdown,
                            tables=tables,
                            usage_events=get_vlm_usage_events(),
                        )
                        write_run_outputs(run_dir, markdown, tables, summary)
                    except Exception as exc:
                        summary = summarize_run(
                            pdf=pdf,
                            start_page=args.start_page,
                            end_page=args.end_page,
                            model=model,
                            prompt_variant=prompt_variant,
                            reasoning_effort=reasoning_effort,
                            image_detail=image_detail,
                            response_format=args.response_format,
                            scale=args.scale,
                            elapsed=0.0,
                            markdown="",
                            tables=[],
                            usage_events=[],
                            status="error",
                            error=str(exc),
                        )
                        run_dir.mkdir(parents=True, exist_ok=True)
                        (run_dir / "summary.json").write_text(
                            json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        if args.stop_on_error:
                            rows.append(summary)
                            write_sweep_outputs(out_dir, rows)
                            raise
                    rows.append(summary)
                    completed_keys.add(key)
                    write_sweep_outputs(out_dir, rows)

    print(json.dumps({"out_dir": str(out_dir), "runs": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
