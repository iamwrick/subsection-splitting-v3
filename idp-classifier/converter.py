"""Convert Textract JSON (list of {page_no, blocks}) to the L-block page-header text format."""

import json
import re
import statistics


# ── Primary doc-type signals ──────────────────────────────────────────────────

_RTS_CHECKLIST_RE = re.compile(r"return.{0,15}to.{0,15}service.{0,15}checklist", re.I)
_RETURN_TO_SERVICE_RE = re.compile(r"return.{0,10}to.{0,10}service", re.I)

_PAGINATION_RE = re.compile(r"page\s+(\d+)\s+of\s+(\d+)", re.I)
_PAGE_1_OF_1_RE = re.compile(r"\bpage\s+1\s+of\s+1\b", re.I)

# Explicit ARC certificate header titles — includes British "AUTHORISED" spelling (EASA Form 1)
_ARC_HEADER_RE = re.compile(
    r"authori[sz]ed\s+release\s+certificate|airworthiness\s+approval\s+tag",
    re.I,
)

# Form 8050 / 8110 as bare numbers — but NOT "8110-3" (FAA Form 8110-3 is a DER Statement,
# not a Work Card form). Require "8110" is NOT followed by "-<digit>".
_FORM_8050_RE = re.compile(r"\b8050\b|\b8110(?!-\d)", re.I)

_LBE_RE = re.compile(r"logbook\s+entry|airframe\s+(?:log|entry)", re.I)
_FORMAL_LBE_RE = re.compile(
    r"maintenance\s+transaction\s+re(?:cord|port)"   # CAMP MTR (Record) and Cessna MTR (Report)
    r"|make\s*:\s*(?:gulfstream|cessna|beechcraft|boeing|airbus|bombardier|hawker|"
    r"learjet|dassault|embraer|piaggio|cirrus|piper|mooney|eclipse)"
    r"|\bmoc\s*:\s*(?:complied|not\s+applicable|n/a)",
    # Note: "traxxall maintenance tracking" removed — it appears on ALL Traxxall
    # document types (LBE and Status Report alike) and is not a reliable LBE signal.
    re.I,
)

# Detector for document index / effective-pages tables — always Other
_DOC_INDEX_RE = re.compile(
    r"log\s+of\s+effective\s+pages"
    r"|list\s+of\s+effective\s+pages"
    r"|table\s+of\s+contents.*page"
    r"|service\s+bulletin\s+compliance\s+check\s+list",
    re.I,
)

_AFM_SUPPLEMENT_RE = re.compile(
    r"airplane\s+flight\s+manual\s+supplement|afm\s+supplement|afms\s*:", re.I
)

_ICA_TITLE_RE  = re.compile(r"instructions\s+for\s+continued\s+airworthiness", re.I)
_ICA_DOCNUM_RE = re.compile(r"doc\.?\s*no\.?\s*:", re.I)

# Maintenance tracking / status log documents
_STATUS_REPORT_RE = re.compile(
    r"alert\s+service\s+bulletin\s+list"
    r"|service\s+bulletin\s+(?:list|compliance\s+record|log)"
    r"|ad\s*/\s*sb\s+compliance"
    r"|airworthiness\s+directives?\s+compliance\s+record"
    r"|\bnon.routine\s+items\b"
    r"|\bcamp\s+due\s+list\b|\bdue\s+list\b"
    r"|flightdocs.*maintenance\s+items|maintenance\s+items.*flightdocs",  # Flightdocs Maintenance Items
    re.I,
)

# FAA Airworthiness Directive — checked in early lines only
_AD_RE = re.compile(
    r"\bairworthiness\s+directive\b"
    r"|\b14\s+cfr\s+part\s+39\b"
    r"|docket\s+no\.?\s+faa-\d{4}",
    re.I,
)

# Manufacturer Service Bulletin — checked in early lines only
_SB_RE = re.compile(r"(?:alert\s+)?service\s+bulletin\b", re.I)

# Gulfstream MYCMP Work Cards — two-stage check:
# 1. Short phrase \bMYCMP\b — valid in first 3 lines (standalone header)
# 2. Full phrase — valid in first N lines (may appear on line 4-5 in some card formats)
_MYCMP_SHORT_RE = re.compile(r"\bMYCMP\b", re.I)
_MYCMP_FULL_RE  = re.compile(r"computerized\s+maintenance\s+program", re.I)

# Type Certificate Data Sheet — checked in early lines only
_TCDS_RE = re.compile(
    r"type\s+certificate\s+data\s+sheet"
    r"|revision\s+no\.?\s+\d+.*\b[A-Z]\d{2,5}[A-Z]{1,3}\b"
    r"|\bTCDS\b",
    re.I,
)

