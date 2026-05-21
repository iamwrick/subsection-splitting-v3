import json
import re
import sys

from itertools import groupby
from models import ChunkResult, ClassificationResult, TokenUsage, Zone
from prompts import SYSTEM_PROMPT, build_user_prompt
from config import MODEL_ID, MAX_TOKENS, TEMPERATURE

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def classify_chunk(bedrock_client, chunk_text: str) -> tuple[ClassificationResult, TokenUsage]:
    """
    Send one chunk to Nova 2 Lite.
    Returns (ClassificationResult, TokenUsage).
    Raises ValueError if the model returns invalid JSON or JSON that fails Pydantic validation.
    """
    body = {
        "schemaVersion": "messages-v1",
        # Note: cacheControl is only supported via the Converse API, not invoke_model.
        # Prompt caching savings are shown as a projection in the cost report.
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [
            {
                "role": "user",
                "content": [{"text": build_user_prompt(chunk_text)}],
            }
        ],
        "inferenceConfig": {
            "temperature": TEMPERATURE,
            "maxTokens": MAX_TOKENS,
        },
    }

    response = bedrock_client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    status = response["ResponseMetadata"]["HTTPStatusCode"]
    if status != 200:
        body_bytes = response["body"].read()
        raise RuntimeError(
            f"Bedrock returned HTTP {status}: {body_bytes.decode('utf-8', errors='replace')}"
        )

    result   = json.loads(response["body"].read())
    raw_text = result["output"]["message"]["content"][0]["text"]

    # Extract token usage — Nova 2 Lite returns this in the response body.
    # With prompt caching enabled the response includes separate fields for
    # cache-write and cache-read tokens alongside standard input tokens.
    usage_raw = result.get("usage", {})
    token_usage = TokenUsage(
        input_tokens=usage_raw.get("inputTokens", 0),
        output_tokens=usage_raw.get("outputTokens", 0),
        cache_write_tokens=usage_raw.get("cacheWriteInputTokens", 0),
        cache_read_tokens=usage_raw.get("cacheReadInputTokens", 0),
    )

    cleaned = _FENCE_RE.sub("", raw_text).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print(raw_text, file=sys.stderr)
        raise ValueError(f"Model returned invalid JSON: {exc}") from exc

    try:
        return ClassificationResult.model_validate(parsed), token_usage
    except Exception as exc:
        print(f"Pydantic validation failed: {exc}", file=sys.stderr)
        raise ValueError(f"JSON failed schema validation: {exc}") from exc


def enforce_single_page_types(zones: list[Zone]) -> list[Zone]:
    """
    Enforce structural constraints that cannot be expressed in the prompt alone:
    - Standard Airworthiness Certificate is ALWAYS exactly 1 page.
      Any SAC zone spanning multiple pages is a misclassification → reclassify as Other.
    """
    result = []
    for z in zones:
        if z.document_type == "Standard Airworthiness Certificate" and z.start_page != z.end_page:
            print(
                f"  Post-processing: SAC zone pages {z.start_page}-{z.end_page} "
                f"spans multiple pages → reclassified as Other",
                file=sys.stderr,
            )
            result.append(Zone(
                document_type="Other",
                confidence=0.7,
                alternatives=[],
                start_page=z.start_page,
                end_page=z.end_page,
            ))
        else:
            result.append(z)
    return result


def resolve_overlaps(zones: list[Zone]) -> list[Zone]:
    """
    Remove overlapping page ranges from the zone list.
    When two zones overlap, prefer the one with higher confidence; on a tie, prefer
    the smaller (more specific) zone. Operates on a sorted-by-start_page list.
    """
    if not zones:
        return zones

    sorted_zones = sorted(zones, key=lambda z: (z.start_page, z.end_page))
    result: list[Zone] = []

    for zone in sorted_zones:
        if not result:
            result.append(zone)
            continue

        prev = result[-1]

        # No overlap
        if zone.start_page > prev.end_page:
            result.append(zone)
            continue

        # Overlap — keep the higher-confidence zone; on tie keep the smaller one
        prev_span = prev.end_page - prev.start_page
        curr_span = zone.end_page - zone.start_page

        if zone.confidence > prev.confidence or (zone.confidence == prev.confidence and curr_span < prev_span):
            result[-1] = zone
        # else keep prev as-is

    return result


def fill_gaps(zones: list[Zone], pages: list[tuple[int, str]]) -> list[Zone]:
    """
    Insert zones for any pages not covered by model output.
    Uses page hints to infer document type: ARC hint → ARC, otherwise Other.
    Guarantees every page in `pages` appears in exactly one zone.
    """
    covered = set()
    for z in zones:
        covered.update(range(z.start_page, z.end_page + 1))

    page_text = {pno: text for pno, text in pages}
    uncovered = sorted(set(page_text) - covered)
    if not uncovered:
        return zones

    gap_zones: list[Zone] = []
    for _, group in groupby(enumerate(uncovered), key=lambda x: x[1] - x[0]):
        run = [p for _, p in group]
        for page_no in run:
            text = page_text.get(page_no, "")
            inferred = _infer_type_from_hints(text)
            doc_type, conf = inferred if inferred is not None else ("Other", 0.5)
            gap_zones.append(Zone(
                document_type=doc_type,
                confidence=conf,
                alternatives=[],
                start_page=page_no,
                end_page=page_no,
            ))
            print(
                f"  Gap filled: page {page_no} → {doc_type} (not covered by model)",
                file=sys.stderr,
            )

    return sorted(zones + gap_zones, key=lambda z: z.start_page)


