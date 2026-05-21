"""Generate a cost analysis PDF showing actual costs vs. prompt-caching savings."""

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
    Spacer, Table, TableStyle,
)

# ── Palette ───────────────────────────────────────────────────────────────────
C_DARK   = colors.HexColor("#1A252F")
C_GREEN  = colors.HexColor("#1E8449")
C_AMBER  = colors.HexColor("#D35400")
C_BLUE   = colors.HexColor("#1A5276")
C_LGREY  = colors.HexColor("#F2F3F4")
C_MGREY  = colors.HexColor("#BDC3C7")
C_WHITE  = colors.white
C_SAVING = colors.HexColor("#D5F5E3")
C_COST   = colors.HexColor("#FADBD8")

# ── Pricing constants ─────────────────────────────────────────────────────────
PRICE_INPUT_PER_1M       = 0.30
PRICE_OUTPUT_PER_1M      = 2.50
PRICE_CACHE_WRITE_PER_1M = 0.375   # 1.25× standard input
PRICE_CACHE_READ_PER_1M  = 0.030   # 0.10× standard input

# Approximate system prompt token count (measured once)
SYSTEM_PROMPT_TOKENS = 3_500

PRICE_INPUT_PER_1K  = PRICE_INPUT_PER_1M  / 1000
PRICE_OUTPUT_PER_1K = PRICE_OUTPUT_PER_1M / 1000


def _load_batch(summary_path: str) -> dict:
    """Load batch_summary.json and compute aggregate token stats."""
    if not os.path.exists(summary_path):
        return {}
    with open(summary_path) as f:
        data = json.load(f)
    total_in   = sum(r.get("input_tokens", 0)  for r in data)
    total_out  = sum(r.get("output_tokens", 0) for r in data)
    total_cost = sum(r.get("cost_usd", 0.0)    for r in data)
    total_docs  = len(data)
    total_pages = sum(int(r["pages"]) for r in data)
    return {
        "docs": total_docs,
        "pages": total_pages,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cost_usd": total_cost,
        "rows": data,
    }


def _compute_cache_savings(stats: dict) -> dict:
    """
    Estimate savings if the system prompt (~3,500 tokens) were cached.

    Assumptions:
    - Each document typically requires ~1 API call (single chunk for most docs).
    - Estimated total calls ≈ total_docs × 1.1 (10% overhead for multi-chunk docs).
    - First call per batch = cache WRITE (1.25×), all subsequent = cache READ (0.10×).
    - For production (continuous processing), cache hit rate ≈ 99%.
    """
    if not stats:
        return {}

    total_docs   = stats["docs"]
    total_in     = stats["input_tokens"]
    total_out    = stats["output_tokens"]
    est_calls    = int(total_docs * 1.1)       # rough API call estimate

    # ── Without caching ───────────────────────────────────────────────────────
    cost_no_cache = (total_in  / 1_000_000 * PRICE_INPUT_PER_1M
                   + total_out / 1_000_000 * PRICE_OUTPUT_PER_1M)

    # System prompt tokens across all calls
    sys_tokens_total = est_calls * SYSTEM_PROMPT_TOKENS

    # ── With prompt caching (benchmark batch) ────────────────────────────────
    # Assume 1 cache write (first call), rest are cache reads
    cache_write_tokens = SYSTEM_PROMPT_TOKENS
    cache_read_tokens  = (est_calls - 1) * SYSTEM_PROMPT_TOKENS
    # Non-system input = total_in - sys_tokens_total  (page text, not cached)
    non_sys_input = max(0, total_in - sys_tokens_total)

    cost_with_cache = (
        non_sys_input      / 1_000_000 * PRICE_INPUT_PER_1M
      + cache_write_tokens / 1_000_000 * PRICE_CACHE_WRITE_PER_1M
      + cache_read_tokens  / 1_000_000 * PRICE_CACHE_READ_PER_1M
      + total_out          / 1_000_000 * PRICE_OUTPUT_PER_1M
    )

    saving      = cost_no_cache - cost_with_cache
    saving_pct  = saving / cost_no_cache * 100 if cost_no_cache > 0 else 0

    # ── 1M docs/month projection ──────────────────────────────────────────────
    # Scale token counts to 1M documents
    scale        = 1_000_000 / total_docs
    m_in         = total_in  * scale
    m_out        = total_out * scale
    m_calls      = est_calls * scale

    m_cost_no_cache = (m_in  / 1_000_000 * PRICE_INPUT_PER_1M
                     + m_out / 1_000_000 * PRICE_OUTPUT_PER_1M)

    # With caching at production scale — nearly all calls are cache reads
    m_sys_total      = m_calls * SYSTEM_PROMPT_TOKENS
    m_non_sys        = max(0, m_in - m_sys_total)
    m_cache_write    = SYSTEM_PROMPT_TOKENS * 100          # ~100 batch-start writes/month
    m_cache_read     = (m_calls - 100) * SYSTEM_PROMPT_TOKENS

    m_cost_with_cache = (
        m_non_sys       / 1_000_000 * PRICE_INPUT_PER_1M
      + m_cache_write   / 1_000_000 * PRICE_CACHE_WRITE_PER_1M
      + m_cache_read    / 1_000_000 * PRICE_CACHE_READ_PER_1M
      + m_out           / 1_000_000 * PRICE_OUTPUT_PER_1M
    )

    m_saving     = m_cost_no_cache - m_cost_with_cache
    m_saving_pct = m_saving / m_cost_no_cache * 100 if m_cost_no_cache > 0 else 0

    return {
        "est_calls": est_calls,
        "sys_tokens_total": sys_tokens_total,
        "cache_write_tokens": cache_write_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cost_no_cache": cost_no_cache,
        "cost_with_cache": cost_with_cache,
        "saving": saving,
        "saving_pct": saving_pct,
        "m_cost_no_cache":  m_cost_no_cache,
        "m_cost_with_cache": m_cost_with_cache,
        "m_saving": m_saving,
        "m_saving_pct": m_saving_pct,
        "m_in": m_in,
        "m_out": m_out,
    }


