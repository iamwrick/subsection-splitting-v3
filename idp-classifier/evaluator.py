"""Compare classifier output zones against a ground-truth CSV."""

import csv
import re
from dataclasses import dataclass

from models import DocumentResult, Zone, VALID_DOCUMENT_TYPES


# ── Page-range parsing ────────────────────────────────────────────────────────

def _parse_page_range(pages_str: str) -> tuple[int, int]:
    pages_str = pages_str.strip()
    m = re.fullmatch(r"(\d+)-(\d+)", pages_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    if "+" in pages_str:
        nums = [int(x) for x in pages_str.split("+")]
        return min(nums), max(nums)
    if pages_str.isdigit():
        n = int(pages_str)
        return n, n
    raise ValueError(f"Cannot parse pages value: {pages_str!r}")


# GT type names that map to our taxonomy types (legacy / variant naming)
_GT_TYPE_ALIASES: dict[str, str] = {
    "ADSI Report": "Status Report",   # Veryon/ADSI AD compliance record ≡ Status Report
}


def _parse_also_acceptable(notes: str) -> list[str]:
    """
    Extract document-type alternatives from a notes string.
    Handles patterns like "also acceptable: Logbook Entry"
    and "also acceptable: Logbook Entry, Other".
    Only returns values that are valid taxonomy types.
    """
    if not notes:
        return []
    acceptable = []
    for segment in re.findall(r"also acceptable:\s*([^;]+)", notes, re.I):
        for part in segment.split(","):
            candidate = part.strip().rstrip(".")
            if candidate in VALID_DOCUMENT_TYPES:
                acceptable.append(candidate)
    return acceptable


# ── Ground-truth loading ──────────────────────────────────────────────────────

@dataclass
class GTZone:
    doc_index: int
    start_page: int
    end_page: int
    document_type: str
    acceptable_types: list[str]   # additional accepted types from notes
    raw_pages: str


def load_ground_truth(csv_path: str) -> list[GTZone]:
    """
    Read the CSV and return one GTZone per document index (document_type rows only).
    Parses 'also acceptable: X' from the notes column into acceptable_types.
    """
    zones: dict[int, GTZone] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["field"].strip() != "document_type":
                continue
            doc_index = int(row["doc_index"])
            raw_pages = row["pages"].strip()
            start, end = _parse_page_range(raw_pages)
            primary = _GT_TYPE_ALIASES.get(row["expected_value"].strip(), row["expected_value"].strip())
            notes = row.get("notes", "")
            also = _parse_also_acceptable(notes)
            # deduplicate, exclude primary
            acceptable = [t for t in also if t != primary]
            zones[doc_index] = GTZone(
                doc_index=doc_index,
                start_page=start,
                end_page=end,
                document_type=primary,
                acceptable_types=acceptable,
                raw_pages=raw_pages,
            )

    return [zones[k] for k in sorted(zones)]


# ── Zone comparison ───────────────────────────────────────────────────────────

@dataclass
class ZoneMatch:
    gt: GTZone
    predicted: Zone | None
    pages_match: bool
    type_match: bool          # primary OR acceptable type matched
    type_match_primary: bool  # matched the primary expected type exactly

    @property
    def exact_match(self) -> bool:
        return self.pages_match and self.type_match


def _find_predicted_zone(gt: GTZone, predicted: list[Zone]) -> Zone | None:
    # Exact page range first
    for z in predicted:
        if z.start_page == gt.start_page and z.end_page == gt.end_page:
            return z
    # Best overlap fallback
    gt_pages = set(range(gt.start_page, gt.end_page + 1))
    best: Zone | None = None
    best_overlap = 0
    for z in predicted:
        overlap = len(gt_pages & set(range(z.start_page, z.end_page + 1)))
        if overlap > best_overlap:
            best_overlap = overlap
            best = z
    return best


def compare(result: DocumentResult, gt_zones: list[GTZone]) -> list[ZoneMatch]:
    matches = []
    for gt in gt_zones:
        pred = _find_predicted_zone(gt, result.zones)
        pages_match = (
            pred is not None
            and pred.start_page == gt.start_page
            and pred.end_page == gt.end_page
        )
        type_match_primary = pred is not None and pred.document_type == gt.document_type
        type_match = type_match_primary or (
            pred is not None and pred.document_type in gt.acceptable_types
        )
        matches.append(ZoneMatch(
            gt=gt,
            predicted=pred,
            pages_match=pages_match,
            type_match=type_match,
            type_match_primary=type_match_primary,
        ))
    return matches


# ── Summary ───────────────────────────────────────────────────────────────────

@dataclass
class EvalSummary:
    total_gt_zones: int
    exact_matches: int
    type_only_matches: int
    page_only_matches: int
    misses: int
    matches: list[ZoneMatch]

    @property
    def exact_accuracy(self) -> float:
        return self.exact_matches / self.total_gt_zones if self.total_gt_zones else 0.0

    @property
    def type_accuracy(self) -> float:
        return (self.exact_matches + self.type_only_matches) / self.total_gt_zones if self.total_gt_zones else 0.0


def summarise(matches: list[ZoneMatch]) -> EvalSummary:
    exact      = sum(1 for m in matches if m.exact_match)
    type_only  = sum(1 for m in matches if m.type_match and not m.pages_match)
    page_only  = sum(1 for m in matches if m.pages_match and not m.type_match)
    misses     = sum(1 for m in matches if m.predicted is None)
    return EvalSummary(
        total_gt_zones=len(matches),
        exact_matches=exact,
        type_only_matches=type_only,
        page_only_matches=page_only,
        misses=misses,
        matches=matches,
    )


# ── Text report ───────────────────────────────────────────────────────────────

def format_report(summary: EvalSummary, input_file: str) -> str:
    lines = [
        "=" * 72,
        f"  Evaluation Report — {input_file}",
        "=" * 72,
        f"  Ground-truth zones : {summary.total_gt_zones}",
        f"  Exact matches       : {summary.exact_matches}  (pages + type correct)",
        f"  Type-only matches   : {summary.type_only_matches}  (type correct, pages differ)",
        f"  Page-only matches   : {summary.page_only_matches}  (pages correct, type wrong)",
        f"  Misses              : {summary.misses}  (no predicted zone found)",
        f"  Exact accuracy      : {summary.exact_accuracy * 100:.1f}%",
        f"  Type accuracy       : {summary.type_accuracy * 100:.1f}%",
        "=" * 72,
        "",
        f"  {'#':<3}  {'GT Pages':<10}  {'GT Type':<35}  {'Pred Pages':<12}  "
        f"{'Pred Type':<35}  {'Status'}",
        f"  {'-'*3}  {'-'*10}  {'-'*35}  {'-'*12}  {'-'*35}  {'-'*10}",
    ]

    for i, m in enumerate(summary.matches, start=1):
        pred_pages = "—"
        pred_type  = "—"
        if m.predicted:
            sp, ep = m.predicted.start_page, m.predicted.end_page
            pred_pages = str(sp) if sp == ep else f"{sp}-{ep}"
            pred_type  = m.predicted.document_type

        # Show acceptable flag next to GT type when relevant
        gt_label = m.gt.document_type
        if m.gt.acceptable_types:
            gt_label += f" [+{','.join(m.gt.acceptable_types)}]"

        if m.exact_match:
            status = "✓ EXACT" if m.type_match_primary else "✓ EXACT (alt)"
        elif m.type_match:
            status = "~ TYPE OK" if m.type_match_primary else "~ TYPE OK (alt)"
        elif m.pages_match:
            status = "~ PAGE OK"
        elif m.predicted is None:
            status = "✗ MISS"
        else:
            status = "✗ WRONG"

        lines.append(
            f"  {i:<3}  {m.gt.raw_pages:<10}  {gt_label:<45}  {pred_pages:<12}  "
            f"{pred_type:<35}  {status}"
        )

    lines.append("")
    return "\n".join(lines)
