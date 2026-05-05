from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption
from dotenv import load_dotenv

from docling_openai_vlm import (
    DEFAULT_VLM_MAX_COMPLETION_TOKENS,
    DEFAULT_VLM_REASONING_EFFORT,
    DEFAULT_VLM_SCALE,
    DEFAULT_VLM_TIMEOUT_SECONDS,
    build_openai_vlm_converter,
    check_openai_chat_access,
    clear_vlm_usage_events,
    get_vlm_usage_events,
)


ROOT = Path(__file__).resolve().parent
ROUTING_RUNS_DIR = ROOT / "outputs" / "docling_routing_runs"
UNKNOWN_TOKEN = "[[読み取り不明]]"

RoutingMode = Literal[
    "TEXT_LIGHT",
    "TEXT_TABLE_FAST",
    "TEXT_TABLE_ACCURATE",
    "TEXT_TABLE_COORD",
    "TEXT_TABLE_VLM",
    "IMAGE_RECONCILE",
    "IMAGE_RECONCILE_APPEND",
]
WarningLevel = Literal["info", "warning", "needs_retry"]
ReconcileCompareMode = Literal["ocr_vlm", "vlm_vlm"]


@dataclass
class RoutedPdfOptions:
    model: str = "gpt-5.2"
    reconcile_compare_mode: ReconcileCompareMode = "ocr_vlm"
    secondary_model: str = "gpt-5.4-mini"
    max_completion_tokens: int = DEFAULT_VLM_MAX_COMPLETION_TOKENS
    reasoning_effort: str = DEFAULT_VLM_REASONING_EFFORT
    timeout_seconds: float = DEFAULT_VLM_TIMEOUT_SECONDS
    vlm_scale: float = DEFAULT_VLM_SCALE
    response_format: str = "markdown"
    prompt_variant: str = "strict_preserve"
    text_chars_threshold: int = 200
    text_quality_threshold: float = 0.80
    table_text_quality_threshold: float = 0.70
    table_score_threshold: float = 0.40
    complex_table_score_threshold: float = 0.60
    image_area_threshold: float = 0.40
    enable_embedded_visual_append: bool = True
    embedded_visual_min_area_ratio: float = 0.08
    embedded_visual_min_width_ratio: float = 0.30
    embedded_visual_min_height_ratio: float = 0.06
    embedded_visual_text_overlap_threshold: float = 0.05
    embedded_visual_complexity_threshold: float = 0.35
    embedded_visual_force_area_ratio: float = 0.25
    embedded_visual_crop_margin_points: float = 8.0
    parallel_reconcile_candidates: bool = True
    max_parallel_table_groups: int = 2
    use_coordinate_table_reconstruction: bool = False
    enable_table_vlm_fallback: bool = False
    table_vlm_model: str = "gpt-5.4-mini"
    large_table_vlm_model: str = "gpt-5.4"
    table_vlm_prompt_variant: str = "table_first"
    table_vlm_reasoning_effort: str = "none"
    enable_reconcile_table_fallback: bool = True
    reconcile_table_fallback_model: str = "gpt-5.4"
    reconcile_table_fallback_prompt_variant: str = "table_first"
    reconcile_table_fallback_reasoning_effort: str = "none"
    enable_vlm_coordinate_quality_check: bool = True
    enable_vlm_coordinate_auto_correct: bool = True
    coordinate_min_span_coverage: float = 0.98
    coordinate_max_cell_chars: int = 160
    coordinate_max_cell_char_ratio: float = 8.0
    table_vlm_large_min_columns: int = 12
    table_vlm_large_min_area_ratio: float = 0.60
    force_reconcile_pages: list[int] = field(default_factory=list)
    save_outputs: bool = True


@dataclass
class PagePreflight:
    page: int
    width: float
    height: float
    text_chars: int
    text_density: float
    text_quality_score: float
    image_area_ratio: float
    line_count: int
    rect_count: int
    table_score: float
    complex_table_score: float
    image_read_risk_score: float
    mode: RoutingMode
    reasons: list[str]
    elapsed_seconds: float
    extra_actions: list[str] = field(default_factory=list)
    embedded_visual_region_count: int = 0
    embedded_visual_regions: list[dict[str, Any]] = field(default_factory=list)
    visual_reference_count: int = 0


@dataclass
class RoutingGroup:
    mode: RoutingMode
    start_page: int
    end_page: int
    pages: list[int]


@dataclass
class WarningItem:
    page: int
    mode: RoutingMode
    level: WarningLevel
    code: str
    score: float
    message: str
    suggested_action: str
    evidence: dict[str, Any] = field(default_factory=dict)
    target: str | None = None


@dataclass
class ConversionSegment:
    mode: RoutingMode
    start_page: int
    end_page: int
    markdown: str
    safe_markdown: str
    raw_ocr_markdown: str = ""
    raw_vlm_markdown: str = ""
    tables: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    candidate_timings_seconds: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class TableRepairWarning:
    page: int
    table_index: int
    code: str
    level: WarningLevel
    score: float
    message: str
    suggested_action: str
    evidence: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[str], None]


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def normalize_reconcile_compare_mode(value: str) -> ReconcileCompareMode:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in {"ocr_vlm", "vlm_vlm"}:
        raise ValueError(f"Unsupported reconcile compare mode: {value}")
    return normalized  # type: ignore[return-value]


def reconcile_source_labels(options: RoutedPdfOptions) -> tuple[str, str]:
    compare_mode = normalize_reconcile_compare_mode(options.reconcile_compare_mode)
    if compare_mode == "vlm_vlm":
        secondary_model = options.secondary_model.strip() or options.model
        return (f"vlm_primary:{options.model}", f"vlm_secondary:{secondary_model}")
    return ("ocr", f"vlm:{options.model}")


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def normalized_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def compact_for_compare(value: str) -> str:
    text = normalized_text(value).lower()
    return re.sub(r"[\s,_，、。．.・:：;；|｜/\\()\[\]{}<>＜＞「」『』`'\"“”]+", "", text)


