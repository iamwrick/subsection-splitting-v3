"""
Re-run P3 + P4 extraction on already-classified documents.

Reads existing output.json (P1/P2 zone results) and the original ocr.json,
then re-runs P3 (split) and P4 (extract) with current prompts.
Writes full_pipeline.json and p4_eval.json to --output-dir.

Usage:
  python reextract.py \
    --input-dir  /path/to/previous/batch   \  # must contain <folder>/output.json
    --docs-dir   /path/to/original/docs    \  # must contain <folder>/ocr.json and ac.csv
    --output-dir /path/to/new/output
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3
import click
from dotenv import load_dotenv

from chunker import parse_pages
from config import MODEL_ID, AWS_REGION
from converter import enrich_txt_hints, json_to_text
from models import DocumentResult, TokenUsage
from p4_evaluator import evaluate_p4, format_p4_report, summary_to_dict as p4_dict
from pipeline import run_p3_p4

load_dotenv()


def _make_bedrock_client(region: str):
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
    )


@click.command()
@click.option("--input-dir",  "input_dir",  required=True,
              help="Batch output dir with <folder>/output.json (P1/P2 results).")
@click.option("--docs-dir",   "docs_dir",   required=True,
              help="Original documents dir with <folder>/ocr.json and ac.csv.")
@click.option("--output-dir", "output_dir", required=True,
              help="Where to write new full_pipeline.json and p4_eval.json.")
@click.option("--region",     default=AWS_REGION, show_default=True)
@click.option("--only",       default=None,
              help="Comma-separated folder IDs to process (default: all).")
def reextract(input_dir: str, docs_dir: str, output_dir: str,
              region: str, only: str | None):
    """Re-run P3+P4 extraction with current prompts on existing zone classifications."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    bedrock = _make_bedrock_client(region)

    only_set = {x.strip() for x in only.split(",")} if only else None

    folders = sorted(
        f for f in os.listdir(input_dir)
        if os.path.isdir(os.path.join(input_dir, f))
        and os.path.isfile(os.path.join(input_dir, f, "output.json"))
    )
    if only_set:
        folders = [f for f in folders if f in only_set]

    total = len(folders)
    total_p3_in = total_p3_out = total_p4_in = total_p4_out = 0
    total_start = time.time()

    p4_rows = []

    for i, folder_id in enumerate(folders, 1):
        out_path  = os.path.join(input_dir, folder_id, "output.json")
        ocr_candidates = [
            os.path.join(docs_dir, folder_id, "ocr.json"),
            os.path.join(input_dir, folder_id, "ocr.json"),
        ]
        gt_path = os.path.join(docs_dir, folder_id, "ac.csv")

        ocr_file = next((p for p in ocr_candidates if os.path.isfile(p)), None)
        if not ocr_file:
            click.echo(f"[{i}/{total}] {folder_id}: SKIP — no ocr.json found")
            continue

        click.echo(f"\n[{i}/{total}] {folder_id}")

        with open(out_path, encoding="utf-8") as f:
            doc_result = DocumentResult.model_validate(json.load(f))

        if ocr_file.lower().endswith(".json"):
            raw_text = json_to_text(ocr_file)
        else:
            with open(ocr_file, encoding="utf-8") as f:
                raw_text = f.read()
            raw_text = enrich_txt_hints(raw_text)

        pages = parse_pages(raw_text)

        t0 = time.time()
        try:
            package = run_p3_p4(bedrock, doc_result, pages, verbose=True)
        except Exception as exc:
            click.echo(f"  ERROR: {exc}", err=True)
            continue

        elapsed = time.time() - t0
        total_p3_in  += package.p3_input_tokens
        total_p3_out += package.p3_output_tokens
        total_p4_in  += package.p4_input_tokens
        total_p4_out += package.p4_output_tokens

        # Write full_pipeline.json
        out_folder = Path(output_dir) / folder_id
        out_folder.mkdir(parents=True, exist_ok=True)
        fp_path = str(out_folder / "full_pipeline.json")
        with open(fp_path, "w", encoding="utf-8") as f:
            json.dump({
                "input_file":      package.input_file,
                "total_pages":     package.total_pages,
                "total_documents": len(package.documents),
                "p3_tokens": {"input": package.p3_input_tokens,
                              "output": package.p3_output_tokens},
                "p4_tokens": {"input": package.p4_input_tokens,
                              "output": package.p4_output_tokens},
                "zones":     [z.model_dump() for z in package.zones],
                "documents": [d.model_dump() for d in package.documents],
                "page_map":  {str(k): v for k, v in package.page_map.items()},
            }, f, indent=2)

        click.echo(f"  {len(package.documents)} docs  "
                   f"[P3: {package.p3_input_tokens:,}/{package.p3_output_tokens:,}  "
                   f"P4: {package.p4_input_tokens:,}/{package.p4_output_tokens:,}]  "
                   f"→ {fp_path}  ({elapsed:.0f}s)")

        # P4 evaluation if ground truth exists
        if os.path.isfile(gt_path):
            try:
                p4_summary = evaluate_p4(gt_path, fp_path)
                click.echo(format_p4_report(p4_summary))
                p4_eval_path = str(out_folder / "p4_eval.json")
                with open(p4_eval_path, "w", encoding="utf-8") as f:
                    json.dump(p4_dict(p4_summary), f, indent=2)
                p4_rows.append({
                    "folder": folder_id,
                    "total_gt_docs": p4_summary.total_gt_docs,
                    "overall_total":   p4_summary.overall_total,
                    "overall_found":   p4_summary.overall_found,
                    "overall_correct": p4_summary.overall_correct,
                    "presence_pct": round(100 * p4_summary.overall_found   / p4_summary.overall_total, 1) if p4_summary.overall_total else 0,
                    "accuracy_pct": round(100 * p4_summary.overall_correct / p4_summary.overall_total, 1) if p4_summary.overall_total else 0,
                    "field_stats": p4_summary.field_stats,
                })
            except Exception as exc:
                click.echo(f"  P4 eval error: {exc}", err=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed_total = time.time() - total_start
    click.echo(f"\n{'='*60}")
    click.echo("  RE-EXTRACTION SUMMARY")
    click.echo(f"{'='*60}")
    click.echo(f"  Documents processed : {len(p4_rows)}/{total}")
    click.echo(f"  P3 tokens           : {total_p3_in:,} in / {total_p3_out:,} out")
    click.echo(f"  P4 tokens           : {total_p4_in:,} in / {total_p4_out:,} out")
    click.echo(f"  Total elapsed       : {elapsed_total:.0f}s")

    if p4_rows:
        agg = {}
        for row in p4_rows:
            for fname, fs in row["field_stats"].items():
                s = agg.setdefault(fname, {"total": 0, "correct": 0, "wrong": 0, "missing": 0})
                for k in ("total", "correct", "wrong", "missing"):
                    s[k] += fs[k]

        click.echo(f"\n  {'Field':<20}  {'GT':>4}  {'Found':>5}  {'Correct':>7}  {'Presence':>9}  {'Accuracy':>9}")
        click.echo("  " + "-" * 60)
        order = ["document_date", "document_title", "serial_number",
                 "total_hours", "total_landings", "total_cycles"]
        grand_total = grand_correct = grand_found = 0
        for fname in order:
            if fname not in agg or agg[fname]["total"] == 0:
                continue
            s = agg[fname]
            found = s["correct"] + s["wrong"]
            grand_total   += s["total"]
            grand_correct += s["correct"]
            grand_found   += found
            click.echo(f"  {fname:<20}  {s['total']:>4}  {found:>5}  {s['correct']:>7}"
                       f"  {100*found/s['total']:>8.1f}%  {100*s['correct']/s['total']:>8.1f}%")
        click.echo("  " + "-" * 60)
        click.echo(f"  {'OVERALL':<20}  {grand_total:>4}  {grand_found:>5}  {grand_correct:>7}"
                   f"  {100*grand_found/grand_total:>8.1f}%  {100*grand_correct/grand_total:>8.1f}%")

    # Write summary JSON
    summary_path = Path(output_dir) / "p4_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(p4_rows, f, indent=2)
    click.echo(f"\n  P4 summary saved to {summary_path}")


if __name__ == "__main__":
    reextract()
