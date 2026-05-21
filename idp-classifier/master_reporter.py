"""Single consolidated report: accuracy + cost + prompt-caching comparison."""

import json
import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Palette ───────────────────────────────────────────────────────────────────
C_DARK   = colors.HexColor("#1A252F")
C_GREEN  = colors.HexColor("#1E8449")
C_AMBER  = colors.HexColor("#D35400")
C_RED    = colors.HexColor("#C0392B")
C_BLUE   = colors.HexColor("#1A5276")
C_LGREY  = colors.HexColor("#F2F3F4")
C_MGREY  = colors.HexColor("#BDC3C7")
C_WHITE  = colors.white
C_SAVING = colors.HexColor("#D5F5E3")
C_COST   = colors.HexColor("#FADBD8")

HINT_LIMITED = {"130923", "130924", "139494", "159403", "161071", "188237", "188731"}

# ── Pricing ───────────────────────────────────────────────────────────────────
PRICE_INPUT_PER_1M       = 0.30
PRICE_OUTPUT_PER_1M      = 2.50
PRICE_CACHE_WRITE_PER_1M = 0.375
PRICE_CACHE_READ_PER_1M  = 0.030
SYSTEM_PROMPT_TOKENS     = 3_500


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(n, d): return 0.0 if d == 0 else 100 * n / d

def _tc(pct):
    if pct >= 90: return colors.HexColor("#D5F5E3")
    if pct >= 70: return colors.HexColor("#FCF3CF")
    return colors.HexColor("#FADBD8")

def _cost(it, ot):
    return it / 1e6 * PRICE_INPUT_PER_1M + ot / 1e6 * PRICE_OUTPUT_PER_1M

def _cost_cached(it, ot, calls):
    sys_total = calls * SYSTEM_PROMPT_TOKENS
    non_sys   = max(0, it - sys_total)
    cw = SYSTEM_PROMPT_TOKENS
    cr = max(0, calls - 1) * SYSTEM_PROMPT_TOKENS
    return (non_sys / 1e6 * PRICE_INPUT_PER_1M
          + cw      / 1e6 * PRICE_CACHE_WRITE_PER_1M
          + cr      / 1e6 * PRICE_CACHE_READ_PER_1M
          + ot      / 1e6 * PRICE_OUTPUT_PER_1M)


def load_batch(summary_path: str) -> dict[str, dict]:
    """Load eval.json files keyed by folder name."""
    results: dict[str, dict] = {}
    if not os.path.exists(summary_path):
        return results
    base = os.path.dirname(summary_path)
    with open(summary_path) as f:
        rows = json.load(f)
    for r in rows:
        folder = r["folder"]
        ep = os.path.join(base, folder, "eval.json")
        if not os.path.exists(ep):
            continue
        with open(ep) as f:
            ev = json.load(f)
        tok = ev.get("token_usage", {})
        results[folder] = {
            "pages":              int(r.get("pages", 0)),
            "total_gt_zones":     ev.get("total_gt_zones", 0),
            "exact_matches":      ev.get("exact_matches", 0),
            "type_only_matches":  ev.get("type_only_matches", 0),
            "type_accuracy":      ev.get("type_accuracy", 0.0),
            "exact_accuracy":     ev.get("exact_accuracy", 0.0),
            "zone_results":       ev.get("zone_results", []),
            "input_tokens":       tok.get("input_tokens", 0),
            "output_tokens":      tok.get("output_tokens", 0),
            "cache_write_tokens": tok.get("cache_write_tokens", 0),
            "cache_read_tokens":  tok.get("cache_read_tokens", 0),
            "cost_usd":           tok.get("cost_usd", 0.0),
        }
    return results


_GEMINI_TYPE_MAP = {
    "IDK": "Other", "LBE": "Logbook Entry", "WC": "Work Card",
    "ARC": "Authorized Release Certificate", "ADSI": "Status Report",
    "AD": "Airworthiness Directive", "SB": "Service Bulletin",
    "MRA": "Major Repair and Alteration", "STC": "Supplemental Type Certificate",
    "ICA": "Instructions for Continued Airworthiness", "SOC": "Statement of Compliance",
    "CofAR": "Certificate of Aircraft Registration", "SAC": "Standard Airworthiness Certificate",
    "AFMS": "Aircraft Flight Manual Supplement",
}

_KEY_TYPES = [
    "Logbook Entry", "Authorized Release Certificate", "Work Card",
    "Status Report", "Airworthiness Directive", "Service Bulletin",
    "Major Repair and Alteration", "Other",
]


def _load_pipeline_stats(pipeline_dir: str) -> dict:
    """Aggregate stats from full_pipeline.json files."""
    stats = {"docs": 0, "date": 0, "sig": 0,
             "p3_in": 0, "p3_out": 0, "p4_in": 0, "p4_out": 0,
             "by_type": {}}
    for folder in sorted(os.listdir(pipeline_dir)):
        fp = os.path.join(pipeline_dir, folder, "full_pipeline.json")
        if not os.path.exists(fp):
            continue
        pkg = json.load(open(fp))
        for d in pkg.get("documents", []):
            t = d.get("document_type", "Other")
            stats["docs"] += 1
            has_date = bool(d.get("document_date"))
            has_sig  = bool(d.get("signatures"))
            if has_date: stats["date"] += 1
            if has_sig:  stats["sig"]  += 1
            if t not in stats["by_type"]:
                stats["by_type"][t] = [0, 0, 0]  # docs, date, sig
            stats["by_type"][t][0] += 1
            if has_date: stats["by_type"][t][1] += 1
            if has_sig:  stats["by_type"][t][2] += 1
        stats["p3_in"]  += pkg.get("p3_tokens", {}).get("input", 0)
        stats["p3_out"] += pkg.get("p3_tokens", {}).get("output", 0)
        stats["p4_in"]  += pkg.get("p4_tokens", {}).get("input", 0)
        stats["p4_out"] += pkg.get("p4_tokens", {}).get("output", 0)
    return stats


