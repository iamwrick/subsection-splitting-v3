"""Generate a combined OCR + rendered_text comparison PDF report."""

import json
import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, KeepTogether, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Palette ───────────────────────────────────────────────────────────────────
C_DARK  = colors.HexColor("#1A252F")
C_GREEN = colors.HexColor("#1E8449")
C_AMBER = colors.HexColor("#D35400")
C_RED   = colors.HexColor("#C0392B")
C_BLUE  = colors.HexColor("#1A5276")
C_LGREY = colors.HexColor("#F2F3F4")
C_MGREY = colors.HexColor("#BDC3C7")
C_WHITE = colors.white

def _tc(pct):
    if pct >= 90: return colors.HexColor("#D5F5E3")
    if pct >= 70: return colors.HexColor("#FCF3CF")
    return colors.HexColor("#FADBD8")

def _pct(n, d): return 0.0 if d == 0 else 100 * n / d


# ── Known issues catalogue ────────────────────────────────────────────────────
# Folders where OCR>=85% but text<70%: the rendered_text.txt lacks the enriched hints
# that our JSON converter generates, so the model must rely on content alone.
HINT_LIMITED = {"130923", "130924", "139494", "159403", "161071", "188237", "188731"}

KNOWN_ISSUES = {
    "131247": {
        "reason": "Four Corners Aviation MyCMP Work Cards have no CAMP/MYCMP text in OCR — "
                  "branding is likely image-only. Model cannot distinguish from LBE content.",
        "gt_note": None,
    },
    "139270": {
        "reason": "PNC Aviation maintenance tracking report pages lack a formal logbook header. "
                  "Model classifies based on aircraft identifiers (N-number, AFTT) → LBE.",
        "gt_note": "GT alternates Other/LBE for visually similar pages; boundary is ambiguous.",
    },
    "139493": {
        "reason": "ARC pages (form:8130) interleaved with LBE MTR pages cause model to over-extend "
                  "ARC zone across adjacent MTR pages despite [LBE] hints.",
        "gt_note": None,
    },
    "188233": {
        "reason": "365-page Metro Aviation multi-aircraft maintenance file archive. Model classifies "
                  "entire document as Logbook Entry based on dominant maintenance content despite "
                  "[WORK_ORDER_DETAIL] hint on page 1.",
        "gt_note": None,
    },
    "131243": {
        "reason": "Page 2 is a blank aviation log template (column headers only: LOG PAGE #, DATE, "
                  "TAIL #). Has [RETURN_TO_SERVICE] from body text → classified as LBE.",
        "gt_note": None,
    },
    "130918": {
        "reason": "rendered_text.txt contains only 2 pages (60-61) of a 61-page document. "
                  "GT covers all 61 pages → scores appear low due to missing coverage.",
        "gt_note": "Data limitation: rendered_text.txt is a partial extract.",
    },
    "131176": {
        "reason": "rendered_text.txt contains 85 pages (non-sequential) vs 232 in ocr.json. "
                  "GT covers 135 zones across the full document.",
        "gt_note": "Data limitation: rendered_text.txt is a partial extract.",
    },
    "163747": {
        "reason": "rendered_text.txt contains 66 pages vs 249 in ocr.json with 226 GT zones. "
                  "Most GT zones are outside the rendered_text page range.",
        "gt_note": "Data limitation: rendered_text.txt is a partial extract.",
    },
    "130917": {
        "reason": "rendered_text.txt starts at page 9 (missing pages 1-8). GT covers pages 1-119.",
        "gt_note": "Data limitation: rendered_text.txt is a partial extract.",
    },
    "158550": {
        "reason": "rendered_text.txt has 65 pages vs 127 in ocr.json. GT covers 23 zones across full document.",
        "gt_note": "Data limitation: rendered_text.txt is a partial extract.",
    },
    "139261": {
        "reason": "rendered_text.txt uses annotated page headers (## PAGE N -- FORM_8130-3 ##). "
                  "Chunker now updated to handle this format.",
        "gt_note": None,
    },
    "139494": {
        "reason": "Single-page document. Type may vary between runs due to ambiguous content.",
        "gt_note": None,
    },
}


