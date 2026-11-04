"""Render the seven supplementary figures from explicit, read-only numerical inputs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from record_compatibility import normalize_dataframe
import pandas as pd

SOURCES = {
    "component_s40": "results/frozen/component_ablation_s40_v3/component_ablation_seed_level.csv",
    "component_s50": "results/frozen/component_ablation_s50_v3_final/canonical_component_ablation_s50_180.csv",
    "deadline": "outputs/deadline_sensitivity_s40_v3_120_36b9632/deadline_sensitivity_seed_level.csv",
    "temporal": "outputs/temporal_scalability_v3_160_0caacc9/temporal_scalability_seed_level.csv",
    "parallel": "outputs/parallel_pricing_v3_60_4796b5b/parallel_pricing_seed_level.csv",
}


def load_module(name):
    path = Path(__file__).with_name("historical") / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def capture(call):
    """Reuse the original drawing function without its file-writing side effects."""
    figures = []
    with patch.object(Figure, "savefig", lambda fig, *a, **kw: figures.append(fig)), patch.object(plt, "close"):
        call()
    if not figures or any(fig is not figures[0] for fig in figures):
        raise ValueError("Expected one figure")
    return figures[0]


def build_figures(data_root, output_dir):
    """Yield (stem, figure, PDF save options); no optimizer is invoked."""
    zoom = load_module("generate_component_ablation_acceptance_zoomed")
    for scale in ("s40", "s50"):
        config = replace(getattr(zoom, scale.upper()), source=data_root / SOURCES["component_" + scale], output_dir=output_dir)
        rows = zoom.normalized_rows(config)
        fig = capture(lambda: zoom.plot_acceptance(config, rows, False))
        yield "component_ablation_" + scale + "_acceptance_zoomed", fig, {"bbox_inches": "tight"}
        runtime = load_module("generate_component_ablation_final_figures" if scale == "s40" else "generate_component_ablation_s50_figures")
        runtime.OUT = output_dir
        if scale == "s40":
            runtime.SEED_LEVEL = config.source
            runtime.MANIFEST = config.source.with_name("component_ablation_manifest.json")
        else:
            runtime.SOURCE = config.source
        rows = runtime.load_rows()
        fig = capture(lambda: runtime.plot_figure(rows, metric="runtime", grayscale=False))
        yield "component_ablation_" + scale + "_runtime", fig, {"bbox_inches": "tight"}

    deadline = load_module("generate_deadline_sensitivity_s40_final")
    frame = normalize_dataframe(pd.read_csv(data_root / SOURCES["deadline"]))
    # The full profile/validity checks are retained in the source helper.
    deadline.v1.SOURCE_CSV = data_root / SOURCES["deadline"]
    deadline.v1.FROZEN_MANIFEST = data_root / "results/frozen/deadline_sensitivity_s40_v3/deadline_sensitivity_manifest.json"
    deadline.v1.PROFILE_MANIFEST = data_root / "results/frozen/deadline_sensitivity_s40_v3/deadline_profile_manifest.json"
    deadline.v1.validate(frame, hashlib.sha256(deadline.v1.SOURCE_CSV.read_bytes()).hexdigest())
    yield "deadline_sensitivity_s40", deadline.make_figure(frame, False), {}

    temporal = load_module("generate_temporal_scalability_s40_final")
    temporal.SOURCE = data_root / SOURCES["temporal"]
    frame, _ = temporal.load_and_validate()
    yield "operational_temporal_scalability_s40", temporal.create_figure(frame, False), {"bbox_inches": "tight", "pad_inches": 0.018}

    parallel = load_module("generate_parallel_pricing_runtime")
    parallel.SOURCE_CSV = data_root / SOURCES["parallel"]
    frame, _ = parallel.load_and_validate()
    yield "parallel_pricing_runtime", parallel.make_runtime_figure(frame, False), {"bbox_inches": "tight", "pad_inches": 0.018}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated/supplementary")
    parser.add_argument("--dpi", type=int, default=450)
    args = parser.parse_args()
    source = args.data_root.resolve()
    destination = args.output_dir.resolve()
    if source == destination or source in destination.parents:
        parser.error("Output must be outside the read-only data root")
    destination.mkdir(parents=True, exist_ok=True)
    fingerprints = {key: {"path": value, "sha256": hashlib.sha256((source / value).read_bytes()).hexdigest(), "rows": len(pd.read_csv(source / value))} for key, value in SOURCES.items()}
    for stem, figure, options in build_figures(source, destination):
        pdf = destination / (stem + ".pdf")
        figure.savefig(pdf, **options)
        plt.close(figure)
        with tempfile.TemporaryDirectory(dir=destination) as scratch:
            prefix = Path(scratch) / "page"
            subprocess.run(["pdftoppm", "-r", str(args.dpi), str(pdf), str(prefix)], check=True, stdout=subprocess.DEVNULL)
            pages = list(Path(scratch).glob("*.ppm"))
            if len(pages) != 1:
                raise ValueError("Expected one PDF page")
            with Image.open(pages[0]) as image:
                image.save(destination / (stem + ".png"), optimize=True)
        print(stem)
    (destination / "figure-inputs.json").write_text(json.dumps(fingerprints, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
