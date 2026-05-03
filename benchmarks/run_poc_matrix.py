from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from benchmarks.poc_common import (  # noqa: E402
    METRIC_FORMULA,
    POC_RUNS_DIR,
    benchmark_case,
    load_cases,
    scale_slug,
    slug,
    summarize_usage,
    write_results_csv,
)
from docling.datamodel.base_models import InputFormat  # noqa: E402
from docling.datamodel.pipeline_options import (  # noqa: E402
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: E402
from docling_openai_vlm import (  # noqa: E402
    DEFAULT_VLM_MAX_COMPLETION_TOKENS,
    DEFAULT_VLM_TIMEOUT_SECONDS,
    build_openai_vlm_converter,
    check_openai_chat_access,
    clear_vlm_usage_events,
    get_vlm_usage_events,
)


VLM_MODELS = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.2"]
VLM_RESPONSE_FORMATS = ["markdown", "html"]
VLM_SCALES = [1.0, 2.0, 2.5]
VLM_REASONING_EFFORTS = ["none", "low", "medium"]
VLM_PROMPT_VARIANTS = ["strict_preserve", "table_first"]
FIXED_MAX_COMPLETION_TOKENS = DEFAULT_VLM_MAX_COMPLETION_TOKENS
DEFAULT_PILOT_CASE_IDS = ["C01", "C03", "C05", "C06", "C09"]

# Prices are deliberately isolated here because OpenAI model pricing changes.
# They are used only for budget gating and can be overridden later without
# changing benchmark results. gpt-5.2 falls back to the gpt-5.4 class.
MODEL_PRICES_PER_MTOK = {
    "gpt-5.5": {"input": 5.00, "output": 30.00},
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.2": {"input": 2.50, "output": 15.00},
}


@dataclass(frozen=True)
class MatrixConfig:
    pipeline: str
    config_id: str
    settings: dict[str, Any]


def build_standard_configs(depth: str) -> list[MatrixConfig]:
    def standard_config(config_id: str, **overrides: Any) -> MatrixConfig:
        settings = {
            "do_table_structure": True,
            "tableformer_mode": "accurate",
            "do_cell_matching": True,
            "do_ocr": True,
            "force_full_page_ocr": False,
            "force_backend_text": False,
            "images_scale": 1.0,
            "generate_page_images": False,
            "batch_profile": "default",
            "ocr_batch_size": 4,
            "layout_batch_size": 4,
            "table_batch_size": 4,
        }
        settings.update(overrides)
        return MatrixConfig("standard", config_id, settings)

    if depth == "focused":
        return [
            standard_config("std_baseline_accurate"),
            standard_config("std_no_table_structure", do_table_structure=False),
            standard_config("std_table_fast", tableformer_mode="fast"),
            standard_config("std_table_no_cell_match", do_cell_matching=False),
            standard_config("std_no_ocr", do_ocr=False),
            standard_config("std_force_full_page_ocr", force_full_page_ocr=True),
            standard_config("std_force_backend_text", force_backend_text=True),
            standard_config(
                "std_page_images_scale_2",
                images_scale=2.0,
                generate_page_images=True,
            ),
            standard_config(
                "std_batch_small",
                batch_profile="small",
                ocr_batch_size=1,
                layout_batch_size=1,
                table_batch_size=1,
            ),
            standard_config(
                "std_batch_large",
                batch_profile="large",
                ocr_batch_size=8,
                layout_batch_size=8,
                table_batch_size=8,
            ),
        ]

    configs: list[MatrixConfig] = []
    batch_profiles = [
        ("default", 4, 4, 4),
        ("small", 1, 1, 1),
        ("large", 8, 8, 8),
    ]
    table_variants: list[tuple[bool, str | None, bool | None]] = [
        (False, None, None)
    ]
    for mode in ["fast", "accurate"]:
        for do_cell_matching in [False, True]:
            table_variants.append((True, mode, do_cell_matching))

    for do_table, table_mode, do_cell_matching in table_variants:
        for do_ocr in [False, True]:
            force_ocr_options = [False, True] if do_ocr else [False]
            for force_full_page_ocr in force_ocr_options:
                for force_backend_text in [False, True]:
                    for images_scale in [1.0, 2.0]:
                        for profile, ocr_bs, layout_bs, table_bs in batch_profiles:
                            table_part = (
                                "notable"
                                if not do_table
                                else f"{table_mode}_{'cell' if do_cell_matching else 'nocell'}"
                            )
                            config_id = (
                                f"std_{table_part}_"
                                f"{'ocr' if do_ocr else 'noocr'}_"
                                f"{'fullocr' if force_full_page_ocr else 'pageocr'}_"
                                f"{'backendtext' if force_backend_text else 'normal'}_"
                                f"s{scale_slug(images_scale)}_{profile}"
                            )
                            configs.append(
                                standard_config(
                                    config_id,
                                    do_table_structure=do_table,
                                    tableformer_mode=table_mode or "accurate",
                                    do_cell_matching=bool(do_cell_matching)
                                    if do_cell_matching is not None
                                    else True,
                                    do_ocr=do_ocr,
                                    force_full_page_ocr=force_full_page_ocr,
                                    force_backend_text=force_backend_text,
                                    images_scale=images_scale,
                                    generate_page_images=images_scale != 1.0,
                                    batch_profile=profile,
                                    ocr_batch_size=ocr_bs,
                                    layout_batch_size=layout_bs,
                                    table_batch_size=table_bs,
                                )
                            )
    return configs


def build_vlm_configs() -> list[MatrixConfig]:
    configs: list[MatrixConfig] = []
    for model in VLM_MODELS:
        for response_format in VLM_RESPONSE_FORMATS:
            for scale in VLM_SCALES:
                for reasoning_effort in VLM_REASONING_EFFORTS:
                    for prompt_variant in VLM_PROMPT_VARIANTS:
                        config_id = (
                            f"vlm_{slug(model)}_{response_format}_"
                            f"s{scale_slug(scale)}_r{reasoning_effort}_"
                            f"{slug(prompt_variant)}"
                        )
                        configs.append(
                            MatrixConfig(
                                "vlm",
                                config_id,
                                {
                                    "model": model,
                                    "response_format": response_format,
                                    "scale": scale,
                                    "reasoning_effort": reasoning_effort,
                                    "prompt_variant": prompt_variant,
                                    "max_completion_tokens": FIXED_MAX_COMPLETION_TOKENS,
                                    "timeout_seconds": DEFAULT_VLM_TIMEOUT_SECONDS,
                                },
                            )
                        )
    return configs


def select_pilot_vlm_configs(configs: list[MatrixConfig]) -> list[MatrixConfig]:
    selected: list[MatrixConfig] = []
    for config in configs:
        settings = config.settings
        if (
            settings["response_format"] == "markdown"
            and settings["scale"] == 2.0
            and settings["reasoning_effort"] == "none"
            and settings["prompt_variant"] == "strict_preserve"
        ):
            selected.append(config)

    mini_variants = {
        ("html", 2.0, "none", "strict_preserve"),
        ("markdown", 1.0, "none", "strict_preserve"),
        ("markdown", 2.5, "none", "strict_preserve"),
        ("markdown", 2.0, "low", "strict_preserve"),
        ("markdown", 2.0, "medium", "strict_preserve"),
        ("markdown", 2.0, "none", "table_first"),
    }
    for config in configs:
        settings = config.settings
        key = (
            settings["response_format"],
            settings["scale"],
            settings["reasoning_effort"],
            settings["prompt_variant"],
        )
        if settings["model"] == "gpt-5.4-mini" and key in mini_variants:
            selected.append(config)

    seen: set[str] = set()
    unique: list[MatrixConfig] = []
    for config in selected:
        if config.config_id not in seen:
            seen.add(config.config_id)
            unique.append(config)
    return unique


def build_standard_converter(settings: dict[str, Any]) -> DocumentConverter:
    options = PdfPipelineOptions()
    options.do_table_structure = bool(settings["do_table_structure"])
    options.table_structure_options.mode = TableFormerMode(settings["tableformer_mode"])
    options.table_structure_options.do_cell_matching = bool(settings["do_cell_matching"])
    options.do_ocr = bool(settings["do_ocr"])
    options.ocr_options.force_full_page_ocr = bool(
        settings["force_full_page_ocr"] and options.do_ocr
    )
    options.force_backend_text = bool(settings["force_backend_text"])
    options.images_scale = float(settings["images_scale"])
    options.generate_page_images = bool(settings["generate_page_images"])
    options.ocr_batch_size = int(settings["ocr_batch_size"])
    options.layout_batch_size = int(settings["layout_batch_size"])
    options.table_batch_size = int(settings["table_batch_size"])
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )


