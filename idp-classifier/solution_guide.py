"""Generate a plain-language solution guide PDF."""

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, PageBreak,
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Palette ───────────────────────────────────────────────────────────────────
C_DARK   = colors.HexColor("#1A252F")
C_BLUE   = colors.HexColor("#1A5276")
C_GREEN  = colors.HexColor("#1E8449")
C_AMBER  = colors.HexColor("#D35400")
C_LGREY  = colors.HexColor("#F2F3F4")
C_MGREY  = colors.HexColor("#BDC3C7")
C_WHITE  = colors.white
C_BOX_BG = colors.HexColor("#EBF5FB")
C_TIP_BG = colors.HexColor("#EAFAF1")
C_WARN_BG= colors.HexColor("#FEF9E7")


def _sty(name, parent_name="Normal", **kw):
    styles = getSampleStyleSheet()
    return ParagraphStyle(name, parent=styles[parent_name], **kw)


def _box(para, bg=C_BOX_BG, border=C_BLUE):
    """Wrap a paragraph in a single-cell table for a coloured box effect."""
    t = Table([[para]], colWidths=[6.8*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(0,0), bg),
        ("BOX",           (0,0),(0,0), 0.8, border),
        ("TOPPADDING",    (0,0),(0,0), 8),
        ("BOTTOMPADDING", (0,0),(0,0), 8),
        ("LEFTPADDING",   (0,0),(0,0), 10),
        ("RIGHTPADDING",  (0,0),(0,0), 10),
    ]))
    return t


def _step_table(steps):
    """Numbered step list rendered as a compact table."""
    rows = [[
        Paragraph(f"<b>{i}</b>", _sty("N", fontSize=11, textColor=C_WHITE)),
        Paragraph(txt, _sty("B", fontSize=9, leading=13)),
    ] for i, txt in enumerate(steps, 1)]
    t = Table(rows, colWidths=[0.35*inch, 6.45*inch])
    ts = [
        ("BACKGROUND",    (0,0),(0,-1), C_DARK),
        ("TEXTCOLOR",     (0,0),(0,-1), C_WHITE),
        ("ALIGN",         (0,0),(0,-1), "CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ("LEFTPADDING",   (1,0),(1,-1), 8),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),
         [C_LGREY if i%2==0 else C_WHITE for i in range(len(rows))]),
    ]
    t.setStyle(TableStyle(ts))
    return t


def _component_table(rows):
    """Two-column component reference table."""
    header = [
        Paragraph("<b>Component</b>", _sty("H", fontSize=9, textColor=C_WHITE)),
        Paragraph("<b>What it does</b>", _sty("H", fontSize=9, textColor=C_WHITE)),
    ]
    data = [header] + [
        [Paragraph(f"<b>{name}</b>", _sty("N2", fontSize=8.5, textColor=C_BLUE)),
         Paragraph(desc, _sty("D", fontSize=8.5, leading=12))]
        for name, desc in rows
    ]
    t = Table(data, colWidths=[1.5*inch, 5.3*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), C_DARK),
        ("TEXTCOLOR",     (0,0),(-1,0), C_WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 8.5),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LGREY]),
        ("GRID",          (0,0),(-1,-1), 0.3, C_MGREY),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 7),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
    ]))
    return t


