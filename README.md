# IDP Document Zone Classifier

A 4-pass Python CLI pipeline that classifies aviation maintenance document pages into zones, splits zones into individual documents, and extracts structured metadata — powered by **Amazon Nova 2 Lite** via Amazon Bedrock.

---

## What It Does

Aviation maintenance packages are large PDFs bundling dozens of different documents together. This pipeline:

1. **P1 — Zone Classification**: Labels contiguous page ranges with a document type (e.g. "pages 3–8 are an Authorized Release Certificate")
2. **P2 — Post-processing**: Resolves overlaps, fills gaps, splits `[STANDALONE_PAGE]`-tagged pages into their own zones (deterministic, no LLM)
3. **P3 — Document Splitting**: Within each zone, identifies individual document boundaries
4. **P4 — Metadata Extraction**: Extracts structured fields per document (date, registration, serial, hours, landings, cycles, signatures, tasks)

---

## The 14 Document Types

| # | Type | Key Signals |
|---|------|-------------|
| 1 | Logbook Entry | Formal logbook header or A&P/IA certification statement |
| 2 | Authorized Release Certificate | FAA Form 8130-3 or EASA Form 1 header |
| 3 | Work Card | CAMP/MyCMP branding, FREQUENCY / ACCOMPLISHED / NEXT DUE fields |
| 4 | Status Report | Tabular AD/SB compliance tracking list |
| 5 | Airworthiness Directive | 14 CFR Part 39 reference, docket number |
| 6 | Service Bulletin | Manufacturer SB primary header |
| 7 | Major Repair and Alteration | FAA Form 337, OMB No. 2120-0020 |
| 8 | Supplemental Type Certificate | FAA Form 8110-2, STC number |
| 9 | Statement of Compliance | DER designation number (DERT-XXXXXX-XX) |
| 10 | Instructions for Continued Airworthiness | STC maintenance supplement |
| 11 | Aircraft Flight Manual Supplement | FAA-approved cockpit manual supplement |
| 12 | Certificate of Aircraft Registration | FAA Form AC 8050-3 |
| 13 | Standard Airworthiness Certificate | FAA Form 8100-2, always single page |
| 14 | Other | Packing slips, work order details, blank pages, indexes |

---

## Project Structure

```
idp-classifier/
├── main.py                # CLI entry point — classify, batch, report, evaluate, evaluate-p4
├── config.py              # Model ID, pricing, temperature, chunk size
├── models.py              # Pydantic v2 data models (Zone, DocumentResult, TokenUsage)
├── prompts.py             # P1 system prompt + user prompt builder
├── classifier.py          # Bedrock calls + post-processing (resolve, fill, fix-boundaries)
├── chunker.py             # Page parsing and chunking
├── converter.py           # Textract JSON → text + hint injection system
├── evaluator.py           # P1/P2 ground-truth comparison and zone accuracy scoring
├── p4_evaluator.py        # P4 field extraction scoring (date, title, serial, hours, etc.)
├── pipeline.py            # P3 (zone split) + P4 (metadata extraction) orchestrator
├── extraction_models.py   # Pydantic models for P3/P4 output
├── extraction_prompts.py  # P3 and P4 prompts per document type
├── reextract.py           # Re-run P3+P4 on existing P1/P2 results (no re-classification)
├── reporter.py            # Per-document PDF report
├── master_reporter.py     # Master accuracy + cost + charts PDF report
├── batch_reporter.py      # Batch evaluation PDF
├── combined_reporter.py   # OCR vs. text comparison PDF
├── cost_report.py         # Cost analysis PDF with caching projections
├── charts.py              # Lollipop, radar, and slope chart generators
├── solution_guide.py      # Plain-language solution guide PDF generator
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Prerequisites

- Python 3.12+
- AWS account with **Amazon Bedrock** access
- Nova 2 Lite model enabled in `us-east-1`
- IAM user or role with `bedrock:InvokeModel` permission

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your AWS credentials
```

`.env`:
```env
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=your_secret_key_here
```

> **Note:** Nova 2 Lite must be invoked via the cross-region inference profile `us.amazon.nova-2-lite-v1:0`. This is set in `config.py`.

---

## Commands

### `classify` — single document

```bash
python main.py classify \
  --input  path/to/ocr.json \
  --output results/output.json \
  --pdf    results/report.pdf \
  --groundtruth path/to/ac.csv \
  --fix-boundaries \
  --full-pipeline
```