# ── Vendor / Other signals ────────────────────────────────────────────────────

_VENDOR_RE = re.compile(
    r"packing\s+(?:slip|sheet|list)|pack\s+list|shipping\s+invoice|bill\s+of\s+lading",
    re.I,
)
_SERVICE_REPORT_RE = re.compile(
    r"service\s+(?:work\s+)?report|inspection\s+report|battery\s+(?:pack\s+)?inspection",
    re.I,
)
_BATTERY_LOG_RE = re.compile(
    r"battery\s+maintenance\s+log|battery\s+service\s+record", re.I
)
_WORK_ORDER_DETAIL_RE = re.compile(
    r"work\s+order\s+(?:detail|tracking\s+report)|\bmaintenance\s+file\b",
    re.I,
)
_VENDOR_CERT_RE = re.compile(
    r"mill\s+certif"
    r"|certificate\s+of\s+(?:conformity|conformance|compliance|test|analysis)"
    r"|certif[a-z]*\s+of\s+conforman",
    re.I,
)
_MAINT_LOG_RE = re.compile(
    r"aircraft\s+maintenance\s+log|maintenance\s+log\s+form|form\s+86-410", re.I
)
_PART_ID_RE    = re.compile(r"part\s+identification", re.I)
_AUTH_LETTER_RE = re.compile(
    r"(?:enclosed|attached)\s+data\s+package|please\s+find\s+enclosed", re.I
)
_OPERATOR_RELEASE_FORM_RE = re.compile(
    r"airworthiness\s+release\s+form"
    r"|\bform\s+[A-Z]{2,}\d+\s*[-–]\s*airworthiness"
    r"|fly\s+exclusive\s+airworthiness",
    re.I,
)
_CERTIFIED_COPY_RE = re.compile(
    r"\bcertified\b.*\btrue\s+copy\b|\bcertified\s+true\s+copy\b", re.I
)
_DISCREPANCY_SHEET_RE = re.compile(
    r"discrepancy\s+sheet|squawk\s+sheet|sq-\d+\s+discrepancy", re.I,
)
# "flight/maintenance log" is always a vendor signal.
# "maintenance work order" is only a vendor signal in the early header lines —
# the phrase appears as a reference in Cessna MTR body text (line 50+) and must
# not trigger vendor detection there.
_FLIGHT_MAINT_LOG_RE = re.compile(r"flight[/\s]+maintenance\s+log", re.I)

# Flight operations tracking forms (trip logs, crew logs) — always Other
_FLIGHT_OPS_LOG_RE = re.compile(
    r"\baircraft\s+flight\s+log\b"
    r"|\btrip\s+captain\b"
    r"|\bflight\s+log\s+page\b"
    r"|\b(?:lvsc|iops)\b",   # Low-Visibility Single-Crew / Instrument Operations designations
    re.I,
)
_MAINT_WORK_ORDER_EARLY_RE = re.compile(r"\bmaintenance\s+work\s+order\b", re.I)

# STC supporting documents
_STC_SUPPORT_DOC_RE = re.compile(
    r"approved\s+model\s+list|aml\s+stc|master\s+data\s+list"
    r"|configuration\s+specification|compliance\s+report|approved\s+equipment\s+list",
    re.I,
)

# "A GENERAL DYNAMICS COMPANY" tag NOTE: removed from vendor detection
# (appears on all JetAviation/Gulfstream docs including legitimate LBE/WC)

# CAMP Work Card branding — requires CAMP + structural Work Card fields
_CAMP_WORKCARD_RE = re.compile(r"\bCAMP\b", re.I)
_CAMP_WORKCARD_FIELDS_RE = re.compile(
    r"\bAIRFRAME\s+HOURS\b|\bAIRFRAME\s+CYCLES\b|\bWID\s*(?:NO\.?|NUMBER|:)\b"
    r"|\bFREQUENCY\s*:\s*(?:O/C|HARD\s+TIME|\d+\s+HRS|\d+\s+MOS)"
    r"|\bACCOMPLISHED\s*:\s*\d|\bNEXT\s+DUE\s*:",
    re.I,
)

_MYCMP_HEADER_RE = re.compile(r"\bMYCMP\b|computerized\s+maintenance\s+program", re.I)

_AD_SB_COMPLIANCE_STMT_RE = re.compile(
    r"airworthiness\s+directives?\s+compliance\s+record"
    r"|faa\s+airworthiness\s+directives?\s+compliance",
    re.I,
)

