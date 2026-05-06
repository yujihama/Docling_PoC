from __future__ import annotations

import argparse
import json
import random
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "pdf_validation_suite"
PDF_DIR = OUT_DIR / "pdfs"
MANIFEST_PATH = OUT_DIR / "manifest.json"


def ensure_dirs() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)


def register_fonts() -> None:
    for name in ("HeiseiKakuGo-W5", "HeiseiMin-W3"):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(name))
        except Exception:
            pass


def make_canvas(path: Path, page_size: tuple[float, float] = A4) -> canvas.Canvas:
    return canvas.Canvas(str(path), pagesize=page_size, invariant=1)


def draw_header(
    c: canvas.Canvas,
    title: str,
    case_id: str,
    page_no: int,
    page_count: int,
    page_size: tuple[float, float] = A4,
) -> None:
    width, height = page_size
    c.setFillColor(colors.HexColor("#263238"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(14 * mm, height - 13 * mm, f"{case_id} | {title}")
    c.setFont("Helvetica", 8)
    c.drawRightString(width - 14 * mm, height - 13 * mm, f"Page {page_no} of {page_count}")
    c.setStrokeColor(colors.HexColor("#90A4AE"))
    c.line(14 * mm, height - 16 * mm, width - 14 * mm, height - 16 * mm)
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#607D8B"))
    c.drawString(14 * mm, 11 * mm, "PDF validation suite for routed PDF extraction.")
    c.drawRightString(width - 14 * mm, 11 * mm, f"{case_id}-FOOTER-P{page_no:02d}")


def draw_wrapped(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    *,
    font: str = "Helvetica",
    size: int = 8,
    leading: int = 10,
) -> float:
    c.setFont(font, size)
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
    size: int = 7,
    header_fill: colors.Color = colors.HexColor("#E3F2FD"),
) -> float:
    table_width = sum(col_widths)
    all_rows = [headers] + rows
    top = y
    c.setStrokeColor(colors.HexColor("#78909C"))
    for r, row in enumerate(all_rows):
        row_top = top - (r * row_height)
        if r == 0:
            c.setFillColor(header_fill)
            c.rect(x, row_top - row_height, table_width, row_height, stroke=0, fill=1)
        c.setFillColor(colors.HexColor("#263238"))
        c.setFont("Helvetica-Bold" if r == 0 else font, size)
        left = x
        for col_width, value in zip(col_widths, row):
            c.rect(left, row_top - row_height, col_width, row_height, stroke=1, fill=0)
            text = str(value).replace("\n", " ")
            c.drawString(left + 2, row_top - row_height + 3, text[: max(8, int(col_width / 3.2))])
            left += col_width
    return top - (len(all_rows) * row_height)


def fitted_text(value: str, font: str, size: float, max_width: float) -> str:
    text = str(value)
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    if max_width <= pdfmetrics.stringWidth("...", font, size):
        return ""
    shortened = text
    while shortened and pdfmetrics.stringWidth(f"{shortened}...", font, size) > max_width:
        shortened = shortened[:-1]
    return f"{shortened}..." if shortened else ""


def draw_cell(
    c: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    *,
    font: str = "Helvetica",
    size: float = 5.0,
    fill: colors.Color | None = None,
    text_color: colors.Color = colors.HexColor("#263238"),
    stroke: colors.Color = colors.HexColor("#78909C"),
    align: str = "left",
    bold: bool = False,
) -> None:
    if fill is not None:
        c.setFillColor(fill)
        c.rect(x, y, width, height, stroke=0, fill=1)
    c.setStrokeColor(stroke)
    c.rect(x, y, width, height, stroke=1, fill=0)
    text_font = "Helvetica-Bold" if bold else font
    c.setFont(text_font, size)
    c.setFillColor(text_color)
    lines = str(text).splitlines() or [""]
    max_lines = max(1, int((height - 3) / (size + 1)))
    visible_lines = lines[:max_lines]
    block_height = len(visible_lines) * (size + 1)
    start_y = y + (height + block_height) / 2 - size
    for line_index, line in enumerate(visible_lines):
        fitted = fitted_text(line, text_font, size, width - 4)
        if align == "right":
            tx = x + width - 2 - pdfmetrics.stringWidth(fitted, text_font, size)
        elif align == "center":
            tx = x + (width - pdfmetrics.stringWidth(fitted, text_font, size)) / 2
        else:
            tx = x + 2
        c.drawString(tx, start_y - line_index * (size + 1), fitted)


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
    case_id = "V01"
    filename = "v01_invoice_purchase_order_2p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    headers = ["Line", "SKU", "Description", "Qty", "Unit", "Amount", "Tax"]
    col_widths = [16 * mm, 28 * mm, 58 * mm, 18 * mm, 23 * mm, 28 * mm, 19 * mm]
    for page in range(1, 3):
        draw_header(c, "Invoice and purchase order", case_id, page, 2)
        width, height = A4
        invoice_no = f"INV-2026-05-{page:03d}"
        po_no = f"PO-VAL-{page:03d}-ALPHA"
        anchors.extend([invoice_no, po_no, f"{case_id}-APPROVAL-STAMP-P{page:02d}"])
        c.setFont("Helvetica-Bold", 17)
        c.setFillColor(colors.HexColor("#111827"))
        c.drawString(14 * mm, height - 29 * mm, f"Invoice {invoice_no}")
        c.setFont("Helvetica", 8)
        c.drawString(14 * mm, height - 37 * mm, f"Customer PO: {po_no}")
        c.drawString(14 * mm, height - 43 * mm, "Ship to: Sakura Components, 3-12-7 Nihonbashi, Tokyo")
        c.drawRightString(width - 14 * mm, height - 37 * mm, "Payment terms: Net 30")
        c.drawRightString(width - 14 * mm, height - 43 * mm, "Bank ref: BREF-7791-4420")
        rows: list[list[str]] = []
        for row in range(1, 9):
            sku = f"SKU-V01-{page}{row:02d}"
            amount = f"{(1290 + row * 317 + page * 41):,.2f}"
            anchors.extend([sku, amount])
            rows.append(
                [
                    f"L{row:02d}",
                    sku,
                    f"Replacement bracket kit REV-{page}-{row}",
                    str(2 + row),
                    f"{140 + row}.00",
                    amount,
                    "10%",
                ]
            )
        bottom = draw_table(c, 14 * mm, height - 64 * mm, col_widths, 8 * mm, headers, rows, size=6)
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(width - 14 * mm, bottom - 8 * mm, f"Grand total: JPY {sum(float(r[5].replace(',', '')) for r in rows):,.2f}")
        c.saveState()
        c.setStrokeColor(colors.HexColor("#1565C0"))
        c.setFillColor(colors.HexColor("#E3F2FD"))
        c.rotate(12)
        c.rect(112 * mm, 37 * mm, 48 * mm, 15 * mm, stroke=1, fill=1)
        c.setFillColor(colors.HexColor("#1565C0"))
        c.setFont("Helvetica-Bold", 10)
        c.drawString(116 * mm, 42 * mm, f"{case_id}-APPROVAL-STAMP-P{page:02d}")
        c.restoreState()
        barcode = code128.Code128(f"{invoice_no}|{po_no}", barHeight=13 * mm, barWidth=0.38)
        barcode.drawOn(c, 14 * mm, bottom - 23 * mm)
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Two-page invoice with dense line-item tables, barcode, rotated approval stamp, totals, and Japanese address text.",
        pages=2,
        expected_anchors=anchors,
        tags=["invoice", "purchase-order", "tables", "barcode", "rotated-stamp"],
    )


