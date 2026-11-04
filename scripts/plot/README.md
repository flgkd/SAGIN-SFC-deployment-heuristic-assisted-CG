# Plotting experimental results

## New executions

`plot_new_results.py` takes explicit `runs.jsonl` paths, validates per-method seed pairing and full instance fingerprints, rejects invalid runs and duplicate keys, and produces admission/runtime PDFs and PNGs in a new output directory. Run it from the repository root as described in the [protocol](../../docs/experiments/README.md#6-new-executions-and-outputs).

Exact-reference, cost-eligibility and speedup comparisons require their corresponding paired campaigns and certificate/eligibility fields. Supply every planned comparison arm; a one-seed output is an execution check rather than a distributional scientific result.

## Supplementary figures

[regenerate_supplementary.py](regenerate_supplementary.py) renders the seven supplementary figures from explicit numerical inputs: S40/S50 component admission and runtime, deadline sensitivity, operational temporal scalability, and parallel-pricing runtime. It uses the original drawing routines with the manuscript's algorithm names, preserves the selected observations and visual identities, and exports vector PDFs plus lossless PNG previews at 450 DPI.

```powershell
python scripts/plot/regenerate_supplementary.py --data-root "C:\path\to\experiment-archive" --output-dir generated/supplementary
```

The input directory is read-only; output must be outside it. No optimization is performed. Python plotting dependencies are listed in [requirements.txt](../../requirements.txt); PNG export also requires `pdftoppm` on `PATH`. The renderer validates the campaign matrices and records input SHA-256 fingerprints. The [figure-input manifest](../../figures/supplementary/figure-inputs.json) identifies the exact tables used by the published supplementary assets. Numerical tables are not distributed with this repository.

## Specialized plotting sources

`historical/` contains the specialized drawing and aggregation routines for main scaling, split ILP references, component ablations, deadline sensitivity, operational horizon, fixed-request control, pricing tradeoffs and parallel pricing. Algorithm labels follow the shared [naming definitions](../../algorithm_names.py). These sources retain campaign-specific joins and input layouts; use the supplementary command above for its seven supported figures. Other layouts require the corresponding original numerical inputs and explicit path adaptation before execution.

The [source manifest](../../docs/source-manifest.json) records current-file SHA-256 hashes for its listed repository paths. It verifies those files, not historical numerical inputs; Git records revisions. No per-seed tables, JSONL records, solver logs or caches are included.
