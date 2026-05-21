import json
import os
from pathlib import Path
import sys
import time

import boto3
import click
from dotenv import load_dotenv

from chunker import build_chunks, parse_pages
from classifier import classify_chunk, consolidate_adjacent_zones, enforce_single_page_types, fill_gaps, fix_boundaries, merge_chunk_results, resolve_overlaps
from converter import enrich_txt_hints, json_to_text
from evaluator import compare, format_report, load_ground_truth, summarise
from models import ChunkResult, DocumentResult, TokenUsage
from p4_evaluator import (
    evaluate_p4, format_p4_report, summary_to_dict as p4_summary_to_dict,
    evaluate_p3, format_p3_report, p3_summary_to_dict,
)
from reporter import generate_pdf

load_dotenv()


def _make_bedrock_client(region: str):
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
    )


def _classify_document(
    input_path: str,
    output_path: str,
    bedrock,
    max_pages: int,
    run_fix_boundaries: bool,
    pdf_path: str | None = None,
) -> DocumentResult:
    """Core classification logic, shared by classify and batch commands."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if pdf_path:
        Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)

    if input_path.lower().endswith(".json"):
        raw_text = json_to_text(input_path)
    else:
        with open(input_path, encoding="utf-8") as f:
            raw_text = f.read()
        raw_text = enrich_txt_hints(raw_text)

    pages = parse_pages(raw_text)
    total_pages = len(pages)
    chunks = build_chunks(pages, max_pages=max_pages)
    n_chunks = len(chunks)

    click.echo(f"  Classifying {total_pages} pages across {n_chunks} chunks...")

    chunk_results: list[ChunkResult] = []
    doc_tokens = TokenUsage()
    for chunk in chunks:
        i = chunk["chunk_index"]
        start = chunk["start_page"]
        end = chunk["end_page"]
        try:
            result, usage = classify_chunk(bedrock, chunk["text"])
            doc_tokens = doc_tokens.add(usage)
            cr = ChunkResult(
                chunk_index=i,
                start_page=start,
                end_page=end,
                zones=result.zones,
            )
            chunk_results.append(cr)
            cache_info = ""
            if usage.cache_write_tokens:
                cache_info = f"  [cache WRITE {usage.cache_write_tokens:,}]"
            elif usage.cache_read_tokens:
                cache_info = f"  [cache HIT {usage.cache_read_tokens:,} tokens saved]"
            click.echo(
                f"    Chunk {i + 1}/{n_chunks}: pages {start}–{end} → {len(result.zones)} zones"
                f"  [{usage.input_tokens:,} in / {usage.output_tokens:,} out]{cache_info}"
            )
        except ValueError as exc:
            print(f"    Chunk {i + 1}/{n_chunks}: ERROR — {exc}", file=sys.stderr)

    zones = merge_chunk_results(chunk_results)
    zones = resolve_overlaps(zones)
    zones = enforce_single_page_types(zones)
    zones = fill_gaps(zones, pages)
    if run_fix_boundaries:
        zones = fix_boundaries(zones, pages)
    zones = consolidate_adjacent_zones(zones)

    doc_result = DocumentResult(
        input_file=os.path.basename(input_path),
        total_pages=total_pages,
        total_chunks=n_chunks,
        zones=zones,
        token_usage=doc_tokens,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(doc_result.model_dump_json(indent=2))

    click.echo(f"  Done — {len(zones)} zones written to {output_path}")

    if pdf_path:
        generate_pdf(doc_result, pdf_path)
        click.echo(f"  PDF report saved to {pdf_path}")

    return doc_result


@click.group()
def cli():
    pass


@cli.command()
@click.option("--input", "input_path", required=True, help="Path to input .txt or .json file.")
@click.option("--output", "output_path", required=True, help="Path for the output .json file.")
@click.option("--pdf", "pdf_path", default=None, help="Optional path to save a PDF report.")
@click.option("--groundtruth", "gt_path", default=None, help="Optional path to ground-truth CSV for evaluation.")
@click.option("--region", default="us-east-1", show_default=True, help="AWS region.")
@click.option("--max-pages", default=300, show_default=True, help="Max pages per chunk.")
@click.option("--fix-boundaries", "run_fix_boundaries", is_flag=True, default=False,
              help="Post-process zones to split out pages flagged [STANDALONE_PAGE].")
@click.option("--full-pipeline", "run_full_pipeline", is_flag=True, default=False,
              help="Run P3 (split) and P4 (extract) after zone classification. "
                   "Adds structured metadata extraction to the output.")
def classify(
    input_path: str,
    output_path: str,
    pdf_path: str | None,
    gt_path: str | None,
    region: str,
    max_pages: int,
    run_fix_boundaries: bool,
    run_full_pipeline: bool,
):
    """Classify a single document."""
    bedrock = _make_bedrock_client(region)
    doc_result = _classify_document(
        input_path, output_path, bedrock, max_pages, run_fix_boundaries, pdf_path
    )

    if run_full_pipeline:
        from pipeline import run_p3_p4
        from chunker import parse_pages
        from converter import enrich_txt_hints, json_to_text

        click.echo("Running P3 (split) + P4 (extract)...")
        if input_path.lower().endswith(".json"):
            raw_text = json_to_text(input_path)
        else:
            with open(input_path, encoding="utf-8") as f:
                raw_text = f.read()
            raw_text = enrich_txt_hints(raw_text)

        pages = parse_pages(raw_text)
        package = run_p3_p4(bedrock, doc_result, pages, verbose=True)

        # Write enriched output
        pkg_path = output_path.replace(".json", "_full_pipeline.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({
                "input_file":      package.input_file,
                "total_pages":     package.total_pages,
                "total_documents": len(package.documents),
                "p3_tokens": {"input": package.p3_input_tokens, "output": package.p3_output_tokens},
                "p4_tokens": {"input": package.p4_input_tokens, "output": package.p4_output_tokens},
                "zones":     [z.model_dump() for z in package.zones],
                "documents": [d.model_dump() for d in package.documents],
                "page_map":  {str(k): v for k, v in package.page_map.items()},
            }, f, indent=2)
        click.echo(f"Full pipeline output: {pkg_path}")
        click.echo(f"  {len(package.documents)} documents extracted  "
                   f"[P3: {package.p3_input_tokens:,}/{package.p3_output_tokens:,} tokens  "
                   f"P4: {package.p4_input_tokens:,}/{package.p4_output_tokens:,} tokens]")

        if gt_path:
            _run_p3_evaluation(pkg_path, gt_path)
            _run_p4_evaluation(pkg_path, gt_path)

    if gt_path:
        _run_evaluation(doc_result, gt_path, output_path)


@cli.command()
@click.argument("folders", nargs=-1, required=True)
@click.option("--output-dir", "output_dir", required=True,
              help="Directory to write all results, PDFs and evaluation files.")
@click.option("--region", default="us-east-1", show_default=True, help="AWS region.")
@click.option("--max-pages", default=300, show_default=True, help="Max pages per chunk.")
@click.option("--fix-boundaries", "run_fix_boundaries", is_flag=True, default=False,
              help="Post-process zones to split out pages flagged [STANDALONE_PAGE].")
@click.option("--pdf", "gen_pdf", is_flag=True, default=False,
              help="Generate a PDF report for each document.")
@click.option("--input-file", "input_filename", default=None,
              help="Filename to use as input inside each folder (e.g. rendered_text.txt). "
                   "Defaults to ocr.json, then any *.txt file.")
@click.option("--full-pipeline", "run_full_pipeline", is_flag=True, default=False,
              help="Run P3 (split) + P4 (extract) after zone classification per document.")
@click.option("--master-report", "master_report_path", default=None,
              help="Auto-generate a master PDF report after batch completes. "
                   "For a comparison report across two runs use the 'report' command.")
@click.option("--compare-with", "compare_dir", default=None,
              help="Path to a second batch output dir to include as a comparison column "
                   "in the master report (e.g. a text-input run alongside an OCR run).")
def batch(
    folders: tuple[str, ...],
    output_dir: str,
    region: str,
    max_pages: int,
    run_fix_boundaries: bool,
    gen_pdf: bool,
    input_filename: str | None,
    run_full_pipeline: bool,
    master_report_path: str | None,
    compare_dir: str | None,
):
    """
    Classify and evaluate multiple folders in one run.

    Each FOLDER must contain an input file and optionally a ground-truth CSV:
      ocr.json       — Textract JSON (preferred)
      *.txt          — L-block rendered text (used when ocr.json is absent)
      ac.csv         — ground-truth CSV (optional; skips evaluation if absent)

    Results, evaluation JSON and optional PDFs are written to OUTPUT_DIR/<folder_name>/.

    A consolidated summary table is printed at the end.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    bedrock = _make_bedrock_client(region)

    rows = []   # one row per folder for the summary table
    total_start = time.time()

    for folder_path in folders:
        folder = Path(folder_path)
        folder_id = folder.name

        # Resolve input file: explicit name > ocr.json > any *.txt
        if input_filename:
            ocr_file = folder / input_filename
            if not ocr_file.exists():
                click.echo(f"\n[{folder_id}] SKIP — {input_filename} not found in {folder}")
                continue
        else:
            ocr_file = folder / "ocr.json"
            if not ocr_file.exists():
                txt_candidates = sorted(folder.glob("*.txt"))
                if txt_candidates:
                    ocr_file = txt_candidates[0]
                    click.echo(f"\n[{folder_id}] No ocr.json — using {ocr_file.name}")
                else:
                    click.echo(f"\n[{folder_id}] SKIP — no ocr.json or .txt found in {folder}")
                    continue

        gt_file = folder / "ac.csv"
        has_gt  = gt_file.exists()

        click.echo(f"\n{'='*60}")
        click.echo(f"  [{folder_id}]  {ocr_file}")
        click.echo(f"{'='*60}")

        out_dir = Path(output_dir) / folder_id
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / "output.json")
        pdf_path    = str(out_dir / "report.pdf") if gen_pdf else None

        t0 = time.time()
        try:
            doc_result = _classify_document(
                str(ocr_file), output_path, bedrock,
                max_pages, run_fix_boundaries, pdf_path,
            )
        except Exception as exc:
            click.echo(f"  ERROR during classification: {exc}", err=True)
            rows.append({
                "folder": folder_id,
                "pages": "—",
                "gt_zones": "—",
                "exact": "ERROR",
                "type_acc": "ERROR",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0,
                "elapsed": f"{time.time() - t0:.0f}s",
            })
            continue

        # P3 + P4 full pipeline (optional)
        p3p4_tokens = TokenUsage()
        if run_full_pipeline:
            try:
                from pipeline import run_p3_p4
                from converter import enrich_txt_hints, json_to_text as _jtt
                from chunker import parse_pages as _pp

                if str(ocr_file).lower().endswith(".json"):
                    raw_text = _jtt(str(ocr_file))
                else:
                    with open(str(ocr_file), encoding="utf-8") as f:
                        raw_text = f.read()
                    raw_text = enrich_txt_hints(raw_text)

                pages_for_pipeline = _pp(raw_text)
                package = run_p3_p4(bedrock, doc_result, pages_for_pipeline,
                                    verbose=True)

                pkg_path = str(out_dir / "full_pipeline.json")
                with open(pkg_path, "w", encoding="utf-8") as f:
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

                p3p4_tokens = TokenUsage(
                    input_tokens=package.p3_input_tokens + package.p4_input_tokens,
                    output_tokens=package.p3_output_tokens + package.p4_output_tokens,
                )
                click.echo(f"  Full pipeline: {len(package.documents)} docs extracted"
                           f"  [P3+P4: {p3p4_tokens.input_tokens:,} in"
                           f" / {p3p4_tokens.output_tokens:,} out tokens]"
                           f"  → {pkg_path}")

                if has_gt:
                    _run_p3_evaluation(pkg_path, str(gt_file))
                    _run_p4_evaluation(pkg_path, str(gt_file))

            except Exception as exc:
                click.echo(f"  P3/P4 ERROR: {exc}", err=True)

        # Evaluate (only if ground-truth CSV exists)
        if has_gt:
            gt_zones = load_ground_truth(str(gt_file))
            matches  = compare(doc_result, gt_zones)
            summary  = summarise(matches)
            report   = format_report(summary, doc_result.input_file)
            click.echo("\n" + report)

            eval_path = str(out_dir / "eval.json")
            _write_eval_json(summary, doc_result, gt_file.name, eval_path)
            click.echo(f"  Evaluation saved to {eval_path}")

            rows.append({
                "folder":        folder_id,
                "pages":         str(doc_result.total_pages),
                "gt_zones":      str(summary.total_gt_zones),
                "exact":         f"{summary.exact_matches}/{summary.total_gt_zones}  ({summary.exact_accuracy*100:.1f}%)",
                "type_acc":      f"{summary.type_accuracy*100:.1f}%",
                "input_tokens":  doc_result.token_usage.input_tokens + p3p4_tokens.input_tokens,
                "output_tokens": doc_result.token_usage.output_tokens + p3p4_tokens.output_tokens,
                "cost_usd":      round(doc_result.token_usage.add(p3p4_tokens).cost_usd(), 6),
                "elapsed":       f"{time.time() - t0:.0f}s",
            })
        else:
            rows.append({
                "folder":        folder_id,
                "pages":         str(doc_result.total_pages),
                "gt_zones":      "—",
                "exact":         "—",
                "type_acc":      "—",
                "input_tokens":  doc_result.token_usage.input_tokens + p3p4_tokens.input_tokens,
                "output_tokens": doc_result.token_usage.output_tokens + p3p4_tokens.output_tokens,
                "cost_usd":      round(doc_result.token_usage.add(p3p4_tokens).cost_usd(), 6),
                "elapsed":       f"{time.time() - t0:.0f}s",
            })

    # ── Consolidated summary ──────────────────────────────────────────────
    elapsed_total = time.time() - total_start
    click.echo(f"\n{'='*60}")
    click.echo("  BATCH SUMMARY")
    click.echo(f"{'='*60}")
    _w = [12, 7, 9, 28, 10, 9]
    headers = ["Folder", "Pages", "GT Zones", "Exact matches", "Type acc.", "Time"]
    header_line = "  " + "  ".join(h.ljust(w) for h, w in zip(headers, _w))
    click.echo(header_line)
    click.echo("  " + "-" * (sum(_w) + 2 * len(_w)))
    for row in rows:
        line = "  " + "  ".join(str(row[k]).ljust(w) for k, w in zip(
            ["folder", "pages", "gt_zones", "exact", "type_acc", "elapsed"], _w
        ))
        click.echo(line)
    click.echo(f"\n  Total elapsed: {elapsed_total:.0f}s")

    # Write machine-readable batch summary
    summary_path = Path(output_dir) / "batch_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    click.echo(f"  Batch summary saved to {summary_path}")

    # ── Auto-generate master report ───────────────────────────────────────────
    if master_report_path:
        try:
            from master_reporter import generate_master_report
            rpt_path = master_report_path
            if not rpt_path.lower().endswith(".pdf"):
                rpt_path = rpt_path + ".pdf"
            Path(rpt_path).parent.mkdir(parents=True, exist_ok=True)
            # Use compare_dir as the "text" column if provided; otherwise self-compare
            generate_master_report(
                ocr_dir=output_dir,
                txt_dir=compare_dir or output_dir,
                pdf_path=rpt_path,
                txt_enriched_dir=None,
            )
            click.echo(f"  Master report saved to {rpt_path}")
        except Exception as exc:
            print(f"  WARNING: master report generation failed — {exc}", file=sys.stderr)


