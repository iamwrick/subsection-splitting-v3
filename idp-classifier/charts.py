"""
Classification accuracy visualisations for the master report.

Three chart types:
  lollipop_chart  — dot/lollipop comparing 3 conditions across metrics (less ink)
  radar_chart     — spider/radar giving a holistic metric overview
  slope_chart     — parallel-coordinates showing direction of change between methods
"""

from __future__ import annotations

import io
import math

import matplotlib
matplotlib.use("Agg")            # non-interactive backend — no display needed
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.patches import FancyArrowPatch


# ── Brand colours ─────────────────────────────────────────────────────────────
COL_OCR   = "#1A5276"   # deep blue
COL_TXT   = "#D35400"   # amber
COL_TXTE  = "#1E8449"   # green
COLS      = [COL_OCR, COL_TXT, COL_TXTE]
ALPHA_BG  = 0.12


def _buf() -> io.BytesIO:
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close("all")
    buf.seek(0)
    return buf


# ── Data helpers ──────────────────────────────────────────────────────────────

def build_metrics(stats: dict, n_folders: int) -> dict[str, float]:
    """Convert aggregated batch stats into a normalised 0-100 metric dict."""
    ex  = stats.get("exact_matches", 0)
    ty  = stats.get("exact_matches", 0) + stats.get("type_only_matches", 0)
    gt  = stats.get("total_gt_zones", 1)
    at100 = stats.get("at_100", 0)
    ge90  = stats.get("ge_90",  0)
    low   = stats.get("low_70", 0)
    return {
        "Exact\naccuracy":  100 * ex / gt if gt else 0,
        "Type\naccuracy":   100 * ty / gt if gt else 0,
        "100% type\nfolders": 100 * at100 / n_folders if n_folders else 0,
        "≥90% type\nfolders": 100 * ge90  / n_folders if n_folders else 0,
        "No issue\nfolders":  100 * (n_folders - low) / n_folders if n_folders else 0,
    }


def _aggregate(results: dict) -> dict:
    """Compute aggregate stats from load_batch results dict."""
    if not results:
        return {}
    ex = sum(r["exact_matches"] for r in results.values())
    ty = sum(r["exact_matches"] + r["type_only_matches"] for r in results.values())
    gt = sum(r["total_gt_zones"] for r in results.values())
    at100 = sum(1 for r in results.values() if r["type_accuracy"] >= 0.999)
    ge90  = sum(1 for r in results.values() if r["type_accuracy"] >= 0.90)
    low   = sum(1 for r in results.values() if r["type_accuracy"] < 0.70)
    return {"exact_matches": ex, "type_only_matches": ty - ex,
            "total_gt_zones": gt, "at_100": at100, "ge_90": ge90, "low_70": low}


# ══════════════════════════════════════════════════════════════════════════════
# 1 — LOLLIPOP CHART
# ══════════════════════════════════════════════════════════════════════════════

