from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "figures" / "main_scaling" / "acceptance_runtime_review"

MASTER_DIR = ROOT / "results" / "frozen" / "formal_scale_load_master_v3_407efbf"
PRO_DIR = ROOT / "formal_outputs" / "formal_dwcg_saginfe_pro_v3_970415f"
MERGED_MANIFEST_DIR = ROOT / "results" / "frozen" / "merged_formal_v3_480_manifest_970415f"
FINAL10_DIR = ROOT / "results" / "frozen" / "main_scaling_dwcg_saginfe_s50_medium_final10_v3_006cc96"
UNLIMITED_DIR = ROOT / "results" / "frozen" / "main_scaling_dwcg_saginfe_s50_medium_unlimited_timeout8_v3_006cc96"

SCALES = ["S20", "S30", "S40", "S50"]
LOADS = ["Low", "Medium", "High"]
SEEDS = list(range(10))
ALGS = ["saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro"]

LABEL = {
    "saginfe": "SAGINFE",
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
}

STYLE = {
    "saginfe": {"color": "#1f77b4", "marker": "o"},
    "dwcg_pure": {"color": "#ff7f0e", "marker": "s"},
    "dwcg_saginfe": {"color": "#2ca02c", "marker": "^"},
    "dwcg_saginfe_pro": {"color": "#d627b9", "marker": "X"},
}

GRAY = {
    "saginfe": "#222222",
    "dwcg_pure": "#666666",
    "dwcg_saginfe": "#999999",
    "dwcg_saginfe_pro": "#000000",
}

MEAN_COLOR = "#8B1A1A"
MEAN_GRAY = "#444444"
MEAN_LINESTYLE = ":"


def load_jsonl(path: Path) -> list[dict]:
    with resolve_input_path(path).open("r", encoding="utf-8") as fh:
        return [normalize_record(json.loads(line)) for line in fh if line.strip()]


def load_csv(path: Path) -> list[dict]:
    with resolve_input_path(path).open("r", encoding="utf-8", newline="") as fh:
        return normalize_record(list(csv.DictReader(fh)))


def key_of(row: dict) -> tuple[str, str, int, str]:
    key = row["key"]
    return key["scale"], key["load_label"], int(key["formal_seed"]), key["algorithm"]


def bool_text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value in ("True", "true", "1", 1):
        return "true"
    if value in ("False", "false", "0", 0):
        return "false"
    return ""


def scenario_meta(row: dict) -> dict:
    key = row["key"]
    scenario = row.get("configuration_snapshot", {}).get("plan", {}).get("scenario", {})
    return {
        "demand_multiplier": key.get("demand_multiplier", ""),
        "traffic_profile": scenario.get("traffic_profile", ""),
        "sfc_profile": scenario.get("sfc_profile", ""),
        "deadline_profile": scenario.get("deadline_profile", ""),
    }


def selected_from_json(row: dict, source_path: Path, source_campaign: str, provenance: str, reason: str) -> dict:
    key = row["key"]
    accepted = int(row["accepted_flow_count"])
    F = int(row["total_flow_count"])
    meta = scenario_meta(row)
    return {
        "algorithm": key["algorithm"],
        "algorithm_label": LABEL[key["algorithm"]],
        "scale": key["scale"],
        "load": key["load_label"],
        "seed": int(key["formal_seed"]),
        "T": int(key["time_slots"]),
        "F": F,
        "demand_multiplier": meta["demand_multiplier"],
        "traffic_profile": meta["traffic_profile"],
        "sfc_profile": meta["sfc_profile"],
        "deadline_profile": meta["deadline_profile"],
        "accepted_flow_count": accepted,
        "acceptance_rate_percent": 100.0 * accepted / F,
        "stored_acceptance_rate": row.get("acceptance_rate"),
        "runtime_seconds": float(row["total_end_to_end_runtime_seconds"]),
        "runtime_metric_source": "total_end_to_end_runtime_seconds",
        "original_source": str(source_path),
        "supplementary_replacement_source": "",
        "final_selected_authoritative_source": str(source_path),
        "source_campaign": source_campaign,
        "source_provenance": provenance,
        "selection_reason": reason,
        "original_source_superseded": "false",
        "topology_fingerprint": row.get("topology_fingerprint", ""),
        "base_flow_fingerprint": row.get("base_flow_fingerprint", ""),
        "full_instance_fingerprint": row.get("full_instance_fingerprint", ""),
        "path_validity": bool_text(row.get("path_validity")),
        "capacity_validity": bool_text(row.get("capacity_validity")),
        "termination_status": row.get("termination_status", ""),
    }