_OFFICIAL_FAA_DOC_VENDOR_GUARD_RE = re.compile(
    r"supplemental\s+type\s+certificate"
    r"|statement\s+of\s+compliance\s+with\s+(?:the\s+)?(?:federal\s+aviation|airworthiness)"
    r"|major\s+repair\s+and\s+alteration",
    re.I,
)
_STC_DOC_RE     = re.compile(r"supplemental\s+type\s+certificate", re.I)
_AIRCRAFT_REG_RE = re.compile(
    r"certificate\s+of\s+aircraft\s+registration"
    r"|aircraft\s+registration\s+application|mike\s+monroney\s+aeronautical",
    re.I,
)


def _detect_hints(lines: list[str], avg_conf: float, _mycmp_window: int = 6) -> list[str]:
    """Return bracketed hint tokens derived from page text content.

    _mycmp_window: lines to check for 'COMPUTERIZED MAINTENANCE PROGRAM'.
    For txt enrichment pass 0 to use per-line length check instead.
    """
    combined = " ".join(lines)
    hints    = []

    if avg_conf < 30:
        hints.append("[BLANK]")

    # ── Text windows ──────────────────────────────────────────────────────────
    early   = " ".join(lines[:10])
    early_3 = " ".join(lines[:3])

    is_work_order_detail = bool(_WORK_ORDER_DETAIL_RE.search(combined))
    is_camp_workcard = bool(
        _CAMP_WORKCARD_RE.search(combined) and _CAMP_WORKCARD_FIELDS_RE.search(combined)
    )

    if _mycmp_window == 0:
        # Per-line length check: MYCMP header is a short standalone line
        is_mycmp_workcard = any(
            bool(_MYCMP_HEADER_RE.search(line)) and len(line.strip()) < 50
            for line in lines[:6]
        )
    else:
        early_n = " ".join(lines[:_mycmp_window])
        is_mycmp_workcard = bool(_MYCMP_HEADER_RE.search(early_n))

    is_ad = bool(_AD_RE.search(early))
    is_sb = bool(_SB_RE.search(early))

    is_official_faa  = bool(_OFFICIAL_FAA_DOC_VENDOR_GUARD_RE.search(combined))
    is_stc_doc       = bool(_STC_DOC_RE.search(combined))
    is_aircraft_reg  = bool(_AIRCRAFT_REG_RE.search(combined))

    is_vendor_doc = (
        not is_official_faa and not is_stc_doc and not is_aircraft_reg and (
            _MAINT_LOG_RE.search(combined)
            or _PART_ID_RE.search(combined)
            or _BATTERY_LOG_RE.search(combined)
            or _VENDOR_RE.search(combined)
            or _VENDOR_CERT_RE.search(combined)
            or _SERVICE_REPORT_RE.search(combined)
            or _STC_SUPPORT_DOC_RE.search(combined)
            or _AUTH_LETTER_RE.search(combined)
            or _OPERATOR_RELEASE_FORM_RE.search(combined)
            or _CERTIFIED_COPY_RE.search(combined)
            or _DISCREPANCY_SHEET_RE.search(combined)
            or _FLIGHT_MAINT_LOG_RE.search(combined)
            or _FLIGHT_OPS_LOG_RE.search(combined)    # flight operations tracking forms
            or _MAINT_WORK_ORDER_EARLY_RE.search(early)   # header-only check
            or _DOC_INDEX_RE.search(combined)             # document index pages
        )
    )

    # ── RTS / Logbook signals ─────────────────────────────────────────────────
    is_rts_checklist = bool(_RTS_CHECKLIST_RE.search(combined))
    is_lbe_signal = bool(
        (_RETURN_TO_SERVICE_RE.search(combined) or
         _LBE_RE.search(combined) or
         _FORMAL_LBE_RE.search(combined))
        and not is_vendor_doc and not is_work_order_detail
    )

    # Blank/template log pages: many header lines (LOG PAGE #, DATE, TAIL #, TRIP #)
    # but very few words of actual content — suppress LBE signals to avoid misclassification.
    # Heuristic: if avg_conf < 84% and more than 40% of lines are ≤ 4 chars (column headers),
    # treat as a blank form template (Other).
    is_blank_template = (
        avg_conf < 84
        and len(lines) > 10
        and sum(1 for l in lines if len(l.strip()) <= 4) / len(lines) > 0.35
    )

    if is_rts_checklist:
        hints.append("[RTS_CHECKLIST]")
        hints.append("[STANDALONE_PAGE]")
    elif not is_vendor_doc and not is_work_order_detail and not is_blank_template:
        if _RETURN_TO_SERVICE_RE.search(combined):
            hints.append("[RETURN_TO_SERVICE]")

    if not is_rts_checklist and not is_vendor_doc and not is_work_order_detail \
            and not is_stc_doc and not is_blank_template:
        if _LBE_RE.search(combined) or _FORMAL_LBE_RE.search(combined):
            hints.append("[LBE]")

    # ── Pagination ────────────────────────────────────────────────────────────
    m = _PAGINATION_RE.search(combined)
    if m:
        hints.append(f"[pagination: page {m.group(1)} of {m.group(2)}]")
        if _PAGE_1_OF_1_RE.search(combined):
            hints.append("[STANDALONE_PAGE]")

    # ── ARC ───────────────────────────────────────────────────────────────────
    if _ARC_HEADER_RE.search(combined):
        hints.append("[form:8130]")
        hints.append("[STANDALONE_PAGE]")

    # ── Work Card ─────────────────────────────────────────────────────────────
    _MTR_PRECHECK = re.compile(
        r"maintenance\s+transaction\s+re(?:cord|port)|mtr\s+id\s+#", re.I
    )
    _is_status = bool(_STATUS_REPORT_RE.search(combined)) and not _MTR_PRECHECK.search(combined)

    if (not is_aircraft_reg and not _is_status and
            (_FORM_8050_RE.search(combined) or is_camp_workcard or is_mycmp_workcard)):
        hints.append("[form:8050]")

    # ── AD / SB ───────────────────────────────────────────────────────────────
    is_workcard = is_camp_workcard or is_mycmp_workcard or bool(_FORM_8050_RE.search(combined))
    if is_ad and not is_workcard and not is_lbe_signal:
        hints.append("[AIRWORTHINESS_DIRECTIVE]")
        hints.append("[STANDALONE_PAGE]")
    if is_sb and not is_workcard and not is_lbe_signal:
        hints.append("[SERVICE_BULLETIN]")
        hints.append("[STANDALONE_PAGE]")

    # ── AFM Supplement / ICA ──────────────────────────────────────────────────
    if not is_stc_doc and _AFM_SUPPLEMENT_RE.search(combined):
        hints.append("[AFM_SUPPLEMENT]")
    if _ICA_TITLE_RE.search(combined) and _ICA_DOCNUM_RE.search(combined):
        hints.append("[ICA]")

    # ── TCDS ──────────────────────────────────────────────────────────────────
    if _TCDS_RE.search(early):
        hints.append("[TCDS]")
        hints.append("[vendor]")
        return hints

    # ── Status report ─────────────────────────────────────────────────────────
    # Suppress when LBE signals are present — a logbook entry documenting
    # compliance with SBs/ADs has "status" language but is Logbook Entry
    if _is_status and not is_lbe_signal:
        hints.append("[STATUS_REPORT]")
        return hints

    # ── Vendor / Other ────────────────────────────────────────────────────────
    if is_work_order_detail:
        hints.append("[WORK_ORDER_DETAIL]")
        hints.append("[STANDALONE_PAGE]")
        return hints

    if is_vendor_doc:
        if _BATTERY_LOG_RE.search(combined) or _SERVICE_REPORT_RE.search(combined):
            hints.append("[SERVICE_REPORT]")
        else:
            hints.append("[vendor]")
        hints.append("[STANDALONE_PAGE]")

    return hints


