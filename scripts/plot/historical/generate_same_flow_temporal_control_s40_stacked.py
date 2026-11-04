from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


def read_dataframe(path, *args, **kwargs):
    return normalize_dataframe(pd.read_csv(resolve_input_path(path), *args, **kwargs))


import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent
PLOTTED_ROWS = OUTPUT_DIR / "PLOTTED_ROWS.csv"
PLOTTED_ROWS_SHA256 = "8560bc8948a701d4129521bc045aadb740bbe3634f8d10b88489a979b00c9d71"

ALGORITHMS = ["saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro"]
DISPLAY = {
    "saginfe": "SAGINFE",
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
}
COLOR = {
    "saginfe": "#1f77b4",
    "dwcg_pure": "#ff7f0e",
    "dwcg_saginfe": "#2ca02c",
    "dwcg_saginfe_pro": "#d627b9",
}
GRAY = {
    "saginfe": "#222222",
    "dwcg_pure": "#666666",
    "dwcg_saginfe": "#999999",
    "dwcg_saginfe_pro": "#000000",
}
MARKER = {
    "saginfe": "o",
    "dwcg_pure": "s",
    "dwcg_saginfe": "^",
    "dwcg_saginfe_pro": "X",
}
# The authoritative figure uses solid algorithm trend lines in both modes.
LINESTYLE = {alg: "-" for alg in ALGORITHMS}
MARKER_SIZE = {
    "saginfe": 14,
    "dwcg_pure": 15,
    "dwcg_saginfe": 17,
    "dwcg_saginfe_pro": 18,
}
TIME_SLOTS = [6, 8, 10, 12]
OFFSETS = [-0.27, -0.09, 0.09, 0.27]
SEED_JITTER = [-0.026, -0.020, -0.014, -0.008, -0.003, 0.003, 0.008, 0.014, 0.020, 0.026]
SEED_ALPHA = 0.40
TREND_ALPHA = 0.45
TREND_LINEWIDTH = 0.72
BOX_WIDTH = 0.16

ACCEPTANCE_YLIM = (74, 86)
ACCEPTANCE_TICKS = [74, 76, 78, 80, 82, 84, 86]
RUNTIME_YLIM = (15, 1800)
RUNTIME_TICKS = [15, 20, 30, 60, 120, 300, 600, 1200, 1800]