def lollipop_chart(
    ocr: dict,
    txt: dict,
    txte: dict | None,
    n_folders: int,
    labels: tuple[str, str, str] = ("OCR Input", "Text (raw)", "Text + Hints"),
) -> io.BytesIO:
    """
    Horizontal lollipop plot: each metric is a row, three coloured dots
    (one per condition) sit on a thin horizontal track.
    """
    agg_ocr  = _aggregate(ocr)
    agg_txt  = _aggregate(txt)
    agg_txte = _aggregate(txte) if txte else None

    m_ocr  = build_metrics(agg_ocr,  n_folders)
    m_txt  = build_metrics(agg_txt,  n_folders)
    m_txte = build_metrics(agg_txte, n_folders) if agg_txte else None

    metrics = list(m_ocr.keys())
    n       = len(metrics)
    active  = [("OCR", m_ocr, COL_OCR), ("Text", m_txt, COL_TXT)]
    if m_txte:
        active.append(("Text+Hints", m_txte, COL_TXTE))

    fig, axes = plt.subplots(n, 1, figsize=(7, n * 0.9 + 0.8), sharex=True)
    if n == 1:
        axes = [axes]

    fig.patch.set_facecolor("#FAFAFA")

    for ax, metric in zip(axes, metrics):
        ax.set_facecolor("#F5F5F5")
        ax.spines[["top","right","left"]].set_visible(False)
        ax.tick_params(left=False, labelsize=8)
        ax.set_yticks([])
        ax.set_ylabel(metric, fontsize=8.5, rotation=0, ha="right", va="center",
                      labelpad=120, fontweight="bold", color="#2C3E50")

        vals = [(lbl, d[metric], c) for lbl, d, c in active]
        min_v, max_v = min(v for _, v, _ in vals), max(v for _, v, _ in vals)

        # Range bar
        ax.barh(0, max_v - min_v, left=min_v, height=0.08,
                color="#CCCCCC", alpha=0.6, zorder=1)

        for lbl, val, col in vals:
            ax.plot(val, 0, "o", color=col, markersize=10, zorder=3,
                    markeredgewidth=1, markeredgecolor="white")
            ax.vlines(val, -0.25, 0.25, color=col, linewidth=1.5, alpha=0.5, zorder=2)

        ax.set_ylim(-0.6, 0.6)
        ax.set_xlim(-2, 104)
        ax.xaxis.grid(True, color="white", linewidth=1)

    axes[-1].set_xlabel("Value (%)", fontsize=8.5, color="#5D6D7E")
    axes[-1].tick_params(axis="x", labelsize=8)

    # Legend
    patches = [mpatches.Patch(color=c, label=l) for l, _, c in active]
    fig.legend(handles=patches, loc="upper center", ncol=len(active),
               fontsize=8.5, frameon=False,
               bbox_to_anchor=(0.5, 1.0), bbox_transform=fig.transFigure)

    fig.suptitle("Classification Accuracy — Lollipop Comparison",
                 fontsize=11, fontweight="bold", color="#1A252F", y=1.03)
    plt.tight_layout(h_pad=0.3)
    return _buf()


# ══════════════════════════════════════════════════════════════════════════════
# 2 — RADAR / SPIDER CHART
# ══════════════════════════════════════════════════════════════════════════════

def radar_chart(
    ocr: dict,
    txt: dict,
    txte: dict | None,
    n_folders: int,
    labels: tuple[str, str, str] = ("OCR Input", "Text (raw)", "Text + Hints"),
) -> io.BytesIO:
    """
    Radar/spider chart: five axes, one polygon per condition, filled with
    transparency to show overlap.
    """
    agg_ocr  = _aggregate(ocr)
    agg_txt  = _aggregate(txt)
    agg_txte = _aggregate(txte) if txte else None

    m_ocr  = build_metrics(agg_ocr,  n_folders)
    m_txt  = build_metrics(agg_txt,  n_folders)
    m_txte = build_metrics(agg_txte, n_folders) if agg_txte else None

    categories = list(m_ocr.keys())
    N = len(categories)
    angles = [n / N * 2 * math.pi for n in range(N)]
    angles += angles[:1]   # close polygon

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={"polar": True})
    fig.patch.set_facecolor("#FAFAFA")
    ax.set_facecolor("#F8F9FA")

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, size=8.5, color="#2C3E50", fontweight="bold")
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], size=7, color="#888888")
    ax.spines["polar"].set_color("#CCCCCC")
    ax.grid(color="#DDDDDD", linewidth=0.8)

    datasets = [("OCR Input", m_ocr, COL_OCR), ("Text (raw)", m_txt, COL_TXT)]
    if m_txte:
        datasets.append(("Text + Hints", m_txte, COL_TXTE))

    for label, metrics_d, col in datasets:
        vals = [metrics_d[c] for c in categories] + [metrics_d[categories[0]]]
        ax.plot(angles, vals, "o-", linewidth=2, color=col, label=label, markersize=5)
        ax.fill(angles, vals, alpha=ALPHA_BG, color=col)

    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.18),
              ncol=len(datasets), fontsize=9, frameon=False)
    ax.set_title("Classification Accuracy — Radar Overview",
                 fontsize=11, fontweight="bold", color="#1A252F", pad=20)

    plt.tight_layout()
    return _buf()