def compact_number(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", "", text)
    text = text.replace(",", "")
    text = text.replace("，", "")
    text = text.replace("￥", "")
    text = text.replace("¥", "")
    text = text.replace("円", "")
    text = re.sub(r"(?<=\d)\.(?=\d)", ".", text)
    return text.strip()


def is_numeric_like(value: str) -> bool:
    text = normalized_text(value)
    if not re.search(r"\d", text):
        return False
    compact = compact_number(text)
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", compact))


def numeric_values_equal(left: str, right: str) -> bool:
    left_compact = compact_number(left).rstrip("%")
    right_compact = compact_number(right).rstrip("%")
    if left_compact == right_compact:
        return True
    try:
        return math.isclose(float(left_compact), float(right_compact), rel_tol=0.0, abs_tol=1e-9)
    except ValueError:
        return False


def text_quality_score(text: str) -> float:
    if not text.strip():
        return 0.0
    total = len(text)
    replacement = text.count("\ufffd") / total
    controls = (
        sum(
            1
            for char in text
            if unicodedata.category(char)[0] == "C" and not char.isspace()
        )
        / total
    )
    symbols = sum(1 for char in text if unicodedata.category(char)[0] in {"S", "P"}) / total
    whitespace = sum(1 for char in text if char.isspace()) / total
    tokens = re.findall(r"\S+", text)
    single_char_tokens = (
        sum(1 for token in tokens if len(token) == 1) / len(tokens) if tokens else 1.0
    )
    repeated_fragments = repeated_fragment_ratio(text)

    score = 1.0
    score -= min(replacement * 20.0, 0.45)
    score -= min(controls * 20.0, 0.35)
    score -= min(max(symbols - 0.25, 0.0) * 1.2, 0.20)
    score -= min(max(whitespace - 0.35, 0.0) * 1.0, 0.15)
    score -= min(max(single_char_tokens - 0.35, 0.0) * 0.8, 0.20)
    score -= min(repeated_fragments * 0.5, 0.20)
    return round(clamp(score), 4)


def repeated_fragment_ratio(text: str) -> float:
    tokens = [token for token in re.findall(r"\w{2,}", normalized_text(text).lower())]
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    repeated = sum(count for count in counts.values() if count >= 3)
    return clamp(repeated / len(tokens))


def symbol_ratio(text: str) -> float:
    if not text:
        return 0.0
    symbols = sum(
        1
        for char in text
        if not char.isalnum() and not char.isspace() and char not in ".,:/\\-+%￥¥円()[]{}"
    )
    return clamp(symbols / len(text))


def single_char_token_ratio(text: str) -> float:
    tokens = re.findall(r"\S+", text)
    if not tokens:
        return 1.0
    return clamp(sum(1 for token in tokens if len(token) == 1) / len(tokens))


def replacement_char_rate(text: str) -> float:
    if not text:
        return 0.0
    return clamp(text.count("\ufffd") / len(text))


def estimate_table_score(
    *,
    line_count: int,
    rect_count: int,
    text_rects: list[tuple[float, float, float, float]],
    text: str,
) -> tuple[float, float]:
    if not text_rects:
        line_signal = clamp((line_count + rect_count) / 30.0)
        return round(line_signal * 0.4, 4), 0.0

    x_buckets = Counter(round(rect[0] / 12.0) for rect in text_rects)
    y_buckets = Counter(round(rect[1] / 8.0) for rect in text_rects)
    aligned_x = sum(1 for count in x_buckets.values() if count >= 3)
    aligned_y = sum(1 for count in y_buckets.values() if count >= 3)
    numeric_tokens = re.findall(r"(?:\d[\d,.\-/%]*|\d+年|\d+月|\d+日)", text)
    tokens = re.findall(r"\S+", text)
    numeric_density = len(numeric_tokens) / len(tokens) if tokens else 0.0

    line_signal = clamp((line_count + rect_count) / 40.0)
    x_signal = clamp(aligned_x / 8.0)
    y_signal = clamp(aligned_y / 16.0)
    numeric_signal = clamp(numeric_density * 2.5)
    table_score = (
        0.35 * line_signal
        + 0.30 * x_signal
        + 0.20 * y_signal
        + 0.15 * numeric_signal
    )

    column_estimate = aligned_x
    row_estimate = aligned_y
    complex_score = (
        0.25 * clamp(line_count / 80.0)
        + 0.25 * clamp(rect_count / 80.0)
        + 0.25 * clamp(column_estimate / 8.0)
        + 0.25 * clamp(row_estimate / 24.0)
    )
    if table_score < 0.40:
        complex_score *= 0.5
    return round(clamp(table_score), 4), round(clamp(complex_score), 4)


def rect_area(rect: tuple[float, float, float, float]) -> float:
    left, bottom, right, top = rect
    return max(right - left, 0.0) * max(top - bottom, 0.0)


def overlap_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    l1, b1, r1, t1 = left
    l2, b2, r2, t2 = right
    x_overlap = max(min(r1, r2) - max(l1, l2), 0.0)
    y_overlap = max(min(t1, t2) - max(b1, b2), 0.0)
    return x_overlap * y_overlap


def union_rect(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    l1, b1, r1, t1 = left
    l2, b2, r2, t2 = right
    return min(l1, l2), min(b1, b2), max(r1, r2), max(t1, t2)


def expand_rect(
    rect: tuple[float, float, float, float],
    padding: float,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float]:
    left, bottom, right, top = rect
    return (
        max(left - padding, 0.0),
        max(bottom - padding, 0.0),
        min(right + padding, page_width),
        min(top + padding, page_height),
    )


def merge_visual_regions(
    regions: list[tuple[float, float, float, float]],
    *,
    page_width: float,
    page_height: float,
    padding: float = 8.0,
) -> list[tuple[float, float, float, float]]:
    merged: list[tuple[float, float, float, float]] = []
    for region in sorted(regions, key=lambda item: (item[1], item[0])):
        if rect_area(region) <= 0:
            continue
        found_index: int | None = None
        expanded_region = expand_rect(region, padding, page_width, page_height)
        for index, existing in enumerate(merged):
            if overlap_area(expanded_region, expand_rect(existing, padding, page_width, page_height)) > 0:
                found_index = index
                break
        if found_index is None:
            merged.append(region)
        else:
            merged[found_index] = union_rect(merged[found_index], region)

    changed = True
    while changed:
        changed = False
        next_regions: list[tuple[float, float, float, float]] = []
        for region in merged:
            expanded_region = expand_rect(region, padding, page_width, page_height)
            for index, existing in enumerate(next_regions):
                if overlap_area(
                    expanded_region,
                    expand_rect(existing, padding, page_width, page_height),
                ) > 0:
                    next_regions[index] = union_rect(existing, region)
                    changed = True
                    break
            else:
                next_regions.append(region)
        merged = next_regions
    return merged


def visual_reference_count(text: str) -> int:
    patterns = [
        r"(?:^|\n)\s*(?:表|図|図表)\s*\d+",
        r"(?:^|\n)\s*(?:Table|Figure|Fig\.?|Chart)\s*\d+",
        r"(?:表|図|図表)\s*\d+\s*(?:を|に|で|参照|示す)",
        r"(?:see|shown in|as shown in)\s+(?:Table|Figure|Fig\.?|Chart)\s*\d+",
    ]
    count = 0
    for pattern in patterns:
        count += len(re.findall(pattern, text, flags=re.IGNORECASE))
    return count


def line_has_visual_reference(text: str) -> bool:
    return bool(
        re.search(
            r"^\s*(?:表|図|図表|Table|Figure|Fig\.?|Chart)\s*\d+",
            normalized_text(text),
            flags=re.IGNORECASE,
        )
    )


def region_text_overlap_ratio(
    region: tuple[float, float, float, float],
    text_rects: list[tuple[float, float, float, float]],
) -> float:
    area = max(rect_area(region), 1.0)
    overlap = sum(overlap_area(region, rect) for rect in text_rects)
    return clamp(overlap / area)


def caption_geometry_score(
    region: tuple[float, float, float, float],
    text_lines: list[dict[str, Any]],
) -> tuple[float, str | None]:
    left, bottom, right, top = region
    image_width = max(right - left, 1.0)
    best_score = 0.0
    best_text: str | None = None
    for line in text_lines:
        line_rect = tuple(float(v) for v in line["bbox"])
        line_left, line_bottom, line_right, line_top = line_rect
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        line_width = max(line_right - line_left, 1.0)
        line_height = max(line_top - line_bottom, 1.0)
        horizontal_overlap = max(min(right, line_right) - max(left, line_left), 0.0)
        overlap_ratio = horizontal_overlap / min(image_width, line_width)
        distance_above = max(line_bottom - top, 0.0)
        distance_below = max(bottom - line_top, 0.0)
        close = min(distance_above, distance_below)
        if close > 80:
            continue
        if line_height > 45:
            continue
        if overlap_ratio < 0.25:
            continue
        score = 0.35
        score += 0.30 * clamp(overlap_ratio)
        score += 0.20 * (1.0 - clamp(close / 80.0))
        score += 0.15 if line_has_visual_reference(text) else 0.0
        if score > best_score:
            best_score = score
            best_text = text[:120]
    return round(clamp(best_score), 4), best_text


def crop_visual_complexity(
    page: Any,
    page_width: float,
    page_height: float,
    region: tuple[float, float, float, float],
) -> float:
    try:
        bitmap = page.render(scale=0.5)
        image = bitmap.to_pil().convert("RGB")
    except Exception:
        return 0.0

    scale = image.width / max(page_width, 1.0)
    left, bottom, right, top = region
    crop_box = (
        max(int(left * scale), 0),
        max(int((page_height - top) * scale), 0),
        min(int(right * scale), image.width),
        min(int((page_height - bottom) * scale), image.height),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return 0.0
    crop = image.crop(crop_box)
    if crop.width < 16 or crop.height < 16:
        return 0.0
    if max(crop.width, crop.height) > 360:
        crop.thumbnail((360, 360))

    gray = crop.convert("L")
    pixels = list(gray.getdata())
    if not pixels:
        return 0.0
    mean = sum(pixels) / len(pixels)
    variance = sum((pixel - mean) ** 2 for pixel in pixels) / len(pixels)
    contrast_score = clamp(math.sqrt(variance) / 80.0)

    width, height = gray.size
    edge_count = 0
    comparisons = 0
    sample_step = 2 if width * height > 60000 else 1
    for y in range(0, height - sample_step, sample_step):
        row_offset = y * width
        next_row_offset = (y + sample_step) * width
        for x in range(0, width - sample_step, sample_step):
            pixel = pixels[row_offset + x]
            if abs(pixel - pixels[row_offset + x + sample_step]) > 28:
                edge_count += 1
            if abs(pixel - pixels[next_row_offset + x]) > 28:
                edge_count += 1
            comparisons += 2
    edge_density = edge_count / comparisons if comparisons else 0.0
    edge_score = clamp(edge_density / 0.12)

    dark_threshold = min(mean - 20, 220)
    row_peaks = 0
    for y in range(height):
        row = pixels[y * width : (y + 1) * width]
        dark_ratio = sum(1 for pixel in row if pixel < dark_threshold) / width
        if dark_ratio >= 0.03:
            row_peaks += 1
    col_peaks = 0
    for x in range(width):
        dark_ratio = sum(1 for y in range(height) if pixels[y * width + x] < dark_threshold) / height
        if dark_ratio >= 0.03:
            col_peaks += 1
    projection_score = 0.5 * clamp(row_peaks / max(height * 0.35, 1.0)) + 0.5 * clamp(
        col_peaks / max(width * 0.35, 1.0)
    )

    colors = crop.quantize(colors=16).getcolors(maxcolors=16) or []
    non_background_colors = max(len(colors) - 1, 0)
    color_score = clamp(non_background_colors / 8.0)
    complexity = (
        0.40 * edge_score
        + 0.25 * contrast_score
        + 0.20 * projection_score
        + 0.15 * color_score
    )
    return round(clamp(complexity), 4)


def embedded_visual_candidates(
    *,
    page: Any,
    page_width: float,
    page_height: float,
    image_regions: list[tuple[float, float, float, float]],
    text_rects: list[tuple[float, float, float, float]],
    text_lines: list[dict[str, Any]],
    good_text_layer: bool,
    page_visual_reference_count: int,
    options: RoutedPdfOptions,
) -> list[dict[str, Any]]:
    if not options.enable_embedded_visual_append or not good_text_layer:
        return []

    page_area = max(page_width * page_height, 1.0)
    candidates: list[dict[str, Any]] = []
    merged_regions = merge_visual_regions(
        image_regions,
        page_width=page_width,
        page_height=page_height,
    )
    for index, region in enumerate(merged_regions, start=1):
        left, bottom, right, top = region
        region_area = rect_area(region)
        area_ratio = region_area / page_area
        width_ratio = max(right - left, 0.0) / max(page_width, 1.0)
        height_ratio = max(top - bottom, 0.0) / max(page_height, 1.0)
        if area_ratio < options.embedded_visual_min_area_ratio:
            continue
        if width_ratio < options.embedded_visual_min_width_ratio:
            continue
        if height_ratio < options.embedded_visual_min_height_ratio:
            continue
        text_overlap = region_text_overlap_ratio(region, text_rects)
        if text_overlap > options.embedded_visual_text_overlap_threshold:
            continue

        visual_complexity = crop_visual_complexity(page, page_width, page_height, region)
        caption_score, nearest_caption = caption_geometry_score(region, text_lines)
        strong_large_region = (
            area_ratio >= options.embedded_visual_force_area_ratio
            and visual_complexity >= 0.20
        )
        structural_reference_region = (
            page_visual_reference_count > 0
            and area_ratio >= options.embedded_visual_min_area_ratio
            and visual_complexity >= 0.20
        )
        candidate = (
            visual_complexity >= options.embedded_visual_complexity_threshold
            or caption_score >= 0.50
            or strong_large_region
            or structural_reference_region
        )
        if not candidate:
            continue

        reasons = []
        if visual_complexity >= options.embedded_visual_complexity_threshold:
            reasons.append("visual_complexity")
        if caption_score >= 0.50:
            reasons.append("caption_geometry")
        if strong_large_region:
            reasons.append("large_visual_region")
        if structural_reference_region:
            reasons.append("text_layer_visual_reference")
        candidates.append(
            {
                "region_index": index,
                "bbox": [round(value, 2) for value in region],
                "area_ratio": round(area_ratio, 4),
                "width_ratio": round(width_ratio, 4),
                "height_ratio": round(height_ratio, 4),
                "text_overlap_ratio": round(text_overlap, 4),
                "visual_complexity_score": visual_complexity,
                "caption_geometry_score": caption_score,
                "nearest_caption": nearest_caption,
                "page_visual_reference_count": page_visual_reference_count,
                "reasons": reasons,
            }
        )
    return candidates


def classify_page(
    *,
    page: int,
    text_chars: int,
    text_quality: float,
    image_area_ratio: float,
    table_score: float,
    complex_table_score: float,
    options: RoutedPdfOptions,
) -> tuple[RoutingMode, list[str]]:
    reasons: list[str] = []
    good_text = (
        text_chars >= options.text_chars_threshold
        and text_quality >= options.text_quality_threshold
    )
    table_text = (
        text_chars >= options.text_chars_threshold
        and text_quality >= options.table_text_quality_threshold
        and image_area_ratio < options.image_area_threshold
        and table_score >= options.table_score_threshold
    )
    if page in set(options.force_reconcile_pages):
        return "IMAGE_RECONCILE", ["forced_reconcile_page"]
    if good_text and table_score < options.table_score_threshold:
        reasons.extend(["good_text_layer", "no_table_signal"])
        return "TEXT_LIGHT", reasons
    if good_text and complex_table_score < options.complex_table_score_threshold:
        reasons.extend(["good_text_layer", "simple_table_signal"])
        return "TEXT_TABLE_FAST", reasons
    if good_text:
        reasons.extend(["good_text_layer", "complex_table_signal"])
        return "TEXT_TABLE_ACCURATE", reasons
    if table_text and complex_table_score < options.complex_table_score_threshold:
        reasons.extend(["table_text_layer", "relaxed_text_quality", "simple_table_signal"])
        return "TEXT_TABLE_FAST", reasons
    if table_text:
        reasons.extend(["table_text_layer", "relaxed_text_quality", "complex_table_signal"])
        return "TEXT_TABLE_ACCURATE", reasons
    if image_area_ratio >= options.image_area_threshold:
        reasons.extend(["weak_text_layer", "image_page"])
    else:
        reasons.extend(["weak_text_layer", "fallback_reconcile"])
    return "IMAGE_RECONCILE", reasons


def pdf_preflight(pdf_path: Path, options: RoutedPdfOptions) -> list[PagePreflight]:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise RuntimeError("pypdfium2 is required for routed PDF preflight.") from exc

    raw = pdfium.raw
    document = pdfium.PdfDocument(str(pdf_path))
    pages: list[PagePreflight] = []
    try:
        for page_index in range(len(document)):
            started = time.perf_counter()
            page = document[page_index]
            textpage = page.get_textpage()
            try:
                width, height = page.get_size()
                page_area = max(float(width) * float(height), 1.0)
                char_count = textpage.count_chars()
                text = textpage.get_text_range(0, char_count) if char_count else ""
                text_rects: list[tuple[float, float, float, float]] = []
                try:
                    for rect_index in range(textpage.count_rects()):
                        text_rects.append(tuple(float(v) for v in textpage.get_rect(rect_index)))
                except Exception:
                    text_rects = []

                text_lines: list[dict[str, Any]] = []
                for rect in text_rects[:800]:
                    try:
                        bounded_text = textpage.get_text_bounded(*rect).strip()
                    except Exception:
                        bounded_text = ""
                    if bounded_text:
                        text_lines.append(
                            {
                                "bbox": [round(value, 2) for value in rect],
                                "text": normalized_text(bounded_text)[:240],
                            }
                        )

                line_count = 0
                rect_count = 0
                image_area = 0.0
                image_regions: list[tuple[float, float, float, float]] = []
                try:
                    for obj in page.get_objects():
                        obj_type = getattr(obj, "type", None)
                        left, bottom, right, top = (float(v) for v in obj.get_bounds())
                        obj_area = max(right - left, 0.0) * max(top - bottom, 0.0)
                        if obj_type == raw.FPDF_PAGEOBJ_IMAGE:
                            image_area += obj_area
                            image_regions.append((left, bottom, right, top))
                        elif obj_type == raw.FPDF_PAGEOBJ_PATH:
                            rect_count += 1
                            if (right - left) < 3 or (top - bottom) < 3:
                                line_count += 1
                except Exception:
                    pass

                quality = text_quality_score(text)
                density = len(text.strip()) / page_area
                image_ratio = clamp(image_area / page_area)
                table_score, complex_table_score = estimate_table_score(
                    line_count=line_count,
                    rect_count=rect_count,
                    text_rects=text_rects,
                    text=text,
                )
                image_read_risk = round(
                    clamp(
                        (0.45 * image_ratio)
                        + (0.35 * (1.0 - quality))
                        + (0.20 * table_score)
                    ),
                    4,
                )
                mode, reasons = classify_page(
                    page=page_index + 1,
                    text_chars=len(text.strip()),
                    text_quality=quality,
                    image_area_ratio=image_ratio,
                    table_score=table_score,
                    complex_table_score=complex_table_score,
                    options=options,
                )
                good_text_layer = (
                    len(text.strip()) >= options.text_chars_threshold
                    and quality >= options.text_quality_threshold
                )
                page_visual_refs = visual_reference_count(text)
                embedded_regions = embedded_visual_candidates(
                    page=page,
                    page_width=float(width),
                    page_height=float(height),
                    image_regions=image_regions,
                    text_rects=text_rects,
                    text_lines=text_lines,
                    good_text_layer=good_text_layer,
                    page_visual_reference_count=page_visual_refs,
                    options=options,
                )
                extra_actions: list[str] = []
                if embedded_regions and mode != "IMAGE_RECONCILE":
                    extra_actions.append("IMAGE_RECONCILE_APPEND")
                    reasons = [*reasons, "embedded_visual_region_candidate"]
                pages.append(
                    PagePreflight(
                        page=page_index + 1,
                        width=round(float(width), 2),
                        height=round(float(height), 2),
                        text_chars=len(text.strip()),
                        text_density=round(density, 6),
                        text_quality_score=quality,
                        image_area_ratio=round(image_ratio, 4),
                        line_count=line_count,
                        rect_count=rect_count,
                        table_score=table_score,
                        complex_table_score=complex_table_score,
                        image_read_risk_score=image_read_risk,
                        mode=mode,
                        reasons=reasons,
                        elapsed_seconds=round(time.perf_counter() - started, 4),
                        extra_actions=extra_actions,
                        embedded_visual_region_count=len(embedded_regions),
                        embedded_visual_regions=embedded_regions,
                        visual_reference_count=page_visual_refs,
                    )
                )
            finally:
                textpage.close()
                page.close()
    finally:
        document.close()
    return pages


def group_pages(preflight: list[PagePreflight]) -> list[RoutingGroup]:
    if not preflight:
        return []
    groups: list[RoutingGroup] = []
    current_mode = preflight[0].mode
    current_extra_actions = tuple(preflight[0].extra_actions)
    current_pages = [preflight[0].page]
    for page in preflight[1:]:
        page_extra_actions = tuple(page.extra_actions)
        can_group = (
            page.mode == current_mode
            and page.page == current_pages[-1] + 1
            and not current_extra_actions
            and not page_extra_actions
        )
        if can_group:
            current_pages.append(page.page)
            continue
        groups.append(
            RoutingGroup(
                mode=current_mode,
                start_page=current_pages[0],
                end_page=current_pages[-1],
                pages=list(current_pages),
            )
        )
        current_mode = page.mode
        current_extra_actions = page_extra_actions
        current_pages = [page.page]
    groups.append(
        RoutingGroup(
            mode=current_mode,
            start_page=current_pages[0],
            end_page=current_pages[-1],
            pages=list(current_pages),
        )
    )
    return groups


def build_standard_converter(
    mode: RoutingMode,
    *,
    table_mode: str | None = None,
) -> DocumentConverter:
    options = PdfPipelineOptions()
    options.generate_page_images = False
    options.images_scale = 1.0
    options.ocr_batch_size = 2
    options.layout_batch_size = 4
    options.table_batch_size = 2

    if mode == "TEXT_LIGHT":
        options.do_ocr = False
        options.do_table_structure = False
        options.force_backend_text = True
    elif mode == "TEXT_TABLE_FAST":
        options.do_ocr = False
        options.do_table_structure = True
        options.table_structure_options.mode = TableFormerMode.FAST
        options.table_structure_options.do_cell_matching = True
        options.force_backend_text = True
    elif mode == "TEXT_TABLE_ACCURATE":
        options.do_ocr = False
        options.do_table_structure = True
        options.table_structure_options.mode = TableFormerMode.ACCURATE
        options.table_structure_options.do_cell_matching = True
        options.force_backend_text = True
    else:
        options.do_ocr = True
        options.ocr_options.force_full_page_ocr = True
        options.do_table_structure = True
        chosen = table_mode or "fast"
        options.table_structure_options.mode = TableFormerMode(chosen)
        options.table_structure_options.do_cell_matching = True
        options.force_backend_text = False

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def bbox_to_dict(bbox: Any) -> dict[str, Any]:
    if bbox is None:
        return {}
    if hasattr(bbox, "model_dump"):
        return bbox.model_dump()
    if hasattr(bbox, "dict"):
        return bbox.dict()
    return {
        "l": float(getattr(bbox, "l")),
        "t": float(getattr(bbox, "t")),
        "r": float(getattr(bbox, "r")),
        "b": float(getattr(bbox, "b")),
        "coord_origin": str(getattr(bbox, "coord_origin", "")),
    }


def table_page_number(table: Any) -> int | None:
    prov = getattr(table, "prov", None) or []
    if not prov:
        return None
    page_no = getattr(prov[0], "page_no", None)
    return int(page_no) if page_no is not None else None


def table_prov_bbox_top_left(table: Any, page_height: float) -> tuple[float, float, float, float] | None:
    prov = getattr(table, "prov", None) or []
    if not prov:
        return None
    bbox = getattr(prov[0], "bbox", None)
    if bbox is None:
        return None
    origin = str(getattr(bbox, "coord_origin", "")).upper()
    left, right = float(bbox.l), float(bbox.r)
    if "BOTTOMLEFT" in origin:
        top = page_height - float(bbox.t)
        bottom = page_height - float(bbox.b)
    else:
        top = float(bbox.t)
        bottom = float(bbox.b)
    return left, top, right, bottom


def docling_bbox_tuple(bbox: Any) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    return float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)


def bbox_center_top_left(rect: tuple[float, float, float, float]) -> tuple[float, float]:
    left, top, right, bottom = rect
    return (left + right) / 2.0, (top + bottom) / 2.0


def rect_contains_point(
    rect: tuple[float, float, float, float],
    x: float,
    y: float,
    *,
    tolerance: float = 2.0,
) -> bool:
    left, top, right, bottom = rect
    return left - tolerance <= x <= right + tolerance and top - tolerance <= y <= bottom + tolerance


def horizontal_distance_to_rect(rect: tuple[float, float, float, float], x: float) -> float:
    left, _top, right, _bottom = rect
    if x < left:
        return left - x
    if x > right:
        return x - right
    return 0.0


def is_empty_cell(value: Any) -> bool:
    text = normalized_text("" if value is None else str(value))
    return not text or text.lower() == "nan"


def dataframe_to_markdown(dataframe: pd.DataFrame) -> str:
    headers = [str(column) for column in dataframe.columns]
    rows = [[str(value) if not pd.isna(value) else "" for value in row] for row in dataframe.to_numpy()]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def format_row(values: list[str]) -> str:
        return "| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(values)) + " |"

    separator = "| " + " | ".join("-" * max(width, 3) for width in widths) + " |"
    return "\n".join([format_row(headers), separator, *[format_row(row) for row in rows]])


def replace_markdown_table(markdown: str, table_index: int, dataframe: pd.DataFrame) -> str:
    target_index = table_index - 1
    current_index = -1
    output: list[str] = []
    block: list[str] = []
    in_table = False
    replaced = False

    def flush_block() -> None:
        nonlocal block, current_index, replaced
        if not block:
            return
        if current_index == target_index:
            output.append(dataframe_to_markdown(dataframe))
            replaced = True
        else:
            output.extend(block)
        block = []

    for line in markdown.splitlines():
        stripped = line.strip()
        is_table_line = stripped.startswith("|") and stripped.endswith("|")
        if is_table_line:
            if not in_table:
                in_table = True
                current_index += 1
            block.append(line)
            continue
        if in_table:
            flush_block()
            in_table = False
        output.append(line)
    if in_table:
        flush_block()
    return "\n".join(output) if replaced else markdown


def pdf_text_spans_in_rect(
    textpage: Any,
    *,
    page_height: float,
    rect: tuple[float, float, float, float],
    tolerance: float = 2.0,
) -> list[dict[str, Any]]:
    char_count = textpage.count_chars()
    spans: list[dict[str, Any]] = []
    current_text: list[str] = []
    current_boxes: list[tuple[float, float, float, float]] = []
    previous_center: tuple[float, float] | None = None

    def flush() -> None:
        nonlocal current_text, current_boxes, previous_center
        text = normalized_text("".join(current_text))
        if text and current_boxes:
            left = min(box[0] for box in current_boxes)
            top = min(box[1] for box in current_boxes)
            right = max(box[2] for box in current_boxes)
            bottom = max(box[3] for box in current_boxes)
            spans.append(
                {
                    "text": text,
                    "bbox": (left, top, right, bottom),
                    "center": ((left + right) / 2.0, (top + bottom) / 2.0),
                }
            )
        current_text = []
        current_boxes = []
        previous_center = None

    page_text = textpage.get_text_range(0, char_count) if char_count else ""
    for char_index, char in enumerate(page_text):
        if char.isspace():
            flush()
            continue
        try:
            left, bottom, right, top = (float(value) for value in textpage.get_charbox(char_index))
        except Exception:
            flush()
            continue
        box = (left, page_height - top, right, page_height - bottom)
        center = bbox_center_top_left(box)
        if not rect_contains_point(rect, center[0], center[1], tolerance=tolerance):
            flush()
            continue
        if previous_center is not None:
            y_delta = abs(center[1] - previous_center[1])
            x_gap = box[0] - current_boxes[-1][2] if current_boxes else 0.0
            if y_delta > 5.0 or x_gap > 10.0 or x_gap < -2.0:
                flush()
        current_text.append(char)
        current_boxes.append(box)
        previous_center = center
    flush()
    return spans


def pdf_text_lines_excluding_rects(
    textpage: Any,
    *,
    page_width: float,
    page_height: float,
    excluded_rects: list[tuple[float, float, float, float]],
    tolerance: float = 3.0,
) -> list[dict[str, Any]]:
    spans = pdf_text_spans_in_rect(
        textpage,
        page_height=page_height,
        rect=(0.0, 0.0, page_width, page_height),
        tolerance=0.0,
    )
    kept_spans: list[dict[str, Any]] = []
    for span in spans:
        center_x, center_y = span["center"]
        if any(
            rect_contains_point(rect, center_x, center_y, tolerance=tolerance)
            for rect in excluded_rects
        ):
            continue
        kept_spans.append(span)

    lines: list[dict[str, Any]] = []
    for span in sorted(kept_spans, key=lambda item: (item["center"][1], item["center"][0])):
        center_y = float(span["center"][1])
        if not lines or abs(center_y - float(lines[-1]["y"])) > 5.0:
            lines.append({"y": center_y, "spans": [span]})
        else:
            lines[-1]["spans"].append(span)
            line_spans = lines[-1]["spans"]
            lines[-1]["y"] = sum(float(item["center"][1]) for item in line_spans) / len(line_spans)

    output: list[dict[str, Any]] = []
    for line in lines:
        ordered = sorted(line["spans"], key=lambda item: item["bbox"][0])
        text = normalized_text(" ".join(str(item["text"]) for item in ordered))
        if text:
            output.append({"y": float(line["y"]), "text": text})
    return output


def pdf_text_layer_logical_lines(textpage: Any) -> list[str]:
    char_count = textpage.count_chars()
    if char_count <= 0:
        return []
    text = unicodedata.normalize("NFKC", textpage.get_text_range(0, char_count))
    text = text.replace("\u00a0", " ")
    return [normalized_text(line) for line in text.splitlines() if normalized_text(line)]


def significant_coverage_tokens(line: str) -> list[str]:
    tokens: list[str] = []
    for token in re.split(r"\s+", normalized_text(line)):
        compact = compact_for_compare(token)
        if len(compact) >= 4:
            tokens.append(compact)
    if tokens:
        return tokens
    compact_line = compact_for_compare(line)
    return [compact_line] if len(compact_line) >= 4 else []


def supplement_missing_text_layer_lines(
    markdown: str,
    textpage: Any,
) -> tuple[str, dict[str, Any]]:
    output_compact = compact_for_compare(markdown)
    lines = pdf_text_layer_logical_lines(textpage)
    supplemented: list[dict[str, Any]] = []
    seen_lines: set[str] = set()

    for line in lines:
        line_compact = compact_for_compare(line)
        if len(line_compact) < 4 or line_compact in output_compact:
            continue
        tokens = significant_coverage_tokens(line)
        missing_tokens = [token for token in tokens if token not in output_compact]
        if not missing_tokens:
            continue
        if line_compact in seen_lines:
            continue
        seen_lines.add(line_compact)
        supplemented.append(
            {
                "text": line,
                "missing_tokens": missing_tokens[:20],
            }
        )
        output_compact += line_compact

    diagnostics = {
        "raw_line_count": len(lines),
        "supplemented_line_count": len(supplemented),
        "supplemented_lines": supplemented[:50],
    }
    if not supplemented:
        return markdown, diagnostics

    supplement_text = "\n".join(str(item["text"]) for item in supplemented)
    supplemented_markdown = (
        f"{markdown}\n\n"
        f"<!-- text_layer_coverage_supplement lines={len(supplemented)} -->\n\n"
        f"{supplement_text}"
    )
    return supplemented_markdown, diagnostics


def coordinate_page_markdown(
    *,
    textpage: Any,
    page_width: float,
    page_height: float,
    table_markdown: str | None,
    table_bbox: tuple[float, float, float, float] | None,
) -> tuple[str, dict[str, int]]:
    excluded_rects = [table_bbox] if table_bbox is not None else []
    text_lines = pdf_text_lines_excluding_rects(
        textpage,
        page_width=page_width,
        page_height=page_height,
        excluded_rects=excluded_rects,
    )
    blocks: list[dict[str, Any]] = [
        {"kind": "text", "y": line["y"], "text": line["text"]}
        for line in text_lines
    ]
    if table_markdown and table_bbox is not None:
        blocks.append({"kind": "table", "y": table_bbox[1], "text": table_markdown})
    blocks.sort(key=lambda item: (float(item["y"]), 0 if item["kind"] == "text" else 1))

    markdown_parts: list[str] = []
    pending_text: list[str] = []
    for block in blocks:
        if block["kind"] == "text":
            pending_text.append(str(block["text"]))
            continue
        if pending_text:
            markdown_parts.append("\n".join(pending_text))
            pending_text = []
        markdown_parts.append(str(block["text"]))
    if pending_text:
        markdown_parts.append("\n".join(pending_text))

    text_chars = sum(len(str(line["text"])) for line in text_lines)
    return "\n\n".join(part for part in markdown_parts if part.strip()), {
        "non_table_text_line_count": len(text_lines),
        "non_table_text_chars": text_chars,
    }


def candidate_row_header_columns(dataframe: pd.DataFrame) -> list[int]:
    if dataframe.empty:
        return []
    candidate_limit = min(len(dataframe.columns), 5)
    candidates: list[int] = []
    for col_index in range(candidate_limit):
        values = [dataframe.iat[row_index, col_index] for row_index in range(len(dataframe))]
        empty_ratio = sum(1 for value in values if is_empty_cell(value)) / max(len(values), 1)
        nonempty_values = [str(value) for value in values if not is_empty_cell(value)]
        numeric_ratio = (
            sum(1 for value in nonempty_values if is_numeric_like(value)) / len(nonempty_values)
            if nonempty_values
            else 0.0
        )
        if empty_ratio >= 0.50 and numeric_ratio < 0.50:
            candidates.append(col_index)
    return candidates


def row_centers_from_boxes(
    row_boxes: dict[int, Any],
    header_rows: int,
    dataframe_rows: int,
) -> list[tuple[int, int, float]]:
    centers: list[tuple[int, int, float]] = []
    for dataframe_row in range(dataframe_rows):
        row_offset = header_rows + dataframe_row
        row_box = docling_bbox_tuple(row_boxes.get(row_offset))
        if row_box is None:
            continue
        centers.append((dataframe_row, row_offset, bbox_center_top_left(row_box)[1]))
    return centers


def repair_row_header_spans(
    dataframe: pd.DataFrame,
    table: Any,
    *,
    pdf_path: Path,
    page_cache: dict[int, tuple[Any, Any, float]],
) -> tuple[pd.DataFrame, dict[str, Any], list[TableRepairWarning]]:
    page_no = table_page_number(table)
    if page_no is None or dataframe.empty:
        return dataframe, {}, []

    row_boxes = table.data.get_row_bounding_boxes()
    col_boxes = table.data.get_column_bounding_boxes()
    header_rows = max(int(getattr(table.data, "num_rows", 0)) - len(dataframe), 0)
    body_row_centers = row_centers_from_boxes(row_boxes, header_rows, len(dataframe))
    if not body_row_centers:
        return dataframe, {}, []

    candidate_columns = candidate_row_header_columns(dataframe)
    if not candidate_columns:
        return dataframe, {}, []

    if page_no not in page_cache:
        try:
            import pypdfium2 as pdfium
        except Exception:
            return dataframe, {}, []
        document = pdfium.PdfDocument(str(pdf_path))
        page = document[page_no - 1]
        page_width, page_height = page.get_size()
        page_cache[page_no] = (document, page, float(page_height))
    _document, page, page_height = page_cache[page_no]
    textpage = page.get_textpage()
    try:
        table_rect = table_prov_bbox_top_left(table, page_height)
        if table_rect is None:
            return dataframe, {}, []
        left, top, right, bottom = table_rect
        table_rect = (max(left - 3.0, 0.0), max(top - 3.0, 0.0), right + 3.0, bottom + 3.0)
        spans = pdf_text_spans_in_rect(textpage, page_height=page_height, rect=table_rect)
    finally:
        textpage.close()

    repaired = dataframe.copy()
    repaired_cells: list[dict[str, Any]] = []
    unrepaired_spans: list[dict[str, Any]] = []

    for col_index in candidate_columns:
        col_box = docling_bbox_tuple(col_boxes.get(col_index))
        if col_box is None:
            continue
        column_compacts = {
            compact_for_compare(str(repaired.iat[row_index, col_index]))
            for row_index in range(len(repaired))
            if not is_empty_cell(repaired.iat[row_index, col_index])
        }
        column_spans: list[dict[str, Any]] = []
        for span in spans:
            text = str(span["text"])
            compact = compact_for_compare(text)
            if not compact or compact in column_compacts:
                continue
            center_x, center_y = span["center"]
            candidate_distances = []
            for candidate_col in candidate_columns:
                candidate_box = docling_bbox_tuple(col_boxes.get(candidate_col))
                if candidate_box is None:
                    continue
                candidate_distances.append(
                    (horizontal_distance_to_rect(candidate_box, center_x), candidate_col)
                )
            if not candidate_distances:
                continue
            nearest_distance, nearest_column = min(candidate_distances, key=lambda item: item[0])
            if nearest_column != col_index or nearest_distance > 28.0:
                continue
            if is_numeric_like(text):
                continue
            if center_y < body_row_centers[0][2] - 8.0 or center_y > body_row_centers[-1][2] + 8.0:
                continue
            column_spans.append(span)

        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for span in sorted(column_spans, key=lambda item: item["center"][1]):
            key = (compact_for_compare(str(span["text"])), round(float(span["center"][1]) / 3.0))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(span)

        for span_index, span in enumerate(deduped):
            y_center = float(span["center"][1])
            upper = (
                (float(deduped[span_index - 1]["center"][1]) + y_center) / 2.0
                if span_index > 0
                else body_row_centers[0][2] - 999.0
            )
            lower = (
                (y_center + float(deduped[span_index + 1]["center"][1])) / 2.0
                if span_index + 1 < len(deduped)
                else body_row_centers[-1][2] + 999.0
            )
            rows_in_interval = [
                dataframe_row
                for dataframe_row, _row_offset, row_center in body_row_centers
                if upper <= row_center < lower
            ]
            if not rows_in_interval:
                unrepaired_spans.append({"column": col_index, "text": span["text"]})
                continue
            target_row = rows_in_interval[0]
            if not is_empty_cell(repaired.iat[target_row, col_index]):
                unrepaired_spans.append(
                    {
                        "column": col_index,
                        "row": target_row + 1,
                        "text": span["text"],
                        "existing": str(repaired.iat[target_row, col_index]),
                    }
                )
                continue
            repaired.iat[target_row, col_index] = str(span["text"])
            repaired_cells.append(
                {
                    "row": target_row + 1,
                    "column": col_index + 1,
                    "text": str(span["text"]),
                    "source_bbox": [round(value, 2) for value in span["bbox"]],
                    "covered_rows": [row + 1 for row in rows_in_interval],
                }
            )

    repair_info = {
        "enabled": True,
        "page": page_no,
        "header_rows": header_rows,
        "candidate_columns": [column + 1 for column in candidate_columns],
        "repaired_cell_count": len(repaired_cells),
        "repaired_cells": repaired_cells,
        "unrepaired_span_count": len(unrepaired_spans),
        "unrepaired_spans": unrepaired_spans[:20],
    }
    warnings: list[TableRepairWarning] = []
    if repaired_cells:
        warnings.append(
            TableRepairWarning(
                page=page_no,
                table_index=0,
                code="ROW_HEADER_SPAN_MISSED",
                level="warning",
                score=0.78,
                message="PDFテキストレイヤー上の行見出しSPANがDoclingの構造化テーブルから欠落していたため、座標ベースで補完しました。",
                suggested_action="補完セルを確認してください。補完できない未使用テキストが残る場合は対象ページだけVLM突合を検討してください。",
                evidence={
                    "repaired_cell_count": len(repaired_cells),
                    "repaired_cells": repaired_cells[:20],
                    "candidate_columns": repair_info["candidate_columns"],
                },
            )
        )
    if unrepaired_spans:
        warnings.append(
            TableRepairWarning(
                page=page_no,
                table_index=0,
                code="TABLE_TEXT_COVERAGE_LOSS",
                level="warning",
                score=0.72,
                message="表領域内のPDFテキストの一部を構造化テーブルへ安全に割り当てられませんでした。",
                suggested_action="該当テーブルのraw出力とPDFを確認してください。必要なら対象ページだけVLM突合を実行してください。",
                evidence={"unrepaired_spans": unrepaired_spans[:20]},
            )
        )
    return repaired, repair_info, warnings


def close_page_cache(page_cache: dict[int, tuple[Any, Any, float]]) -> None:
    for document, page, _height in page_cache.values():
        try:
            page.close()
        except Exception:
            pass
        try:
            document.close()
        except Exception:
            pass


def cluster_positions(values: list[float], tolerance: float) -> list[float]:
    if not values:
        return []
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - clusters[-1][-1]) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def coordinate_cell_empty(value: str) -> bool:
    return not compact_for_compare(value)


def coordinate_row_numeric_count(row: list[str]) -> int:
    return sum(1 for cell in row if is_numeric_like(cell))


def infer_coordinate_header_rows(matrix: list[list[str]]) -> int:
    if not matrix:
        return 0
    column_count = len(matrix[0])
    for row_index, row in enumerate(matrix[: min(len(matrix), 8)]):
        numeric_count = coordinate_row_numeric_count(row)
        if numeric_count >= max(2, int(column_count * 0.25)):
            return max(row_index, 1)
    return 1


def infer_measure_start_column(body_rows: list[list[str]]) -> int:
    if not body_rows:
        return 0
    column_count = len(body_rows[0])
    sample_rows = body_rows[: min(len(body_rows), 20)]
    numeric_ratios: list[float] = []
    for col_index in range(column_count):
        values = [row[col_index] for row in sample_rows if not coordinate_cell_empty(row[col_index])]
        if not values:
            numeric_ratios.append(0.0)
            continue
        numeric_ratios.append(sum(1 for value in values if is_numeric_like(value)) / len(values))
    for col_index in range(column_count - 1):
        if numeric_ratios[col_index] >= 0.55 and numeric_ratios[col_index + 1] >= 0.55:
            return col_index
    for col_index, ratio in enumerate(numeric_ratios):
        if ratio >= 0.70:
            return col_index
    return 0


def nearest_header_value(row: list[str], positions: list[int], col_index: int) -> str:
    if not positions:
        return ""
    nearest = min(positions, key=lambda position: (abs(position - col_index), position > col_index))
    return row[nearest]


def scoped_header_fill_value(
    row: list[str],
    positions: list[int],
    col_index: int,
) -> str:
    if not positions:
        return ""
    left_positions = [position for position in positions if position <= col_index]
    if left_positions:
        return row[max(left_positions)]
    right_positions = [position for position in positions if position >= col_index]
    if right_positions:
        return row[min(right_positions)]
    return ""


def expand_coordinate_header_rows(
    header_rows: list[list[str]],
    *,
    measure_start_col: int,
) -> list[list[str]]:
    if not header_rows:
        return []
    expanded = [list(row) for row in header_rows]
    column_count = len(expanded[0])

    for row_index, row in enumerate(expanded):
        explicit_measure_positions = [
            col_index
            for col_index in range(measure_start_col, column_count)
            if not coordinate_cell_empty(row[col_index])
        ]
        if row_index == 0:
            if explicit_measure_positions:
                original_empty = [
                    coordinate_cell_empty(header_rows[0][col_index])
                    for col_index in range(column_count)
                ]
                for col_index in range(measure_start_col, column_count):
                    if coordinate_cell_empty(row[col_index]):
                        row[col_index] = nearest_header_value(row, explicit_measure_positions, col_index)
                if len(expanded) > 1:
                    child_row = expanded[1]
                    for col_index in range(measure_start_col + 1, column_count):
                        if (
                            original_empty[col_index]
                            and row[col_index] != row[col_index - 1]
                            and coordinate_cell_empty(child_row[col_index])
                        ):
                            row[col_index] = row[col_index - 1]
            continue
        if explicit_measure_positions:
            for col_index in range(measure_start_col, column_count):
                if coordinate_cell_empty(row[col_index]):
                    if row_index > 0:
                        parent_value = expanded[row_index - 1][col_index]
                        parent_compact = compact_for_compare(parent_value)
                        scoped_positions = [
                            position
                            for position in explicit_measure_positions
                            if compact_for_compare(expanded[row_index - 1][position]) == parent_compact
                        ]
                        if not scoped_positions:
                            continue
                    else:
                        scoped_positions = explicit_measure_positions
                    row[col_index] = scoped_header_fill_value(row, scoped_positions, col_index)
        if row_index > 0:
            for col_index in range(measure_start_col):
                if coordinate_cell_empty(row[col_index]):
                    row[col_index] = expanded[row_index - 1][col_index]
    return expanded


def make_unique_headers(headers: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    unique: list[str] = []
    for index, header in enumerate(headers, start=1):
        base = normalized_text(header) or f"col_{index}"
        counts[base] += 1
        unique.append(base if counts[base] == 1 else f"{base}.{counts[base]}")
    return unique


def build_coordinate_headers(
    header_rows: list[list[str]],
    *,
    measure_start_col: int,
) -> list[str]:
    if not header_rows:
        return []
    expanded = expand_coordinate_header_rows(
        header_rows,
        measure_start_col=measure_start_col,
    )
    headers: list[str] = []
    column_count = len(expanded[0])
    for col_index in range(column_count):
        parts: list[str] = []
        for row in expanded:
            value = normalized_text(row[col_index])
            if value and value not in parts:
                parts.append(value)
        headers.append(".".join(parts))
    return make_unique_headers(headers)


def fill_coordinate_row_headers(
    body_rows: list[list[str]],
    *,
    measure_start_col: int,
) -> tuple[list[list[str]], int]:
    if not body_rows or measure_start_col <= 0:
        return body_rows, 0
    filled = [list(row) for row in body_rows]
    fill_count = 0
    last_values = ["" for _ in range(measure_start_col)]
    for row in filled:
        for col_index in range(measure_start_col):
            value = row[col_index]
            if coordinate_cell_empty(value):
                if last_values[col_index]:
                    row[col_index] = last_values[col_index]
                    fill_count += 1
            else:
                last_values[col_index] = value
    for col_index in range(measure_start_col):
        first_value = next(
            (row[col_index] for row in filled if not coordinate_cell_empty(row[col_index])),
            "",
        )
        if not first_value:
            continue
        for row in filled:
            if not coordinate_cell_empty(row[col_index]):
                break
            row[col_index] = first_value
            fill_count += 1
    return filled, fill_count


def structure_coordinate_matrix(matrix: list[list[str]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    column_count = len(matrix[0]) if matrix else 0
    headers = [f"col_{index + 1:03d}" for index in range(column_count)]
    dataframe = pd.DataFrame(matrix, columns=headers)
    return dataframe, {
        "header_mode": "none",
        "header_inference_enabled": False,
        "header_row_count": 0,
        "measure_start_column": None,
        "filled_row_header_count": 0,
        "pdf_grid_rows_preserved": True,
        "headers": headers,
    }


def page_grid_lines(page: Any, page_height: float) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return [], []
    raw = pdfium.raw
    horizontal: list[dict[str, float]] = []
    vertical: list[dict[str, float]] = []
    try:
        objects = list(page.get_objects())
    except Exception:
        return horizontal, vertical
    try:
        for obj in objects:
            if getattr(obj, "type", None) != raw.FPDF_PAGEOBJ_PATH:
                continue
            try:
                left, bottom, right, top = (float(value) for value in obj.get_bounds())
            except Exception:
                continue
            width = max(right - left, 0.0)
            height = max(top - bottom, 0.0)
            top_y = page_height - top
            bottom_y = page_height - bottom
            if width >= 20.0 and height <= 2.5:
                horizontal.append({"x1": left, "x2": right, "y": (top_y + bottom_y) / 2.0})
            elif height >= 20.0 and width <= 2.5:
                vertical.append({"x": (left + right) / 2.0, "y1": top_y, "y2": bottom_y})
    except Exception:
        return [], []
    return horizontal, vertical


def coordinate_table_bbox(
    horizontal: list[dict[str, float]],
    vertical: list[dict[str, float]],
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float] | None:
    if len(horizontal) < 3 or len(vertical) < 3:
        return None
    long_horizontal = [
        line
        for line in horizontal
        if (line["x2"] - line["x1"]) >= page_width * 0.25
    ]
    long_vertical = [
        line
        for line in vertical
        if (line["y2"] - line["y1"]) >= page_height * 0.08
    ]
    if len(long_horizontal) < 3 or len(long_vertical) < 3:
        return None
    left = min(line["x1"] for line in long_horizontal)
    right = max(line["x2"] for line in long_horizontal)
    top = min(line["y"] for line in long_horizontal)
    bottom = max(line["y"] for line in long_horizontal)
    return (left, top, right, bottom)


def group_spans_into_lines(spans: list[dict[str, Any]], *, tolerance: float = 5.0) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for span in sorted(spans, key=lambda item: (item["center"][1], item["bbox"][0])):
        center_y = float(span["center"][1])
        if not lines or abs(center_y - float(lines[-1]["y"])) > tolerance:
            lines.append({"y": center_y, "spans": [span]})
            continue
        lines[-1]["spans"].append(span)
        line_spans = lines[-1]["spans"]
        lines[-1]["y"] = sum(float(item["center"][1]) for item in line_spans) / len(line_spans)
    for line in lines:
        line["spans"] = sorted(line["spans"], key=lambda item: item["bbox"][0])
    return lines


def line_table_likelihood(line: dict[str, Any]) -> float:
    spans = list(line.get("spans") or [])
    if not spans:
        return 0.0
    numeric_count = sum(1 for span in spans if is_numeric_like(str(span.get("text") or "")))
    span_count = len(spans)
    if numeric_count >= 2:
        return 1.0
    if span_count >= 6:
        return 0.85
    if span_count >= 4:
        return 0.65
    return 0.0


def coordinate_text_alignment_dataframe(
    *,
    textpage: Any,
    page_no: int,
    page_width: float,
    page_height: float,
    horizontal_line_count: int,
    vertical_line_count: int,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    spans = pdf_text_spans_in_rect(
        textpage,
        page_height=page_height,
        rect=(0.0, 0.0, page_width, page_height),
        tolerance=0.0,
    )
    lines = group_spans_into_lines(spans)
    candidate_indices = [
        index for index, line in enumerate(lines) if line_table_likelihood(line) >= 0.65
    ]
    if not candidate_indices:
        return None, {
            "method": "pdf_text_alignment",
            "page": page_no,
            "status": "no_alignment_rows",
            "horizontal_line_count": horizontal_line_count,
            "vertical_line_count": vertical_line_count,
            "span_count": len(spans),
            "line_count": len(lines),
        }

    start_index = min(candidate_indices)
    end_index = max(candidate_indices)
    while start_index > 0:
        current_y = float(lines[start_index]["y"])
        previous = lines[start_index - 1]
        previous_gap = current_y - float(previous["y"])
        if previous_gap > 24.0 or len(previous.get("spans") or []) == 0:
            break
        if float(previous["y"]) < page_height * 0.06:
            break
        start_index -= 1

    table_lines = [
        line for line in lines[start_index : end_index + 1] if line.get("spans")
    ]
    if len(table_lines) < 2:
        return None, {
            "method": "pdf_text_alignment",
            "page": page_no,
            "status": "too_few_alignment_rows",
            "horizontal_line_count": horizontal_line_count,
            "vertical_line_count": vertical_line_count,
            "span_count": len(spans),
            "line_count": len(lines),
        }

    anchor_positions: list[float] = []
    for line in table_lines:
        if line_table_likelihood(line) < 0.65:
            continue
        for span in line["spans"]:
            anchor_positions.append(float(span["center"][0]))
    column_centers = cluster_positions(anchor_positions, tolerance=8.0)
    if len(column_centers) < 2:
        return None, {
            "method": "pdf_text_alignment",
            "page": page_no,
            "status": "too_few_alignment_columns",
            "horizontal_line_count": horizontal_line_count,
            "vertical_line_count": vertical_line_count,
            "span_count": len(spans),
            "line_count": len(lines),
        }

    matrix: list[list[str]] = []
    assigned_span_count = 0
    table_span_count = 0
    for line in table_lines:
        row_cells: list[list[str]] = [[] for _ in column_centers]
        for span in line["spans"]:
            table_span_count += 1
            center_x = float(span["center"][0])
            nearest_index, nearest_center = min(
                enumerate(column_centers),
                key=lambda item: abs(item[1] - center_x),
            )
            if abs(nearest_center - center_x) > 16.0:
                continue
            row_cells[nearest_index].append(str(span["text"]))
            assigned_span_count += 1
        matrix.append([normalized_text(" ".join(cell)) for cell in row_cells])

    nonempty_row_indices = [
        index for index, row in enumerate(matrix) if any(compact_for_compare(cell) for cell in row)
    ]
    nonempty_col_indices = [
        index
        for index in range(len(column_centers))
        if any(compact_for_compare(row[index]) for row in matrix)
    ]
    if not nonempty_row_indices or len(nonempty_col_indices) < 2:
        return None, {
            "method": "pdf_text_alignment",
            "page": page_no,
            "status": "empty_alignment_table",
            "horizontal_line_count": horizontal_line_count,
            "vertical_line_count": vertical_line_count,
            "span_count": len(spans),
            "line_count": len(lines),
        }

    trimmed = [
        [matrix[row_index][col_index] for col_index in nonempty_col_indices]
        for row_index in nonempty_row_indices
    ]
    dataframe, structure_diagnostics = structure_coordinate_matrix(trimmed)
    used_spans = [
        span
        for line in table_lines
        for span in line["spans"]
    ]
    left = min(float(span["bbox"][0]) for span in used_spans)
    top = min(float(span["bbox"][1]) for span in used_spans)
    right = max(float(span["bbox"][2]) for span in used_spans)
    bottom = max(float(span["bbox"][3]) for span in used_spans)
    page_area = max(page_width * page_height, 1.0)
    table_area = max((right - left) * (bottom - top), 0.0)
    cell_lengths = [
        len(normalized_text(cell))
        for row in trimmed
        for cell in row
        if normalized_text(cell)
    ]
    long_text_row_ratios: list[float] = []
    for row in trimmed:
        long_text_cells = 0
        for cell in row:
            text = normalized_text(cell)
            if len(text) >= 40 and len(text.split()) >= 4:
                long_text_cells += 1
        long_text_row_ratios.append(long_text_cells / max(len(row), 1))
    median_cell_chars = (
        float(pd.Series(cell_lengths).median()) if cell_lengths else 0.0
    )
    max_cell_chars = max(cell_lengths) if cell_lengths else 0
    trimmed_rows = len(dataframe)
    trimmed_columns = len(dataframe.columns)
    diagnostics = {
        "method": "pdf_text_alignment",
        "page": page_no,
        "status": "ok",
        "bbox": [round(value, 2) for value in (left, top, right, bottom)],
        "table_area_ratio": round(table_area / page_area, 4),
        "horizontal_line_count": horizontal_line_count,
        "vertical_line_count": vertical_line_count,
        "line_count": len(lines),
        "table_line_count": len(table_lines),
        "raw_rows": len(matrix),
        "raw_columns": len(column_centers),
        "trimmed_rows": trimmed_rows,
        "trimmed_columns": trimmed_columns,
        "span_count": table_span_count,
        "assigned_span_count": assigned_span_count,
        "span_coverage": round(
            assigned_span_count / max(table_span_count, 1),
            4,
        ),
        "spans_per_trimmed_row": round(
            table_span_count / max(trimmed_rows, 1),
            4,
        ),
        "max_cell_chars": max_cell_chars,
        "median_cell_chars": round(median_cell_chars, 4),
        "max_to_median_cell_char_ratio": round(
            max_cell_chars / max(median_cell_chars, 1.0),
            4,
        ),
        "max_long_text_row_ratio": round(max(long_text_row_ratios or [0.0]), 4),
        **structure_diagnostics,
    }
    return dataframe, diagnostics


def grid_reconstruction_collapsed(diagnostics: dict[str, Any]) -> bool:
    columns = int(diagnostics.get("trimmed_columns") or diagnostics.get("columns") or 0)
    spans_per_row = float(diagnostics.get("spans_per_trimmed_row") or 0.0)
    span_coverage = float(diagnostics.get("span_coverage") or 0.0)
    max_long_text_row_ratio = float(diagnostics.get("max_long_text_row_ratio") or 0.0)
    row_limit = max(12.0, columns * 3.0)
    return (
        span_coverage < 0.95
        or spans_per_row > row_limit
        or max_long_text_row_ratio >= 0.50
    )


def text_alignment_better_than_grid(
    grid_diagnostics: dict[str, Any],
    alignment_diagnostics: dict[str, Any],
) -> bool:
    if alignment_diagnostics.get("status") != "ok":
        return False
    grid_rows = int(grid_diagnostics.get("trimmed_rows") or 0)
    alignment_rows = int(alignment_diagnostics.get("trimmed_rows") or 0)
    grid_columns = int(grid_diagnostics.get("trimmed_columns") or 0)
    alignment_columns = int(alignment_diagnostics.get("trimmed_columns") or 0)
    grid_span_coverage = float(grid_diagnostics.get("span_coverage") or 0.0)
    alignment_span_coverage = float(alignment_diagnostics.get("span_coverage") or 0.0)
    grid_spans_per_row = float(grid_diagnostics.get("spans_per_trimmed_row") or 0.0)
    alignment_spans_per_row = float(alignment_diagnostics.get("spans_per_trimmed_row") or 0.0)
    if alignment_rows < max(2, grid_rows):
        return False
    if alignment_columns < 2:
        return False
    if alignment_span_coverage + 0.05 < grid_span_coverage:
        return False
    if grid_spans_per_row and alignment_spans_per_row >= grid_spans_per_row:
        return False
    return alignment_columns >= min(grid_columns, 2)


def coordinate_table_dataframe(
    *,
    page: Any,
    textpage: Any,
    page_no: int,
    page_width: float,
    page_height: float,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    horizontal, vertical = page_grid_lines(page, page_height)
    bbox = coordinate_table_bbox(horizontal, vertical, page_width, page_height)
    if bbox is None:
        alignment_dataframe, alignment_diagnostics = coordinate_text_alignment_dataframe(
            textpage=textpage,
            page_no=page_no,
            page_width=page_width,
            page_height=page_height,
            horizontal_line_count=len(horizontal),
            vertical_line_count=len(vertical),
        )
        if alignment_dataframe is not None:
            return alignment_dataframe, alignment_diagnostics
        return None, {
            "method": "pdf_coordinate_grid",
            "page": page_no,
            "status": "no_grid_bbox",
            "horizontal_line_count": len(horizontal),
            "vertical_line_count": len(vertical),
        }
    left, top, right, bottom = bbox
    x_lines = cluster_positions(
        [
            line["x"]
            for line in vertical
            if line["y2"] >= top - 3.0 and line["y1"] <= bottom + 3.0
        ],
        tolerance=3.0,
    )
    y_lines = cluster_positions(
        [
            line["y"]
            for line in horizontal
            if line["x2"] >= left - 3.0 and line["x1"] <= right + 3.0
        ],
        tolerance=3.0,
    )
    x_lines = [value for value in x_lines if left - 5.0 <= value <= right + 5.0]
    y_lines = [value for value in y_lines if top - 5.0 <= value <= bottom + 5.0]
    x_lines = sorted(x_lines)
    y_lines = sorted(y_lines)
    if len(x_lines) < 2 or len(y_lines) < 2:
        return None, {
            "method": "pdf_coordinate_grid",
            "page": page_no,
            "status": "insufficient_grid_lines",
            "bbox": [round(value, 2) for value in bbox],
            "x_line_count": len(x_lines),
            "y_line_count": len(y_lines),
        }

    spans = pdf_text_spans_in_rect(
        textpage,
        page_height=page_height,
        rect=(left - 2.0, top - 2.0, right + 2.0, bottom + 2.0),
        tolerance=3.0,
    )
    rows = len(y_lines) - 1
    columns = len(x_lines) - 1
    cells: list[list[list[dict[str, Any]]]] = [
        [[] for _col in range(columns)] for _row in range(rows)
    ]
    assigned_span_count = 0
    for span in spans:
        center_x, center_y = span["center"]
        row_index = next(
            (index for index in range(rows) if y_lines[index] - 2.0 <= center_y <= y_lines[index + 1] + 2.0),
            None,
        )
        col_index = next(
            (index for index in range(columns) if x_lines[index] - 2.0 <= center_x <= x_lines[index + 1] + 2.0),
            None,
        )
        if row_index is None or col_index is None:
            continue
        cells[row_index][col_index].append(span)
        assigned_span_count += 1

    matrix: list[list[str]] = []
    for row in cells:
        values: list[str] = []
        for cell_spans in row:
            ordered = sorted(cell_spans, key=lambda item: (item["bbox"][1], item["bbox"][0]))
            text = " ".join(str(item["text"]) for item in ordered)
            values.append(normalized_text(text))
        matrix.append(values)

    nonempty_row_indices = [
        index for index, row in enumerate(matrix) if any(compact_for_compare(cell) for cell in row)
    ]
    nonempty_col_indices = [
        index
        for index in range(columns)
        if any(compact_for_compare(row[index]) for row in matrix)
    ]
    if not nonempty_row_indices or not nonempty_col_indices:
        return None, {
            "method": "pdf_coordinate_grid",
            "page": page_no,
            "status": "empty_grid_text",
            "bbox": [round(value, 2) for value in bbox],
            "rows": rows,
            "columns": columns,
        }
    trimmed = [
        [matrix[row_index][col_index] for col_index in nonempty_col_indices]
        for row_index in nonempty_row_indices
    ]
    dataframe, structure_diagnostics = structure_coordinate_matrix(trimmed)
    cell_lengths = [
        len(normalized_text(cell))
        for row in trimmed
        for cell in row
        if normalized_text(cell)
    ]
    long_text_row_ratios: list[float] = []
    for row in trimmed:
        long_text_cells = 0
        for cell in row:
            text = normalized_text(cell)
            if len(text) >= 40 and len(text.split()) >= 4:
                long_text_cells += 1
        long_text_row_ratios.append(long_text_cells / max(len(row), 1))
    median_cell_chars = (
        float(pd.Series(cell_lengths).median()) if cell_lengths else 0.0
    )
    max_cell_chars = max(cell_lengths) if cell_lengths else 0
    page_area = max(page_width * page_height, 1.0)
    table_area = max((right - left) * (bottom - top), 0.0)
    trimmed_rows = len(dataframe)
    trimmed_columns = len(dataframe.columns)
    diagnostics = {
        "method": "pdf_coordinate_grid",
        "page": page_no,
        "status": "ok",
        "bbox": [round(value, 2) for value in bbox],
        "table_area_ratio": round(table_area / page_area, 4),
        "x_line_count": len(x_lines),
        "y_line_count": len(y_lines),
        "raw_rows": rows,
        "raw_columns": columns,
        "trimmed_rows": trimmed_rows,
        "trimmed_columns": trimmed_columns,
        "span_count": len(spans),
        "assigned_span_count": assigned_span_count,
        "span_coverage": round(
            assigned_span_count / max(len(spans), 1),
            4,
        ),
        "spans_per_trimmed_row": round(
            len(spans) / max(trimmed_rows, 1),
            4,
        ),
        "max_cell_chars": max_cell_chars,
        "median_cell_chars": round(median_cell_chars, 4),
        "max_to_median_cell_char_ratio": round(
            max_cell_chars / max(median_cell_chars, 1.0),
            4,
        ),
        "max_long_text_row_ratio": round(max(long_text_row_ratios or [0.0]), 4),
        **structure_diagnostics,
    }
    if grid_reconstruction_collapsed(diagnostics):
        alignment_dataframe, alignment_diagnostics = coordinate_text_alignment_dataframe(
            textpage=textpage,
            page_no=page_no,
            page_width=page_width,
            page_height=page_height,
            horizontal_line_count=len(horizontal),
            vertical_line_count=len(vertical),
        )
        if (
            alignment_dataframe is not None
            and text_alignment_better_than_grid(diagnostics, alignment_diagnostics)
        ):
            alignment_diagnostics = {
                **alignment_diagnostics,
                "fallback_from_method": diagnostics.get("method"),
                "fallback_from_status": diagnostics.get("status"),
                "fallback_from_rows": diagnostics.get("trimmed_rows"),
                "fallback_from_columns": diagnostics.get("trimmed_columns"),
                "fallback_from_spans_per_row": diagnostics.get("spans_per_trimmed_row"),
            }
            return alignment_dataframe, alignment_diagnostics
    return dataframe, diagnostics


def coordinate_quality_report(
    diagnostics: dict[str, Any],
    options: RoutedPdfOptions,
) -> dict[str, Any]:
    reasons: list[str] = []
    status = str(diagnostics.get("status") or "")
    rows = int(diagnostics.get("trimmed_rows") or diagnostics.get("rows") or 0)
    columns = int(diagnostics.get("trimmed_columns") or diagnostics.get("columns") or 0)
    span_count = int(diagnostics.get("span_count") or 0)
    span_coverage = float(diagnostics.get("span_coverage") or 0.0)
    spans_per_row = float(diagnostics.get("spans_per_trimmed_row") or 0.0)
    max_cell_chars = int(diagnostics.get("max_cell_chars") or 0)
    max_to_median = float(diagnostics.get("max_to_median_cell_char_ratio") or 0.0)
    max_long_text_row_ratio = float(diagnostics.get("max_long_text_row_ratio") or 0.0)
    span_per_row_limit = max(12.0, float(columns) * 3.0)

    if status != "ok":
        reasons.append(f"coordinate_status_{status or 'unknown'}")
    if rows < 2:
        reasons.append("too_few_rows")
    if columns < 2:
        reasons.append("too_few_columns")
    if span_count > 0 and span_coverage < options.coordinate_min_span_coverage:
        reasons.append("low_span_coverage")
    if spans_per_row > span_per_row_limit:
        reasons.append("row_collapse_risk")
    if max_cell_chars > options.coordinate_max_cell_chars:
        reasons.append("giant_cell_risk")
    if max_to_median > options.coordinate_max_cell_char_ratio:
        reasons.append("cell_size_skew")
    if max_long_text_row_ratio >= 0.50:
        reasons.append("prose_row_risk")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "rows": rows,
        "columns": columns,
        "span_count": span_count,
        "span_coverage": round(span_coverage, 4),
        "spans_per_row": round(spans_per_row, 4),
        "span_per_row_limit": round(span_per_row_limit, 4),
        "max_cell_chars": max_cell_chars,
        "max_to_median_cell_char_ratio": round(max_to_median, 4),
        "max_long_text_row_ratio": round(max_long_text_row_ratio, 4),
    }


def coordinate_bbox_bottom_left(
    diagnostics: dict[str, Any],
    *,
    page_height: float,
) -> tuple[float, float, float, float] | None:
    bbox = diagnostics.get("bbox")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return None
    left, top, right, bottom = (float(value) for value in bbox)
    return (left, page_height - bottom, right, page_height - top)


def coordinate_bbox_overlaps_regions(
    diagnostics: dict[str, Any],
    regions: list[dict[str, Any]],
    *,
    page_height: float,
    min_overlap_ratio: float = 0.25,
) -> bool:
    coord_rect = coordinate_bbox_bottom_left(diagnostics, page_height=page_height)
    if coord_rect is None:
        return False
    coord_area = max(rect_area(coord_rect), 1.0)
    for region in regions:
        bbox = region.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        region_rect = tuple(float(value) for value in bbox)
        region_area = max(rect_area(region_rect), 1.0)
        overlap = overlap_area(coord_rect, region_rect)
        if overlap / min(coord_area, region_area) >= min_overlap_ratio:
            return True
    return False


def select_table_vlm_model(
    page_preflight: PagePreflight,
    diagnostics: dict[str, Any],
    quality: dict[str, Any],
    options: RoutedPdfOptions,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    columns = int(quality.get("columns") or diagnostics.get("trimmed_columns") or 0)
    table_area_ratio = float(diagnostics.get("table_area_ratio") or 0.0)
    spans_per_row = float(quality.get("spans_per_row") or 0.0)
    max_cell_chars = int(quality.get("max_cell_chars") or 0)
    quality_reasons = set(str(reason) for reason in quality.get("reasons") or [])

    if columns >= options.table_vlm_large_min_columns:
        reasons.append("many_columns")
    if (
        table_area_ratio >= options.table_vlm_large_min_area_ratio
        and spans_per_row >= 50.0
    ):
        reasons.append("large_dense_table_area")
    if spans_per_row >= 50.0:
        reasons.append("high_text_density")
    if max_cell_chars > options.coordinate_max_cell_chars:
        reasons.append("giant_cell")
    if quality_reasons.intersection({"giant_cell_risk"}):
        reasons.append("coordinate_collapse_risk")
    if (
        page_preflight.complex_table_score >= options.complex_table_score_threshold + 0.20
        and spans_per_row >= 45.0
    ):
        reasons.append("very_complex_table_signal")

    model = options.large_table_vlm_model if reasons else options.table_vlm_model
    return model, reasons


def convert_coordinate_table_page(
    *,
    document: Any,
    page_no: int,
) -> tuple[str, list[dict[str, Any]], str, list[TableRepairWarning], dict[str, Any]]:
    page = document[page_no - 1]
    textpage = page.get_textpage()
    try:
        page_width, page_height = (float(value) for value in page.get_size())
        dataframe, diagnostics = coordinate_table_dataframe(
            page=page,
            textpage=textpage,
            page_no=page_no,
            page_width=page_width,
            page_height=page_height,
        )
        if dataframe is None:
            page_markdown, text_diagnostics = coordinate_page_markdown(
                textpage=textpage,
                page_width=page_width,
                page_height=page_height,
                table_markdown=None,
                table_bbox=None,
            )
            page_markdown, coverage_diagnostics = supplement_missing_text_layer_lines(
                page_markdown,
                textpage,
            )
            diagnostics = {**diagnostics, **text_diagnostics}
            diagnostics["text_layer_coverage"] = coverage_diagnostics
            warning = TableRepairWarning(
                page=page_no,
                table_index=1,
                code="COORDINATE_TABLE_RECONSTRUCTION_FAILED",
                level="warning",
                score=0.70,
                message="PDF座標ベースの表復元で有効な罫線グリッドを作れませんでした。",
                suggested_action="このページはTEXT_TABLE_VLMまたはTEXT_TABLE_ACCURATEで再実行してください。",
                evidence=diagnostics,
            )
            return page_markdown, [], "", [warning], diagnostics

        csv_text = dataframe.to_csv(index=False)
        html_text = dataframe.to_html(index=False, escape=False)
        table_markdown = dataframe_to_markdown(dataframe)
        table_bbox_values = diagnostics.get("bbox")
        table_bbox = (
            tuple(float(value) for value in table_bbox_values)
            if isinstance(table_bbox_values, list) and len(table_bbox_values) == 4
            else None
        )
        page_markdown, text_diagnostics = coordinate_page_markdown(
            textpage=textpage,
            page_width=page_width,
            page_height=page_height,
            table_markdown=table_markdown,
            table_bbox=table_bbox,
        )
        page_markdown, coverage_diagnostics = supplement_missing_text_layer_lines(
            page_markdown,
            textpage,
        )
        diagnostics.update(text_diagnostics)
        diagnostics["text_layer_coverage"] = coverage_diagnostics
        warnings: list[TableRepairWarning] = []
        if coverage_diagnostics.get("supplemented_line_count"):
            warnings.append(
                TableRepairWarning(
                    page=page_no,
                    table_index=1,
                    code="COORDINATE_TEXT_LAYER_SUPPLEMENTED",
                    level="info",
                    score=0.70,
                    message=(
                        "Coordinate table output was supplemented with PDF text-layer "
                        "lines that were not represented in the reconstructed table."
                    ),
                    suggested_action=(
                        "Review supplemented lines if this page is business-critical."
                    ),
                    evidence=coverage_diagnostics,
                )
            )
        table = {
            "index": 1,
            "rows": int(len(dataframe)),
            "columns": int(len(dataframe.columns)),
            "headers": [str(column) for column in dataframe.columns],
            "csv": csv_text,
            "html": html_text,
            "page": page_no,
            "repair": {
                "enabled": False,
                "coordinate_reconstruction": diagnostics,
            },
        }
        return page_markdown, [table], "\n".join([csv_text, html_text]), warnings, diagnostics
    finally:
        textpage.close()
        page.close()


def convert_coordinate_tables_range(
    pdf_path: Path,
    start_page: int,
    end_page: int,
) -> tuple[str, list[dict[str, Any]], str, list[TableRepairWarning]]:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise RuntimeError("pypdfium2 is required for coordinate table reconstruction.") from exc

    started = time.perf_counter()
    document = pdfium.PdfDocument(str(pdf_path))
    markdown_parts: list[str] = []
    tables: list[dict[str, Any]] = []
    table_text_parts: list[str] = []
    warnings: list[TableRepairWarning] = []
    try:
        for page_no in range(start_page, end_page + 1):
            page_markdown, page_tables, page_table_text, page_warnings, _diagnostics = (
                convert_coordinate_table_page(document=document, page_no=page_no)
            )
            for table in page_tables:
                adjusted = dict(table)
                adjusted["index"] = len(tables) + 1
                tables.append(adjusted)
            for warning in page_warnings:
                warning.table_index = len(tables) + 1
                warnings.append(warning)
            if page_markdown:
                markdown_parts.append(page_markdown)
            if page_table_text:
                table_text_parts.append(page_table_text)
    finally:
        document.close()

    elapsed = round(time.perf_counter() - started, 3)
    if markdown_parts:
        markdown_parts.insert(0, f"<!-- coordinate_table_reconstruction elapsed_seconds: {elapsed} -->")
    return "\n\n".join(markdown_parts), tables, "\n".join(table_text_parts), warnings


def export_docling_result(
    document: Any,
    *,
    pdf_path: Path | None = None,
    repair_tables: bool = False,
) -> tuple[str, list[dict[str, Any]], str, list[TableRepairWarning]]:
    tables: list[dict[str, Any]] = []
    table_text_parts: list[str] = []
    table_warnings: list[TableRepairWarning] = []
    markdown = document.export_to_markdown()
    page_cache: dict[int, tuple[Any, Any, float]] = {}
    for table_index, table in enumerate(getattr(document, "tables", []), start=1):
        dataframe: pd.DataFrame = table.export_to_dataframe(doc=document)
        repair_info: dict[str, Any] = {"enabled": False}
        if repair_tables and pdf_path is not None:
            dataframe, repair_info, repair_warnings = repair_row_header_spans(
                dataframe,
                table,
                pdf_path=pdf_path,
                page_cache=page_cache,
            )
            for warning in repair_warnings:
                warning.table_index = table_index
            table_warnings.extend(repair_warnings)
            if repair_info.get("repaired_cell_count", 0):
                markdown = replace_markdown_table(markdown, table_index, dataframe)
        csv_text = dataframe.to_csv(index=False)
        html_text = (
            dataframe.to_html(index=False, escape=False)
            if repair_info.get("repaired_cell_count", 0)
            else table.export_to_html(doc=document)
        )
        tables.append(
            {
                "index": table_index,
                "rows": int(len(dataframe)),
                "columns": int(len(dataframe.columns)),
                "headers": [str(column) for column in dataframe.columns],
                "csv": csv_text,
                "html": html_text,
                "page": table_page_number(table),
                "repair": repair_info,
            }
        )
        table_text_parts.extend([csv_text, html_text])
    close_page_cache(page_cache)
    return markdown, tables, "\n".join(table_text_parts), table_warnings


def convert_docling_range(
    converter: DocumentConverter,
    pdf_path: Path,
    start_page: int,
    end_page: int,
    *,
    repair_tables: bool = False,
) -> tuple[str, list[dict[str, Any]], str, list[TableRepairWarning]]:
    result = converter.convert(pdf_path, page_range=(start_page, end_page))
    return export_docling_result(
        result.document,
        pdf_path=pdf_path,
        repair_tables=repair_tables,
    )


def timed_docling_candidate(
    *,
    name: str,
    converter: DocumentConverter,
    pdf_path: Path,
    start_page: int,
    end_page: int,
    capture_vlm_usage: bool = False,
    repair_tables: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    if capture_vlm_usage:
        clear_vlm_usage_events()
    try:
        markdown, tables, table_text, table_warnings = convert_docling_range(
            converter,
            pdf_path,
            start_page,
            end_page,
            repair_tables=repair_tables,
        )
        error = None
    except Exception as exc:
        markdown = ""
        tables = []
        table_text = ""
        table_warnings = []
        error = str(exc)
    usage_events = get_vlm_usage_events() if capture_vlm_usage else []
    return {
        "name": name,
        "markdown": markdown,
        "tables": tables,
        "table_text": table_text,
        "table_warnings": table_warnings,
        "usage_events": usage_events,
        "error": error,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def build_embedded_visual_crop_pdf(
    *,
    pdf_path: Path,
    page_preflight: PagePreflight,
    output_dir: Path,
    scale: float,
    margin_points: float,
) -> tuple[Path, int, list[dict[str, Any]]]:
    regions = [
        region
        for region in page_preflight.embedded_visual_regions
        if isinstance(region.get("bbox"), list) and len(region.get("bbox") or []) == 4
    ]
    if not regions:
        return pdf_path, 1, []

    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise RuntimeError("pypdfium2 is required to crop embedded visual regions.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    render_scale = max(float(scale), 1.0)
    crop_path = output_dir / f"page_{page_preflight.page}_embedded_visual_crop.pdf"
    document = pdfium.PdfDocument(str(pdf_path))
    crops: list[Any] = []
    crop_metadata: list[dict[str, Any]] = []
    try:
        page = document[page_preflight.page - 1]
        try:
            page_width, page_height = (float(value) for value in page.get_size())
            bitmap = page.render(scale=render_scale)
            try:
                image = bitmap.to_pil().convert("RGB")
            finally:
                close_bitmap = getattr(bitmap, "close", None)
                if callable(close_bitmap):
                    close_bitmap()

            image_scale = image.width / max(page_width, 1.0)
            for crop_page, region in enumerate(regions, start=1):
                left, bottom, right, top = (float(value) for value in region["bbox"])
                expanded = (
                    max(left - margin_points, 0.0),
                    max(bottom - margin_points, 0.0),
                    min(right + margin_points, page_width),
                    min(top + margin_points, page_height),
                )
                exp_left, exp_bottom, exp_right, exp_top = expanded
                crop_box = (
                    max(int(exp_left * image_scale), 0),
                    max(int((page_height - exp_top) * image_scale), 0),
                    min(int(math.ceil(exp_right * image_scale)), image.width),
                    min(int(math.ceil((page_height - exp_bottom) * image_scale)), image.height),
                )
                if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                    continue
                crop = image.crop(crop_box).convert("RGB")
                if crop.width < 8 or crop.height < 8:
                    continue
                crops.append(crop)
                crop_metadata.append(
                    {
                        "crop_pdf_page": crop_page,
                        "source_page": page_preflight.page,
                        "region_index": region.get("region_index"),
                        "source_bbox": [round(value, 2) for value in (left, bottom, right, top)],
                        "expanded_bbox": [round(value, 2) for value in expanded],
                        "pixel_size": [int(crop.width), int(crop.height)],
                        "render_scale": render_scale,
                    }
                )
        finally:
            page.close()
    finally:
        document.close()

    if not crops:
        return pdf_path, 1, []

    first, *rest = crops
    first.save(
        crop_path,
        "PDF",
        save_all=bool(rest),
        append_images=rest,
        resolution=72 * render_scale,
    )
    return crop_path, len(crops), crop_metadata


def build_page_range_pdf(
    *,
    pdf_path: Path,
    start_page: int,
    end_page: int,
    output_dir: Path,
    label: str,
) -> Path:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise RuntimeError("pypdfium2 is required to extract page ranges.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    extracted_path = output_dir / f"{label}_pages_{start_page}_{end_page}.pdf"
    source = pdfium.PdfDocument(str(pdf_path))
    extracted = pdfium.PdfDocument.new()
    try:
        pages = str(start_page) if start_page == end_page else f"{start_page}-{end_page}"
        extracted.import_pages(source, pages=pages)
        extracted.save(str(extracted_path))
    finally:
        extracted.close()
        source.close()
    return extracted_path


def critical_values(text: str) -> dict[str, list[str]]:
    normalized = normalized_text(text)
    patterns = {
        "amount": r"(?:[¥￥]\s*)?\d{1,3}(?:,\s*\d{3})+(?:\.\s*\d+)?\s*(?:円|yen)?|\d+(?:\.\s*\d+)?\s*円",
        "date": r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|令和\d{1,2}年\d{1,2}月\d{1,2}日",
        "percent": r"\d+(?:\.\d+)?\s*%",
        "id": r"\b(?=[A-Z0-9_\-]*\d)[A-Z0-9][A-Z0-9_\-]{4,}\b",
    }
    found: dict[str, list[str]] = {}
    for key, pattern in patterns.items():
        values = []
        seen = set()
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            value = match.group(0).strip()
            compact = compact_number(value) if key == "amount" else compact_for_compare(value)
            if compact and compact not in seen:
                seen.add(compact)
                values.append(value)
        found[key] = values
    return found


def invalid_value_candidates(text: str) -> list[dict[str, str]]:
    invalid: list[dict[str, str]] = []
    normalized = normalized_text(text)
    date_patterns = [
        (
            r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})",
            "western_slash_date",
        ),
        (
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日",
            "western_japanese_date",
        ),
    ]
    for pattern, kind in date_patterns:
        for match in re.finditer(pattern, normalized):
            value = match.group(0)
            try:
                datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                invalid.append(
                    {
                        "field_type": "date",
                        "value": value,
                        "reason": f"invalid_{kind}",
                    }
                )

    for match in re.finditer(
        r"令和(?P<year>\d{1,2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日",
        normalized,
    ):
        value = match.group(0)
        western_year = 2018 + int(match.group("year"))
        try:
            datetime(
                western_year,
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            invalid.append(
                {
                    "field_type": "date",
                    "value": value,
                    "reason": "invalid_reiwa_date",
                }
            )

    for match in re.finditer(r"(?P<value>\d+(?:\.\d+)?)\s*%", normalized):
        value = match.group("value")
        if float(value) > 1000:
            invalid.append(
                {
                    "field_type": "percent",
                    "value": match.group(0),
                    "reason": "implausibly_large_percent",
                }
            )
    return invalid


def parse_markdown_tables(markdown: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                current.append(cells)
                continue
            current.append(cells)
        else:
            if len(current) >= 2:
                tables.append(current)
            current = []
    if len(current) >= 2:
        tables.append(current)
    return tables


def markdown_table_blocks(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    blocks: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].strip().startswith("|") and lines[index].strip().endswith("|"):
            index += 1
        end = index
        title: str | None = None
        title_index: int | None = None
        scan_index = start - 1
        while scan_index >= 0:
            candidate = lines[scan_index].strip()
            if not candidate:
                scan_index -= 1
                continue
            if candidate.startswith("<!--"):
                scan_index -= 1
                continue
            if not candidate.startswith("|") and len(candidate) <= 140:
                title = candidate.lstrip("#").strip()
                title_index = scan_index
            break
        blocks.append(
            {
                "title": title,
                "title_index": title_index,
                "start": start,
                "end": end,
                "table": "\n".join(lines[start:end]),
            }
        )
    return blocks


def signature_tokens_from_text(text: str) -> set[str]:
    tokens: set[str] = set()
    for part in re.split(r"[\s,|\n\r\t<>/]+", text):
        compact = compact_for_compare(part)
        if len(compact) >= 3:
            tokens.add(compact)
    return tokens


def content_is_duplicate_of_base(text: str, base_markdown: str) -> bool:
    tokens = signature_tokens_from_text(text)
    if len(tokens) < 8:
        return False
    base_compact = compact_for_compare(base_markdown)
    hits = sum(1 for token in tokens if token in base_compact)
    return hits >= 8 and hits / len(tokens) >= 0.55


def clean_append_markdown(markdown: str, base_markdown: str) -> str:
    blocks = markdown_table_blocks(markdown)
    if not blocks:
        lines = [
            line.strip()
            for line in markdown.splitlines()
            if line.strip() and not line.strip().startswith("### Embedded visual extraction")
        ]
        return "\n\n".join(lines)

    base_compact = compact_for_compare(base_markdown)
    output_parts: list[str] = []
    for block in blocks:
        if content_is_duplicate_of_base(str(block["table"]), base_markdown):
            continue
        title = str(block.get("title") or "").strip()
        table_text = str(block["table"]).strip()
        if title and compact_for_compare(title) not in base_compact:
            output_parts.append(title)
        output_parts.append(table_text)
    return "\n\n".join(part for part in output_parts if part.strip())


def filter_append_tables(tables: list[dict[str, Any]], base_markdown: str) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for table in tables:
        signature_text = "\n".join(
            str(table.get(key) or "") for key in ("csv", "html") if table.get(key)
        )
        if signature_text and content_is_duplicate_of_base(signature_text, base_markdown):
            continue
        filtered.append(table)
    return filtered


def is_separator_row(row: list[str]) -> bool:
    return bool(row) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in row
    )


def table_data_rows(table: list[list[str]]) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    for row_index, row in enumerate(table):
        if is_separator_row(row):
            continue
        if not any(compact_for_compare(cell) for cell in row):
            continue
        rows.append((row_index, row))
    return rows


def row_numeric_count(row: list[str]) -> int:
    return sum(1 for cell in row if is_numeric_like(cell))


def row_label_key(row: list[str]) -> str:
    return "|".join(compact_for_compare(cell) for cell in row[:3])


def row_label_similarity(left: list[str], right: list[str]) -> float:
    left_key = row_label_key(left)
    right_key = row_label_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    first_left = compact_for_compare(left[0]) if left else ""
    first_right = compact_for_compare(right[0]) if right else ""
    first_bonus = 0.15 if first_left and first_left == first_right else 0.0
    second_left = compact_for_compare(left[1]) if len(left) > 1 else ""
    second_right = compact_for_compare(right[1]) if len(right) > 1 else ""
    second_ratio = SequenceMatcher(None, second_left, second_right).ratio()
    full_ratio = SequenceMatcher(None, left_key, right_key).ratio()
    return clamp((0.65 * full_ratio) + (0.35 * second_ratio) + first_bonus)


def align_table_rows(
    vlm_table: list[list[str]],
    ocr_table: list[list[str]],
) -> list[tuple[int, list[str], int, list[str], float]]:
    ocr_rows = table_data_rows(ocr_table)
    used_ocr_indices: set[int] = set()
    aligned: list[tuple[int, list[str], int, list[str], float]] = []
    for vlm_row_index, vlm_row in table_data_rows(vlm_table):
        if row_numeric_count(vlm_row) < 2:
            continue
        best: tuple[int, list[str], float] | None = None
        for ocr_row_index, ocr_row in ocr_rows:
            if ocr_row_index in used_ocr_indices:
                continue
            if row_numeric_count(ocr_row) < 2:
                continue
            score = row_label_similarity(vlm_row, ocr_row)
            if best is None or score > best[2]:
                best = (ocr_row_index, ocr_row, score)
        if best is None or best[2] < 0.45:
            continue
        used_ocr_indices.add(best[0])
        aligned.append((vlm_row_index, vlm_row, best[0], best[1], best[2]))
    return aligned


def mask_markdown_table_cells(
    markdown: str,
    disagreements: list[tuple[int, int, int]],
) -> str:
    if not disagreements:
        return markdown
    by_table = {(table_index, row_index, col_index) for table_index, row_index, col_index in disagreements}
    table_index = -1
    row_index = -1
    output: list[str] = []
    in_table = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table:
                in_table = True
                table_index += 1
                row_index = -1
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            row_index += 1
            if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                for col_index in range(len(cells)):
                    if (table_index, row_index, col_index) in by_table:
                        cells[col_index] = UNKNOWN_TOKEN
                output.append("| " + " | ".join(cells) + " |")
            else:
                output.append(line)
        else:
            in_table = False
            output.append(line)
    return "\n".join(output)


def replace_values_with_unknown(markdown: str, values: list[str]) -> str:
    result = markdown
    for value in sorted(values, key=len, reverse=True):
        if not value.strip():
            continue
        result = result.replace(value, UNKNOWN_TOKEN)
    return result


def numeric_cell_count(tables: list[list[list[str]]]) -> int:
    return sum(
        row_numeric_count(row)
        for table in tables
        for _row_index, row in table_data_rows(table)
    )


def markdown_quality_metrics(markdown: str) -> dict[str, Any]:
    tables = parse_markdown_tables(markdown)
    table_column_counts = [
        max((len(row) for _row_index, row in table_data_rows(table)), default=0)
        for table in tables
    ]
    return {
        "chars": len(markdown.strip()),
        "table_count": len(tables),
        "numeric_cell_count": numeric_cell_count(tables),
        "unknown_token_count": markdown.count(UNKNOWN_TOKEN),
        "max_table_columns": max(table_column_counts, default=0),
        "table_column_counts": table_column_counts,
    }


def table_shape_conflicts(
    left_tables: list[list[list[str]]],
    right_tables: list[list[list[str]]],
    *,
    left_label: str,
    right_label: str,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for table_index in range(min(len(left_tables), len(right_tables))):
        aligned_rows = align_table_rows(left_tables[table_index], right_tables[table_index])
        mismatched_rows: list[dict[str, Any]] = []
        left_extra_numeric = 0
        right_extra_numeric = 0
        for left_row_index, left_row, right_row_index, right_row, score in aligned_rows:
            if len(left_row) == len(right_row):
                continue
            if len(left_row) > len(right_row):
                left_extra_numeric += row_numeric_count(left_row[len(right_row) :])
            else:
                right_extra_numeric += row_numeric_count(right_row[len(left_row) :])
            if len(mismatched_rows) < 5:
                mismatched_rows.append(
                    {
                        f"{left_label}_row_index": left_row_index,
                        f"{right_label}_row_index": right_row_index,
                        f"{left_label}_columns": len(left_row),
                        f"{right_label}_columns": len(right_row),
                        "alignment_score": round(score, 4),
                    }
                )
        if mismatched_rows:
            conflicts.append(
                {
                    "table_index": table_index,
                    "comparison_kind": "table_column_count_mismatch",
                    "mismatched_aligned_row_count": sum(
                        1
                        for _left_row_index, left_row, _right_row_index, right_row, _score
                        in aligned_rows
                        if len(left_row) != len(right_row)
                    ),
                    f"{left_label}_extra_numeric_cells": left_extra_numeric,
                    f"{right_label}_extra_numeric_cells": right_extra_numeric,
                    "examples": mismatched_rows,
                }
            )
    return conflicts


def reconcile_table_fallback_triggers(
    safe_markdown: str,
    warnings: list[WarningItem],
) -> list[str]:
    triggers: list[str] = []
    has_unknown = UNKNOWN_TOKEN in safe_markdown
    if has_unknown:
        triggers.append("unknown_token")

    structural_kinds = {
        "critical_value_single_source",
        "missing_primary_table",
        "missing_secondary_table",
        "table_column_count_mismatch",
        "unaligned_numeric_rows",
    }
    fallback_codes = {
        "CRITICAL_VALUE_DISAGREEMENT",
        "MASKED_AS_UNKNOWN",
        "OCR_TABLE_LAYOUT_UNRELIABLE",
        "TABLE_CELL_DISAGREEMENT",
        "VALUE_VALIDATION_FAILED",
        "VLM_CANDIDATE_LAYOUT_UNRELIABLE",
        "VLM_TABLE_STRUCTURE_DISAGREEMENT",
        "OCR_VLM_TABLE_STRUCTURE_DISAGREEMENT",
        "CANDIDATE_CONVERSION_FAILED",
    }
    structural_codes = {
        "VLM_TABLE_STRUCTURE_DISAGREEMENT",
        "OCR_VLM_TABLE_STRUCTURE_DISAGREEMENT",
    }
    for warning in warnings:
        chosen_source = str(warning.evidence.get("chosen_source") or "")
        structure_already_resolved = (
            not has_unknown
            and chosen_source in {"primary", "secondary", "vlm", "ocr"}
        )
        if warning.code in structural_codes and structure_already_resolved:
            continue
        if warning.code in fallback_codes:
            triggers.append(warning.code)
        comparison_kind = str(warning.evidence.get("comparison_kind") or "")
        if comparison_kind in structural_kinds and not structure_already_resolved:
            triggers.append(comparison_kind)
    return sorted(set(triggers))


def should_accept_reconcile_table_fallback(
    current_markdown: str,
    fallback_markdown: str,
    *,
    triggers: list[str],
) -> tuple[bool, str, dict[str, Any]]:
    current_metrics = markdown_quality_metrics(current_markdown)
    fallback_metrics = markdown_quality_metrics(fallback_markdown)
    metrics = {
        "current": current_metrics,
        "fallback": fallback_metrics,
        "triggers": triggers,
    }
    if not fallback_markdown.strip():
        return False, "empty_fallback_output", metrics
    if fallback_metrics["table_count"] == 0 and current_metrics["table_count"] > 0:
        return False, "fallback_has_no_table", metrics

    current_unknown = int(current_metrics["unknown_token_count"])
    fallback_unknown = int(fallback_metrics["unknown_token_count"])
    current_numeric = int(current_metrics["numeric_cell_count"])
    fallback_numeric = int(fallback_metrics["numeric_cell_count"])
    current_max_columns = int(current_metrics["max_table_columns"])
    fallback_max_columns = int(fallback_metrics["max_table_columns"])

    if fallback_unknown:
        if fallback_numeric == 0 and fallback_unknown >= 3:
            return False, "fallback_unknown_without_numeric_values", metrics
        if fallback_unknown > current_unknown and fallback_numeric <= current_numeric:
            return False, "fallback_unknown_count_increased", metrics
        if fallback_unknown >= max(10, fallback_numeric):
            return False, "fallback_too_many_unknown_tokens", metrics

    if current_unknown and fallback_unknown < current_unknown:
        if fallback_numeric >= max(0, current_numeric - current_unknown):
            return True, "unknown_count_reduced", metrics

    structural_trigger = any(
        trigger
        in {
            "critical_value_single_source",
            "missing_primary_table",
            "missing_secondary_table",
            "table_column_count_mismatch",
            "unaligned_numeric_rows",
            "VLM_TABLE_STRUCTURE_DISAGREEMENT",
            "OCR_VLM_TABLE_STRUCTURE_DISAGREEMENT",
            "VLM_CANDIDATE_LAYOUT_UNRELIABLE",
            "OCR_TABLE_LAYOUT_UNRELIABLE",
        }
        for trigger in triggers
    )
    if structural_trigger:
        numeric_gain_threshold = max(2, int(max(current_numeric, 1) * 0.05))
        if fallback_numeric >= current_numeric + numeric_gain_threshold:
            return True, "numeric_cells_increased", metrics
        if fallback_max_columns > current_max_columns and fallback_numeric >= current_numeric:
            return True, "table_columns_increased", metrics

    if (
        current_metrics["table_count"] == 0
        and fallback_metrics["table_count"] > 0
        and (fallback_unknown == 0 or fallback_numeric > 0)
    ):
        return True, "table_recovered", metrics
    return False, "fallback_not_better", metrics


def comparable_value_key(value: str, field_type: str) -> str:
    return compact_number(value) if field_type == "amount" else compact_for_compare(value)


def is_synthetic_coordinate_label(value: str) -> bool:
    return bool(re.fullmatch(r"col_?\d{1,4}", compact_for_compare(value)))


def critical_value_set(values: list[str], field_type: str) -> set[str]:
    return {
        comparable_value_key(value, field_type)
        for value in values
        if comparable_value_key(value, field_type)
    }


def critical_values_not_in(
    source_values: list[str],
    target_values: list[str],
    *,
    field_type: str,
) -> list[str]:
    target_set = critical_value_set(target_values, field_type)
    missing: list[str] = []
    seen: set[str] = set()
    for value in source_values:
        if field_type == "id" and is_synthetic_coordinate_label(value):
            continue
        key = comparable_value_key(value, field_type)
        if not key or key in target_set or key in seen:
            continue
        seen.add(key)
        missing.append(value)
    return missing


def coordinate_evidence_strength(coordinate_quality: dict[str, Any]) -> str:
    reasons = {str(reason) for reason in coordinate_quality.get("reasons") or []}
    span_coverage = float(coordinate_quality.get("span_coverage") or 0.0)
    blocking = {
        "row_collapse_risk",
        "giant_cell_risk",
        "prose_row_risk",
        "too_few_rows",
        "too_few_columns",
    }
    if coordinate_quality.get("ok"):
        return "strong"
    if any(reason.startswith("coordinate_status_") for reason in reasons):
        return "weak"
    if reasons & blocking:
        return "weak"
    if span_coverage >= 0.95:
        return "medium"
    return "weak"


def coordinate_vlm_quality_report(
    *,
    coordinate_markdown: str,
    vlm_markdown: str,
    coordinate_quality: dict[str, Any],
    coordinate_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    coordinate_values = critical_values(coordinate_markdown)
    vlm_values = critical_values(vlm_markdown)
    vlm_only: dict[str, list[str]] = {}
    coordinate_only: dict[str, list[str]] = {}
    for field_type in sorted(set(coordinate_values) | set(vlm_values)):
        unsupported = critical_values_not_in(
            vlm_values.get(field_type, []),
            coordinate_values.get(field_type, []),
            field_type=field_type,
        )
        missing = critical_values_not_in(
            coordinate_values.get(field_type, []),
            vlm_values.get(field_type, []),
            field_type=field_type,
        )
        if unsupported:
            vlm_only[field_type] = unsupported
        if missing:
            coordinate_only[field_type] = missing

    coordinate_metrics = markdown_quality_metrics(coordinate_markdown)
    vlm_metrics = markdown_quality_metrics(vlm_markdown)
    coordinate_numeric = int(coordinate_metrics["numeric_cell_count"])
    vlm_numeric = int(vlm_metrics["numeric_cell_count"])
    coordinate_columns = int(coordinate_metrics["max_table_columns"])
    vlm_columns = int(vlm_metrics["max_table_columns"])
    coordinate_tables = int(coordinate_metrics["table_count"])
    vlm_tables = int(vlm_metrics["table_count"])
    numeric_delta = vlm_numeric - coordinate_numeric
    structure_mismatches: list[str] = []
    if coordinate_tables and vlm_tables and coordinate_tables != vlm_tables:
        structure_mismatches.append("table_count_mismatch")
    if coordinate_columns and vlm_columns and abs(vlm_columns - coordinate_columns) >= 2:
        structure_mismatches.append("column_count_mismatch")
    if coordinate_numeric and abs(numeric_delta) >= max(3, int(coordinate_numeric * 0.10)):
        structure_mismatches.append("numeric_cell_count_mismatch")

    unsupported_count = sum(len(values) for values in vlm_only.values())
    missing_count = sum(len(values) for values in coordinate_only.values())
    unknown_count = int(vlm_metrics["unknown_token_count"])
    evidence_strength = coordinate_evidence_strength(coordinate_quality)
    numeric_evidence_weak = coordinate_numeric < 3 and vlm_numeric >= 3
    coordinate_absence_is_evidence = (
        evidence_strength in {"strong", "medium"} and not numeric_evidence_weak
    )
    coordinate_presence_is_evidence = (
        evidence_strength in {"strong", "medium"} and not numeric_evidence_weak
    )
    actionable_unsupported_count = unsupported_count if coordinate_absence_is_evidence else 0
    actionable_missing_count = missing_count if coordinate_presence_is_evidence else 0
    actionable_structure_mismatch_count = (
        len(structure_mismatches)
        if evidence_strength in {"strong", "medium"} and not numeric_evidence_weak
        else 0
    )
    quality_score = (
        actionable_unsupported_count * 3.0
        + actionable_missing_count * 2.0
        + actionable_structure_mismatch_count * 4.0
        + unknown_count * 3.0
    )
    needs_fallback = bool(
        actionable_unsupported_count
        or actionable_missing_count
        or actionable_structure_mismatch_count
        or unknown_count
    )
    return {
        "needs_fallback": needs_fallback,
        "quality_score": round(quality_score, 4),
        "coordinate_evidence_strength": evidence_strength,
        "coordinate_numeric_evidence_weak": numeric_evidence_weak,
        "coordinate_absence_is_evidence": coordinate_absence_is_evidence,
        "coordinate_presence_is_evidence": coordinate_presence_is_evidence,
        "vlm_only_values": vlm_only,
        "coordinate_only_values": coordinate_only,
        "unsupported_value_count": unsupported_count,
        "missing_value_count": missing_count,
        "actionable_unsupported_value_count": actionable_unsupported_count,
        "actionable_missing_value_count": actionable_missing_count,
        "actionable_structure_mismatch_count": actionable_structure_mismatch_count,
        "structure_mismatches": structure_mismatches,
        "coordinate_metrics": coordinate_metrics,
        "vlm_metrics": vlm_metrics,
        "coordinate_quality": coordinate_quality,
        "coordinate_diagnostics_summary": {
            "status": coordinate_diagnostics.get("status"),
            "rows": coordinate_diagnostics.get("trimmed_rows") or coordinate_diagnostics.get("rows"),
            "columns": coordinate_diagnostics.get("trimmed_columns")
            or coordinate_diagnostics.get("columns"),
            "span_count": coordinate_diagnostics.get("span_count"),
            "span_coverage": coordinate_diagnostics.get("span_coverage"),
            "table_area_ratio": coordinate_diagnostics.get("table_area_ratio"),
        },
    }


def coordinate_vlm_quality_warnings(
    *,
    page: int,
    mode: RoutingMode,
    report: dict[str, Any],
    model: str,
    source: str = "initial",
) -> list[WarningItem]:
    warnings: list[WarningItem] = []
    evidence_strength = str(report.get("coordinate_evidence_strength") or "weak")
    if evidence_strength == "weak" and (
        report.get("vlm_only_values")
        or report.get("coordinate_only_values")
        or report.get("structure_mismatches")
    ):
        warnings.append(
            WarningItem(
                page=page,
                mode=mode,
                level="info",
                code="VLM_COORD_WEAK_EVIDENCE",
                score=0.45,
                message="Coordinate extraction was too weak to validate VLM values authoritatively.",
                suggested_action="Use VLM output as primary for this page, and review manually if values are business-critical.",
                evidence={
                    "source": source,
                    "model": model,
                    "coordinate_evidence_strength": evidence_strength,
                    "vlm_only_values": report.get("vlm_only_values"),
                    "coordinate_only_values": report.get("coordinate_only_values"),
                    "structure_mismatches": report.get("structure_mismatches"),
                    "coordinate_quality": report.get("coordinate_quality"),
                    "coordinate_metrics": report.get("coordinate_metrics"),
                    "vlm_metrics": report.get("vlm_metrics"),
                },
            )
        )
        return warnings

    if report.get("actionable_unsupported_value_count"):
        warnings.append(
            WarningItem(
                page=page,
                mode=mode,
                level="needs_retry",
                code="VLM_UNSUPPORTED_COORD_VALUE",
                score=0.90,
                message="VLM output contains critical values that are not supported by coordinate extraction.",
                suggested_action="Fallback this table crop/page or review the source PDF for possible hallucinated values.",
                evidence={
                    "source": source,
                    "model": model,
                    "vlm_only_values": report.get("vlm_only_values"),
                    "unsupported_value_count": report.get("unsupported_value_count"),
                    "coordinate_metrics": report.get("coordinate_metrics"),
                    "vlm_metrics": report.get("vlm_metrics"),
                },
            )
        )
    if report.get("actionable_missing_value_count"):
        warnings.append(
            WarningItem(
                page=page,
                mode=mode,
                level="needs_retry",
                code="VLM_MISSING_COORD_VALUE",
                score=0.82,
                message="Coordinate extraction contains critical values that are missing from VLM output.",
                suggested_action="Fallback this table crop/page or review the VLM output for omitted rows or columns.",
                evidence={
                    "source": source,
                    "model": model,
                    "coordinate_only_values": report.get("coordinate_only_values"),
                    "missing_value_count": report.get("missing_value_count"),
                    "coordinate_metrics": report.get("coordinate_metrics"),
                    "vlm_metrics": report.get("vlm_metrics"),
                },
            )
        )
    if report.get("actionable_structure_mismatch_count"):
        warnings.append(
            WarningItem(
                page=page,
                mode=mode,
                level="needs_retry",
                code="VLM_COORD_TABLE_STRUCTURE_MISMATCH",
                score=0.84,
                message="VLM table structure differs from coordinate extraction.",
                suggested_action="Fallback this table crop/page and review row/column count differences.",
                evidence={
                    "source": source,
                    "model": model,
                    "structure_mismatches": report.get("structure_mismatches"),
                    "coordinate_metrics": report.get("coordinate_metrics"),
                    "vlm_metrics": report.get("vlm_metrics"),
                },
            )
        )
    return warnings


def mask_vlm_only_values(markdown: str, report: dict[str, Any]) -> tuple[str, int]:
    if not report.get("coordinate_absence_is_evidence"):
        return markdown, 0
    values: list[str] = []
    for field_values in (report.get("vlm_only_values") or {}).values():
        values.extend(str(value) for value in field_values)
    if not values:
        return markdown, 0
    return replace_values_with_unknown(markdown, values), len(values)


def coordinate_auto_correct_allowed(coordinate_quality: dict[str, Any]) -> bool:
    blocking_reasons = {
        "row_collapse_risk",
        "giant_cell_risk",
        "prose_row_risk",
        "too_few_rows",
        "too_few_columns",
    }
    reasons = {str(reason) for reason in coordinate_quality.get("reasons") or []}
    if any(reason.startswith("coordinate_status_") for reason in reasons):
        return False
    return not bool(reasons & blocking_reasons)


def auto_correct_vlm_cells_from_coordinate(
    *,
    vlm_markdown: str,
    coordinate_markdown: str,
    coordinate_quality: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    if not coordinate_auto_correct_allowed(coordinate_quality):
        return vlm_markdown, []
    vlm_tables = parse_markdown_tables(vlm_markdown)
    coordinate_tables = parse_markdown_tables(coordinate_markdown)
    if not vlm_tables or not coordinate_tables:
        return vlm_markdown, []

    coordinate_values = critical_values(coordinate_markdown).get("amount", [])
    coordinate_counts = Counter(comparable_value_key(value, "amount") for value in coordinate_values)
    corrections: list[dict[str, Any]] = []
    disagreements: list[tuple[int, int, int]] = []
    replacement_by_cell: dict[tuple[int, int, int], str] = {}
    for table_index, vlm_table in enumerate(vlm_tables):
        if table_index >= len(coordinate_tables):
            continue
        aligned_rows = align_table_rows(vlm_table, coordinate_tables[table_index])
        for row_index, vlm_row, _coord_row_index, coord_row, score in aligned_rows:
            if len(vlm_row) != len(coord_row):
                continue
            if score < 0.80:
                continue
            for col_index, vlm_cell in enumerate(vlm_row):
                coord_cell = coord_row[col_index]
                if not (is_numeric_like(vlm_cell) and is_numeric_like(coord_cell)):
                    continue
                if numeric_values_equal(vlm_cell, coord_cell):
                    continue
                coord_key = comparable_value_key(coord_cell, "amount")
                if not coord_key or coordinate_counts[coord_key] != 1:
                    continue
                disagreements.append((table_index, row_index, col_index))
                replacement_by_cell[(table_index, row_index, col_index)] = coord_cell
                if len(corrections) < 50:
                    corrections.append(
                        {
                            "table_index": table_index,
                            "row_index": row_index,
                            "column_index": col_index,
                            "vlm_value": vlm_cell,
                            "coordinate_value": coord_cell,
                            "alignment_score": round(score, 4),
                        }
                    )
    if not disagreements:
        return vlm_markdown, []

    table_index = -1
    row_index = -1
    output: list[str] = []
    in_table = False
    for line in vlm_markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table:
                in_table = True
                table_index += 1
                row_index = -1
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            row_index += 1
            if not is_separator_row(cells):
                for col_index in range(len(cells)):
                    replacement = replacement_by_cell.get((table_index, row_index, col_index))
                    if replacement is not None:
                        cells[col_index] = replacement
                output.append("| " + " | ".join(cells) + " |")
            else:
                output.append(line)
        else:
            in_table = False
            output.append(line)
    return "\n".join(output), corrections


def should_accept_coordinate_vlm_fallback(
    current_report: dict[str, Any],
    fallback_report: dict[str, Any],
) -> tuple[bool, str]:
    current_score = float(current_report.get("quality_score") or 0.0)
    fallback_score = float(fallback_report.get("quality_score") or 0.0)
    if fallback_score < current_score:
        return True, "coordinate_vlm_quality_score_improved"
    current_unknown = int((current_report.get("vlm_metrics") or {}).get("unknown_token_count") or 0)
    fallback_unknown = int((fallback_report.get("vlm_metrics") or {}).get("unknown_token_count") or 0)
    current_numeric = int((current_report.get("vlm_metrics") or {}).get("numeric_cell_count") or 0)
    fallback_numeric = int((fallback_report.get("vlm_metrics") or {}).get("numeric_cell_count") or 0)
    if fallback_unknown:
        if fallback_numeric == 0 and fallback_unknown >= 3:
            return False, "coordinate_vlm_fallback_unknown_without_numeric_values"
        if fallback_unknown > current_unknown and fallback_numeric <= current_numeric:
            return False, "coordinate_vlm_fallback_unknown_count_increased"
        if fallback_unknown >= max(10, fallback_numeric):
            return False, "coordinate_vlm_fallback_too_many_unknown_tokens"
    if current_unknown and fallback_unknown < current_unknown:
        return True, "unknown_count_reduced"
    current_unsupported = int(current_report.get("actionable_unsupported_value_count") or 0)
    fallback_unsupported = int(fallback_report.get("actionable_unsupported_value_count") or 0)
    current_missing = int(current_report.get("actionable_missing_value_count") or 0)
    fallback_missing = int(fallback_report.get("actionable_missing_value_count") or 0)
    if fallback_unsupported < current_unsupported and fallback_missing <= current_missing:
        return True, "unsupported_values_reduced"
    return False, "coordinate_vlm_fallback_not_better"


def apply_vlm_coordinate_quality_check(
    *,
    page: int,
    mode: RoutingMode,
    safe_markdown: str,
    tables: list[dict[str, Any]],
    coordinate_markdown: str,
    coordinate_quality: dict[str, Any] | None,
    coordinate_diagnostics: dict[str, Any] | None,
    model: str,
    source: str,
    enable_quality_check: bool,
    enable_auto_correct: bool,
    fallback_converter: DocumentConverter | None,
    fallback_model: str,
    fallback_pdf_path: Path,
    fallback_start_page: int,
    fallback_end_page: int,
) -> tuple[
    str,
    list[dict[str, Any]],
    list[WarningItem],
    dict[str, Any],
    dict[str, float],
]:
    warnings: list[WarningItem] = []
    timings: dict[str, float] = {}
    diagnostics: dict[str, Any] = {
        "enabled": enable_quality_check,
        "applicable": bool(coordinate_markdown.strip() and coordinate_quality and coordinate_diagnostics),
        "source": source,
    }
    if not enable_quality_check:
        diagnostics["reason"] = "disabled"
        return safe_markdown, tables, warnings, diagnostics, timings
    if not diagnostics["applicable"]:
        diagnostics["reason"] = str(
            (coordinate_diagnostics or {}).get("reason") or "no_coordinate_evidence"
        )
        return safe_markdown, tables, warnings, diagnostics, timings

    resolved_coordinate_quality = coordinate_quality or {}
    resolved_coordinate_diagnostics = coordinate_diagnostics or {}
    if enable_auto_correct:
        corrected_markdown, corrections = auto_correct_vlm_cells_from_coordinate(
            vlm_markdown=safe_markdown,
            coordinate_markdown=coordinate_markdown,
            coordinate_quality=resolved_coordinate_quality,
        )
        if corrections:
            safe_markdown = corrected_markdown
            warnings.append(
                WarningItem(
                    page=page,
                    mode=mode,
                    level="info",
                    code="VLM_COORD_AUTO_CORRECTED_CELL",
                    score=0.70,
                    message="VLM table cells were automatically corrected using unique coordinate values.",
                    suggested_action="Review corrected cells if the source table is business-critical.",
                    evidence={
                        "source": source,
                        "correction_count": len(corrections),
                        "corrections": corrections,
                        "model": model,
                    },
                )
            )

    current_report = coordinate_vlm_quality_report(
        coordinate_markdown=coordinate_markdown,
        vlm_markdown=safe_markdown,
        coordinate_quality=resolved_coordinate_quality,
        coordinate_diagnostics=resolved_coordinate_diagnostics,
    )
    warnings.extend(
        coordinate_vlm_quality_warnings(
            page=page,
            mode=mode,
            report=current_report,
            model=model,
            source=source,
        )
    )
    diagnostics["initial_report"] = current_report
    final_report = current_report

    fallback_attempted = False
    if current_report.get("needs_fallback") and fallback_converter is not None:
        fallback_attempted = True
        fallback_result = timed_docling_candidate(
            name="coordinate_vlm_fallback",
            converter=fallback_converter,
            pdf_path=fallback_pdf_path,
            start_page=fallback_start_page,
            end_page=fallback_end_page,
            capture_vlm_usage=True,
        )
        timing_key = (
            f"coordinate_vlm_fallback:{fallback_model}"
            if fallback_model
            else "coordinate_vlm_fallback"
        )
        timings[timing_key] = fallback_result["elapsed_seconds"]
        fallback_safe_markdown = str(fallback_result["markdown"] or "")
        if enable_auto_correct:
            fallback_safe_markdown, fallback_corrections = auto_correct_vlm_cells_from_coordinate(
                vlm_markdown=fallback_safe_markdown,
                coordinate_markdown=coordinate_markdown,
                coordinate_quality=resolved_coordinate_quality,
            )
        else:
            fallback_corrections = []
        fallback_report = coordinate_vlm_quality_report(
            coordinate_markdown=coordinate_markdown,
            vlm_markdown=fallback_safe_markdown,
            coordinate_quality=resolved_coordinate_quality,
            coordinate_diagnostics=resolved_coordinate_diagnostics,
        )
        accept_fallback, accept_reason = should_accept_coordinate_vlm_fallback(
            current_report,
            fallback_report,
        )
        diagnostics["fallback"] = {
            "attempted": True,
            "accepted": accept_fallback,
            "reason": accept_reason,
            "model": fallback_model,
            "elapsed_seconds": fallback_result["elapsed_seconds"],
            "report": fallback_report,
            "auto_corrections": fallback_corrections,
            "usage_events": fallback_result["usage_events"],
        }
        if accept_fallback:
            safe_markdown = fallback_safe_markdown
            if fallback_result["tables"]:
                tables = fallback_result["tables"]
            final_report = fallback_report
            warnings.append(
                WarningItem(
                    page=page,
                    mode=mode,
                    level="info",
                    code="VLM_COORD_FALLBACK_APPLIED",
                    score=0.76,
                    message="VLM output was replaced by coordinate-validated fallback output.",
                    suggested_action="Review the coordinate/VLM quality report if critical values remain uncertain.",
                    evidence={
                        "source": source,
                        "model": fallback_model,
                        "reason": accept_reason,
                        "initial_report": current_report,
                        "fallback_report": fallback_report,
                    },
                )
            )
        else:
            warnings.append(
                WarningItem(
                    page=page,
                    mode=mode,
                    level="info",
                    code="VLM_COORD_FALLBACK_SKIPPED",
                    score=0.45,
                    message="Coordinate-validated fallback was run but was not better than the initial VLM output.",
                    suggested_action="Review the coordinate/VLM quality report before trusting unsupported values.",
                    evidence={
                        "source": source,
                        "model": fallback_model,
                        "reason": accept_reason,
                        "initial_report": current_report,
                        "fallback_report": fallback_report,
                    },
                )
            )

    if not fallback_attempted:
        diagnostics["fallback"] = {
            "attempted": False,
            "enabled": fallback_converter is not None,
            "reason": "no_coordinate_vlm_fallback_trigger",
        }

    masked_markdown, masked_count = mask_vlm_only_values(safe_markdown, final_report)
    if masked_count:
        safe_markdown = masked_markdown
        warnings.append(
            WarningItem(
                page=page,
                mode=mode,
                level="warning",
                code="VLM_COORD_MASKED_UNSUPPORTED_VALUE",
                score=0.86,
                message="VLM-only critical values were masked because coordinate extraction did not support them.",
                suggested_action="Review the source PDF or rerun the target page with higher resolution.",
                evidence={
                    "source": source,
                    "masked_value_count": masked_count,
                    "final_report": final_report,
                },
            )
        )
        warnings.append(
            WarningItem(
                page=page,
                mode=mode,
                level="warning",
                code="MASKED_AS_UNKNOWN",
                score=0.80,
                message="Unsupported VLM values were replaced with the unknown token in safe output.",
                suggested_action="Review the coordinate/VLM quality report and source PDF.",
                evidence={
                    "source": source,
                    "masked_value_count": masked_count,
                    "reason": "vlm_coordinate_unsupported_values",
                },
            )
        )
    diagnostics["final_report"] = coordinate_vlm_quality_report(
        coordinate_markdown=coordinate_markdown,
        vlm_markdown=safe_markdown,
        coordinate_quality=resolved_coordinate_quality,
        coordinate_diagnostics=resolved_coordinate_diagnostics,
    )
    return safe_markdown, tables, warnings, diagnostics, timings


def reconcile_vlm_vlm(
    *,
    page: int,
    preflight: PagePreflight,
    primary_markdown: str,
    secondary_markdown: str,
    primary_usage: dict[str, Any] | None,
    secondary_usage: dict[str, Any] | None,
    primary_model: str,
    secondary_model: str,
) -> tuple[str, list[WarningItem], str]:
    warnings: list[WarningItem] = []
    safe_markdown = primary_markdown.strip() or secondary_markdown
    chosen_source = "primary"

    for source_name, model, markdown, usage in [
        ("primary", primary_model, primary_markdown, primary_usage),
        ("secondary", secondary_model, secondary_markdown, secondary_usage),
    ]:
        finish_reason = str((usage or {}).get("finish_reason") or "")
        if finish_reason in {"length", "content_filter"}:
            warnings.append(
                WarningItem(
                    page=page,
                    mode="IMAGE_RECONCILE",
                    level="needs_retry",
                    code="VLM_TRUNCATED_OUTPUT",
                    score=0.95,
                    message=f"{source_name} VLM output may be truncated.",
                    suggested_action="Narrow the page range or raise the token limit and rerun.",
                    evidence={
                        "source": source_name,
                        "model": model,
                        "finish_reason": finish_reason,
                    },
                )
            )

        if preflight.image_read_risk_score >= 0.50 and len(markdown.strip()) < 80:
            warnings.append(
                WarningItem(
                    page=page,
                    mode="IMAGE_RECONCILE",
                    level="warning",
                    code="VLM_LOW_DETAIL_OUTPUT",
                    score=0.70,
                    message=f"{source_name} VLM output is short for a dense image page.",
                    suggested_action="Rerun the target page with a higher image scale or a narrower page range.",
                    evidence={
                        "source": source_name,
                        "model": model,
                        "image_read_risk_score": preflight.image_read_risk_score,
                        "vlm_extracted_chars": len(markdown.strip()),
                    },
                )
            )

    primary_values = critical_values(primary_markdown)
    secondary_values = critical_values(secondary_markdown)
    values_to_mask: list[str] = []
    for field_type in sorted(set(primary_values) | set(secondary_values)):
        primary_list = primary_values.get(field_type, [])
        secondary_list = secondary_values.get(field_type, [])
        if field_type == "amount":
            primary_set = {compact_number(value) for value in primary_list}
            secondary_set = {compact_number(value) for value in secondary_list}
        else:
            primary_set = {compact_for_compare(value) for value in primary_list}
            secondary_set = {compact_for_compare(value) for value in secondary_list}
        if primary_set == secondary_set:
            continue
        if not primary_set or not secondary_set:
            warnings.append(
                WarningItem(
                    page=page,
                    mode="IMAGE_RECONCILE",
                    level="warning",
                    code="VLM_VLM_DISAGREEMENT",
                    score=0.70,
                    message=f"{field_type} appears in only one VLM candidate.",
                    suggested_action="Review both raw VLM candidates for the target page.",
                    evidence={
                        "comparison_kind": "critical_value_single_source",
                        "field_type": field_type,
                        "primary_model": primary_model,
                        "secondary_model": secondary_model,
                        "primary_values": primary_list,
                        "secondary_values": secondary_list,
                    },
                )
            )
            continue
        differing_primary_values = [
            value
            for value in primary_list
            if (compact_number(value) if field_type == "amount" else compact_for_compare(value))
            not in secondary_set
        ]
        differing_secondary_values = [
            value
            for value in secondary_list
            if (compact_number(value) if field_type == "amount" else compact_for_compare(value))
            not in primary_set
        ]
        values_to_mask.extend(differing_primary_values)
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="VLM_VLM_DISAGREEMENT",
                score=0.90,
                message=f"{field_type} differs between VLM candidates.",
                suggested_action="Review both raw VLM candidates and rerun the target page if needed.",
                evidence={
                    "comparison_kind": "critical_value_conflict",
                    "field_type": field_type,
                    "primary_model": primary_model,
                    "secondary_model": secondary_model,
                    "primary_values": differing_primary_values,
                    "secondary_values": differing_secondary_values,
                },
            )
        )

    primary_tables = parse_markdown_tables(primary_markdown)
    secondary_tables = parse_markdown_tables(secondary_markdown)
    cell_disagreements: list[tuple[int, int, int]] = []
    numeric_cell_comparisons = 0
    text_cell_disagreement_count = 0
    unaligned_numeric_rows = 0
    missing_secondary_tables = max(0, len(primary_tables) - len(secondary_tables))
    missing_primary_tables = max(0, len(secondary_tables) - len(primary_tables))

    for table_index, primary_table in enumerate(primary_tables):
        if table_index >= len(secondary_tables):
            continue
        aligned_rows = align_table_rows(primary_table, secondary_tables[table_index])
        aligned_primary_row_indices = {row[0] for row in aligned_rows}
        unaligned_numeric_rows += sum(
            1
            for row_index, row in table_data_rows(primary_table)
            if row_numeric_count(row) >= 2 and row_index not in aligned_primary_row_indices
        )
        for row_index, primary_row, _secondary_row_index, secondary_row, _score in aligned_rows:
            if len(primary_row) != len(secondary_row):
                continue
            for col_index, primary_cell in enumerate(primary_row):
                secondary_cell = secondary_row[col_index]
                primary_compact = compact_for_compare(primary_cell)
                secondary_compact = compact_for_compare(secondary_cell)
                if not primary_compact and not secondary_compact:
                    continue
                primary_numeric = is_numeric_like(primary_cell)
                secondary_numeric = is_numeric_like(secondary_cell)
                if primary_numeric or secondary_numeric:
                    numeric_cell_comparisons += 1
                    if not (
                        primary_numeric
                        and secondary_numeric
                        and numeric_values_equal(primary_cell, secondary_cell)
                    ):
                        cell_disagreements.append((table_index, row_index, col_index))
                    continue
                if primary_compact != secondary_compact:
                    text_cell_disagreement_count += 1

    primary_numeric_cells = numeric_cell_count(primary_tables)
    secondary_numeric_cells = numeric_cell_count(secondary_tables)
    shape_conflicts = table_shape_conflicts(
        primary_tables,
        secondary_tables,
        left_label="primary",
        right_label="secondary",
    )
    disagreement_ratio = (
        len(cell_disagreements) / numeric_cell_comparisons
        if numeric_cell_comparisons
        else 0.0
    )
    layout_unreliable_source: str | None = None
    if len(cell_disagreements) >= 10 and disagreement_ratio >= 0.35:
        if primary_numeric_cells >= max(10.0, secondary_numeric_cells * 1.5):
            layout_unreliable_source = "secondary"
        elif secondary_numeric_cells >= max(10.0, primary_numeric_cells * 1.5):
            layout_unreliable_source = "primary"
            chosen_source = "secondary"
            safe_markdown = secondary_markdown.strip() or primary_markdown

    if layout_unreliable_source:
        layout_cell_disagreement_count = len(cell_disagreements)
        warnings = [
            warning
            for warning in warnings
            if warning.evidence.get("comparison_kind") != "critical_value_conflict"
        ]
        values_to_mask = []
        cell_disagreements = []
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="VLM_CANDIDATE_LAYOUT_UNRELIABLE",
                score=round(clamp(disagreement_ratio), 4),
                message=f"{layout_unreliable_source} VLM table layout appears unreliable.",
                suggested_action="Review both raw VLM candidates and rerun the target page if needed.",
                evidence={
                    "unreliable_source": layout_unreliable_source,
                    "chosen_source": chosen_source,
                    "primary_model": primary_model,
                    "secondary_model": secondary_model,
                    "primary_numeric_cells": primary_numeric_cells,
                    "secondary_numeric_cells": secondary_numeric_cells,
                    "cell_disagreement_count": layout_cell_disagreement_count,
                    "numeric_cell_comparisons": numeric_cell_comparisons,
                    "disagreement_ratio": round(disagreement_ratio, 4),
                },
            )
        )

    if shape_conflicts:
        if secondary_numeric_cells >= primary_numeric_cells + max(2, int(max(primary_numeric_cells, 1) * 0.05)):
            chosen_source = "secondary"
            safe_markdown = secondary_markdown.strip() or primary_markdown
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="VLM_TABLE_STRUCTURE_DISAGREEMENT",
                score=0.86,
                message="VLM candidates disagree on table structure.",
                suggested_action="Use local table fallback or review the candidate with more complete columns.",
                evidence={
                    "comparison_kind": "table_column_count_mismatch",
                    "primary_model": primary_model,
                    "secondary_model": secondary_model,
                    "primary_numeric_cells": primary_numeric_cells,
                    "secondary_numeric_cells": secondary_numeric_cells,
                    "chosen_source": chosen_source,
                    "shape_conflicts": shape_conflicts,
                },
            )
        )

    if missing_secondary_tables:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="VLM_VLM_DISAGREEMENT",
                score=0.65,
                message="One or more primary VLM tables were not found in the secondary candidate.",
                suggested_action="Review both raw VLM candidates for missing tables.",
                evidence={
                    "comparison_kind": "missing_secondary_table",
                    "missing_secondary_tables": missing_secondary_tables,
                    "primary_table_count": len(primary_tables),
                    "secondary_table_count": len(secondary_tables),
                },
            )
        )

    if missing_primary_tables:
        chosen_source = "secondary"
        safe_markdown = secondary_markdown.strip() or primary_markdown
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="VLM_VLM_DISAGREEMENT",
                score=0.65,
                message="One or more secondary VLM tables were not found in the primary candidate.",
                suggested_action="Review both raw VLM candidates for missing tables.",
                evidence={
                    "comparison_kind": "missing_primary_table",
                    "missing_primary_tables": missing_primary_tables,
                    "primary_table_count": len(primary_tables),
                    "secondary_table_count": len(secondary_tables),
                    "chosen_source": chosen_source,
                },
            )
        )

    if text_cell_disagreement_count:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="VLM_VLM_DISAGREEMENT",
                score=0.55,
                message="Table text differs between VLM candidates.",
                suggested_action="Review both raw VLM candidates for the target page.",
                evidence={
                    "comparison_kind": "table_text_conflict",
                    "text_cell_disagreement_count": text_cell_disagreement_count,
                },
            )
        )

    if unaligned_numeric_rows:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="VLM_VLM_DISAGREEMENT",
                score=0.65,
                message="Some numeric rows could not be aligned between VLM candidates.",
                suggested_action="Review both raw VLM candidates for the target page.",
                evidence={
                    "comparison_kind": "unaligned_numeric_rows",
                    "unaligned_numeric_rows": unaligned_numeric_rows,
                },
            )
        )

    if cell_disagreements:
        safe_markdown = mask_markdown_table_cells(safe_markdown, cell_disagreements)
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="TABLE_CELL_DISAGREEMENT",
                score=0.88,
                message="Table cells differ between VLM candidates.",
                suggested_action="Review both raw VLM candidates and rerun the target page if needed.",
                evidence={
                    "compare_mode": "vlm_vlm",
                    "cell_disagreement_count": len(cell_disagreements),
                    "numeric_cell_comparisons": numeric_cell_comparisons,
                    "disagreement_ratio": round(disagreement_ratio, 4),
                    "primary_model": primary_model,
                    "secondary_model": secondary_model,
                },
            )
        )

    if values_to_mask:
        safe_markdown = replace_values_with_unknown(safe_markdown, values_to_mask)

    invalid_values = invalid_value_candidates(safe_markdown)
    if invalid_values:
        invalid_value_texts = [item["value"] for item in invalid_values]
        safe_markdown = replace_values_with_unknown(safe_markdown, invalid_value_texts)
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="VALUE_VALIDATION_FAILED",
                score=0.82,
                message="A value failed format or range validation.",
                suggested_action="Compare the PDF with both raw VLM candidates.",
                evidence={"invalid_values": invalid_values, "compare_mode": "vlm_vlm"},
            )
        )

    if values_to_mask or cell_disagreements or invalid_values:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="MASKED_AS_UNKNOWN",
                score=0.80,
                message="Low-confidence values were replaced with the unknown token in safe output.",
                suggested_action="Review both raw VLM candidates for masked values.",
                evidence={
                    "compare_mode": "vlm_vlm",
                    "masked_value_count": len(values_to_mask),
                    "masked_cell_count": len(cell_disagreements),
                    "invalid_value_count": len(invalid_values),
                },
            )
        )

    return safe_markdown, warnings, chosen_source


