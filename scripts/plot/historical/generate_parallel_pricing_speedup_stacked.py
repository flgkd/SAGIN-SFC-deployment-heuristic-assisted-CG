from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


def read_dataframe(path, *args, **kwargs):
    return normalize_dataframe(pd.read_csv(resolve_input_path(path), *args, **kwargs))


from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent
PLOTTED_ROWS = OUTPUT_DIR / "PLOTTED_ROWS.csv"

ALGORITHMS = ["dwcg_pure", "dwcg_saginfe_pro"]
DISPLAY = {
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
}
COLOR = {
    "dwcg_pure": "#ff7f0e",
    "dwcg_saginfe_pro": "#d627b9",
}
GRAY = {
    "dwcg_pure": "#666666",
    "dwcg_saginfe_pro": "#000000",
}
MARKER = {"dwcg_pure": "s", "dwcg_saginfe_pro": "X"}
MARKER_SIZE = {"dwcg_pure": 18, "dwcg_saginfe_pro": 21}
GRAY_LINESTYLE = {"dwcg_pure": "-", "dwcg_saginfe_pro": (0, (3.2, 1.8))}

WORKERS = [1, 2, 4]
WORKER_LABELS = {1: "1", 2: "2", 4: "4"}
X_AXIS_LABEL = "Number of Pricing Workers"
OFFSETS = [-0.12, 0.12]
SEED_JITTER = [-0.026, -0.020, -0.014, -0.008, -0.003, 0.003, 0.008, 0.014, 0.020, 0.026]
SEED_ALPHA = 0.42
TREND_ALPHA = 0.50
TREND_LINEWIDTH = 0.82
BOX_WIDTH = 0.18
PRICING_YLIM = (0.8, 4.2)
PRICING_TICKS = [1, 2, 3, 4]
E2E_YLIM = (0.95, 1.70)
E2E_TICKS = [1.0, 1.2, 1.4, 1.6]

# IEEE single-column canvas and explicit compact vertical composition.
FIGURE_SIZE_IN = (3.5, 5.95)
GRID_LEFT = 0.180
GRID_RIGHT = 0.975
GRID_BOTTOM = 0.230
GRID_TOP = 0.935
GRID_HSPACE = 0.520
PANEL_LABEL_OFFSET = 0.097
FOOTER_LINE_Y = (0.068, 0.030)
METHOD_LEGEND_Y = 0.995
IDEAL_LEGEND_Y = 0.966


def load_and_validate() -> pd.DataFrame:
    df = read_dataframe(PLOTTED_ROWS)
    required = {
        "algorithm",
        "formal_seed",
        "pricing_worker_count",
        "total_pricing_runtime",
        "total_end_to_end_runtime_seconds",
        "pricing_speedup",
        "e2e_speedup",
        "scale",
        "time_slots",
        "load_label",
    }
    missing_columns = required.difference(df.columns)
    if missing_columns:
        raise SystemExit(f"missing authoritative plotted-row columns: {sorted(missing_columns)}")

    df["formal_seed"] = df["formal_seed"].astype(int)
    df["pricing_worker_count"] = df["pricing_worker_count"].astype(int)
    expected = pd.MultiIndex.from_product(
        [ALGORITHMS, range(10), WORKERS],
        names=["algorithm", "formal_seed", "pricing_worker_count"],
    )
    actual = pd.MultiIndex.from_frame(df[list(expected.names)])
    if len(df) != 60 or actual.has_duplicates or len(expected.difference(actual)) or len(actual.difference(expected)):
        raise SystemExit("PLOTTED_ROWS.csv is not the complete 2 algorithms x 10 seeds x 3 workers matrix")
    if set(df["scale"]) != {"S40"} or set(df["time_slots"].astype(int)) != {8} or set(df["load_label"]) != {"High"}:
        raise SystemExit("authoritative plotted rows do not describe S40, |T|=8, High Load")

    # Recheck the authoritative paired, seed-level W1-relative calculation.
    for alg in ALGORITHMS:
        for seed in range(10):
            cell = df[(df["algorithm"] == alg) & (df["formal_seed"] == seed)].set_index("pricing_worker_count")
            for worker in WORKERS:
                pricing = float(cell.loc[1, "total_pricing_runtime"]) / float(cell.loc[worker, "total_pricing_runtime"])
                e2e = float(cell.loc[1, "total_end_to_end_runtime_seconds"]) / float(
                    cell.loc[worker, "total_end_to_end_runtime_seconds"]
                )
                if abs(pricing - float(cell.loc[worker, "pricing_speedup"])) > 1e-12:
                    raise SystemExit(f"pricing speedup mismatch for {alg}, seed {seed}, W{worker}")
                if abs(e2e - float(cell.loc[worker, "e2e_speedup"])) > 1e-12:
                    raise SystemExit(f"end-to-end speedup mismatch for {alg}, seed {seed}, W{worker}")
    return df