def build_bank_statement(cases: list[dict[str, Any]]) -> None:
    case_id = "V02"
    filename = "v02_bank_statement_twocolumn_2p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    headers = ["Date", "Ref", "Description", "Debit", "Credit", "Balance"]
    col_widths = [18 * mm, 27 * mm, 58 * mm, 25 * mm, 25 * mm, 28 * mm]
    for page in range(1, 3):
        draw_header(c, "Bank statement with notes", case_id, page, 2)
        width, height = A4
        statement_id = f"STMT-V02-2026-05-P{page:02d}"
        anchors.append(statement_id)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(14 * mm, height - 29 * mm, statement_id)
        c.setFont("Helvetica", 7)
        left_note = (
            f"Account ending 8842. This export includes small-print disclosures and a transaction "
            f"anchor {case_id}-DISCLOSURE-P{page:02d}. Negative amounts use parentheses."
        )
        right_note = (
            f"Internal review code {case_id}-RECON-P{page:02d}-LOCK is printed in the right column."
        )
        anchors.extend([f"{case_id}-DISCLOSURE-P{page:02d}", f"{case_id}-RECON-P{page:02d}-LOCK"])
        draw_wrapped(c, left_note, 14 * mm, height - 40 * mm, 82 * mm, size=7, leading=8)
        draw_wrapped(c, right_note, 108 * mm, height - 40 * mm, 80 * mm, size=7, leading=8)
        rows: list[list[str]] = []
        balance = 10500 + page * 1000
        for row in range(1, 15):
            ref = f"ACH-V02-{page}{row:02d}"
            debit = f"({row * 18}.47)" if row % 3 == 0 else ""
            credit = f"{row * 123}.19" if row % 3 != 0 else ""
            balance += (row * 123 if credit else -(row * 18))
            anchors.extend([ref, f"BAL-{balance}"])
            rows.append(
                [
                    f"05/{row + page:02d}",
                    ref,
                    f"Counterparty {row} memo BAL-{balance}",
                    debit,
                    credit,
                    f"BAL-{balance}",
                ]
            )
        bottom = draw_table(c, 14 * mm, height - 67 * mm, col_widths, 6.6 * mm, headers, rows, size=5)
        c.saveState()
        c.setFillColor(colors.Color(0.7, 0.7, 0.7, alpha=0.15))
        c.setFont("Helvetica-Bold", 48)
        c.translate(width / 2, height / 2)
        c.rotate(35)
        c.drawCentredString(0, 0, "DRAFT COPY")
        c.restoreState()
        c.setFont("Helvetica", 5)
        c.drawString(14 * mm, bottom - 7 * mm, f"Microprint control: {case_id}-MICRO-P{page:02d}-8PT-LINE")
        anchors.append(f"{case_id}-MICRO-P{page:02d}-8PT-LINE")
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Two-page bank statement with dense transaction table, two-column notes, negative amounts, watermark, and 5pt microprint.",
        pages=2,
        expected_anchors=anchors,
        tags=["bank-statement", "dense-table", "two-column", "watermark", "microprint"],
    )


def build_application_form(cases: list[dict[str, Any]]) -> None:
    case_id = "V03"
    filename = "v03_application_form_checkboxes_2p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    for page in range(1, 3):
        draw_header(c, "Application form with fields", case_id, page, 2)
        width, height = A4
        form_id = f"FORM-V03-APPL-{page:02d}"
        anchors.append(form_id)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(14 * mm, height - 30 * mm, f"Supplier onboarding {form_id}")
        c.setFont("Helvetica", 8)
        y = height - 43 * mm
        field_values = [
            ("Legal name", f"Northbridge Test LLC {case_id}-LEGAL-P{page:02d}"),
            ("Tax ID", f"TIN-V03-{page}88-4420"),
            ("Contact", f"ops+v03p{page}@example.test"),
            ("Address", f"Suite {page}00, Validation Ave, Osaka"),
        ]
        for label, value in field_values:
            anchors.append(value)
            c.drawString(14 * mm, y, label)
            c.line(45 * mm, y - 1, 126 * mm, y - 1)
            c.drawString(47 * mm, y + 1, value)
            y -= 10 * mm
        options = [
            ("Domestic entity", True),
            ("Foreign entity", False),
            ("Requires NDA", True),
            ("Expedited payment", False),
        ]
        for idx, (label, checked) in enumerate(options, start=1):
            x = 14 * mm + ((idx - 1) % 2) * 85 * mm
            y2 = y - ((idx - 1) // 2) * 10 * mm
            c.rect(x, y2 - 3, 9, 9)
            if checked:
                c.line(x + 1, y2 + 1, x + 4, y2 - 2)
                c.line(x + 4, y2 - 2, x + 8, y2 + 5)
            checkbox_token = f"{case_id}-CHECK-P{page:02d}-{idx:02d}-{int(checked)}"
            anchors.append(checkbox_token)
            c.drawString(x + 13, y2, f"{label} {checkbox_token}")
        y -= 29 * mm
        headers = ["Approver", "Role", "Decision", "Date"]
        rows = []
        for row in range(1, 5):
            token = f"APPR-V03-P{page:02d}-R{row:02d}"
            anchors.append(token)
            rows.append([token, "Finance" if row % 2 else "Legal", "Approved" if row < 4 else "Pending", f"2026-05-{row + page:02d}"])
        bottom = draw_table(c, 14 * mm, y, [48 * mm, 42 * mm, 42 * mm, 40 * mm], 8 * mm, headers, rows, size=6)
        c.saveState()
        c.setFillColor(colors.HexColor("#212121"))
        c.rect(118 * mm, bottom - 22 * mm, 53 * mm, 10 * mm, stroke=0, fill=1)
        redaction_note = f"{case_id}-REDACTED-FIELD-P{page:02d}"
        anchors.append(redaction_note)
        c.setFillColor(colors.HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 7)
        c.drawString(121 * mm, bottom - 19 * mm, redaction_note)
        c.restoreState()
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Onboarding form with checkboxes, underlined fields, approval table, email address, and black redaction bars.",
        pages=2,
        expected_anchors=anchors,
        tags=["form", "checkbox", "redaction", "email", "table"],
    )