def load_results(out_dir: str) -> dict[str, dict]:
    results = {}
    for folder in sorted(os.listdir(out_dir)):
        ep = os.path.join(out_dir, folder, "eval.json")
        op = os.path.join(out_dir, folder, "output.json")
        if not os.path.exists(ep):
            continue
        with open(ep) as f: ev = json.load(f)
        pages = 0
        if os.path.exists(op):
            with open(op) as f: pages = json.load(f).get("total_pages", 0)
        tok = ev.get("token_usage", {})
        results[folder] = {
            "pages": pages,
            "total_gt_zones": ev.get("total_gt_zones", 0),
            "exact_matches": ev.get("exact_matches", 0),
            "type_only_matches": ev.get("type_only_matches", 0),
            "page_only_matches": ev.get("page_only_matches", 0),
            "misses": ev.get("misses", 0),
            "exact_accuracy": ev.get("exact_accuracy", 0.0),
            "type_accuracy": ev.get("type_accuracy", 0.0),
            "zone_results": ev.get("zone_results", []),
            "input_tokens":       tok.get("input_tokens", 0),
            "output_tokens":      tok.get("output_tokens", 0),
            "cache_write_tokens": tok.get("cache_write_tokens", 0),
            "cache_read_tokens":  tok.get("cache_read_tokens", 0),
            "cost_usd":           tok.get("cost_usd", 0.0),
        }
    return results