def reconcile_ocr_vlm(
    *,
    page: int,
    preflight: PagePreflight,
    ocr_markdown: str,
    vlm_markdown: str,
    vlm_usage: dict[str, Any] | None,
) -> tuple[str, list[WarningItem]]:
    warnings: list[WarningItem] = []
    safe_markdown = vlm_markdown.strip() or ocr_markdown

    if preflight.image_area_ratio >= 0.60 and len(ocr_markdown.strip()) < 50:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="OCR_EMPTY_RESULT",
                score=0.90,
                message="画像中心のページですが、OCRで抽出された文字数が非常に少ないです。",
                suggested_action="このページだけを高解像度で再投入してください。",
                evidence={
                    "image_area_ratio": preflight.image_area_ratio,
                    "ocr_extracted_chars": len(ocr_markdown.strip()),
                },
            )
        )

    garble_score = (
        0.30 * clamp(symbol_ratio(ocr_markdown) / 0.35)
        + 0.30 * clamp(replacement_char_rate(ocr_markdown) / 0.01)
        + 0.20 * repeated_fragment_ratio(ocr_markdown)
        + 0.20 * single_char_token_ratio(ocr_markdown)
    )
    if garble_score >= 0.40:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry" if garble_score >= 0.75 else "warning",
                code="OCR_GARBLED_TEXT",
                score=round(clamp(garble_score), 4),
                message="OCR結果に文字化け、記号過多、または断片化の疑いがあります。",
                suggested_action="このページだけを高解像度で再投入してください。",
                evidence={
                    "symbol_ratio": round(symbol_ratio(ocr_markdown), 4),
                    "replacement_char_rate": round(replacement_char_rate(ocr_markdown), 4),
                    "single_char_token_ratio": round(single_char_token_ratio(ocr_markdown), 4),
                },
            )
        )

    finish_reason = str((vlm_usage or {}).get("finish_reason") or "")
    if finish_reason in {"length", "content_filter"}:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="VLM_TRUNCATED_OUTPUT",
                score=0.95,
                message="VLM出力が途中で切れた可能性があります。",
                suggested_action="ページ範囲を絞るか、token上限を上げて再投入してください。",
                evidence={"finish_reason": finish_reason},
            )
        )

    if preflight.image_read_risk_score >= 0.50 and len(vlm_markdown.strip()) < 80:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="VLM_LOW_DETAIL_OUTPUT",
                score=0.70,
                message="入力ページの密度に対してVLM出力が短すぎる可能性があります。",
                suggested_action="このページだけを高解像度で再投入してください。",
                evidence={
                    "image_read_risk_score": preflight.image_read_risk_score,
                    "vlm_extracted_chars": len(vlm_markdown.strip()),
                },
            )
        )

    ocr_values = critical_values(ocr_markdown)
    vlm_values = critical_values(vlm_markdown)
    values_to_mask: list[str] = []
    for field_type, ocr_list in ocr_values.items():
        vlm_list = vlm_values.get(field_type, [])
        if field_type == "amount":
            ocr_set = {compact_number(value) for value in ocr_list}
            vlm_set = {compact_number(value) for value in vlm_list}
        else:
            ocr_set = {compact_for_compare(value) for value in ocr_list}
            vlm_set = {compact_for_compare(value) for value in vlm_list}
        if not ocr_set or not vlm_set or ocr_set == vlm_set:
            continue
        differing_vlm_values = [
            value
            for value in vlm_list
            if (compact_number(value) if field_type == "amount" else compact_for_compare(value))
            not in ocr_set
        ]
        differing_ocr_values = [
            value
            for value in ocr_list
            if (compact_number(value) if field_type == "amount" else compact_for_compare(value))
            not in vlm_set
        ]
        values_to_mask.extend(differing_vlm_values)
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="CRITICAL_VALUE_DISAGREEMENT",
                score=0.90,
                message=f"{field_type} のOCR/VLM読み取り結果が一致しません。",
                suggested_action="該当ページを確認し、必要なら高解像度で再投入してください。",
                evidence={
                    "field_type": field_type,
                    "ocr_values": differing_ocr_values,
                    "vlm_values": differing_vlm_values,
                },
            )
        )

    ocr_tables = parse_markdown_tables(ocr_markdown)
    vlm_tables = parse_markdown_tables(vlm_markdown)
    shape_conflicts = table_shape_conflicts(
        vlm_tables,
        ocr_tables,
        left_label="vlm",
        right_label="ocr",
    )
    cell_disagreements: list[tuple[int, int, int]] = []
    numeric_cell_comparisons = 0
    text_cell_disagreement_count = 0
    unaligned_numeric_rows = 0
    for table_index, vlm_table in enumerate(vlm_tables):
        if table_index >= len(ocr_tables):
            continue
        aligned_rows = align_table_rows(vlm_table, ocr_tables[table_index])
        aligned_vlm_row_indices = {row[0] for row in aligned_rows}
        unaligned_numeric_rows += sum(
            1
            for row_index, row in table_data_rows(vlm_table)
            if row_numeric_count(row) >= 2 and row_index not in aligned_vlm_row_indices
        )
        for row_index, vlm_row, _ocr_row_index, ocr_row, _score in aligned_rows:
            if len(vlm_row) != len(ocr_row):
                continue
            for col_index, vlm_cell in enumerate(vlm_row):
                ocr_cell = ocr_row[col_index]
                if not compact_for_compare(vlm_cell) and not compact_for_compare(ocr_cell):
                    continue
                vlm_compact = compact_for_compare(vlm_cell)
                ocr_compact = compact_for_compare(ocr_cell)
                if not vlm_compact and not ocr_compact:
                    continue
                vlm_numeric = is_numeric_like(vlm_cell)
                ocr_numeric = is_numeric_like(ocr_cell)
                if vlm_numeric or ocr_numeric:
                    numeric_cell_comparisons += 1
                    if not (vlm_numeric and ocr_numeric and numeric_values_equal(vlm_cell, ocr_cell)):
                        cell_disagreements.append((table_index, row_index, col_index))
                    continue
                if vlm_compact != ocr_compact:
                    text_cell_disagreement_count += 1

    ocr_table_layout_unreliable = False
    disagreement_ratio = (
        len(cell_disagreements) / numeric_cell_comparisons
        if numeric_cell_comparisons
        else 0.0
    )
    if len(cell_disagreements) >= 10 and disagreement_ratio >= 0.35:
        ocr_table_layout_unreliable = True
        warnings = [
            warning
            for warning in warnings
            if warning.code not in {"CRITICAL_VALUE_DISAGREEMENT"}
        ]
        values_to_mask = []
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="OCR_TABLE_LAYOUT_UNRELIABLE",
                score=round(clamp(disagreement_ratio), 4),
                message="OCR側の表構造が大きく崩れているため、OCR/VLMのセル単位不一致ではsafe出力をmaskせず、VLM側の表を採用しました。",
                suggested_action="warningを確認し、必要なら対象ページを高解像度または対象領域に絞って再投入してください。",
                evidence={
                    "cell_disagreement_count": len(cell_disagreements),
                    "numeric_cell_comparisons": numeric_cell_comparisons,
                    "disagreement_ratio": round(disagreement_ratio, 4),
                },
            )
        )
        cell_disagreements = []

    if text_cell_disagreement_count:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="OCR_VLM_DISAGREEMENT",
                score=0.55,
                message="表内テキストにOCR/VLMの表記ゆれがあります。safe出力ではVLM側の表記を採用しました。",
                suggested_action="必要に応じてraw_outputのOCR/VLM候補値を確認してください。",
                evidence={"text_cell_disagreement_count": text_cell_disagreement_count},
            )
        )

    if unaligned_numeric_rows:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="OCR_VLM_DISAGREEMENT",
                score=0.65,
                message="OCR/VLM間で一部の数値行を対応付けできませんでした。safe出力ではVLM側の表を採用しました。",
                suggested_action="必要に応じてraw_outputのOCR/VLM候補値を確認してください。",
                evidence={"unaligned_numeric_rows": unaligned_numeric_rows},
            )
        )

    if shape_conflicts:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="OCR_VLM_TABLE_STRUCTURE_DISAGREEMENT",
                score=0.86,
                message="OCR and VLM disagree on table structure.",
                suggested_action="Use local table fallback or review the VLM candidate and source image.",
                evidence={
                    "comparison_kind": "table_column_count_mismatch",
                    "vlm_numeric_cells": numeric_cell_count(vlm_tables),
                    "ocr_numeric_cells": numeric_cell_count(ocr_tables),
                    "shape_conflicts": shape_conflicts,
                },
            )
        )

    if cell_disagreements:
        safe_markdown = mask_markdown_table_cells(safe_markdown, cell_disagreements)
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="TABLE_CELL_DISAGREEMENT",
                score=0.88,
                message="表セル単位でOCR/VLMの読み取り結果が一致しません。",
                suggested_action="該当セルを確認し、必要なら該当ページを再投入してください。",
                evidence={"cell_disagreement_count": len(cell_disagreements)},
            )
        )

    if values_to_mask:
        safe_markdown = replace_values_with_unknown(safe_markdown, values_to_mask)

    invalid_values = invalid_value_candidates(safe_markdown)
    if invalid_values:
        invalid_value_texts = [item["value"] for item in invalid_values]
        safe_markdown = replace_values_with_unknown(safe_markdown, invalid_value_texts)
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="needs_retry",
                code="VALUE_VALIDATION_FAILED",
                score=0.82,
                message="値の形式または範囲がルール検証に失敗しました。",
                suggested_action="元PDFと該当値を照合し、必要なら該当ページを再投入してください。",
                evidence={"invalid_values": invalid_values},
            )
        )

    if values_to_mask or cell_disagreements or invalid_values:
        warnings.append(
            WarningItem(
                page=page,
                mode="IMAGE_RECONCILE",
                level="warning",
                code="MASKED_AS_UNKNOWN",
                score=0.80,
                message="safe出力で低信頼箇所を読み取り不明に置換しました。",
                suggested_action="raw_outputのOCR/VLM候補値を確認してください。",
                evidence={
                    "masked_value_count": len(values_to_mask),
                    "masked_cell_count": len(cell_disagreements),
                    "invalid_value_count": len(invalid_values),
                },
            )
        )

    return safe_markdown, warnings


