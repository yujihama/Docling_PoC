from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TableShape:
    rows: int
    columns: int


def safe_div(n: float, d: float) -> float:
    return n / d if d else 1.0


def calc_detection_metrics(expected_tables: int, detected_tables: int) -> dict[str, float]:
    tp = min(expected_tables, detected_tables)
    precision = safe_div(tp, detected_tables) if detected_tables else (1.0 if expected_tables == 0 else 0.0)
    recall = safe_div(tp, expected_tables) if expected_tables else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    duplicate_rate = max(detected_tables - expected_tables, 0) / detected_tables if detected_tables else 0.0
    over_detection_penalty = max(detected_tables - expected_tables, 0) / max(expected_tables, 1)
    return {
        "table_detection_precision": precision,
        "table_detection_recall": recall,
        "table_detection_f1": f1,
        "duplicate_table_rate": duplicate_rate,
        "over_detection_penalty": over_detection_penalty,
    }


def table_shapes(tables: list[dict[str, Any]]) -> list[TableShape]:
    return [TableShape(rows=int(t.get("rows", 0)), columns=int(t.get("columns", 0))) for t in tables]


def shape_match_rates(expected_count: int, detected: list[TableShape]) -> dict[str, float]:
    if expected_count <= 0:
        return {
            "row_count_match_rate": 1.0,
            "column_count_match_rate": 1.0,
            "header_match_rate": 1.0,
        }
    comparable = min(expected_count, len(detected))
    if comparable == 0:
        return {
            "row_count_match_rate": 0.0,
            "column_count_match_rate": 0.0,
            "header_match_rate": 0.0,
        }
    row_nonzero = sum(1 for s in detected[:comparable] if s.rows > 0)
    col_nonzero = sum(1 for s in detected[:comparable] if s.columns > 0)
    header_like = sum(1 for s in detected[:comparable] if s.columns > 1 and s.rows > 1)
    return {
        "row_count_match_rate": row_nonzero / comparable,
        "column_count_match_rate": col_nonzero / comparable,
        "header_match_rate": header_like / comparable,
    }


def compute_page_level_score(base_score: float, detection_f1: float, over_penalty: float) -> float:
    score = (0.75 * base_score) + (0.25 * detection_f1) - (0.20 * over_penalty)
    return max(0.0, min(1.0, score))