# ══════════════════════════════════════════════════════════════════════════════
# 3 — SLOPE / PARALLEL-COORDINATES CHART
# ══════════════════════════════════════════════════════════════════════════════

def slope_chart(
    ocr: dict,
    txt: dict,
    txte: dict | None,
    n_folders: int,
    labels: tuple[str, str, str] = ("OCR Input", "Text (raw)", "Text + Hints"),
) -> io.BytesIO:
    """
    Slope chart (parallel coordinates): each metric is a coloured line
    connecting its value across the three input methods. Makes direction
    of change immediately visible.
    """
    agg_ocr  = _aggregate(ocr)
    agg_txt  = _aggregate(txt)
    agg_txte = _aggregate(txte) if txte else None

    m_ocr  = build_metrics(agg_ocr,  n_folders)
    m_txt  = build_metrics(agg_txt,  n_folders)
    m_txte = build_metrics(agg_txte, n_folders) if agg_txte else None

    metrics    = list(m_ocr.keys())
    conditions = ["OCR\nInput", "Text\n(raw)", "Text\n+Hints"] if m_txte else ["OCR\nInput", "Text\n(raw)"]
    xs = list(range(len(conditions)))

    # Metric colour palette — distinct from the condition colours
    metric_cols = ["#E74C3C", "#8E44AD", "#2980B9", "#27AE60", "#E67E22"]

    fig, ax = plt.subplots(figsize=(6, 5.5))
    fig.patch.set_facecolor("#FAFAFA")
    ax.set_facecolor("#F8F9FA")

    for i, (metric, col) in enumerate(zip(metrics, metric_cols)):
        vals = [m_ocr[metric], m_txt[metric]]
        if m_txte:
            vals.append(m_txte[metric])

        ax.plot(xs, vals, "o-", color=col, linewidth=2.2, markersize=8,
                label=metric.replace("\n", " "), zorder=3,
                markeredgewidth=1.5, markeredgecolor="white")

        # Value labels at each point
        for x, v in zip(xs, vals):
            offset = 2.5
            ax.annotate(f"{v:.0f}%", xy=(x, v),
                        xytext=(0, offset), textcoords="offset points",
                        ha="center", va="bottom", fontsize=7.5,
                        color=col, fontweight="bold")

        # Metric label at right end
        ax.annotate(metric.replace("\n", " "),
                    xy=(xs[-1], vals[-1]),
                    xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=8, color=col)

    ax.set_xticks(xs)
    ax.set_xticklabels(conditions, fontsize=9.5, fontweight="bold", color="#2C3E50")
    ax.set_xlim(-0.3, len(conditions) - 0.3 + 1.2)   # room for labels
    ax.set_ylim(0, 115)
    ax.set_ylabel("Value (%)", fontsize=9, color="#5D6D7E")
    ax.spines[["top","right"]].set_visible(False)
    ax.spines[["left","bottom"]].set_color("#CCCCCC")
    ax.yaxis.grid(True, color="#E8E8E8", linewidth=0.8, zorder=0)
    ax.tick_params(left=True, labelsize=8)

    # Vertical separator lines between conditions
    for x in xs:
        ax.axvline(x, color="#DDDDDD", linewidth=1, zorder=0)

    ax.legend(loc="lower left", fontsize=7.5, frameon=True,
              framealpha=0.85, edgecolor="#CCCCCC",
              bbox_to_anchor=(0.01, 0.01))
    ax.set_title("Classification Accuracy — Slope Chart\n(direction of change across input methods)",
                 fontsize=11, fontweight="bold", color="#1A252F", pad=10)

    plt.tight_layout()
    return _buf()