def _infer_type_from_hints(text: str) -> tuple[str, float] | None:
    """
    Return (document_type, confidence) inferred from a page's pre-computed hint tokens,
    or None if no strong hint is present (caller should fall back to the surrounding
    zone's type rather than blindly defaulting to Other).
    """
    if "[form:8050]" in text:      # Work Card takes priority over AD/SB references
        return "Work Card", 0.75
    if "[AIRWORTHINESS_DIRECTIVE]" in text:
        return "Airworthiness Directive", 0.85
    if "[SERVICE_BULLETIN]" in text:
        return "Service Bulletin", 0.85
    if "[form:8130]" in text:
        return "Authorized Release Certificate", 0.85
    if "[AFM_SUPPLEMENT]" in text:
        return "Aircraft Flight Manual Supplement", 0.85
    if "[ICA]" in text:
        return "Instructions for Continued Airworthiness", 0.85
    if "[STATUS_REPORT]" in text:
        return "Status Report", 0.85
    if "[RTS_CHECKLIST]" in text:
        return "Other", 0.90
    if "[RETURN_TO_SERVICE]" in text or "[LBE]" in text:
        return "Logbook Entry", 0.75
    if any(h in text for h in ("[vendor]", "[SERVICE_REPORT]", "[WORK_ORDER_DETAIL]")):
        return "Other", 0.85
    return None  # no strong signal — caller keeps surrounding zone type


def fix_boundaries(zones: list[Zone], pages: list[tuple[int, str]]) -> list[Zone]:
    """
    Post-processing pass: split any zone that contains pages flagged with
    [STANDALONE_PAGE]. Each flagged page becomes its own zone with a type
    inferred from its hints; the surrounding pages keep the original zone type.
    Pages without [STANDALONE_PAGE] are left untouched.
    """
    page_text = {pno: text for pno, text in pages}
    result: list[Zone] = []
    splits = 0

    for zone in zones:
        zone_pages = [
            p for p in range(zone.start_page, zone.end_page + 1)
            if p in page_text
        ]

        # Find which pages inside this zone carry [STANDALONE_PAGE]
        standalone = {p for p in zone_pages if "[STANDALONE_PAGE]" in page_text.get(p, "")}

        if not standalone:
            result.append(zone)
            continue

        # Walk page-by-page, emitting sub-zones
        run_start: int | None = None
        for page_no in zone_pages:
            if page_no in standalone:
                # Close any open run of non-standalone pages
                if run_start is not None and run_start < page_no:
                    result.append(Zone(
                        document_type=zone.document_type,
                        confidence=zone.confidence,
                        alternatives=zone.alternatives[:],
                        start_page=run_start,
                        end_page=page_no - 1,
                    ))
                # Emit the standalone page — infer type from hints or keep zone type
                inferred = _infer_type_from_hints(page_text[page_no])
                doc_type, conf = inferred if inferred is not None else (zone.document_type, zone.confidence)
                result.append(Zone(
                    document_type=doc_type,
                    confidence=conf,
                    alternatives=[],
                    start_page=page_no,
                    end_page=page_no,
                ))
                splits += 1
                run_start = page_no + 1
            else:
                if run_start is None:
                    run_start = page_no

        # Close final run
        if run_start is not None and run_start <= zone.end_page:
            result.append(Zone(
                document_type=zone.document_type,
                confidence=zone.confidence,
                alternatives=zone.alternatives[:],
                start_page=run_start,
                end_page=zone.end_page,
            ))

    if splits:
        print(f"  Boundary correction: {splits} standalone page(s) split out", file=sys.stderr)

    return result


def consolidate_adjacent_zones(zones: list[Zone]) -> list[Zone]:
    """
    Merge consecutive zones of the same document type into one.

    This reduces over-segmentation where P1 creates many single-page zones
    of the same type (e.g. 106 individual "Other" pages that should be grouped).
    Only merges when both adjacent zones have the same type and confidence >= 0.65.
    Does NOT merge Logbook Entry, Work Card, or ARC zones (each is a distinct document).
    """
    _NO_MERGE = {"Logbook Entry", "Work Card", "Authorized Release Certificate",
                 "Standard Airworthiness Certificate", "Certificate of Aircraft Registration"}
    if not zones:
        return zones
    result = [zones[0]]
    for z in zones[1:]:
        prev = result[-1]
        if (
            prev.document_type == z.document_type
            and prev.document_type not in _NO_MERGE
            and prev.confidence >= 0.65
            and z.confidence >= 0.65
            and prev.end_page + 1 == z.start_page  # must be strictly adjacent
        ):
            result[-1] = Zone(
                document_type=prev.document_type,
                confidence=min(prev.confidence, z.confidence),
                alternatives=[],
                start_page=prev.start_page,
                end_page=z.end_page,
            )
        else:
            result.append(z)
    return result


def merge_chunk_results(chunk_results: list[ChunkResult]) -> list[Zone]:
    """
    Merge zone lists across chunks.
    If the last zone of chunk N and the first zone of chunk N+1 share the same
    document_type AND neither zone has confidence below 0.70, merge them into a
    single zone. Otherwise concatenate as-is.
    Return the final flat list of zones.
    """
    if not chunk_results:
        return []

    merged: list[Zone] = list(chunk_results[0].zones)

    for chunk in chunk_results[1:]:
        if not chunk.zones:
            continue

        first_new = chunk.zones[0]

        if (
            merged
            and merged[-1].document_type == first_new.document_type
            and merged[-1].confidence >= 0.70
            and first_new.confidence >= 0.70
        ):
            last = merged[-1]
            merged[-1] = Zone(
                document_type=last.document_type,
                confidence=min(last.confidence, first_new.confidence),
                alternatives=[],
                start_page=last.start_page,
                end_page=first_new.end_page,
            )
            merged.extend(chunk.zones[1:])
        else:
            merged.extend(chunk.zones)

    return merged