def build_converter(config: MatrixConfig) -> DocumentConverter:
    if config.pipeline == "standard":
        return build_standard_converter(config.settings)
    settings = config.settings
    return build_openai_vlm_converter(
        model=settings["model"],
        max_completion_tokens=FIXED_MAX_COMPLETION_TOKENS,
        reasoning_effort=settings["reasoning_effort"],
        timeout_seconds=float(settings["timeout_seconds"]),
        scale=float(settings["scale"]),
        response_format=settings["response_format"],
        prompt_variant=settings["prompt_variant"],
    )


def ensure_corpus(skip_generate: bool) -> None:
    if skip_generate:
        return
    from benchmarks.generate_complex_pdfs import main as generate_corpus

    generate_corpus()


def price_for_tokens(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = MODEL_PRICES_PER_MTOK[model]
    return (
        (prompt_tokens / 1_000_000) * price["input"]
        + (completion_tokens / 1_000_000) * price["output"]
    )


def row_cost(row: dict[str, Any], config: MatrixConfig) -> float:
    if config.pipeline != "vlm":
        return 0.0
    return price_for_tokens(
        config.settings["model"],
        int(row.get("prompt_tokens") or 0),
        int(row.get("completion_tokens") or 0),
    )


def estimate_full_vlm_budget(
    rows: list[dict[str, Any]],
    all_vlm_configs: list[MatrixConfig],
    all_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    pilot_rows = [
        row
        for row in rows
        if row.get("pipeline") == "vlm"
        and row.get("status") == "ok"
        and int(row.get("expected_pages") or 0) > 0
    ]
    if not pilot_rows:
        return {
            "status": "no_vlm_pilot_rows",
            "estimated_full_cost_usd": 0.0,
            "estimated_additional_cost_usd": 0.0,
        }

    pages = sum(int(case.get("pages", 0)) for case in all_cases)
    already_cost = sum(float(row.get("estimated_cost_usd") or 0.0) for row in rows)
    avg_by_model: dict[str, dict[str, float]] = {}
    for model in VLM_MODELS:
        model_rows = [
            row
            for row in pilot_rows
            if row.get("settings", {}).get("model") == model
        ]
        if not model_rows:
            model_rows = pilot_rows
        total_pages = sum(int(row.get("expected_pages") or 0) for row in model_rows)
        prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in model_rows)
        completion_tokens = sum(
            int(row.get("completion_tokens") or 0) for row in model_rows
        )
        avg_by_model[model] = {
            "prompt_tokens_per_page": prompt_tokens / total_pages,
            "completion_tokens_per_page": completion_tokens / total_pages,
        }

    estimated_full_cost = 0.0
    for config in all_vlm_configs:
        model = config.settings["model"]
        avg = avg_by_model[model]
        estimated_full_cost += price_for_tokens(
            model,
            int(avg["prompt_tokens_per_page"] * pages),
            int(avg["completion_tokens_per_page"] * pages),
        )

    return {
        "status": "estimated",
        "models": VLM_MODELS,
        "price_table_per_mtok": MODEL_PRICES_PER_MTOK,
        "estimated_full_cost_usd": round(estimated_full_cost, 4),
        "already_measured_cost_usd": round(already_cost, 4),
        "estimated_additional_cost_usd": round(
            max(estimated_full_cost - already_cost, 0.0), 4
        ),
        "full_vlm_config_count": len(all_vlm_configs),
        "full_case_count": len(all_cases),
        "full_case_pages": pages,
        "model_token_averages": avg_by_model,
    }


def load_existing_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "results.json"
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("results", []))