EXPECTED_ACCEPTED = {
    ("saginfe", 6): (127.2, 127.5), ("saginfe", 8): (127.2, 127.5),
    ("saginfe", 10): (127.2, 127.5), ("saginfe", 12): (127.2, 127.5),
    ("dwcg_pure", 6): (128.8, 129.0), ("dwcg_pure", 8): (128.2, 129.0),
    ("dwcg_pure", 10): (128.8, 129.0), ("dwcg_pure", 12): (128.2, 128.5),
    ("dwcg_saginfe", 6): (128.1, 129.5), ("dwcg_saginfe", 8): (128.0, 129.0),
    ("dwcg_saginfe", 10): (127.9, 128.0), ("dwcg_saginfe", 12): (128.0, 128.5),
    ("dwcg_saginfe_pro", 6): (127.9, 128.0), ("dwcg_saginfe_pro", 8): (127.9, 128.5),
    ("dwcg_saginfe_pro", 10): (128.0, 129.0), ("dwcg_saginfe_pro", 12): (128.2, 128.5),
}
EXPECTED_RUNTIME = {
    ("saginfe", 6): (19.948, 19.935), ("saginfe", 8): (29.976, 30.516),
    ("saginfe", 10): (40.113, 40.398), ("saginfe", 12): (50.730, 51.451),
    ("dwcg_pure", 6): (533.377, 532.362), ("dwcg_pure", 8): (637.198, 636.982),
    ("dwcg_pure", 10): (758.868, 760.277), ("dwcg_pure", 12): (884.143, 880.526),
    ("dwcg_saginfe", 6): (838.262, 867.786), ("dwcg_saginfe", 8): (1092.204, 1118.152),
    ("dwcg_saginfe", 10): (1342.710, 1395.673), ("dwcg_saginfe", 12): (1669.288, 1690.033),
    ("dwcg_saginfe_pro", 6): (566.866, 561.821), ("dwcg_saginfe_pro", 8): (685.174, 682.248),
    ("dwcg_saginfe_pro", 10): (830.610, 828.924), ("dwcg_saginfe_pro", 12): (976.322, 976.770),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with resolve_input_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_time(seconds: float, _pos: int | None = None) -> str:
    if seconds < 60:
        return f"{int(seconds)} s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} min" if abs(minutes - round(minutes)) < 1e-9 else f"{minutes:g} min"
    hours = minutes / 60
    return f"{hours:g} h"


def load_and_validate() -> pd.DataFrame:
    actual_hash = sha256(PLOTTED_ROWS)
    if actual_hash != PLOTTED_ROWS_SHA256:
        raise SystemExit(
            "PLOTTED_ROWS.csv differs from the authoritative final plotting input: "
            f"expected {PLOTTED_ROWS_SHA256}, found {actual_hash}"
        )

    df = read_dataframe(PLOTTED_ROWS)
    df["T"] = df["T"].astype(int)
    df["seed"] = df["seed"].astype(int)
    for column in ["accepted_flow_count", "acceptance_rate", "runtime"]:
        df[column] = df[column].astype(float)

    expected = pd.MultiIndex.from_product(
        [ALGORITHMS, TIME_SLOTS, range(10)], names=["algorithm", "T", "seed"]
    )
    actual = pd.MultiIndex.from_frame(df[["algorithm", "T", "seed"]])
    if len(df) != 160:
        raise SystemExit(f"expected 160 plotted rows, found {len(df)}")
    if df.duplicated(["algorithm", "T", "seed"]).any():
        raise SystemExit("duplicate algorithm/T/seed cells found")
    if len(expected.difference(actual)) or len(actual.difference(expected)):
        raise SystemExit("missing or extra algorithm/T/seed cells found")
    if set(df["scale"]) != {"S40"} or set(df["load"]) != {"Medium"}:
        raise SystemExit("scenario fields are not fixed at S40/Medium")
    if set(df["total_flow_count"]) != {160} or set(df["deadline_profile"]) != {"Balanced"}:
        raise SystemExit("flow count or deadline profile differs from the final figure")
    if not df[["accepted_flow_count", "acceptance_rate", "runtime"]].applymap(math.isfinite).all().all():
        raise SystemExit("non-finite plotting values found")
    if not ((df["acceptance_rate"] - df["accepted_flow_count"] / 160.0).abs() < 1e-10).all():
        raise SystemExit("acceptance_rate does not match accepted_flow_count / 160")
    if not (df["runtime"] > 0).all():
        raise SystemExit("non-positive runtime found")

    for algorithm in ALGORITHMS:
        for time_slot in TIME_SLOTS:
            cell = df[(df["algorithm"] == algorithm) & (df["T"] == time_slot)]
            accepted = (round(float(cell["accepted_flow_count"].mean()), 1),
                        round(float(cell["accepted_flow_count"].median()), 1))
            runtime = (round(float(cell["runtime"].mean()), 3),
                       round(float(cell["runtime"].median()), 3))
            if accepted != EXPECTED_ACCEPTED[(algorithm, time_slot)]:
                raise SystemExit(f"accepted-flow summary mismatch for {(algorithm, time_slot)}")
            if runtime != EXPECTED_RUNTIME[(algorithm, time_slot)]:
                raise SystemExit(f"runtime summary mismatch for {(algorithm, time_slot)}")
    return df


def draw_panel(ax: mpl.axes.Axes, df: pd.DataFrame, metric: str, grayscale: bool) -> None:
    colors = GRAY if grayscale else COLOR
    for time_index, time_slot in enumerate(TIME_SLOTS):
        center = float(time_index)
        for algorithm_index, algorithm in enumerate(ALGORITHMS):
            position = center + OFFSETS[algorithm_index]
            cell = df[(df["T"] == time_slot) & (df["algorithm"] == algorithm)].sort_values("seed")
            values = (
                (cell[metric] * 100.0).to_numpy()
                if metric == "acceptance_rate"
                else cell[metric].to_numpy()
            )
            color = colors[algorithm]
            boxplot = ax.boxplot(
                values,
                positions=[position],
                widths=BOX_WIDTH,
                patch_artist=True,
                showfliers=False,
                whis=(0, 100),
                zorder=3,
            )
            for box in boxplot["boxes"]:
                box.set(facecolor=mpl.colors.to_rgba(color, 0.16), edgecolor=color, linewidth=0.95)
            for whisker in boxplot["whiskers"]:
                whisker.set(color=color, linewidth=0.8)
            for cap in boxplot["caps"]:
                cap.set(color=color, linewidth=0.8)
            for median in boxplot["medians"]:
                median.set(color="#202020", linewidth=1.35, zorder=8)

            x_values = [position + SEED_JITTER[int(seed)] for seed in cell["seed"]]
            ax.scatter(
                x_values,
                values,
                s=MARKER_SIZE[algorithm],
                marker=MARKER[algorithm],
                facecolor=color,
                edgecolor="white" if not grayscale else "#F2F2F2",
                linewidth=0.28,
                alpha=SEED_ALPHA,
                zorder=5,
            )
            mean_value = float(values.mean())
            ax.hlines(
                mean_value,
                position - BOX_WIDTH * 0.42,
                position + BOX_WIDTH * 0.42,
                colors="#8B0000",
                linestyles=(0, (1.0, 1.2)),
                linewidth=1.55,
                alpha=0.95,
                zorder=9,
            )

    for algorithm_index, algorithm in enumerate(ALGORITHMS):
        x_values = [float(index) + OFFSETS[algorithm_index] for index in range(len(TIME_SLOTS))]
        y_values = []
        for time_slot in TIME_SLOTS:
            cell = df[(df["T"] == time_slot) & (df["algorithm"] == algorithm)]
            value = (
                float(cell[metric].mean() * 100.0)
                if metric == "acceptance_rate"
                else float(cell[metric].mean())
            )
            y_values.append(value)
        ax.plot(
            x_values,
            y_values,
            color=colors[algorithm],
            linestyle=LINESTYLE[algorithm],
            linewidth=TREND_LINEWIDTH,
            alpha=TREND_ALPHA,
            zorder=2,
        )

    ax.set_xticks(range(len(TIME_SLOTS)))
    ax.set_xticklabels([f"|T|={time_slot}" for time_slot in TIME_SLOTS])
    ax.set_xlim(-0.62, len(TIME_SLOTS) - 1 + 0.62)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.78, zorder=1)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#303030")


