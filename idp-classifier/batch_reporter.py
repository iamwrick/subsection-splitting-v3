"""Generate a comprehensive batch evaluation PDF report."""

import json
import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle, KeepTogether,
)

# ── Colour palette ────────────────────────────────────────────────────────────
C_DARK   = colors.HexColor("#1A252F")
C_GREEN  = colors.HexColor("#1E8449")
C_AMBER  = colors.HexColor("#D35400")
C_RED    = colors.HexColor("#C0392B")
C_LGREY  = colors.HexColor("#F2F3F4")
C_MGREY  = colors.HexColor("#BDC3C7")
C_WHITE  = colors.white

# ── Accuracy tier colours ─────────────────────────────────────────────────────
def tier_color(pct: float) -> colors.Color:
    if pct >= 90: return colors.HexColor("#D5F5E3")   # green
    if pct >= 70: return colors.HexColor("#FCF3CF")   # amber
    return colors.HexColor("#FADBD8")                 # red

def tier_text_color(pct: float) -> colors.Color:
    if pct >= 90: return C_GREEN
    if pct >= 70: return C_AMBER
    return C_RED


def _pct(n, d):
    return 0.0 if d == 0 else 100 * n / d


def load_results(output_dir: str) -> list[dict]:
    """Load all eval.json files from the batch output directory."""
    results = []
    for folder in sorted(os.listdir(output_dir)):
        eval_path = os.path.join(output_dir, folder, "eval.json")
        out_path  = os.path.join(output_dir, folder, "output.json")
        if not os.path.exists(eval_path):
            continue
        with open(eval_path) as f:
            ev = json.load(f)
        pages = 0
        if os.path.exists(out_path):
            with open(out_path) as f:
                pages = json.load(f).get("total_pages", 0)
        results.append({
            "folder": folder,
            "pages": pages,
            "total_gt_zones": ev.get("total_gt_zones", 0),
            "exact_matches": ev.get("exact_matches", 0),
            "type_only_matches": ev.get("type_only_matches", 0),
            "page_only_matches": ev.get("page_only_matches", 0),
            "misses": ev.get("misses", 0),
            "exact_accuracy": ev.get("exact_accuracy", 0.0),
            "type_accuracy": ev.get("type_accuracy", 0.0),
            "zone_results": ev.get("zone_results", []),
            "input_tokens":  ev.get("token_usage", {}).get("input_tokens", 0),
            "output_tokens": ev.get("token_usage", {}).get("output_tokens", 0),
            "cost_usd":      ev.get("token_usage", {}).get("cost_usd", 0.0),
        })
    return results