def write_run_payload(
    run_dir: Path,
    *,
    run_id: str,
    args: argparse.Namespace,
    cases: list[dict[str, Any]],
    configs: list[MatrixConfig],
    rows: list[dict[str, Any]],
    budget_estimate: dict[str, Any] | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "matrix": args.matrix,
        "phase": args.phase,
        "standard_depth": args.standard_depth,
        "metric": METRIC_FORMULA,
        "budget_usd": args.budget_usd,
        "fixed_max_completion_tokens": FIXED_MAX_COMPLETION_TOKENS,
        "cases": cases,
        "configs": [
            {
                "pipeline": config.pipeline,
                "config_id": config.config_id,
                "settings": config.settings,
            }
            for config in configs
        ],
        "budget_estimate": budget_estimate,
        "results": rows,
    }
    (run_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_results_csv(run_dir / "results.csv", rows)
    if budget_estimate is not None:
        (run_dir / "budget_estimate.json").write_text(
            json.dumps(budget_estimate, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def build_config_plan(args: argparse.Namespace) -> tuple[list[MatrixConfig], list[MatrixConfig]]:
    standard_configs = build_standard_configs(args.standard_depth)
    all_vlm_configs = build_vlm_configs()
    selected_vlm_configs = (
        select_pilot_vlm_configs(all_vlm_configs)
        if args.phase == "pilot"
        else all_vlm_configs
    )
    configs: list[MatrixConfig] = []
    if args.matrix in {"standard", "all"}:
        configs.extend(standard_configs)
    if args.matrix in {"vlm", "all"}:
        configs.extend(selected_vlm_configs)
    return configs, all_vlm_configs


def resolve_cases(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_cases = load_cases(limit=args.limit)
    selected_cases = all_cases
    if args.phase == "pilot" and args.matrix in {"vlm", "all"}:
        requested = args.pilot_case_ids.split(",") if args.pilot_case_ids else []
        selected_cases = load_cases(case_ids=requested)
        if not selected_cases:
            selected_cases = all_cases[: min(3, len(all_cases))]
    return selected_cases, all_cases


def dry_run(
    run_dir: Path,
    run_id: str,
    args: argparse.Namespace,
    cases: list[dict[str, Any]],
    all_cases: list[dict[str, Any]],
    configs: list[MatrixConfig],
    all_vlm_configs: list[MatrixConfig],
) -> int:
    vlm_configs = [config for config in configs if config.pipeline == "vlm"]
    standard_configs = [config for config in configs if config.pipeline == "standard"]
    payload = {
        "run_id": run_id,
        "phase": args.phase,
        "matrix": args.matrix,
        "cases": [
            {
                "case_id": case["case_id"],
                "filename": case["filename"],
                "pages": case.get("pages"),
                "tags": case.get("tags", []),
            }
            for case in cases
        ],
        "case_count": len(cases),
        "case_pages": sum(int(case.get("pages", 0)) for case in cases),
        "standard_config_count": len(standard_configs),
        "vlm_config_count": len(vlm_configs),
        "full_vlm_config_count": len(all_vlm_configs),
        "estimated_vlm_requests_for_selected_phase": len(vlm_configs)
        * sum(int(case.get("pages", 0)) for case in cases),
        "estimated_vlm_requests_for_full": len(all_vlm_configs)
        * sum(int(case.get("pages", 0)) for case in all_cases),
        "configs": [
            {
                "pipeline": config.pipeline,
                "config_id": config.config_id,
                "settings": config.settings,
            }
            for config in configs
        ],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dry_run.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def preflight_vlm(config: MatrixConfig, cache: dict[tuple[str, str], str | None]) -> str | None:
    settings = config.settings
    key = (settings["model"], settings["reasoning_effort"])
    if key in cache:
        return cache[key]
    try:
        check_openai_chat_access(
            settings["model"],
            reasoning_effort=settings["reasoning_effort"],
            timeout_seconds=min(float(settings["timeout_seconds"]), 60),
        )
        cache[key] = None
    except Exception as exc:
        cache[key] = str(exc)
    return cache[key]


def make_preflight_failure_row(
    run_id: str,
    args: argparse.Namespace,
    config: MatrixConfig,
    error: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "phase": args.phase,
        "pipeline": config.pipeline,
        "config_id": config.config_id,
        "settings": config.settings,
        "case_id": "PRE",
        "filename": "",
        "description": "",
        "tags": [],
        "status": "failed",
        "error": error,
        "expected_pages": 0,
        "detected_pages": None,
        "expected_tables": 0,
        "detected_tables": 0,
        "text_anchor_recall": 0.0,
        "table_cell_recall": 0.0,
        "table_detection_ratio": 0.0,
        "table_detection_precision": 0.0,
        "table_detection_f1": 0.0,
        "case_confidence_score": 0.0,
        "low_confidence": True,
        "overall_score": 0.0,
        "convert_seconds": 0.0,
        "total_seconds": 0.0,
        "seconds_per_page": None,
        "markdown_chars": 0,
        "request_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
    }


def run_matrix(args: argparse.Namespace) -> int:
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = POC_RUNS_DIR / run_id
    ensure_corpus(args.skip_generate_corpus)
    configs, all_vlm_configs = build_config_plan(args)
    cases, all_cases = resolve_cases(args)

    if args.dry_run:
        return dry_run(run_dir, run_id, args, cases, all_cases, configs, all_vlm_configs)

    rows = load_existing_rows(run_dir) if args.resume else []
    completed = {
        (row.get("pipeline"), row.get("config_id"), row.get("case_id"))
        for row in rows
        if row.get("status") == "ok"
    }

    if args.phase == "full" and args.matrix in {"vlm", "all"} and not args.allow_over_budget:
        budget = estimate_full_vlm_budget(rows, all_vlm_configs, all_cases)
        if budget["status"] != "estimated":
            message = (
                "VLM full phase requires pilot rows in the same run. "
                "Run --phase pilot first, then rerun with --phase full --resume."
            )
            if args.matrix == "vlm":
                print(message, flush=True)
                write_run_payload(
                    run_dir,
                    run_id=run_id,
                    args=args,
                    cases=cases,
                    configs=configs,
                    rows=rows,
                    budget_estimate=budget,
                )
                return 2
            print(f"Skipping VLM full phase. {message}", flush=True)
            configs = [config for config in configs if config.pipeline != "vlm"]
        if float(budget["estimated_additional_cost_usd"]) > args.budget_usd:
            message = (
                "Skipping VLM full phase because estimated additional cost "
                f"${budget['estimated_additional_cost_usd']:.2f} exceeds "
                f"${args.budget_usd:.2f}."
            )
            if args.matrix == "vlm":
                print(message, flush=True)
                write_run_payload(
                    run_dir,
                    run_id=run_id,
                    args=args,
                    cases=cases,
                    configs=configs,
                    rows=rows,
                    budget_estimate=budget,
                )
                return 3
            print(message, flush=True)
            configs = [config for config in configs if config.pipeline != "vlm"]

    preflight_cache: dict[tuple[str, str], str | None] = {}
    for config in configs:
        print(f"Config {config.config_id} ({config.pipeline})", flush=True)
        if config.pipeline == "vlm":
            error = preflight_vlm(config, preflight_cache)
            if error:
                row = make_preflight_failure_row(run_id, args, config, error)
                rows.append(row)
                write_run_payload(
                    run_dir,
                    run_id=run_id,
                    args=args,
                    cases=cases,
                    configs=configs,
                    rows=rows,
                )
                print(f"  preflight failed: {error}", flush=True)
                continue

        converter = build_converter(config)
        for case in cases:
            key = (config.pipeline, config.config_id, case["case_id"])
            if args.resume and key in completed:
                print(f"  skip {case['case_id']} (already complete)", flush=True)
                continue

            print(f"  {case['case_id']} {case['filename']}", flush=True)
            extracted_dir = run_dir / "extracted" / config.pipeline / config.config_id
            if config.pipeline == "vlm":
                clear_vlm_usage_events()
            row = benchmark_case(
                converter,
                case,
                extracted_dir,
                fail_on_empty_markdown=config.pipeline == "vlm",
            )
            usage_events = (
                get_vlm_usage_events() if config.pipeline == "vlm" else []
            )
            usage_summary = summarize_usage(usage_events)
            row.update(usage_summary)
            row.update(
                {
                    "run_id": run_id,
                    "phase": args.phase,
                    "pipeline": config.pipeline,
                    "config_id": config.config_id,
                    "settings": config.settings,
                    "estimated_cost_usd": round(row_cost(row, config), 6),
                }
            )
            if usage_events:
                usage_dir = extracted_dir / case["case_id"]
                usage_dir.mkdir(parents=True, exist_ok=True)
                (usage_dir / "vlm_usage.json").write_text(
                    json.dumps(usage_events, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            rows.append(row)
            print(
                "    status={status} overall={overall:.3f} time={seconds:.3f}s "
                "cost=${cost:.4f}".format(
                    status=row["status"],
                    overall=float(row["overall_score"]),
                    seconds=float(row["total_seconds"]),
                    cost=float(row["estimated_cost_usd"]),
                ),
                flush=True,
            )
            write_run_payload(
                run_dir,
                run_id=run_id,
                args=args,
                cases=cases,
                configs=configs,
                rows=rows,
            )

    budget = None
    if args.matrix in {"vlm", "all"}:
        budget = estimate_full_vlm_budget(rows, all_vlm_configs, all_cases)
        if (
            args.phase == "pilot"
            and budget["status"] == "estimated"
            and float(budget["estimated_additional_cost_usd"]) > args.budget_usd
        ):
            budget["full_phase_recommendation"] = "blocked_by_budget"
        elif budget and budget["status"] == "estimated":
            budget["full_phase_recommendation"] = "within_budget"

    write_run_payload(
        run_dir,
        run_id=run_id,
        args=args,
        cases=cases,
        configs=configs,
        rows=rows,
        budget_estimate=budget,
    )
    print(f"Wrote {run_dir / 'results.json'}", flush=True)
    print(f"Wrote {run_dir / 'results.csv'}", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the broad Standard Docling/OpenAI VLM PoC matrix."
    )
    parser.add_argument("--matrix", choices=["standard", "vlm", "all"], default="all")
    parser.add_argument("--phase", choices=["pilot", "full"], default="pilot")
    parser.add_argument("--budget-usd", type=float, default=30.0)
    parser.add_argument("--run-id", default=os.getenv("DOCLING_POC_RUN_ID"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--standard-depth",
        choices=["focused", "full"],
        default="focused",
        help="focused covers every requested Standard parameter; full is exhaustive.",
    )
    parser.add_argument(
        "--pilot-case-ids",
        default=",".join(DEFAULT_PILOT_CASE_IDS),
        help="Comma-separated case IDs used for VLM pilot runs.",
    )
    parser.add_argument("--skip-generate-corpus", action="store_true")
    parser.add_argument(
        "--allow-over-budget",
        action="store_true",
        help="Allow VLM full phase even when pilot estimate exceeds --budget-usd.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_matrix(parse_args()))
