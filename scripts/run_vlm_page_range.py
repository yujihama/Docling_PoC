from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docling_openai_vlm import (  # noqa: E402
    build_openai_vlm_converter,
    clear_vlm_usage_events,
    get_vlm_usage_events,
)
from settings import load_settings  # noqa: E402

SETTINGS = load_settings()


def model_slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", model).strip("_").lower()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run provider VLM on a PDF page range.")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--start-page", required=True, type=int)
    parser.add_argument("--end-page", required=True, type=int)
    parser.add_argument("--model", default=SETTINGS.models.primary)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--response-format", default=SETTINGS.vlm.response_format, choices=["markdown", "html"])
    parser.add_argument("--prompt-variant", default=SETTINGS.vlm.prompt_variant, choices=["strict_preserve", "table_first"])
    parser.add_argument("--scale", type=float, default=SETTINGS.vlm.scale)
    parser.add_argument("--max-completion-tokens", type=int, default=SETTINGS.vlm.max_completion_tokens)
    parser.add_argument("--reasoning-effort", default=SETTINGS.vlm.reasoning_effort)
    parser.add_argument("--image-detail", default=SETTINGS.vlm.image_detail or "auto", choices=["auto", "low", "high"])
    parser.add_argument("--timeout-seconds", type=float, default=SETTINGS.vlm.timeout_seconds)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf = args.pdf if args.pdf.is_absolute() else (ROOT / args.pdf).resolve()
    out_dir = args.out_dir or (
        SETTINGS.outputs.routing_runs_dir
        / f"vlm_{pdf.stem}_p{args.start_page}_{args.end_page}_{model_slug(args.model)}"
    )
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    clear_vlm_usage_events()
    converter = build_openai_vlm_converter(
        model=args.model,
        max_completion_tokens=args.max_completion_tokens,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout_seconds,
        scale=args.scale,
        response_format=args.response_format,
        prompt_variant=args.prompt_variant,
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
    started = time.perf_counter()
    result = converter.convert(pdf, page_range=(args.start_page, args.end_page))
    elapsed = round(time.perf_counter() - started, 3)
    document = result.document
    markdown = document.export_to_markdown()

    tables: list[dict[str, object]] = []
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

    summary = {
        "pdf": str(pdf),
        "pages": f"{args.start_page}-{args.end_page}",
        "mode": "OPENAI_VLM_DIRECT",
        "model": args.model,
        "response_format": args.response_format,
        "prompt_variant": args.prompt_variant,
        "image_detail": args.image_detail,
        "scale": args.scale,
        "elapsed_seconds": elapsed,
        "markdown_chars": len(markdown),
        "table_count": len(tables),
        "tables": [
            {
                "index": table["index"],
                "rows": table["rows"],
                "columns": table["columns"],
                "headers": table["headers"],
            }
            for table in tables
        ],
        "usage_events": get_vlm_usage_events(),
        "config_sources": list(SETTINGS.config_sources),
    }
    (out_dir / "vlm_p30_32.md").write_text(markdown, encoding="utf-8")
    (out_dir / "tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
