"""Score P4 metadata extraction against ground-truth CSV fields."""

import csv
import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Optional


SCORED_FIELDS = frozenset({
    "document_date",
    "document_title",
    "serial_number",
    "total_hours",
    "total_landings",
    "total_cycles",
})


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class GTDocument:
    doc_index: int
    start_page: int
    end_page: int
    raw_pages: str
    fields: dict[str, str]       # field_name -> expected_value
    field_notes: dict[str, str]  # field_name -> notes column


@dataclass
class FieldResult:
    field: str
    expected: str
    predicted: Optional[str]
    status: str  # 'correct' | 'wrong' | 'missing'


@dataclass
class DocP4Result:
    doc_index: int
    gt_pages: str
    matched_pages: Optional[str]  # predicted doc pages (None = no overlap found)
    field_results: list[FieldResult] = dc_field(default_factory=list)


@dataclass
class P4EvalSummary:
    input_file: str
    total_gt_docs: int
    unmatched_gt_docs: int
    doc_results: list[DocP4Result]
    field_stats: dict[str, dict]  # field -> {total, correct, wrong, missing, presence_pct, accuracy_pct}
    overall_total: int
    overall_correct: int
    overall_found: int  # correct + wrong (i.e. non-null predictions)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_page_range(pages_str: str) -> tuple[int, int]:
    s = pages_str.strip()
    m = re.fullmatch(r"(\d+)-(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    if "+" in s:
        nums = [int(x) for x in s.split("+")]
        return min(nums), max(nums)
    if s.isdigit():
        n = int(s)
        return n, n
    raise ValueError(f"Cannot parse pages: {s!r}")


def _parse_acceptable(notes: str) -> list[str]:
    """Extract 'also acceptable: X, Y' alternatives from notes column."""
    result = []
    for segment in re.findall(r"also acceptable:\s*([^;]+)", notes, re.I):
        for part in segment.split(","):
            v = part.strip().rstrip(".")
            if v:
                result.append(v)
    return result


def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(s.strip().lower().split())


def _norm_title(s: Optional[str]) -> str:
    """Normalise a document title for fuzzy comparison."""
    if not s:
        return ""
    t = s.strip().lower()
    # Replace punctuation that varies (hyphens, dashes, slashes) with space
    t = re.sub(r"[-–—/\\]", " ", t)
    # Common word variants
    t = re.sub(r"\bcertification\b", "certificate", t)
    t = re.sub(r"\bcertificates\b", "certificate", t)
    t = re.sub(r"\bentries\b", "entry", t)
    t = re.sub(r"\blogs\b", "log", t)
    return " ".join(t.split())


def _norm_serial(s: Optional[str]) -> str:
    """Normalise a serial number — collapse punctuation."""
    if not s:
        return ""
    return re.sub(r"[-–/\s]", "", s.strip().lower())


def _find_best_doc(gt_start: int, gt_end: int, docs: list[dict]) -> Optional[dict]:
    """Return extracted doc with greatest page overlap with [gt_start, gt_end]."""
    gt_pages = set(range(gt_start, gt_end + 1))
    best: Optional[dict] = None
    best_overlap = 0
    for doc in docs:
        sp = doc.get("start_page", 0)
        ep = doc.get("end_page", 0)
        overlap = len(gt_pages & set(range(sp, ep + 1)))
        if overlap > best_overlap:
            best_overlap = overlap
            best = doc
    return best


# ── Field scorers ─────────────────────────────────────────────────────────────

def _score_date(pred: Optional[str], expected: str, notes: str) -> str:
    if not pred:
        return "missing"
    candidates = [expected] + _parse_acceptable(notes)
    pred_norm = _norm(pred)
    for c in candidates:
        if pred_norm == _norm(c):
            return "correct"
    return "wrong"


def _score_title(pred: Optional[str], expected: str, notes: str) -> str:
    if not pred:
        return "missing"
    candidates = [expected] + _parse_acceptable(notes)
    pred_n = _norm_title(pred)
    for c in candidates:
        c_n = _norm_title(c)
        if pred_n == c_n:
            return "correct"
        # Substring in either direction (min 8 chars of the shorter)
        shorter, longer = (pred_n, c_n) if len(pred_n) <= len(c_n) else (c_n, pred_n)
        if len(shorter) >= 8 and shorter in longer:
            return "correct"
    return "wrong"


def _score_numeric(pred: Optional[str], expected: str) -> str:
    if not pred:
        return "missing"
    try:
        pred_f = float(str(pred).strip().replace(",", ""))
        exp_f  = float(expected.strip().replace(",", ""))
        tol = max(0.15, abs(exp_f) * 0.001)
        return "correct" if abs(pred_f - exp_f) <= tol else "wrong"
    except (ValueError, TypeError):
        return "wrong"


def _score_serial(pred: Optional[str], expected: str) -> str:
    if not pred:
        return "missing"
    pred_n = _norm_serial(pred)
    exp_n  = _norm_serial(expected)
    if pred_n == exp_n:
        return "correct"
    # Accept suffix match: "525a0408" predicted when GT is "0408", or vice-versa
    if pred_n.endswith(exp_n) or exp_n.endswith(pred_n):
        return "correct"
    return "wrong"


def _score_field(fname: str, pred: Optional[str], expected: str, notes: str) -> FieldResult:
    if fname == "document_date":
        status = _score_date(pred, expected, notes)
    elif fname == "document_title":
        status = _score_title(pred, expected, notes)
    elif fname in ("total_hours", "total_landings", "total_cycles"):
        status = _score_numeric(pred, expected)
    elif fname == "serial_number":
        status = _score_serial(pred, expected)
    else:
        status = "wrong"
    return FieldResult(field=fname, expected=expected, predicted=pred, status=status)


# ── Ground-truth loader ───────────────────────────────────────────────────────

def load_p4_ground_truth(csv_path: str) -> list[GTDocument]:
    """
    Load all SCORED_FIELDS rows from ac.csv grouped by doc_index.
    Page ranges are taken from the document_type row for each doc_index.
    """
    page_ranges: dict[int, tuple[int, int, str]] = {}
    field_vals: dict[int, dict[str, str]] = {}
    field_notes: dict[int, dict[str, str]] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fname = row["field"].strip()
            doc_idx = int(row["doc_index"])
            raw_pages = row["pages"].strip()

            if fname == "document_type":
                try:
                    start, end = _parse_page_range(raw_pages)
                    page_ranges[doc_idx] = (start, end, raw_pages)
                except ValueError:
                    pass

            if fname in SCORED_FIELDS:
                field_vals.setdefault(doc_idx, {})[fname] = row["expected_value"].strip()
                field_notes.setdefault(doc_idx, {})[fname] = row.get("notes", "").strip()

    result = []
    for doc_idx in sorted(field_vals):
        if doc_idx not in page_ranges:
            continue
        start, end, raw = page_ranges[doc_idx]
        result.append(GTDocument(
            doc_index=doc_idx,
            start_page=start,
            end_page=end,
            raw_pages=raw,
            fields=field_vals[doc_idx],
            field_notes=field_notes[doc_idx],
        ))
    return result


# ── Main evaluator ────────────────────────────────────────────────────────────

def evaluate_p4(csv_path: str, full_pipeline_path: str) -> P4EvalSummary:
    """Compare P4 extracted fields against ground-truth CSV."""
    gt_docs = load_p4_ground_truth(csv_path)

    with open(full_pipeline_path, encoding="utf-8") as f:
        fp = json.load(f)
    extracted = fp.get("documents", [])
    input_file = fp.get("input_file", "")

    doc_results: list[DocP4Result] = []
    for gt in gt_docs:
        best = _find_best_doc(gt.start_page, gt.end_page, extracted)

        matched_pages = None
        if best:
            sp, ep = best.get("start_page"), best.get("end_page")
            matched_pages = str(sp) if sp == ep else f"{sp}-{ep}"

        dr = DocP4Result(doc_index=gt.doc_index, gt_pages=gt.raw_pages,
                         matched_pages=matched_pages)
        for fname, expected in gt.fields.items():
            pred = None
            if best:
                raw = best.get(fname)
                if raw is not None:
                    pred = str(raw) if not isinstance(raw, str) else raw
                    if not pred.strip():
                        pred = None
            notes = gt.field_notes.get(fname, "")
            dr.field_results.append(_score_field(fname, pred, expected, notes))
        doc_results.append(dr)

    # Aggregate per-field stats
    fstats: dict[str, dict] = {}
    for dr in doc_results:
        for fr in dr.field_results:
            s = fstats.setdefault(fr.field, {"total": 0, "correct": 0, "wrong": 0, "missing": 0})
            s["total"] += 1
            s[fr.status] += 1

    for s in fstats.values():
        found = s["correct"] + s["wrong"]
        s["presence_pct"] = round(100 * found / s["total"], 1) if s["total"] else 0.0
        s["accuracy_pct"] = round(100 * s["correct"] / s["total"], 1) if s["total"] else 0.0

    overall_total   = sum(s["total"]   for s in fstats.values())
    overall_correct = sum(s["correct"] for s in fstats.values())
    overall_found   = sum(s["correct"] + s["wrong"] for s in fstats.values())
    unmatched       = sum(1 for dr in doc_results if dr.matched_pages is None)

    return P4EvalSummary(
        input_file=input_file,
        total_gt_docs=len(gt_docs),
        unmatched_gt_docs=unmatched,
        doc_results=doc_results,
        field_stats=fstats,
        overall_total=overall_total,
        overall_correct=overall_correct,
        overall_found=overall_found,
    )


# ── Report formatter ──────────────────────────────────────────────────────────

def format_p4_report(summary: P4EvalSummary) -> str:
    pres = f"{100*summary.overall_found/summary.overall_total:.1f}%" if summary.overall_total else "—"
    acc  = f"{100*summary.overall_correct/summary.overall_total:.1f}%" if summary.overall_total else "—"

    lines = [
        "=" * 72,
        f"  P4 Extraction Evaluation — {summary.input_file}",
        "=" * 72,
        f"  GT documents with scored fields : {summary.total_gt_docs}",
        f"  Unmatched GT docs               : {summary.unmatched_gt_docs}",
        f"  Total fields scored             : {summary.overall_total}",
        f"  Overall field presence          : {summary.overall_found}/{summary.overall_total}  ({pres})",
        f"  Overall field accuracy          : {summary.overall_correct}/{summary.overall_total}  ({acc})",
        "",
        f"  {'Field':<20}  {'GT':>4}  {'Found':>5}  {'Correct':>7}  {'Presence':>9}  {'Accuracy':>9}",
        "  " + "-" * 60,
    ]

    field_order = ["document_date", "document_title", "serial_number",
                   "total_hours", "total_landings", "total_cycles"]
    for fname in field_order:
        if fname not in summary.field_stats:
            continue
        s = summary.field_stats[fname]
        found = s["correct"] + s["wrong"]
        lines.append(
            f"  {fname:<20}  {s['total']:>4}  {found:>5}  {s['correct']:>7}  "
            f"{s['presence_pct']:>8.1f}%  {s['accuracy_pct']:>8.1f}%"
        )
    lines.append("=" * 72)
    lines.append("")
    return "\n".join(lines)


# ── P3 boundary evaluator ────────────────────────────────────────────────────

@dataclass
class P3DocResult:
    doc_index: int
    gt_pages: str
    gt_start: int
    gt_end: int
    pred_pages: Optional[str]   # None = no overlapping P3 document found
    pred_start: Optional[int]
    pred_end: Optional[int]
    boundary_status: str        # 'exact' | 'off_by_1' | 'off_by_2plus' | 'unmatched'


@dataclass
class P3EvalSummary:
    input_file: str
    total_gt_docs: int
    exact: int
    off_by_1: int
    off_by_2plus: int
    unmatched: int
    doc_results: list[P3DocResult]

    @property
    def exact_pct(self) -> float:
        return 100 * self.exact / self.total_gt_docs if self.total_gt_docs else 0.0

    @property
    def within_1_pct(self) -> float:
        return 100 * (self.exact + self.off_by_1) / self.total_gt_docs if self.total_gt_docs else 0.0


def _load_all_gt_docs(csv_path: str) -> list[tuple[int, int, int, str]]:
    """Load ALL document_type rows as (doc_index, start, end, raw_pages)."""
    rows = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["field"].strip() != "document_type":
                continue
            idx = int(row["doc_index"])
            raw = row["pages"].strip()
            try:
                start, end = _parse_page_range(raw)
                rows[idx] = (idx, start, end, raw)
            except ValueError:
                pass
    return [rows[k] for k in sorted(rows)]


def evaluate_p3(csv_path: str, full_pipeline_path: str) -> P3EvalSummary:
    """
    Score P3 document-splitting boundary accuracy against ground truth.

    For each GT document (from document_type rows), finds the P3-extracted
    document with the greatest page overlap and classifies the boundary match:
      exact       — start and end pages both match exactly
      off_by_1    — max boundary deviation is 1 page
      off_by_2plus — max boundary deviation is 2+ pages
      unmatched   — no P3 document overlaps this GT document at all
    """
    gt_docs = _load_all_gt_docs(csv_path)

    with open(full_pipeline_path, encoding="utf-8") as f:
        fp = json.load(f)
    extracted = fp.get("documents", [])
    input_file = fp.get("input_file", "")

    exact = off1 = off2 = unmatched = 0
    doc_results: list[P3DocResult] = []

    for idx, gt_start, gt_end, raw in gt_docs:
        best = _find_best_doc(gt_start, gt_end, extracted)

        if not best:
            unmatched += 1
            doc_results.append(P3DocResult(
                doc_index=idx, gt_pages=raw,
                gt_start=gt_start, gt_end=gt_end,
                pred_pages=None, pred_start=None, pred_end=None,
                boundary_status="unmatched",
            ))
            continue

        ps, pe = best["start_page"], best["end_page"]
        pred_str = str(ps) if ps == pe else f"{ps}-{pe}"
        deviation = max(abs(ps - gt_start), abs(pe - gt_end))

        if deviation == 0:
            status = "exact"; exact += 1
        elif deviation == 1:
            status = "off_by_1"; off1 += 1
        else:
            status = "off_by_2plus"; off2 += 1

        doc_results.append(P3DocResult(
            doc_index=idx, gt_pages=raw,
            gt_start=gt_start, gt_end=gt_end,
            pred_pages=pred_str, pred_start=ps, pred_end=pe,
            boundary_status=status,
        ))

    return P3EvalSummary(
        input_file=input_file,
        total_gt_docs=len(gt_docs),
        exact=exact, off_by_1=off1, off_by_2plus=off2, unmatched=unmatched,
        doc_results=doc_results,
    )


def format_p3_report(summary: P3EvalSummary) -> str:
    lines = [
        "=" * 72,
        f"  P3 Splitting Evaluation — {summary.input_file}",
        "=" * 72,
        f"  GT documents        : {summary.total_gt_docs}",
        f"  Exact boundary      : {summary.exact:>4}  ({summary.exact_pct:.1f}%)",
        f"  Off by ≤1 page      : {summary.off_by_1:>4}  ({100*summary.off_by_1/summary.total_gt_docs:.1f}%)" if summary.total_gt_docs else "",
        f"  Off by 2+ pages     : {summary.off_by_2plus:>4}  ({100*summary.off_by_2plus/summary.total_gt_docs:.1f}%)" if summary.total_gt_docs else "",
        f"  Unmatched           : {summary.unmatched:>4}  ({100*summary.unmatched/summary.total_gt_docs:.1f}%)" if summary.total_gt_docs else "",
        f"  Within ≤1 page      : {summary.exact+summary.off_by_1:>4}  ({summary.within_1_pct:.1f}%)",
        "=" * 72,
        "",
    ]
    return "\n".join(l for l in lines if l is not None)


def p3_summary_to_dict(summary: P3EvalSummary) -> dict:
    return {
        "input_file": summary.input_file,
        "total_gt_docs": summary.total_gt_docs,
        "exact": summary.exact,
        "off_by_1": summary.off_by_1,
        "off_by_2plus": summary.off_by_2plus,
        "unmatched": summary.unmatched,
        "exact_pct": round(summary.exact_pct, 2),
        "within_1_pct": round(summary.within_1_pct, 2),
        "doc_results": [
            {
                "doc_index": dr.doc_index,
                "gt_pages": dr.gt_pages,
                "pred_pages": dr.pred_pages,
                "boundary_status": dr.boundary_status,
            }
            for dr in summary.doc_results
        ],
    }


# ── JSON serialiser ───────────────────────────────────────────────────────────

def summary_to_dict(summary: P4EvalSummary) -> dict:
    return {
        "input_file": summary.input_file,
        "total_gt_docs": summary.total_gt_docs,
        "unmatched_gt_docs": summary.unmatched_gt_docs,
        "overall": {
            "total_fields": summary.overall_total,
            "found":        summary.overall_found,
            "correct":      summary.overall_correct,
            "presence_pct": round(100 * summary.overall_found   / summary.overall_total, 2) if summary.overall_total else 0,
            "accuracy_pct": round(100 * summary.overall_correct / summary.overall_total, 2) if summary.overall_total else 0,
        },
        "field_stats": summary.field_stats,
        "doc_results": [
            {
                "doc_index":    dr.doc_index,
                "gt_pages":     dr.gt_pages,
                "matched_pages": dr.matched_pages,
                "fields": [
                    {
                        "field":     fr.field,
                        "expected":  fr.expected,
                        "predicted": fr.predicted,
                        "status":    fr.status,
                    }
                    for fr in dr.field_results
                ],
            }
            for dr in summary.doc_results
        ],
    }