def generate_cost_report(ocr_summary: str, txt_summary: str, pdf_path: str) -> None:
    ocr = _load_batch(ocr_summary)
    txt = _load_batch(txt_summary)
    ocr_c = _compute_cache_savings(ocr)
    txt_c = _compute_cache_savings(txt)

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
    h2   = sty("H2",  fontSize=13, fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4, textColor=C_DARK)
    h3   = sty("H3",  fontSize=10, fontName="Helvetica-Bold", spaceBefore=8,  spaceAfter=3, textColor=C_DARK)
    sub  = sty("Sub", fontSize=9,  textColor=colors.HexColor("#5D6D7E"), spaceAfter=2)
    body = sty("Bod", fontSize=9,  leading=13)
    note = sty("Nte", fontSize=8,  textColor=C_BLUE,
               backColor=colors.HexColor("#EBF5FB"), borderPadding=4, leading=12)

    story = []

    # ── Cover ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("IDP Classifier — Cost Analysis", h1))
    story.append(Paragraph("Actual Cost · Prompt Caching Savings · 1M Docs/Month Projection", sub))
    story.append(Paragraph(
        f"Model: Amazon Nova 2 Lite (us.amazon.nova-2-lite-v1:0)  |  "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", sub))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_DARK, spaceAfter=14))

    # ── Pricing reference ─────────────────────────────────────────────────────
    story.append(Paragraph("Pricing Reference — Amazon Nova 2 Lite", h2))
    price_data = [
        ["Token Type",                   "Price per 1M tokens", "Notes"],
        ["Standard input",               f"${PRICE_INPUT_PER_1M:.2f}",       "Every non-cached input token"],
        ["Output",                        f"${PRICE_OUTPUT_PER_1M:.2f}",       "All response tokens"],
        ["Prompt cache WRITE",            f"${PRICE_CACHE_WRITE_PER_1M:.3f}",  "First call stores the prompt (1.25×)"],
        ["Prompt cache READ",             f"${PRICE_CACHE_READ_PER_1M:.3f}",   "Subsequent calls read from cache (0.10×)"],
    ]
    pt = Table(price_data, colWidths=[2.5*inch, 1.8*inch, 3.0*inch])
    pt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 9),
        ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
        ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("BACKGROUND",    (0,4),(2,4), C_SAVING),
    ]))
    story.append(pt)
    story.append(Paragraph(
        f"System prompt size: ~{SYSTEM_PROMPT_TOKENS:,} tokens (constant across all documents). "
        "Prompt caching stores this once and reuses it for every subsequent API call within the "
        "5-minute TTL, converting expensive standard-input tokens into cheap cache-read tokens.",
        sty("N2", fontSize=8, textColor=C_BLUE, spaceBefore=6,
            backColor=colors.HexColor("#EBF5FB"), borderPadding=4)))
    story.append(PageBreak())

    for label, stats, cache in [
        ("OCR Input (ocr.json)", ocr, ocr_c),
        ("Text Input (rendered_text.txt + enriched hints)", txt, txt_c),
    ]:
        if not stats:
            continue

        story.append(Paragraph(f"Cost Analysis — {label}", h2))

        # ── Benchmark run actual stats ────────────────────────────────────────
        story.append(Paragraph("Benchmark Run (65 Documents)", h3))
        bench_data = [
            ["Metric",             "Value"],
            ["Documents",          str(stats["docs"])],
            ["Total pages",        f"{stats['pages']:,}"],
            ["Input tokens",       f"{stats['input_tokens']:,}"],
            ["Output tokens",      f"{stats['output_tokens']:,}"],
            ["Est. API calls",     f"{cache['est_calls']:,}"],
            ["System prompt tokens sent", f"{cache['sys_tokens_total']:,}  ({cache['est_calls']} calls × {SYSTEM_PROMPT_TOKENS:,})"],
            ["──────────────",     ""],
            ["Cost WITHOUT caching", f"${cache['cost_no_cache']:.4f}"],
            ["Cost WITH caching",  f"${cache['cost_with_cache']:.4f}"],
            ["Saving",             f"${cache['saving']:.4f}  ({cache['saving_pct']:.1f}%)"],
            ["Cost per document",  f"${cache['cost_no_cache']/stats['docs']:.4f}  →  ${cache['cost_with_cache']/stats['docs']:.4f}  (cached)"],
            ["Cost per page",      f"${cache['cost_no_cache']/stats['pages']:.5f}  →  ${cache['cost_with_cache']/stats['pages']:.5f}  (cached)"],
        ]
        bt = Table(bench_data, colWidths=[3.0*inch, 4.3*inch])
        bt_style = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 9),
            ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("BACKGROUND",    (0,9),(1,9), C_COST),
            ("BACKGROUND",    (0,10),(1,10), C_SAVING),
            ("BACKGROUND",    (0,11),(1,11), colors.HexColor("#D6EAF8")),
            ("FONTNAME",      (0,9),(1,11), "Helvetica-Bold"),
        ]
        bt.setStyle(TableStyle(bt_style))
        story.append(bt)
        story.append(Spacer(1, 14))

        # ── 1M docs/month projection ──────────────────────────────────────────
        story.append(Paragraph("Projection — 1,000,000 Documents / Month", h3))
        scale = 1_000_000 / stats["docs"]

        proj_data = [
            ["Metric",               "Without Caching",       "With Prompt Caching",   "Monthly Saving"],
            ["Input tokens",          f"{cache['m_in']/1e9:.1f}B",   "—",  "—"],
            ["Output tokens",         f"{cache['m_out']/1e9:.2f}B",  "—",  "—"],
            ["Input cost",            f"${cache['m_in']/1e6*PRICE_INPUT_PER_1M:,.0f}",
                                      f"≈${cache['m_cost_with_cache'] - cache['m_out']/1e6*PRICE_OUTPUT_PER_1M:,.0f}",
                                      f"−${cache['m_saving']:,.0f}"],
            ["Output cost",           f"${cache['m_out']/1e6*PRICE_OUTPUT_PER_1M:,.0f}",
                                      f"${cache['m_out']/1e6*PRICE_OUTPUT_PER_1M:,.0f}",
                                      "$0"],
            ["TOTAL MONTHLY COST",    f"${cache['m_cost_no_cache']:,.0f}",
                                      f"${cache['m_cost_with_cache']:,.0f}",
                                      f"${cache['m_saving']:,.0f}  ({cache['m_saving_pct']:.1f}%)"],
            ["Cost per document",     f"${cache['m_cost_no_cache']/1e6:.4f}",
                                      f"${cache['m_cost_with_cache']/1e6:.4f}",
                                      f"${cache['m_saving']/1e6:.4f}"],
        ]
        pj = Table(proj_data, colWidths=[2.0*inch, 1.8*inch, 1.9*inch, 1.6*inch])
        pj.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8.5),
            ("ALIGN",         (1,0),(-1,-1), "CENTER"),
            ("GRID",          (0,0),(-1,-1), 0.4, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("BACKGROUND",    (0,5),(-1,5), C_SAVING),
            ("FONTNAME",      (0,5),(-1,5), "Helvetica-Bold"),
            ("FONTSIZE",      (0,5),(-1,5), 9),
            ("TEXTCOLOR",     (3,5),(3,5), C_GREEN),
        ]))
        story.append(pj)
        story.append(Spacer(1, 10))

    # ── Per-document cost table ───────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Per-Document Cost Detail (OCR Input)", h2))
    story.append(Paragraph(
        "Actual token counts from the benchmark run. "
        "Cost WITHOUT caching = standard pricing. Cost WITH caching = system prompt (~3,500 tokens) "
        "billed as cache-read for all calls after the first.", sub))
    story.append(Spacer(1, 6))

    if ocr and ocr.get("rows"):
        doc_hdr = ["Folder", "Pages", "Input\nTokens", "Output\nTokens",
                   "Cost\n(no cache)", "Cost\n(cached)", "Saving"]
        doc_rows = [doc_hdr]
        for r in ocr["rows"]:
            it   = r.get("input_tokens", 0)
            ot   = r.get("output_tokens", 0)
            # Estimate calls for this document
            est_doc_calls = max(1, round(it / (it / max(1, ocr["docs"]))))
            # Simple per-doc: 1 write + (calls-1) reads if multi-chunk
            doc_calls   = 1 + max(0, int(r["pages"]) // 70)  # approx chunks
            sys_no_cache = doc_calls * SYSTEM_PROMPT_TOKENS
            non_sys      = max(0, it - sys_no_cache)

            c_no  = (it / 1e6 * PRICE_INPUT_PER_1M + ot / 1e6 * PRICE_OUTPUT_PER_1M)
            # With cache: 1 write + (doc_calls-1) reads
            cw    = SYSTEM_PROMPT_TOKENS
            cr    = max(0, doc_calls - 1) * SYSTEM_PROMPT_TOKENS
            c_yes = (non_sys / 1e6 * PRICE_INPUT_PER_1M
                   + cw / 1e6 * PRICE_CACHE_WRITE_PER_1M
                   + cr / 1e6 * PRICE_CACHE_READ_PER_1M
                   + ot / 1e6 * PRICE_OUTPUT_PER_1M)
            saving = c_no - c_yes

            doc_rows.append([
                r["folder"],
                str(r["pages"]),
                f"{it:,}",
                f"{ot:,}",
                f"${c_no:.4f}",
                f"${c_yes:.4f}",
                f"-${saving:.4f}",
            ])

        # Totals row
        tot_no  = sum(float(r[4].lstrip("$")) for r in doc_rows[1:])
        tot_yes = sum(float(r[5].lstrip("$")) for r in doc_rows[1:])
        doc_rows.append(["TOTAL", "", "", "",
                          f"${tot_no:.4f}", f"${tot_yes:.4f}", f"-${tot_no-tot_yes:.4f}"])

        cw_doc = [0.95*inch, 0.48*inch, 0.8*inch, 0.8*inch, 0.85*inch, 0.85*inch, 0.8*inch]
        dt = Table(doc_rows, colWidths=cw_doc, repeatRows=1)
        dt_s = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK), ("TEXTCOLOR",(0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 7.5),
            ("ALIGN",         (1,0),(-1,-1), "CENTER"),
            ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
            ("ROWBACKGROUNDS",(0,1),(-1,-2), [C_WHITE, C_LGREY]),
            ("TOPPADDING",    (0,0),(-1,-1), 3), ("BOTTOMPADDING",(0,0),(-1,-1), 3),
            ("LEFTPADDING",   (0,0),(-1,-1), 5),
            ("BACKGROUND",    (0,-1),(-1,-1), C_SAVING),
            ("FONTNAME",      (0,-1),(-1,-1), "Helvetica-Bold"),
            ("TEXTCOLOR",     (6,1),(6,-1), C_GREEN),
        ]
        dt.setStyle(TableStyle(dt_s))
        story.append(dt)

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_MGREY))
    story.append(Paragraph(
        "Assumptions: system prompt ≈ 3,500 tokens; cache TTL = 5 min; production cache hit rate ≈ 99%. "
        "Prompt caching must be enabled in the API call (cacheControl: ephemeral on the system block). "
        "Prices as published by AWS for Amazon Nova 2 Lite, us-east-1, on-demand.",
        sty("Foot", fontSize=7, textColor=colors.HexColor("#95A5A6"), spaceBefore=4)))

    doc.build(story)
    print(f"Cost report saved: {pdf_path}")