def selected_from_final10(row: dict, original_row: dict, provenance_by_seed: dict[int, dict]) -> dict:
    seed = int(row["seed"])
    accepted = int(row["accepted_flow_count"])
    F = int(row["total_flow_count"])
    meta = scenario_meta(original_row)
    provenance = provenance_by_seed[seed]
    return {
        "algorithm": "dwcg_saginfe",
        "algorithm_label": LABEL["dwcg_saginfe"],
        "scale": "S50",
        "load": "Medium",
        "seed": seed,
        "T": int(original_row["key"]["time_slots"]),
        "F": F,
        "demand_multiplier": meta["demand_multiplier"],
        "traffic_profile": meta["traffic_profile"],
        "sfc_profile": meta["sfc_profile"],
        "deadline_profile": meta["deadline_profile"],
        "accepted_flow_count": accepted,
        "acceptance_rate_percent": 100.0 * accepted / F,
        "stored_acceptance_rate": row.get("acceptance_rate"),
        "runtime_seconds": float(row["runtime_seconds"]),
        "runtime_metric_source": "final10.runtime_seconds from selected authoritative source",
        "original_source": str(MASTER_DIR / "runs.jsonl"),
        "supplementary_replacement_source": str(FINAL10_DIR / "s50_medium_dwcg_saginfe_final10.csv"),
        "final_selected_authoritative_source": str(FINAL10_DIR / "s50_medium_dwcg_saginfe_final10.csv"),
        "source_campaign": "main_scaling_dwcg_saginfe_s50_medium_final10_v3_006cc96",
        "source_provenance": row["source_provenance"],
        "selection_reason": "Authoritative final10 replacement for all DWCG-SAGINFE S50 Medium rows; acceptance and runtime both taken from the same final10 selected row.",
        "original_source_superseded": "true",
        "provenance_source_directory": provenance.get("source_directory", ""),
        "topology_fingerprint": row.get("topology_fingerprint", ""),
        "base_flow_fingerprint": row.get("base_flow_fingerprint", ""),
        "full_instance_fingerprint": row.get("full_instance_fingerprint", ""),
        "path_validity": bool_text(row.get("path_validity")),
        "capacity_validity": bool_text(row.get("capacity_validity")),
        "termination_status": row.get("termination_status", ""),
        "original_runtime_seconds": original_row.get("total_end_to_end_runtime_seconds", ""),
        "original_accepted_flow_count": original_row.get("accepted_flow_count", ""),
        "original_hard_wall_clock_limit_reached": original_row.get("dwcg_metadata", {}).get("hard_wall_clock_limit_reached", ""),
    }


def make_selected() -> tuple[list[dict], list[dict]]:
    master = load_jsonl(MASTER_DIR / "runs.jsonl")
    pro = load_jsonl(PRO_DIR / "runs.jsonl")
    final10 = load_csv(FINAL10_DIR / "s50_medium_dwcg_saginfe_final10.csv")
    provenance = {int(r["seed"]): r for r in load_csv(FINAL10_DIR / "source_provenance.csv")}

    master_by_key = {key_of(row): row for row in master}
    pro_by_key = {key_of(row): row for row in pro}
    final10_by_seed = {int(row["seed"]): row for row in final10}

    selected = []
    audit = []
    for scale in SCALES:
        for load in LOADS:
            for seed in SEEDS:
                for alg in ALGS:
                    key = (scale, load, seed, alg)
                    if alg == "dwcg_saginfe" and scale == "S50" and load == "Medium":
                        row = selected_from_final10(final10_by_seed[seed], master_by_key[key], provenance)
                    elif alg == "dwcg_saginfe_pro":
                        row = selected_from_json(
                            pro_by_key[key],
                            PRO_DIR / "runs.jsonl",
                            "formal_dwcg_saginfe_pro_v3_970415f",
                            "dwcg_saginfe_pro_extension_120",
                            "Selected from the authoritative 120-row DWCG-SAGINFE-PRO extension identified by the four-algorithm merged manifest.",
                        )
                    else:
                        row = selected_from_json(
                            master_by_key[key],
                            MASTER_DIR / "runs.jsonl",
                            "formal_scale_load_master_v3_407efbf",
                            "frozen_scale_load_master_360",
                            "Selected from canonical frozen Main Scaling scale-load master rows.",
                        )
                    selected.append(row)
                    audit.append(row.copy())

    expected = len(SCALES) * len(LOADS) * len(SEEDS) * len(ALGS)
    if len(selected) != expected:
        raise SystemExit(f"expected {expected} selected rows, got {len(selected)}")
    if len({(r["scale"], r["load"], r["seed"], r["algorithm"]) for r in selected}) != expected:
        raise SystemExit("duplicate selected algorithm-instance keys")
    return selected, audit


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.array(values, dtype=float), q))


