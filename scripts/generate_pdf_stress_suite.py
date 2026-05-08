from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from settings import load_settings  # noqa: E402

SETTINGS = load_settings()
OUT_DIR = SETTINGS.outputs.root / "pdf_stress_suite"
PDF_DIR = OUT_DIR / "pdfs"
IMG_DIR = OUT_DIR / "_images"
MANIFEST_PATH = OUT_DIR / "manifest.json"


def ensure_dirs() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)


def register_fonts() -> None:
    for name in ("HeiseiKakuGo-W5", "HeiseiMin-W3"):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(name))
        except Exception:
            pass


def make_canvas(path: Path, page_size: tuple[float, float] = A4) -> canvas.Canvas:
    return canvas.Canvas(str(path), pagesize=page_size, invariant=1)


def draw_doc_header(
    c: canvas.Canvas,
    title: str,
    case_id: str,
    page_no: int,
    page_count: int,
    page_size: tuple[float, float],
) -> None:
    width, height = page_size
    c.setFillColor(colors.HexColor("#263238"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(12 * mm, height - 11 * mm, f"{case_id} | {title}")
    c.setFont("Helvetica", 7)
    c.drawRightString(width - 12 * mm, height - 11 * mm, f"Page {page_no} of {page_count}")
    c.setStrokeColor(colors.HexColor("#B0BEC5"))
    c.line(12 * mm, height - 14 * mm, width - 12 * mm, height - 14 * mm)


def fitted_text(value: str, font: str, size: float, max_width: float) -> str:
    text = str(value)
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "..."
    if max_width <= pdfmetrics.stringWidth(ellipsis, font, size):
        return ""
    while text and pdfmetrics.stringWidth(f"{text}{ellipsis}", font, size) > max_width:
        text = text[:-1]
    return f"{text}{ellipsis}" if text else ""


def draw_cell(
    c: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    *,
    font: str = "Helvetica",
    size: float = 6.0,
    fill: colors.Color | None = None,
    bold: bool = False,
    align: str = "left",
    stroke: colors.Color = colors.HexColor("#78909C"),
) -> None:
    if fill is not None:
        c.setFillColor(fill)
        c.rect(x, y, width, height, stroke=0, fill=1)
    c.setStrokeColor(stroke)
    c.rect(x, y, width, height, stroke=1, fill=0)
    font_name = "Helvetica-Bold" if bold and font == "Helvetica" else font
    c.setFont(font_name, size)
    c.setFillColor(colors.HexColor("#263238"))
    lines = str(text).splitlines() or [""]
    max_lines = max(1, int((height - 3) / (size + 1)))
    visible = lines[:max_lines]
    start_y = y + height - size - 2
    if len(visible) == 1:
        start_y = y + (height - size) / 2
    for index, line in enumerate(visible):
        fitted = fitted_text(line, font_name, size, width - 4)
        if align == "right":
            tx = x + width - 2 - pdfmetrics.stringWidth(fitted, font_name, size)
        elif align == "center":
            tx = x + (width - pdfmetrics.stringWidth(fitted, font_name, size)) / 2
        else:
            tx = x + 2
        c.drawString(tx, start_y - index * (size + 1), fitted)


def draw_table(
    c: canvas.Canvas,
    x: float,
    y: float,
    col_widths: list[float],
    row_height: float,
    headers: list[str],
    rows: list[list[str]],
    *,
    font: str = "Helvetica",
    size: float = 6.0,
    header_fill: colors.Color = colors.HexColor("#E3F2FD"),
) -> float:
    current_y = y
    left = x
    for width, header in zip(col_widths, headers, strict=True):
        draw_cell(c, left, current_y - row_height, width, row_height, header, font=font, size=size, fill=header_fill, bold=True, align="center")
        left += width
    current_y -= row_height
    for row_index, row in enumerate(rows):
        left = x
        fill = colors.HexColor("#FAFAFA") if row_index % 2 else None
        for width, value in zip(col_widths, row, strict=True):
            align = "right" if any(ch.isdigit() for ch in str(value)) and len(str(value)) <= 12 else "left"
            draw_cell(c, left, current_y - row_height, width, row_height, str(value), font=font, size=size, fill=fill, align=align)
            left += width
        current_y -= row_height
    return current_y


def draw_wrapped(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    *,
    font: str = "Helvetica",
    size: float = 8.0,
    leading: float = 10.0,
) -> float:
    c.setFont(font, size)
    c.setFillColor(colors.HexColor("#263238"))
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            line = candidate
            continue
        if line:
            c.drawString(x, y, line)
            y -= leading
        line = word
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


def add_case(
    cases: list[dict[str, Any]],
    *,
    case_id: str,
    filename: str,
    description: str,
    pages: int,
    expected_anchors: list[str],
    tags: list[str],
    source_url: str | None = None,
) -> None:
    cases.append(
        {
            "case_id": case_id,
            "filename": filename,
            "description": description,
            "pages": pages,
            "expected_anchors": sorted(set(expected_anchors)),
            "tags": tags,
            "source_url": source_url,
        }
    )


def build_invoice(cases: list[dict[str, Any]]) -> None:
    case_id = "S01"
    filename = "s01_multicurrency_invoice_2p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    for page in range(1, 3):
        draw_doc_header(c, "Multi-currency invoice", case_id, page, 2, A4)
        width, height = A4
        token = f"INV-S01-2026-P{page:02d}"
        anchors.append(token)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(14 * mm, height - 30 * mm, f"Invoice {token}")
        c.setFont("Helvetica", 8)
        c.drawString(14 * mm, height - 38 * mm, "Bill to: Northwind Trading / Currency: USD, EUR, JPY / VAT reverse-charge")
        rows = []
        for row in range(1, 9):
            line_id = f"S01-LINE-P{page:02d}-{row:02d}"
            anchors.append(line_id)
            rows.append([line_id, f"SKU-{page}{row:03d}", f"Service bundle {row}", str(row + 1), f"{110 + row * 7}.50", f"{(row + 1) * (110 + row * 7.5):,.2f}", "10%"])
        draw_table(c, 14 * mm, height - 55 * mm, [30 * mm, 24 * mm, 54 * mm, 16 * mm, 24 * mm, 28 * mm, 16 * mm], 8 * mm, ["Line", "SKU", "Description", "Qty", "Unit", "Amount", "Tax"], rows, size=5.8)
        c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Two-page invoice with currency, VAT, line IDs, and numeric table.", pages=2, expected_anchors=anchors, tags=["invoice", "table", "multi-page", "numeric"])


def build_bank_statement(cases: list[dict[str, Any]]) -> None:
    case_id = "S02"
    filename = "s02_bank_statement_transactions_3p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    headers = ["Date", "Description", "Ref", "Debit", "Credit", "Balance"]
    for page in range(1, 4):
        draw_doc_header(c, "Bank statement", case_id, page, 3, A4)
        width, height = A4
        acct = f"ACCT-S02-88{page}771"
        anchors.append(acct)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(14 * mm, height - 30 * mm, f"Operating account statement {acct}")
        rows = []
        for row in range(1, 18):
            tx = f"TX-S02-P{page:02d}-R{row:02d}"
            anchors.append(tx)
            debit = f"{row * 19.23:,.2f}" if row % 3 else ""
            credit = f"{row * 87.41:,.2f}" if row % 3 == 0 else ""
            rows.append([f"2026-05-{row:02d}", f"Counterparty memo {row}", tx, debit, credit, f"{12000 + page * row * 11.2:,.2f}"])
        draw_table(c, 14 * mm, height - 47 * mm, [23 * mm, 54 * mm, 35 * mm, 25 * mm, 25 * mm, 30 * mm], 7.2 * mm, headers, rows, size=5.2)
        c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Three-page bank statement with transaction IDs, blank debit/credit cells, and running balances.", pages=3, expected_anchors=anchors, tags=["bank-statement", "transactions", "multi-page", "numeric"])


def build_dense_ledger(cases: list[dict[str, Any]]) -> None:
    case_id = "S03"
    filename = "s03_dense_general_ledger_4p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path, landscape(A4))
    page_size = landscape(A4)
    anchors: list[str] = []
    headers = ["#", "Period", "Account", "Dept", "Doc ID", "Dr", "Cr", "Tax", "FX", "Net", "Status", "Comment"]
    widths = [8, 14, 21, 18, 28, 18, 18, 14, 14, 18, 18, 54]
    col_widths = [w * mm for w in widths]
    for page in range(1, 5):
        draw_doc_header(c, "Dense general ledger", case_id, page, 4, page_size)
        width, height = page_size
        book = f"GL-S03-BOOK-P{page:02d}"
        anchors.append(book)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(10 * mm, height - 28 * mm, book)
        y = height - 38 * mm
        rows = []
        for row in range(1, 24):
            global_row = (page - 1) * 23 + row
            doc_id = f"GL-S03-R{global_row:04d}"
            anchors.append(doc_id)
            rows.append([str(global_row), f"2026-{(row % 12) + 1:02d}", f"4{row:03d}", f"D{row % 8}", doc_id, f"{row * 101:,}", f"{row * 97:,}", "10%", f"{(-1) ** row * (row % 9):+}", f"{row * 203:,}", "posted", f"memo with cost center {global_row:04d}"])
        draw_table(c, 8 * mm, y, col_widths, 5.8 * mm, headers, rows, size=4.2)
        c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Four-page landscape dense ledger with many rows and small table text.", pages=4, expected_anchors=anchors, tags=["ledger", "dense", "landscape", "wide-table"])


def build_split_age_table(cases: list[dict[str, Any]]) -> None:
    case_id = "S04"
    filename = "s04_stacked_wide_age_tables_1p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors = ["AGE-S04-TOP", "AGE-S04-BOTTOM", "AGE-S04-FOOTNOTE"]
    width, height = A4
    draw_doc_header(c, "Stacked wide age tables", case_id, 1, 1, A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, height - 28 * mm, "AGE-S04-TOP / Population by age band")
    headers_top = ["Year", "Total", "0-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39", "40-44"]
    headers_bottom = ["Year", "45-49", "50-54", "55-59", "60-64", "65-69", "70-74", "75-79", "80-84", "85-89", "90+"]
    col_widths = [28 * mm] + [16 * mm] * 10
    def age_rows(prefix: str) -> list[list[str]]:
        rows = []
        for section in ["Total", "Male", "Female"]:
            rows.append([section] + [""] * 10)
            for year in ["2015", "2020", "2026"]:
                rows.append([year, *[f"{1000 + len(rows) * 31 + col * 13:,}" for col in range(10)]])
        return rows
    draw_table(c, 12 * mm, height - 48 * mm, col_widths, 6.5 * mm, headers_top, age_rows("TOP"), size=4.8)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(12 * mm, height - 158 * mm, "AGE-S04-BOTTOM")
    draw_table(c, 12 * mm, height - 166 * mm, col_widths, 6.5 * mm, headers_bottom, age_rows("BOT"), size=4.8)
    c.setFont("Helvetica", 6)
    c.drawString(12 * mm, 20 * mm, "AGE-S04-FOOTNOTE: two stacked tables share the same stub column and repeated year bands.")
    c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Two stacked wide tables with repeated stubs and age bands.", pages=1, expected_anchors=anchors, tags=["stacked-tables", "wide-table", "stub-column"])


def build_side_by_side(cases: list[dict[str, Any]]) -> None:
    case_id = "S05"
    filename = "s05_side_by_side_kpi_tables_1p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path, landscape(A4))
    page_size = landscape(A4)
    width, height = page_size
    anchors = ["S05-LEFT-KPI", "S05-RIGHT-KPI"]
    draw_doc_header(c, "Side-by-side KPI tables", case_id, 1, 1, page_size)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(12 * mm, height - 30 * mm, "S05-LEFT-KPI and S05-RIGHT-KPI")
    headers = ["Metric", "Jan", "Feb", "Mar", "Delta"]
    rows_left = []
    rows_right = []
    for row in range(1, 12):
        left_id = f"S05-L-{row:02d}"
        right_id = f"S05-R-{row:02d}"
        anchors.extend([left_id, right_id])
        rows_left.append([left_id, f"{row * 10}", f"{row * 11}", f"{row * 12}", f"{row:+}"])
        rows_right.append([right_id, f"{row * 7}", f"{row * 8}", f"{row * 9}", f"{-row:+}"])
    draw_table(c, 12 * mm, height - 45 * mm, [33 * mm, 20 * mm, 20 * mm, 20 * mm, 20 * mm], 7 * mm, headers, rows_left, size=5)
    draw_table(c, 152 * mm, height - 45 * mm, [33 * mm, 20 * mm, 20 * mm, 20 * mm, 20 * mm], 7 * mm, headers, rows_right, size=5)
    c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Two independent side-by-side tables on a landscape page.", pages=1, expected_anchors=anchors, tags=["side-by-side", "tables", "landscape"])


def build_merged_header_table(cases: list[dict[str, Any]]) -> None:
    case_id = "S06"
    filename = "s06_merged_header_financial_table_2p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path, landscape(A4))
    page_size = landscape(A4)
    anchors = ["S06-HDR-GROUP-A", "S06-HDR-GROUP-B"]
    widths = [22, 24, 28, 22, 22, 22, 22, 22, 22, 45]
    col_widths = [w * mm for w in widths]
    x0 = 10 * mm
    for page in range(1, 3):
        draw_doc_header(c, "Merged multi-row header", case_id, page, 2, page_size)
        width, height = page_size
        y = height - 38 * mm
        groups = [(0, 3, f"Identity {anchors[0] if page == 1 else ''}", colors.HexColor("#E8EEF7")), (3, 6, f"Measures {anchors[1] if page == 1 else ''}", colors.HexColor("#E8F5E9"))]
        for start, span, label, fill in groups:
            draw_cell(c, x0 + sum(col_widths[:start]), y - 7 * mm, sum(col_widths[start:start + span]), 7 * mm, label.strip(), size=5.5, fill=fill, bold=True, align="center")
        y -= 7 * mm
        headers = ["Region", "Unit", "Scenario", "Open", "Add", "Loss", "FX", "Close", "Margin", "Note"]
        left = x0
        for width_pt, header in zip(col_widths, headers, strict=True):
            draw_cell(c, left, y - 7 * mm, width_pt, 7 * mm, header, size=5, fill=colors.HexColor("#CFD8DC"), bold=True, align="center")
            left += width_pt
        y -= 7 * mm
        for row in range(1, 19):
            row_id = f"S06-P{page:02d}-R{row:02d}"
            anchors.append(row_id)
            values = ["JP" if row % 2 else "US", f"U{row:02d}", "base", f"{900 + row}", f"{row * 3}", f"({row})", f"{(-1) ** row * row:+}", f"{930 + row}", f"{22 + row / 10:.1f}%", row_id]
            left = x0
            for col_index, (width_pt, value) in enumerate(zip(col_widths, values, strict=True)):
                draw_cell(c, left, y - 6 * mm, width_pt, 6 * mm, value, size=4.6, align="right" if 3 <= col_index <= 8 else "left")
                left += width_pt
            y -= 6 * mm
        c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Landscape financial table with merged-style header bands and dense numeric rows.", pages=2, expected_anchors=anchors, tags=["merged-header", "financial-table", "landscape"])


def build_rowspan_table(cases: list[dict[str, Any]]) -> None:
    case_id = "S07"
    filename = "s07_vertical_rowspan_schedule_1p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path, landscape(A4))
    page_size = landscape(A4)
    anchors = ["S07-ROWSPAN-NORTH", "S07-ROWSPAN-SOUTH", "S07-ROWSPAN-EAST"]
    width, height = page_size
    draw_doc_header(c, "Vertical rowspan schedule", case_id, 1, 1, page_size)
    x0 = 14 * mm
    y = height - 38 * mm
    col_widths = [42 * mm, 34 * mm, 34 * mm, 34 * mm, 42 * mm, 72 * mm]
    headers = ["Region", "Team", "Window", "Load", "Owner", "Note"]
    left = x0
    for width_pt, header in zip(col_widths, headers, strict=True):
        draw_cell(c, left, y - 8 * mm, width_pt, 8 * mm, header, size=5.5, fill=colors.HexColor("#CFD8DC"), bold=True, align="center")
        left += width_pt
    y -= 8 * mm
    groups = [("North S07-ROWSPAN-NORTH", 2), ("South S07-ROWSPAN-SOUTH", 3), ("East S07-ROWSPAN-EAST", 2)]
    row_id = 0
    for group_name, span in groups:
        draw_cell(c, x0, y - span * 8 * mm, col_widths[0], span * 8 * mm, group_name, size=5.4, fill=colors.HexColor("#E8EEF7"), bold=True, align="center")
        for sub in range(span):
            row_id += 1
            item = f"S07-TASK-{row_id:02d}"
            anchors.append(item)
            left = x0 + col_widths[0]
            values = [f"Team {sub + 1}", f"{8 + row_id}:00", str(100 + row_id), f"Owner {row_id}", item]
            for width_pt, value in zip(col_widths[1:], values, strict=True):
                draw_cell(c, left, y - 8 * mm, width_pt, 8 * mm, value, size=5.2)
                left += width_pt
            y -= 8 * mm
    c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Real vertical merged cells in a first-column rowspan schedule.", pages=1, expected_anchors=anchors, tags=["rowspan", "vertical-merge", "table"])


def build_forms_and_text(cases: list[dict[str, Any]]) -> None:
    specs = [
        ("S08", "s08_application_checkboxes_2p.pdf", "Application checkboxes", ["form", "checkbox", "multi-page"]),
        ("S09", "s09_contract_two_column_3p.pdf", "Two-column contract", ["contract", "two-column", "paragraphs"]),
        ("S10", "s10_email_export_quote_table_2p.pdf", "Email export quote table", ["email", "quoted-text", "table"]),
        ("S11", "s11_lab_report_units_1p.pdf", "Lab report units", ["lab-report", "units", "table"]),
        ("S12", "s12_redacted_payroll_1p.pdf", "Redacted payroll", ["payroll", "redaction", "table"]),
    ]
    for case_id, filename, title, tags in specs:
        pages = 3 if case_id == "S09" else (2 if case_id in {"S08", "S10"} else 1)
        path = PDF_DIR / filename
        c = make_canvas(path)
        anchors: list[str] = []
        for page in range(1, pages + 1):
            draw_doc_header(c, title, case_id, page, pages, A4)
            width, height = A4
            token = f"{case_id}-DOC-P{page:02d}"
            anchors.append(token)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(14 * mm, height - 30 * mm, f"{title} {token}")
            y = height - 42 * mm
            if "contract" in tags:
                for col in range(2):
                    yy = y
                    for para in range(1, 9):
                        clause = f"{case_id}-CLAUSE-P{page:02d}-{col + 1}-{para:02d}"
                        anchors.append(clause)
                        yy = draw_wrapped(c, f"{clause} The parties agree that service levels, audit trails, and exception handling remain enforceable for operational evidence.", 14 * mm + col * 92 * mm, yy, 82 * mm, size=6.2, leading=7.2)
                        yy -= 1.5 * mm
            elif "checkbox" in tags:
                rows = []
                for row in range(1, 13):
                    item = f"{case_id}-FIELD-P{page:02d}-{row:02d}"
                    anchors.append(item)
                    rows.append([item, "[x]" if row % 3 == 0 else "[ ]", f"Applicant value {row}", "OK" if row % 2 else "Review"])
                draw_table(c, 14 * mm, y, [54 * mm, 16 * mm, 72 * mm, 34 * mm], 8 * mm, ["Field", "Set", "Value", "Status"], rows, size=5.8)
            else:
                rows = []
                for row in range(1, 10):
                    item = f"{case_id}-ROW-P{page:02d}-{row:02d}"
                    anchors.append(item)
                    rows.append([item, f"{row * 11.7:.1f}", "mg/dL" if case_id == "S11" else "JPY", "high" if row % 4 == 0 else "normal"])
                draw_table(c, 14 * mm, y, [52 * mm, 32 * mm, 32 * mm, 42 * mm], 8 * mm, ["Item", "Value", "Unit", "Flag"], rows, size=5.8)
                if case_id == "S12":
                    c.setFillColor(colors.black)
                    c.rect(120 * mm, height - 36 * mm, 52 * mm, 8 * mm, stroke=0, fill=1)
                    redacted = "S12-REDACTED-PAY-ID"
                    anchors.append(redacted)
                    c.setFillColor(colors.white)
                    c.setFont("Helvetica-Bold", 6)
                    c.drawString(122 * mm, height - 33.5 * mm, redacted)
            c.showPage()
        c.save()
        add_case(cases, case_id=case_id, filename=filename, description=title, pages=pages, expected_anchors=anchors, tags=tags)


def build_japanese_cases(cases: list[dict[str, Any]]) -> None:
    for case_id, filename, title, vertical in [
        ("S13", "s13_japanese_tax_notice_2p.pdf", "日本語税務通知", False),
        ("S14", "s14_japanese_vertical_mixed_1p.pdf", "日本語縦書き混在", True),
    ]:
        path = PDF_DIR / filename
        c = make_canvas(path)
        pages = 2 if case_id == "S13" else 1
        anchors: list[str] = []
        for page in range(1, pages + 1):
            draw_doc_header(c, title, case_id, page, pages, A4)
            width, height = A4
            token = f"{case_id}-JP-P{page:02d}"
            anchors.append(token)
            c.setFont("HeiseiKakuGo-W5", 13)
            c.drawString(14 * mm, height - 30 * mm, f"{title} {token}")
            headers = ["区分", "令和5年", "令和6年", "増減", "備考"]
            rows = []
            for row in range(1, 12):
                row_id = f"{case_id}-明細-{page:02d}-{row:02d}"
                anchors.append(row_id)
                rows.append([row_id, f"{1000 + row * 13:,}", f"{1100 + row * 17:,}", f"{row:+}", "確認済"])
            draw_table(c, 14 * mm, height - 48 * mm, [44 * mm, 30 * mm, 30 * mm, 24 * mm, 48 * mm], 8 * mm, headers, rows, font="HeiseiKakuGo-W5", size=5.6)
            if vertical:
                c.setFont("HeiseiKakuGo-W5", 8)
                for i, ch in enumerate("縦書き注記S14"):
                    c.drawString(182 * mm, height - (50 + i * 6) * mm, ch)
                anchors.append("S14")
            c.showPage()
        c.save()
        add_case(cases, case_id=case_id, filename=filename, description=title, pages=pages, expected_anchors=anchors, tags=["japanese", "table", "vertical-text" if vertical else "notice"])


def build_visual_cases(cases: list[dict[str, Any]]) -> None:
    stamp_img = IMG_DIR / "s15_stamp.png"
    image = Image.new("RGBA", (420, 180), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((25, 20, 165, 160), outline=(190, 0, 0, 210), width=8)
    draw.text((54, 78), "S15-STAMP", fill=(190, 0, 0, 230))
    draw.rectangle((190, 38, 395, 138), outline=(0, 75, 170, 220), width=5)
    draw.text((216, 82), "S15-RECEIVED", fill=(0, 75, 170, 230))
    image.save(stamp_img)

    case_id = "S15"
    filename = "s15_text_pdf_image_layer_stamps_1p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors = ["S15-BODY-TEXT", "S15-STAMP", "S15-RECEIVED"]
    width, height = A4
    draw_doc_header(c, "Image-layer stamps", case_id, 1, 1, A4)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(14 * mm, height - 30 * mm, "Approval package S15-BODY-TEXT")
    rows = [[f"S15-TABLE-R{row:02d}", f"{row * 10}", "approved" if row % 2 else "pending"] for row in range(1, 9)]
    anchors.extend(row[0] for row in rows)
    draw_table(c, 14 * mm, height - 48 * mm, [52 * mm, 28 * mm, 40 * mm], 9 * mm, ["Ref", "Value", "State"], rows, size=5.8)
    c.drawImage(str(stamp_img), 92 * mm, height - 96 * mm, width=82 * mm, height=35 * mm, mask="auto")
    c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Text-layer table with overlaid raster image-layer stamps.", pages=1, expected_anchors=anchors, tags=["embedded-image", "stamp", "mixed-layer"])

    for case_id, filename, noise in [
        ("S16", "s16_clean_image_scan_1p.pdf", 0),
        ("S17", "s17_noisy_image_scan_1p.pdf", 1),
    ]:
        img_path = IMG_DIR / f"{case_id.lower()}_scan.png"
        scan = Image.new("RGB", (1300, 1700), "white")
        draw = ImageDraw.Draw(scan)
        anchors = [f"{case_id}-SCAN-ANCHOR", f"{case_id}-SCAN-TOTAL-9981"]
        y = 80
        draw.text((90, y), f"Scanned receipt {anchors[0]}", fill="black")
        y += 60
        for row in range(1, 22):
            text = f"{case_id}-ITEM-{row:02d}    description {row:02d}    {row * 7.13:0.2f}"
            anchors.append(f"{case_id}-ITEM-{row:02d}")
            draw.text((90, y), text, fill="black")
            y += 54
        draw.text((90, y + 30), f"TOTAL {anchors[1]}", fill="black")
        if noise:
            scan = scan.rotate(1.4, expand=True, fillcolor="white")
            scan = ImageEnhance.Contrast(scan).enhance(0.75)
            scan = scan.filter(ImageFilter.GaussianBlur(radius=0.45))
        scan.save(img_path)
        pdf_path = PDF_DIR / filename
        first = Image.open(img_path).convert("RGB")
        first.save(pdf_path, "PDF", resolution=144)
        add_case(cases, case_id=case_id, filename=filename, description="Image-only scanned receipt, clean" if not noise else "Image-only scanned receipt, skewed and blurred", pages=1, expected_anchors=anchors, tags=["image-only", "scan", "ocr-risk" if noise else "scan"])


def build_misc_cases(cases: list[dict[str, Any]]) -> None:
    specs = [
        ("S18", "s18_shipping_manifest_barcode_1p.pdf", "Shipping manifest with barcode", ["shipping", "barcode", "table"]),
        ("S19", "s19_meeting_minutes_actions_2p.pdf", "Meeting minutes with actions", ["meeting", "actions", "paragraphs"]),
        ("S20", "s20_chart_formula_code_1p.pdf", "Chart formula code mix", ["chart", "formula", "code"]),
        ("S21", "s21_tiny_footnotes_1p.pdf", "Tiny footnotes and micro text", ["tiny-text", "footnotes"]),
        ("S22", "s22_questionnaire_matrix_1p.pdf", "Questionnaire matrix", ["survey", "matrix", "checkbox"]),
        ("S23", "s23_irregular_nested_table_1p.pdf", "Irregular nested table", ["nested-table", "irregular"]),
        ("S24", "s24_landscape_technical_drawing_1p.pdf", "Landscape technical drawing", ["technical-drawing", "rotated-text"]),
    ]
    for case_id, filename, title, tags in specs:
        page_size = landscape(A4) if "landscape" in tags or "technical" in title.lower() else A4
        path = PDF_DIR / filename
        c = make_canvas(path, page_size)
        pages = 2 if case_id == "S19" else 1
        anchors: list[str] = []
        for page in range(1, pages + 1):
            draw_doc_header(c, title, case_id, page, pages, page_size)
            width, height = page_size
            token = f"{case_id}-ANCHOR-P{page:02d}"
            anchors.append(token)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(12 * mm, height - 28 * mm, f"{title} {token}")
            if "barcode" in tags:
                code = code128.Code128(token, barHeight=18 * mm, barWidth=0.35 * mm)
                code.drawOn(c, 12 * mm, height - 55 * mm)
                rows = [[f"{case_id}-SHIP-{row:02d}", f"CTN-{row:04d}", f"{row * 2.5:.1f}", "dock A"] for row in range(1, 12)]
                anchors.extend(row[0] for row in rows)
                draw_table(c, 12 * mm, height - 75 * mm, [46 * mm, 38 * mm, 28 * mm, 42 * mm], 8 * mm, ["Shipment", "Carton", "Kg", "Location"], rows, size=5.5)
            elif "technical" in tags:
                c.setStrokeColor(colors.HexColor("#607D8B"))
                for i in range(10):
                    c.line(18 * mm, (35 + i * 13) * mm, width - 18 * mm, (35 + i * 13) * mm)
                    c.line((20 + i * 25) * mm, 35 * mm, (20 + i * 25) * mm, height - 45 * mm)
                c.saveState()
                c.translate(width - 28 * mm, height - 75 * mm)
                c.rotate(90)
                rot = f"{case_id}-ROTATED-NOTE"
                anchors.append(rot)
                c.setFont("Helvetica", 7)
                c.drawString(0, 0, rot)
                c.restoreState()
                rows = [[f"{case_id}-PART-{row:02d}", "A36", f"{row * 100}x{row * 50}", "OK"] for row in range(1, 8)]
                anchors.extend(row[0] for row in rows)
                draw_table(c, 190 * mm, height - 55 * mm, [28 * mm, 22 * mm, 36 * mm, 22 * mm], 7 * mm, ["Part", "Mat", "Size", "QC"], rows, size=4.8)
            elif "paragraphs" in tags:
                y = height - 42 * mm
                for para in range(1, 11):
                    item = f"{case_id}-ACTION-P{page:02d}-{para:02d}"
                    anchors.append(item)
                    y = draw_wrapped(c, f"{item} Owner confirmed due date, blocker, decision, and follow-up dependency for the steering committee.", 14 * mm, y, 176 * mm, size=7, leading=8)
                    y -= 1 * mm
            else:
                rows = []
                for row in range(1, 14):
                    item = f"{case_id}-ROW-{row:02d}"
                    anchors.append(item)
                    rows.append([item, "A" if row % 2 else "B", "[x]" if row % 3 == 0 else "[ ]", f"{row * 13.37:.2f}", "note / formula =A+B"])
                draw_table(c, 14 * mm, height - 46 * mm, [46 * mm, 24 * mm, 18 * mm, 28 * mm, 64 * mm], 8 * mm, ["ID", "Group", "Set", "Value", "Comment"], rows, size=5.3)
                if case_id == "S21":
                    c.setFont("Helvetica", 3.5)
                    micro = f"{case_id}-MICROTEXT-DO-NOT-DROP"
                    anchors.append(micro)
                    c.drawString(14 * mm, 24 * mm, micro)
            c.showPage()
        c.save()
        add_case(cases, case_id=case_id, filename=filename, description=title, pages=pages, expected_anchors=anchors, tags=tags)


def build_long_register(cases: list[dict[str, Any]]) -> None:
    case_id = "S25"
    filename = "s25_long_register_8p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    headers = ["No", "Asset ID", "Category", "Owner", "Acquired", "Cost", "Dep.", "Book"]
    for page in range(1, 9):
        draw_doc_header(c, "Long fixed asset register", case_id, page, 8, A4)
        width, height = A4
        rows = []
        for row in range(1, 24):
            idx = (page - 1) * 23 + row
            asset = f"S25-ASSET-{idx:04d}"
            anchors.append(asset)
            rows.append([str(idx), asset, "equipment", f"owner-{idx % 7}", f"202{idx % 6}-05-01", f"{idx * 1000:,}", f"{idx * 77:,}", f"{idx * 923:,}"])
        draw_table(c, 12 * mm, height - 34 * mm, [15 * mm, 38 * mm, 28 * mm, 28 * mm, 28 * mm, 25 * mm, 22 * mm, 25 * mm], 6.6 * mm, headers, rows, size=4.9)
        c.showPage()
    c.save()
    add_case(cases, case_id=case_id, filename=filename, description="Eight-page fixed asset register with repeated headers and many numeric rows.", pages=8, expected_anchors=anchors, tags=["long-document", "register", "multi-page", "table"])


def download_pdf(url: str, target: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "docling-poc-stress-suite/1.0", "Accept": "application/pdf,*/*"})
        with urllib.request.urlopen(req, timeout=60) as response:
            target.write_bytes(response.read())
        return True
    except Exception:
        return False


def pdf_page_count(path: Path, fallback: int) -> int:
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(path))
        try:
            return len(document)
        finally:
            document.close()
    except Exception:
        return fallback


def add_web_cases(cases: list[dict[str, Any]], *, skip_web: bool) -> None:
    web_cases = [
        {
            "case_id": "S26",
            "filename": "s26_web_cdc_flu_vis.pdf",
            "url": "https://www.cdc.gov/vaccines/hcp/current-vis/downloads/flulive.pdf",
            "description": "CDC Vaccine Information Statement for live attenuated influenza vaccine.",
            "pages": 2,
            "expected_anchors": [
                "VACCINE INFORMATION STATEMENT",
                "Influenza vaccine can prevent influenza",
                "Live, Attenuated Influenza Vaccine",
                "Vaccine Adverse Event Reporting System",
                "www.cdc.gov/flu",
            ],
            "tags": ["web", "public-health", "two-column", "medical-notice"],
        },
        {
            "case_id": "S27",
            "filename": "s27_web_census_population_estimates_layout.pdf",
            "url": "https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2024/NST-EST2024-ALLDATA.pdf",
            "description": "U.S. Census Bureau population estimates file layout with field definitions and structured rows.",
            "pages": 4,
            "expected_anchors": [
                "NST-EST2024-ALLDATA",
                "Annual Population Estimates",
                "Estimated Components of Resident Population Change",
                "SUMLEV",
                "REGION",
                "POPESTIMATE2024",
            ],
            "tags": ["web", "census", "file-layout", "dense-text", "structured-rows"],
        },
        {
            "case_id": "S28",
            "filename": "s28_web_nist_digital_identity.pdf",
            "url": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-63-4.pdf",
            "description": "NIST SP 800-63-4 Digital Identity Guidelines technical publication.",
            "pages": 96,
            "expected_anchors": [
                "NIST SP 800-63-4",
                "Digital Identity Guidelines",
                "identity proofing",
                "authentication",
                "federation",
                "National Institute of Standards and Technology",
            ],
            "tags": ["web", "technical-standard", "long-document", "dense-text"],
        },
    ]
    for item in web_cases:
        target = PDF_DIR / str(item["filename"])
        downloaded = target.exists()
        if not downloaded and not skip_web:
            downloaded = download_pdf(str(item["url"]), target)
        if not downloaded:
            continue
        add_case(
            cases,
            case_id=str(item["case_id"]),
            filename=str(item["filename"]),
            description=str(item["description"]),
            pages=pdf_page_count(target, int(item["pages"]) or 1),
            expected_anchors=list(item["expected_anchors"]),
            tags=list(item["tags"]),
            source_url=str(item["url"]),
        )


def build_suite(skip_web: bool = False) -> dict[str, Any]:
    ensure_dirs()
    register_fonts()
    random.seed(20260506)
    cases: list[dict[str, Any]] = []
    build_invoice(cases)
    build_bank_statement(cases)
    build_dense_ledger(cases)
    build_split_age_table(cases)
    build_side_by_side(cases)
    build_merged_header_table(cases)
    build_rowspan_table(cases)
    build_forms_and_text(cases)
    build_japanese_cases(cases)
    build_visual_cases(cases)
    build_misc_cases(cases)
    build_long_register(cases)
    add_web_cases(cases, skip_web=skip_web)
    manifest = {
        "generated_at": "2026-05-06",
        "suite": "pdf_stress_suite_practical_diverse_cases",
        "output_dir": str(OUT_DIR),
        "notes": [
            "Generated cases include text-layer PDFs, dense tables, forms, Japanese text, merged headers, vertical rowspans, image-layer stamps, image-only scans, long ledgers, side-by-side tables, and stacked wide tables.",
            "Web cases are official public PDFs when available.",
            "Expected anchors are designed for omission checks, not for semantic correctness scoring.",
        ],
        "cases": cases,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()
    manifest = build_suite(skip_web=args.skip_web)
    print(f"Wrote {len(manifest['cases'])} cases to {MANIFEST_PATH}")
    for case in manifest["cases"]:
        print(f"{case['case_id']} {case['filename']} anchors={len(case['expected_anchors'])} tags={','.join(case['tags'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
