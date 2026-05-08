from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_pipeline as rp  # noqa: E402
from settings import load_settings  # noqa: E402

SETTINGS = load_settings()


COORDINATE_MARKDOWN = """Synthetic coordinate table

| Segment | Region | Jan | Feb |
| --- | --- | ---: | ---: |
| Enterprise | East | 1,234 | 2,345 |
| SMB | West | 3,456 | 4,567 |
"""

GOOD_VLM_MARKDOWN = """Synthetic VLM table

| Segment | Region | Jan | Feb |
| --- | --- | ---: | ---: |
| Enterprise | East | 1,234 | 2,345 |
| SMB | West | 3,456 | 4,567 |
"""

FORMAT_VARIANT_VLM_MARKDOWN = """Synthetic VLM table

| Segment | Region | Jan | Feb |
| --- | --- | ---: | ---: |
| Enterprise | East | 1, 234 | 2,345 |
| SMB | West | 3,456 | 4,567 |
"""

NUMERIC_MISREAD_VLM_MARKDOWN = """Synthetic VLM table

| Segment | Region | Jan | Feb |
| --- | --- | ---: | ---: |
| Enterprise | East | 1,239 | 2,345 |
| SMB | West | 3,456 | 4,567 |
"""

VLM_EXTRA_VALUES_MARKDOWN = """Synthetic VLM table

| Segment | Region | Jan | Feb |
| --- | --- | ---: | ---: |
| Enterprise | East | 1,234 | 2,345 |
| SMB | West | 3,456 | 4,567 |
| Phantom | Mars | 99,999 | 88,888 |
"""

VLM_MISSING_ROW_MARKDOWN = """Synthetic VLM table

| Segment | Region | Jan | Feb |
| --- | --- | ---: | ---: |
| Enterprise | East | 1,234 | 2,345 |
"""

COORDINATE_WITH_SYNTHETIC_IDS = """Synthetic coordinate labels

| col_001 | col_002 | Amount |
| --- | --- | ---: |
| A | East | 1,234 |
"""

VLM_WITHOUT_SYNTHETIC_IDS = """Synthetic VLM labels

| Segment | Region | Amount |
| --- | --- | ---: |
| A | East | 1,234 |
"""

STRONG_COORDINATE_QUALITY: dict[str, Any] = {
    "ok": True,
    "reasons": [],
    "span_coverage": 1.0,
}

MEDIUM_COORDINATE_QUALITY: dict[str, Any] = {
    "ok": False,
    "reasons": ["minor_span_gap"],
    "span_coverage": 0.97,
}

WEAK_COORDINATE_QUALITY: dict[str, Any] = {
    "ok": False,
    "reasons": ["row_collapse_risk"],
    "span_coverage": 0.99,
}

COORDINATE_DIAGNOSTICS: dict[str, Any] = {
    "status": "ok",
    "trimmed_rows": 4,
    "trimmed_columns": 4,
    "span_count": 16,
    "span_coverage": 1.0,
    "table_area_ratio": 0.42,
}


class DummyFallbackConverter:
    pass


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def warning_codes(warnings: list[rp.WarningItem]) -> list[str]:
    return [warning.code for warning in warnings]


def run_check(
    *,
    vlm_markdown: str,
    coordinate_markdown: str = COORDINATE_MARKDOWN,
    coordinate_quality: dict[str, Any] = STRONG_COORDINATE_QUALITY,
    enable_quality_check: bool = True,
    enable_auto_correct: bool = True,
    fallback_converter: Any | None = None,
) -> tuple[str, list[dict[str, Any]], list[rp.WarningItem], dict[str, Any], dict[str, float]]:
    return rp.apply_vlm_coordinate_quality_check(
        page=1,
        mode="TEXT_TABLE_VLM",
        safe_markdown=vlm_markdown,
        tables=[],
        coordinate_markdown=coordinate_markdown,
        coordinate_quality=coordinate_quality,
        coordinate_diagnostics=COORDINATE_DIAGNOSTICS,
        model="synthetic-vlm",
        source="synthetic-test",
        enable_quality_check=enable_quality_check,
        enable_auto_correct=enable_auto_correct,
        fallback_converter=fallback_converter,
        fallback_model="synthetic-fallback",
        fallback_pdf_path=ROOT / "synthetic.pdf",
        fallback_start_page=1,
        fallback_end_page=1,
    )