def build_japanese_mixed(cases: list[dict[str, Any]]) -> None:
    case_id = "V04"
    filename = "v04_japanese_vertical_mixed_2p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    headers = ["行", "品目コード", "摘要", "数量", "金額"]
    col_widths = [15 * mm, 34 * mm, 64 * mm, 24 * mm, 32 * mm]
    for page in range(1, 3):
        draw_header(c, "Japanese mixed layout", case_id, page, 2)
        width, height = A4
        doc_id = f"JP-V04-請求-{page:02d}"
        anchors.append(doc_id)
        c.setFont("HeiseiKakuGo-W5", 16)
        c.drawString(14 * mm, height - 31 * mm, f"請求明細書 {doc_id}")
        c.setFont("HeiseiMin-W3", 8)
        c.drawString(14 * mm, height - 41 * mm, f"取引先: 東西検証株式会社 参照番号 {case_id}-参照-P{page:02d}")
        anchors.append(f"{case_id}-参照-P{page:02d}")
        c.saveState()
        c.translate(width - 14 * mm, height - 55 * mm)
        c.rotate(-90)
        vertical_token = f"{case_id}-縦書き-P{page:02d}"
        anchors.append(vertical_token)
        c.setFont("HeiseiKakuGo-W5", 9)
        c.drawString(0, 0, f"縦書き確認 {vertical_token}")
        c.restoreState()
        rows = []
        for row in range(1, 8):
            sku = f"JP-SKU-V04-{page}{row:02d}"
            amount = f"JPY-{page}{row}80{row}"
            anchors.extend([sku, amount])
            rows.append([str(row), sku, f"部品交換費用 摘要-V04-{page}-{row}", str(row + 1), amount])
        draw_table(
            c,
            14 * mm,
            height - 62 * mm,
            col_widths,
            8 * mm,
            headers,
            rows,
            font="HeiseiMin-W3",
            size=6,
            header_fill=colors.HexColor("#E8F5E9"),
        )
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Japanese invoice-like PDF with CID fonts, mixed Japanese/ASCII anchors, table, and rotated vertical side label.",
        pages=2,
        expected_anchors=anchors,
        tags=["japanese", "cid-font", "vertical-text", "table"],
    )