# ── JSON → L-block text ───────────────────────────────────────────────────────

def json_to_text(json_path: str) -> str:
    """Read a Textract JSON file and return the L-block page-header text."""
    with open(json_path, encoding="utf-8") as f:
        pages = json.load(f)

    parts = []
    for page in pages:
        page_no = page["page_no"]
        blocks  = page["blocks"]

        line_blocks = sorted(
            [b for b in blocks if b.get("block_type") == "L"],
            key=lambda b: b["geometry"]["y"],
        )
        lines       = [b["text"] for b in line_blocks if b.get("text", "").strip()]
        confidences = [b["confidence"] for b in line_blocks if b.get("text", "").strip()]

        avg_conf = statistics.mean(confidences) if confidences else 0.0
        n_lines  = len(lines)

        label     = f"PAGE {page_no}"
        padding   = 64 - 4 - len(label)
        left_pad  = padding // 2
        right_pad = padding - left_pad
        header = (
            "#" * 64 + "\n"
            f"##{' ' * left_pad}{label}{' ' * right_pad}##\n"
            + "#" * 64
        )

        hints     = _detect_hints(lines, avg_conf)
        hint_line = f"[{n_lines} lines, avg_conf={avg_conf:.0f}%]"
        if hints:
            hint_line += "\n" + "\n".join(hints)

        parts.append(header + "\n" + hint_line + "\n" + "\n".join(lines) + "\n")

    return "\n".join(parts)


