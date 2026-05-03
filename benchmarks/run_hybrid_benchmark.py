from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docling.document_converter import DocumentConverter

from benchmarks.run_docling_benchmark import benchmark_case as benchmark_docling_case
from benchmarks.run_openai_vlm_benchmark import benchmark_case as benchmark_vlm_case
from docling_openai_vlm import build_openai_vlm_converter

OUT_DIR = ROOT / "outputs" / "docling_benchmark"
GT_PATH = OUT_DIR / "ground_truth.json"
RESULTS_DIR = OUT_DIR / "results_hybrid"
JSON_PATH = RESULTS_DIR / "hybrid_results.json"


def should_rerun_with_vlm(row: dict[str, Any], threshold: float) -> bool:
    confidence_score = row.get("case_confidence_score", 0.0)
    return bool(row.get("low_confidence", False) or float(confidence_score) < threshold)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run hybrid benchmark (Docling + selective VLM rerun).")
    p.add_argument("--model", default="gpt-5.4-mini")
    p.add_argument("--threshold", type=float, default=0.75)
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    cases = gt["cases"][: args.limit] if args.limit else gt["cases"]

    docling_converter = DocumentConverter()
    vlm_converter = None

    rows = []
    for case in cases:
        base_row = benchmark_docling_case(docling_converter, case)
        selected = "docling"
        final_row = base_row
        if should_rerun_with_vlm(base_row, args.threshold):
            if vlm_converter is None:
                vlm_converter = build_openai_vlm_converter(args.model)
            vlm_row = benchmark_vlm_case(vlm_converter, case)
            if vlm_row.get("status") == "ok" and vlm_row.get("overall_score", 0.0) >= base_row.get("overall_score", 0.0):
                final_row = vlm_row
                selected = "vlm"
        final_row = dict(final_row)
        final_row["selected_pipeline"] = selected
        final_row["docling_overall_score"] = base_row.get("overall_score", 0.0)
        final_row["docling_case_confidence_score"] = base_row.get("case_confidence_score", 0.0)
        rows.append(final_row)

    payload = {
        "generated_at": date.today().isoformat(),
        "mode": "case_level_hybrid",
        "selection_scope": "case",
        "fallback_model": args.model,
        "threshold": args.threshold,
        "results": rows,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
