from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, JpegImagePlugin
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "docling_benchmark"
PDF_DIR = OUT_DIR / "pdfs"
GT_PATH = OUT_DIR / "ground_truth.json"


def norm_page_size(page_size: tuple[float, float]) -> tuple[float, float]:
    return float(page_size[0]), float(page_size[1])


def draw_header(
    c: canvas.Canvas,
    case_id: str,
    title: str,
    page_no: int,
    page_count: int,
    page_size: tuple[float, float],
) -> None:
    width, height = norm_page_size(page_size)
    c.setFillColor(colors.HexColor("#263238"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(18 * mm, height - 15 * mm, f"{case_id} | {title}")
    c.setFont("Helvetica", 8)
    c.drawRightString(width - 18 * mm, height - 15 * mm, f"Page {page_no} of {page_count}")
    c.setStrokeColor(colors.HexColor("#90A4AE"))
    c.line(18 * mm, height - 18 * mm, width - 18 * mm, height - 18 * mm)
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#607D8B"))
    c.drawString(18 * mm, 12 * mm, "Synthetic benchmark document generated for Docling extraction testing.")
    c.drawRightString(width - 18 * mm, 12 * mm, f"{case_id}-FOOTER-P{page_no:02d}")


def draw_wrapped(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font_name: str = "Helvetica",
    font_size: int = 8,
    leading: int = 10,
) -> float:
    c.setFont(font_name, font_size)
    words = text.split()
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if c.stringWidth(candidate, font_name, font_size) <= max_width:
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


def make_table_data(
    case_id: str,
    page_no: int,
    table_no: int,
    rows: int,
    columns: list[str],
    *,
    rng: random.Random,
    multiline: bool = False,
) -> tuple[list[list[str]], list[str]]:
    data = [columns]
    expected_cells: list[str] = []
    for row_no in range(1, rows + 1):
        row: list[str] = []
        for col_no, column in enumerate(columns, start=1):
            if col_no == 1:
                value = f"{case_id}-P{page_no:02d}-T{table_no}-R{row_no:02d}"
            elif col_no == 2:
                value = f"SKU-{page_no:02d}{table_no}{row_no:02d}-{rng.randint(100, 999)}"
            elif col_no == 3:
                value = f"QTY-{rng.randint(10, 999)}"
            elif col_no == 4:
                value = f"AMT-{rng.randint(1000, 9999)}.{rng.randint(10, 99)}"
            else:
                value = f"{column[:3].upper()}-{page_no:02d}-{row_no:02d}-{col_no:02d}"
            if multiline and col_no == len(columns) and row_no % 4 == 0:
                value = f"{value}\nWRAP-{case_id}-{page_no:02d}-{row_no:02d}"
            row.append(value)
            expected_cells.append(value)
        data.append(row)
    return data, expected_cells


def draw_table(
    c: canvas.Canvas,
    data: list[list[str]],
    x: float,
    y: float,
    width: float,
    *,
    font_size: int = 6,
    header_fill: colors.Color = colors.HexColor("#DCE775"),
    span_title: str | None = None,
) -> float:
    styles = getSampleStyleSheet()
    cell_style = styles["BodyText"]
    cell_style.fontName = "Helvetica"
    cell_style.fontSize = font_size
    cell_style.leading = font_size + 2
    rendered: list[list[object]] = []
    for row in data:
        rendered.append([Paragraph(str(cell).replace("\n", "<br/>"), cell_style) for cell in row])

    if span_title:
        rendered = [[Paragraph(span_title, cell_style)] + [""] * (len(data[0]) - 1)] + rendered

    col_width = width / len(data[0])
    table = Table(rendered, colWidths=[col_width] * len(data[0]))
    style_commands = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#78909C")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("BACKGROUND", (0, 0), (-1, 0), header_fill),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#263238")),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if span_title:
        style_commands.extend(
            [
                ("SPAN", (0, 0), (-1, 0)),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B0BEC5")),
                ("BACKGROUND", (0, 1), (-1, 1), header_fill),
            ]
        )
    table.setStyle(TableStyle(style_commands))
    _, table_height = table.wrapOn(c, width, 240 * mm)
    table.drawOn(c, x, y - table_height)
    return y - table_height


def add_case(
    cases: list[dict[str, object]],
    case_id: str,
    filename: str,
    description: str,
    pages: int,
    expected_tables: int,
    text_anchors: list[str],
    table_cells: list[str],
    tags: list[str],
) -> None:
    cases.append(
        {
            "case_id": case_id,
            "filename": filename,
            "description": description,
            "pages": pages,
            "expected_tables": expected_tables,
            "expected_text_anchors": sorted(set(text_anchors)),
            "expected_table_cells": sorted(set(cell.replace("\n", " ") for cell in table_cells)),
            "tags": tags,
        }
    )


def build_case_01(cases: list[dict[str, object]], rng: random.Random) -> None:
    case_id = "C01"
    filename = "case01_clean_financial_2p.pdf"
    path = PDF_DIR / filename
    page_count = 2
    c = canvas.Canvas(str(path), pagesize=A4)
    anchors: list[str] = []
    cells: list[str] = []
    for page_no in range(1, page_count + 1):
        c.setPageSize(A4)
        width, height = norm_page_size(A4)
        draw_header(c, case_id, "Clean financial summary", page_no, page_count, A4)
        token = f"{case_id}-TEXT-P{page_no:02d}-ALPHA"
        anchors.append(token)
        c.setFillColor(colors.HexColor("#263238"))
        c.setFont("Helvetica-Bold", 16)
        c.drawString(18 * mm, height - 30 * mm, f"Quarterly Controls {token}")
        c.setFont("Helvetica", 8)
        left_text = (
            f"This page has a clean vector table, narrow footnotes, and a repeated control marker "
            f"{case_id}-NOTE-P{page_no:02d}. Values are deterministic."
        )
        right_text = (
            f"Reviewers compare extracted text anchors and table cells. The secondary marker is "
            f"{case_id}-RIGHT-P{page_no:02d}."
        )
        anchors.extend([f"{case_id}-NOTE-P{page_no:02d}", f"{case_id}-RIGHT-P{page_no:02d}"])
        draw_wrapped(c, left_text, 18 * mm, height - 43 * mm, 78 * mm, font_size=8)
        draw_wrapped(c, right_text, 110 * mm, height - 43 * mm, 78 * mm, font_size=8)
        columns = ["Control ID", "SKU", "Quantity", "Amount", "Owner", "Status"]
        data, expected = make_table_data(case_id, page_no, 1, 9 + page_no, columns, rng=rng)
        cells.extend(expected)
        draw_table(c, data, 18 * mm, height - 82 * mm, width - 36 * mm, font_size=6)
        c.setFillColor(colors.HexColor("#455A64"))
        c.setFont("Helvetica", 7)
        c.drawString(18 * mm, 23 * mm, f"Footnote {case_id}-FOOTNOTE-P{page_no:02d}: rounded values use USD.")
        anchors.append(f"{case_id}-FOOTNOTE-P{page_no:02d}")
        c.showPage()
    c.save()
    add_case(cases, case_id, filename, "2-page clean vector PDF with ordinary tables.", page_count, 2, anchors, cells, ["clean", "vector", "short"])


def build_case_02(cases: list[dict[str, object]], rng: random.Random) -> None:
    case_id = "C02"
    filename = "case02_dense_multisection_5p.pdf"
    path = PDF_DIR / filename
    page_count = 5
    c = canvas.Canvas(str(path), pagesize=A4)
    anchors: list[str] = []
    cells: list[str] = []
    for page_no in range(1, page_count + 1):
        c.setPageSize(A4)
        width, height = norm_page_size(A4)
        draw_header(c, case_id, "Dense multi-section operating log", page_no, page_count, A4)
        c.saveState()
        c.setFillColor(colors.HexColor("#ECEFF1"))
        c.setFont("Helvetica-Bold", 28)
        c.translate(width - 35 * mm, height / 2)
        c.rotate(90)
        c.drawString(0, 0, f"DENSE-{case_id}-P{page_no:02d}")
        c.restoreState()
        anchor = f"{case_id}-DENSE-TEXT-P{page_no:02d}"
        anchors.append(anchor)
        c.setFillColor(colors.HexColor("#263238"))
        c.setFont("Helvetica-Bold", 13)
        c.drawString(18 * mm, height - 30 * mm, f"Operations Snapshot {anchor}")
        c.setFont("Helvetica", 7)
        text = (
            f"Small font, merged title rows, alternating row colors, and wrapped final cells. "
            f"Section marker {case_id}-SECTION-P{page_no:02d} appears near dense content."
        )
        anchors.append(f"{case_id}-SECTION-P{page_no:02d}")
        draw_wrapped(c, text, 18 * mm, height - 42 * mm, width - 48 * mm, font_size=7, leading=9)
        columns = ["Event ID", "SKU", "Quantity", "Amount", "Region", "Signal", "Owner", "Escalation"]
        data, expected = make_table_data(case_id, page_no, 1, 16, columns, rng=rng, multiline=True)
        cells.extend(expected)
        draw_table(
            c,
            data,
            14 * mm,
            height - 61 * mm,
            width - 28 * mm,
            font_size=5,
            header_fill=colors.HexColor("#FFCC80"),
            span_title=f"Merged Header {case_id}-P{page_no:02d}-TABLE-01",
        )
        anchors.append(f"{case_id}-P{page_no:02d}-TABLE-01")
        c.showPage()
    c.save()
    add_case(cases, case_id, filename, "5-page dense vector PDF with merged table headers and rotated side labels.", page_count, 5, anchors, cells, ["dense", "merged-header", "rotated"])


def build_case_03(cases: list[dict[str, object]], rng: random.Random) -> None:
    case_id = "C03"
    filename = "case03_mixed_orientation_9p.pdf"
    path = PDF_DIR / filename
    page_count = 9
    c = canvas.Canvas(str(path), pagesize=A4)
    anchors: list[str] = []
    cells: list[str] = []
    for page_no in range(1, page_count + 1):
        page_size = landscape(A4) if page_no % 3 == 0 else A4
        c.setPageSize(page_size)
        width, height = norm_page_size(page_size)
        draw_header(c, case_id, "Mixed orientation field packet", page_no, page_count, page_size)
        anchor = f"{case_id}-MIXED-P{page_no:02d}-ANCHOR"
        anchors.append(anchor)
        c.setFillColor(colors.HexColor("#263238"))
        c.setFont("Helvetica-Bold", 14)
        c.drawString(18 * mm, height - 31 * mm, f"Field Packet {page_no}: {anchor}")
        c.setFont("Helvetica", 7)
        sidebar_anchor = f"{case_id}-SIDEBAR-P{page_no:02d}"
        anchors.append(sidebar_anchor)
        c.setFillColor(colors.HexColor("#ECEFF1"))
        c.rect(width - 44 * mm, height - 86 * mm, 26 * mm, 54 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#37474F"))
        draw_wrapped(c, f"Sidebar {sidebar_anchor} includes callouts and labels.", width - 41 * mm, height - 38 * mm, 20 * mm, font_size=6, leading=8)
        chart_anchor = f"{case_id}-CHART-P{page_no:02d}"
        anchors.append(chart_anchor)
        c.setStrokeColor(colors.HexColor("#546E7A"))
        chart_x = 18 * mm
        chart_y = height - 78 * mm
        c.rect(chart_x, chart_y - 36 * mm, 62 * mm, 36 * mm, fill=0, stroke=1)
        c.setFont("Helvetica", 6)
        c.drawString(chart_x + 3 * mm, chart_y - 7 * mm, chart_anchor)
        last_x, last_y = chart_x + 5 * mm, chart_y - 28 * mm
        for step in range(1, 9):
            next_x = chart_x + (5 + step * 6) * mm
            next_y = chart_y - (rng.randint(8, 32)) * mm
            c.line(last_x, last_y, next_x, next_y)
            last_x, last_y = next_x, next_y
        if page_no % 3 == 0:
            columns = ["Trace ID", "SKU", "Quantity", "Amount", "North", "South", "East", "West", "Audit", "Note"]
            rows = 13
            table_x = 88 * mm
            table_y = height - 38 * mm
            table_w = width - 110 * mm
            font_size = 5
        else:
            columns = ["Trace ID", "SKU", "Quantity", "Amount", "Zone", "Reviewer"]
            rows = 12
            table_x = 18 * mm
            table_y = height - 125 * mm
            table_w = width - 36 * mm
            font_size = 6
        data, expected = make_table_data(case_id, page_no, 1, rows, columns, rng=rng, multiline=page_no % 2 == 0)
        cells.extend(expected)
        draw_table(c, data, table_x, table_y, table_w, font_size=font_size, header_fill=colors.HexColor("#80CBC4"))
        c.showPage()
    c.save()
    add_case(cases, case_id, filename, "9-page vector PDF with portrait/landscape pages, sidebars, charts, and tables.", page_count, 9, anchors, cells, ["mixed-orientation", "charts", "sidebars"])


def build_case_04(cases: list[dict[str, object]], rng: random.Random) -> None:
    case_id = "C04"
    filename = "case04_long_register_16p.pdf"
    path = PDF_DIR / filename
    page_count = 16
    c = canvas.Canvas(str(path), pagesize=A4)
    anchors: list[str] = []
    cells: list[str] = []
    for page_no in range(1, page_count + 1):
        c.setPageSize(A4)
        width, height = norm_page_size(A4)
        draw_header(c, case_id, "Long compliance register", page_no, page_count, A4)
        anchor = f"{case_id}-REGISTER-P{page_no:02d}"
        anchors.append(anchor)
        c.setFillColor(colors.HexColor("#263238"))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(18 * mm, height - 29 * mm, f"Register continuation {page_no:02d} {anchor}")
        c.setFont("Helvetica", 6)
        c.drawString(18 * mm, height - 36 * mm, f"Cross-page repeated header with continuation marker {case_id}-CONT-P{page_no:02d}.")
        anchors.append(f"{case_id}-CONT-P{page_no:02d}")
        columns = ["Line ID", "SKU", "Quantity", "Amount", "Rule", "Evidence", "Disposition"]
        data, expected = make_table_data(case_id, page_no, 1, 18, columns, rng=rng, multiline=page_no % 4 == 0)
        cells.extend(expected)
        draw_table(c, data, 12 * mm, height - 48 * mm, width - 24 * mm, font_size=5, header_fill=colors.HexColor("#B39DDB"))
        c.showPage()
    c.save()
    add_case(cases, case_id, filename, "16-page long vector register with repeated dense tables.", page_count, 16, anchors, cells, ["long", "dense", "cross-page"])


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/verdana.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def build_case_05(cases: list[dict[str, object]], rng: random.Random) -> None:
    case_id = "C05"
    filename = "case05_scanned_image_4p.pdf"
    path = PDF_DIR / filename
    page_count = 4
    pages: list[Image.Image] = []
    anchors: list[str] = []
    cells: list[str] = []
    font_title = load_font(28)
    font_body = load_font(18)
    font_cell = load_font(15)
    for page_no in range(1, page_count + 1):
        img = Image.new("RGB", (1700, 2200), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((80, 80, 1620, 2120), outline=(120, 120, 120), width=3)
        anchor = f"{case_id}-SCAN-P{page_no:02d}-ANCHOR"
        anchors.append(anchor)
        draw.text((120, 125), f"Scanned Intake Packet {page_no} {anchor}", font=font_title, fill=(20, 20, 20))
        note = f"Image-only page with raster text, stamped noise, and table marker {case_id}-IMAGE-NOTE-P{page_no:02d}."
        anchors.append(f"{case_id}-IMAGE-NOTE-P{page_no:02d}")
        draw.text((120, 180), note, font=font_body, fill=(30, 30, 30))
        for _ in range(80):
            x = rng.randint(90, 1600)
            y = rng.randint(230, 2050)
            shade = rng.randint(205, 245)
            draw.point((x, y), fill=(shade, shade, shade))
        columns = ["Scan ID", "SKU", "Quantity", "Amount", "Route"]
        data, expected = make_table_data(case_id, page_no, 1, 8, columns, rng=rng)
        cells.extend(expected)
        x0, y0 = 120, 300
        cell_w, cell_h = 285, 72
        rows = len(data)
        cols = len(columns)
        for r in range(rows + 1):
            y = y0 + r * cell_h
            draw.line((x0, y, x0 + cols * cell_w, y), fill=(0, 0, 0), width=2)
        for col in range(cols + 1):
            x = x0 + col * cell_w
            draw.line((x, y0, x, y0 + rows * cell_h), fill=(0, 0, 0), width=2)
        draw.rectangle((x0, y0, x0 + cols * cell_w, y0 + cell_h), fill=(225, 235, 240))
        for r, row in enumerate(data):
            for col, value in enumerate(row):
                draw.text((x0 + col * cell_w + 10, y0 + r * cell_h + 18), str(value), font=font_cell, fill=(0, 0, 0))
        draw.text((120, 1050), f"Rubber stamp: {case_id}-STAMP-P{page_no:02d}", font=font_body, fill=(90, 90, 90))
        anchors.append(f"{case_id}-STAMP-P{page_no:02d}")
        pages.append(img)
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=200.0)
    add_case(cases, case_id, filename, "4-page image-only PDF that simulates scanned forms.", page_count, 4, anchors, cells, ["scanned", "image-only", "ocr"])


def main() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260502)
    cases: list[dict[str, object]] = []
    build_case_01(cases, rng)
    build_case_02(cases, rng)
    build_case_03(cases, rng)
    build_case_04(cases, rng)
    build_case_05(cases, rng)
    payload = {
        "generated_at": "2026-05-02",
        "metric_notes": {
            "text_anchor_recall": "Share of expected control markers found in Docling Markdown.",
            "table_cell_recall": "Share of expected table cell strings found in exported Docling tables.",
            "table_detection_ratio": "Detected table count divided by expected table count, capped at 1.0.",
        },
        "cases": cases,
    }
    GT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(cases)} PDFs to {PDF_DIR}")
    print(f"Wrote ground truth to {GT_PATH}")


if __name__ == "__main__":
    main()
