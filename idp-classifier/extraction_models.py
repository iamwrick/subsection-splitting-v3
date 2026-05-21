"""Pydantic models for P3 split and P4 extraction outputs."""

from typing import Optional
from pydantic import BaseModel, field_validator
import sys


# ── P3 Split ──────────────────────────────────────────────────────────────────

class SplitDocument(BaseModel):
    start_page: int
    end_page: int
    document_title: Optional[str] = None
    boundary_reason: Optional[str] = None
    document_type: Optional[str] = None   # may refine the zone type


class ZoneSplit(BaseModel):
    documents: list[SplitDocument]


# ── P4 Extraction — shared sub-models ────────────────────────────────────────

class Task(BaseModel):
    task_description: Optional[str] = None
    part_number_removed: Optional[str] = None
    serial_number_removed: Optional[str] = None
    part_number_installed: Optional[str] = None
    serial_number_installed: Optional[str] = None
    compliance_status: Optional[str] = None
    reference: Optional[str] = None


class Signature(BaseModel):
    full_name: Optional[str] = None
    title: Optional[str] = None
    certificate_number: Optional[str] = None   # A&P / IA / CRS
    date_of_signature: Optional[str] = None


class Equipment(BaseModel):
    make_model: Optional[str] = None
    serial_number: Optional[str] = None
    total_hours: Optional[str] = None
    total_landings: Optional[str] = None
    total_cycles: Optional[str] = None


# ── P4 Extraction — per-document result ──────────────────────────────────────

class ExtractedDocument(BaseModel):
    """Unified extraction result for any document type."""
    document_type: str
    start_page: int
    end_page: int
    document_date: Optional[str] = None          # YYYY-MM-DD
    document_date_raw: Optional[str] = None       # as-written on page
    document_title: Optional[str] = None
    date_confidence: Optional[str] = None         # high / medium / low

    # Aircraft / registration
    registration_number: Optional[str] = None     # N-number
    serial_number: Optional[str] = None           # aircraft or component S/N
    work_order_number: Optional[str] = None
    make_model: Optional[str] = None

    # Aircraft hours (Logbook Entry)
    total_hours: Optional[str] = None
    total_landings: Optional[str] = None
    total_cycles: Optional[str] = None

    # Part / component (ARC / Work Card)
    part_number: Optional[str] = None
    part_description: Optional[str] = None
    quantity: Optional[str] = None
    status_condition: Optional[str] = None       # NEW / REPAIRED / OVERHAULED
    approval_reference: Optional[str] = None     # CRS / DER number

    # Regulatory (AD / SB)
    regulatory_reference: Optional[str] = None   # docket number, SB number

    # Structured sub-data
    tasks: list[Task] = []
    signatures: list[Signature] = []
    equipment: list[Equipment] = []

    # Summary
    summary: Optional[str] = None

    # Raw model output preserved for debugging
    _raw: Optional[str] = None


# ── Full pipeline output ──────────────────────────────────────────────────────

class DocumentPackage(BaseModel):
    """Complete 4-pass pipeline result for one input file."""
    input_file: str
    total_pages: int

    # P1+P2: zone classification
    zones: list  # list[Zone] from models.py — kept as Any to avoid circular import

    # P3+P4: document-level extraction
    documents: list[ExtractedDocument] = []

    # Page→document mapping (page_no → document index in documents list)
    page_map: dict[int, int] = {}

    # Cost tracking
    p3_input_tokens: int = 0
    p3_output_tokens: int = 0
    p4_input_tokens: int = 0
    p4_output_tokens: int = 0