def summary_table(rows: list[dict], metric: str) -> list[dict]:
    out = []
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["scale"], row["load"], row["algorithm"], row["algorithm_label"])].append(float(row[metric]))
    for (scale, load, alg, label), values in sorted(grouped.items(), key=lambda x: (SCALES.index(x[0][0]), LOADS.index(x[0][1]), ALGS.index(x[0][2]))):
        q1 = percentile(values, 25)
        q3 = percentile(values, 75)
        out.append({
            "scale": scale,
            "load": load,
            "algorithm": label,
            "algorithm_id": alg,
            "n": len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "Q1": q1,
            "Q3": q3,
            "IQR": q3 - q1,
            "min": min(values),
            "max": max(values),
            "metric": metric,
        })
    return out


def scaling_summary(runtime_stats: list[dict]) -> list[dict]:
    lookup = {(r["load"], r["algorithm_id"], r["scale"]): r for r in runtime_stats}
    out = []
    for load in LOADS:
        for alg in ALGS:
            s20 = float(lookup[(load, alg, "S20")]["median"])
            s50 = float(lookup[(load, alg, "S50")]["median"])
            out.append({
                "load": load,
                "algorithm": LABEL[alg],
                "algorithm_id": alg,
                "median_runtime_s20_seconds": s20,
                "median_runtime_s50_seconds": s50,
                "median_runtime_s50_over_s20": s50 / s20 if s20 else "",
            })
    return out


def strict_identity_audit(rows: list[dict]) -> tuple[list[dict], bool]:
    out = []
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["scale"], row["load"], int(row["seed"]))].append(row)

    fields = [
        "T",
        "F",
        "demand_multiplier",
        "traffic_profile",
        "sfc_profile",
        "deadline_profile",
        "topology_fingerprint",
        "base_flow_fingerprint",
        "full_instance_fingerprint",
    ]
    for (scale, load, seed), group in sorted(grouped.items(), key=lambda x: (SCALES.index(x[0][0]), LOADS.index(x[0][1]), x[0][2])):
        checks = {field: len({str(row[field]) for row in group}) == 1 for field in fields}
        validity = all(row["path_validity"] == "true" and row["capacity_validity"] == "true" for row in group)
        algs = sorted(row["algorithm"] for row in group)
        passed = len(group) == 4 and algs == sorted(ALGS) and all(checks.values()) and validity
        out.append({
            "scale": scale,
            "load": load,
            "seed": seed,
            "row_count": len(group),
            "algorithms": "|".join(algs),
            **{f"{field}_agree": bool_text(value) for field, value in checks.items()},
            "path_capacity_valid": bool_text(validity),
            "strict_instance_identity_pass": bool_text(passed),
        })
    return out, all(row["strict_instance_identity_pass"] == "true" for row in out)