@cli.command()
@click.option("--ocr-dir",          "ocr_dir",  required=True,
              help="Batch output directory from the OCR (ocr.json) run.")
@click.option("--txt-dir",          "txt_dir",  required=True,
              help="Batch output directory from the text (rendered_text.txt) run.")
@click.option("--txt-enriched-dir", "txte_dir", default=None,
              help="Optional: output directory for a third run with enriched text hints.")
@click.option("--pipeline-dir",     "pipeline_dir", default=None,
              help="Optional: 4-pass pipeline output directory (for Nova vs Gemini section).")
@click.option("--gemini-dir",       "gemini_dir", default=None,
              help="Optional: Gemini BADASS output directory for comparison.")
@click.option("--output", "pdf_path", required=True,
              help="Where to write the master PDF report.")
def report(ocr_dir: str, txt_dir: str, txte_dir: str | None,
           pipeline_dir: str | None, gemini_dir: str | None, pdf_path: str):
    """Generate a master PDF combining accuracy, cost and prompt-caching analysis."""
    from master_reporter import generate_master_report
    generate_master_report(
        ocr_dir=ocr_dir,
        txt_dir=txt_dir,
        pdf_path=pdf_path,
        txt_enriched_dir=txte_dir,
        pipeline_dir=pipeline_dir,
        gemini_dir=gemini_dir,
    )
    click.echo(f"Master report saved to {pdf_path}")