def generate_batch_report(output_dir: str, pdf_path: str) -> None:
    results = load_results(output_dir)
    if not results:
        print(f"No eval.json files found in {output_dir}")
        return

    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=LETTER,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        topMargin=0.65 * inch,  bottomMargin=0.65 * inch,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Title"],   fontSize=20, spaceAfter=4,  textColor=C_DARK)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"],fontSize=13, spaceBefore=14, spaceAfter=4, textColor=C_DARK)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"],fontSize=11, spaceBefore=8,  spaceAfter=2, textColor=C_DARK)
    sub = ParagraphStyle("Sub",parent=styles["Normal"], fontSize=9,  textColor=colors.HexColor("#5D6D7E"), spaceAfter=2)
    body = ParagraphStyle("Body",parent=styles["Normal"],fontSize=9, leading=13)
    small = ParagraphStyle("Small",parent=styles["Normal"],fontSize=8,leading=11,textColor=colors.HexColor("#5D6D7E"))
    issue = ParagraphStyle("Issue",parent=styles["Normal"],fontSize=8,leading=11,textColor=C_RED)

    story = []

    # ── Cover ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("IDP Document Zone Classifier", h1))
    story.append(Paragraph("Batch Evaluation Report", ParagraphStyle(
        "Sub2", parent=styles["Normal"], fontSize=14, textColor=colors.HexColor("#5D6D7E"), spaceAfter=4)))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", sub))
    story.append(Paragraph(f"Total documents evaluated: <b>{len(results)}</b>", sub))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_DARK, spaceAfter=16))

    # ── Aggregate stats ───────────────────────────────────────────────────────
    total_gt    = sum(r["total_gt_zones"] for r in results)
    total_ex    = sum(r["exact_matches"] for r in results)
    total_type  = sum(r["exact_matches"] + r["type_only_matches"] for r in results)
    total_pages = sum(r["pages"] for r in results)
    total_in    = sum(r["input_tokens"]  for r in results)
    total_out   = sum(r["output_tokens"] for r in results)
    total_cost  = sum(r["cost_usd"]      for r in results)
    at100  = sum(1 for r in results if r["type_accuracy"] >= 0.999)
    ge90   = sum(1 for r in results if r["type_accuracy"] >= 0.90)
    ge70   = sum(1 for r in results if r["type_accuracy"] >= 0.70)
    issues = sum(1 for r in results if r["type_accuracy"] < 0.70)

    story.append(Paragraph("Overall Summary", h2))
    agg = [
        ["Metric", "Value"],
        ["Total documents",             str(len(results))],
        ["Total pages classified",      f"{total_pages:,}"],
        ["Total GT zones",              str(total_gt)],
        ["Overall exact accuracy",      f"{_pct(total_ex, total_gt):.1f}%"],
        ["Overall type accuracy",       f"{_pct(total_type, total_gt):.1f}%"],
        ["Documents at 100% type acc.", f"{at100} / {len(results)}"],
        ["Documents ≥ 90% type acc.",   f"{ge90} / {len(results)}"],
        ["Documents < 70% (issues)",    f"{issues} / {len(results)}"],
        ["─── Cost (Nova 2 Lite) ───",  ""],
        ["Total input tokens",          f"{total_in:,}"],
        ["Total output tokens",         f"{total_out:,}"],
        ["Total cost (USD)",            f"${total_cost:.4f}"],
        ["Cost per document (avg)",     f"${total_cost/len(results):.4f}" if results else "—"],
        ["Cost per page (avg)",         f"${total_cost/total_pages:.5f}" if total_pages else "—"],
    ]
    agg_table = Table(agg, colWidths=[3.5*inch, 2.5*inch])
    agg_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), C_DARK),
        ("TEXTCOLOR",     (0,0), (-1,0), C_WHITE),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LGREY]),
        ("GRID",          (0,0), (-1,-1), 0.4, C_MGREY),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
    ]))
    story.append(agg_table)

    # ── Accuracy distribution chart (ASCII-style bar) ─────────────────────────
    story.append(Spacer(1, 10))
    tiers = [
        ("100%",         sum(1 for r in results if r["type_accuracy"] >= 0.999), C_GREEN),
        ("90–99%",       sum(1 for r in results if 0.90 <= r["type_accuracy"] < 0.999), colors.HexColor("#58D68D")),
        ("70–89%",       sum(1 for r in results if 0.70 <= r["type_accuracy"] < 0.90), C_AMBER),
        ("< 70%",        sum(1 for r in results if r["type_accuracy"] < 0.70), C_RED),
    ]
    tier_data = [["Type Accuracy Tier", "Count", "Share"]]
    for label, cnt, _ in tiers:
        tier_data.append([label, str(cnt), f"{_pct(cnt, len(results)):.0f}%"])

    tier_table = Table(tier_data, colWidths=[2.0*inch, 1.0*inch, 1.0*inch])
    tier_style = [
        ("BACKGROUND",    (0,0), (-1,0), C_DARK),
        ("TEXTCOLOR",     (0,0), (-1,0), C_WHITE),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("ALIGN",         (1,0), (-1,-1), "CENTER"),
        ("GRID",          (0,0), (-1,-1), 0.4, C_MGREY),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
    ]
    for i, (_, _, clr) in enumerate(tiers, start=1):
        tier_style.append(("BACKGROUND", (0, i), (-1, i), clr))
    tier_table.setStyle(TableStyle(tier_style))
    story.append(tier_table)
    story.append(PageBreak())

    # ── Per-document summary table ────────────────────────────────────────────
    story.append(Paragraph("Per-Document Results", h2))
    story.append(Paragraph(
        "Colour coding: green ≥ 90%, amber 70–89%, red < 70% type accuracy.", small))
    story.append(Spacer(1, 6))

    hdr = ["Folder", "Pages", "GT\nZones", "Exact", "Type\nAcc.",
           "Input\nTokens", "Output\nTokens", "Cost\n(USD)"]
    rows = [hdr]
    for r in results:
        ta_pct = r["type_accuracy"] * 100
        rows.append([
            r["folder"],
            str(r["pages"]),
            str(r["total_gt_zones"]),
            f"{r['exact_matches']}/{r['total_gt_zones']}",
            f"{ta_pct:.0f}%",
            f"{r.get('input_tokens', 0):,}",
            f"{r.get('output_tokens', 0):,}",
            f"${r.get('cost_usd', 0.0):.4f}",
        ])

    cw = [1.0*inch, 0.48*inch, 0.48*inch, 0.68*inch, 0.58*inch, 0.72*inch, 0.72*inch, 0.64*inch]
    sum_table = Table(rows, colWidths=cw, repeatRows=1)
    ts = [
        ("BACKGROUND",    (0,0), (-1,0), C_DARK),
        ("TEXTCOLOR",     (0,0), (-1,0), C_WHITE),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 8),
        ("ALIGN",         (1,0), (-1,-1), "CENTER"),
        ("ALIGN",         (0,1), (0,-1), "LEFT"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.3, C_MGREY),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
    ]
    for i, r in enumerate(results, start=1):
        bg = tier_color(r["type_accuracy"] * 100)
        ts.append(("BACKGROUND", (4, i), (4, i), bg))
        ts.append(("TEXTCOLOR",  (4, i), (4, i), tier_text_color(r["type_accuracy"] * 100)))
        ts.append(("FONTNAME",   (4, i), (4, i), "Helvetica-Bold"))
    sum_table.setStyle(TableStyle(ts))
    story.append(sum_table)
    story.append(PageBreak())

    # ── Per-document detail pages ─────────────────────────────────────────────
    story.append(Paragraph("Per-Document Detail", h2))

    for r in results:
        ta_pct  = r["type_accuracy"] * 100
        ex_pct  = r["exact_accuracy"] * 100
        wrong   = [z for z in r["zone_results"] if not z.get("type_match", True)]
        correct = [z for z in r["zone_results"] if z.get("exact_match", False)]

        # Section header block
        header_color = tier_color(ta_pct)
        section_rows = [[
            Paragraph(f"<b>{r['folder']}</b>", ParagraphStyle("FH", parent=styles["Normal"], fontSize=11, textColor=C_DARK)),
            Paragraph(
                f"Pages: <b>{r['pages']}</b>  |  GT zones: <b>{r['total_gt_zones']}</b>  |  "
                f"Exact: <b>{r['exact_matches']}/{r['total_gt_zones']} ({ex_pct:.0f}%)</b>  |  "
                f"Type acc: <b>{ta_pct:.0f}%</b>",
                ParagraphStyle("FD", parent=styles["Normal"], fontSize=8, textColor=C_DARK)
            ),
        ]]
        sect_table = Table(section_rows, colWidths=[1.3*inch, 5.5*inch])
        sect_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), header_color),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING",    (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
            ("LEFTPADDING",   (0,0), (-1,-1), 8),
            ("BOX",           (0,0), (-1,-1), 0.5, C_DARK),
        ]))
        story.append(KeepTogether([sect_table, Spacer(1, 4)]))

        if not wrong and not r["misses"] and ta_pct >= 99:
            story.append(Paragraph("✓ All zones classified correctly.",
                ParagraphStyle("OK", parent=styles["Normal"], fontSize=8, textColor=C_GREEN, spaceAfter=8)))
            continue

        # Wrong-type zones
        if wrong:
            story.append(Paragraph(f"Wrong-type zones ({len(wrong)}):",
                ParagraphStyle("SH", parent=styles["Normal"], fontSize=8, fontName="Helvetica-Bold",
                               textColor=C_RED, spaceAfter=2)))
            w_rows = [["GT Pages", "GT Type", "Predicted Type", "Conf"]]
            for z in wrong:
                pp = z.get("pred_pages") or "—"
                pt = z.get("pred_type") or "—"
                conf = f"{z['pred_confidence']*100:.0f}%" if z.get("pred_confidence") else "—"
                w_rows.append([z["gt_pages"], z["gt_type"], pt, conf])
            wt = Table(w_rows, colWidths=[0.8*inch, 2.2*inch, 2.2*inch, 0.6*inch])
            wt.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#FADBD8")),
                ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",      (0,0), (-1,-1), 7.5),
                ("GRID",          (0,0), (-1,-1), 0.3, C_MGREY),
                ("TOPPADDING",    (0,0), (-1,-1), 3),
                ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ("LEFTPADDING",   (0,0), (-1,-1), 5),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGREY]),
            ]))
            story.append(wt)

        # Misses
        if r["misses"] > 0:
            missed = [z for z in r["zone_results"] if z.get("pred_type") is None]
            story.append(Spacer(1, 3))
            story.append(Paragraph(f"Missing zones ({r['misses']}):",
                ParagraphStyle("SH2", parent=styles["Normal"], fontSize=8, fontName="Helvetica-Bold",
                               textColor=C_AMBER, spaceAfter=2)))
            for z in missed[:5]:
                story.append(Paragraph(f"  • GT pages {z['gt_pages']}: {z['gt_type']} — no predicted zone found", small))
            if len(missed) > 5:
                story.append(Paragraph(f"  … and {len(missed)-5} more", small))

        # Boundary-only issues (correct type, wrong boundary)
        boundary_only = [z for z in r["zone_results"]
                         if z.get("type_match") and not z.get("exact_match") and not z.get("pages_match")]
        if boundary_only:
            story.append(Spacer(1, 3))
            story.append(Paragraph(f"Boundary precision issues ({len(boundary_only)} zones correct type, wrong boundary):",
                ParagraphStyle("SH3", parent=styles["Normal"], fontSize=8, fontName="Helvetica-Bold",
                               textColor=C_AMBER, spaceAfter=2)))

        story.append(Spacer(1, 8))

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_MGREY))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Classified by Amazon Nova 2 Lite via Amazon Bedrock · IDP Document Zone Classifier",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7,
                       textColor=colors.HexColor("#95A5A6"), alignment=1)))

    doc.build(story)
    print(f"Batch report saved to {pdf_path}")
