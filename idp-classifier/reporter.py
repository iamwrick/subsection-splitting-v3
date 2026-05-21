from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from models import DocumentResult

_ZONE_COLORS = {
    "Logbook Entry":                  colors.HexColor("#D6EAF8"),
    "Authorized Release Certificate": colors.HexColor("#D5F5E3"),
    "Work Card":                      colors.HexColor("#FCF3CF"),
    "Certificate of Aircraft Registration": colors.HexColor("#E8DAEF"),
    "Standard Airworthiness Certificate":   colors.HexColor("#FAD7A0"),
    "Major Repair and Alteration":    colors.HexColor("#FADBD8"),
    "Statement of Compliance":        colors.HexColor("#D1F2EB"),
    "Supplemental Type Certificate":  colors.HexColor("#FDEBD0"),
    "Aircraft Flight Manual Supplement": colors.HexColor("#A9CCE3"),
    "Instructions for Continued Airworthiness": colors.HexColor("#A9DFBF"),
    "Airworthiness Directive":        colors.HexColor("#F9E79F"),
    "Service Bulletin":               colors.HexColor("#A9DFBF"),
    "Status Report":                  colors.HexColor("#D6DBDF"),
    "Other":                          colors.HexColor("#EAECEE"),
}
_DEFAULT_COLOR = colors.HexColor("#F2F3F4")


def _confidence_bar(value: float, width: int = 20) -> str:
    filled = round(value * width)
    return "█" * filled + "░" * (width - filled)


def generate_pdf(result: DocumentResult, pdf_path: str) -> None:
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title2",
        parent=styles["Title"],
        fontSize=18,
        spaceAfter=4,
        textColor=colors.HexColor("#1A252F"),
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#5D6D7E"),
        spaceAfter=2,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=colors.HexColor("#1A252F"),
        spaceBefore=14,
        spaceAfter=6,
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
    )
    alt_style = ParagraphStyle(
        "Alt",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#5D6D7E"),
        leading=11,
    )

    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    story.append(Paragraph("IDP Document Zone Classification Report", title_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", subtitle_style))
    story.append(Paragraph(f"Input file: <b>{result.input_file}</b>", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1A252F"), spaceAfter=10))

    # ── Summary stats ────────────────────────────────────────────────────────
    story.append(Paragraph("Summary", section_style))

    summary_data = [
        ["Total Pages", "Total Chunks", "Total Zones"],
        [str(result.total_pages), str(result.total_chunks), str(len(result.zones))],
    ]
    summary_table = Table(summary_data, colWidths=[2.2 * inch, 2.2 * inch, 2.2 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A252F")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 10),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE",   (0, 1), (-1, 1), 16),
        ("FONTNAME",   (0, 1), (-1, 1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F8F9FA")]),
    ]))
    story.append(summary_table)

    # ── Zone table ───────────────────────────────────────────────────────────
    story.append(Paragraph("Zone Classifications", section_style))

    header = ["#", "Document Type", "Pages", "Confidence", "Alternatives"]
    rows = [header]

    for idx, zone in enumerate(result.zones, start=1):
        pct = f"{zone.confidence * 100:.0f}%"
        bar = _confidence_bar(zone.confidence)

        if zone.alternatives:
            alt_lines = "\n".join(
                f"{a.document_type} ({a.confidence * 100:.0f}%)"
                for a in zone.alternatives
            )
        else:
            alt_lines = "—"

        page_range = (
            str(zone.start_page)
            if zone.start_page == zone.end_page
            else f"{zone.start_page} – {zone.end_page}"
        )

        rows.append([
            Paragraph(str(idx), cell_style),
            Paragraph(f"<b>{zone.document_type}</b>", cell_style),
            Paragraph(page_range, cell_style),
            Paragraph(f"<b>{pct}</b>\n<font name='Courier' size='7'>{bar}</font>", cell_style),
            Paragraph(alt_lines, alt_style),
        ])

    col_widths = [0.35 * inch, 2.5 * inch, 0.85 * inch, 1.55 * inch, 1.65 * inch]
    zone_table = Table(rows, colWidths=col_widths, repeatRows=1)

    table_style = [
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#1A252F")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
        ("ALIGN",         (2, 1), (2, -1), "CENTER"),
        ("ALIGN",         (3, 1), (3, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#BDC3C7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FA")]),
    ]

    for i, zone in enumerate(result.zones, start=1):
        bg = _ZONE_COLORS.get(zone.document_type, _DEFAULT_COLOR)
        table_style.append(("BACKGROUND", (1, i), (1, i), bg))

    zone_table.setStyle(TableStyle(table_style))
    story.append(zone_table)

    # ── Footer note ──────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#BDC3C7")))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Classified by Amazon Nova 2 Lite via Amazon Bedrock · IDP Document Zone Classifier",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7,
                       textColor=colors.HexColor("#95A5A6"), alignment=1),
    ))

    doc.build(story)