| Option | Default | Description |
|--------|---------|-------------|
| `--input` | required | `.json` (Textract) or `.txt` (L-block rendered text) |
| `--output` | required | Path for the output JSON |
| `--pdf` | — | Generate a per-document PDF report |
| `--groundtruth` | — | Ground-truth CSV for P1/P2 accuracy evaluation |
| `--region` | `us-east-1` | AWS region |
| `--max-pages` | `300` | Pages per LLM chunk |
| `--fix-boundaries` | off | **Recommended.** Post-process to split `[STANDALONE_PAGE]` zones deterministically |
| `--full-pipeline` | off | Run P3 + P4 after zone classification |

---

### `batch` — multiple documents

```bash
# Pass all folders via glob expansion
FOLDERS=(/path/to/docs/*)
python main.py batch "${FOLDERS[@]}" \
  --output-dir ./results \
  --fix-boundaries \
  --pdf \
  --full-pipeline \
  --master-report ./MASTER_REPORT.pdf
```

Each folder must contain an input file (`ocr.json` preferred, falls back to any `.txt`) and optionally an `ac.csv` ground-truth file.

| Option | Description |
|--------|-------------|
| `--output-dir` | Required. Directory for all results. |
| `--fix-boundaries` | **Recommended.** Deterministic standalone-page splitting. |
| `--pdf` | Generate a PDF report per document. |
| `--full-pipeline` | Run P3 + P4 per document. |
| `--input-file` | Override input filename (e.g. `rendered_text.txt`). |
| `--master-report` | Path to auto-generate a master PDF after the batch. |
| `--compare-with` | Second batch output dir for side-by-side comparison. |

---

### `report` — master PDF from existing batch outputs

```bash
python main.py report \
  --ocr-dir  ./results/ocr_run \
  --txt-dir  ./results/txt_run \
  --output   ./MASTER_REPORT.pdf
```

---

### `evaluate` — offline P1/P2 zone accuracy

```bash
python main.py evaluate \
  --results     results/output.json \
  --groundtruth path/to/ac.csv
```

Compares zone classifications against ground truth. No API calls.

---

### `evaluate-p4` — offline P4 field extraction accuracy

```bash
python main.py evaluate-p4 \
  --full-pipeline path/to/full_pipeline.json \
  --groundtruth   path/to/ac.csv
```

Scores extracted fields (date, title, serial, hours, landings, cycles) against ground truth. Writes `p4_eval.json`.

---

### `reextract` — re-run P3+P4 without repeating P1/P2

```bash
python reextract.py \
  --input-dir  ./results/previous_batch \
  --docs-dir   /path/to/original/docs \
  --output-dir ./results/new_extraction
```

Reads existing `output.json` (P1/P2 zones) and re-runs only P3+P4 with current prompts. Use this to iterate on extraction prompts without re-spending P1/P2 classification costs.

---

## Input Formats

**Textract JSON** (`ocr.json`):
```json
[
  {
    "page_no": 1,
    "blocks": [
      {"block_type": "L", "text": "AUTHORIZED RELEASE CERTIFICATE",
       "confidence": 99.1, "geometry": {"x": 0.1, "y": 0.05, "w": 0.8, "h": 0.02}}
    ]
  }
]
```

**L-block text** (`rendered_text.txt`): Pre-rendered page text with `## PAGE N ##` headers. Hints are re-computed and merged at runtime.

---

## Output Formats

### Zone classification (`output.json`)
```json
{
  "input_file": "ocr.json",
  "total_pages": 14,
  "total_chunks": 1,
  "zones": [
    {"document_type": "Logbook Entry", "confidence": 0.95, "start_page": 1, "end_page": 2, "alternatives": []},
    {"document_type": "Authorized Release Certificate", "confidence": 0.99, "start_page": 3, "end_page": 3, "alternatives": []}
  ],
  "token_usage": {"input_tokens": 42000, "output_tokens": 210, "cost_usd": 0.0139}
}
```

### Full pipeline (`full_pipeline.json`)
```json
{
  "input_file": "ocr.json",
  "total_pages": 14,
  "total_documents": 3,
  "p3_tokens": {"input": 12000, "output": 180},
  "p4_tokens": {"input": 9500, "output": 620},
  "documents": [
    {
      "document_type": "Logbook Entry",
      "start_page": 1, "end_page": 1,
      "document_date": "2023-04-15",
      "registration_number": "N515LT",
      "serial_number": "49009",
      "total_hours": "4821.3",
      "total_landings": "1253",
      "total_cycles": null,
      "signatures": [{"full_name": "John Smith", "certificate_number": "A&P 1234567"}],
      "tasks": [{"task_description": "Annual inspection completed"}]
    }
  ]
}
```