def _find_scan_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def make_image_stamp(
    path: Path,
    *,
    lines: list[str],
    size: tuple[int, int] = (1300, 560),
    color: tuple[int, int, int, int] = (190, 20, 20, 220),
    border: str = "ellipse",
    rotate_degrees: float = 0.0,
    noise_seed: int | None = None,
) -> None:
    img = Image.new("RGBA", size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    red = color
    width, height = size
    inset = max(18, min(width, height) // 18)
    if border == "rectangle":
        draw.rounded_rectangle(
            (inset, inset, width - inset, height - inset),
            radius=max(18, min(width, height) // 14),
            outline=red,
            width=max(10, min(width, height) // 34),
        )
        draw.rounded_rectangle(
            (inset * 2, inset * 2, width - inset * 2, height - inset * 2),
            radius=max(10, min(width, height) // 22),
            outline=red,
            width=max(5, min(width, height) // 70),
        )
    else:
        draw.ellipse(
            (inset, inset, width - inset, height - inset),
            outline=red,
            width=max(12, min(width, height) // 30),
        )
        draw.ellipse(
            (inset * 2, inset * 2, width - inset * 2, height - inset * 2),
            outline=red,
            width=max(5, min(width, height) // 70),
        )
    font_large = _find_scan_font(max(42, height // 7))
    font_medium = _find_scan_font(max(34, height // 10))
    fonts = [font_medium, font_large, font_medium, font_medium]
    line_metrics = []
    total_text_height = 0
    for line, font in zip(lines, fonts, strict=False):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_height = bbox[3] - bbox[1]
        line_metrics.append((line, font, bbox[2] - bbox[0], line_height))
        total_text_height += line_height
    line_gap = max(10, height // 24)
    y = (height - total_text_height - line_gap * max(len(line_metrics) - 1, 0)) / 2
    for line, font, text_width, line_height in line_metrics:
        draw.text(((width - text_width) / 2, y), line, fill=red, font=font)
        y += line_height + line_gap
    if noise_seed is not None:
        rng = random.Random(noise_seed)
        pixels = img.load()
        for _ in range(max(500, width * height // 110)):
            px = rng.randrange(width)
            py = rng.randrange(height)
            r, g, b, a = pixels[px, py]
            if a:
                pixels[px, py] = (r, g, b, max(30, a - rng.randrange(15, 85)))
    if rotate_degrees:
        resample = getattr(Image, "Resampling", Image).BICUBIC
        img = img.rotate(rotate_degrees, expand=True, resample=resample, fillcolor=(255, 255, 255, 0))
    img.save(path)


def build_scanned_receipt(cases: list[dict[str, Any]]) -> None:
    case_id = "V05"
    filename = "v05_image_only_scanned_receipt_2p.pdf"
    path = PDF_DIR / filename
    temp_dir = OUT_DIR / "_generated_images"
    temp_dir.mkdir(parents=True, exist_ok=True)
    c = make_canvas(path)
    anchors: list[str] = []
    for page in range(1, 3):
        img = Image.new("RGB", (1700, 2200), "white")
        draw = ImageDraw.Draw(img)
        font_title = _find_scan_font(46)
        font = _find_scan_font(28)
        font_small = _find_scan_font(21)
        scan_id = f"SCAN-V05-RECEIPT-P{page:02d}-7788"
        anchors.append(scan_id)
        draw.text((120, 95), "WAREHOUSE RECEIPT", fill=(20, 20, 20), font=font_title)
        draw.text((120, 165), scan_id, fill=(20, 20, 20), font=font)
        draw.text((120, 215), "Vendor: Kanda Optical Parts / Cashier: STN-442", fill=(45, 45, 45), font=font_small)
        y = 310
        headers = ["SKU", "QTY", "PRICE", "LOT", "NOTE"]
        x_positions = [120, 430, 600, 820, 1040]
        for x, header in zip(x_positions, headers):
            draw.text((x, y), header, fill=(0, 0, 0), font=font_small)
        y += 45
        for row in range(1, 11):
            sku = f"SCAN-SKU-V05-{page}{row:02d}"
            lot = f"LOT-{page}{row}9A"
            anchors.extend([sku, lot])
            values = [sku, str(row + 1), f"{row * 13}.40", lot, "faint ink" if row % 3 == 0 else "ok"]
            for x, value in zip(x_positions, values):
                color = (70, 70, 70) if row % 3 == 0 else (20, 20, 20)
                draw.text((x, y), value, fill=color, font=font_small)
            y += 45
        draw.rectangle((105, 290, 1520, y + 20), outline=(50, 50, 50), width=2)
        draw.text((120, y + 65), f"Manual note: {case_id}-HANDNOTE-P{page:02d}-BLUE", fill=(30, 70, 130), font=font)
        anchors.append(f"{case_id}-HANDNOTE-P{page:02d}-BLUE")
        draw.line((100, 300, 1550, 300), fill=(0, 0, 0), width=2)
        img = img.rotate(1.7 if page == 1 else -2.3, expand=True, fillcolor="white")
        img = ImageEnhance.Contrast(img).enhance(0.78)
        img = img.filter(ImageFilter.GaussianBlur(radius=0.55))
        pixels = img.load()
        rng = random.Random(500 + page)
        for _ in range(8500):
            px = rng.randrange(img.width)
            py = rng.randrange(img.height)
            shade = rng.randrange(160, 245)
            pixels[px, py] = (shade, shade, shade)
        jpg_path = temp_dir / f"v05_scan_page_{page}.jpg"
        img.save(jpg_path, quality=68)
        c.drawImage(str(jpg_path), 8 * mm, 8 * mm, width=A4[0] - 16 * mm, height=A4[1] - 16 * mm, preserveAspectRatio=True, anchor="c")
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Image-only scanned receipt with skew, blur, noise, faint rows, and handwritten-style note.",
        pages=2,
        expected_anchors=anchors,
        tags=["scanned", "image-only", "low-quality", "receipt", "ocr"],
    )


def build_image_layer_stamps(cases: list[dict[str, Any]]) -> None:
    case_id = "V09"
    filename = "v09_text_pdf_image_layer_stamps_2p.pdf"
    path = PDF_DIR / filename
    temp_dir = OUT_DIR / "_generated_images"
    temp_dir.mkdir(parents=True, exist_ok=True)
    stamp_large_p1 = temp_dir / "v09_stamp_p1_approved.png"
    stamp_received_p2 = temp_dir / "v09_stamp_p2_received.png"
    stamp_overlap_p2 = temp_dir / "v09_stamp_p2_overlap.png"
    stamp_small_p2 = temp_dir / "v09_stamp_p2_small.png"
    make_image_stamp(
        stamp_large_p1,
        lines=["APPROVED", "IMG-STAMP-V09-P01-APPROVED", "2026-05-06"],
        rotate_degrees=-8.0,
        noise_seed=901,
    )
    make_image_stamp(
        stamp_received_p2,
        lines=["RECEIVED", "IMG-STAMP-V09-P02-RECEIVED", "TOKYO OPS"],
        color=(170, 18, 18, 205),
        border="rectangle",
        rotate_degrees=3.5,
        noise_seed=902,
    )
    make_image_stamp(
        stamp_overlap_p2,
        lines=["CHECKED", "IMG-STAMP-V09-P02-OVERLAP", "QA"],
        size=(1050, 420),
        color=(205, 40, 40, 135),
        rotate_degrees=-12.0,
        noise_seed=903,
    )
    make_image_stamp(
        stamp_small_p2,
        lines=["HANKO", "IMG-STAMP-V09-P02-SMALL"],
        size=(760, 480),
        color=(185, 10, 10, 230),
        rotate_degrees=0.0,
        noise_seed=904,
    )

    c = make_canvas(path)
    anchors: list[str] = []
    headers = ["No.", "Item", "Qty", "Owner", "Control"]
    col_widths = [14 * mm, 68 * mm, 18 * mm, 40 * mm, 42 * mm]

    draw_header(c, "Mixed text PDF with image-layer stamps", case_id, 1, 2)
    width, height = A4
    request_id = "REQ-V09-IMAGE-STAMP-P01"
    anchors.append(request_id)
    c.setFont("Helvetica-Bold", 15)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(14 * mm, height - 30 * mm, f"Purchase approval memo {request_id}")
    c.setFont("Helvetica", 8)
    c.drawString(14 * mm, height - 40 * mm, "Figure 1 Approval seal image area")
    c.drawString(14 * mm, height - 46 * mm, "Vendor: Tokyo Maintenance Supply / Currency: JPY")
    rows = []
    for row in range(1, 7):
        token = f"TXT-V09-P01-R{row:02d}"
        anchors.append(token)
        rows.append([str(row), f"Repair kit {row} {token}", str(row + 2), "Facilities", f"CTRL-V09-1{row:02d}"])
        anchors.append(f"CTRL-V09-1{row:02d}")
    bottom = draw_table(c, 14 * mm, height - 64 * mm, col_widths, 8 * mm, headers, rows, size=6)
    c.setStrokeColor(colors.HexColor("#B0BEC5"))
    c.roundRect(102 * mm, bottom - 62 * mm, 92 * mm, 54 * mm, 6, stroke=1, fill=0)
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#37474F"))
    c.drawString(106 * mm, bottom - 15 * mm, "Approval box contains raster stamp only.")
    anchors.append("IMG-STAMP-V09-P01-APPROVED")
    c.drawImage(
        str(stamp_large_p1),
        99 * mm,
        bottom - 61 * mm,
        width=100 * mm,
        height=55 * mm,
        preserveAspectRatio=True,
        mask="auto",
    )
    c.setFont("Helvetica", 7)
    note = "TEXT-V09-P01-AFTER-STAMP: this text must remain available from the PDF text layer."
    anchors.append(note)
    c.drawString(14 * mm, 35 * mm, note)
    c.showPage()

    draw_header(c, "Delivery note with overlapping image stamps", case_id, 2, 2)
    delivery_id = "DELIV-V09-IMAGE-STAMP-P02"
    anchors.append(delivery_id)
    c.setFont("Helvetica-Bold", 15)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(14 * mm, height - 30 * mm, f"Delivery acceptance {delivery_id}")
    c.setFont("Helvetica", 8)
    c.drawString(14 * mm, height - 40 * mm, "Figure 2 Received and checked stamps are embedded image objects.")
    rows = []
    for row in range(1, 10):
        token = f"TXT-V09-P02-R{row:02d}"
        anchors.append(token)
        rows.append([str(row), f"Line item {row} {token}", str(row + 1), "Warehouse", f"CTRL-V09-2{row:02d}"])
        anchors.append(f"CTRL-V09-2{row:02d}")
    bottom = draw_table(c, 14 * mm, height - 58 * mm, col_widths, 7 * mm, headers, rows, size=6)
    anchors.extend(
        [
            "IMG-STAMP-V09-P02-OVERLAP",
            "IMG-STAMP-V09-P02-RECEIVED",
            "IMG-STAMP-V09-P02-SMALL",
        ]
    )
    c.drawImage(
        str(stamp_overlap_p2),
        60 * mm,
        height - 104 * mm,
        width=91 * mm,
        height=37 * mm,
        preserveAspectRatio=True,
        mask="auto",
    )
    c.drawImage(
        str(stamp_received_p2),
        18 * mm,
        bottom - 66 * mm,
        width=112 * mm,
        height=56 * mm,
        preserveAspectRatio=True,
        mask="auto",
    )
    c.drawImage(
        str(stamp_small_p2),
        152 * mm,
        bottom - 45 * mm,
        width=31 * mm,
        height=20 * mm,
        preserveAspectRatio=True,
        mask="auto",
    )
    c.setFont("Helvetica", 7)
    footer_note = "TEXT-V09-P02-FOOTER-CONTROL: image stamps above are not part of the PDF text layer."
    anchors.append(footer_note)
    c.drawString(14 * mm, 25 * mm, footer_note)
    c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description=(
            "Text-layer business PDF with raster image-layer stamps: a large detached approval seal, "
            "a low-opacity stamp overlapping a table, a received stamp, and a small margin hanko."
        ),
        pages=2,
        expected_anchors=anchors,
        tags=["image-layer-stamp", "embedded-visual", "mixed-layer", "table", "approval"],
    )


def build_complex_structured_table(cases: list[dict[str, Any]]) -> None:
    case_id = "V10"
    filename = "v10_complex_multi_header_table_2p.pdf"
    path = PDF_DIR / filename
    page_size = landscape(A4)
    c = make_canvas(path, page_size)
    width, height = page_size
    anchors: list[str] = []
    col_widths = [18, 21, 28, 20, 17, 17, 17, 15, 15, 18, 17, 16, 20, 40]
    col_widths_pt = [value * mm for value in col_widths]
    x0 = 10 * mm
    header_rows = [7 * mm, 7 * mm, 8 * mm]
    data_height = 7.2 * mm
    header_anchor_identity = "V10-HDR-IDENTITY-GROUP"
    header_anchor_rollforward = "V10-HDR-REV-BRIDGE"
    anchors.extend([header_anchor_identity, header_anchor_rollforward])
    col_headers = [
        "Entity",
        "Segment",
        "Contract ID",
        "Scenario",
        "Opening",
        "New",
        "Churn",
        "FX",
        "Adj.",
        "Closing",
        "Margin %",
        "Risk",
        "Owner",
        "Note / condition",
    ]
    regions = ["Japan", "APAC", "EU", "US"]
    segments = ["Core", "Renewal", "Expansion", "Partner"]
    owners = ["FIN-A", "OPS-B", "LEGAL-C", "REV-D"]
    for page in range(1, 3):
        draw_header(c, "Complex multi-row financial table", case_id, page, 2, page_size)
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.HexColor("#111827"))
        c.drawString(x0, height - 30 * mm, f"Revenue waterfall review V10-COMPLEX-BOOK-P{page:02d}")
        anchors.append(f"V10-COMPLEX-BOOK-P{page:02d}")
        c.setFont("Helvetica", 7)
        c.drawString(x0, height - 36 * mm, "Three-level header, subtotal rows, conditional notes, negative values, and compact multi-field cells.")
        table_top = height - 44 * mm
        y = table_top

        group_specs = [
            (0, 4, f"Identity / ownership {header_anchor_identity if page == 1 else ''}".strip(), colors.HexColor("#E8EEF7")),
            (4, 6, f"Revenue bridge JPYm {header_anchor_rollforward if page == 1 else ''}".strip(), colors.HexColor("#E8F5E9")),
            (10, 4, "Quality checks / comments", colors.HexColor("#FFF3E0")),
        ]
        for start, span, label, fill in group_specs:
            left = x0 + sum(col_widths_pt[:start])
            draw_cell(
                c,
                left,
                y - header_rows[0],
                sum(col_widths_pt[start : start + span]),
                header_rows[0],
                label,
                size=6,
                fill=fill,
                align="center",
                bold=True,
            )
        y -= header_rows[0]
        subgroups = [
            (0, 2, "Org hierarchy"),
            (2, 2, "Deal metadata"),
            (4, 3, "Movement"),
            (7, 3, "Revaluation / close"),
            (10, 4, "Review workflow"),
        ]
        for start, span, label in subgroups:
            left = x0 + sum(col_widths_pt[:start])
            draw_cell(
                c,
                left,
                y - header_rows[1],
                sum(col_widths_pt[start : start + span]),
                header_rows[1],
                label,
                size=5.4,
                fill=colors.HexColor("#ECEFF1"),
                align="center",
                bold=True,
            )
        y -= header_rows[1]
        left = x0
        for col_width, header in zip(col_widths_pt, col_headers, strict=True):
            draw_cell(
                c,
                left,
                y - header_rows[2],
                col_width,
                header_rows[2],
                header,
                size=5.1,
                fill=colors.HexColor("#CFD8DC"),
                align="center",
                bold=True,
            )
            left += col_width
        y -= header_rows[2]

        for row in range(1, 13):
            global_row = (page - 1) * 12 + row
            is_subtotal = row in {6, 12}
            contract_token = f"CPLX-V10-P{page:02d}-R{row:02d}"
            anchors.append(contract_token)
            if is_subtotal:
                subtotal_token = f"SUBTOTAL-V10-P{page:02d}-G{row // 6:02d}"
                anchors.append(subtotal_token)
                cells = [
                    regions[(global_row // 3) % len(regions)],
                    "Subtotal",
                    subtotal_token,
                    "All cases",
                    f"{1200 + global_row * 7:,}",
                    f"{80 + row * 3:,}",
                    f"({20 + row})",
                    f"{(-1) ** row * (row + 3)}",
                    f"{7 - row}",
                    f"{1300 + global_row * 8:,}",
                    f"{28 + row / 10:.1f}%",
                    "watch",
                    owners[row % len(owners)],
                    f"rollup after eliminations {contract_token}",
                ]
            else:
                note_token = f"NOTE-V10-P{page:02d}-R{row:02d}" if row in {4, 9} else ""
                if note_token:
                    anchors.append(note_token)
                opening = 930 + global_row * 17
                new = 40 + (row * 13) % 91
                churn = -1 * (8 + row * 2) if row % 3 == 0 else 0
                fx = -6 if row % 4 == 0 else 5
                adj = 3 if row % 5 else -12
                closing = opening + new + churn + fx + adj
                cells = [
                    regions[(global_row - 1) % len(regions)],
                    segments[(row - 1) % len(segments)],
                    contract_token,
                    f"Base\nS{page}-{row}",
                    f"{opening:,}",
                    f"{new:,}",
                    f"({abs(churn)})" if churn else "-",
                    f"{fx:+}",
                    f"{adj:+}",
                    f"{closing:,}",
                    f"{24 + row / 3:.1f}%",
                    "red" if row in {4, 10} else "green",
                    owners[row % len(owners)],
                    note_token or f"term={12 + row}m / clause {global_row:03d}",
                ]
            fill = colors.HexColor("#ECEFF1") if is_subtotal else (colors.HexColor("#FAFAFA") if row % 2 else None)
            left = x0
            for col_index, (col_width, cell) in enumerate(zip(col_widths_pt, cells, strict=True)):
                align = "right" if 4 <= col_index <= 10 else "left"
                draw_cell(
                    c,
                    left,
                    y - data_height,
                    col_width,
                    data_height,
                    str(cell),
                    size=4.8,
                    fill=fill,
                    align=align,
                    bold=is_subtotal and col_index in {1, 2, 9},
                )
                left += col_width
            y -= data_height
        c.setFont("Helvetica", 6)
        c.setFillColor(colors.HexColor("#455A64"))
        c.drawString(x0, 24 * mm, f"Footnote V10-FOOTNOTE-P{page:02d}: parenthetical churn is negative; FX signs are explicit.")
        anchors.append(f"V10-FOOTNOTE-P{page:02d}")
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description=(
            "Complex financial table with merged-style multi-row headers, nested group labels, subtotal rows, "
            "multi-line cells, negative values, percentages, workflow status, and dense notes."
        ),
        pages=2,
        expected_anchors=anchors,
        tags=["complex-table", "multi-row-header", "merged-header", "subtotal", "financial-table"],
    )


def build_excel_long_table_pdf(cases: list[dict[str, Any]]) -> None:
    case_id = "V11"
    filename = "v11_excel_long_wide_ledger_6p.pdf"
    path = PDF_DIR / filename
    page_size = landscape(A4)
    c = make_canvas(path, page_size)
    width, height = page_size
    anchors: list[str] = []
    workbook_anchor = "EXCEL-V11-BOOK-2026"
    repeated_header_anchor = "V11-COLHEADER-REPEATED"
    anchors.extend([workbook_anchor, repeated_header_anchor])
    columns = [
        ("#", 8),
        ("Period", 12),
        ("Account", 15),
        ("Dept", 14),
        ("CostCtr", 16),
        ("Customer", 18),
        ("Invoice ID", 20),
        ("Qty", 10),
        ("Unit", 12),
        ("Gross", 15),
        ("Disc", 13),
        ("Net", 15),
        ("Tax", 13),
        ("Local", 15),
        ("FX", 12),
        ("USD", 15),
        ("Status", 14),
        ("Formula", 18),
        ("Comment", 25),
    ]
    col_widths_pt = [width_mm * mm for _, width_mm in columns]
    x0 = 8 * mm
    rows_per_page = 28
    row_height = 5.1 * mm
    page_count = 6
    for page in range(1, page_count + 1):
        draw_header(c, "Excel-like long wide ledger", case_id, page, page_count, page_size)
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(colors.HexColor("#111827"))
        page_anchor = f"{workbook_anchor}-P{page:02d}"
        anchors.append(page_anchor)
        c.drawString(x0, height - 27 * mm, f"{page_anchor} / print area A:S / repeated title rows")
        c.setFont("Helvetica", 6)
        if page == 1:
            c.drawString(x0, height - 31 * mm, f"Repeated column header control: {repeated_header_anchor}")
        c.drawRightString(width - 8 * mm, height - 27 * mm, "Scale 72% | Fit all columns on one page wide")
        table_top = height - 36 * mm

        y = table_top
        left = x0
        for excel_col_index, col_width in enumerate(col_widths_pt, start=1):
            excel_label = chr(ord("A") + excel_col_index - 1)
            draw_cell(
                c,
                left,
                y - 4.5 * mm,
                col_width,
                4.5 * mm,
                excel_label,
                size=4.2,
                fill=colors.HexColor("#E0E0E0"),
                align="center",
                bold=True,
            )
            left += col_width
        y -= 4.5 * mm

        left = x0
        for col_index, ((header, _), col_width) in enumerate(zip(columns, col_widths_pt, strict=True)):
            label = f"{header}\n{repeated_header_anchor}" if page == 1 and col_index == 6 else header
            draw_cell(
                c,
                left,
                y - 7.4 * mm,
                col_width,
                7.4 * mm,
                label,
                size=4.4,
                fill=colors.HexColor("#B0BEC5"),
                align="center",
                bold=True,
            )
            left += col_width
        y -= 7.4 * mm

        for row_on_page in range(1, rows_per_page + 1):
            global_row = (page - 1) * rows_per_page + row_on_page
            invoice_token = f"XL-V11-R{global_row:04d}"
            selected_anchor = row_on_page in {1, 14, 28}
            if selected_anchor:
                anchors.append(invoice_token)
            comment_token = f"CM-V11-R{global_row:04d}" if row_on_page in {7, 21} else ""
            if comment_token:
                anchors.append(comment_token)
            gross = 1000 + global_row * 37
            discount = -1 * (global_row % 9) * 3
            net = gross + discount
            tax = round(net * 0.1)
            local = net + tax
            fx = 142.5 + (global_row % 11) / 10
            usd = local / fx
            values = [
                str(global_row),
                f"2026-{((global_row - 1) // 14) % 12 + 1:02d}",
                f"4{global_row % 900:03d}",
                f"D{global_row % 17:02d}",
                f"CC-{global_row % 41:02d}",
                f"CUST-{global_row % 73:03d}",
                invoice_token,
                str((global_row % 8) + 1),
                f"{120 + global_row % 25}.50",
                f"{gross:,}",
                f"({abs(discount):,})" if discount else "-",
                f"{net:,}",
                f"{tax:,}",
                f"{local:,}",
                f"{fx:.1f}",
                f"{usd:,.2f}",
                "closed" if global_row % 5 else "review",
                f"=SUM(J{global_row + 4}:M{global_row + 4})",
                comment_token or f"carry {global_row % 13:02d}",
            ]
            fill = colors.HexColor("#F5F5F5") if row_on_page % 2 else None
            if row_on_page in {14, 28}:
                fill = colors.HexColor("#FFFDE7")
            left = x0
            for col_index, (col_width, value) in enumerate(zip(col_widths_pt, values, strict=True)):
                align = "right" if col_index in {0, 7, 8, 9, 10, 11, 12, 13, 14, 15} else "left"
                draw_cell(
                    c,
                    left,
                    y - row_height,
                    col_width,
                    row_height,
                    value,
                    size=4.05,
                    fill=fill,
                    align=align,
                )
                left += col_width
            y -= row_height
        subtotal_anchor = f"V11-SUBTOTAL-P{page:02d}"
        anchors.append(subtotal_anchor)
        c.setFont("Helvetica-Bold", 5.5)
        c.setFillColor(colors.HexColor("#263238"))
        c.drawRightString(width - 8 * mm, 15 * mm, f"{subtotal_anchor} rows {(page - 1) * rows_per_page + 1}-{page * rows_per_page}")
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description=(
            "Excel-like PDF export of a long wide ledger: six landscape pages, tiny grid text, repeated title rows, "
            "column letters, formulas, negative values, and hundreds of data rows."
        ),
        pages=page_count,
        expected_anchors=anchors,
        tags=["excel-export", "long-table", "wide-table", "repeated-header", "tiny-text"],
    )


def build_technical_drawing(cases: list[dict[str, Any]]) -> None:
    case_id = "V06"
    filename = "v06_landscape_technical_drawing_1p.pdf"
    path = PDF_DIR / filename
    page_size = landscape(A4)
    c = make_canvas(path, page_size)
    width, height = page_size
    anchors: list[str] = []
    draw_header(c, "Landscape drawing and schedule", case_id, 1, 1, page_size)
    drawing_id = "DWG-V06-FLOOR-A-117"
    anchors.append(drawing_id)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(14 * mm, height - 30 * mm, drawing_id)
    c.setStrokeColor(colors.HexColor("#37474F"))
    c.setLineWidth(1.2)
    x0, y0 = 18 * mm, 44 * mm
    c.rect(x0, y0, 160 * mm, 115 * mm)
    for i in range(1, 7):
        x = x0 + i * 24 * mm
        c.line(x, y0, x, y0 + 115 * mm)
    for i in range(1, 5):
        y = y0 + i * 23 * mm
        c.line(x0, y, x0 + 160 * mm, y)
    for idx, (x, y) in enumerate([(45, 76), (95, 112), (142, 61), (166, 134)], start=1):
        token = f"CALL-V06-{idx:02d}-GRID"
        anchors.append(token)
        c.circle(x * mm, y * mm, 4 * mm)
        c.setFont("Helvetica", 6)
        c.drawString((x + 5) * mm, (y + 2) * mm, token)
    c.saveState()
    c.translate(20 * mm, 155 * mm)
    c.rotate(90)
    rotate_token = "ROTATE-V06-NORTH-ELEVATION"
    anchors.append(rotate_token)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(0, 0, rotate_token)
    c.restoreState()
    headers = ["Mark", "Type", "Size", "Rating", "Note"]
    rows = []
    for row in range(1, 7):
        mark = f"DOOR-V06-{row:02d}"
        anchors.append(mark)
        rows.append([mark, "FRP", f"{800 + row * 25}x2100", f"F{row}0", f"Room {100 + row}"])
    draw_table(c, 192 * mm, 148 * mm, [22 * mm, 18 * mm, 25 * mm, 18 * mm, 26 * mm], 7 * mm, headers, rows, size=5)
    c.setFont("Helvetica", 5)
    tiny = "TINY-V06-REVISION-CLOUD-04"
    anchors.append(tiny)
    c.drawString(225 * mm, 40 * mm, tiny)
    c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Landscape technical drawing with grid, callouts, rotated north elevation text, schedule table, and tiny revision note.",
        pages=1,
        expected_anchors=anchors,
        tags=["landscape", "technical-drawing", "rotated-text", "tiny-text", "table"],
    )


def build_lab_report(cases: list[dict[str, Any]]) -> None:
    case_id = "V07"
    filename = "v07_lab_report_units_redactions_1p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    width, height = A4
    anchors: list[str] = []
    draw_header(c, "Lab report with units", case_id, 1, 1)
    report_id = "LAB-V07-2026-05-ABC"
    anchors.append(report_id)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(14 * mm, height - 30 * mm, report_id)
    c.setFont("Helvetica", 8)
    c.drawString(14 * mm, height - 40 * mm, "Patient: Jane Validation / DOB: 1980-02-03")
    c.setFillColor(colors.black)
    c.rect(126 * mm, height - 43 * mm, 50 * mm, 7 * mm, fill=1, stroke=0)
    redacted = "MRN-V07-REDACTED-BAR"
    anchors.append(redacted)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 6)
    c.drawString(128 * mm, height - 41 * mm, redacted)
    c.setFillColor(colors.HexColor("#263238"))
    headers = ["Test", "Result", "Unit", "Reference Range", "Flag", "Code"]
    rows = []
    tests = [
        ("Glucose", "102", "mg/dL", "70-99", "H"),
        ("Creatinine", "0.82", "mg/dL", "0.60-1.10", ""),
        ("eGFR", ">90", "mL/min/1.73m2", ">=60", ""),
        ("HbA1c", "5.8", "%", "4.0-5.6", "H"),
        ("TSH", "1.72", "uIU/mL", "0.40-4.00", ""),
        ("CRP", "<0.3", "mg/dL", "<0.5", ""),
        ("Vitamin D", "24.1", "ng/mL", "30-100", "L"),
    ]
    for row, (test, result, unit, ref, flag) in enumerate(tests, start=1):
        code = f"LABCODE-V07-{row:02d}"
        anchors.extend([code, f"{test}:{result}:{unit}"])
        rows.append([test, result, unit, ref, flag, code])
    draw_table(c, 14 * mm, height - 60 * mm, [34 * mm, 22 * mm, 33 * mm, 42 * mm, 18 * mm, 36 * mm], 9 * mm, headers, rows, size=6)
    c.setFont("Helvetica", 7)
    footer_note = "NOTE-V07-DELTA-CHECK: compare against 2026-04 specimen."
    anchors.append(footer_note)
    c.drawString(14 * mm, 32 * mm, footer_note)
    c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Clinical lab report with inequality values, units, reference ranges, flags, and a redacted MRN bar.",
        pages=1,
        expected_anchors=anchors,
        tags=["lab-report", "units", "inequalities", "redaction", "table"],
    )


def build_email_thread(cases: list[dict[str, Any]]) -> None:
    case_id = "V08"
    filename = "v08_email_thread_quote_table_2p.pdf"
    path = PDF_DIR / filename
    c = make_canvas(path)
    anchors: list[str] = []
    for page in range(1, 3):
        draw_header(c, "Email thread export", case_id, page, 2)
        width, height = A4
        thread_id = f"THREAD-V08-7788-P{page:02d}"
        anchors.append(thread_id)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(14 * mm, height - 29 * mm, f"Subject: Month-end exception review {thread_id}")
        c.setFont("Helvetica", 7)
        y = height - 42 * mm
        paragraphs = [
            f"From: controller@example.test  To: ops@example.test  Sent: 2026-05-0{page} 08:14",
            f"Please confirm the three exceptions below. Control token {case_id}-EMAIL-OPEN-P{page:02d}.",
            "> Prior message: the attached spreadsheet totals did not match the GL export.",
            f"> Quoted anchor {case_id}-QUOTE-P{page:02d}-DEPTH2 must remain visible.",
            "Code-like snippet: if variance > threshold: escalate(row_id)",
        ]
        for text in paragraphs:
            anchors.extend([part for part in text.split() if part.startswith(case_id)])
            y = draw_wrapped(c, text, 14 * mm, y, 176 * mm, size=7, leading=8)
            y -= 2 * mm
        headers = ["Exception", "Owner", "Amount", "Resolution"]
        rows = []
        for row in range(1, 6):
            exception = f"EXC-V08-P{page:02d}-R{row:02d}"
            anchors.append(exception)
            rows.append([exception, f"user{row}@example.test", f"{row * 811}.07", "pending" if row % 2 else "cleared"])
        draw_table(c, 14 * mm, y - 4 * mm, [48 * mm, 48 * mm, 36 * mm, 44 * mm], 8 * mm, headers, rows, size=6)
        c.showPage()
    c.save()
    add_case(
        cases,
        case_id=case_id,
        filename=filename,
        description="Email thread PDF export with headers, quoted replies, code-like text, and exception table.",
        pages=2,
        expected_anchors=anchors,
        tags=["email-export", "quoted-text", "code", "table"],
    )


def download_pdf(url: str, target: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "docling-poc-validation/1.0 nyham@example.test",
            "Accept": "application/pdf,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        target.write_bytes(response.read())


def add_web_cases(cases: list[dict[str, Any]], *, skip_download: bool) -> None:
    web_cases = [
        {
            "case_id": "W01",
            "filename": "w01_irs_form_w9.pdf",
            "url": "https://www.irs.gov/pub/irs-pdf/fw9.pdf",
            "description": "Official IRS Form W-9 PDF, a fillable tax form with checkboxes and instructions.",
            "pages": 6,
            "expected_anchors": [
                "Form W-9",
                "Request for Taxpayer",
                "Give form to the requester",
                "Part I",
                "Certification",
                "www.irs.gov/FormW9",
            ],
            "tags": ["web", "official-form", "fillable", "checkbox", "instructions"],
        },
        {
            "case_id": "W02",
            "filename": "w02_sec_form_10k.pdf",
            "url": "https://www.sec.gov/files/form10-k.pdf",
            "description": "Official SEC Form 10-K PDF with dense legal instructions and tabular cover-page fields.",
            "pages": 19,
            "expected_anchors": [
                "UNITED STATES",
                "SECURITIES AND EXCHANGE COMMISSION",
                "FORM 10-K",
                "ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d)",
                "Commission File Number",
                "GENERAL INSTRUCTIONS",
            ],
            "tags": ["web", "official-form", "dense-legal", "long-document"],
        },
    ]
    for item in web_cases:
        target = PDF_DIR / item["filename"]
        if not skip_download:
            download_pdf(str(item["url"]), target)
        add_case(
            cases,
            case_id=str(item["case_id"]),
            filename=str(item["filename"]),
            description=str(item["description"]),
            pages=int(item["pages"]),
            expected_anchors=list(item["expected_anchors"]),
            tags=list(item["tags"]),
            source_url=str(item["url"]),
        )


def build_suite(skip_web: bool = False) -> dict[str, Any]:
    ensure_dirs()
    register_fonts()
    cases: list[dict[str, Any]] = []
    build_invoice(cases)
    build_bank_statement(cases)
    build_application_form(cases)
    build_japanese_mixed(cases)
    build_scanned_receipt(cases)
    build_technical_drawing(cases)
    build_lab_report(cases)
    build_email_thread(cases)
    build_image_layer_stamps(cases)
    build_complex_structured_table(cases)
    build_excel_long_table_pdf(cases)
    add_web_cases(cases, skip_download=skip_web)
    manifest = {
        "generated_at": "2026-05-06",
        "suite": "pdf_validation_command_guide_realistic_cases",
        "output_dir": str(OUT_DIR),
        "notes": [
            "Synthetic cases contain exact anchors for omission checks.",
            "Web cases use a small set of stable public-form anchors.",
            "The image-only case is intentionally difficult and should exercise IMAGE_RECONCILE/OCR paths.",
            "The image-layer stamp case keeps normal body text in the PDF text layer while stamp text exists only inside embedded raster images.",
            "The complex table case stresses multi-row headers, subtotal rows, multi-line cells, and conditional notes.",
            "The Excel-like long table case stresses tiny repeated headers across many landscape pages.",
        ],
        "cases": cases,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-web", action="store_true", help="Do not download web PDFs.")
    args = parser.parse_args()
    manifest = build_suite(skip_web=args.skip_web)
    print(f"Wrote {len(manifest['cases'])} cases to {MANIFEST_PATH}")
    for case in manifest["cases"]:
        print(f"{case['case_id']} {case['filename']} anchors={len(case['expected_anchors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