def generate_combined_report(ocr_dir: str, txt_dir: str, pdf_path: str,
                             txt_enriched_dir: str | None = None) -> None:
    ocr  = load_results(ocr_dir)
    txt  = load_results(txt_dir)
    txte = load_results(txt_enriched_dir) if txt_enriched_dir else {}
    all_folders = sorted(set(ocr) | set(txt))

    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        pdf_path, pagesize=LETTER,
        leftMargin=0.6*inch, rightMargin=0.6*inch,
        topMargin=0.65*inch, bottomMargin=0.65*inch,
    )

    styles = getSampleStyleSheet()
    def sty(name, **kw):
        return ParagraphStyle(name, parent=styles.get("Normal", styles["Normal"]), **kw)

    h1   = sty("H1",  fontSize=20, spaceAfter=4,  textColor=C_DARK, fontName="Helvetica-Bold")
    h2   = sty("H2",  fontSize=13, spaceBefore=12, spaceAfter=4, textColor=C_DARK, fontName="Helvetica-Bold")
    h3   = sty("H3",  fontSize=10, spaceBefore=8,  spaceAfter=3, textColor=C_DARK, fontName="Helvetica-Bold")
    sub  = sty("Sub", fontSize=9,  textColor=colors.HexColor("#5D6D7E"), spaceAfter=2)
    body = sty("Bod", fontSize=8.5, leading=12)
    sm   = sty("Sm",  fontSize=7.5, leading=11, textColor=colors.HexColor("#5D6D7E"))
    red  = sty("Red", fontSize=7.5, leading=11, textColor=C_RED)
    amb  = sty("Amb", fontSize=7.5, leading=11, textColor=C_AMBER)
    grn  = sty("Grn", fontSize=7.5, leading=11, textColor=C_GREEN)
    note = sty("Nte", fontSize=7.5, leading=11, textColor=C_BLUE,
               backColor=colors.HexColor("#EBF5FB"), borderPadding=3)

    story = []

    # ── Cover ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("IDP Document Zone Classifier", h1))
    story.append(Paragraph("Combined Evaluation Report: OCR.json vs Rendered Text",
        sty("Cover", fontSize=13, textColor=colors.HexColor("#5D6D7E"), spaceAfter=4)))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", sub))
    story.append(Paragraph(f"Documents evaluated: <b>{len(all_folders)}</b>  |  "
        f"Model: Amazon Nova 2 Lite (us.amazon.nova-2-lite-v1:0)", sub))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_DARK, spaceAfter=14))

    # ── Aggregate stats ───────────────────────────────────────────────────────
    def agg(results):
        ex  = sum(r["exact_matches"] for r in results.values())
        typ = sum(r["exact_matches"] + r["type_only_matches"] for r in results.values())
        gt  = sum(r["total_gt_zones"] for r in results.values())
        pg  = sum(r["pages"] for r in results.values())
        return ex, typ, gt, pg

    oe, ot, og, op = agg(ocr)
    te, tt, tg, tp = agg(txt)
    ee, et, eg, _  = agg(txte) if txte else (0, 0, tg, 0)

    def cost_str(results_dict):
        c = sum(r.get("cost_usd", 0) for r in results_dict.values())
        return f"${c:.4f}"

    def tokens_str(results_dict):
        i  = sum(r.get("input_tokens", 0) for r in results_dict.values())
        o  = sum(r.get("output_tokens", 0) for r in results_dict.values())
        cr = sum(r.get("cache_read_tokens", 0) for r in results_dict.values())
        cw = sum(r.get("cache_write_tokens", 0) for r in results_dict.values())
        base = f"{i:,} in / {o:,} out"
        if cr or cw:
            base += f" / {cw:,} cache-write / {cr:,} cache-read"
        return base

    story.append(Paragraph("Aggregate Statistics", h2))

    has_enriched = bool(txte)
    if has_enriched:
        cols  = ["Metric", "OCR Input", "Text\n(no hints)", "Text\n(+enriched hints)"]
        cws   = [2.4*inch, 1.5*inch, 1.5*inch, 1.9*inch]
        rows_ = [
            ["Total pages classified",  f"{op:,}", f"{tp:,}", f"{tp:,}"],
            ["Total GT zones",          str(og), str(tg), str(eg)],
            ["Overall exact accuracy",  f"{_pct(oe,og):.1f}%", f"{_pct(te,tg):.1f}%", f"{_pct(ee,eg):.1f}%"],
            ["Overall type accuracy",   f"{_pct(ot,og):.1f}%", f"{_pct(tt,tg):.1f}%", f"{_pct(et,eg):.1f}%"],
            ["Folders at 100% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']>=0.999)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']>=0.999)}/{len(txt)}",
                f"{sum(1 for r in txte.values() if r['type_accuracy']>=0.999)}/{len(txte)}"],
            ["Folders ≥ 90% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']>=0.90)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']>=0.90)}/{len(txt)}",
                f"{sum(1 for r in txte.values() if r['type_accuracy']>=0.90)}/{len(txte)}"],
            ["Folders < 70% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']<0.70)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']<0.70)}/{len(txt)}",
                f"{sum(1 for r in txte.values() if r['type_accuracy']<0.70)}/{len(txte)}"],
            ["── Cost (Nova 2 Lite) ──", "", "", ""],
            ["Tokens (in / out)", tokens_str(ocr), tokens_str(txt), tokens_str(txte)],
            ["Total cost (USD)",  cost_str(ocr),   cost_str(txt),   cost_str(txte)],
        ]
        hi_col = (3, 0)
    else:
        txt_adj = {k: v for k, v in txt.items() if k not in HINT_LIMITED}
        te_adj, tt_adj, tg_adj, _ = agg(txt_adj)
        cols  = ["Metric", "OCR Input", "Text (All)", "Text (Adjusted★)"]
        cws   = [2.4*inch, 1.5*inch, 1.5*inch, 1.9*inch]
        rows_ = [
            ["Total pages classified", f"{op:,}", f"{tp:,}", "—"],
            ["Total GT zones",         str(og), str(tg), str(tg_adj)],
            ["Overall exact accuracy", f"{_pct(oe,og):.1f}%", f"{_pct(te,tg):.1f}%", f"{_pct(te_adj,tg_adj):.1f}%"],
            ["Overall type accuracy",  f"{_pct(ot,og):.1f}%", f"{_pct(tt,tg):.1f}%", f"{_pct(tt_adj,tg_adj):.1f}%"],
            ["Folders at 100% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']>=0.999)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']>=0.999)}/{len(txt)}",
                f"{sum(1 for r in txt_adj.values() if r['type_accuracy']>=0.999)}/{len(txt_adj)}"],
            ["Folders ≥ 90% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']>=0.90)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']>=0.90)}/{len(txt)}",
                f"{sum(1 for r in txt_adj.values() if r['type_accuracy']>=0.90)}/{len(txt_adj)}"],
            ["Folders < 70% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']<0.70)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']<0.70)}/{len(txt)}",
                f"{sum(1 for r in txt_adj.values() if r['type_accuracy']<0.70)}/{len(txt_adj)}"],
            ["── Cost (Nova 2 Lite) ──", "", "", ""],
            ["Tokens (in / out)", tokens_str(ocr), tokens_str(txt), "—"],
            ["Total cost (USD)",  cost_str(ocr),   cost_str(txt),  "—"],
        ]
        hi_col = (3, 0)

    agg_data = [cols] + rows_
    at = Table(agg_data, colWidths=cws)
    ts_agg = [
        ("BACKGROUND",    (0,0), (-1,0), C_DARK), ("TEXTCOLOR", (0,0), (-1,0), C_WHITE),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 8.5),
        ("ALIGN",         (1,0), (-1,-1), "CENTER"),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LGREY]),
        ("GRID",          (0,0), (-1,-1), 0.4, C_MGREY),
        ("TOPPADDING",    (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("BACKGROUND",    hi_col, hi_col, C_BLUE),
        ("FONTNAME",      (hi_col[0],1), (hi_col[0],-1), "Helvetica-Bold"),
    ]
    at.setStyle(TableStyle(ts_agg))
    story.append(at)

    if has_enriched:
        story.append(Paragraph(
            "Enriched hints: our converter's hint detectors ([form:8130], [form:8050], "
            "[SERVICE_BULLETIN], [AIRWORTHINESS_DIRECTIVE], [STANDALONE_PAGE], etc.) are "
            "applied to the rendered_text.txt content at classification time. This closes "
            "the gap between text and OCR input by adding signals the upstream system did "
            "not pre-compute.",
            sty("Note2", fontSize=8, textColor=C_BLUE, spaceBefore=6, spaceAfter=4,
                backColor=colors.HexColor("#EBF5FB"), borderPadding=4)))
    else:
        story.append(Paragraph(
            "★ Adjusted = excluding 7 hint-limited folders. "
            f"Affected: {', '.join(sorted(HINT_LIMITED))}.",
            sty("Note2", fontSize=8, textColor=C_BLUE, spaceBefore=6, spaceAfter=4,
                backColor=colors.HexColor("#EBF5FB"), borderPadding=4)))
    story.append(PageBreak())

    # ── Per-folder comparison table ───────────────────────────────────────────
    story.append(Paragraph("Per-Document Results", h2))
    story.append(Paragraph(
        "Green ≥ 90% · Amber 70–89% · Red < 70%  |  ⚠ = known issue · 📋 = data limitation",
        sm))
    story.append(Spacer(1, 4))

    show_enriched = bool(txte)
    if show_enriched:
        hdr = ["Folder", "Pages", "GT\nZones", "OCR\nExact",
               "OCR\nType%", "Txt\nType%", "Txt+Hints\nType%", "OCR\nCost $"]
        cw2 = [1.0*inch, 0.5*inch, 0.46*inch, 0.65*inch,
               0.58*inch, 0.58*inch, 0.72*inch, 0.65*inch]
    else:
        hdr = ["Folder", "Pages", "GT\nZones",
               "OCR\nExact", "OCR\nType%", "Text\nType%", "Delta", "OCR\nCost $"]
        cw2 = [1.0*inch, 0.55*inch, 0.46*inch, 0.65*inch, 0.58*inch, 0.58*inch, 0.6*inch, 0.65*inch]

    rows = [hdr]
    for folder in all_folders:
        r_ocr  = ocr.get(folder, {})
        r_txt  = txt.get(folder, {})
        r_txte = txte.get(folder, {}) if txte else {}
        gt    = r_ocr.get("total_gt_zones", r_txt.get("total_gt_zones", 0))
        pages = r_ocr.get("pages", r_txt.get("pages", 0))
        ot_pct  = r_ocr.get("type_accuracy", 0.0) * 100
        tt_pct  = r_txt.get("type_accuracy", 0.0) * 100
        tte_pct = r_txte.get("type_accuracy", 0.0) * 100 if r_txte else 0.0
        ex    = r_ocr.get("exact_matches", 0)
        delta = tt_pct - ot_pct
        ki    = KNOWN_ISSUES.get(folder)
        hl    = folder in HINT_LIMITED
        flag  = "🔸" if hl else ("📋" if ki and ki.get("gt_note") else ("⚠" if ki else ""))
        ocr_cost = f"${r_ocr.get('cost_usd', 0.0):.4f}" if r_ocr else "—"
        if show_enriched:
            rows.append([
                f"{folder} {flag}", str(pages), str(gt), f"{ex}/{gt}",
                f"{ot_pct:.0f}%", f"{tt_pct:.0f}%", f"{tte_pct:.0f}%",
                ocr_cost,
            ])
        else:
            rows.append([
                f"{folder} {flag}", str(pages), str(gt), f"{ex}/{gt}",
                f"{ot_pct:.0f}%", f"{tt_pct:.0f}%",
                f"{delta:+.0f}pp" if delta != 0 else "—",
                ocr_cost,
            ])

    ft = Table(rows, colWidths=cw2, repeatRows=1)
    ts = [
        ("BACKGROUND",    (0,0), (-1,0), C_DARK), ("TEXTCOLOR", (0,0), (-1,0), C_WHITE),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 7.5),
        ("ALIGN",         (1,0), (-1,-1), "CENTER"), ("ALIGN", (0,1), (0,-1), "LEFT"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.3, C_MGREY),
        ("TOPPADDING",    (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LGREY]),
    ]
    for i, folder in enumerate(all_folders, start=1):
        ot_pct = ocr.get(folder, {}).get("type_accuracy", 0.0) * 100
        tt_pct = txt.get(folder, {}).get("type_accuracy", 0.0) * 100
        ts.append(("BACKGROUND", (4,i), (4,i), _tc(ot_pct)))
        ts.append(("BACKGROUND", (5,i), (5,i), _tc(tt_pct)))
    ft.setStyle(TableStyle(ts))
    story.append(ft)
    story.append(PageBreak())

    # ── Issue detail pages ────────────────────────────────────────────────────
    story.append(Paragraph("Per-Document Detail & Issues", h2))

    for folder in all_folders:
        r_ocr = ocr.get(folder)
        r_txt = txt.get(folder)
        if not r_ocr and not r_txt:
            continue

        ot_pct = (r_ocr.get("type_accuracy", 0.0) if r_ocr else 0.0) * 100
        tt_pct = (r_txt.get("type_accuracy", 0.0) if r_txt else 0.0) * 100
        has_issue = ot_pct < 90 or tt_pct < 70
        ki = KNOWN_ISSUES.get(folder)

        # Header
        header_clr = _tc(min(ot_pct, tt_pct if r_txt else ot_pct))
        gt = r_ocr.get("total_gt_zones", r_txt.get("total_gt_zones", 0)) if r_ocr or r_txt else 0
        pages = r_ocr.get("pages", r_txt.get("pages", 0)) if r_ocr or r_txt else 0

        hrow = [[
            Paragraph(f"<b>{folder}</b>", sty("FH2", fontSize=10, textColor=C_DARK)),
            Paragraph(
                f"Pages: {pages}  |  GT Zones: {gt}  |  "
                f"OCR Type: <b>{ot_pct:.0f}%</b>  |  Text Type: <b>{tt_pct:.0f}%</b>",
                sty("FD2", fontSize=8, textColor=C_DARK)),
        ]]
        ht = Table(hrow, colWidths=[1.2*inch, 5.6*inch])
        ht.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), header_clr),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("BOX",           (0,0),(-1,-1), 0.5, C_DARK),
        ]))
        story.append(KeepTogether([ht, Spacer(1, 3)]))

        # No issues
        if not has_issue and not ki:
            story.append(Paragraph("✓ Both inputs correct — no issues.",
                sty("OK2", fontSize=8, textColor=C_GREEN, spaceAfter=8)))
            continue

        # Hint-limited note
        if folder in HINT_LIMITED:
            story.append(Paragraph(
                "🔸 <b>Hint-limited (excluded from adjusted text accuracy):</b> "
                "OCR input achieves ≥85% type accuracy because our JSON converter enriches pages "
                "with classification hints ([form:8050], [STANDALONE_PAGE], [SERVICE_BULLETIN] etc.). "
                "The rendered_text.txt lacks these hints so the model classifies from content alone, "
                "resulting in lower accuracy. This is a pre-processing difference, not a fundamental "
                "classifier failure.",
                sty("HL", fontSize=8, textColor=C_BLUE,
                    backColor=colors.HexColor("#EBF5FB"), borderPadding=3, spaceAfter=4)))

        # Known issue note
        if ki:
            reason_txt = ki["reason"]
            gt_note    = ki.get("gt_note")
            story.append(Paragraph(f"<b>Issue:</b> {reason_txt}", amb))
            if gt_note:
                story.append(Paragraph(f"<b>Ground-truth note:</b> {gt_note}", note))

        # OCR wrong-type table
        if r_ocr:
            wrong_ocr = [z for z in r_ocr["zone_results"] if not z.get("type_match", True)]
            if wrong_ocr:
                story.append(Paragraph(f"OCR wrong-type zones ({len(wrong_ocr)}):", h3))
                w_rows = [["GT Pages", "GT Type", "Predicted Type", "Conf"]]
                for z in wrong_ocr[:8]:
                    w_rows.append([
                        z["gt_pages"],
                        z["gt_type"],
                        z.get("pred_type") or "—",
                        f"{z['pred_confidence']*100:.0f}%" if z.get("pred_confidence") else "—",
                    ])
                if len(wrong_ocr) > 8:
                    w_rows.append(["…", f"+{len(wrong_ocr)-8} more", "", ""])
                wt = Table(w_rows, colWidths=[0.8*inch, 2.2*inch, 2.2*inch, 0.6*inch])
                wt.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0),(-1,0), colors.HexColor("#FADBD8")),
                    ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
                    ("FONTSIZE",      (0,0),(-1,-1), 7.5),
                    ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
                    ("TOPPADDING",    (0,0),(-1,-1), 3), ("BOTTOMPADDING",(0,0),(-1,-1), 3),
                    ("LEFTPADDING",   (0,0),(-1,-1), 5),
                    ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
                ]))
                story.append(wt)

        # Text wrong-type table
        if r_txt:
            wrong_txt = [z for z in r_txt["zone_results"] if not z.get("type_match", True)]
            if wrong_txt:
                story.append(Paragraph(f"Text wrong-type zones ({len(wrong_txt)}):", h3))
                w_rows = [["GT Pages", "GT Type", "Predicted Type", "Conf"]]
                for z in wrong_txt[:8]:
                    w_rows.append([
                        z["gt_pages"],
                        z["gt_type"],
                        z.get("pred_type") or "—",
                        f"{z['pred_confidence']*100:.0f}%" if z.get("pred_confidence") else "—",
                    ])
                if len(wrong_txt) > 8:
                    w_rows.append(["…", f"+{len(wrong_txt)-8} more", "", ""])
                wt2 = Table(w_rows, colWidths=[0.8*inch, 2.2*inch, 2.2*inch, 0.6*inch])
                wt2.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0),(-1,0), colors.HexColor("#FEF9E7")),
                    ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
                    ("FONTSIZE",      (0,0),(-1,-1), 7.5),
                    ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
                    ("TOPPADDING",    (0,0),(-1,-1), 3), ("BOTTOMPADDING",(0,0),(-1,-1), 3),
                    ("LEFTPADDING",   (0,0),(-1,-1), 5),
                    ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
                ]))
                story.append(wt2)

        # Boundary-only note
        if r_ocr:
            bnd = sum(1 for z in r_ocr["zone_results"]
                      if z.get("type_match") and not z.get("exact_match"))
            if bnd > 0:
                story.append(Paragraph(
                    f"Boundary precision: {bnd} zone(s) have correct type but imprecise page boundaries "
                    f"(counted in type accuracy but not exact accuracy).", sm))

        story.append(Spacer(1, 8))

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_MGREY))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "IDP Document Zone Classifier · Amazon Nova 2 Lite (us.amazon.nova-2-lite-v1:0) · "
        f"Report generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        sty("Foot", fontSize=7, textColor=colors.HexColor("#95A5A6"), alignment=1)))

    doc.build(story)
    print(f"Combined report saved: {pdf_path}")