def make_figure(df: pd.DataFrame, grayscale: bool) -> plt.Figure:
    mpl.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8.6,
        "axes.labelsize": 8.9,
        "xtick.labelsize": 8.2,
        "ytick.labelsize": 8.2,
        "legend.fontsize": 8.1,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    fig, axes = plt.subplots(2, 1, figsize=(3.5, 5.65), dpi=300)
    fig.subplots_adjust(left=0.205, right=0.975, bottom=0.210, top=0.895, hspace=0.36)

    draw_panel(axes[0], df, "acceptance_rate", grayscale)
    axes[0].set_ylabel("Acceptance Rate (%)")
    axes[0].set_ylim(*ACCEPTANCE_YLIM)
    axes[0].set_yticks(ACCEPTANCE_TICKS)

    draw_panel(axes[1], df, "runtime", grayscale)
    axes[1].set_ylabel("Total Runtime")
    axes[1].set_yscale("log")
    axes[1].set_ylim(*RUNTIME_YLIM)
    axes[1].yaxis.set_major_locator(mpl.ticker.FixedLocator(RUNTIME_TICKS))
    axes[1].yaxis.set_major_formatter(mpl.ticker.FuncFormatter(human_time))
    axes[1].yaxis.set_minor_formatter(mpl.ticker.NullFormatter())

    colors = GRAY if grayscale else COLOR
    handles = [
        mlines.Line2D(
            [], [],
            color=colors[algorithm],
            marker=MARKER[algorithm],
            linestyle=LINESTYLE[algorithm],
            linewidth=1.2,
            markersize=5.2,
            label=DISPLAY[algorithm],
        )
        for algorithm in ALGORITHMS
    ]
    # Matplotlib fills multi-column legends top-to-bottom. Reorder handles so
    # the visible reading order stays identical to the horizontal figure.
    legend_handles = [handles[0], handles[2], handles[1], handles[3]]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.965),
        ncol=2,
        frameon=False,
        columnspacing=0.70,
        handlelength=1.30,
        handletextpad=0.35,
        labelspacing=0.18,
        borderpad=0.0,
        borderaxespad=0.0,
    )

    for axis, label in zip(axes, ["(a) Acceptance Rate", "(b) Total Runtime"]):
        position = axis.get_position()
        fig.text(
            (position.x0 + position.x1) / 2,
            position.y0 - 0.055,
            label,
            ha="center",
            va="top",
            fontsize=8.9,
        )
    fig.text(0.5, 0.096, "Same-flow Temporal Control with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.066, "S40 (|F|=160, Medium Load)", ha="center", fontsize=8.8)
    return fig


def main() -> None:
    data = load_and_validate()
    outputs: list[str] = []
    for grayscale, stem in [
        (False, "same_flow_temporal_control_s40_stacked"),
        (True, "same_flow_temporal_control_s40_stacked_gray"),
    ]:
        figure = make_figure(data, grayscale)
        for suffix in ["pdf", "png"]:
            filename = f"{stem}.{suffix}"
            figure.savefig(
                OUTPUT_DIR / filename,
                dpi=600 if suffix == "png" else None,
                facecolor="white",
            )
            outputs.append(filename)
        plt.close(figure)

    for filename in outputs:
        path = OUTPUT_DIR / filename
        if not path.exists() or path.stat().st_size <= 0:
            raise SystemExit(f"missing or empty output: {filename}")

    result = {
        "status": "ok",
        "authoritative_plotted_rows": PLOTTED_ROWS.name,
        "authoritative_plotted_rows_sha256": sha256(PLOTTED_ROWS),
        "plotted_row_count": len(data),
        "algorithm_counts": dict(Counter(data["algorithm"])),
        "T_counts": {str(key): value for key, value in sorted(Counter(data["T"]).items())},
        "outputs": {filename: sha256(OUTPUT_DIR / filename) for filename in outputs},
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