def generate(pdf_path: str) -> None:
    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        pdf_path, pagesize=LETTER,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch,  bottomMargin=0.75*inch,
    )

    h1  = _sty("H1",  "Title",   fontSize=22, spaceAfter=4,  textColor=C_DARK)
    h2  = _sty("H2",  "Heading1",fontSize=14, spaceBefore=18, spaceAfter=4,  textColor=C_DARK)
    h3  = _sty("H3",  "Heading2",fontSize=11, spaceBefore=10, spaceAfter=3,  textColor=C_BLUE)
    sub = _sty("Sub", "Normal",  fontSize=9,  textColor=colors.HexColor("#5D6D7E"), spaceAfter=2)
    bod = _sty("Bod", "Normal",  fontSize=9.5, leading=14)
    sm  = _sty("Sm",  "Normal",  fontSize=8.5, leading=12, textColor=colors.HexColor("#444444"))
    tip = _sty("Tip", "Normal",  fontSize=9,  leading=13, textColor=C_GREEN)
    bld = _sty("Bld", "Normal",  fontSize=9.5, leading=14, fontName="Helvetica-Bold")

    S = Spacer(1, 8)

    story = []

    # ══════════════════════════════════════════════════════════════════════════
    # COVER
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("IDP Document Zone Classifier", h1),
        Paragraph("Plain-Language Solution Guide", _sty("CovSub", fontSize=15,
            textColor=colors.HexColor("#5D6D7E"), spaceAfter=6)),
        Paragraph(
            f"A complete guide to how the system works — what every piece does, "
            f"why it exists, and how they fit together. "
            f"No prior machine-learning knowledge required.",
            _sty("CovDesc", fontSize=10, leading=15, textColor=colors.HexColor("#444444"),
                 spaceAfter=8)),
        Paragraph(f"Generated {datetime.now().strftime('%B %d, %Y')}", sub),
        HRFlowable(width="100%", thickness=2, color=C_DARK, spaceAfter=20),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 1 — WHAT THE SYSTEM DOES
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("1 — What the System Does", h2),
        Paragraph(
            "Aviation maintenance packages are large PDF files containing dozens — sometimes "
            "hundreds — of different documents bundled together: logbook entries, airworthiness "
            "certificates, work cards, release certificates, service bulletins, and more.",
            bod),
        S,
        Paragraph(
            "The IDP Document Zone Classifier reads the OCR text from those pages and "
            "<b>automatically labels each page range with the correct document type</b>. "
            "Instead of a human manually reviewing 200 pages to find where one document ends "
            "and the next begins, the system does it in seconds.",
            bod),
        S,
        _box(Paragraph(
            "📄  <b>Input</b>: OCR text extracted from a scanned aviation maintenance package<br/>"
            "🏷️  <b>Output</b>: A structured list of zones — each zone says \"pages 3–8 are an "
            "Authorized Release Certificate\" or \"pages 12–14 are a Work Card\"<br/>"
            "💡  <b>Why it matters</b>: Downstream systems (data extraction, compliance checking, "
            "archiving) need to know what kind of document each page belongs to before they "
            "can do their job.",
            _sty("BoxTxt", fontSize=9, leading=14))),
        S,
        Paragraph(
            "The system recognises <b>14 distinct document types</b>, from the most common "
            "(Authorized Release Certificate, Logbook Entry, Work Card) to rarer forms "
            "(Standard Airworthiness Certificate, Aircraft Flight Manual Supplement).",
            bod),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 2 — THE DOCUMENT TYPES
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("2 — The 14 Document Types", h2),
        Paragraph(
            "These are the categories the classifier can assign. Each has specific "
            "identifying signals (form numbers, headers, field names) that distinguish "
            "it from the others.",
            bod),
        S,
        _component_table([
            ("Logbook Entry",
             "A formal maintenance record where a certified technician (A&P or IA) "
             "certifies that specific work was performed and the aircraft is airworthy. "
             "Must have an explicit certification statement. "
             "<i>Example headers: 'AIRFRAME LOG BOOK ENTRY', 'Maintenance Transaction Record'.</i>"),
            ("Authorized Release Certificate",
             "FAA Form 8130-3 (or EASA Form 1) — the official tag certifying a repaired "
             "or overhauled part is approved to go back into service. Always has "
             "'AUTHORIZED RELEASE CERTIFICATE' or 'AIRWORTHINESS APPROVAL TAG' as its header."),
            ("Work Card",
             "A structured task card from a maintenance tracking system (CAMP, MyCMP, etc.) "
             "listing a specific maintenance task. Always shows FREQUENCY, ACCOMPLISHED, "
             "and NEXT DUE fields alongside the aircraft serial number."),
            ("Status Report",
             "A tabular compliance tracking log listing multiple Airworthiness Directives "
             "or Service Bulletins with their compliance dates. Examples: CAMP Due List, "
             "Bell 407 Alert Service Bulletin List, Flightdocs Non-Routine Items."),
            ("Airworthiness Directive",
             "A mandatory FAA safety order (published under 14 CFR Part 39) requiring "
             "specific action on aircraft, engines, or components. Identified by a docket "
             "number and 'AIRWORTHINESS DIRECTIVE' header."),
            ("Service Bulletin",
             "Non-mandatory guidance from the manufacturer (e.g., Honeywell, Gulfstream) "
             "recommending an inspection or modification. Identified by 'SERVICE BULLETIN' "
             "as the primary header."),
            ("Major Repair and Alteration",
             "FAA Form 337 — documents a significant structural change or repair. "
             "Always says 'MAJOR REPAIR AND ALTERATION' and 'OMB No. 2120-0020'."),
            ("Supplemental Type Certificate",
             "FAA Form 8110-2 — approval for a modification to a certified aircraft design. "
             "Identified by 'Supplemental Type Certificate' and an STC number (e.g., ST11071SC)."),
            ("Statement of Compliance",
             "A DER (Designated Engineering Representative) certification that a design "
             "meets specific FAA regulations. Must carry a DER designation number "
             "(format: DERT-XXXXXX-XX)."),
            ("Instructions for Continued Airworthiness",
             "A maintenance manual supplement that comes with an STC installation, "
             "explaining ongoing maintenance requirements for the modification."),
            ("Aircraft Flight Manual Supplement",
             "An FAA-approved operational supplement for the cockpit manual, accompanying "
             "an STC. Identified by 'FAA APPROVED AIRPLANE FLIGHT MANUAL SUPPLEMENT'."),
            ("Cert. of Aircraft Registration",
             "FAA Form AC 8050-3 — the official registration document for a specific aircraft."),
            ("Standard Airworthiness Certificate",
             "FAA Form 8100-2 — the single-page certificate confirming an aircraft is "
             "airworthy. Always exactly one page for one specific registered aircraft."),
            ("Other",
             "Everything else: packing slips, shipping invoices, vendor certificates of "
             "conformity, blank forms, cover pages, document indexes, and any page that "
             "doesn't fit the 13 types above."),
        ]),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 3 — HOW IT WORKS: THE PIPELINE
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("3 — How It Works: The Pipeline", h2),
        Paragraph(
            "The system processes a document through a series of steps. Each step "
            "prepares the data for the next one. Here is the journey from raw scan to "
            "labelled zones.",
            bod),
        S,
        _step_table([
            "<b>Read the input file.</b> The system accepts two formats: "
            "(a) a Textract JSON file containing the raw OCR blocks extracted from each page, "
            "or (b) a pre-processed text file where pages are already separated with headers.",
            "<b>Convert to page text.</b> If the input is JSON, the converter sorts each "
            "page's text blocks from top to bottom (using their Y-coordinates) to reconstruct "
            "the reading order. It also injects hint tokens — short codes like [RETURN_TO_SERVICE] "
            "or [form:8130] — that flag important signals the AI model should pay attention to.",
            "<b>Split into chunks.</b> Very large documents (100+ pages) are split into smaller "
            "batches of up to 70 pages each. This keeps each AI call within a manageable size "
            "and prevents timeouts.",
            "<b>Classify with Amazon Nova 2 Lite.</b> Each chunk is sent to Amazon Bedrock with "
            "a detailed system prompt explaining all 14 document types. The AI reads the full "
            "page text and returns a JSON list of zones — each zone specifying the page range, "
            "document type, and confidence score.",
            "<b>Post-process the results.</b> Three clean-up steps run on the AI output: "
            "(a) resolve any overlapping zones, "
            "(b) fill in any pages the AI missed, "
            "(c) optionally split out pages that should be standalone documents.",
            "<b>Write the output.</b> The final zone list is saved as a JSON file. "
            "Optionally, a PDF report, an accuracy evaluation (if ground-truth is provided), "
            "and a master cost+accuracy report are also generated.",
        ]),
        S,
        _box(Paragraph(
            "💡 <b>Why use an AI model instead of rules?</b><br/>"
            "Aviation documents vary enormously in layout, language, and format — "
            "a Cessna logbook entry looks very different from a Gulfstream one, and both "
            "look different from a Honeywell Service Bulletin. A rules-based system would "
            "require thousands of special cases. The AI model learns the general patterns "
            "from the detailed descriptions in the system prompt, making it robust to "
            "document variety it has never seen before.",
            _sty("BoxTxt2", fontSize=9, leading=14)), bg=C_TIP_BG, border=C_GREEN),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 4 — THE HINT SYSTEM
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("4 — The Hint System", h2),
        Paragraph(
            "Before the AI model sees the page text, a set of fast text detectors scan "
            "each page and inject short 'hint' tokens into the header. These give the model "
            "a pre-digested summary of the most important signals on the page.",
            bod),
        S,
        Paragraph("Why hints help", h3),
        Paragraph(
            "The AI model processes text holistically. A short token like "
            "<b>[RETURN_TO_SERVICE]</b> carries the same weight as the full return-to-service "
            "paragraph buried on line 80. Hints act like margin notes that say "
            "\"pay attention to this\" — they consistently improve accuracy without "
            "requiring the model to re-discover every pattern from scratch.",
            bod),
        S,
        Paragraph("Key hints and what they mean", h3),
        _component_table([
            ("[form:8130]",
             "The page contains the 'AUTHORIZED RELEASE CERTIFICATE' or "
             "'AIRWORTHINESS APPROVAL TAG' header. Strong signal for ARC type. "
             "Also forces a zone break so the ARC is always its own zone."),
            ("[form:8050]",
             "The page has CAMP or MyCMP Work Card branding with structural fields "
             "(AIRFRAME HOURS, FREQUENCY, NEXT DUE). Strong signal for Work Card type."),
            ("[RETURN_TO_SERVICE]",
             "The page contains return-to-service certification language — "
             "'return to service', 'approved for return to service'. "
             "Strong signal for Logbook Entry."),
            ("[LBE]",
             "The page has an explicit logbook header ('AIRFRAME LOG BOOK ENTRY', "
             "'Airframe Entry') or a formal logbook make/model identification field. "
             "Strong signal for Logbook Entry."),
            ("[STATUS_REPORT]",
             "The page is a tabular AD/SB compliance tracking list "
             "(CAMP Due List, Non-Routine Items, Alert Service Bulletin List). "
             "Definitive signal for Status Report — returned immediately."),
            ("[AIRWORTHINESS_DIRECTIVE]",
             "The page is an actual Airworthiness Directive (has 14 CFR Part 39 "
             "reference or docket number). Only fired in the early header lines "
             "to avoid false positives from references within other documents."),
            ("[SERVICE_BULLETIN]",
             "The page is a manufacturer Service Bulletin (primary header in first "
             "10 lines). Forces a zone break so SB pages don't get absorbed into "
             "adjacent Work Card zones."),
            ("[STANDALONE_PAGE]",
             "This page MUST start a new zone, even if the preceding page has the "
             "same document type. Used for pages with 'Page 1 of 1' pagination, "
             "ARC pages, and certain vendor documents."),
            ("[vendor]",
             "The page is a vendor/Other document (packing slip, mill cert, "
             "discrepancy sheet, etc.). Suppresses Logbook Entry signals — "
             "these pages should never be classified as LBE."),
            ("[RTS_CHECKLIST]",
             "The page is an operator internal Return-to-Service Checklist form "
             "(like Silver Air M-100). Suppresses both [RETURN_TO_SERVICE] and [LBE] "
             "since checklists contain RTS language but are Other, not Logbook Entry."),
        ]),
        S,
        _box(Paragraph(
            "⚠️ <b>Conflict resolution:</b> When multiple hints fire on the same page, "
            "the system resolves conflicts. For example: a Work Card that documents "
            "AD compliance will contain 'Airworthiness Directive' text — but if "
            "[form:8050] (Work Card) is present, [AIRWORTHINESS_DIRECTIVE] is suppressed. "
            "A discrepancy sheet may have return-to-service language, but [vendor] wins "
            "over [RETURN_TO_SERVICE].",
            _sty("BoxTxt3", fontSize=9, leading=14)), bg=C_WARN_BG,
            border=C_AMBER),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 5 — THE AI CLASSIFICATION PROMPT
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("5 — The AI Classification Prompt", h2),
        Paragraph(
            "The system prompt is the instruction manual the AI model reads before "
            "classifying every document. It is the most important single piece of the system — "
            "the entire classification contract.",
            bod),
        S,
        Paragraph("What the prompt contains", h3),
        _step_table([
            "<b>Role definition.</b> The model is told it is an expert aviation document "
            "classifier specialising in MRO records and FAA regulatory documentation.",
            "<b>14 document type definitions.</b> Each type has: a plain-language description, "
            "a list of key signals (form numbers, header text, field names), a mandatory gate "
            "for ambiguous types (e.g., Logbook Entry MUST have an explicit header or "
            "certification statement — N-number alone is not sufficient), and input hints "
            "that apply to it.",
            "<b>Zoning rules.</b> Seven explicit rules govern how pages are grouped: "
            "consecutive same-type pages are merged; [STANDALONE_PAGE] forces a new zone; "
            "explicit pagination groups multi-page documents; vendor pages must be their "
            "own zone even when surrounded by ARCs.",
            "<b>Confidence scoring rules.</b> 0.90–1.00 means strong explicit signals present. "
            "0.70–0.89 means multiple supporting signals with some ambiguity. "
            "Below 0.70 means best guess. Below 0.50 must not be the primary classification.",
            "<b>Disambiguation rules.</b> Detailed decision rules for the hardest pairs: "
            "Logbook Entry vs Other, Work Card vs Status Report, TCDS vs Standard "
            "Airworthiness Certificate, ARC reference vs ARC document, and more.",
            "<b>Output format.</b> The model is instructed to return only a JSON object "
            "with a 'zones' array — no explanations, no markdown, no preamble. "
            "Every page must appear in exactly one zone.",
        ]),
        S,
        Paragraph("Why the prompt has a 'mandatory gate' for Logbook Entry", h3),
        Paragraph(
            "This was added after observing the model's most common error: classifying "
            "maintenance tracking forms, blank log templates, and flight operations logs "
            "as Logbook Entries simply because they contained aircraft N-numbers, serial "
            "numbers, and dates. The gate requires an <i>explicit formal header</i> "
            "(e.g., 'AIRFRAME LOG BOOK ENTRY') or a first-person technician certification "
            "statement — not just aviation-looking content.",
            bod),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 6 — POST-PROCESSING
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("6 — Post-Processing", h2),
        Paragraph(
            "After the AI returns its zones, three automated steps clean up the results "
            "before they are written to the output file.",
            bod),
        S,

        Paragraph("Step A — Overlap resolver", h3),
        Paragraph(
            "Occasionally the AI returns two zones that cover overlapping page ranges "
            "(e.g., 'pages 29–37 Other' and 'pages 31–37 Logbook Entry'). "
            "The overlap resolver detects this and keeps whichever zone has the higher "
            "confidence score, discarding the other. This prevents downstream systems "
            "from receiving contradictory labels for the same page.",
            bod),
        S,

        Paragraph("Step B — Gap filler", h3),
        Paragraph(
            "The AI is instructed to cover every page, but occasionally a page or two "
            "slips through uncovered — especially in complex documents where chunk "
            "boundaries fall mid-document. The gap filler scans for uncovered pages and "
            "assigns them a type based on their hints: a page with [form:8130] becomes "
            "an Authorized Release Certificate; anything else becomes Other. "
            "This guarantees complete coverage.",
            bod),
        S,

        Paragraph("Step C — Boundary fixer (optional, --fix-boundaries flag)", h3),
        Paragraph(
            "Even when the AI correctly identifies a document type, it sometimes "
            "absorbs nearby pages into the wrong zone. For example, a packing sheet "
            "between two ARC pages might be pulled into the ARC zone. The boundary fixer "
            "scans every zone for pages marked [STANDALONE_PAGE] and splits them out as "
            "individual zones, inferring their type from their hints. This is run as a "
            "post-processing pass so it can never introduce new AI calls or costs.",
            bod),
        S,
        _box(Paragraph(
            "💡 <b>Structural constraint enforcement:</b> One additional rule is always "
            "applied — a Standard Airworthiness Certificate is structurally limited to "
            "exactly one page. If the AI returns a multi-page SAC zone, it is automatically "
            "reclassified as Other. This prevents the model's occasional tendency to "
            "misidentify Type Certificate Data Sheets (multi-page FAA documents with "
            "similar headers) as Standard Airworthiness Certificates.",
            _sty("BoxTxt4", fontSize=9, leading=14)), bg=C_TIP_BG, border=C_GREEN),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 7 — TEXT INPUT ENRICHMENT
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("7 — Text Input Enrichment", h2),
        Paragraph(
            "The system accepts two input formats. JSON (Textract output) goes through "
            "the full converter which generates all hints from scratch. Text files "
            "(rendered_text.txt) may already contain some pre-computed hints from an "
            "upstream system — but those hints are often incomplete or inconsistent.",
            bod),
        S,
        Paragraph(
            "The enrichment step bridges this gap. When a text file is loaded, the system "
            "runs the same hint detectors over each page's content and <b>merges the new "
            "hints with the existing upstream ones</b>. It also resolves conflicts where "
            "an upstream hint is wrong — for example, if the upstream system tagged a "
            "Discrepancy Sheet with [RETURN_TO_SERVICE] (which would make it look like a "
            "Logbook Entry), the enrichment detects the discrepancy sheet pattern, adds "
            "[vendor], and removes the conflicting [RETURN_TO_SERVICE].",
            bod),
        S,
        _box(Paragraph(
            "📊 <b>Impact of enrichment:</b> Without enrichment, text input achieves ~68% "
            "type accuracy. With enrichment applied at run time, text input matches OCR "
            "input performance (~69–70%). This means the same accuracy is achievable even "
            "when the original Textract JSON is not available.",
            _sty("BoxTxt5", fontSize=9, leading=14))),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 8 — ACCURACY EVALUATION
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("8 — Accuracy Evaluation", h2),
        Paragraph(
            "When a ground-truth CSV file is available, the system automatically compares "
            "its predictions against the expected answers and produces a detailed accuracy report.",
            bod),
        S,
        Paragraph("Two accuracy metrics", h3),
        _component_table([
            ("Exact accuracy",
             "Both the document type AND the page boundaries are correct. "
             "A prediction of 'ARC pages 3–8' against ground truth 'ARC pages 3–8' "
             "is exact. If the type is right but boundaries differ (e.g., 3–12 instead "
             "of 3–8), it does NOT count as exact."),
            ("Type accuracy",
             "Only the document type needs to be correct — page boundaries are ignored. "
             "This is the primary quality metric because knowing what a document IS "
             "matters more than knowing its exact page range. A classifier with 90% "
             "type accuracy correctly identifies 9 out of 10 document types."),
        ]),
        S,
        Paragraph("'Also acceptable' answers", h3),
        Paragraph(
            "Some ground-truth files mark certain zones as having acceptable alternative "
            "classifications (e.g., 'Other — also acceptable: Logbook Entry'). "
            "The evaluator correctly handles this: if the prediction matches either the "
            "primary or any acceptable alternative, it is counted as correct.",
            bod),
        S,
        Paragraph("Zone matching algorithm", h3),
        Paragraph(
            "For each ground-truth zone, the evaluator first looks for an exact page-range "
            "match in the predictions. If none is found, it picks the predicted zone with "
            "the greatest page overlap. This handles the common case where the classifier "
            "correctly identifies a document type but extends the zone by one or two pages.",
            bod),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 9 — COST AND TOKEN TRACKING
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("9 — Cost and Token Tracking", h2),
        Paragraph(
            "Every API call to Amazon Bedrock returns a count of how many tokens were "
            "consumed. The system captures these and records them in the output files "
            "alongside the zone classifications.",
            bod),
        S,
        Paragraph("What a token is", h3),
        Paragraph(
            "A token is roughly 3–4 characters of text. The full system prompt "
            "(about 3,500 tokens) plus the page text (typically 40,000–45,000 tokens "
            "per document) makes up the input. The AI's JSON response is the output "
            "(typically 500–600 tokens).",
            bod),
        S,
        Paragraph("Pricing and typical costs", h3),
        _component_table([
            ("Standard input", "$0.30 per 1 million tokens — applies to all page text"),
            ("Output",         "$2.50 per 1 million tokens — applies to the JSON zone response"),
            ("Cache write",    "$0.375 per 1M tokens — first call stores the system prompt"),
            ("Cache read",     "$0.030 per 1M tokens — subsequent calls reuse it (90% saving)"),
        ]),
        S,
        Paragraph(
            "At current prices, classifying one document costs approximately "
            "<b>$0.015</b> on average. At one million documents per month, the "
            "total cost is approximately <b>$15,000/month</b>. Enabling prompt caching "
            "for the system prompt saves about <b>$1,032/month</b> at that scale (6–7% reduction).",
            bod),
        S,
        _box(Paragraph(
            "💡 <b>Why output tokens cost so much more per token:</b> Output generation "
            "is computationally more expensive than reading input. However, because our "
            "output (a short JSON list of zones) is tiny compared to the input "
            "(full page text of an entire document), output tokens account for less "
            "than 2% of the total cost in practice.",
            _sty("BoxTxt6", fontSize=9, leading=14)), bg=C_TIP_BG, border=C_GREEN),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 10 — THE REPORTING SYSTEM
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("10 — The Reporting System", h2),
        Paragraph(
            "The system generates several types of PDF reports, each targeting a different audience.",
            bod),
        S,
        _component_table([
            ("Per-document PDF\n(reporter.py)",
             "Generated for every classified document when --pdf is used. Shows a "
             "colour-coded table of all zones: document type, page range, confidence "
             "score, and a visual confidence bar. Each document type has its own "
             "background colour for quick visual scanning."),
            ("Batch summary PDF\n(batch_reporter.py)",
             "Generated after a batch run. Shows overall accuracy statistics, "
             "an accuracy distribution tier chart, and a per-document table with "
             "token counts and costs. Highlights wrong-type zones for each document."),
            ("Master report PDF\n(master_reporter.py)",
             "The single comprehensive report combining everything: classification "
             "accuracy (with three charts), cost analysis, prompt-caching savings "
             "projection, per-folder results table, and per-folder issue detail. "
             "Generated automatically with --master-report or via the 'report' command."),
            ("Cost analysis PDF\n(cost_report.py)",
             "Standalone cost report showing actual costs from a batch run alongside "
             "estimated savings from prompt caching, and a 1-million-documents-per-month "
             "projection table."),
        ]),
        S,
        Paragraph("The three accuracy charts", h3),
        _component_table([
            ("Lollipop plot",
             "One row per metric; three coloured dots (OCR / Text / Text+Hints) on a "
             "horizontal track. A grey bar spans the range between the best and worst "
             "result — narrow bars mean all three inputs agree; wide bars reveal where "
             "input format makes a big difference."),
            ("Radar chart",
             "A spider/polygon chart with five axes (exact accuracy, type accuracy, "
             "100% folders, ≥90% folders, no-issue folders). Each input method draws "
             "a filled polygon — a larger, more circular shape means more consistently "
             "strong performance across all metrics."),
            ("Slope chart",
             "A parallel-coordinates chart where each coloured line represents one "
             "metric, and its value is plotted for each input method (OCR → Text → "
             "Text+Hints). Rising lines show where enrichment helps; flat lines show "
             "where all three methods agree; crossing lines reveal unexpected behaviour."),
        ]),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 11 — THE CLI
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("11 — The Command-Line Interface", h2),
        Paragraph(
            "All functionality is accessed through four commands in main.py.",
            bod),
        S,
        _component_table([
            ("classify",
             "<b>Classifies a single document.</b><br/>"
             "Required: --input (path to .json or .txt), --output (where to save results).<br/>"
             "Optional: --pdf (generate a visual report), --groundtruth (measure accuracy), "
             "--fix-boundaries (post-process zone splits), --max-pages (chunk size, default 300)."),
            ("batch",
             "<b>Classifies multiple folders in one run.</b><br/>"
             "Accepts any number of folder paths. Each folder needs an input file and "
             "optionally an ac.csv ground-truth file. Writes results, PDFs, and evaluation "
             "files to --output-dir. Use --input-file to specify rendered_text.txt "
             "instead of ocr.json. Use --master-report to auto-generate the master PDF "
             "at the end of the run."),
            ("report",
             "<b>Generates a master PDF from two existing batch outputs.</b><br/>"
             "Combines an OCR run and a text run into one comparison report without "
             "re-running any classification. Use --txt-enriched-dir for a third column "
             "showing results with enriched text hints."),
            ("evaluate",
             "<b>Compares a saved output.json against a ground-truth CSV.</b><br/>"
             "Offline accuracy measurement — no API calls, no cost. Produces a "
             "detailed eval.json with per-zone results and accuracy metrics."),
        ]),
        S,
        _box(Paragraph(
            "🚀 <b>Recommended production command:</b><br/><br/>"
            "<font name='Courier' size='8'>"
            "python main.py batch /path/to/folder1 /path/to/folder2 \\\n"
            "  --output-dir ./results \\\n"
            "  --fix-boundaries \\\n"
            "  --pdf \\\n"
            "  --max-pages 70 \\\n"
            "  --master-report ./REPORT.pdf"
            "</font>",
            _sty("BoxCode", fontSize=9, leading=14))),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 12 — KNOWN LIMITATIONS
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("12 — Known Limitations", h2),
        Paragraph(
            "Three cases remain where the classifier consistently struggles. "
            "These are genuine ambiguities that a human reviewer would also find challenging.",
            bod),
        S,
        _component_table([
            ("Metro Aviation Maintenance File\n(188233)",
             "A 365-page multi-aircraft work order archive from Metro Aviation. "
             "Despite the 'Maintenance File' header on page 1 triggering a vendor signal, "
             "the remaining 364 pages look like maintenance records and the AI classifies "
             "the whole document as a single Logbook Entry. The file lacks enough structural "
             "signals to distinguish it from a large logbook."),
            ("PNC Aviation Tracking Forms\n(139270)",
             "Maintenance tracking report pages (PNC Aviation) that contain aircraft "
             "N-numbers, serial numbers, total time, and dates — but lack the formal "
             "'AIRFRAME LOG BOOK ENTRY' header required by the Logbook Entry gate. "
             "The model's strong association between 'aviation identifiers = Logbook Entry' "
             "overrides the text rule at 95% confidence. Human review of the GT labeling "
             "may be needed — these pages genuinely look like maintenance records."),
            ("Four Corners MyCMP Work Cards\n(131247)",
             "Work cards from the Four Corners Aviation MyCMP system. The MYCMP or "
             "'COMPUTERIZED MAINTENANCE PROGRAM' branding does not appear in the OCR "
             "output (it is likely in an image/logo that was not extracted). Without this "
             "signal, the classifier cannot distinguish these Work Cards from Logbook "
             "Entries based on content alone."),
        ]),
        S,
        _box(Paragraph(
            "🔧 <b>What would fix these:</b><br/>"
            "188233 — adding a second-pass hint on pages 2+ when page 1 is WORK_ORDER_DETAIL.<br/>"
            "139270 — reviewing whether the GT labels are correct; these pages may be "
            "legitimately borderline between Other and Logbook Entry.<br/>"
            "131247 — obtaining the OCR-extracted text from the MYCMP logo/header area, "
            "or adding a document-level hint when page 1 is a MYCMP cover page.",
            _sty("BoxTxt7", fontSize=9, leading=14)), bg=C_WARN_BG, border=C_AMBER),
        PageBreak(),
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 13 — QUICK REFERENCE
    # ══════════════════════════════════════════════════════════════════════════
    story += [
        Paragraph("13 — Quick Reference", h2),
        Paragraph("Key files and what each one does:", h3),
        _component_table([
            ("config.py",      "Model ID, pricing constants, temperature, chunk size limits."),
            ("models.py",      "Data structures: Zone, DocumentResult, TokenUsage. "
                               "Includes type validation — unknown types are coerced to 'Other'."),
            ("prompts.py",     "The full system prompt (the classification contract) and "
                               "the user prompt builder."),
            ("converter.py",   "Converts Textract JSON to page text; runs all hint detectors; "
                               "provides enrich_txt_hints() for text file enrichment."),
            ("chunker.py",     "Splits page text into chunks; handles both standard and "
                               "annotated page headers."),
            ("classifier.py",  "Makes the Bedrock API call; extracts zone list and token counts; "
                               "contains resolve_overlaps(), fill_gaps(), fix_boundaries(), "
                               "enforce_single_page_types()."),
            ("evaluator.py",   "Loads ground-truth CSV; matches predicted zones to GT zones; "
                               "computes exact and type accuracy; handles 'also acceptable' variants."),
            ("main.py",        "CLI entry point — classify, batch, report, evaluate commands."),
            ("reporter.py",    "Per-document PDF report."),
            ("master_reporter.py", "Combined accuracy + cost + charts master PDF."),
            ("charts.py",      "Lollipop, radar, and slope chart generators (matplotlib)."),
            ("cost_report.py", "Standalone cost analysis PDF with caching projections."),
        ]),
        S,
        Paragraph("Performance summary (65 documents, 3,681 pages):", h3),
        _component_table([
            ("Overall exact accuracy",  "69.7% — both type and page boundaries correct"),
            ("Overall type accuracy",   "~88% — document type correct regardless of boundaries"),
            ("Folders at 100% type",    "49 out of 65 documents classified with zero type errors"),
            ("Folders ≥ 90% type",      "54 out of 65 documents"),
            ("Cost per document",       "~$0.015 (OCR input), ~$0.015 (text input)"),
            ("Cost per page",           "~$0.00027"),
            ("Processing time",         "~6 minutes for 65 documents on a single machine"),
        ]),
    ]

    # ── Footer ─────────────────────────────────────────────────────────────────
    story += [
        Spacer(1, 20),
        HRFlowable(width="100%", thickness=0.5, color=C_MGREY),
        Paragraph(
            "IDP Document Zone Classifier · Amazon Nova 2 Lite (us.amazon.nova-2-lite-v1:0) · "
            f"Generated {datetime.now().strftime('%B %d, %Y')}",
            _sty("Foot", fontSize=7, textColor=colors.HexColor("#95A5A6"),
                 alignment=1, spaceBefore=4)),
    ]

    doc.build(story)
    print(f"Solution guide saved: {pdf_path}")