def scenario_no_false_positive() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=GOOD_VLM_MARKDOWN,
    )
    assert_true(warnings == [], "matching VLM/coordinate output should not warn")
    assert_true(rp.UNKNOWN_TOKEN not in safe, "matching output should not be masked")
    assert_true(
        not diagnostics["initial_report"]["needs_fallback"],
        "matching output should not request fallback",
    )
    return summarize("no_false_positive", safe, warnings, diagnostics, timings)


def scenario_format_normalization() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=FORMAT_VARIANT_VLM_MARKDOWN,
    )
    assert_true(warnings == [], "format-only numeric variants should not warn")
    assert_true(rp.UNKNOWN_TOKEN not in safe, "format-only variants should not be masked")
    assert_true(
        not diagnostics["initial_report"]["needs_fallback"],
        "format-only variants should not request fallback",
    )
    return summarize("format_normalization", safe, warnings, diagnostics, timings)


def scenario_auto_correct_unique_cell() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=NUMERIC_MISREAD_VLM_MARKDOWN,
    )
    codes = warning_codes(warnings)
    assert_true(
        "VLM_COORD_AUTO_CORRECTED_CELL" in codes,
        "unique numeric disagreement should be auto-corrected",
    )
    assert_true("1,239" not in safe, "misread value should be removed")
    assert_true("1,234" in safe, "coordinate value should replace the misread value")
    assert_true(
        not diagnostics["final_report"]["needs_fallback"],
        "auto-corrected output should not need fallback",
    )
    return summarize("auto_correct_unique_cell", safe, warnings, diagnostics, timings)


def scenario_vlm_extra_values_masked() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=VLM_EXTRA_VALUES_MARKDOWN,
    )
    codes = warning_codes(warnings)
    assert_true(
        "VLM_UNSUPPORTED_COORD_VALUE" in codes,
        "VLM-only critical values should be reported",
    )
    assert_true(
        "VLM_COORD_MASKED_UNSUPPORTED_VALUE" in codes,
        "VLM-only critical values should be masked",
    )
    assert_true("MASKED_AS_UNKNOWN" in codes, "masking should emit generic unknown warning")
    assert_true("99,999" not in safe and "88,888" not in safe, "unsupported values should be removed")
    assert_true(
        safe.count(rp.UNKNOWN_TOKEN) == 2,
        "two unsupported values should be replaced by unknown tokens",
    )
    assert_true(
        diagnostics["initial_report"]["actionable_unsupported_value_count"] == 2,
        "two VLM-only critical values should be actionable",
    )
    return summarize("vlm_extra_values_masked", safe, warnings, diagnostics, timings)


def scenario_coordinate_missing_values_warns() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=VLM_MISSING_ROW_MARKDOWN,
    )
    codes = warning_codes(warnings)
    assert_true(
        "VLM_MISSING_COORD_VALUE" in codes,
        "coordinate-only critical values should be reported",
    )
    assert_true(
        diagnostics["initial_report"]["needs_fallback"],
        "coordinate-only critical values should request fallback",
    )
    assert_true(
        rp.UNKNOWN_TOKEN not in safe,
        "missing coordinate values should warn, not inject unknown tokens into absent cells",
    )
    assert_true(
        diagnostics["initial_report"]["actionable_missing_value_count"] == 2,
        "two missing coordinate critical values should be actionable",
    )
    return summarize("coordinate_missing_values_warns", safe, warnings, diagnostics, timings)