@cli.command()
@click.option("--results", "results_path", required=True, help="Path to classifier output .json file.")
@click.option("--groundtruth", "gt_path", required=True, help="Path to ground-truth CSV.")
def evaluate(results_path: str, gt_path: str):
    """Compare a saved classifier output JSON against a ground-truth CSV."""
    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)
    doc_result = DocumentResult.model_validate(data)
    gt_zones = load_ground_truth(gt_path)
    matches  = compare(doc_result, gt_zones)
    summary  = summarise(matches)
    click.echo("\n" + format_report(summary, doc_result.input_file))
    eval_path = os.path.splitext(results_path)[0] + "_eval.json"
    _write_eval_json(summary, doc_result, os.path.basename(gt_path), eval_path)
    click.echo(f"Evaluation details saved to {eval_path}")


def _run_evaluation(doc_result: DocumentResult, gt_path: str, results_path: str) -> None:
    gt_zones = load_ground_truth(gt_path)
    matches  = compare(doc_result, gt_zones)
    summary  = summarise(matches)
    click.echo("\n" + format_report(summary, doc_result.input_file))
    eval_path = os.path.splitext(results_path)[0] + "_eval.json"
    Path(eval_path).parent.mkdir(parents=True, exist_ok=True)
    _write_eval_json(summary, doc_result, os.path.basename(gt_path), eval_path)
    click.echo(f"Evaluation details saved to {eval_path}")


