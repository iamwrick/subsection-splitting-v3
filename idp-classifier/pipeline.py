"""
4-pass IDP pipeline orchestrator.

Pass 1 (P1) — Zone classification        [existing: classifier.py]
Pass 2 (P2) — Seam merge + post-process  [existing: classifier.py]
Pass 3 (P3) — Per-zone document splitting [new]
Pass 4 (P4) — Per-document extraction    [new]
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from extraction_models import (
    DocumentPackage, ExtractedDocument, SplitDocument, ZoneSplit,
    Task, Signature,
)
from extraction_prompts import (
    P3_SYSTEM, P4_SYSTEM,
    build_p3_prompt, build_p4_prompt,
)
from models import DocumentResult, TokenUsage, Zone
from config import MODEL_ID, TEMPERATURE

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# ── Date regex fallback patterns (applied when P4 returns null date) ───────────

# dd-Mon-YYYY or dd/Mon/YYYY or dd Mon YY/YYYY (e.g. 21-Aug-2020, 04/Jan/2019)
_DATE_DMY_ABBR = re.compile(
    r'\b(\d{1,2})[/\-\s]'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
    r'[/\-\s](\d{2,4})\b', re.IGNORECASE
)
# Mon/dd/YYYY — older FAA Form 8130-3 field 18 (e.g. JAN/21/2014)
_DATE_MDY_ABBR = re.compile(
    r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
    r'[/\-\s](\d{1,2})[/\-\s](\d{2,4})\b', re.IGNORECASE
)
# YYYY-MM-DD
_DATE_ISO = re.compile(r'\b(20\d{2})-(\d{2})-(\d{2})\b')
# MM/DD/YYYY (US format, e.g. 4/27/2016)
_DATE_MDY = re.compile(r'\b(\d{1,2})/(\d{1,2})/(20\d{2})\b')
# dd Mon YYYY (long month, e.g. 22 March 2024)
_DATE_LONG = re.compile(
    r'\b(\d{1,2})\s+'
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+(\d{4})\b', re.IGNORECASE
)
# Mon dd, YYYY
_DATE_LONG2 = re.compile(
    r'\b(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+(\d{1,2}),?\s+(\d{4})\b', re.IGNORECASE
)
# Handwritten logbook D M YY column format — each value on its own line
# e.g. "03\n11\n20" = day=03, month=11, year=20 = 2020-11-03
# Only used when "Y M D" column header is detected in the page text
_DATE_COL_DMY = re.compile(r'\b([0-3]?\d)\s+([01]?\d)\s+(\d{2})\b')
# Trigger pattern for handwritten logbook column format
_HANDWRITTEN_LOG_RE = re.compile(r'\bY\s+M\s+D\b', re.IGNORECASE)

_MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'june': 6, 'july': 7, 'august': 8, 'september': 9,
    'october': 10, 'november': 11, 'december': 12,
}


def _two_digit_year(yy: int) -> int:
    """Convert 2-digit year to 4-digit, assuming 2000-2099 for yy < 50."""
    return 2000 + yy if yy < 50 else 1900 + yy


def _extract_date_regex(text: str) -> tuple[str | None, str | None]:
    """
    Try to extract a document date from text using regex patterns.
    Returns (iso_date, raw_text) or (None, None).
    Looks near Date/Date completed labels first, then full text.
    """
    # Special case: handwritten logbook with Y M D column header
    # Each row's date values appear as three separate numbers: day, month, 2-digit-year
    # Take the LAST valid triplet (most recent entry on the page)
    if _HANDWRITTEN_LOG_RE.search(text):
        candidates = []
        for m in _DATE_COL_DMY.finditer(text):
            d_val, mo_val, yr_val = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= d_val <= 31 and 1 <= mo_val <= 12:
                year = _two_digit_year(yr_val)
                if 2000 <= year <= 2099:
                    candidates.append((f"{year:04d}-{mo_val:02d}-{d_val:02d}", m.group(0)))
        if candidates:
            return candidates[-1]  # last = most recent entry

    # Build search zones: near 'Date' or 'Date completed' labels first, then full text
    search_zones = []
    for m in re.finditer(r'Date(?:\s+completed)?\b', text, re.IGNORECASE):
        search_zones.append(text[m.start(): m.start() + 250])
    search_zones.append(text)  # fallback

    for zone in search_zones:
        # dd-Mon-YY or dd-Mon-YYYY (also space/slash separators)
        m = _DATE_DMY_ABBR.search(zone)
        if m:
            day, mon_str = int(m.group(1)), m.group(2).lower()
            yr_raw = int(m.group(3))
            year = _two_digit_year(yr_raw) if yr_raw < 100 else yr_raw
            mon = _MONTH_MAP.get(mon_str, 0)
            if mon and 1 <= day <= 31 and 2000 <= year <= 2099:
                return f"{year:04d}-{mon:02d}-{day:02d}", m.group(0)

        # Mon/dd/YYYY — older 8130-3 field 18 (e.g. JAN/21/2014)
        m = _DATE_MDY_ABBR.search(zone)
        if m:
            mon_str, day = m.group(1).lower(), int(m.group(2))
            yr_raw = int(m.group(3))
            year = _two_digit_year(yr_raw) if yr_raw < 100 else yr_raw
            mon = _MONTH_MAP.get(mon_str, 0)
            if mon and 1 <= day <= 31 and 2000 <= year <= 2099:
                return f"{year:04d}-{mon:02d}-{day:02d}", m.group(0)

        # YYYY-MM-DD
        m = _DATE_ISO.search(zone)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", m.group(0)

        # MM/DD/YYYY (US)
        m = _DATE_MDY.search(zone)
        if m:
            mon, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mon <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{mon:02d}-{day:02d}", m.group(0)

        # dd Month YYYY
        m = _DATE_LONG.search(zone)
        if m:
            day, mon_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            mon = _MONTH_MAP.get(mon_str[:3], 0)
            if mon and 1 <= day <= 31 and 2000 <= year <= 2099:
                return f"{year:04d}-{mon:02d}-{day:02d}", m.group(0)

        # Month dd, YYYY
        m = _DATE_LONG2.search(zone)
        if m:
            mon_str, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
            mon = _MONTH_MAP.get(mon_str[:3], 0)
            if mon and 1 <= day <= 31 and 2000 <= year <= 2099:
                return f"{year:04d}-{mon:02d}-{day:02d}", m.group(0)

    return None, None


_VALID_DOC_TYPES = {
    "Logbook Entry", "Work Card", "Authorized Release Certificate",
    "Status Report", "Airworthiness Directive", "Service Bulletin",
    "Major Repair and Alteration", "Supplemental Type Certificate",
    "Instructions for Continued Airworthiness", "Statement of Compliance",
    "Certificate of Aircraft Registration", "Standard Airworthiness Certificate",
    "Aircraft Flight Manual Supplement", "Other",
}


def _normalize_doc_type(doc_type: str | None) -> str:
    """Map any non-standard P3 type label back to the 14-type taxonomy."""
    if not doc_type:
        return "Other"
    if doc_type in _VALID_DOC_TYPES:
        return doc_type
    dt = doc_type.lower()
    if any(k in dt for k in ("logbook", "maintenance log", "airframe log", "engine log")):
        return "Logbook Entry"
    if any(k in dt for k in ("work card", "task card", "job card", "work instruction")):
        return "Work Card"
    if any(k in dt for k in ("release cert", "8130", "airworthiness approval")):
        return "Authorized Release Certificate"
    if any(k in dt for k in ("status report", "adsi", "due list", "compliance tracking")):
        return "Status Report"
    if any(k in dt for k in ("airworthiness directive", " ad ", "faa ad")):
        return "Airworthiness Directive"
    if any(k in dt for k in ("service bulletin", "service letter")):
        return "Service Bulletin"
    if any(k in dt for k in ("form 337", "major repair", "alteration")):
        return "Major Repair and Alteration"
    if any(k in dt for k in ("supplemental type", " stc ")):
        return "Supplemental Type Certificate"
    if any(k in dt for k in ("continued airworthiness", " ica ")):
        return "Instructions for Continued Airworthiness"
    if any(k in dt for k in ("statement of compliance", " soc ")):
        return "Statement of Compliance"
    if any(k in dt for k in ("registration",)):
        return "Certificate of Aircraft Registration"
    if any(k in dt for k in ("airworthiness certificate", " sac ")):
        return "Standard Airworthiness Certificate"
    if any(k in dt for k in ("flight manual", " afms ")):
        return "Aircraft Flight Manual Supplement"
    return "Other"


# Types where we apply the regex date fallback when P4 returns null
_DATE_FALLBACK_TYPES = {
    "Logbook Entry", "Work Card", "Authorized Release Certificate",
    "Status Report", "Airworthiness Directive", "Service Bulletin",
    "Major Repair and Alteration", "Supplemental Type Certificate",
    "Instructions for Continued Airworthiness", "Statement of Compliance",
    "Certificate of Aircraft Registration", "Standard Airworthiness Certificate",
    "Aircraft Flight Manual Supplement", "Other",
}


# ── Bedrock call helper (reused for P3 and P4) ────────────────────────────────

def _call_bedrock(client, system_prompt: str, user_prompt: str,
                  max_tokens: int = 2048) -> tuple[str, TokenUsage]:
    body = {
        "schemaVersion": "messages-v1",
        "system": [{"text": system_prompt}],
        "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
        "inferenceConfig": {"temperature": TEMPERATURE, "maxTokens": max_tokens},
    }
    response = client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    result   = json.loads(response["body"].read())
    raw_text = result["output"]["message"]["content"][0]["text"]
    usage    = result.get("usage", {})
    tok = TokenUsage(
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
    )
    cleaned = _FENCE_RE.sub("", raw_text).strip()
    return cleaned, tok


# ── P3: split a zone into individual documents ────────────────────────────────

_P3_COMPLEX_TYPES = {"Logbook Entry", "Work Card", "Authorized Release Certificate"}

# Types where P4 extracts tasks/signatures — needs more output tokens
_P4_COMPLEX_TYPES = {"Logbook Entry", "Work Card", "Authorized Release Certificate"}


def _p3_max_tokens(n_pages: int, doc_type: str) -> int:
    """Scale P3 output budget: each split document needs ~90 tokens of JSON."""
    if n_pages <= 1:
        return 256
    # Dense types can split into one doc per page → n_pages × 90 + buffer
    if doc_type in _P3_COMPLEX_TYPES:
        return min(5120, max(512, n_pages * 90 + 512))
    return min(3072, max(512, n_pages * 60 + 512))


def _fill_p3_gaps(docs: list[SplitDocument], zone_start: int, zone_end: int,
                  doc_type: str) -> list[SplitDocument]:
    """Fill any page gaps the model left in the P3 output."""
    result = []
    cursor = zone_start
    for d in docs:
        if d.start_page > cursor:
            # Gap before this document — fill it
            result.append(SplitDocument(
                start_page=cursor, end_page=d.start_page - 1,
                document_title=None, boundary_reason="Gap fill",
                document_type=doc_type,
            ))
        result.append(d)
        cursor = d.end_page + 1
    if cursor <= zone_end:
        result.append(SplitDocument(
            start_page=cursor, end_page=zone_end,
            document_title=None, boundary_reason="Trailing gap fill",
            document_type=doc_type,
        ))
    return result


def p3_split_zone(client, zone: Zone, zone_text: str) -> tuple[list[SplitDocument], TokenUsage]:
    """
    Ask Nova 2 Lite to split one zone into individual documents.
    Returns (list of SplitDocument, token usage).
    Falls back to the zone as a single document if the call fails.
    """
    n_pages = zone.end_page - zone.start_page + 1

    # Single-page zones are always exactly one document — skip the API call
    if n_pages == 1:
        return [SplitDocument(
            start_page=zone.start_page, end_page=zone.end_page,
            document_title=None, boundary_reason="Single-page zone",
            document_type=zone.document_type,
        )], TokenUsage()

    # Large LBE zones: one entry per page to avoid output token exhaustion.
    if zone.document_type == "Logbook Entry" and n_pages > 30:
        docs = [
            SplitDocument(
                start_page=p, end_page=p,
                document_title=None,
                boundary_reason="One-per-page heuristic for large LBE zone",
                document_type="Logbook Entry",
            )
            for p in range(zone.start_page, zone.end_page + 1)
        ]
        return docs, TokenUsage()

    # Large ARC zones: each certificate is exactly 1 page. At 5120 output tokens
    # the model can only describe ~57 documents before truncating, so use the
    # one-per-page heuristic for zones larger than that threshold.
    if zone.document_type == "Authorized Release Certificate" and n_pages > 50:
        docs = [
            SplitDocument(
                start_page=p, end_page=p,
                document_title=None,
                boundary_reason="One-per-page heuristic for large ARC zone",
                document_type="Authorized Release Certificate",
            )
            for p in range(zone.start_page, zone.end_page + 1)
        ]
        return docs, TokenUsage()

    # Medium-large zones (10-30 pages): call P3 but cap at 5120 tokens
    # to avoid truncation — handled by _p3_max_tokens

    prompt = build_p3_prompt(zone_text, zone.document_type,
                             zone.start_page, zone.end_page)
    max_tok = _p3_max_tokens(n_pages, zone.document_type)
    try:
        raw, tok = _call_bedrock(client, P3_SYSTEM, prompt, max_tokens=max_tok)
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("P3 response is not a list")
        docs = []
        for item in parsed:
            sp = int(item.get("start_page", zone.start_page))
            ep = int(item.get("end_page", zone.end_page))
            # Clamp to zone boundaries
            sp = max(zone.start_page, min(sp, zone.end_page))
            ep = max(zone.start_page, min(ep, zone.end_page))
            if sp > ep:
                ep = sp
            docs.append(SplitDocument(
                start_page=sp,
                end_page=ep,
                document_title=item.get("document_title"),
                boundary_reason=item.get("boundary_reason"),
                document_type=_normalize_doc_type(item.get("document_type") or zone.document_type),
            ))
        if not docs:
            raise ValueError("Empty document list")
        # Sort by start page
        docs.sort(key=lambda d: d.start_page)
        # Deduplicate: when multiple splits share the same page range, keep only the first
        seen: set[tuple[int, int]] = set()
        deduped = []
        for d in docs:
            key = (d.start_page, d.end_page)
            if key not in seen:
                seen.add(key)
                deduped.append(d)
        docs = deduped
        # Fill any gaps the model missed (coverage repair)
        docs = _fill_p3_gaps(docs, zone.start_page, zone.end_page, zone.document_type)
        return docs, tok
    except Exception as exc:
        print(f"  P3 warning zone {zone.start_page}-{zone.end_page}: {exc}", file=sys.stderr)
        return [SplitDocument(
            start_page=zone.start_page, end_page=zone.end_page,
            document_title=None, boundary_reason="P3 fallback — kept as single document",
            document_type=zone.document_type,
        )], TokenUsage()


# ── P4: extract metadata from one document ────────────────────────────────────

def _parse_p4_output(raw: str, doc_type: str,
                     start_page: int, end_page: int) -> ExtractedDocument:
    try:
        data = json.loads(raw)
    except Exception:
        data = {}

    def _get(key, default=None):
        return data.get(key, default)

    tasks = []
    for t in (_get("tasks") or []):
        if isinstance(t, dict):
            tasks.append(Task(
                task_description=t.get("task_description"),
                part_number_removed=t.get("part_number_removed"),
                serial_number_removed=t.get("serial_number_removed"),
                part_number_installed=t.get("part_number_installed"),
                serial_number_installed=t.get("serial_number_installed"),
                compliance_status=t.get("compliance_status"),
                reference=t.get("reference"),
            ))

    sigs = []
    for s in (_get("signatures") or []):
        if isinstance(s, dict):
            sigs.append(Signature(
                full_name=s.get("full_name"),
                title=s.get("title"),
                certificate_number=s.get("certificate_number") or s.get("license_number"),
                date_of_signature=s.get("date_of_signature"),
            ))

    return ExtractedDocument(
        document_type=doc_type,
        start_page=start_page,
        end_page=end_page,
        document_date=_get("document_date"),
        document_date_raw=_get("document_date_raw"),
        document_title=_get("document_title"),
        date_confidence=_get("date_confidence"),
        registration_number=_get("registration_number"),
        serial_number=_get("serial_number"),
        make_model=_get("make_model"),
        work_order_number=_get("work_order_number"),
        total_hours=_get("total_hours"),
        total_landings=_get("total_landings"),
        total_cycles=_get("total_cycles"),
        part_number=_get("part_number"),
        part_description=_get("part_description"),
        quantity=_get("quantity"),
        status_condition=_get("status_condition"),
        approval_reference=_get("approval_reference"),
        regulatory_reference=_get("regulatory_reference"),
        tasks=tasks,
        signatures=sigs,
        summary=_get("summary"),
    )


def p4_extract_document(client, split_doc: SplitDocument,
                        doc_text: str, zone_type: str) -> tuple[ExtractedDocument, TokenUsage]:
    """
    Extract structured metadata from one document.
    Returns (ExtractedDocument, token usage).
    """
    doc_type = _normalize_doc_type(split_doc.document_type or zone_type)
    # Complex types with tasks/signatures need more output tokens
    max_tok = 2048 if doc_type in _P4_COMPLEX_TYPES else 1024
    prompt = build_p4_prompt(doc_text, doc_type,
                             split_doc.start_page, split_doc.end_page)
    try:
        raw, tok = _call_bedrock(client, P4_SYSTEM, prompt, max_tokens=max_tok)
        extracted = _parse_p4_output(raw, doc_type,
                                     split_doc.start_page, split_doc.end_page)
        # Override title from split if extraction didn't find one
        if not extracted.document_title and split_doc.document_title:
            extracted.document_title = split_doc.document_title
        # Regex fallback: fill in date when P4 returned null for types that always have one
        if not extracted.document_date and doc_type in _DATE_FALLBACK_TYPES:
            iso, raw_date = _extract_date_regex(doc_text)
            if iso:
                extracted.document_date = iso
                extracted.document_date_raw = raw_date
                extracted.date_confidence = "medium"
        return extracted, tok
    except Exception as exc:
        print(f"  P4 warning doc {split_doc.start_page}-{split_doc.end_page}: {exc}",
              file=sys.stderr)
        doc = ExtractedDocument(
            document_type=doc_type,
            start_page=split_doc.start_page,
            end_page=split_doc.end_page,
            document_title=split_doc.document_title,
            summary="P4 extraction failed",
        )
        # Still try regex date on fallback path
        if doc_type in _DATE_FALLBACK_TYPES:
            iso, raw_date = _extract_date_regex(doc_text)
            if iso:
                doc.document_date = iso
                doc.document_date_raw = raw_date
                doc.date_confidence = "medium"
        return doc, TokenUsage()


# ── Full pipeline orchestrator ────────────────────────────────────────────────

def run_p3_p4(
    client,
    doc_result: DocumentResult,
    pages: list[tuple[int, str]],
    verbose: bool = True,
) -> DocumentPackage:
    """
    Run P3 (split) and P4 (extract) on an already-classified document.

    Args:
        client:     Bedrock client
        doc_result: output of P1+P2 (zones already classified)
        pages:      list of (page_number, page_text) from the chunker
        verbose:    print progress to stdout

    Returns DocumentPackage with zones + extracted documents.
    """
    page_text_map = {pno: text for pno, text in pages}

    def get_zone_text(start: int, end: int) -> str:
        parts = []
        for pno in range(start, end + 1):
            if pno in page_text_map:
                parts.append(page_text_map[pno])
        return "\n".join(parts)

    all_documents: list[ExtractedDocument] = []
    page_map: dict[int, int] = {}
    p3_tok = TokenUsage()
    p4_tok = TokenUsage()
    doc_index = 0

    n_zones = len(doc_result.zones)
    for zi, zone in enumerate(doc_result.zones):
        if verbose:
            print(f"  P3 zone {zi+1}/{n_zones}: {zone.document_type} "
                  f"pages {zone.start_page}–{zone.end_page}")

        zone_text = get_zone_text(zone.start_page, zone.end_page)

        # P3 — split zone into individual documents
        split_docs, tok3 = p3_split_zone(client, zone, zone_text)
        p3_tok = p3_tok.add(tok3)
        if verbose:
            print(f"    → {len(split_docs)} document(s)  "
                  f"[{tok3.input_tokens:,} in / {tok3.output_tokens:,} out tokens]")

        # P4 — extract metadata from each split document (parallel within zone)
        p4_inputs = [
            (sd, get_zone_text(sd.start_page, sd.end_page), zone.document_type)
            for sd in split_docs
        ]

        if len(p4_inputs) == 1:
            # Single doc — run directly, no thread overhead
            sd, doc_text, ztype = p4_inputs[0]
            extracted, tok4 = p4_extract_document(client, sd, doc_text, ztype)
            p4_tok = p4_tok.add(tok4)
            all_documents.append(extracted)
            for pno in range(sd.start_page, sd.end_page + 1):
                page_map[pno] = doc_index
            doc_index += 1
        else:
            # Multiple docs — run P4 calls in parallel (thread-safe: each call is independent)
            max_workers = min(8, len(p4_inputs))
            futures = {}
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for idx, (sd, doc_text, ztype) in enumerate(p4_inputs):
                    fut = pool.submit(p4_extract_document, client, sd, doc_text, ztype)
                    futures[fut] = (idx, sd)

            # Collect results in submission order
            results: dict[int, tuple[ExtractedDocument, TokenUsage]] = {}
            for fut, (idx, sd) in futures.items():
                try:
                    results[idx] = fut.result()
                except Exception as exc:
                    print(f"  P4 parallel error: {exc}", file=sys.stderr)
                    results[idx] = (ExtractedDocument(
                        document_type=sd.document_type or zone.document_type,
                        start_page=sd.start_page, end_page=sd.end_page,
                        summary="P4 parallel error",
                    ), TokenUsage())

            for idx, (sd, _dt, _zt) in enumerate(p4_inputs):
                extracted, tok4 = results[idx]
                p4_tok = p4_tok.add(tok4)
                all_documents.append(extracted)
                for pno in range(sd.start_page, sd.end_page + 1):
                    page_map[pno] = doc_index
                doc_index += 1

    return DocumentPackage(
        input_file=doc_result.input_file,
        total_pages=doc_result.total_pages,
        zones=doc_result.zones,
        documents=all_documents,
        page_map=page_map,
        p3_input_tokens=p3_tok.input_tokens,
        p3_output_tokens=p3_tok.output_tokens,
        p4_input_tokens=p4_tok.input_tokens,
        p4_output_tokens=p4_tok.output_tokens,
    )