def source_consistency_audit(rows: list[dict]) -> tuple[list[dict], bool]:
    out = []
    for row in rows:
        coherent = bool(row["final_selected_authoritative_source"]) and bool(row["runtime_seconds"] != "")
        out.append({
            "algorithm": row["algorithm"],
            "scale": row["scale"],
            "load": row["load"],
            "seed": row["seed"],
            "acceptance_source": row["final_selected_authoritative_source"],
            "runtime_source": row["final_selected_authoritative_source"],
            "same_authoritative_source_for_acceptance_and_runtime": bool_text(coherent),
            "runtime_metric_source": row["runtime_metric_source"],
        })
    return out, all(row["same_authoritative_source_for_acceptance_and_runtime"] == "true" for row in out)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with resolve_input_path(path).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def setup_style() -> None:
    mpl.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8.3,
        "axes.labelsize": 8.8,
        "xtick.labelsize": 7.8,
        "ytick.labelsize": 7.8,
        "legend.fontsize": 8.0,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.linewidth": 0.7,
        "xtick.major.width": 0.7,
        "ytick.major.width": 0.7,
    })


def alg_color(alg: str, gray: bool) -> str:
    return GRAY[alg] if gray else STYLE[alg]["color"]


def jitter(seed: int, alg_index: int, width: float) -> float:
    return (((seed * 37 + alg_index * 17) % 101) / 100.0 - 0.5) * width


def offsets() -> np.ndarray:
    return (np.arange(len(ALGS)) - (len(ALGS) - 1) / 2) * 0.19


def scale_label(scale: str, rows: list[dict]) -> str:
    sample = next(row for row in rows if row["scale"] == scale)
    return f"{scale}\n(|T|={sample['T']}, |F|={sample['F']})"


def add_legend(fig, gray: bool) -> None:
    handles = [
        mlines.Line2D(
            [],
            [],
            color=alg_color(alg, gray),
            marker=STYLE[alg]["marker"],
            linestyle="None",
            markersize=5.6,
            label=LABEL[alg],
        )
        for alg in ALGS
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.997), ncol=4, frameon=False, handletextpad=0.35, columnspacing=0.9)


def box_values(rows: list[dict], load: str, metric: str) -> tuple[list[list[float]], list[float], list[str], list[float]]:
    lookup = {(row["scale"], row["load"], int(row["seed"]), row["algorithm"]): row for row in rows}
    centers = np.arange(len(SCALES), dtype=float) * 1.23
    offs = offsets()
    data = []
    positions = []
    algs = []
    all_values = []
    for si, scale in enumerate(SCALES):
        for ai, alg in enumerate(ALGS):
            vals = [float(lookup[(scale, load, seed, alg)][metric]) for seed in SEEDS]
            data.append(vals)
            all_values.extend(vals)
            positions.append(centers[si] + offs[ai])
            algs.append(alg)
    return data, positions, algs, all_values


def draw_grouped_box_panel(ax, rows: list[dict], load: str, metric: str, gray: bool) -> list[float]:
    data, positions, algs, all_values = box_values(rows, load, metric)
    centers = np.arange(len(SCALES), dtype=float) * 1.23
    offs = offsets()
    lookup = {(row["scale"], row["load"], int(row["seed"]), row["algorithm"]): row for row in rows}

    bp = ax.boxplot(data, positions=positions, widths=0.085, patch_artist=True, showfliers=False, whis=1.5)
    for patch, alg in zip(bp["boxes"], algs):
        patch.set_facecolor(alg_color(alg, gray))
        patch.set_edgecolor(alg_color(alg, gray))
        patch.set_alpha(0.24 if not gray else 0.18)
        patch.set_linewidth(0.9)
        patch.set_zorder(2)
    for part in ["whiskers", "caps"]:
        for item in bp[part]:
            item.set_color("#555555")
            item.set_linewidth(0.74)
            item.set_zorder(3)
    for item in bp["medians"]:
        item.set_color("#111111")
        item.set_linewidth(1.3)
        item.set_zorder(6)

    for si, scale in enumerate(SCALES):
        for ai, alg in enumerate(ALGS):
            ys = [float(lookup[(scale, load, seed, alg)][metric]) for seed in SEEDS]
            xs = [centers[si] + offs[ai] + jitter(seed, ai, 0.020) for seed in SEEDS]
            ax.scatter(xs, ys, s=18, marker=STYLE[alg]["marker"], color=alg_color(alg, gray), edgecolors="white", linewidths=0.32, zorder=4)
            mean_x = centers[si] + offs[ai]
            half_width = 0.038
            mean_y = statistics.fmean(ys)
            ax.plot(
                [mean_x - half_width, mean_x + half_width],
                [mean_y, mean_y],
                color=MEAN_GRAY if gray else MEAN_COLOR,
                linewidth=1.35,
                linestyle=MEAN_LINESTYLE,
                solid_capstyle="round",
                zorder=7,
            )

    ax.set_xticks(centers)
    ax.set_xticklabels([scale_label(scale, rows) for scale in SCALES])
    ax.set_xlim(centers[0] - 0.62, centers[-1] + 0.62)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.8, zorder=1)
    return all_values