# ── Rendered-text hint enrichment ─────────────────────────────────────────────

_PAGE_BLOCK_RE    = re.compile(r"(#{64}\n##[^\n]*##\n#{64}\n)", re.MULTILINE)
_CONF_LINE_RE     = re.compile(r"^\[\d+ lines, avg_conf=\d+%\]$")

# Upstream hints that conflict with signals we add during enrichment.
# When our enrichment determines a page is clearly vendor/Other or a Logbook Entry,
# we strip the conflicting upstream signal to avoid model confusion.
_CONFLICTING_UPSTREAM = {
    # Our signal → upstream hints to remove
    "[vendor]":           {"RETURN_TO_SERVICE", "LBE", "LOGBOOK_ENTRY", "WORK_CARD",
                           "FORM_8130-3", "FORM_8130"},
    "[SERVICE_REPORT]":   {"RETURN_TO_SERVICE", "LBE", "LOGBOOK_ENTRY",
                           "FORM_8130-3", "FORM_8130"},
    "[WORK_ORDER_DETAIL]":{"RETURN_TO_SERVICE", "LBE", "LOGBOOK_ENTRY"},
    "[LBE]":              {"FORM_8130-3", "FORM_8130"},   # Logbook entries referencing 8130 in body
    "[RETURN_TO_SERVICE]":{"WORK_CARD"},                  # LBE with RTS must not be Work Card
    "[STATUS_REPORT]":    {"WORK_CARD", "LOGBOOK_ENTRY", "LBE", "RETURN_TO_SERVICE"},
}


def _hint_token(h: str) -> str:
    """Extract the bare token name from a hint string, e.g. '[form:8130]' → 'FORM:8130'."""
    return re.sub(r"^\[|\].*$", "", h).upper().strip()


def enrich_txt_hints(raw_text: str) -> str:
    """
    Post-process a rendered_text.txt file:
    1. Runs our hint detectors on each page's text content.
    2. Merges new hints with existing upstream hints — adds missing, resolves conflicts.
    3. When a stronger signal contradicts an upstream hint, the upstream hint is removed.
    """
    if not raw_text.strip():
        return raw_text

    parts  = _PAGE_BLOCK_RE.split(raw_text)
    result = []

    i = 0
    while i < len(parts):
        chunk = parts[i]
        if not _PAGE_BLOCK_RE.match(chunk):
            result.append(chunk)
            i += 1
            continue

        header = chunk.rstrip("\n")
        body   = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2

        body_lines = body.split("\n")

        # Separate existing hints from OCR content lines
        existing_hints: list[str] = []
        content_lines:  list[str] = []
        in_hints = True
        for line in body_lines:
            if in_hints and (line.startswith("[") or line == ""):
                if line:
                    existing_hints.append(line)
            else:
                in_hints = False
                content_lines.append(line)

        avg_conf = 90.0
        if existing_hints and _CONF_LINE_RE.match(existing_hints[0]):
            m = re.search(r"avg_conf=(\d+)%", existing_hints[0])
            if m:
                avg_conf = float(m.group(1))

        # Run detectors with _mycmp_window=0 (per-line length check)
        # so "COMPUTERIZED MAINTENANCE PROGRAM" on line 4-5 fires as a Work Card header
        # but "MyCMP" embedded in a sentence on line 4 does not.
        text_lines = [l for l in content_lines if l.strip()]
        new_hints  = _detect_hints(text_lines, avg_conf, _mycmp_window=0)

        # Build set of tokens to remove from upstream hints
        tokens_to_remove: set[str] = set()
        for nh in new_hints:
            nt = _hint_token(nh)
            for signal, removals in _CONFLICTING_UPSTREAM.items():
                if _hint_token(signal) == nt:
                    tokens_to_remove.update(removals)

        # Filter upstream hints: remove conflicting ones
        filtered_upstream = [
            h for h in existing_hints
            if _hint_token(h) not in tokens_to_remove
        ]

        # Add new hints not already in filtered set
        existing_tokens = {_hint_token(h) for h in filtered_upstream}
        added = []
        for h in new_hints:
            token = _hint_token(h)
            if token not in existing_tokens:
                added.append(h)
                existing_tokens.add(token)

        all_hints  = filtered_upstream + added
        hint_block = "\n".join(all_hints)

        result.append(header + "\n" + hint_block + "\n" + "\n".join(content_lines))

    return "".join(result)
