"""
P3 (split) and P4 (extract) prompts for each document type.

P3: given a zone's page text, split it into individual documents.
P4: given a document's page text, extract structured metadata.
"""

# ── P3 SPLIT ─────────────────────────────────────────────────────────────────

P3_SYSTEM = """You are an expert aviation document analyst. You will be given OCR text
from a document zone that has already been classified by type. Your job is to identify
where individual documents begin and end within the zone.

Rules:
- Return ONLY valid JSON — no preamble, no markdown, no explanations.
- The JSON must be an array of objects with: start_page, end_page, document_title, boundary_reason, document_type.
- Pages must cover the full zone with no gaps and no overlaps.
- start_page and end_page are the actual page numbers from the input (not offsets).
- If the zone is a single document, return a one-element array.
- boundary_reason: one concise sentence explaining what signals the document boundary.
- document_type: the type of this specific document (may refine the zone type if needed).

Boundary signals by document type:
- Logbook Entry: each individual entry starts with a form number (e.g. "0000376 - 1/1"), a
  "Date / WO# / Make & Model" header row, or "Airframe Logbook Entry" / "Engine Logbook Entry"
  title. Each entry ends at the technician signature block ("Date / Signature / License # /
  Inspector" row). Multiple entries per page are possible — when two form headers appear on
  the same page, keep them in the same document boundary (same page range). Treat each
  contiguous block sharing a single signature block as one document.
- Authorized Release Certificate (FAA Form 8130-3): almost always exactly 1 page per certificate.
  Split on every occurrence of "AUTHORIZED RELEASE CERTIFICATE" / "FAA Form 8130-3" header.
- Work Card: split on task code / job number / work instruction header. Each card has its own
  task reference number. Multi-page cards that share a single task number stay together.
- Status Report: typically 1 page per report; split when a new report header appears.
- Other types: split when a new document title / cover page / header appears on a fresh page.
"""

# Per-type guidance injected into the user prompt
_P3_TYPE_HINTS = {
    "Logbook Entry": (
        "SPLITTING GUIDANCE — Logbook Entry:\n"
        "• Each entry is bounded by a form number (e.g. '0000376 - 1/1') or a\n"
        "  header containing 'Date', 'WO#', 'Make & Model' in a structured table,\n"
        "  followed by work tasks and ending with a technician signature block.\n"
        "• Multiple stacked entries on the same page count as separate documents\n"
        "  only when each has its own complete Date+WO#+Signature set.\n"
        "• If you cannot identify a clear boundary within a page, keep the page\n"
        "  as a single document.\n"
        "• IMPORTANT: return one JSON element per identified entry, even if this\n"
        "  means listing dozens of elements. Do not truncate the list.\n"
    ),
    "Authorized Release Certificate": (
        "SPLITTING GUIDANCE — Authorized Release Certificate (FAA Form 8130-3):\n"
        "• Each certificate is 1 page. Split at every new 'AUTHORIZED RELEASE\n"
        "  CERTIFICATE' / 'FAA Form 8130-3' header — each new occurrence on a\n"
        "  page is a separate certificate.\n"
        "• Return one JSON element per page (or per certificate header if multiple\n"
        "  certificates appear on one page).\n"
    ),
    "Work Card": (
        "SPLITTING GUIDANCE — Work Card:\n"
        "• Each card has a unique task code / job number / work instruction ID.\n"
        "• Multi-page cards that share a single task reference stay together.\n"
        "• Split when a new task code header or 'Job Card' cover appears.\n"
    ),
}


def build_p3_prompt(zone_text: str, doc_type: str, start_page: int, end_page: int) -> str:
    n_pages = end_page - start_page + 1
    type_hint = _P3_TYPE_HINTS.get(doc_type, "")
    hint_block = f"\n{type_hint}\n" if type_hint else "\n"
    return (
        f"=== ZONE: pages {start_page} through {end_page} "
        f"({n_pages} page{'s' if n_pages>1 else ''}, classified as: {doc_type}) ==="
        f"{hint_block}"
        f"{zone_text}\n\n"
        f"Split the above zone into individual {doc_type} documents.\n"
        "Return JSON array (include ALL documents — do not stop early):\n"
        '[{"start_page": N, "end_page": M, "document_type": "...", '
        '"document_title": "...", "boundary_reason": "..."}]'
    )