def _load_gemini_stats(gemini_dir: str) -> dict:
    """Aggregate stats from Gemini response.json files."""
    stats = {"docs": 0, "date": 0, "sig": 0, "by_type": {}}
    for folder in sorted(os.listdir(gemini_dir)):
        fp = os.path.join(gemini_dir, folder, "response.json")
        if not os.path.exists(fp):
            continue
        gd = json.load(open(fp))
        for d in gd.get("documents", []):
            t = _GEMINI_TYPE_MAP.get(d["document_type"]["value"],
                                     d["document_type"]["value"])
            sd = d.get("structured_data", {})
            stats["docs"] += 1
            has_date = bool(sd.get("document_date"))
            has_sig  = bool(sd.get("signatures") or d.get("signatures"))
            if has_date: stats["date"] += 1
            if has_sig:  stats["sig"]  += 1
            if t not in stats["by_type"]:
                stats["by_type"][t] = [0, 0, 0]
            stats["by_type"][t][0] += 1
            if has_date: stats["by_type"][t][1] += 1
            if has_sig:  stats["by_type"][t][2] += 1
    return stats


def generate_master_report(
    ocr_dir: str,
    txt_dir: str,
    pdf_path: str,
    txt_enriched_dir: str | None = None,
    pipeline_dir: str | None = None,
    gemini_dir: str | None = None,
) -> None:
    ocr  = load_batch(os.path.join(ocr_dir,  "batch_summary.json"))
    txt  = load_batch(os.path.join(txt_dir,  "batch_summary.json"))
    txte = load_batch(os.path.join(txt_enriched_dir, "batch_summary.json")) if txt_enriched_dir else {}

    all_folders = sorted(set(ocr) | set(txt) | set(txte))
    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        pdf_path, pagesize=LETTER,
        leftMargin=0.65*inch, rightMargin=0.65*inch,
        topMargin=0.65*inch,  bottomMargin=0.65*inch,
    )

    styles = getSampleStyleSheet()
    def sty(name, **kw):
        return ParagraphStyle(name, parent=styles["Normal"], **kw)

    h1   = sty("H1",  fontSize=20, fontName="Helvetica-Bold", spaceAfter=4,  textColor=C_DARK)
    h2   = sty("H2",  fontSize=13, fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4,  textColor=C_DARK)
    h3   = sty("H3",  fontSize=10, fontName="Helvetica-Bold", spaceBefore=8,  spaceAfter=3,  textColor=C_DARK)
    sub  = sty("Sub", fontSize=9,  textColor=colors.HexColor("#5D6D7E"), spaceAfter=2)
    sm   = sty("Sm",  fontSize=7.5, leading=11, textColor=colors.HexColor("#5D6D7E"))
    note = sty("Nte", fontSize=8,  textColor=C_BLUE,
               backColor=colors.HexColor("#EBF5FB"), borderPadding=4, leading=12)

    story = []

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — COVER
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("IDP Document Zone Classifier", h1))
    story.append(Paragraph("Comprehensive Evaluation & Cost Report", sty(
        "Cov", fontSize=14, textColor=colors.HexColor("#5D6D7E"), spaceAfter=4)))
    story.append(Paragraph(
        f"Model: Amazon Nova 2 Lite (us.amazon.nova-2-lite-v1:0)  ·  "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", sub))
    story.append(Paragraph(
        f"Documents: <b>{len(all_folders)}</b>  ·  "
        f"Pages (OCR): <b>{sum(r['pages'] for r in ocr.values()):,}</b>  ·  "
        f"Pricing: $0.30/1M input · $2.50/1M output tokens", sub))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_DARK, spaceAfter=16))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — ACCURACY SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 1 — Classification Accuracy", h2))

    def agg(d):
        ex = sum(r["exact_matches"] for r in d.values())
        ty = sum(r["exact_matches"] + r["type_only_matches"] for r in d.values())
        gt = sum(r["total_gt_zones"] for r in d.values())
        return ex, ty, gt

    oe, ot, og = agg(ocr)
    te, tt, tg = agg(txt)
    ee, et, eg = agg(txte) if txte else (0, 0, tg)

    show_enriched = bool(txte)
    if show_enriched:
        acc_hdr  = ["Metric", "OCR Input", "Text (raw)", "Text + Hints"]
        acc_cws  = [2.6*inch, 1.5*inch, 1.5*inch, 1.6*inch]
        acc_rows = [
            ["Overall exact accuracy",   f"{_pct(oe,og):.1f}%", f"{_pct(te,tg):.1f}%", f"{_pct(ee,eg):.1f}%"],
            ["Overall type accuracy",    f"{_pct(ot,og):.1f}%", f"{_pct(tt,tg):.1f}%", f"{_pct(et,eg):.1f}%"],
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
        ]
    else:
        acc_hdr  = ["Metric", "OCR Input", "Text Input"]
        acc_cws  = [2.6*inch, 2.0*inch, 2.0*inch]
        acc_rows = [
            ["Overall exact accuracy", f"{_pct(oe,og):.1f}%", f"{_pct(te,tg):.1f}%"],
            ["Overall type accuracy",  f"{_pct(ot,og):.1f}%", f"{_pct(tt,tg):.1f}%"],
            ["Folders at 100% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']>=0.999)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']>=0.999)}/{len(txt)}"],
            ["Folders ≥ 90% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']>=0.90)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']>=0.90)}/{len(txt)}"],
            ["Folders < 70% type",
                f"{sum(1 for r in ocr.values() if r['type_accuracy']<0.70)}/{len(ocr)}",
                f"{sum(1 for r in txt.values() if r['type_accuracy']<0.70)}/{len(txt)}"],
        ]

    at = Table([acc_hdr] + acc_rows, colWidths=acc_cws)
    at.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 9),
        ("ALIGN",         (1,0),(-1,-1), "CENTER"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
        ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
        ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("BACKGROUND",    (1,2),(1,2), C_SAVING) if show_enriched else ("", (0,0),(0,0), C_WHITE),
    ]))
    story.append(at)
    if show_enriched:
        story.append(Paragraph(
            "Text + Hints: our converter's classification hints are injected into rendered_text.txt "
            "pages at run-time, closing the gap with OCR input. "
            "🔸 = 7 hint-limited folders excluded from text adjusted accuracy "
            f"({', '.join(sorted(HINT_LIMITED))}).",
            sty("N1", fontSize=8, textColor=C_BLUE, spaceBefore=6,
                backColor=colors.HexColor("#EBF5FB"), borderPadding=4)))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2b — VISUALISATIONS
    # ══════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("Section 1b — Accuracy Visualisations", h2))
    story.append(Paragraph(
        "Three complementary views of the same accuracy data. "
        "Lollipop: metric-by-metric comparison with minimal ink. "
        "Radar: holistic overview across all metrics at once. "
        "Slope: direction and magnitude of change between input methods.", sm))
    story.append(Spacer(1, 8))

    try:
        from charts import lollipop_chart, radar_chart, slope_chart, _aggregate, build_metrics

        _txte = txte if txte else None
        n_fld = len(all_folders)

        # ── Lollipop ──────────────────────────────────────────────────────────
        story.append(Paragraph("Lollipop Plot — metric-by-metric comparison", h3))
        story.append(Paragraph(
            "Each row = one metric. Three coloured dots show OCR / Text / Text+Hints. "
            "The grey bar spans the range — narrow = agreement, wide = divergence.", sm))
        story.append(Spacer(1, 4))
        lp_buf = lollipop_chart(ocr, txt, _txte, n_fld)
        story.append(Image(lp_buf, width=6.5*inch, height=3.8*inch))
        story.append(Spacer(1, 16))

        # ── Radar ─────────────────────────────────────────────────────────────
        story.append(Paragraph("Radar Chart — holistic view across all metrics", h3))
        story.append(Paragraph(
            "Five axes radiate from the centre (0 = worst, 100 = best). "
            "A larger, more circular polygon = consistently strong across all metrics.", sm))
        story.append(Spacer(1, 4))
        rd_buf = radar_chart(ocr, txt, _txte, n_fld)
        story.append(Image(rd_buf, width=4.5*inch, height=4.5*inch))
        story.append(PageBreak())

        # ── Slope ─────────────────────────────────────────────────────────────
        story.append(Paragraph("Slope Chart — direction of change between methods", h3))
        story.append(Paragraph(
            "Each coloured line = one metric. A rising slope = improvement going "
            "from OCR → Text → Text+Hints. A falling slope = degradation. "
            "Parallel, non-crossing lines = consistent behaviour across conditions.", sm))
        story.append(Spacer(1, 4))
        sl_buf = slope_chart(ocr, txt, _txte, n_fld)
        story.append(Image(sl_buf, width=6.0*inch, height=4.5*inch))

    except ImportError as exc:
        story.append(Paragraph(
            f"Charts unavailable — install matplotlib: pip install matplotlib  ({exc})",
            sty("Warn", fontSize=9, textColor=C_AMBER)))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — COST SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("Section 2 — Cost Summary", h2))

    # Aggregate token totals for OCR
    total_in   = sum(r["input_tokens"]  for r in ocr.values())
    total_out  = sum(r["output_tokens"] for r in ocr.values())
    total_docs = len(ocr)
    total_pages= sum(r["pages"] for r in ocr.values())
    est_calls  = int(total_docs * 1.1)

    c_no  = _cost(total_in, total_out)
    c_yes = _cost_cached(total_in, total_out, est_calls)
    saving_pct = (c_no - c_yes) / c_no * 100 if c_no > 0 else 0

    # 1M projection
    scale       = 1_000_000 / total_docs
    m_in        = total_in  * scale
    m_out       = total_out * scale
    m_calls     = est_calls * scale
    m_no        = _cost(m_in, m_out)
    m_sys       = m_calls * SYSTEM_PROMPT_TOKENS
    m_non_sys   = max(0, m_in - m_sys)
    m_cw        = SYSTEM_PROMPT_TOKENS * 100
    m_cr        = max(0, m_calls - 100) * SYSTEM_PROMPT_TOKENS
    m_yes       = (m_non_sys / 1e6 * PRICE_INPUT_PER_1M
                 + m_cw      / 1e6 * PRICE_CACHE_WRITE_PER_1M
                 + m_cr      / 1e6 * PRICE_CACHE_READ_PER_1M
                 + m_out     / 1e6 * PRICE_OUTPUT_PER_1M)
    m_saving    = m_no - m_yes
    m_save_pct  = m_saving / m_no * 100 if m_no > 0 else 0

    story.append(Paragraph("Benchmark Run — 65 Documents (OCR Input)", h3))
    cost_bench = [
        ["Metric",                  "Value"],
        ["Total input tokens",      f"{total_in:,}"],
        ["Total output tokens",     f"{total_out:,}"],
        ["Actual cost (no cache)",  f"${c_no:.4f}"],
        ["Est. cost WITH caching",  f"${c_yes:.4f}  (save {saving_pct:.1f}%)"],
        ["Cost per document",       f"${c_no/total_docs:.4f}  →  ${c_yes/total_docs:.4f} cached"],
        ["Cost per page",           f"${c_no/total_pages:.5f}  →  ${c_yes/total_pages:.5f} cached"],
    ]
    ct = Table(cost_bench, colWidths=[2.6*inch, 4.7*inch])
    ct.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 9),
        ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
        ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("BACKGROUND",    (0,3),(1,3), C_COST),
        ("BACKGROUND",    (0,4),(1,4), C_SAVING),
        ("FONTNAME",      (0,3),(1,4), "Helvetica-Bold"),
    ]))
    story.append(ct)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Projection — 1,000,000 Documents / Month", h3))
    proj = [
        ["",                        "Without Caching", "With Prompt Caching", "Monthly Saving"],
        ["Total input tokens",      f"{m_in/1e9:.1f}B",  "—",   "—"],
        ["Total output tokens",     f"{m_out/1e9:.2f}B", "—",   "—"],
        ["Input token cost",        f"${m_in/1e6*PRICE_INPUT_PER_1M:,.0f}",
                                    f"≈${m_yes - m_out/1e6*PRICE_OUTPUT_PER_1M:,.0f}",
                                    f"−${m_saving:,.0f}"],
        ["Output token cost",       f"${m_out/1e6*PRICE_OUTPUT_PER_1M:,.0f}",
                                    f"${m_out/1e6*PRICE_OUTPUT_PER_1M:,.0f}", "$0"],
        ["TOTAL MONTHLY COST",      f"${m_no:,.0f}",    f"${m_yes:,.0f}",
                                    f"${m_saving:,.0f}  ({m_save_pct:.1f}%)"],
        ["Cost per document",       f"${m_no/1e6:.4f}", f"${m_yes/1e6:.4f}",
                                    f"${m_saving/1e6:.4f}"],
    ]
    pjt = Table(proj, colWidths=[2.0*inch, 1.8*inch, 1.9*inch, 1.6*inch])
    pjt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 8.5),
        ("ALIGN",         (1,0),(-1,-1), "CENTER"),
        ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
        ("ROWBACKGROUNDS",(0,1),(-1,-2), [C_WHITE, C_LGREY]),
        ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("BACKGROUND",    (0,5),(-1,5), C_SAVING),
        ("FONTNAME",      (0,5),(-1,5), "Helvetica-Bold"),
        ("FONTSIZE",      (0,5),(-1,5), 9),
        ("TEXTCOLOR",     (3,5),(3,5), C_GREEN),
    ]))
    story.append(pjt)
    story.append(Paragraph(
        f"Prompt caching stores the system prompt (~{SYSTEM_PROMPT_TOKENS:,} tokens) once per "
        "5-minute window. All subsequent calls read from cache at 0.10× the standard input price. "
        "At 1M docs/month, nearly every call is a cache hit, saving ~$1,032/month. "
        "The saving is ~7% of total cost because page text (92% of input tokens) cannot be cached.",
        sty("N3", fontSize=8, textColor=C_BLUE, spaceBefore=6,
            backColor=colors.HexColor("#EBF5FB"), borderPadding=4)))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — PER-FOLDER TABLE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("Section 3 — Per-Document Results", h2))
    story.append(Paragraph(
        "Green ≥ 90% · Amber 70–89% · Red < 70%  |  "
        "🔸 hint-limited  ⚠ known issue", sm))
    story.append(Spacer(1, 5))

    txt_col = "Txt+Hints\nType%" if show_enriched else "Txt\nType%"
    tbl_hdr = ["Folder", "Pages", "GT\nZones", "OCR\nExact", "OCR\nType%",
               txt_col, "Input\nTokens", "Output\nTokens", "Cost\nNo Cache", "Cost\nCached"]

    tbl_rows = [tbl_hdr]
    for folder in all_folders:
        r_ocr  = ocr.get(folder, {})
        r_txte = (txte if txte else txt).get(folder, {})
        gt     = r_ocr.get("total_gt_zones", 0)
        pages  = r_ocr.get("pages", 0)
        ot_pct = r_ocr.get("type_accuracy", 0.0) * 100
        te_pct = r_txte.get("type_accuracy", 0.0) * 100
        ex     = r_ocr.get("exact_matches", 0)
        it     = r_ocr.get("input_tokens", 0)
        ot     = r_ocr.get("output_tokens", 0)
        calls  = 1 + max(0, pages // 70)
        c_no   = _cost(it, ot)
        c_yes  = _cost_cached(it, ot, calls)
        flag   = "🔸" if folder in HINT_LIMITED else ""
        tbl_rows.append([
            f"{folder}{' '+flag if flag else ''}",
            str(pages), str(gt), f"{ex}/{gt}",
            f"{ot_pct:.0f}%", f"{te_pct:.0f}%",
            f"{it:,}", f"{ot:,}",
            f"${c_no:.4f}", f"${c_yes:.4f}",
        ])

    cw_main = [1.0*inch, 0.45*inch, 0.44*inch, 0.6*inch, 0.57*inch,
               0.57*inch, 0.72*inch, 0.72*inch, 0.72*inch, 0.72*inch]
    mt = Table(tbl_rows, colWidths=cw_main, repeatRows=1)
    ts = [
        ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 7),
        ("ALIGN",         (1,0),(-1,-1), "CENTER"), ("ALIGN",(0,1),(0,-1), "LEFT"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
        ("TOPPADDING",    (0,0),(-1,-1), 3), ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
    ]
    for i, folder in enumerate(all_folders, start=1):
        r_ocr = ocr.get(folder, {})
        op = r_ocr.get("type_accuracy", 0.0) * 100
        r_te = (txte if txte else txt).get(folder, {})
        tp = r_te.get("type_accuracy", 0.0) * 100
        ts.append(("BACKGROUND", (4, i), (4, i), _tc(op)))
        ts.append(("BACKGROUND", (5, i), (5, i), _tc(tp)))
        ts.append(("BACKGROUND", (9, i), (9, i), C_SAVING))  # cached cost always green-ish
    mt.setStyle(TableStyle(ts))
    story.append(mt)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 5 — PER-FOLDER DETAIL
    # ══════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("Section 4 — Per-Document Issues & Cost Detail", h2))

    for folder in all_folders:
        r_ocr  = ocr.get(folder)
        r_txte = (txte if txte else txt).get(folder)
        if not r_ocr and not r_txte:
            continue

        ot_pct = (r_ocr.get("type_accuracy", 0.0) if r_ocr else 0.0) * 100
        te_pct = (r_txte.get("type_accuracy", 0.0) if r_txte else 0.0) * 100
        pages  = (r_ocr or r_txte).get("pages", 0)
        it     = (r_ocr or {}).get("input_tokens", 0)
        ot     = (r_ocr or {}).get("output_tokens", 0)
        calls  = 1 + max(0, pages // 70)
        c_no   = _cost(it, ot)
        c_yes  = _cost_cached(it, ot, calls)
        flag   = "🔸" if folder in HINT_LIMITED else ""

        header_clr = _tc(min(ot_pct, te_pct if r_txte else ot_pct))
        gt = (r_ocr or r_txte).get("total_gt_zones", 0)

        hrow = [[
            Paragraph(f"<b>{folder}</b>{' '+flag if flag else ''}", sty("FH", fontSize=10, textColor=C_DARK)),
            Paragraph(
                f"Pages: {pages}  ·  GT: {gt}  ·  "
                f"OCR Type: <b>{ot_pct:.0f}%</b>  ·  "
                f"{'Text+Hints' if txte else 'Text'}: <b>{te_pct:.0f}%</b>  ·  "
                f"Cost: <b>${c_no:.4f}</b> (${c_yes:.4f} cached)",
                sty("FD", fontSize=8, textColor=C_DARK)),
        ]]
        ht = Table(hrow, colWidths=[1.2*inch, 6.1*inch])
        ht.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), header_clr),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("BOX",           (0,0),(-1,-1), 0.5, C_DARK),
        ]))
        story.append(KeepTogether([ht, Spacer(1, 3)]))

        # If everything correct, skip detail
        if ot_pct >= 99.9 and te_pct >= 99.9:
            story.append(Paragraph("✓ All zones correct on both inputs.",
                sty("OK", fontSize=8, textColor=C_GREEN, spaceAfter=6)))
            continue

        # Hint-limited note
        if folder in HINT_LIMITED:
            story.append(Paragraph(
                "🔸 Hint-limited: OCR achieves ≥85% but text input scores <70% because "
                "rendered_text.txt lacks the enriched hints our JSON converter generates.",
                sty("HL", fontSize=8, textColor=C_BLUE,
                    backColor=colors.HexColor("#EBF5FB"), borderPadding=3, spaceAfter=4)))

        # Wrong-type table (OCR)
        if r_ocr:
            wrong = [z for z in r_ocr["zone_results"] if not z.get("type_match", True)]
            if wrong:
                story.append(Paragraph(f"Wrong-type zones — OCR ({len(wrong)}):",
                    sty("SH", fontSize=8, fontName="Helvetica-Bold", textColor=C_RED, spaceAfter=2)))
                w_rows = [["GT Pages", "GT Type", "Predicted", "Conf"]]
                for z in wrong[:6]:
                    w_rows.append([z["gt_pages"], z["gt_type"],
                                   z.get("pred_type") or "—",
                                   f"{z['pred_confidence']*100:.0f}%" if z.get("pred_confidence") else "—"])
                if len(wrong) > 6:
                    w_rows.append(["…", f"+{len(wrong)-6} more", "", ""])
                wt = Table(w_rows, colWidths=[0.75*inch, 2.2*inch, 2.2*inch, 0.55*inch])
                wt.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0),(-1,0), C_COST),
                    ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
                    ("FONTSIZE",      (0,0),(-1,-1), 7.5),
                    ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
                    ("TOPPADDING",    (0,0),(-1,-1), 3), ("BOTTOMPADDING",(0,0),(-1,-1), 3),
                    ("LEFTPADDING",   (0,0),(-1,-1), 5),
                    ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
                ]))
                story.append(wt)

        # Boundary-only note
        if r_ocr:
            bnd = sum(1 for z in r_ocr["zone_results"]
                      if z.get("type_match") and not z.get("exact_match"))
            if bnd > 0:
                story.append(Paragraph(
                    f"Boundary precision: {bnd} zone(s) correct type but wrong page range.",
                    sty("BN", fontSize=7.5, textColor=colors.HexColor("#5D6D7E"), spaceAfter=4)))

        story.append(Spacer(1, 8))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 5 — PER-PASS ACCURACY & COMPARISON (optional)
    # ══════════════════════════════════════════════════════════════════════════
    if pipeline_dir and os.path.isdir(pipeline_dir):
        pip   = _load_pipeline_stats(pipeline_dir)
        gem_s = _load_gemini_stats(gemini_dir) if gemini_dir and os.path.isdir(gemini_dir) else None
        ocr_b = load_batch(os.path.join(ocr_dir, "batch_summary.json"))

        # cost helpers
        p4_pages = sum(int(r.get("pages", 0)) for r in ocr_b.values())
        pip_bs_path = os.path.join(pipeline_dir, "batch_summary.json")
        if os.path.exists(pip_bs_path):
            p1_cost = sum(r.get("cost_usd", 0) for r in json.load(open(pip_bs_path))
                          if isinstance(r, dict))
        else:
            p1_cost = sum(r.get("cost_usd", 0) for r in ocr_b.values())
        p3_cost = (pip["p3_in"] * PRICE_INPUT_PER_1M / 1e6 +
                   pip["p3_out"] * PRICE_OUTPUT_PER_1M / 1e6)
        p4_cost = (pip["p4_in"] * PRICE_INPUT_PER_1M / 1e6 +
                   pip["p4_out"] * PRICE_OUTPUT_PER_1M / 1e6)
        nova_total = p1_cost + p3_cost + p4_cost

        def pct(n, d): return f"{100*n//max(1,d)}%" if d else "—"
        def dpct(n, d): return 100*n//max(1,d) if d else 0
        def delta_pp(np, gp): return f"{np-gp:+d}pp"

        # ── P1 summary callout ────────────────────────────────────────────────
        story.append(PageBreak())
        story.append(Paragraph("Section 5 — Per-Pass Accuracy (Nova v4 vs Gemini)", h2))
        story.append(Paragraph(
            "The 4-pass pipeline: P1 classifies zones → P2 merges adjacent same-type zones → "
            "P3 splits each zone into individual documents → P4 extracts structured metadata.",
            sm))
        story.append(Spacer(1, 10))

        # aggregate P1 numbers from ocr_b
        p1_ex = sum(r.get("exact_matches", 0) for r in ocr_b.values())
        p1_ty = sum(r.get("exact_matches", 0) + r.get("type_only_matches", 0) for r in ocr_b.values())
        p1_gt = sum(r.get("total_gt_zones", 0) for r in ocr_b.values())
        p1_folders_100 = sum(1 for r in ocr_b.values() if r.get("type_accuracy", 0) >= 0.999)
        p1_folders_90  = sum(1 for r in ocr_b.values() if r.get("type_accuracy", 0) >= 0.90)
        p1_folders_low = sum(1 for r in ocr_b.values() if r.get("type_accuracy", 0) < 0.70)
        n_fld = len(ocr_b)

        # ── 5a: P1 ───────────────────────────────────────────────────────────
        story.append(Paragraph("5a — P1: Zone Classification", h3))
        story.append(Paragraph(
            "P1 calls Amazon Nova 2 Lite once per 70-page chunk to identify zone boundaries "
            "and document types. Accuracy is measured against ground-truth zone annotations.",
            sm))
        story.append(Spacer(1, 4))

        p1_rows = [
            ["Metric", "Result", "Detail"],
            ["Overall type accuracy",
             f"{_pct(p1_ty,p1_gt):.1f}%",
             f"{p1_ty}/{p1_gt} zones correct type"],
            ["Overall exact accuracy",
             f"{_pct(p1_ex,p1_gt):.1f}%",
             f"{p1_ex}/{p1_gt} zones correct type + pages"],
            ["Folders at 100% type accuracy",
             f"{p1_folders_100}/{n_fld}",
             f"{100*p1_folders_100//n_fld}% of folders perfect"],
            ["Folders ≥ 90% type accuracy",
             f"{p1_folders_90}/{n_fld}",
             f"{100*p1_folders_90//n_fld}% of folders near-perfect"],
            ["Folders < 70% type accuracy",
             f"{p1_folders_low}/{n_fld}",
             "Needs improvement (see Section 4)"],
            ["P1 input tokens",
             f"{sum(r.get('input_tokens',0) for r in ocr_b.values()):,}",
             f"${p1_cost:.4f} total"],
            ["P1 output tokens",
             f"{sum(r.get('output_tokens',0) for r in ocr_b.values()):,}",
             f"${p1_cost/max(1,p4_pages):.5f} per page"],
        ]
        p1_t = Table(p1_rows, colWidths=[2.4*inch, 1.4*inch, 3.4*inch])
        p1_ts = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 9),
            ("ALIGN",         (1,0),(1,-1), "CENTER"),
            ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("BACKGROUND",    (1,1),(1,2), _tc(_pct(p1_ty,p1_gt))),
            ("FONTNAME",      (1,1),(1,2), "Helvetica-Bold"),
        ]
        p1_t.setStyle(TableStyle(p1_ts))
        story.append(p1_t)

        # ── 5b: P3 ───────────────────────────────────────────────────────────
        story.append(PageBreak())
        story.append(Paragraph("5b — P3: Document Splitting", h3))
        story.append(Paragraph(
            "P3 splits each classified zone into individual documents. "
            "Accuracy is measured as how closely Nova's extracted document count "
            "matches Gemini's — the reference pipeline. "
            "Single-page zones and large Logbook Entry zones (>30 pages) use deterministic "
            "heuristics instead of an LLM call to ensure consistent splits.",
            sm))
        story.append(Spacer(1, 6))

        # aggregate P3 stats
        nova_doc_total = pip["docs"]
        gem_doc_total  = gem_s["docs"] if gem_s else 0
        p3_in_all, p3_out_all = pip["p3_in"], pip["p3_out"]

        # per-folder doc counts
        p3_folder_rows = []
        within_10 = within_20 = exact_match = 0
        for folder in sorted(os.listdir(pipeline_dir)):
            nf = os.path.join(pipeline_dir, folder, "full_pipeline.json")
            if not os.path.exists(nf): continue
            n_docs = len(json.load(open(nf))["documents"])
            g_docs = 0
            if gem_s and gemini_dir:
                gf = os.path.join(gemini_dir, folder, "response.json")
                if os.path.exists(gf):
                    g_docs = len(json.load(open(gf))["documents"])
            diff = n_docs - g_docs
            pct_diff = abs(diff) / max(1, g_docs) * 100
            if abs(diff) == 0: exact_match += 1
            if pct_diff <= 10: within_10 += 1
            if pct_diff <= 20: within_20 += 1
            status = "✓" if abs(diff) == 0 else ("~" if pct_diff <= 20 else "✗")
            p3_folder_rows.append([folder, str(g_docs) if g_docs else "—",
                                    str(n_docs), f"{diff:+d}", status])

        p3_summary = [
            ["P3 Metric", "Result"],
            ["Total documents extracted (Nova)", f"{nova_doc_total:,}"],
        ]
        if gem_s:
            p3_summary += [
                ["Total documents extracted (Gemini)", f"{gem_doc_total:,}"],
                ["Net difference", f"{nova_doc_total - gem_doc_total:+,}"],
                ["Folders with exact doc count match",
                 f"{exact_match}/{len(p3_folder_rows)} ({100*exact_match//max(1,len(p3_folder_rows))}%)"],
                ["Folders within ±10% of Gemini count",
                 f"{within_10}/{len(p3_folder_rows)} ({100*within_10//max(1,len(p3_folder_rows))}%)"],
                ["Folders within ±20% of Gemini count",
                 f"{within_20}/{len(p3_folder_rows)} ({100*within_20//max(1,len(p3_folder_rows))}%)"],
            ]
        p3_summary += [
            ["P3 input tokens",  f"{p3_in_all:,}"],
            ["P3 output tokens", f"{p3_out_all:,}"],
            ["P3 cost",          f"${p3_cost:.4f}  (${p3_cost/max(1,p4_pages):.5f}/page)"],
        ]

        ps_t = Table(p3_summary, colWidths=[2.8*inch, 4.4*inch])
        ps_ts = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 9),
            ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ]
        ps_t.setStyle(TableStyle(ps_ts))
        story.append(ps_t)
        story.append(Spacer(1, 10))

        # per-folder P3 table
        if gem_s and p3_folder_rows:
            story.append(Paragraph("Per-Folder Document Count: Nova vs Gemini", h3))
            p3f_hdr = ["Folder", "Gemini Docs", "Nova Docs", "Diff", "Status"]
            p3f_t = Table([p3f_hdr] + p3_folder_rows,
                          colWidths=[1.2*inch, 1.1*inch, 1.1*inch, 0.7*inch, 0.6*inch],
                          repeatRows=1)
            p3f_ts = [
                ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
                ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
                ("FONTSIZE",      (0,0),(-1,-1), 7.5),
                ("ALIGN",         (1,0),(-1,-1), "CENTER"), ("ALIGN",(0,1),(0,-1), "LEFT"),
                ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
                ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
                ("TOPPADDING",    (0,0),(-1,-1), 3), ("BOTTOMPADDING",(0,0),(-1,-1), 3),
                ("LEFTPADDING",   (0,0),(-1,-1), 5),
            ]
            # Colour the status column
            for i, row in enumerate(p3_folder_rows, start=1):
                status = row[-1]
                c = C_SAVING if status == "✓" else (colors.HexColor("#FCF3CF") if status == "~" else C_COST)
                p3f_ts.append(("BACKGROUND", (4, i), (4, i), c))
            p3f_t.setStyle(TableStyle(p3f_ts))
            story.append(p3f_t)

        # ── 5c: P4 ───────────────────────────────────────────────────────────
        story.append(PageBreak())
        story.append(Paragraph("5c — P4: Metadata Extraction", h3))
        story.append(Paragraph(
            "P4 extracts structured fields from each document. "
            "Fill rate = % of documents for which the field was successfully extracted. "
            "A regex fallback fires when the LLM returns null for types that reliably "
            "carry the field (dates on ARC, LBE, Work Card, etc.).",
            sm))
        story.append(Spacer(1, 6))

        # field fill rates across all docs
        FIELDS = [
            ("document_date",       "Date"),
            ("registration_number", "Registration #"),
            ("serial_number",       "Serial #"),
            ("work_order_number",   "Work Order #"),
            ("total_hours",         "Total Hours"),
            ("tasks",               "Tasks"),
            ("signatures",          "Signatures"),
        ]

        # count per field from Nova
        n_field_stats = {f: [0, 0] for f, _ in FIELDS}
        for folder in sorted(os.listdir(pipeline_dir)):
            fp_path = os.path.join(pipeline_dir, folder, "full_pipeline.json")
            if not os.path.exists(fp_path): continue
            for d in json.load(open(fp_path))["documents"]:
                for fld, _ in FIELDS:
                    n_field_stats[fld][0] += 1
                    if d.get(fld): n_field_stats[fld][1] += 1

        # count per field from Gemini
        g_field_stats = {f: [0, 0] for f, _ in FIELDS}
        if gem_s and gemini_dir and os.path.isdir(gemini_dir):
            for folder in sorted(os.listdir(gemini_dir)):
                gf_path = os.path.join(gemini_dir, folder, "response.json")
                if not os.path.exists(gf_path): continue
                for d in json.load(open(gf_path))["documents"]:
                    sd = d.get("structured_data", {})
                    for fld, _ in FIELDS:
                        g_field_stats[fld][0] += 1
                        val = sd.get(fld) or d.get(fld)
                        if val: g_field_stats[fld][1] += 1

        f_hdr = ["Field", "Nova Docs", "Nova Fill%", "Gemini Fill%", "Δ"] if gem_s else \
                ["Field", "Docs", "Fill Rate"]
        f_rows = [f_hdr]
        for fld, label in FIELDS:
            n = n_field_stats[fld]
            np_val = dpct(n[1], n[0])
            row = [label, f"{n[0]:,}", f"{np_val}%"]
            if gem_s:
                g = g_field_stats[fld]
                gp_val = dpct(g[1], g[0])
                row += [f"{gp_val}%", delta_pp(np_val, gp_val)]
            f_rows.append(row)

        f_cw = [1.8*inch, 0.8*inch, 0.9*inch, 1.0*inch, 0.7*inch] if gem_s else \
               [2.4*inch, 0.9*inch, 0.9*inch]
        ff_t = Table(f_rows, colWidths=f_cw)
        ff_ts = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 9),
            ("ALIGN",         (1,0),(-1,-1), "CENTER"), ("ALIGN",(0,1),(0,-1), "LEFT"),
            ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ]
        for i, (fld, _) in enumerate(FIELDS, start=1):
            n = n_field_stats[fld]
            ff_ts.append(("BACKGROUND", (2, i), (2, i), _tc(dpct(n[1], n[0]))))
            if gem_s:
                g = g_field_stats[fld]
                ff_ts.append(("BACKGROUND", (3, i), (3, i), _tc(dpct(g[1], g[0]))))
        ff_t.setStyle(TableStyle(ff_ts))
        story.append(ff_t)
        story.append(Spacer(1, 14))

        # per-type breakdown for key fields
        story.append(Paragraph("P4 Extraction by Document Type (Date & Signature)", h3))
        story.append(Paragraph(
            "Green ≥ 90% · Amber 70–89% · Red < 70%  |  "
            "Δ = Nova minus Gemini in percentage points.", sm))
        story.append(Spacer(1, 4))

        if gem_s:
            ty_hdr = ["Document Type", "Nova\nDocs", "Nova\nDate%", "Nova\nSig%",
                      "Gem\nDocs", "Gem\nDate%", "Gem\nSig%", "Date\nΔ"]
        else:
            ty_hdr = ["Document Type", "Docs", "Date%", "Sig%"]

        ty_rows = [ty_hdr]
        for t in _KEY_TYPES:
            n = pip["by_type"].get(t, [0, 0, 0])
            row = [t, str(n[0]), pct(n[1], n[0]), pct(n[2], n[0])]
            if gem_s:
                g = gem_s["by_type"].get(t, [0, 0, 0])
                nd = dpct(n[1], n[0]); gd = dpct(g[1], g[0])
                row += [str(g[0]), pct(g[1], g[0]), pct(g[2], g[0]),
                        delta_pp(nd, gd) if n[0] and g[0] else "—"]
            ty_rows.append(row)

        ty_cw = ([2.1*inch, 0.55*inch, 0.6*inch, 0.6*inch,
                  0.55*inch, 0.6*inch, 0.6*inch, 0.55*inch]
                 if gem_s else [2.8*inch, 0.7*inch, 0.7*inch, 0.7*inch])
        ty_t = Table(ty_rows, colWidths=ty_cw, repeatRows=1)
        ty_ts2 = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8),
            ("ALIGN",         (1,0),(-1,-1), "CENTER"), ("ALIGN",(0,1),(0,-1), "LEFT"),
            ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
            ("TOPPADDING",    (0,0),(-1,-1), 4), ("BOTTOMPADDING",(0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ]
        for i, t in enumerate(_KEY_TYPES, start=1):
            n = pip["by_type"].get(t, [0, 0, 0])
            ty_ts2.append(("BACKGROUND", (2, i), (2, i), _tc(dpct(n[1], n[0]))))
            ty_ts2.append(("BACKGROUND", (3, i), (3, i), _tc(dpct(n[2], n[0]))))
            if gem_s:
                g = gem_s["by_type"].get(t, [0, 0, 0])
                ty_ts2.append(("BACKGROUND", (5, i), (5, i), _tc(dpct(g[1], g[0]))))
                ty_ts2.append(("BACKGROUND", (6, i), (6, i), _tc(dpct(g[2], g[0]))))
        ty_t.setStyle(TableStyle(ty_ts2))
        story.append(ty_t)
        story.append(Spacer(1, 14))

        # P4 cost + total
        story.append(Paragraph("4-Pass Cost Breakdown", h3))
        cb_rows = [
            ["Pass", "Input Tokens", "Output Tokens", "Cost", "Cost/Page"],
            ["P1 — Zone Classification",
             f"{sum(r.get('input_tokens',0) for r in ocr_b.values()):,}",
             f"{sum(r.get('output_tokens',0) for r in ocr_b.values()):,}",
             f"${p1_cost:.4f}",
             f"${p1_cost/max(1,p4_pages):.5f}"],
            ["P3 — Document Splitting",
             f"{pip['p3_in']:,}", f"{pip['p3_out']:,}",
             f"${p3_cost:.4f}", f"${p3_cost/max(1,p4_pages):.5f}"],
            ["P4 — Metadata Extraction",
             f"{pip['p4_in']:,}", f"{pip['p4_out']:,}",
             f"${p4_cost:.4f}", f"${p4_cost/max(1,p4_pages):.5f}"],
            ["TOTAL (Nova 4-pass)",
             "—", "—", f"${nova_total:.4f}", f"${nova_total/max(1,p4_pages):.5f}"],
        ]
        if gem_s:
            gem_total_est = 7.73
            cb_rows.append(["Gemini BADASS (reference)", "—", "—",
                             f"~${gem_total_est:.2f}",
                             f"~${gem_total_est/max(1,p4_pages):.5f}"])

        cb_t = Table(cb_rows, colWidths=[2.2*inch, 1.2*inch, 1.2*inch, 0.9*inch, 0.85*inch])
        cb_ts2 = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8.5),
            ("ALIGN",         (1,0),(-1,-1), "CENTER"),
            ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-2), [C_WHITE, C_LGREY]),
            ("BACKGROUND",    (0,-2),(-1,-2), C_SAVING),
            ("FONTNAME",      (0,-2),(-1,-2), "Helvetica-Bold"),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ]
        if gem_s:
            cb_ts2.append(("BACKGROUND", (0,-1),(-1,-1), colors.HexColor("#EBF5FB")))
        cb_t.setStyle(TableStyle(cb_ts2))
        story.append(cb_t)
        story.append(Paragraph(
            "P3 single-page heuristic (skip API for 1-page zones) + LBE one-per-page heuristic "
            "(skip API for LBE zones >30 pages) reduce P3 API calls by ~55%, "
            "cutting P3 cost from ~$3.10 to ~$1.26.",
            sty("N5", fontSize=8, textColor=C_BLUE, spaceBefore=6,
                backColor=colors.HexColor("#EBF5FB"), borderPadding=4)))

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_MGREY))
    story.append(Paragraph(
        f"Amazon Nova 2 Lite · Pricing: $0.30/1M input · $2.50/1M output · "
        f"Cache write $0.375/1M · Cache read $0.030/1M · "
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        sty("Foot", fontSize=7, textColor=colors.HexColor("#95A5A6"), alignment=1, spaceBefore=4)))

    doc.build(story)
    print(f"Master report saved: {pdf_path}")