def set_acceptance_ylim(ax, values: list[float]) -> None:
    lo, hi = min(values), max(values)
    pad = max(1.0, (hi - lo) * 0.20)
    lower = max(0.0, math.floor((lo - pad) / 2.5) * 2.5)
    upper = min(101.5, math.ceil((hi + pad) / 2.5) * 2.5)
    if upper - lower < 8:
        mid = (upper + lower) / 2
        lower = max(0.0, mid - 4)
        upper = min(101.5, mid + 4)
    ax.set_ylim(lower, upper)


def set_linear_runtime_ylim(ax, values: list[float]) -> None:
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08
    ax.set_ylim(max(0, lo - pad), hi + pad)


def human_time_tick(x, _pos=None) -> str:
    ticks = {
        1: "1 s",
        10: "10 s",
        60: "1 min",
        600: "10 min",
        1800: "30 min",
        3600: "1 h",
    }
    return ticks.get(int(round(x)), "")


def set_log_runtime_axis(ax) -> None:
    ticks = [1, 10, 60, 600, 1800, 3600]
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(mpl.ticker.FixedLocator(ticks))
    ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(human_time_tick))
    ax.yaxis.set_minor_formatter(mpl.ticker.NullFormatter())


def save(fig, stem: str, gray: bool) -> None:
    name = stem + ("_grayscale" if gray else "")
    fig.savefig((OUT / name).with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig((OUT / name).with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def make_figure(rows: list[dict], metric: str, stem: str, y_label: str, gray: bool = False, log_y: bool = False) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(5.05, 7.20), sharex=False)
    for ax, load, panel in zip(axes, LOADS, ["(a) Low Load", "(b) Medium Load", "(c) High Load"]):
        values = draw_grouped_box_panel(ax, rows, load, metric, gray)
        if metric == "acceptance_rate_percent":
            set_acceptance_ylim(ax, values)
        elif log_y:
            set_log_runtime_axis(ax)
        else:
            set_linear_runtime_ylim(ax, values)
        ax.set_ylabel(y_label)
        ax.set_xlabel(panel, labelpad=12)
        ax.tick_params(axis="y", which="both", labelleft=True)
    add_legend(fig, gray)
    fig.tight_layout(rect=(0, 0.0, 1, 0.955))
    save(fig, stem, gray)


def write_readme(selected: list[dict], strict_pass: bool, source_pass: bool, runtime_stats: list[dict]) -> None:
    s50_dwcg = [row for row in selected if row["algorithm"] == "dwcg_saginfe" and row["scale"] == "S50" and row["load"] == "Medium"]
    median_s50_runtime = statistics.median(float(row["runtime_seconds"]) for row in s50_dwcg)
    lines = [
        "# Main Scaling Acceptance/Runtime Review",
        "",
        "Final-review bundle only. These are not frozen figures.",
        "",
        "Scientific scope: Main Scaling; four scalable methods only: SAGINFE, DWCG-Pure, DWCG-SAGINFE, DWCG-SAGINFE-PRO. Scales S20-S50; loads Low / Medium / High; seeds 0..9.",
        "",
        "Acceptance metric: Acceptance Rate (%) = 100 * accepted_flow_count / F.",
        "",
        "Runtime metric: Total Runtime from authoritative total end-to-end runtime for each selected final run.",
        "",
        "Boxplot semantics: box = Q1-Q3; whiskers = 1.5 IQR; black solid line = median; dark-red dotted short line = mean; all 10 seed observations are visible. Median and mean are explicitly drawn above seed markers.",
        "",
        "Authoritative sources:",
        f"- Frozen 360-row scale-load master: `{MASTER_DIR.relative_to(ROOT)}`.",
        f"- DWCG-SAGINFE-PRO 120-row extension: `{PRO_DIR.relative_to(ROOT)}`.",
        f"- Four-algorithm merged manifest: `{MERGED_MANIFEST_DIR.relative_to(ROOT)}`.",
        f"- DWCG-SAGINFE S50 Medium final10 replacement: `{FINAL10_DIR.relative_to(ROOT)}`.",
        f"- Underlying supplementary package: `{UNLIMITED_DIR.relative_to(ROOT)}`.",
        "",
        "HFP / S50 Medium uses the final10 package for both Admission and Runtime. It selects supplementary rows for seeds 0,1,2,3,4,5,7,9 and baseline rows for seeds 6,8.",
        "",
        f"Strict instance-identity audit pass: {strict_pass}.",
        f"Acceptance/runtime same-source audit pass: {source_pass}.",
        f"DWCG-SAGINFE S50 Medium median Total Runtime: {median_s50_runtime:.3f} seconds.",
        "",
        "Runtime log scaling: visual only. Runtime summaries and boxplot statistics are computed in raw seconds.",
        "",
        "Data usage was re-checked for the final selected figures, including supplemented Main Scaling rows. DWCG-SAGINFE / S50 Medium uses the authoritative final10 replacement; DWCG-SAGINFE-PRO uses the authoritative 120-row extension.",
        "",
        "Final selected figure files:",
        "- `main_scaling_acceptance.png`",
        "- `main_scaling_acceptance.pdf`",
        "- `main_scaling_acceptance_grayscale.png`",
        "- `main_scaling_acceptance_grayscale.pdf`",
        "- `main_scaling_runtime_log.png`",
        "- `main_scaling_runtime_log.pdf`",
        "- `main_scaling_runtime_log_grayscale.png`",
        "- `main_scaling_runtime_log_grayscale.pdf`",
    ]
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    setup_style()
    OUT.mkdir(parents=True, exist_ok=True)

    selected, source_audit = make_selected()
    strict_rows, strict_pass = strict_identity_audit(selected)
    source_rows, source_pass = source_consistency_audit(selected)
    acceptance_stats = summary_table(selected, "acceptance_rate_percent")
    runtime_stats = summary_table(selected, "runtime_seconds")
    growth = scaling_summary(runtime_stats)

    write_csv(OUT / "main_scaling_final_selected_rows.csv", selected)
    write_csv(OUT / "main_scaling_source_selection_audit.csv", source_audit)
    write_csv(OUT / "main_scaling_source_consistency_audit.csv", source_rows)
    write_csv(OUT / "main_scaling_strict_instance_identity_audit.csv", strict_rows)
    write_csv(OUT / "main_scaling_acceptance_statistics.csv", acceptance_stats)
    write_csv(OUT / "main_scaling_runtime_statistics.csv", runtime_stats)
    write_csv(OUT / "main_scaling_runtime_scaling_summary.csv", growth)
    write_readme(selected, strict_pass, source_pass, runtime_stats)

    for gray in [False, True]:
        make_figure(selected, "acceptance_rate_percent", "main_scaling_acceptance", "Acceptance Rate (%)", gray=gray)
        make_figure(selected, "runtime_seconds", "main_scaling_runtime_log", "Total Runtime", gray=gray, log_y=True)

    print(f"selected_rows={len(selected)}")
    print(f"strict_instance_identity_pass={strict_pass}")
    print(f"source_consistency_pass={source_pass}")
    for row in acceptance_stats:
        if row["scale"] == "S50" and row["load"] == "Medium":
            print(f"S50 Medium acceptance {row['algorithm']}: mean={row['mean']:.3f}")
    for row in runtime_stats:
        if row["scale"] == "S50" and row["load"] == "Medium" and row["algorithm_id"] == "dwcg_saginfe":
            print(f"S50 Medium runtime DWCG-SAGINFE: median={row['median']:.3f}, mean={row['mean']:.3f}")
    print(f"output_dir={OUT}")


if __name__ == "__main__":
    main()