# ── P4 EXTRACT — per-type schemas and prompts ─────────────────────────────────

P4_SYSTEM = """You are an expert aviation document analyst. Extract structured metadata
from the document text provided.

Rules:
- Return ONLY valid JSON matching the schema below — no preamble, no markdown.
- Use null for any field not present in the document.
- Dates must be in YYYY-MM-DD format where possible; use null if unparseable.
- Be precise: extract values exactly as written; do not infer or fabricate.
- Scan the ENTIRE document text for each field — dates and signatures often appear
  at the bottom of the page or in labeled table cells (e.g. "13e. Date", "14e. Date").
- For document_date: check headers, footers, title blocks, signatures, and any labeled
  date field. Even a date stamp in a corner counts. Try hard before returning null.
- For document_title: extract the primary heading — the first major title text visible
  in the document body. Avoid appending subtitles or long qualifiers. IMPORTANT: text
  inside square brackets like [RETURN_TO_SERVICE], [LBE], [form:8130] are internal
  metadata tags. ALL_CAPS_UNDERSCORED identifiers like STATUS_REPORT, RETURN_TO_SERVICE,
  WORK_ORDER_DETAIL are also internal tags. Never use any of these as document_title.
"""

# Per-type extraction hints (appended to the user prompt before the schema)
_P4_HINTS = {
    "Logbook Entry": (
        "EXTRACTION HINTS — Logbook Entry:\n"
        "• document_date: Extract THIS document's own transaction date. This document\n"
        "  starts at the beginning of the provided text — look for the entry header\n"
        "  (WO#, form number, or 'Date | WO#' row) at the TOP of the text and use\n"
        "  the date from that first header. Ignore dates from task descriptions,\n"
        "  referenced parts, prior logbook entries further down, or AD compliance.\n"
        "  - Digital format (PNC/Flightdocs/CAMP): header columns in this order:\n"
        "    'Date | WO# | Airframe Make & Model | Airframe Hours | Airframe Landings |\n"
        "    Airframe Serial Number'. Values row immediately follows — date is first.\n"
        "  - Handwritten 'Y M D' column: three adjacent numbers = day/month/2-digit-year\n"
        "    (e.g. '05 08 20' = 2020-08-05). Each page has entries in chronological order\n"
        "    (oldest at top, newest at bottom). Use the LAST (most recent) entry's date.\n"
        "  - Venture/other: look for 'Date: [value]' in the entry header block.\n"
        "• document_title: return only the form name or entry type — the short header that\n"
        "  describes this entry (e.g. 'Maintenance Transaction Record', 'AIRFRAME LOG ENTRY',\n"
        "  'Engine Entry', 'Airframe Entries'). Do NOT prepend the aircraft N-number or\n"
        "  registration to the title. Do NOT return the aircraft make/model as the title.\n"
        "• serial_number: prefer the AIRFRAME serial number (near 'Airframe Serial Number'\n"
        "  or 'A/C S/N'), which identifies the aircraft (e.g. 'RC-65', '700-0014', '1530').\n"
        "  For ENGINE logbook entries (page contains 'Engine Entry', '#1 Eng', '#2 Eng'),\n"
        "  the serial number is the ENGINE serial number (near '#1 Eng S/N:', 'Engine SN:',\n"
        "  or 'Engine 1 SN:'). Return whatever S/N is most prominent if unsure.\n"
        "• total_hours: labeled 'Airframe Hours', 'TT:', or — for engine entries —\n"
        "  'TSN' (Total Service Hours/Time Since New), '#1 Eng TSN:'. Typically decimal.\n"
        "• total_cycles: labeled 'CSN' (Cycles Since New), '#1 Eng CSN:', 'Enc' (Engine\n"
        "  Cycles), or 'Engine Cycles'. Written as 'N,XXX Enc' or '#1 Eng CSN: XXXX'.\n"
        "  Only present in engine logbook entries — null for airframe-only entries.\n"
        "• total_hours and total_landings — column ordering guidance:\n"
        "  In digital logbooks the column order is: Airframe Hours | Airframe Landings |\n"
        "  Airframe Serial Number. Match values to their labels.\n"
        "  - total_hours is typically a decimal (e.g. 4961.4, 1684.5).\n"
        "  - total_landings is typically a whole number (e.g. 2154, 750).\n"
        "  When the label is ambiguous: the decimal value is more likely hours.\n"
        "• registration_number: N-number near 'Registration:' or in the header.\n"
        "• work_order_number: labeled 'WO#' or 'Work Order' in the entry header.\n"
        "• signatures: look for 'Date | Signature | License # | Inspector' or similar\n"
        "  label rows. Technician name and cert number follow. Multiple techs = multiple sigs.\n"
        "• tasks: each numbered line (01., 02. etc.) after 'Performed the Following\n"
        "  Maintenance' or 'Component Changes' or similar heading is one task.\n"
    ),
    "Authorized Release Certificate": (
        "EXTRACTION HINTS — Authorized Release Certificate (FAA Form 8130-3):\n"
        "• document_date: check these field locations in priority order:\n"
        "  - Field 14e ('14e. Date (dd/mmm/yyyy):') — newer form, format: 04/Jan/2019\n"
        "  - Field 13e ('13e. Date (dd/mmm/yyyy):') — also newer form\n"
        "  - Field 18 ('18. Date (m/d/y):') — older form 8130-3 (6-01), format: JAN/21/2014\n"
        "  The date value immediately follows the label in the OCR flow.\n"
        "• part_number: field 8 ('8. Part Number:').\n"
        "• part_description: field 7 ('7. Description:').\n"
        "• serial_number: field 10 ('10. Serial Number:').\n"
        "• quantity: field 9 ('9. Quantity:').\n"
        "• status_condition: field 11 ('11. Status/Work:') — typically INSPECTED, NEW,\n"
        "  REPAIRED, or OVERHAULED.\n"
        "• approval_reference: field 14c ('14c. Approval/Certificate No.:') or\n"
        "  field 13c ('13c. Approval/Authorization No.:').\n"
        "• work_order_number: field 5 ('5. Work Order/Contract/Invoice Number:').\n"
        "• signatures: field 14b ('14b. Authorized Signature:') with name from field 14d;\n"
        "  or field 15 ('15. Authorized Signature:') with name from field 17 on older forms.\n"
    ),
    "Work Card": (
        "EXTRACTION HINTS — Work Card:\n"
        "• document_date: the date THIS task was CURRENTLY completed/signed off.\n"
        "  CRITICAL for CAMP work cards: the line 'ACCOMPLISHED: [date] HRS, [AFL], [date]'\n"
        "  in the task body shows the PREVIOUS compliance date (when it was last done).\n"
        "  The CURRENT completion date is in the SIGN-OFF BLOCK at the BOTTOM of the form —\n"
        "  near the technician's name, CRS number, and signature. Look for 'Date Completed:',\n"
        "  'Date Signed:', or an inline date in the signature row. If the sign-off block has\n"
        "  no date (card not yet signed), return null rather than the previous ACCOMPLISHED date.\n"
        "  For non-CAMP cards: look for 'Date:', 'Accomplished:', or 'Completed:' fields.\n"
        "• registration_number: N-number near 'A/C Reg', 'Registration', or 'Tail Number'.\n"
        "• serial_number: prefer the airframe serial number near 'A/C S/N' or 'Serial\n"
        "  Number'. If you find it, use it; otherwise return any serial number present.\n"
        "• total_hours and total_landings: for CAMP work cards, these come from the\n"
        "  'ACCOMPLISHED: [hours] HRS, [landings] AFL, [date]' line in the task body —\n"
        "  the decimal value is hours (e.g. 8030.7), the whole number is landings (e.g. 6216).\n"
        "  This is SEPARATE from the sign-off block — do NOT take hours/landings from there.\n"
        "  For non-CAMP cards, look for 'Airframe Hours', 'A/C Hours', 'TT' (decimal),\n"
        "  and 'Airframe Landings', 'A/C Landings', 'LDG' (whole number).\n"
        "• total_cycles: cycles at time of task (turbine engines only).\n"
        "• work_order_number: labeled 'WO#', 'Work Order', or 'Job Number'.\n"
        "• regulatory_reference: task code, WID number, or AMM reference (e.g. 71-00-00-IC).\n"
        "• signatures: 'Accomplished By', 'Inspected By', 'Technician', or 'Inspector' fields\n"
        "  with a name and certificate number.\n"
    ),
    "Airworthiness Directive": (
        "EXTRACTION HINTS — Airworthiness Directive:\n"
        "• document_date: the EFFECTIVE DATE of the AD. Look for 'Effective Date:',\n"
        "  'Effective:' or 'This AD is effective [date]'. Format: month day, year or YYYY-MM-DD.\n"
        "• regulatory_reference: the FAA docket number (e.g. 'FAA-2020-1234') or AD number\n"
        "  (e.g. '2020-21-02'). Often in the title or header.\n"
        "• make_model: the applicability statement — what aircraft or component this AD covers.\n"
    ),
    "Service Bulletin": (
        "EXTRACTION HINTS — Service Bulletin:\n"
        "• document_date: issue date of the SB. Look for 'Date:', 'Issue Date:',\n"
        "  'Revision Date:', or a date line near the SB number.\n"
        "• regulatory_reference: the SB number (e.g. '350-27-010', 'SB-A320-27-1234').\n"
        "• make_model: the applicability — what aircraft/component the SB applies to.\n"
    ),
    "Major Repair and Alteration": (
        "EXTRACTION HINTS — Major Repair and Alteration (FAA Form 337):\n"
        "• document_date: look for Block 1 'Date' or 'Date of Approval'. Also 'Approved by'\n"
        "  signatures near a date. Format varies: MM/DD/YYYY or Month DD, YYYY.\n"
        "• registration_number: Block 3 'Aircraft Registration Mark' (N-number).\n"
        "• serial_number: Block 4 'Aircraft Serial No.'.\n"
        "• make_model: Block 5 'Aircraft Make and Model'.\n"
        "• approval_reference: DER approval number or FSDO office.\n"
        "• signatures: Block 7 'Return to Service' certification with name and cert #.\n"
    ),
    "Instructions for Continued Airworthiness": (
        "EXTRACTION HINTS — Instructions for Continued Airworthiness:\n"
        "• document_date: look for 'Effective Date:', 'Issue Date:', 'Revision Date:',\n"
        "  or a date line in the header/footer of the document.\n"
        "• regulatory_reference: the document number (e.g. 'L60-CA001', 'ICA-SB-2020-01').\n"
    ),
    "Other": (
        "EXTRACTION HINTS — Other (diverse aviation documents):\n"
        "• document_date: SCAN THE ENTIRE DOCUMENT aggressively. Dates appear in many places:\n"
        "  - 'Date:', 'Effective:', 'Issue Date:', 'Revision:', 'Rev. Date:' labels\n"
        "  - 'Test Date:', 'Report Date:', 'Date of Analysis:' (certificates of analysis)\n"
        "  - 'Ship Date:', 'Invoice Date:', 'Order Date:' (shipping/packing documents)\n"
        "  - 'Certified:', 'Issued:', 'Valid From:' (conformity/compliance certificates)\n"
        "  - Header/footer date stamps, even partial dates\n"
        "  - STC/AML documents: 'Effective Date:', 'Approval Date:', revision tables\n"
        "  Match any format: dd-Mon-YYYY, MM/DD/YYYY, YYYY-MM-DD, Mon dd YYYY.\n"
        "• document_title: the main heading, form name, or document type at the top.\n"
        "• serial_number: any serial number, lot number, or batch number.\n"
        "• part_number: any part number, material number, or specification number.\n"
    ),
}