def scenario_medium_evidence_is_actionable() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=VLM_EXTRA_VALUES_MARKDOWN,
        coordinate_quality=MEDIUM_COORDINATE_QUALITY,
    )
    codes = warning_codes(warnings)
    assert_true(
        "VLM_UNSUPPORTED_COORD_VALUE" in codes,
        "medium coordinate evidence should still be actionable",
    )
    assert_true(
        "VLM_COORD_MASKED_UNSUPPORTED_VALUE" in codes,
        "medium coordinate evidence should mask VLM-only critical values",
    )
    assert_true(
        diagnostics["initial_report"]["coordinate_evidence_strength"] == "medium",
        "medium evidence should be classified as medium",
    )
    assert_true(
        safe.count(rp.UNKNOWN_TOKEN) == 2,
        "medium evidence should replace unsupported values",
    )
    return summarize("medium_evidence_is_actionable", safe, warnings, diagnostics, timings)


def scenario_weak_evidence_does_not_mask() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=VLM_EXTRA_VALUES_MARKDOWN,
        coordinate_quality=WEAK_COORDINATE_QUALITY,
    )
    codes = warning_codes(warnings)
    assert_true(codes == ["VLM_COORD_WEAK_EVIDENCE"], "weak evidence should only emit info warning")
    assert_true("99,999" in safe and "88,888" in safe, "weak evidence should not mask values")
    assert_true(rp.UNKNOWN_TOKEN not in safe, "weak evidence should not inject unknown tokens")
    assert_true(
        diagnostics["initial_report"]["coordinate_evidence_strength"] == "weak",
        "weak evidence should be classified as weak",
    )
    assert_true(
        not diagnostics["initial_report"]["needs_fallback"],
        "weak coordinate-only disagreement should not request fallback",
    )
    return summarize("weak_evidence_does_not_mask", safe, warnings, diagnostics, timings)


def scenario_synthetic_coordinate_ids_ignored() -> dict[str, Any]:
    report = rp.coordinate_vlm_quality_report(
        coordinate_markdown=COORDINATE_WITH_SYNTHETIC_IDS,
        vlm_markdown=VLM_WITHOUT_SYNTHETIC_IDS,
        coordinate_quality=STRONG_COORDINATE_QUALITY,
        coordinate_diagnostics=COORDINATE_DIAGNOSTICS,
    )
    assert_true(
        report["coordinate_only_values"].get("id") in (None, []),
        "synthetic coordinate labels such as col_001 should not be treated as missing IDs",
    )
    return {
        "name": "synthetic_coordinate_ids_ignored",
        "passed": True,
        "warning_codes": [],
        "unknown_token_count": 0,
        "diagnostics": {
            "coordinate_only_values": report["coordinate_only_values"],
            "vlm_only_values": report["vlm_only_values"],
        },
        "timings": {},
    }