def _write_eval_json(summary, doc_result: DocumentResult, gt_filename: str, path: str) -> None:
    tok = doc_result.token_usage
    data = {
        "input_file":       doc_result.input_file,
        "ground_truth_file": gt_filename,
        "total_gt_zones":   summary.total_gt_zones,
        "exact_matches":    summary.exact_matches,
        "token_usage": {
            "input_tokens":  tok.input_tokens,
            "output_tokens": tok.output_tokens,
            "total_tokens":  tok.total_tokens,
            "cost_usd":      round(tok.cost_usd(), 6),
        },
        "type_only_matches": summary.type_only_matches,
        "page_only_matches": summary.page_only_matches,
        "misses":           summary.misses,
        "exact_accuracy":   round(summary.exact_accuracy, 4),
        "type_accuracy":    round(summary.type_accuracy, 4),
        "zone_results": [
            {
                "gt_pages":       m.gt.raw_pages,
                "gt_type":        m.gt.document_type,
                "pred_pages": (
                    str(m.predicted.start_page)
                    if m.predicted and m.predicted.start_page == m.predicted.end_page
                    else (f"{m.predicted.start_page}-{m.predicted.end_page}" if m.predicted else None)
                ),
                "pred_type":      m.predicted.document_type if m.predicted else None,
                "pred_confidence": m.predicted.confidence if m.predicted else None,
                "pages_match":    m.pages_match,
                "type_match":     m.type_match,
                "exact_match":    m.exact_match,
            }
            for m in summary.matches
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _run_p3_evaluation(full_pipeline_path: str, gt_path: str) -> None:
    """Run P3 boundary evaluation and write p3_eval.json alongside full_pipeline.json."""
    try:
        p3_summary = evaluate_p3(gt_path, full_pipeline_path)
        click.echo("\n" + format_p3_report(p3_summary))
        p3_eval_path = full_pipeline_path.replace("full_pipeline.json", "p3_eval.json")
        Path(p3_eval_path).parent.mkdir(parents=True, exist_ok=True)
        with open(p3_eval_path, "w", encoding="utf-8") as f:
            json.dump(p3_summary_to_dict(p3_summary), f, indent=2)
        click.echo(f"  P3 evaluation saved to {p3_eval_path}")
    except Exception as exc:
        click.echo(f"  P3 evaluation ERROR: {exc}", err=True)


def _run_p4_evaluation(full_pipeline_path: str, gt_path: str) -> None:
    """Run P4 field evaluation and write p4_eval.json alongside full_pipeline.json."""
    try:
        p4_summary = evaluate_p4(gt_path, full_pipeline_path)
        click.echo("\n" + format_p4_report(p4_summary))
        p4_eval_path = full_pipeline_path.replace("full_pipeline.json", "p4_eval.json")
        Path(p4_eval_path).parent.mkdir(parents=True, exist_ok=True)
        with open(p4_eval_path, "w", encoding="utf-8") as f:
            json.dump(p4_summary_to_dict(p4_summary), f, indent=2)
        click.echo(f"  P4 evaluation saved to {p4_eval_path}")
    except Exception as exc:
        click.echo(f"  P4 evaluation ERROR: {exc}", err=True)


@cli.command("evaluate-p3")
@click.option("--full-pipeline", "fp_path", required=True,
              help="Path to full_pipeline.json from a --full-pipeline run.")
@click.option("--groundtruth", "gt_path", required=True,
              help="Path to ground-truth CSV.")
def evaluate_p3_cmd(fp_path: str, gt_path: str):
    """Score P3 document-splitting boundary accuracy against ground truth."""
    p3_summary = evaluate_p3(gt_path, fp_path)
    click.echo("\n" + format_p3_report(p3_summary))
    out_path = fp_path.replace("full_pipeline.json", "p3_eval.json")
    if out_path == fp_path:
        out_path = os.path.splitext(fp_path)[0] + "_p3_eval.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(p3_summary_to_dict(p3_summary), f, indent=2)
    click.echo(f"P3 evaluation saved to {out_path}")


@cli.command("evaluate-p4")
@click.option("--full-pipeline", "fp_path", required=True,
              help="Path to full_pipeline.json from a --full-pipeline run.")
@click.option("--groundtruth", "gt_path", required=True,
              help="Path to ground-truth CSV.")
def evaluate_p4_cmd(fp_path: str, gt_path: str):
    """Score P4 extracted fields (date, title, serial, hours, landings) against ground truth."""
    p4_summary = evaluate_p4(gt_path, fp_path)
    click.echo("\n" + format_p4_report(p4_summary))
    out_path = fp_path.replace("full_pipeline.json", "p4_eval.json")
    if out_path == fp_path:
        out_path = os.path.splitext(fp_path)[0] + "_p4_eval.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(p4_summary_to_dict(p4_summary), f, indent=2)
    click.echo(f"P4 evaluation saved to {out_path}")


if __name__ == "__main__":
    cli()
