from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from routing_pipeline import RoutedPdfOptions, run_routed_pdf
from settings import load_settings, settings_to_safe_dict


ROOT = Path(__file__).resolve().parent


def parse_pages(value: str | None) -> list[int]:
    if not value:
        return []
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def parse_config_arg() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path)
    return parser.parse_known_args()[0]


def configured_bool(value: bool | None, default: bool) -> bool:
    return bool(default) if value is None else bool(value)


def parse_args(settings: object) -> argparse.Namespace:
    routed_defaults = RoutedPdfOptions.from_settings(settings)
    parser = argparse.ArgumentParser(
        description="Run CPU-oriented Docling routing with OCR/VLM reconciliation.",
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model", default=routed_defaults.model)
    parser.add_argument(
        "--compare-mode",
        choices=["ocr-vlm", "vlm-vlm"],
        default=routed_defaults.reconcile_compare_mode.replace("_", "-"),
        help="Comparison source pair used on IMAGE_RECONCILE pages.",
    )
    parser.add_argument(
        "--secondary-model",
        default=routed_defaults.secondary_model,
        help="Second OpenAI model used when --compare-mode vlm-vlm.",
    )
    parser.add_argument(
        "--max-completion-tokens",
        type=int,
        default=routed_defaults.max_completion_tokens,
    )
    parser.add_argument(
        "--reasoning-effort",
        default=routed_defaults.reasoning_effort,
        choices=["none", "low", "medium", "high", "xhigh"],
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=routed_defaults.timeout_seconds,
    )
    parser.add_argument("--vlm-scale", type=float, default=routed_defaults.vlm_scale)
    parser.add_argument(
        "--response-format",
        choices=["markdown", "html"],
        default="markdown",
    )
    parser.add_argument(
        "--prompt-variant",
        choices=["strict_preserve", "table_first"],
        default=routed_defaults.prompt_variant,
    )
    parser.add_argument(
        "--force-reconcile-pages",
        help="Comma-separated pages or ranges, for example 2,5-7.",
    )
    parser.add_argument(
        "--enable-embedded-visual-append",
        dest="enable_embedded_visual_append",
        action="store_true",
        default=None,
        help="Enable OCR/VLM append for embedded visual regions on text-layer pages.",
    )
    parser.add_argument(
        "--disable-embedded-visual-append",
        dest="enable_embedded_visual_append",
        action="store_false",
        help="Disable OCR/VLM append for embedded visual regions on text-layer pages.",
    )
    parser.add_argument(
        "--parallel-reconcile-candidates",
        dest="parallel_reconcile_candidates",
        action="store_true",
        default=None,
        help="Run reconcile candidates concurrently.",
    )
    parser.add_argument(
        "--disable-parallel-reconcile-candidates",
        dest="parallel_reconcile_candidates",
        action="store_false",
        help="Run reconcile candidates sequentially instead of in parallel.",
    )
    parser.add_argument(
        "--max-parallel-table-groups",
        type=int,
        default=routed_defaults.max_parallel_table_groups,
        help="Maximum number of independent standard table groups to run in parallel.",
    )
    parser.add_argument(
        "--use-coordinate-table-reconstruction",
        dest="use_coordinate_table_reconstruction",
        action="store_true",
        default=None,
        help=(
            "Experimental: try PDF coordinate-grid reconstruction on text-table pages. "
            "With --enable-table-vlm-fallback, low-confidence coordinate output falls back to VLM."
        ),
    )
    parser.add_argument(
        "--disable-coordinate-table-reconstruction",
        dest="use_coordinate_table_reconstruction",
        action="store_false",
        help="Disable PDF coordinate-grid reconstruction on text-table pages.",
    )
    parser.add_argument(
        "--enable-table-vlm-fallback",
        dest="enable_table_vlm_fallback",
        action="store_true",
        default=None,
        help="Fallback from low-confidence coordinate table reconstruction to OpenAI VLM.",
    )
    parser.add_argument(
        "--disable-table-vlm-fallback",
        dest="enable_table_vlm_fallback",
        action="store_false",
        help="Disable table VLM fallback.",
    )
    parser.add_argument(
        "--table-vlm-model",
        default=routed_defaults.table_vlm_model,
        help="OpenAI model for normal table VLM fallback.",
    )
    parser.add_argument(
        "--large-table-vlm-model",
        default=routed_defaults.large_table_vlm_model,
        help="OpenAI model for large, dense, or high-risk table VLM fallback.",
    )
    parser.add_argument(
        "--table-vlm-prompt-variant",
        choices=["strict_preserve", "table_first"],
        default=routed_defaults.table_vlm_prompt_variant,
        help="Prompt variant used for table VLM fallback.",
    )
    parser.add_argument(
        "--table-vlm-reasoning-effort",
        default=routed_defaults.table_vlm_reasoning_effort,
        choices=["none", "low", "medium", "high", "xhigh"],
        help="Reasoning effort used for table VLM fallback.",
    )
    parser.add_argument(
        "--enable-reconcile-table-fallback",
        dest="enable_reconcile_table_fallback",
        action="store_true",
        default=None,
        help="Enable local table VLM fallback after reconcile warnings.",
    )
    parser.add_argument(
        "--disable-reconcile-table-fallback",
        dest="enable_reconcile_table_fallback",
        action="store_false",
        help=(
            "Disable local table VLM fallback when reconciliation produces unknown "
            "tokens or table-structure disagreements."
        ),
    )
    parser.add_argument(
        "--reconcile-table-fallback-model",
        default=routed_defaults.reconcile_table_fallback_model,
        help="OpenAI model used for local table fallback after reconcile warnings.",
    )
    parser.add_argument(
        "--reconcile-table-fallback-prompt-variant",
        choices=["strict_preserve", "table_first"],
        default=routed_defaults.reconcile_table_fallback_prompt_variant,
        help="Prompt variant used for local table fallback after reconcile warnings.",
    )
    parser.add_argument(
        "--reconcile-table-fallback-reasoning-effort",
        default=routed_defaults.reconcile_table_fallback_reasoning_effort,
        choices=["none", "low", "medium", "high", "xhigh"],
        help="Reasoning effort used for local table fallback after reconcile warnings.",
    )
    parser.add_argument(
        "--enable-vlm-coordinate-quality-check",
        dest="enable_vlm_coordinate_quality_check",
        action="store_true",
        default=None,
        help="Enable coordinate evidence checks for TEXT_TABLE_VLM output.",
    )
    parser.add_argument(
        "--disable-vlm-coordinate-quality-check",
        dest="enable_vlm_coordinate_quality_check",
        action="store_false",
        help="Disable coordinate evidence checks for TEXT_TABLE_VLM output.",
    )
    parser.add_argument(
        "--enable-vlm-coordinate-auto-correct",
        dest="enable_vlm_coordinate_auto_correct",
        action="store_true",
        default=None,
        help="Enable conservative cell auto-correction from coordinate values.",
    )
    parser.add_argument(
        "--disable-vlm-coordinate-auto-correct",
        dest="enable_vlm_coordinate_auto_correct",
        action="store_false",
        help="Disable conservative cell auto-correction from coordinate values.",
    )
    parser.add_argument(
        "--embedded-visual-min-area-ratio",
        type=float,
        default=routed_defaults.embedded_visual_min_area_ratio,
        help="Minimum page-area ratio for embedded visual region detection.",
    )
    parser.add_argument(
        "--save-outputs",
        dest="save_outputs",
        action="store_true",
        default=None,
        help="Write output files even when the configured default disables saving.",
    )
    parser.add_argument(
        "--no-save",
        dest="save_outputs",
        action="store_false",
        help="Run without writing output files.",
    )
    return parser.parse_args()


def main() -> int:
    config_args = parse_config_arg()
    settings = load_settings(config_args.config)
    safe_settings = settings_to_safe_dict(settings)
    args = parse_args(settings)
    pdf_path = args.pdf
    if not pdf_path.is_absolute():
        pdf_path = (ROOT / pdf_path).resolve()
    save_outputs = (
        bool(args.save_outputs)
        if args.save_outputs is not None
        else bool(settings.outputs.save_outputs)
    )
    output_root = args.output_root or settings.outputs.root
    options = RoutedPdfOptions.from_settings(
        settings,
        model=args.model,
        reconcile_compare_mode=args.compare_mode.replace("-", "_"),
        secondary_model=args.secondary_model,
        max_completion_tokens=args.max_completion_tokens,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout_seconds,
        vlm_scale=args.vlm_scale,
        response_format=args.response_format,
        prompt_variant=args.prompt_variant,
        enable_embedded_visual_append=configured_bool(
            args.enable_embedded_visual_append,
            settings.routing.enable_embedded_visual_append,
        ),
        parallel_reconcile_candidates=configured_bool(
            args.parallel_reconcile_candidates,
            settings.routing.parallel_reconcile_candidates,
        ),
        max_parallel_table_groups=args.max_parallel_table_groups,
        use_coordinate_table_reconstruction=configured_bool(
            args.use_coordinate_table_reconstruction,
            settings.routing.use_coordinate_table_reconstruction,
        ),
        enable_table_vlm_fallback=configured_bool(
            args.enable_table_vlm_fallback,
            settings.routing.enable_table_vlm_fallback,
        ),
        table_vlm_model=args.table_vlm_model,
        large_table_vlm_model=args.large_table_vlm_model,
        table_vlm_prompt_variant=args.table_vlm_prompt_variant,
        table_vlm_reasoning_effort=args.table_vlm_reasoning_effort,
        enable_reconcile_table_fallback=configured_bool(
            args.enable_reconcile_table_fallback,
            settings.routing.enable_reconcile_table_fallback,
        ),
        reconcile_table_fallback_model=args.reconcile_table_fallback_model,
        reconcile_table_fallback_prompt_variant=args.reconcile_table_fallback_prompt_variant,
        reconcile_table_fallback_reasoning_effort=args.reconcile_table_fallback_reasoning_effort,
        enable_vlm_coordinate_quality_check=configured_bool(
            args.enable_vlm_coordinate_quality_check,
            settings.routing.enable_vlm_coordinate_quality_check,
        ),
        enable_vlm_coordinate_auto_correct=configured_bool(
            args.enable_vlm_coordinate_auto_correct,
            settings.routing.enable_vlm_coordinate_auto_correct,
        ),
        embedded_visual_min_area_ratio=args.embedded_visual_min_area_ratio,
        force_reconcile_pages=parse_pages(args.force_reconcile_pages),
        save_outputs=save_outputs,
        output_root=str(output_root),
        resolved_settings=safe_settings,
        config_sources=list(settings.config_sources),
        invocation=" ".join([Path(sys.argv[0]).name, *sys.argv[1:]]),
    )
    result = run_routed_pdf(
        pdf_path,
        options=options,
        run_id=args.run_id,
        output_dir=args.output_dir,
        progress_callback=lambda message: print(f"[routing] {message}", flush=True),
    )
    metadata = result["metadata"]
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    print(f"Output directory: {result['run_dir']}")
    if result["warnings"]:
        print("Warnings:")
        for warning in result["warnings"][:20]:
            print(
                f"- page {warning['page']} {warning['level']} "
                f"{warning['code']}: {warning['message']}"
            )
        if len(result["warnings"]) > 20:
            print(f"... {len(result['warnings']) - 20} more warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