def run_with_fake_fallback(
    fallback_markdown: str,
    scenario: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    original = rp.timed_docling_candidate

    def fake_timed_docling_candidate(**_kwargs: Any) -> dict[str, Any]:
        return {
            "elapsed_seconds": 0.001,
            "markdown": fallback_markdown,
            "tables": [{"source": "fake_fallback"}],
            "usage_events": [],
        }

    rp.timed_docling_candidate = fake_timed_docling_candidate
    try:
        return scenario()
    finally:
        rp.timed_docling_candidate = original


def scenario_fallback_applied() -> dict[str, Any]:
    def _scenario() -> dict[str, Any]:
        safe, tables, warnings, diagnostics, timings = run_check(
            vlm_markdown=VLM_EXTRA_VALUES_MARKDOWN,
            fallback_converter=DummyFallbackConverter(),
        )
        codes = warning_codes(warnings)
        assert_true("VLM_COORD_FALLBACK_APPLIED" in codes, "better fallback should be accepted")
        assert_true(rp.UNKNOWN_TOKEN not in safe, "accepted fallback should avoid masking")
        assert_true("99,999" not in safe and "88,888" not in safe, "bad VLM-only values should be gone")
        assert_true(tables == [{"source": "fake_fallback"}], "accepted fallback tables should replace tables")
        assert_true(
            diagnostics["fallback"]["accepted"],
            "fallback diagnostics should record accepted=True",
        )
        assert_true(
            "coordinate_vlm_fallback:synthetic-fallback" in timings,
            "fallback timing should be recorded",
        )
        return summarize("fallback_applied", safe, warnings, diagnostics, timings)

    return run_with_fake_fallback(COORDINATE_MARKDOWN, _scenario)


def scenario_fallback_skipped_then_masked() -> dict[str, Any]:
    def _scenario() -> dict[str, Any]:
        safe, _tables, warnings, diagnostics, timings = run_check(
            vlm_markdown=VLM_EXTRA_VALUES_MARKDOWN,
            fallback_converter=DummyFallbackConverter(),
        )
        codes = warning_codes(warnings)
        assert_true("VLM_COORD_FALLBACK_SKIPPED" in codes, "non-improving fallback should be skipped")
        assert_true("VLM_COORD_MASKED_UNSUPPORTED_VALUE" in codes, "skipped fallback should still mask")
        assert_true(safe.count(rp.UNKNOWN_TOKEN) == 2, "unsupported values should be masked after skipped fallback")
        assert_true(
            not diagnostics["fallback"]["accepted"],
            "fallback diagnostics should record accepted=False",
        )
        assert_true(
            "coordinate_vlm_fallback:synthetic-fallback" in timings,
            "fallback timing should be recorded",
        )
        return summarize("fallback_skipped_then_masked", safe, warnings, diagnostics, timings)

    return run_with_fake_fallback(VLM_EXTRA_VALUES_MARKDOWN, _scenario)


def scenario_quality_check_disabled() -> dict[str, Any]:
    safe, _tables, warnings, diagnostics, timings = run_check(
        vlm_markdown=VLM_EXTRA_VALUES_MARKDOWN,
        enable_quality_check=False,
    )
    assert_true(warnings == [], "disabled check should not warn")
    assert_true("99,999" in safe and "88,888" in safe, "disabled check should not mask")
    assert_true(diagnostics["reason"] == "disabled", "disabled check should explain why it did not run")
    return summarize("quality_check_disabled", safe, warnings, diagnostics, timings)


def summarize(
    name: str,
    safe_markdown: str,
    warnings: list[rp.WarningItem],
    diagnostics: dict[str, Any],
    timings: dict[str, float],
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": True,
        "warning_codes": warning_codes(warnings),
        "unknown_token_count": safe_markdown.count(rp.UNKNOWN_TOKEN),
        "diagnostics": diagnostics,
        "timings": timings,
        "warnings": [asdict(warning) for warning in warnings],
    }


def main() -> int:
    scenarios: list[Callable[[], dict[str, Any]]] = [
        scenario_no_false_positive,
        scenario_format_normalization,
        scenario_auto_correct_unique_cell,
        scenario_vlm_extra_values_masked,
        scenario_coordinate_missing_values_warns,
        scenario_medium_evidence_is_actionable,
        scenario_weak_evidence_does_not_mask,
        scenario_synthetic_coordinate_ids_ignored,
        scenario_fallback_applied,
        scenario_fallback_skipped_then_masked,
        scenario_quality_check_disabled,
    ]

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for scenario in scenarios:
        try:
            results.append(scenario())
        except Exception as exc:  # noqa: BLE001
            failures.append({"name": scenario.__name__, "error": str(exc)})

    summary = {
        "passed": not failures,
        "passed_count": len(results),
        "failed_count": len(failures),
        "failures": failures,
        "results": results,
    }

    out_dir = SETTINGS.outputs.routing_runs_dir / "coordinate_quality_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(
        {
            "passed": summary["passed"],
            "passed_count": summary["passed_count"],
            "failed_count": summary["failed_count"],
            "summary_path": str(out_dir / "summary.json"),
            "failures": failures,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