def table_mode_for_pages(pages: list[PagePreflight]) -> str:
    if any(page.complex_table_score >= 0.60 for page in pages):
        return "accurate"
    return "fast"


def embedded_visual_warning(page_preflight: PagePreflight) -> WarningItem:
    scores = [
        float(region.get("visual_complexity_score") or 0.0)
        for region in page_preflight.embedded_visual_regions
    ]
    score = max(scores) if scores else 0.60
    return WarningItem(
        page=page_preflight.page,
        mode="IMAGE_RECONCILE_APPEND",
        level="info",
        code="EMBEDDED_VISUAL_REGION_CANDIDATE",
        score=round(clamp(score), 4),
        message="テキストレイヤー付きページ内に、通常のテキスト抽出では拾えない可能性がある埋め込み画像領域を検知したため、OCR/VLM追加突合を実行しました。",
        suggested_action="追加抽出結果と通常抽出結果を確認してください。不一致や読み取り不明がある場合は対象ページを絞るか解像度を上げて再投入してください。",
        evidence={
            "embedded_visual_region_count": page_preflight.embedded_visual_region_count,
            "embedded_visual_regions": page_preflight.embedded_visual_regions,
            "visual_reference_count": page_preflight.visual_reference_count,
        },
    )


def convert_reconciled_page(
    *,
    pdf_path: Path,
    page_preflight: PagePreflight,
    ocr_converter: DocumentConverter | None,
    vlm_converter: DocumentConverter,
    secondary_vlm_converter: DocumentConverter | None = None,
    compare_mode: ReconcileCompareMode = "ocr_vlm",
    primary_vlm_model: str = "",
    secondary_vlm_model: str = "",
    segment_mode: RoutingMode,
    scratch_dir: Path | None = None,
    crop_scale: float = DEFAULT_VLM_SCALE,
    crop_margin_points: float = 8.0,
    parallel_reconcile_candidates: bool = True,
    enable_reconcile_table_fallback: bool = True,
    table_fallback_converter: DocumentConverter | None = None,
    table_fallback_model: str = "",
    enable_vlm_coordinate_quality_check: bool = True,
    enable_vlm_coordinate_auto_correct: bool = True,
    coordinate_markdown: str = "",
    coordinate_quality: dict[str, Any] | None = None,
    coordinate_diagnostics: dict[str, Any] | None = None,
) -> tuple[ConversionSegment, list[WarningItem]]:
    page_started = time.perf_counter()
    page_number = page_preflight.page
    compare_mode = normalize_reconcile_compare_mode(compare_mode)
    candidate_timings: dict[str, float] = {}
    diagnostics: dict[str, Any] = {
        "compare_mode": compare_mode,
        "parallel_reconcile_candidates": parallel_reconcile_candidates,
    }

    def candidate_failure_warning_items(
        candidate_results: dict[str, dict[str, Any]],
    ) -> list[WarningItem]:
        items: list[WarningItem] = []
        for candidate_name, result in candidate_results.items():
            error = str(result.get("error") or "").strip()
            if not error:
                continue
            items.append(
                WarningItem(
                    page=page_number,
                    mode=segment_mode,
                    level="warning",
                    code="CANDIDATE_CONVERSION_FAILED",
                    score=0.90,
                    message="A reconciliation candidate failed during conversion.",
                    suggested_action=(
                        "Review candidate diagnostics and rerun the affected page if the safe output is incomplete."
                    ),
                    evidence={
                        "candidate": candidate_name,
                        "input_pdf": "embedded_visual_crop"
                        if input_pdf_path != pdf_path
                        else "source_pdf",
                        "start_page": input_start_page,
                        "end_page": input_end_page,
                        "error": error,
                    },
                )
            )
        return items

    input_pdf_path = pdf_path
    input_start_page = page_number
    input_end_page = page_number

    crop_work_dir: Path | None = None
    if segment_mode == "IMAGE_RECONCILE_APPEND" and page_preflight.embedded_visual_regions:
        crop_started = time.perf_counter()
        scratch_parent = scratch_dir or (ROUTING_RUNS_DIR / "_scratch")
        scratch_parent.mkdir(parents=True, exist_ok=True)
        crop_work_dir = scratch_parent / f"append_crop_page_{page_number}_{time.time_ns()}"
        crop_work_dir.mkdir(parents=True, exist_ok=False)
        crop_pdf_path, crop_page_count, crop_metadata = build_embedded_visual_crop_pdf(
            pdf_path=pdf_path,
            page_preflight=page_preflight,
            output_dir=crop_work_dir,
            scale=crop_scale,
            margin_points=crop_margin_points,
        )
        candidate_timings["crop_pdf"] = round(time.perf_counter() - crop_started, 3)
        if crop_metadata:
            input_pdf_path = crop_pdf_path
            input_start_page = 1
            input_end_page = crop_page_count
            diagnostics["append_crop"] = {
                "enabled": True,
                "crop_page_count": crop_page_count,
                "regions": crop_metadata,
            }
        else:
            diagnostics["append_crop"] = {
                "enabled": False,
                "reason": "no_valid_crop_region",
            }

    try:
        if compare_mode == "ocr_vlm":
            if ocr_converter is None:
                raise ValueError("ocr_converter is required for OCR/VLM reconciliation.")
            candidate_jobs = {
                "ocr": {
                    "name": "ocr",
                    "converter": ocr_converter,
                    "pdf_path": input_pdf_path,
                    "start_page": input_start_page,
                    "end_page": input_end_page,
                    "capture_vlm_usage": False,
                },
                "vlm": {
                    "name": "vlm",
                    "converter": vlm_converter,
                    "pdf_path": input_pdf_path,
                    "start_page": input_start_page,
                    "end_page": input_end_page,
                    "capture_vlm_usage": True,
                },
            }
            if parallel_reconcile_candidates:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    candidate_results = {
                        name: future.result()
                        for name, future in {
                            name: executor.submit(timed_docling_candidate, **job)
                            for name, job in candidate_jobs.items()
                        }.items()
                    }
            else:
                candidate_results = {
                    name: timed_docling_candidate(**job)
                    for name, job in candidate_jobs.items()
                }
            ocr_result = candidate_results["ocr"]
            vlm_result = candidate_results["vlm"]
            candidate_failure_warnings = candidate_failure_warning_items(candidate_results)
            ocr_markdown = ocr_result["markdown"]
            ocr_tables = ocr_result["tables"]
            vlm_markdown = vlm_result["markdown"]
            vlm_tables = vlm_result["tables"]
            candidate_timings["ocr"] = ocr_result["elapsed_seconds"]
            candidate_timings["vlm"] = vlm_result["elapsed_seconds"]
            vlm_usage_events = vlm_result["usage_events"]
            safe_markdown, page_warnings = reconcile_ocr_vlm(
                page=page_number,
                preflight=page_preflight,
                ocr_markdown=ocr_markdown,
                vlm_markdown=vlm_markdown,
                vlm_usage=vlm_usage_events[-1] if vlm_usage_events else None,
            )
            page_warnings = [*candidate_failure_warnings, *page_warnings]
            if candidate_failure_warnings and not safe_markdown.strip():
                safe_markdown = UNKNOWN_TOKEN
            raw_markdown = vlm_markdown or ocr_markdown
            raw_ocr_markdown = ocr_markdown
            raw_vlm_markdown = vlm_markdown
            segment_tables = vlm_tables or ocr_tables
        else:
            if secondary_vlm_converter is None:
                raise ValueError("secondary_vlm_converter is required for VLM/VLM reconciliation.")
            candidate_jobs = {
                "vlm_primary": {
                    "name": "vlm_primary",
                    "converter": vlm_converter,
                    "pdf_path": input_pdf_path,
                    "start_page": input_start_page,
                    "end_page": input_end_page,
                    "capture_vlm_usage": True,
                },
                "vlm_secondary": {
                    "name": "vlm_secondary",
                    "converter": secondary_vlm_converter,
                    "pdf_path": input_pdf_path,
                    "start_page": input_start_page,
                    "end_page": input_end_page,
                    "capture_vlm_usage": True,
                },
            }
            if parallel_reconcile_candidates:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    candidate_results = {
                        name: future.result()
                        for name, future in {
                            name: executor.submit(timed_docling_candidate, **job)
                            for name, job in candidate_jobs.items()
                        }.items()
                    }
            else:
                candidate_results = {
                    name: timed_docling_candidate(**job)
                    for name, job in candidate_jobs.items()
                }
            primary_result = candidate_results["vlm_primary"]
            secondary_result = candidate_results["vlm_secondary"]
            candidate_failure_warnings = candidate_failure_warning_items(candidate_results)
            primary_markdown = primary_result["markdown"]
            primary_tables = primary_result["tables"]
            secondary_markdown = secondary_result["markdown"]
            secondary_tables = secondary_result["tables"]
            candidate_timings["vlm_primary"] = primary_result["elapsed_seconds"]
            candidate_timings["vlm_secondary"] = secondary_result["elapsed_seconds"]
            primary_usage_events = primary_result["usage_events"]
            secondary_usage_events = secondary_result["usage_events"]
            primary_usage = primary_usage_events[-1] if primary_usage_events else None
            secondary_usage = secondary_usage_events[-1] if secondary_usage_events else None

            safe_markdown, page_warnings, chosen_source = reconcile_vlm_vlm(
                page=page_number,
                preflight=page_preflight,
                primary_markdown=primary_markdown,
                secondary_markdown=secondary_markdown,
                primary_usage=primary_usage,
                secondary_usage=secondary_usage,
                primary_model=primary_vlm_model,
                secondary_model=secondary_vlm_model,
            )
            page_warnings = [*candidate_failure_warnings, *page_warnings]
            if candidate_failure_warnings and not safe_markdown.strip():
                safe_markdown = UNKNOWN_TOKEN
            raw_markdown = primary_markdown or secondary_markdown
            raw_ocr_markdown = primary_markdown
            raw_vlm_markdown = secondary_markdown
            segment_tables = secondary_tables if chosen_source == "secondary" else primary_tables

        fallback_triggers = reconcile_table_fallback_triggers(safe_markdown, page_warnings)
        if (
            enable_reconcile_table_fallback
            and table_fallback_converter is not None
            and fallback_triggers
        ):
            fallback_result = timed_docling_candidate(
                name="reconcile_table_fallback",
                converter=table_fallback_converter,
                pdf_path=input_pdf_path,
                start_page=input_start_page,
                end_page=input_end_page,
                capture_vlm_usage=True,
            )
            timing_key = (
                f"reconcile_table_fallback:{table_fallback_model}"
                if table_fallback_model
                else "reconcile_table_fallback"
            )
            candidate_timings[timing_key] = fallback_result["elapsed_seconds"]
            fallback_markdown = str(fallback_result["markdown"] or "")
            accept_fallback, accept_reason, fallback_metrics = should_accept_reconcile_table_fallback(
                safe_markdown,
                fallback_markdown,
                triggers=fallback_triggers,
            )
            diagnostics["reconcile_table_fallback"] = {
                "attempted": True,
                "accepted": accept_fallback,
                "reason": accept_reason,
                "model": table_fallback_model,
                "triggers": fallback_triggers,
                "metrics": fallback_metrics,
                "usage_events": fallback_result["usage_events"],
            }
            if accept_fallback:
                safe_markdown = fallback_markdown
                if fallback_result["tables"]:
                    segment_tables = fallback_result["tables"]
                page_warnings.append(
                    WarningItem(
                        page=page_number,
                        mode=segment_mode,
                        level="info",
                        code="RECONCILE_TABLE_FALLBACK_APPLIED",
                        score=0.75,
                        message="Local table fallback replaced the reconciled table output.",
                        suggested_action="Review the fallback metadata and source candidates if the page remains uncertain.",
                        evidence={
                            "model": table_fallback_model,
                            "reason": accept_reason,
                            "triggers": fallback_triggers,
                            "metrics": fallback_metrics,
                        },
                    )
                )
            else:
                page_warnings.append(
                    WarningItem(
                        page=page_number,
                        mode=segment_mode,
                        level="info",
                        code="RECONCILE_TABLE_FALLBACK_SKIPPED",
                        score=0.40,
                        message="Local table fallback was run but was not better than the reconciled output.",
                        suggested_action="Review the raw candidates if the warning is material.",
                        evidence={
                            "model": table_fallback_model,
                            "reason": accept_reason,
                            "triggers": fallback_triggers,
                            "metrics": fallback_metrics,
                        },
                    )
                )
        else:
            diagnostics["reconcile_table_fallback"] = {
                "attempted": False,
                "enabled": enable_reconcile_table_fallback,
                "triggers": fallback_triggers,
            }
        coordinate_model = primary_vlm_model
        reconcile_fallback_diagnostics = diagnostics.get("reconcile_table_fallback") or {}
        if reconcile_fallback_diagnostics.get("accepted") and table_fallback_model:
            coordinate_model = table_fallback_model
        (
            safe_markdown,
            segment_tables,
            coordinate_warnings,
            coordinate_quality_diagnostics,
            coordinate_timings,
        ) = apply_vlm_coordinate_quality_check(
            page=page_number,
            mode=segment_mode,
            safe_markdown=safe_markdown,
            tables=segment_tables,
            coordinate_markdown=coordinate_markdown,
            coordinate_quality=coordinate_quality,
            coordinate_diagnostics=coordinate_diagnostics,
            model=coordinate_model,
            source=f"{compare_mode}_safe_output",
            enable_quality_check=enable_vlm_coordinate_quality_check,
            enable_auto_correct=enable_vlm_coordinate_auto_correct,
            fallback_converter=(
                table_fallback_converter
                if enable_reconcile_table_fallback
                and not reconcile_fallback_diagnostics.get("accepted")
                else None
            ),
            fallback_model=table_fallback_model,
            fallback_pdf_path=input_pdf_path,
            fallback_start_page=input_start_page,
            fallback_end_page=input_end_page,
        )
        page_warnings.extend(coordinate_warnings)
        candidate_timings.update(coordinate_timings)
        diagnostics["coordinate_vlm_quality"] = coordinate_quality_diagnostics
    finally:
        if crop_work_dir is not None:
            shutil.rmtree(crop_work_dir, ignore_errors=True)

    if segment_mode == "IMAGE_RECONCILE_APPEND":
        for warning in page_warnings:
            warning.mode = "IMAGE_RECONCILE_APPEND"
        page_warnings = [embedded_visual_warning(page_preflight), *page_warnings]
    diagnostics["input_pages"] = {
        "pdf": "embedded_visual_crop" if input_pdf_path != pdf_path else "source_pdf",
        "start_page": input_start_page,
        "end_page": input_end_page,
    }

    return (
        ConversionSegment(
            mode=segment_mode,
            start_page=page_number,
            end_page=page_number,
            markdown=raw_markdown,
            safe_markdown=safe_markdown,
            raw_ocr_markdown=raw_ocr_markdown,
            raw_vlm_markdown=raw_vlm_markdown,
            tables=segment_tables,
            elapsed_seconds=round(time.perf_counter() - page_started, 3),
            candidate_timings_seconds=candidate_timings,
            diagnostics=diagnostics,
        ),
        page_warnings,
    )