def configure_matplotlib() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.8,
            "axes.labelsize": 9.2,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 7.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def draw_panel(ax, plotted: pd.DataFrame, metric: str, grayscale: bool, ideal: bool = False) -> None:
    colors = GRAY if grayscale else COLOR
    if ideal:
        ax.plot(
            range(len(WORKERS)),
            WORKERS,
            color="#505050",
            linestyle=(0, (3.0, 2.2)),
            linewidth=0.75,
            alpha=0.72,
            zorder=1.8,
        )
    for worker_index, worker in enumerate(WORKERS):
        for alg_index, alg in enumerate(ALGORITHMS):
            pos = float(worker_index) + OFFSETS[alg_index]
            cell = plotted[
                (plotted["pricing_worker_count"] == worker) & (plotted["algorithm"] == alg)
            ].sort_values("formal_seed")
            values = cell[metric].to_numpy()
            color = colors[alg]
            fill_alpha = 0.18 if not grayscale or alg == "dwcg_pure" else 0.08
            bp = ax.boxplot(
                values,
                positions=[pos],
                widths=BOX_WIDTH,
                patch_artist=True,
                showfliers=False,
                whis=(0, 100),
                zorder=3,
            )
            for box in bp["boxes"]:
                box.set(facecolor=mpl.colors.to_rgba(color, fill_alpha), edgecolor=color, linewidth=0.95)
            for whisker in bp["whiskers"]:
                whisker.set(color=color, linewidth=0.8)
            for cap in bp["caps"]:
                cap.set(color=color, linewidth=0.8)
            for median in bp["medians"]:
                median.set(color="#202020", linewidth=1.35, zorder=8)
            ax.scatter(
                [pos + SEED_JITTER[int(seed)] for seed in cell["formal_seed"]],
                values,
                s=MARKER_SIZE[alg],
                marker=MARKER[alg],
                facecolor=color,
                edgecolor="#F2F2F2" if grayscale else "white",
                linewidth=0.28,
                alpha=SEED_ALPHA,
                zorder=5,
            )
            ax.hlines(
                float(values.mean()),
                pos - BOX_WIDTH * 0.42,
                pos + BOX_WIDTH * 0.42,
                colors="#3A3A3A" if grayscale else "#8B0000",
                linestyles=(0, (1.0, 1.2)),
                linewidth=1.55,
                alpha=0.95,
                zorder=9,
            )
    for alg_index, alg in enumerate(ALGORITHMS):
        xs = [float(i) + OFFSETS[alg_index] for i in range(len(WORKERS))]
        ys = [
            float(
                plotted[
                    (plotted["pricing_worker_count"] == worker) & (plotted["algorithm"] == alg)
                ][metric].mean()
            )
            for worker in WORKERS
        ]
        ax.plot(
            xs,
            ys,
            color=colors[alg],
            linestyle=GRAY_LINESTYLE[alg] if grayscale else "-",
            linewidth=TREND_LINEWIDTH,
            alpha=TREND_ALPHA,
            zorder=2,
        )
    ax.set_xticks(range(len(WORKERS)))
    ax.set_xticklabels([WORKER_LABELS[w] for w in WORKERS])
    ax.set_xlabel(X_AXIS_LABEL)
    ax.set_xlim(-0.48, len(WORKERS) - 1 + 0.48)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.78, zorder=1)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#303030")


