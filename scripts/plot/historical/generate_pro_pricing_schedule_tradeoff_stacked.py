"""Generate the stacked DWCG-SAGINFE-PRO pricing-schedule trade-off figure.

This plotting-only presentation variant reuses the frozen source, validation,
statistics, and panel-drawing code from generate_pro_pricing_schedule_tradeoff_final.py.
It does not run an optimizer or modify scientific data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


def read_dataframe(path, *args, **kwargs):
    return normalize_dataframe(pd.read_csv(resolve_input_path(path), *args, **kwargs))


import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd

import generate_pro_pricing_schedule_tradeoff_final as original


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = Path(__file__).resolve().parent
SOURCE_CSV = PROJECT_ROOT / "outputs" / "pro_pricing_schedule_v3_100_bb65465" / "pro_pricing_schedule_seed_level.csv"
FROZEN_MANIFEST = PROJECT_ROOT / "results" / "frozen" / "pro_pricing_schedule_v3" / "pro_pricing_schedule_final_manifest.json"
PLOTTED_ROWS = OUTPUT_DIR / "PLOTTED_ROWS.csv"

FIGURE_SIZE_IN = (3.5, 6.25)
GRID_LEFT = 0.200
GRID_RIGHT = 0.975
GRID_BOTTOM = 0.240
GRID_TOP = 0.890
GRID_HSPACE = 0.520
PANEL_LABEL_OFFSET = 0.095
LEGEND_FIRST_ROW_Y = 0.988
LEGEND_SECOND_ROW_Y = 0.928
FOOTER_LINE_Y = (0.075, 0.035)


def load_and_validate() -> pd.DataFrame:
    manifest = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    source_sha = original.sha256_file(SOURCE_CSV)
    df = read_dataframe(SOURCE_CSV)
    df["formal_seed"] = df["formal_seed"].astype(int)
    validation = original.validate(df, manifest, source_sha)
    if not validation["pass"]:
        raise SystemExit(json.dumps(validation, indent=2, sort_keys=True))

    # Certify that the frozen source still projects exactly to the final plotted rows.
    plotted = read_dataframe(PLOTTED_ROWS)
    source_view = df[list(plotted.columns)]
    sort_columns = list(plotted.columns)
    pd.testing.assert_frame_equal(
        plotted.sort_values(sort_columns).reset_index(drop=True),
        source_view.sort_values(sort_columns).reset_index(drop=True),
        check_dtype=False,
        atol=1e-12,
        rtol=0.0,
    )
    return df


def legend_handles(grayscale: bool) -> list[Line2D]:
    handles = []
    for schedule in original.SCHEDULE_ORDER:
        color = original.schedule_color(schedule, grayscale)
        marker = original.SCHEDULE_STYLE[schedule]["marker"]
        handles.append(
            Line2D(
                [0],
                [0],
                marker=marker,
                linestyle="none",
                markersize=6.2 if marker not in ("*", "8") else 7.4,
                markerfacecolor=color,
                markeredgecolor="#111111" if grayscale else "#222222",
                markeredgewidth=0.55,
                label=schedule,
            )
        )
    return handles


def make_figure(df: pd.DataFrame, grayscale: bool) -> plt.Figure:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    stats = original.compute_stats(df)
    fig = plt.figure(figsize=FIGURE_SIZE_IN, dpi=300)
    grid = fig.add_gridspec(
        2,
        1,
        left=GRID_LEFT,
        right=GRID_RIGHT,
        bottom=GRID_BOTTOM,
        top=GRID_TOP,
        hspace=GRID_HSPACE,
    )
    axes = [fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[1, 0])]
    for ax, load, label in zip(axes, original.LOAD_ORDER, ["(a) Medium Load", "(b) High Load"]):
        original.draw_tradeoff_panel(ax, df, stats, load, grayscale)
        bbox = ax.get_position()
        fig.text(
            (bbox.x0 + bbox.x1) / 2,
            bbox.y0 - PANEL_LABEL_OFFSET,
            label,
            ha="center",
            va="top",
            fontsize=9.0,
        )

    handles = legend_handles(grayscale)
    first_legend = fig.legend(
        handles=handles[:3],
        title="Pricing Schedule",
        loc="upper center",
        bbox_to_anchor=(0.5, LEGEND_FIRST_ROW_Y),
        ncol=3,
        frameon=False,
        handletextpad=0.42,
        columnspacing=1.05,
        labelspacing=0.50,
        borderpad=0.0,
        borderaxespad=0.0,
    )
    first_legend.get_title().set_fontsize(8.5)
    for text in first_legend.get_texts():
        text.set_fontsize(8.2)
    second_legend = fig.legend(
        handles=handles[3:],
        loc="upper center",
        bbox_to_anchor=(0.5, LEGEND_SECOND_ROW_Y),
        ncol=2,
        frameon=False,
        handletextpad=0.42,
        columnspacing=1.15,
        borderpad=0.0,
        borderaxespad=0.0,
    )
    for text in second_legend.get_texts():
        text.set_fontsize(8.2)

    fig.text(0.5, FOOTER_LINE_Y[0], "DWCG-SAGINFE-PRO Pricing Schedule with", ha="center", fontsize=8.8)
    fig.text(
        0.5,
        FOOTER_LINE_Y[1],
        r"S40 ($|T|=8$, $|F|=160$, Medium/High Load)",
        ha="center",
        fontsize=8.8,
    )
    return fig


def main() -> None:
    df = load_and_validate()
    for grayscale, stem in [
        (False, "pro_pricing_schedule_tradeoff_stacked"),
        (True, "pro_pricing_schedule_tradeoff_stacked_gray"),
    ]:
        fig = make_figure(df, grayscale)
        fig.savefig(OUTPUT_DIR / f"{stem}.pdf", facecolor="white")
        fig.savefig(OUTPUT_DIR / f"{stem}.png", dpi=600, facecolor="white")
        plt.close(fig)
    print(f"Generated stacked DWCG-SAGINFE-PRO trade-off variants from {SOURCE_CSV}")


if __name__ == "__main__":
    main()