def run_routed_pdf(
    pdf_path: Path,
    *,
    options: RoutedPdfOptions,
    run_id: str | None = None,
    output_dir: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    load_dotenv(ROOT / ".env", override=True)
    pdf_path = pdf_path.resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    run_id = run_id or f"routing_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = output_dir or (ROUTING_RUNS_DIR / run_id)
    started = time.perf_counter()
    options.reconcile_compare_mode = normalize_reconcile_compare_mode(
        options.reconcile_compare_mode
    )
    options.secondary_model = options.secondary_model.strip() or options.model
    source_a_label, source_b_label = reconcile_source_labels(options)

    _progress(progress_callback, "preflight")
    preflight_started = time.perf_counter()
    preflight = pdf_preflight(pdf_path, options)
    groups = group_pages(preflight)
    preflight_seconds = time.perf_counter() - preflight_started
    reconcile_pages = [
        page.page
        for page in preflight
        if page.mode == "IMAGE_RECONCILE" or "IMAGE_RECONCILE_APPEND" in page.extra_actions
    ]
    table_vlm_candidate_pages = [
        page.page
        for page in preflight
        if (
            options.use_coordinate_table_reconstruction
            and options.enable_table_vlm_fallback
            and page.mode in {"TEXT_TABLE_FAST", "TEXT_TABLE_ACCURATE"}
        )
    ]
    if (reconcile_pages or table_vlm_candidate_pages) and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "VLM pages require OPENAI_API_KEY because reconciliation or table VLM fallback is executed."
        )
    if reconcile_pages:
        check_openai_chat_access(
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            timeout_seconds=min(options.timeout_seconds, 60),
        )
        if options.reconcile_compare_mode == "vlm_vlm" and options.secondary_model != options.model:
            check_openai_chat_access(
                model=options.secondary_model,
                reasoning_effort=options.reasoning_effort,
                timeout_seconds=min(options.timeout_seconds, 60),
            )
    if table_vlm_candidate_pages:
        for table_model in sorted(
            {options.table_vlm_model, options.large_table_vlm_model}
        ):
            check_openai_chat_access(
                model=table_model,
                reasoning_effort=options.table_vlm_reasoning_effort,
                timeout_seconds=min(options.timeout_seconds, 60),
            )

    converters: dict[str, DocumentConverter] = {}
    segments: list[ConversionSegment] = []
    warnings: list[WarningItem] = []
    scratch_dir = run_dir / "_scratch"

    def table_warnings_to_items(
        table_warnings: list[TableRepairWarning],
        *,
        mode: RoutingMode,
    ) -> list[WarningItem]:
        return [
            WarningItem(
                page=warning.page,
                mode=mode,
                level=warning.level,
                code=warning.code,
                score=warning.score,
                message=warning.message,
                suggested_action=warning.suggested_action,
                evidence={
                    **warning.evidence,
                    "table_index_in_segment": warning.table_index,
                },
            )
            for warning in table_warnings
        ]

    def convert_standard_group(group: RoutingGroup) -> tuple[list[ConversionSegment], list[WarningItem]]:
        group_started = time.perf_counter()
        group_pages = set(group.pages)
        group_preflight = [page for page in preflight if page.page in group_pages]
        if (
            options.use_coordinate_table_reconstruction
            and group.mode in {"TEXT_TABLE_FAST", "TEXT_TABLE_ACCURATE"}
        ):
            return convert_table_group_with_coordinate_fallback(group, group_preflight)
        use_coordinate = (
            options.use_coordinate_table_reconstruction
            and group.mode == "TEXT_TABLE_ACCURATE"
        )
        if use_coordinate:
            markdown, tables, _table_text, table_warnings = convert_coordinate_tables_range(
                pdf_path,
                group.start_page,
                group.end_page,
            )
        else:
            converter = build_standard_converter(group.mode)
            markdown, tables, _table_text, table_warnings = convert_docling_range(
                converter,
                pdf_path,
                group.start_page,
                group.end_page,
                repair_tables=group.mode in {"TEXT_TABLE_FAST", "TEXT_TABLE_ACCURATE"},
            )
        group_warnings = table_warnings_to_items(table_warnings, mode=group.mode)
        segment = ConversionSegment(
            mode=group.mode,
            start_page=group.start_page,
            end_page=group.end_page,
            markdown=markdown,
            safe_markdown=markdown,
            tables=tables,
            elapsed_seconds=round(time.perf_counter() - group_started, 3),
            diagnostics={
                "coordinate_table_reconstruction": use_coordinate,
            },
        )
        if group.mode in {"TEXT_TABLE_FAST", "TEXT_TABLE_ACCURATE"} and not tables:
            for page in group_preflight:
                if page.table_score >= options.table_score_threshold:
                    group_warnings.append(
                        WarningItem(
                            page=page.page,
                            mode=group.mode,
                            level="warning",
                            code="TABLE_MISSED",
                            score=0.70,
                            message="テーブル信号がありましたが、構造化テーブルが抽出されませんでした。",
                            suggested_action="必要ならTEXT_TABLE_ACCURATEで再投入してください。",
                            evidence={
                                "table_score": page.table_score,
                                "complex_table_score": page.complex_table_score,
                            },
                        )
                    )
        return [segment], group_warnings

    def get_vlm_converter(model: str, role: str = "primary") -> DocumentConverter:
        key = f"IMAGE_RECONCILE:vlm:{role}:{model}"
        converter = converters.get(key)
        if converter is None:
            converter = build_openai_vlm_converter(
                model=model,
                max_completion_tokens=options.max_completion_tokens,
                reasoning_effort=options.reasoning_effort,
                timeout_seconds=options.timeout_seconds,
                scale=options.vlm_scale,
                response_format=options.response_format,
                prompt_variant=options.prompt_variant,
            )
            converters[key] = converter
        return converter

    def get_table_vlm_converter(model: str) -> DocumentConverter:
        key = (
            f"TEXT_TABLE_VLM:{model}:"
            f"{options.table_vlm_prompt_variant}:{options.table_vlm_reasoning_effort}"
        )
        converter = converters.get(key)
        if converter is None:
            converter = build_openai_vlm_converter(
                model=model,
                max_completion_tokens=options.max_completion_tokens,
                reasoning_effort=options.table_vlm_reasoning_effort,
                timeout_seconds=options.timeout_seconds,
                scale=options.vlm_scale,
                response_format=options.response_format,
                prompt_variant=options.table_vlm_prompt_variant,
            )
            converters[key] = converter
        return converter

    def get_reconcile_table_fallback_converter() -> DocumentConverter:
        key = (
            f"RECONCILE_TABLE_FALLBACK:{options.reconcile_table_fallback_model}:"
            f"{options.reconcile_table_fallback_prompt_variant}:"
            f"{options.reconcile_table_fallback_reasoning_effort}"
        )
        converter = converters.get(key)
        if converter is None:
            converter = build_openai_vlm_converter(
                model=options.reconcile_table_fallback_model,
                max_completion_tokens=options.max_completion_tokens,
                reasoning_effort=options.reconcile_table_fallback_reasoning_effort,
                timeout_seconds=options.timeout_seconds,
                scale=options.vlm_scale,
                response_format=options.response_format,
                prompt_variant=options.reconcile_table_fallback_prompt_variant,
            )
            converters[key] = converter
        return converter

    coordinate_evidence_cache: dict[int, dict[str, Any]] = {}

    def get_coordinate_evidence(page_no: int) -> dict[str, Any]:
        cached = coordinate_evidence_cache.get(page_no)
        if cached is not None:
            return cached
        try:
            import pypdfium2 as pdfium
        except Exception:
            evidence = {
                "applicable": False,
                "reason": "pypdfium2_unavailable",
            }
            coordinate_evidence_cache[page_no] = evidence
            return evidence
        try:
            document = pdfium.PdfDocument(str(pdf_path))
        except Exception as exc:
            evidence = {
                "applicable": False,
                "reason": "coordinate_evidence_pdf_open_failed",
                "diagnostics": {
                    "status": "not_applicable",
                    "reason": "coordinate_evidence_pdf_open_failed",
                    "error": str(exc),
                },
            }
            coordinate_evidence_cache[page_no] = evidence
            return evidence
        try:
            try:
                (
                    coord_markdown,
                    coord_tables,
                    _coord_table_text,
                    coord_warnings,
                    coord_diagnostics,
                ) = convert_coordinate_table_page(document=document, page_no=page_no)
                quality = coordinate_quality_report(coord_diagnostics, options)
                evidence = {
                    "applicable": bool(coord_tables and coord_markdown.strip()),
                    "reason": "ok" if coord_tables and coord_markdown.strip() else "no_coordinate_table",
                    "markdown": coord_markdown,
                    "tables": coord_tables,
                    "warnings": coord_warnings,
                    "quality": quality,
                    "diagnostics": coord_diagnostics,
                }
            except Exception as exc:
                evidence = {
                    "applicable": False,
                    "reason": "coordinate_evidence_extraction_failed",
                    "diagnostics": {
                        "status": "not_applicable",
                        "reason": "coordinate_evidence_extraction_failed",
                        "error": str(exc),
                    },
                }
        finally:
            document.close()
        coordinate_evidence_cache[page_no] = evidence
        return evidence

    def coordinate_evidence_for_vlm_segment(
        page_preflight: PagePreflight,
        *,
        require_embedded_visual_overlap: bool,
    ) -> dict[str, Any]:
        evidence = dict(get_coordinate_evidence(page_preflight.page))
        if not evidence.get("applicable"):
            return evidence
        if require_embedded_visual_overlap and not coordinate_bbox_overlaps_regions(
            evidence.get("diagnostics") or {},
            page_preflight.embedded_visual_regions,
            page_height=page_preflight.height,
        ):
            return {
                **evidence,
                "applicable": False,
                "reason": "coordinate_table_outside_embedded_visual_region",
            }
        return evidence

    def convert_table_group_with_coordinate_fallback(
        group: RoutingGroup,
        group_preflight: list[PagePreflight],
    ) -> tuple[list[ConversionSegment], list[WarningItem]]:
        try:
            import pypdfium2 as pdfium
        except Exception as exc:
            raise RuntimeError("pypdfium2 is required for coordinate table reconstruction.") from exc

        document = pdfium.PdfDocument(str(pdf_path))
        output_segments: list[ConversionSegment] = []
        output_warnings: list[WarningItem] = []
        preflight_by_page = {page.page: page for page in group_preflight}
        local_fallback_converter: DocumentConverter | None = None

        def convert_single_page_docling(
            *,
            converter: DocumentConverter,
            page_no: int,
            repair_tables: bool,
            label: str,
        ) -> tuple[str, list[dict[str, Any]], str, list[TableRepairWarning], float, float]:
            page_pdf_dir = scratch_dir / f"{label}_page_{page_no}_{time.time_ns()}"
            extract_started = time.perf_counter()
            page_pdf_path = build_page_range_pdf(
                pdf_path=pdf_path,
                start_page=page_no,
                end_page=page_no,
                output_dir=page_pdf_dir,
                label=label,
            )
            extract_seconds = round(time.perf_counter() - extract_started, 3)
            convert_started = time.perf_counter()
            try:
                markdown, tables, table_text, table_warnings = convert_docling_range(
                    converter,
                    page_pdf_path,
                    1,
                    1,
                    repair_tables=repair_tables,
                )
                convert_seconds = round(time.perf_counter() - convert_started, 3)
                return (
                    markdown,
                    tables,
                    table_text,
                    table_warnings,
                    convert_seconds,
                    extract_seconds,
                )
            finally:
                shutil.rmtree(page_pdf_dir, ignore_errors=True)

        try:
            for page_no in group.pages:
                page_started = time.perf_counter()
                page_preflight = preflight_by_page[page_no]
                (
                    coord_markdown,
                    coord_tables,
                    _coord_table_text,
                    coord_warnings,
                    coord_diagnostics,
                ) = convert_coordinate_table_page(document=document, page_no=page_no)
                quality = coordinate_quality_report(coord_diagnostics, options)
                coordinate_strength = coordinate_evidence_strength(quality)
                prefer_coordinate_output = (
                    bool(coord_markdown.strip())
                    and coordinate_strength in {"strong", "medium"}
                    and page_preflight.image_area_ratio < 0.05
                )
                if quality["ok"] or prefer_coordinate_output:
                    output_warnings.extend(
                        table_warnings_to_items(coord_warnings, mode="TEXT_TABLE_COORD")
                    )
                    if not quality["ok"]:
                        output_warnings.append(
                            WarningItem(
                                page=page_no,
                                mode="TEXT_TABLE_COORD",
                                level="info",
                                code="COORDINATE_TABLE_ACCEPTED_WITH_WARNINGS",
                                score=0.55,
                                message=(
                                    "Coordinate table reconstruction was accepted because "
                                    "the PDF has a text layer and coordinate evidence is usable."
                                ),
                                suggested_action=(
                                    "Review the coordinate diagnostics if this table is business-critical."
                                ),
                                evidence={
                                    "routing_source_mode": group.mode,
                                    "coordinate_evidence_strength": coordinate_strength,
                                    "coordinate_quality": quality,
                                    "coordinate_diagnostics": coord_diagnostics,
                                },
                            )
                        )
                    output_segments.append(
                        ConversionSegment(
                            mode="TEXT_TABLE_COORD",
                            start_page=page_no,
                            end_page=page_no,
                            markdown=coord_markdown,
                            safe_markdown=coord_markdown,
                            tables=coord_tables,
                            elapsed_seconds=round(time.perf_counter() - page_started, 3),
                            diagnostics={
                                "routing_source_mode": group.mode,
                                "coordinate_table_reconstruction": True,
                                "coordinate_quality": quality,
                                "coordinate_diagnostics": coord_diagnostics,
                            },
                        )
                    )
                    continue

                if not options.enable_table_vlm_fallback:
                    output_warnings.append(
                        WarningItem(
                            page=page_no,
                            mode=group.mode,
                            level="info",
                            code="COORDINATE_TABLE_FALLBACK_TO_DOCLING",
                            score=0.60,
                            message=(
                                "Coordinate table reconstruction was attempted but the "
                                "evidence was weak, so local Docling table extraction was used."
                            ),
                            suggested_action=(
                                "If this page is slow or inaccurate, review coordinate diagnostics "
                                "or enable table VLM fallback for targeted pages."
                            ),
                            evidence={
                                "routing_source_mode": group.mode,
                                "coordinate_quality": quality,
                                "coordinate_diagnostics": coord_diagnostics,
                            },
                        )
                    )
                    if local_fallback_converter is None:
                        local_fallback_converter = build_standard_converter(group.mode)
                    (
                        markdown,
                        tables,
                        _table_text,
                        table_warnings,
                        fallback_seconds,
                        extract_seconds,
                    ) = convert_single_page_docling(
                        converter=local_fallback_converter,
                        page_no=page_no,
                        repair_tables=True,
                        label="docling_table_fallback",
                    )
                    output_warnings.extend(
                        table_warnings_to_items(table_warnings, mode=group.mode)
                    )
                    output_segments.append(
                        ConversionSegment(
                            mode=group.mode,
                            start_page=page_no,
                            end_page=page_no,
                            markdown=markdown,
                            safe_markdown=markdown,
                            tables=tables,
                            elapsed_seconds=round(time.perf_counter() - page_started, 3),
                            candidate_timings_seconds={
                                "extract_page_pdf": extract_seconds,
                                f"docling_table:{group.mode}": fallback_seconds,
                            },
                            diagnostics={
                                "coordinate_table_reconstruction": True,
                                "coordinate_fallback": "docling_table",
                                "coordinate_quality": quality,
                                "coordinate_diagnostics": coord_diagnostics,
                            },
                        )
                    )
                    continue

                selected_model, model_reasons = select_table_vlm_model(
                    page_preflight,
                    coord_diagnostics,
                    quality,
                    options,
                )
                output_warnings.append(
                    WarningItem(
                        page=page_no,
                        mode="TEXT_TABLE_VLM",
                        level="info",
                        code="COORDINATE_TABLE_FALLBACK_TO_VLM",
                        score=0.65,
                        message="PDF座標ベースの表復元が低信頼のため、表抽出をVLMへフォールバックしました。",
                        suggested_action="出力表とPDF原本を確認してください。重要ページではVLM突合を検討してください。",
                        evidence={
                            "routing_source_mode": group.mode,
                            "coordinate_quality": quality,
                            "coordinate_diagnostics": coord_diagnostics,
                            "selected_model": selected_model,
                            "model_selection_reasons": model_reasons,
                        },
                    )
                )
                converter = get_table_vlm_converter(selected_model)
                (
                    markdown,
                    tables,
                    _table_text,
                    table_warnings,
                    vlm_seconds,
                    extract_seconds,
                ) = convert_single_page_docling(
                    converter=converter,
                    page_no=page_no,
                    repair_tables=False,
                    label="table_vlm_fallback",
                )
                output_warnings.extend(
                    table_warnings_to_items(table_warnings, mode="TEXT_TABLE_VLM")
                )
                safe_markdown = markdown
                safe_tables = tables
                candidate_timings = {
                    "extract_page_pdf": extract_seconds,
                    f"table_vlm:{selected_model}": vlm_seconds,
                }
                coordinate_vlm_diagnostics: dict[str, Any] = {
                    "enabled": options.enable_vlm_coordinate_quality_check,
                }
                if options.enable_vlm_coordinate_quality_check:
                    if options.enable_vlm_coordinate_auto_correct:
                        corrected_markdown, corrections = auto_correct_vlm_cells_from_coordinate(
                            vlm_markdown=safe_markdown,
                            coordinate_markdown=coord_markdown,
                            coordinate_quality=quality,
                        )
                        if corrections:
                            safe_markdown = corrected_markdown
                            output_warnings.append(
                                WarningItem(
                                    page=page_no,
                                    mode="TEXT_TABLE_VLM",
                                    level="info",
                                    code="VLM_COORD_AUTO_CORRECTED_CELL",
                                    score=0.70,
                                    message="VLM table cells were automatically corrected using unique coordinate values.",
                                    suggested_action="Review corrected cells if the source table is business-critical.",
                                    evidence={
                                        "correction_count": len(corrections),
                                        "corrections": corrections,
                                        "selected_model": selected_model,
                                    },
                                )
                            )

                    current_report = coordinate_vlm_quality_report(
                        coordinate_markdown=coord_markdown,
                        vlm_markdown=safe_markdown,
                        coordinate_quality=quality,
                        coordinate_diagnostics=coord_diagnostics,
                    )
                    output_warnings.extend(
                        coordinate_vlm_quality_warnings(
                            page=page_no,
                            mode="TEXT_TABLE_VLM",
                            report=current_report,
                            model=selected_model,
                            source="initial",
                        )
                    )
                    coordinate_vlm_diagnostics["initial_report"] = current_report
                    final_report = current_report
                    fallback_attempted = False
                    if (
                        current_report.get("needs_fallback")
                        and options.enable_reconcile_table_fallback
                    ):
                        fallback_attempted = True
                        fallback_model = options.reconcile_table_fallback_model
                        fallback_converter = get_reconcile_table_fallback_converter()
                        (
                            fallback_markdown,
                            fallback_tables,
                            _fallback_table_text,
                            fallback_table_warnings,
                            fallback_seconds,
                            fallback_extract_seconds,
                        ) = convert_single_page_docling(
                            converter=fallback_converter,
                            page_no=page_no,
                            repair_tables=False,
                            label="coordinate_vlm_fallback",
                        )
                        candidate_timings["extract_page_pdf"] = round(
                            candidate_timings.get("extract_page_pdf", 0.0)
                            + fallback_extract_seconds,
                            3,
                        )
                        candidate_timings[f"coordinate_vlm_fallback:{fallback_model}"] = (
                            fallback_seconds
                        )
                        output_warnings.extend(
                            table_warnings_to_items(
                                fallback_table_warnings,
                                mode="TEXT_TABLE_VLM",
                            )
                        )
                        fallback_safe_markdown = fallback_markdown
                        if options.enable_vlm_coordinate_auto_correct:
                            fallback_safe_markdown, fallback_corrections = (
                                auto_correct_vlm_cells_from_coordinate(
                                    vlm_markdown=fallback_safe_markdown,
                                    coordinate_markdown=coord_markdown,
                                    coordinate_quality=quality,
                                )
                            )
                        else:
                            fallback_corrections = []
                        fallback_report = coordinate_vlm_quality_report(
                            coordinate_markdown=coord_markdown,
                            vlm_markdown=fallback_safe_markdown,
                            coordinate_quality=quality,
                            coordinate_diagnostics=coord_diagnostics,
                        )
                        accept_fallback, accept_reason = should_accept_coordinate_vlm_fallback(
                            current_report,
                            fallback_report,
                        )
                        coordinate_vlm_diagnostics["fallback"] = {
                            "attempted": True,
                            "accepted": accept_fallback,
                            "reason": accept_reason,
                            "model": fallback_model,
                            "elapsed_seconds": fallback_seconds,
                            "report": fallback_report,
                            "auto_corrections": fallback_corrections,
                        }
                        if accept_fallback:
                            safe_markdown = fallback_safe_markdown
                            safe_tables = fallback_tables or safe_tables
                            final_report = fallback_report
                            output_warnings.append(
                                WarningItem(
                                    page=page_no,
                                    mode="TEXT_TABLE_VLM",
                                    level="info",
                                    code="VLM_COORD_FALLBACK_APPLIED",
                                    score=0.76,
                                    message="Table VLM output was replaced by coordinate-validated fallback output.",
                                    suggested_action="Review the coordinate/VLM quality report if critical values remain uncertain.",
                                    evidence={
                                        "model": fallback_model,
                                        "reason": accept_reason,
                                        "initial_report": current_report,
                                        "fallback_report": fallback_report,
                                    },
                                )
                            )
                        else:
                            output_warnings.append(
                                WarningItem(
                                    page=page_no,
                                    mode="TEXT_TABLE_VLM",
                                    level="info",
                                    code="VLM_COORD_FALLBACK_SKIPPED",
                                    score=0.45,
                                    message="Coordinate-validated fallback was run but was not better than the initial VLM output.",
                                    suggested_action="Review the coordinate/VLM quality report before trusting unsupported values.",
                                    evidence={
                                        "model": fallback_model,
                                        "reason": accept_reason,
                                        "initial_report": current_report,
                                        "fallback_report": fallback_report,
                                    },
                                )
                            )
                    if not fallback_attempted:
                        coordinate_vlm_diagnostics["fallback"] = {
                            "attempted": False,
                            "enabled": options.enable_reconcile_table_fallback,
                            "reason": "no_coordinate_vlm_fallback_trigger",
                        }

                    masked_markdown, masked_count = mask_vlm_only_values(
                        safe_markdown,
                        final_report,
                    )
                    if masked_count:
                        safe_markdown = masked_markdown
                        output_warnings.append(
                            WarningItem(
                                page=page_no,
                                mode="TEXT_TABLE_VLM",
                                level="warning",
                                code="VLM_COORD_MASKED_UNSUPPORTED_VALUE",
                                score=0.86,
                                message="VLM-only critical values were masked because coordinate extraction did not support them.",
                                suggested_action="Review the source PDF or rerun the target page with higher resolution.",
                                evidence={
                                    "masked_value_count": masked_count,
                                    "final_report": final_report,
                                },
                            )
                        )
                        output_warnings.append(
                            WarningItem(
                                page=page_no,
                                mode="TEXT_TABLE_VLM",
                                level="warning",
                                code="MASKED_AS_UNKNOWN",
                                score=0.80,
                                message="Unsupported VLM values were replaced with the unknown token in safe output.",
                                suggested_action="Review the coordinate/VLM quality report and source PDF.",
                                evidence={
                                    "masked_value_count": masked_count,
                                    "reason": "vlm_coordinate_unsupported_values",
                                },
                            )
                        )
                    coordinate_vlm_diagnostics["final_report"] = coordinate_vlm_quality_report(
                        coordinate_markdown=coord_markdown,
                        vlm_markdown=safe_markdown,
                        coordinate_quality=quality,
                        coordinate_diagnostics=coord_diagnostics,
                    )

                output_segments.append(
                    ConversionSegment(
                        mode="TEXT_TABLE_VLM",
                        start_page=page_no,
                        end_page=page_no,
                        markdown=markdown,
                        safe_markdown=safe_markdown,
                        raw_vlm_markdown=markdown,
                        tables=safe_tables,
                        elapsed_seconds=round(time.perf_counter() - page_started, 3),
                        candidate_timings_seconds=candidate_timings,
                        diagnostics={
                            "routing_source_mode": group.mode,
                            "coordinate_table_reconstruction": True,
                            "coordinate_quality": quality,
                            "coordinate_diagnostics": coord_diagnostics,
                            "coordinate_markdown": coord_markdown,
                            "table_vlm_model": selected_model,
                            "table_vlm_model_selection_reasons": model_reasons,
                            "table_vlm_prompt_variant": options.table_vlm_prompt_variant,
                            "table_vlm_reasoning_effort": options.table_vlm_reasoning_effort,
                            "coordinate_vlm_quality": coordinate_vlm_diagnostics,
                        },
                    )
                )
        finally:
            document.close()
        return output_segments, output_warnings

    def process_append_pages(
        *,
        group_preflight: list[PagePreflight],
        base_markdown: str,
    ) -> tuple[list[ConversionSegment], list[WarningItem]]:
        append_segments: list[ConversionSegment] = []
        append_warnings_all: list[WarningItem] = []
        append_pages = [
            page for page in group_preflight if "IMAGE_RECONCILE_APPEND" in page.extra_actions
        ]
        for append_page in append_pages:
            _progress(
                progress_callback,
                f"append IMAGE_RECONCILE page {append_page.page}",
            )
            append_ocr_converter: DocumentConverter | None = None
            if options.reconcile_compare_mode == "ocr_vlm":
                append_table_mode = table_mode_for_pages([append_page])
                append_ocr_converter = build_standard_converter(
                    "IMAGE_RECONCILE",
                    table_mode=append_table_mode,
                )
            append_vlm_converter = get_vlm_converter(options.model, "primary")
            append_secondary_vlm_converter = (
                get_vlm_converter(options.secondary_model, "secondary")
                if options.reconcile_compare_mode == "vlm_vlm"
                else None
            )
            append_coordinate_evidence = coordinate_evidence_for_vlm_segment(
                append_page,
                require_embedded_visual_overlap=True,
            )
            append_segment, append_warnings = convert_reconciled_page(
                pdf_path=pdf_path,
                page_preflight=append_page,
                ocr_converter=append_ocr_converter,
                vlm_converter=append_vlm_converter,
                secondary_vlm_converter=append_secondary_vlm_converter,
                compare_mode=options.reconcile_compare_mode,
                primary_vlm_model=options.model,
                secondary_vlm_model=options.secondary_model,
                segment_mode="IMAGE_RECONCILE_APPEND",
                scratch_dir=scratch_dir,
                crop_scale=options.vlm_scale,
                crop_margin_points=options.embedded_visual_crop_margin_points,
                parallel_reconcile_candidates=options.parallel_reconcile_candidates,
                enable_reconcile_table_fallback=options.enable_reconcile_table_fallback,
                table_fallback_converter=(
                    get_reconcile_table_fallback_converter()
                    if options.enable_reconcile_table_fallback
                    else None
                ),
                table_fallback_model=options.reconcile_table_fallback_model,
                enable_vlm_coordinate_quality_check=options.enable_vlm_coordinate_quality_check,
                enable_vlm_coordinate_auto_correct=options.enable_vlm_coordinate_auto_correct,
                coordinate_markdown=str(append_coordinate_evidence.get("markdown") or "")
                if append_coordinate_evidence.get("applicable")
                else "",
                coordinate_quality=append_coordinate_evidence.get("quality")
                if append_coordinate_evidence.get("applicable")
                else None,
                coordinate_diagnostics=append_coordinate_evidence.get("diagnostics")
                if append_coordinate_evidence.get("applicable")
                else {
                    "status": "not_applicable",
                    "reason": append_coordinate_evidence.get("reason"),
                },
            )
            append_segment.safe_markdown = clean_append_markdown(
                append_segment.safe_markdown,
                base_markdown,
            )
            append_segment.markdown = clean_append_markdown(
                append_segment.markdown,
                base_markdown,
            )
            append_segment.tables = filter_append_tables(append_segment.tables, base_markdown)
            append_segments.append(append_segment)
            append_warnings_all.extend(append_warnings)
        return append_segments, append_warnings_all

    parallel_standard_futures: dict[int, Any] = {}
    max_parallel_table_groups = max(int(options.max_parallel_table_groups or 1), 1)
    standard_executor: ThreadPoolExecutor | None = None
    if max_parallel_table_groups > 1:
        standard_executor = ThreadPoolExecutor(max_workers=max_parallel_table_groups)
        for index, group in enumerate(groups, start=1):
            if group.mode == "IMAGE_RECONCILE":
                continue
            parallel_standard_futures[index] = standard_executor.submit(
                convert_standard_group,
                group,
            )

    for group_index, group in enumerate(groups, start=1):
        _progress(
            progress_callback,
            f"group {group_index}/{len(groups)} {group.mode} pages {group.start_page}-{group.end_page}",
        )
        if group_index in parallel_standard_futures:
            group_segments, group_warnings = parallel_standard_futures[group_index].result()
            segments.extend(group_segments)
            warnings.extend(group_warnings)
            group_preflight = [page for page in preflight if page.page in set(group.pages)]
            base_markdown = group_segments[0].markdown if group_segments else ""
            append_segments, append_warnings = process_append_pages(
                group_preflight=group_preflight,
                base_markdown=base_markdown,
            )
            segments.extend(append_segments)
            warnings.extend(append_warnings)
            continue
        group_started = time.perf_counter()
        group_preflight = [page for page in preflight if page.page in set(group.pages)]
        if group.mode != "IMAGE_RECONCILE":
            if (
                options.use_coordinate_table_reconstruction
                and group.mode in {"TEXT_TABLE_FAST", "TEXT_TABLE_ACCURATE"}
            ):
                group_segments, group_warnings = convert_table_group_with_coordinate_fallback(
                    group,
                    group_preflight,
                )
                segments.extend(group_segments)
                warnings.extend(group_warnings)
                base_markdown = "\n\n".join(segment.markdown for segment in group_segments)
                append_segments, append_warnings = process_append_pages(
                    group_preflight=group_preflight,
                    base_markdown=base_markdown,
                )
                segments.extend(append_segments)
                warnings.extend(append_warnings)
                continue
            use_coordinate = (
                options.use_coordinate_table_reconstruction
                and group.mode == "TEXT_TABLE_ACCURATE"
            )
            if use_coordinate:
                markdown, tables, _table_text, table_warnings = convert_coordinate_tables_range(
                    pdf_path,
                    group.start_page,
                    group.end_page,
                )
            else:
                converter = converters.get(group.mode)
                if converter is None:
                    converter = build_standard_converter(group.mode)
                    converters[group.mode] = converter
                markdown, tables, _table_text, table_warnings = convert_docling_range(
                    converter,
                    pdf_path,
                    group.start_page,
                    group.end_page,
                    repair_tables=group.mode in {"TEXT_TABLE_FAST", "TEXT_TABLE_ACCURATE"},
                )
            warnings.extend(table_warnings_to_items(table_warnings, mode=group.mode))
            segments.append(
                ConversionSegment(
                    mode=group.mode,
                    start_page=group.start_page,
                    end_page=group.end_page,
                    markdown=markdown,
                    safe_markdown=markdown,
                    tables=tables,
                    elapsed_seconds=round(time.perf_counter() - group_started, 3),
                    diagnostics={
                        "coordinate_table_reconstruction": use_coordinate,
                    },
                )
            )
            if group.mode in {"TEXT_TABLE_FAST", "TEXT_TABLE_ACCURATE"} and not tables:
                for page in group_preflight:
                    if page.table_score >= options.table_score_threshold:
                        warnings.append(
                            WarningItem(
                                page=page.page,
                                mode=group.mode,
                                level="warning",
                                code="TABLE_MISSED",
                                score=0.70,
                                message="テーブル信号がありましたが、構造化テーブルが抽出されませんでした。",
                                suggested_action="必要ならTEXT_TABLE_ACCURATEで再投入してください。",
                                evidence={
                                    "table_score": page.table_score,
                                    "complex_table_score": page.complex_table_score,
                                },
                            )
                        )
            append_pages = [
                page for page in group_preflight if "IMAGE_RECONCILE_APPEND" in page.extra_actions
            ]
            for append_page in append_pages:
                _progress(
                    progress_callback,
                    f"append IMAGE_RECONCILE page {append_page.page}",
                )
                append_ocr_converter: DocumentConverter | None = None
                if options.reconcile_compare_mode == "ocr_vlm":
                    append_table_mode = table_mode_for_pages([append_page])
                    ocr_key = f"IMAGE_RECONCILE:{append_table_mode}"
                    append_ocr_converter = converters.get(ocr_key)
                    if append_ocr_converter is None:
                        append_ocr_converter = build_standard_converter(
                            "IMAGE_RECONCILE",
                            table_mode=append_table_mode,
                        )
                        converters[ocr_key] = append_ocr_converter
                append_vlm_converter = get_vlm_converter(options.model, "primary")
                append_secondary_vlm_converter = (
                    get_vlm_converter(options.secondary_model, "secondary")
                    if options.reconcile_compare_mode == "vlm_vlm"
                    else None
                )
                append_coordinate_evidence = coordinate_evidence_for_vlm_segment(
                    append_page,
                    require_embedded_visual_overlap=True,
                )
                append_segment, append_warnings = convert_reconciled_page(
                    pdf_path=pdf_path,
                    page_preflight=append_page,
                    ocr_converter=append_ocr_converter,
                    vlm_converter=append_vlm_converter,
                    secondary_vlm_converter=append_secondary_vlm_converter,
                    compare_mode=options.reconcile_compare_mode,
                    primary_vlm_model=options.model,
                    secondary_vlm_model=options.secondary_model,
                    segment_mode="IMAGE_RECONCILE_APPEND",
                    scratch_dir=scratch_dir,
                    crop_scale=options.vlm_scale,
                    crop_margin_points=options.embedded_visual_crop_margin_points,
                    parallel_reconcile_candidates=options.parallel_reconcile_candidates,
                    enable_reconcile_table_fallback=options.enable_reconcile_table_fallback,
                    table_fallback_converter=(
                        get_reconcile_table_fallback_converter()
                        if options.enable_reconcile_table_fallback
                        else None
                    ),
                    table_fallback_model=options.reconcile_table_fallback_model,
                    enable_vlm_coordinate_quality_check=options.enable_vlm_coordinate_quality_check,
                    enable_vlm_coordinate_auto_correct=options.enable_vlm_coordinate_auto_correct,
                    coordinate_markdown=str(append_coordinate_evidence.get("markdown") or "")
                    if append_coordinate_evidence.get("applicable")
                    else "",
                    coordinate_quality=append_coordinate_evidence.get("quality")
                    if append_coordinate_evidence.get("applicable")
                    else None,
                    coordinate_diagnostics=append_coordinate_evidence.get("diagnostics")
                    if append_coordinate_evidence.get("applicable")
                    else {
                        "status": "not_applicable",
                        "reason": append_coordinate_evidence.get("reason"),
                    },
                )
                append_segment.safe_markdown = clean_append_markdown(
                    append_segment.safe_markdown,
                    markdown,
                )
                append_segment.markdown = clean_append_markdown(
                    append_segment.markdown,
                    markdown,
                )
                append_segment.tables = filter_append_tables(append_segment.tables, markdown)
                segments.append(append_segment)
                warnings.extend(append_warnings)
            continue

        ocr_converter: DocumentConverter | None = None
        if options.reconcile_compare_mode == "ocr_vlm":
            ocr_table_mode = table_mode_for_pages(group_preflight)
            ocr_converter = build_standard_converter("IMAGE_RECONCILE", table_mode=ocr_table_mode)
        vlm_converter = get_vlm_converter(options.model, "primary")
        secondary_vlm_converter = (
            get_vlm_converter(options.secondary_model, "secondary")
            if options.reconcile_compare_mode == "vlm_vlm"
            else None
        )
        for page_number in group.pages:
            page_preflight = next(page for page in preflight if page.page == page_number)
            page_coordinate_evidence = coordinate_evidence_for_vlm_segment(
                page_preflight,
                require_embedded_visual_overlap=False,
            )
            segment, page_warnings = convert_reconciled_page(
                pdf_path=pdf_path,
                page_preflight=page_preflight,
                ocr_converter=ocr_converter,
                vlm_converter=vlm_converter,
                secondary_vlm_converter=secondary_vlm_converter,
                compare_mode=options.reconcile_compare_mode,
                primary_vlm_model=options.model,
                secondary_vlm_model=options.secondary_model,
                segment_mode="IMAGE_RECONCILE",
                scratch_dir=scratch_dir,
                crop_scale=options.vlm_scale,
                crop_margin_points=options.embedded_visual_crop_margin_points,
                parallel_reconcile_candidates=options.parallel_reconcile_candidates,
                enable_reconcile_table_fallback=options.enable_reconcile_table_fallback,
                table_fallback_converter=(
                    get_reconcile_table_fallback_converter()
                    if options.enable_reconcile_table_fallback
                    else None
                ),
                table_fallback_model=options.reconcile_table_fallback_model,
                enable_vlm_coordinate_quality_check=options.enable_vlm_coordinate_quality_check,
                enable_vlm_coordinate_auto_correct=options.enable_vlm_coordinate_auto_correct,
                coordinate_markdown=str(page_coordinate_evidence.get("markdown") or "")
                if page_coordinate_evidence.get("applicable")
                else "",
                coordinate_quality=page_coordinate_evidence.get("quality")
                if page_coordinate_evidence.get("applicable")
                else None,
                coordinate_diagnostics=page_coordinate_evidence.get("diagnostics")
                if page_coordinate_evidence.get("applicable")
                else {
                    "status": "not_applicable",
                    "reason": page_coordinate_evidence.get("reason"),
                },
            )
            warnings.extend(page_warnings)
            segments.append(segment)

    if standard_executor is not None:
        standard_executor.shutdown(wait=True)

    segments = sorted(
        segments,
        key=lambda segment: (
            segment.start_page,
            1 if segment.mode == "IMAGE_RECONCILE_APPEND" else 0,
            segment.end_page,
        ),
    )
    safe_markdown = "\n\n".join(
        f"<!-- source_pages: {segment.start_page}-{segment.end_page}, mode: {segment.mode} -->\n\n{segment.safe_markdown}".strip()
        for segment in segments
        if segment.safe_markdown.strip()
    )
    raw_markdown = "\n\n".join(
        f"<!-- source_pages: {segment.start_page}-{segment.end_page}, mode: {segment.mode} -->\n\n{segment.markdown}".strip()
        for segment in segments
        if segment.markdown.strip()
    )
    raw_ocr_markdown = "\n\n".join(
        f"<!-- page: {segment.start_page}, source: {source_a_label} -->\n\n{segment.raw_ocr_markdown}".strip()
        for segment in segments
        if segment.raw_ocr_markdown.strip()
    )
    raw_vlm_markdown = "\n\n".join(
        f"<!-- page: {segment.start_page}, source: {source_b_label} -->\n\n{segment.raw_vlm_markdown}".strip()
        for segment in segments
        if segment.raw_vlm_markdown.strip()
    )
    tables: list[dict[str, Any]] = []
    for segment in segments:
        for table in segment.tables:
            adjusted = dict(table)
            adjusted["index"] = len(tables) + 1
            adjusted["source_start_page"] = segment.start_page
            adjusted["source_end_page"] = segment.end_page
            adjusted["mode"] = segment.mode
            tables.append(adjusted)

    elapsed = time.perf_counter() - started
    warning_counts = Counter(warning.code for warning in warnings)
    level_counts = Counter(warning.level for warning in warnings)
    segment_timing_by_mode: Counter[str] = Counter()
    candidate_timing_seconds: Counter[str] = Counter()
    page_timing_estimates: dict[int, dict[str, Any]] = {}
    for segment in segments:
        segment_timing_by_mode[segment.mode] += float(segment.elapsed_seconds or 0.0)
        page_count = max(segment.end_page - segment.start_page + 1, 1)
        per_page_seconds = float(segment.elapsed_seconds or 0.0) / page_count
        for page_number in range(segment.start_page, segment.end_page + 1):
            page_entry = page_timing_estimates.setdefault(
                page_number,
                {"page": page_number, "estimated_seconds": 0.0, "segments": []},
            )
            page_entry["estimated_seconds"] += per_page_seconds
            page_entry["segments"].append(
                {
                    "mode": segment.mode,
                    "estimated_seconds": round(per_page_seconds, 3),
                    "candidate_timings_seconds": segment.candidate_timings_seconds,
                }
            )
        for source, seconds in segment.candidate_timings_seconds.items():
            candidate_timing_seconds[source] += float(seconds or 0.0)
    metadata = {
        "run_id": run_id,
        "pdf_path": str(pdf_path),
        "filename": pdf_path.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "page_count": len(preflight),
        "groups": [asdict(group) for group in groups],
        "options": asdict(options),
        "comparison": {
            "mode": options.reconcile_compare_mode,
            "source_a_label": source_a_label,
            "source_b_label": source_b_label,
            "primary_model": options.model,
            "secondary_model": options.secondary_model
            if options.reconcile_compare_mode == "vlm_vlm"
            else None,
        },
        "preflight_seconds": round(preflight_seconds, 3),
        "total_seconds": round(elapsed, 3),
        "seconds_per_page": round(elapsed / len(preflight), 3) if preflight else None,
        "segment_timing_seconds_by_mode": {
            key: round(value, 3) for key, value in segment_timing_by_mode.items()
        },
        "candidate_timing_seconds": {
            key: round(value, 3) for key, value in candidate_timing_seconds.items()
        },
        "page_timing_estimates": [
            {
                **entry,
                "estimated_seconds": round(float(entry["estimated_seconds"]), 3),
            }
            for _page, entry in sorted(page_timing_estimates.items())
        ],
        "mode_page_counts": dict(Counter(page.mode for page in preflight)),
        "extra_action_counts": dict(
            Counter(action for page in preflight for action in page.extra_actions)
        ),
        "embedded_visual_page_count": sum(
            1 for page in preflight if "IMAGE_RECONCILE_APPEND" in page.extra_actions
        ),
        "warning_counts": dict(warning_counts),
        "warning_level_counts": dict(level_counts),
        "safe_mask_count": warning_counts.get("MASKED_AS_UNKNOWN", 0),
        "safe_unknown_token_count": safe_markdown.count(UNKNOWN_TOKEN),
        "reconcile_table_fallback_applied_count": warning_counts.get(
            "RECONCILE_TABLE_FALLBACK_APPLIED", 0
        ),
        "reconcile_table_fallback_skipped_count": warning_counts.get(
            "RECONCILE_TABLE_FALLBACK_SKIPPED", 0
        ),
    }
    result = {
        "metadata": metadata,
        "preflight": [asdict(page) for page in preflight],
        "groups": [asdict(group) for group in groups],
        "warnings": [asdict(warning) for warning in warnings],
        "segments": [asdict(segment) for segment in segments],
        "safe_markdown": safe_markdown,
        "raw_markdown": raw_markdown,
        "raw_ocr_markdown": raw_ocr_markdown,
        "raw_vlm_markdown": raw_vlm_markdown,
        "tables": tables,
        "run_dir": str(run_dir),
    }
    if options.save_outputs:
        write_routed_outputs(run_dir, result)
    return result