# Schema definitions (shown to model as part of prompt)
_SCHEMAS = {

"Logbook Entry": """{
  "document_date": "YYYY-MM-DD or null",
  "document_date_raw": "date as written or null",
  "document_title": "form name / entry title or null",
  "date_confidence": "high|medium|low",
  "registration_number": "N-number or null",
  "serial_number": "aircraft serial number or null",
  "make_model": "aircraft make and model or null",
  "work_order_number": "WO# or null",
  "total_hours": "airframe total time or null",
  "total_landings": "total landings or null",
  "total_cycles": "total cycles (turbine only) or null",
  "tasks": [
    {
      "task_description": "brief description of work performed",
      "part_number_removed": "P/N off or null",
      "serial_number_removed": "S/N off or null",
      "part_number_installed": "P/N on or null",
      "serial_number_installed": "S/N on or null",
      "compliance_status": "Complied/N/A/etc or null",
      "reference": "AMM/AD/SB reference or null"
    }
  ],
  "signatures": [
    {
      "full_name": "technician name or null",
      "title": "title/role or null",
      "certificate_number": "A&P/IA/CRS number or null",
      "date_of_signature": "YYYY-MM-DD or null"
    }
  ],
  "summary": "one-sentence summary of work performed"
}""",

"Authorized Release Certificate": """{
  "document_date": "YYYY-MM-DD or null",
  "document_date_raw": "date as written in field 13e or 14e (dd/mmm/yyyy) or null",
  "document_title": "Authorized Release Certificate — always use this exact string for FAA Form 8130-3; use 'EASA Form 1' only for European-authority certificates",
  "date_confidence": "high|medium|low",
  "part_number": "field 8 part number or null",
  "part_description": "field 7 description or null",
  "serial_number": "field 10 serial number or null",
  "quantity": "field 9 quantity or null",
  "status_condition": "field 11 status: NEW|REPAIRED|OVERHAULED|INSPECTED or null",
  "approval_reference": "field 14c or 13c approval/certificate number or null",
  "work_order_number": "field 5 work order/invoice number or null",
  "signatures": [
    {
      "full_name": "field 14d or 13d authorized name or null",
      "certificate_number": "field 14c approval number or null",
      "date_of_signature": "YYYY-MM-DD from field 14e or 13e or null"
    }
  ],
  "summary": "one-sentence summary"
}""",

"Work Card": """{
  "document_date": "YYYY-MM-DD (accomplished date) or null",
  "document_date_raw": "date as written or null",
  "document_title": "task title or null",
  "date_confidence": "high|medium|low",
  "registration_number": "N-number or null",
  "serial_number": "aircraft serial number or null",
  "work_order_number": "WO# or null",
  "total_hours": "airframe hours at time of work or null",
  "total_landings": "landings at time of work or null",
  "total_cycles": "cycles at time of work (turbine only) or null",
  "regulatory_reference": "task code / WID / AMM reference or null",
  "tasks": [
    {
      "task_description": "task description",
      "part_number_removed": "P/N off or null",
      "serial_number_removed": "S/N off or null",
      "part_number_installed": "P/N on or null",
      "serial_number_installed": "S/N on or null",
      "compliance_status": "Complied/N/A etc or null"
    }
  ],
  "signatures": [
    {
      "full_name": "technician name or null",
      "title": "Technician/Inspector/etc or null",
      "certificate_number": "CRS/A&P number or null",
      "date_of_signature": "YYYY-MM-DD or null"
    }
  ],
  "summary": "one-sentence summary of task"
}""",

"Status Report": """{
  "document_date": "YYYY-MM-DD (report date) or null",
  "document_date_raw": "date as written or null",
  "document_title": "report title or null",
  "date_confidence": "high|medium|low",
  "registration_number": "N-number or null",
  "serial_number": "aircraft S/N or null",
  "make_model": "aircraft make/model or null",
  "summary": "one-sentence description of the compliance tracking document"
}""",

"Airworthiness Directive": """{
  "document_date": "YYYY-MM-DD (effective date) or null",
  "document_date_raw": "effective date as written or null",
  "document_title": "AD title or null",
  "date_confidence": "high|medium|low",
  "regulatory_reference": "docket number (e.g. FAA-2020-1234) or null",
  "make_model": "aircraft/component applicability or null",
  "summary": "one-sentence description of what the AD requires"
}""",

"Service Bulletin": """{
  "document_date": "YYYY-MM-DD (issue date) or null",
  "document_date_raw": "date as written or null",
  "document_title": "SB title or null",
  "date_confidence": "high|medium|low",
  "regulatory_reference": "SB number or null",
  "make_model": "applicable aircraft/component or null",
  "summary": "one-sentence description of what the SB requires"
}""",

"Major Repair and Alteration": """{
  "document_date": "YYYY-MM-DD or null",
  "document_date_raw": "date as written or null",
  "document_title": "Major Repair and Alteration",
  "date_confidence": "high|medium|low",
  "registration_number": "N-number or null",
  "serial_number": "aircraft S/N or null",
  "make_model": "aircraft make/model or null",
  "approval_reference": "DER approval number or null",
  "signatures": [
    {
      "full_name": "approving technician or DER name or null",
      "title": "DER/IA/A&P or null",
      "certificate_number": "certificate/approval number or null",
      "date_of_signature": "YYYY-MM-DD or null"
    }
  ],
  "summary": "one-sentence description of the repair/alteration"
}""",

"Supplemental Type Certificate": """{
  "document_date": "YYYY-MM-DD (date of issuance) or null",
  "document_date_raw": "date as written or null",
  "document_title": "STC title or null",
  "date_confidence": "high|medium|low",
  "regulatory_reference": "STC number (e.g. ST11071SC) or null",
  "make_model": "aircraft type design or null",
  "summary": "one-sentence description of what the STC approves"
}""",

"Instructions for Continued Airworthiness": """{
  "document_date": "YYYY-MM-DD or null",
  "document_date_raw": "date as written or null",
  "document_title": "ICA document title or null",
  "date_confidence": "high|medium|low",
  "regulatory_reference": "ICA document number (e.g. L60-CA001) or null",
  "summary": "one-sentence description of the maintenance supplement"
}""",

"Statement of Compliance": """{
  "document_date": "YYYY-MM-DD or null",
  "document_date_raw": "date as written or null",
  "document_title": "Statement of Compliance",
  "date_confidence": "high|medium|low",
  "approval_reference": "DER designation number (DERT-XXXXXX-XX) or null",
  "make_model": "aircraft or component or null",
  "summary": "one-sentence description"
}""",

"Certificate of Aircraft Registration": """{
  "document_date": "YYYY-MM-DD (date of issue or expiry) or null",
  "document_date_raw": "date as written or null",
  "document_title": "Certificate of Aircraft Registration",
  "date_confidence": "high|medium|low",
  "registration_number": "N-number or null",
  "serial_number": "aircraft S/N or null",
  "make_model": "aircraft make/model or null",
  "summary": "one-sentence description"
}""",

"Standard Airworthiness Certificate": """{
  "document_date": "YYYY-MM-DD (date of issue) or null",
  "document_date_raw": "date as written or null",
  "document_title": "Standard Airworthiness Certificate",
  "date_confidence": "high|medium|low",
  "registration_number": "N-number or null",
  "serial_number": "aircraft S/N or null",
  "make_model": "aircraft make/model or null",
  "summary": "one-sentence description"
}""",

"Aircraft Flight Manual Supplement": """{
  "document_date": "YYYY-MM-DD (date of issue) or null",
  "document_date_raw": "date as written or null",
  "document_title": "AFMS document title or null",
  "date_confidence": "high|medium|low",
  "regulatory_reference": "AFMS document number or null",
  "summary": "one-sentence description"
}""",

"Other": """{
  "document_date": "YYYY-MM-DD — scan entire document including headers, footers, and date labels. Return null ONLY if no date exists anywhere.",
  "document_date_raw": "date as written or null",
  "document_title": "main heading or form name or null",
  "date_confidence": "high|medium|low",
  "registration_number": "aircraft N-number if present or null",
  "serial_number": "serial/lot/batch number if present or null",
  "part_number": "part/material/specification number if present or null",
  "work_order_number": "work order or order number if present or null",
  "summary": "one-sentence description of what this document is"
}""",
}

# Default schema for types not explicitly listed
_DEFAULT_SCHEMA = """{
  "document_date": "YYYY-MM-DD or null",
  "document_date_raw": "date as written or null",
  "document_title": "title or null",
  "date_confidence": "high|medium|low",
  "summary": "one-sentence description"
}"""


def build_p4_prompt(doc_text: str, doc_type: str, start_page: int, end_page: int) -> str:
    schema = _SCHEMAS.get(doc_type, _DEFAULT_SCHEMA)
    hint = _P4_HINTS.get(doc_type, "")
    hint_block = f"\n{hint}\n" if hint else "\n"
    n_pages = end_page - start_page + 1
    return (
        f"=== DOCUMENT: {doc_type} (pages {start_page}–{end_page}, {n_pages} page{'s' if n_pages>1 else ''}) ==="
        f"{hint_block}"
        f"{doc_text}\n\n"
        f"Extract metadata from the above {doc_type} document.\n"
        f"Return ONLY valid JSON matching this schema:\n{schema}"
    )


def get_p4_schema(doc_type: str) -> str:
    return _SCHEMAS.get(doc_type, _DEFAULT_SCHEMA)