### Ground-truth CSV (`ac.csv`)
```csv
file_id,doc_index,pages,field,expected_value,notes
130923,0,1+2,document_type,Other,
130923,0,1+2,document_date,2024-12-12,
130923,0,1+2,document_title,M-100 Return to service checklist,also acceptable: Return to Service Checklist
130923,1,3,document_type,Logbook Entry,
```

`pages` supports: single (`9`), range (`4-8`), plus-list (`1+2`).  
`notes` supports `also acceptable: X, Y` for alternative valid values.

---

## The Hint System

Before each P1/P2 LLM call, deterministic regex detectors scan each page and inject bracketed tokens:

| Hint | Signal |
|------|--------|
| `[form:8130]` | ARC document header — strong ARC signal |
| `[form:8050]` | Work Card CAMP/MyCMP branding |
| `[RETURN_TO_SERVICE]` | Return-to-service certification language |
| `[LBE]` | Explicit logbook entry header |
| `[RTS_CHECKLIST]` | Operator RTS checklist (suppresses LBE/RTS signals) |
| `[STATUS_REPORT]` | AD/SB compliance tracking table |
| `[AIRWORTHINESS_DIRECTIVE]` | AD docket number in first lines |
| `[SERVICE_BULLETIN]` | Manufacturer SB primary header |
| `[STANDALONE_PAGE]` | Page must start a new zone — triggered by `page 1 of 1` pagination |
| `[vendor]` | Packing slip, shipping invoice, or vendor certificate |
| `[BLANK]` | Near-blank page (avg OCR confidence < 30%) |

---

## Why `--fix-boundaries`

The P1/P2 LLM occasionally merges adjacent same-type pages into one large zone even when each is a separate document (e.g. 60 individual logbook entries merged into one zone). `--fix-boundaries` post-processes deterministically: any zone containing `[STANDALONE_PAGE]` pages is split so each tagged page becomes its own zone. This raised exact zone accuracy from 17.7% to **61.4%** on the 65-document benchmark with no additional LLM calls.

---

## Measured Accuracy (65 documents, 3,681 pages)

### P1/P2 Zone Classification
| Metric | Result |
|--------|--------|
| Exact accuracy (type + boundaries) | **61.2%** |
| Documents at 100% type accuracy | 46/65 |
| Documents ≥ 90% type accuracy | 49/65 |

### P4 Field Extraction (1,464 GT fields)

| Field | Accuracy | Presence |
|-------|------:|------:|
| `document_date` | **89.5%** | 97.9% |
| `document_title` | **83.4%** | 99.3% |
| `serial_number` | **83.3%** | 88.9% |
| `total_hours` | **76.5%** | 82.4% |
| `total_landings` | **60.4%** | 68.8% |
| `total_cycles` | **100.0%** | 100.0% |
| **Overall** | **85.6%** | 96.6% |

---

## Cost (65 documents, 3,681 pages, full 4-pass pipeline)

| Pass | LLM Calls | Input Tokens | Output Tokens | Cost |
|------|----------:|-------------:|--------------:|-----:|
| P1 Zone Classification | 66 | 2,268,945 | 18,634 | $0.73 |
| P2 Post-processing | 0 | — | — | — |
| P3 Zone Split | 168 | 1,003,700 | 34,814 | $0.39 |
| P4 Doc Extraction | 2,346 | 4,072,052 | 867,506 | $3.39 |
| **Total** | **2,580** | **7,344,697** | **920,954** | **~$4.51** |

**$0.069 per document** · **$0.0012 per page** · ~40 LLM calls per document

---

## Configuration (`config.py`)

```python
MODEL_ID               = "us.amazon.nova-2-lite-v1:0"  # cross-region inference profile
MAX_TOKENS             = 5120
TEMPERATURE            = 0                              # fully deterministic
MAX_PAGES_PER_CHUNK    = 300
AWS_REGION             = "us-east-1"

PRICE_INPUT_PER_1M     = 0.30    # $ per 1M input tokens
PRICE_OUTPUT_PER_1M    = 2.50    # $ per 1M output tokens
PRICE_CACHE_WRITE_PER_1M = 0.375
PRICE_CACHE_READ_PER_1M  = 0.030
```