def write_rows_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_fieldnames = fieldnames or sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not resolved_fieldnames:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=resolved_fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in resolved_fieldnames})


def flatten_preflight_row(row: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(row)
    flattened["reasons"] = ",".join(row.get("reasons") or [])
    return flattened


def flatten_warning_row(row: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(row)
    flattened["evidence"] = json.dumps(row.get("evidence") or {}, ensure_ascii=False)
    return flattened


def write_routed_outputs(run_dir: Path, result: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(result["metadata"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "preflight.json").write_text(
        json.dumps(result["preflight"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "warnings.json").write_text(
        json.dumps(result["warnings"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "segments.json").write_text(
        json.dumps(result["segments"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "tables.json").write_text(
        json.dumps(result["tables"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "safe_output.md").write_text(result["safe_markdown"], encoding="utf-8")
    (run_dir / "raw_output.md").write_text(result["raw_markdown"], encoding="utf-8")
    (run_dir / "raw_ocr_output.md").write_text(result["raw_ocr_markdown"], encoding="utf-8")
    (run_dir / "raw_vlm_output.md").write_text(result["raw_vlm_markdown"], encoding="utf-8")
    (run_dir / "raw_candidate_a_output.md").write_text(
        result["raw_ocr_markdown"], encoding="utf-8"
    )
    (run_dir / "raw_candidate_b_output.md").write_text(
        result["raw_vlm_markdown"], encoding="utf-8"
    )
    write_rows_csv(
        run_dir / "preflight.csv",
        [flatten_preflight_row(row) for row in result["preflight"]],
    )
    if result["warnings"]:
        write_rows_csv(
            run_dir / "warnings.csv",
            [flatten_warning_row(row) for row in result["warnings"]],
        )
    else:
        write_rows_csv(
            run_dir / "warnings.csv",
            [],
            fieldnames=[
                "page",
                "mode",
                "level",
                "code",
                "score",
                "message",
                "suggested_action",
                "target",
                "evidence",
            ],
        )