def make_figure(plotted: pd.DataFrame, grayscale: bool) -> plt.Figure:
    configure_matplotlib()
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

    draw_panel(axes[0], plotted, "pricing_speedup", grayscale, ideal=True)
    axes[0].set_ylabel("Pricing Speedup")
    axes[0].set_ylim(*PRICING_YLIM)
    axes[0].set_yticks(PRICING_TICKS)

    draw_panel(axes[1], plotted, "e2e_speedup", grayscale, ideal=False)
    axes[1].set_ylabel("End-to-End Speedup")
    axes[1].set_ylim(*E2E_YLIM)
    axes[1].set_yticks(E2E_TICKS)

    colors = GRAY if grayscale else COLOR
    algorithm_handles = {
        alg: mlines.Line2D(
            [],
            [],
            color=colors[alg],
            marker=MARKER[alg],
            linestyle=GRAY_LINESTYLE[alg] if grayscale else "-",
            linewidth=1.2,
            markersize=5.4,
            label=DISPLAY[alg],
        )
        for alg in ALGORITHMS
    }
    ideal_handle = mlines.Line2D(
        [],
        [],
        color="#505050",
        linestyle=(0, (3.0, 2.2)),
        linewidth=0.75,
        label="Ideal linear speedup",
    )
    method_legend = fig.legend(
        handles=[algorithm_handles["dwcg_pure"], algorithm_handles["dwcg_saginfe_pro"]],
        loc="upper center",
        bbox_to_anchor=(0.5, METHOD_LEGEND_Y),
        ncol=2,
        frameon=False,
        columnspacing=1.50,
        handlelength=1.55,
        handletextpad=0.48,
        borderpad=0.0,
        borderaxespad=0.0,
    )
    fig.canvas.draw()
    method_bbox = method_legend.get_window_extent(fig.canvas.get_renderer()).transformed(fig.transFigure.inverted())
    ideal_legend = fig.legend(
        handles=[ideal_handle],
        loc="upper left",
        bbox_to_anchor=(method_bbox.x0, IDEAL_LEGEND_Y),
        ncol=1,
        frameon=False,
        handlelength=1.55,
        handletextpad=0.48,
        borderpad=0.0,
        borderaxespad=0.0,
    )
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    pure_handle_left = method_legend.legendHandles[0].get_window_extent(renderer).x0
    ideal_handle_left = ideal_legend.legendHandles[0].get_window_extent(renderer).x0
    handle_alignment_shift = (pure_handle_left - ideal_handle_left) / fig.bbox.width
    ideal_legend.set_bbox_to_anchor(
        (method_bbox.x0 + handle_alignment_shift, IDEAL_LEGEND_Y),
        transform=fig.transFigure,
    )

    for ax, label in zip(axes, ["(a) Pricing Speedup", "(b) End-to-End Speedup"]):
        bbox = ax.get_position()
        fig.text(
            (bbox.x0 + bbox.x1) / 2,
            bbox.y0 - PANEL_LABEL_OFFSET,
            label,
            ha="center",
            va="top",
            fontsize=9.2,
        )
    fig.text(0.5, FOOTER_LINE_Y[0], "Parallel Pricing with", ha="center", fontsize=8.8)
    fig.text(0.5, FOOTER_LINE_Y[1], "S40 (|T|=8, |F|=160, High Load)", ha="center", fontsize=8.8)
    return fig


def main() -> None:
    plotted = load_and_validate()
    for grayscale, stem in [
        (False, "parallel_pricing_speedup_stacked"),
        (True, "parallel_pricing_speedup_stacked_gray"),
    ]:
        fig = make_figure(plotted, grayscale)
        fig.savefig(OUTPUT_DIR / f"{stem}.pdf", facecolor="white")
        fig.savefig(OUTPUT_DIR / f"{stem}.png", dpi=600, facecolor="white")
        plt.close(fig)
    print(f"Generated stacked speedup variants from {PLOTTED_ROWS}")


if __name__ == "__main__":
    main()
