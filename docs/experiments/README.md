# Experimental protocol

[Repository](../../README.md) · [Results](results.md) · [Reproducibility](../reproducibility.md)

The manuscript defines the reported findings, and the JSON plans specify the executable protocol. Retain each new run's configuration snapshot, code revision, environment and stopping status. Configuration files describe new executions; they are not retrospective snapshots of every plotted observation.

## 1. Synthetic topology

| Scale | Space | Air | Ground | Ground regions | Requests | Default slots |
|---|---:|---:|---:|---:|---:|---:|
| S20 | 8 | 4 | 8 | 4 | 80 | 8 |
| S30 | 12 | 6 | 12 | 4 | 120 | 8 |
| S40 | 16 | 8 | 16 | 4 | 160 | 8 |
| S50 | 20 | 10 | 20 | 4 | 200 | 8 |

The candidate physical graph is deterministic given scale. Let ground region size be $q=|N_g|/4$, ground node index $\ell$, region $\rho=\lfloor\ell/q\rfloor$, and within-region position $k=\ell\bmod q$. Layer-local indices start at zero.

| Link class | Candidate undirected connections |
|---|---|
| SS | Space node $i$ to $`(i+1)\bmod\vert N_s\vert`$ and $`(i+2)\bmod\vert N_s\vert`$ |
| SA | Air node $j$ to space nodes $2j$ and $`(2j+\vert N_s\vert /2)\bmod\vert N_s\vert`$ |
| AA | Ring joining air node $j$ to $`(j+1)\bmod\vert N_a\vert`$ |
| AG | Ground node $\ell$ to air nodes $\rho$ and $`(\rho+1+k)\bmod\vert N_a\vert`$ |
| GG | Ring within each ground region; duplicate unordered pairs removed |

There are no direct SG or inter-region GG candidates. S20 has 48 undirected connections: 16 SS, 8 SA, 4 AA, 16 AG, 4 GG. [sagin_generator.py](../../sagin_generator.py) constructs the graph and temporal expansion; [config_schema.py](../../config_schema.py) specifies profiles.

| Resource/class | Base capacity, inclusive uniform integers | Per-slot retention probability |
|---|---|---:|
| Space node | 64–96 | All nodes retained |
| Air node | 24–48 | All nodes retained |
| Ground node | 80–120 | All nodes retained |
| SS | 64–96 | 0.95 |
| SA | 28–56 | 0.85 |
| AA | 16–40 | 0.80 |
| AG | 21–42 | 0.80 |
| GG | 64–120 | 0.95 |

Each slot samples a fresh snapshot from the same candidate graph independently of previous snapshots. Disconnected snapshots are discarded and resampled, with at most 100 attempts; retention probabilities precede conditioning on connectivity. Base capacities receive independent uniform multipliers in $[0.9,1.1]$ per slot. Each active connection becomes two directed arcs with equal but separately accounted capacities. Capacities and demands use consistent synthetic model units, not calibrated measurements of a particular constellation. The complete sequence is available before optimization.

## 2. Requests and deadlines

Request count is $4|N|$. Integer quotas enforce 70% cross-region ground–ground, 20% same-region ground–ground and 10% ground–nonground endpoint pairs. Chain-length quotas allocate two, three and four actual processing stages in proportions 0.2, 0.5 and 0.3. The manuscript's extended length includes two dummy endpoints and is therefore 4, 5 or 6.

Types are sampled with replacement from `FW`, `NAT`, `DPI`, `IDS`, `CACHE`, `TRANSCODER`. Each actual stage independently receives a coefficient uniformly in $[2.5,5.0]$. Base request amounts are uniform integers 12–24. Load multipliers are **Low 0.4, Medium 0.6, High 0.8**. Pairing retains the base request bundle and scales demand; use base-flow and topology fingerprints when comparing loads.

| Code profile | Figure label | Deadline offset distribution |
|---|---|---|
| `Flexible` | Loose | 3, 4, 5 with probabilities 0.3, 0.4, 0.3 |
| `Balanced` | Balanced | 2, 3, 4 with probabilities 0.3, 0.4, 0.3 |
| `RealTime` | Tight | 1, 2 with probabilities 0.5, 0.5 |

In the ordinary generator, after sampling offset $\Delta$, code arrival is uniform on $`\{0,\ldots,H-1-\Delta\}`$ and deadline is arrival plus offset. Feasibility is inclusive, so offset $\Delta$ spans $\Delta+1$ slot labels. [flow_generator.py](../../flow_generator.py) implements quotas and request generation. Specialized paired builders have additional coupling rules and must be used for their campaigns.

## 3. Seeds and pairing

Formal seeds are 0–9, calibration seeds 100–109, integration seed 999, and pilot seed 900. [instance_builder.py](../../instance_builder.py) derives topology and flow seeds from the scenario seed and namespaces. [deterministic_seeds.py](../../deterministic_seeds.py) derives stable algorithm/Gurobi seeds. Do not replace these with process-dependent Python `hash()`.

All methods in a comparison receive identical instances. Preserve `topology_fingerprint`, `base_flow_fingerprint`, `full_instance_fingerprint` and specialized builder fingerprints. Seed equality alone does not prove instance identity across campaigns. `formal_runner` also records configuration snapshots and validates returned solutions.

## 4. Campaigns and executable configurations

The [configuration reference](configuration-reference.md) summarizes per-algorithm settings and links to each executable plan. The following table identifies the scientific role of each plan.

| Study | Configuration | Design |
|---|---|---|
| Main baseline | [scale-load master](../../configs/formal/formal_scale_load_master_v3.json) | Four scales × three loads × ten seeds × SAGINFE/Pure/HFP = 360 planned runs |
| PRO extension | [PRO](../../configs/formal/formal_dwcg_saginfe_pro_v3.json) | Same 120 settings, PRO only |
| Admission reference | [cold ILP](../../configs/formal/formal_direct_ilp_acceptance_cold_s20_s30_v3.json) | Plan covers S20/S30 Low/Medium/High; only certified Low/Medium rows are used in the paper; Medium certification plans also retained |
| Full-objective reference | [S20 seed 0](../../configs/formal/direct_ilp_full_objective_cold_s20_low_seed0_v3.json), [S20 seeds 1–9](../../configs/formal/direct_ilp_full_objective_cold_s20_low_seeds1_9_v3.json), [S30 seed 0](../../configs/formal/direct_ilp_full_objective_cold_s30_low_seed0_v3.json), [S30 seeds 1–9](../../configs/formal/direct_ilp_full_objective_cold_s30_low_seeds1_9_v3.json) | Ten Low-load instances per scale; no Medium cost certificate |
| S40 components | [S40 ablation](../../configs/formal/formal_component_ablation_s40_v3.json) | Warm/HP/PRO-no-PFE additions complement reused baseline rows |
| S50 components | [S50 extension](../../configs/formal/formal_component_ablation_s50_extension_v3.json) | 90 new and 90 reused rows form six variants × three loads × ten seeds |
| Deadline sensitivity | [deadline plan](../../configs/formal/formal_deadline_sensitivity_s40_v3.json) | S40 Medium, 160 requests, eight slots, three profiles, four methods |
| Operational horizon | [temporal plan](../../configs/formal/formal_temporal_scalability_v3.json) | S40 Medium, 160 requests, horizons 6/8/10/12, four methods |
| Fixed-request horizon | [same-flow plan](../../configs/formal/formal_same_flow_temporal_control_v3.json), [HFP extension](../../configs/formal/formal_same_flow_temporal_control_hfp_extension_v3.json) | Six-slot requests fixed; common 12-slot topology prefixes |
| Pure pricing schedules | [pricing plan](../../configs/formal/formal_pricing_schedule_v3.json) | S40 Medium/High; EX, ESV, HYB-S2/S5/S7 |
| PRO pricing schedules | [PRO pricing](../../configs/formal/formal_pro_pricing_schedule_v3.json) | Same schedules with PRO components |
| Parallel pricing | [parallel plan](../../configs/formal/formal_parallel_pricing_v3.json) | S40 High, Pure/PRO, 1/2/4 workers, ten seeds = 60 runs |

Formal runs have no overall end-to-end time limit; pricing-specific limits and the independent 300-second final integer-RMP cap remain in force.

Default CG work limits are 12 rounds and $13|R|$ solver-pricing calls: 1040/1560/2080/2600 at S20/S30/S40/S50. One thread and one pricing worker are standard. HYB-S5, strict/early solver tolerances, selective-PFE caps and candidate acceptance are specified in [Algorithms](../algorithms/README.md). Consult resolved metadata for final-RMP settings; the PRO campaign records a relative gap of 0.0001 for final integer recovery. A requested tolerance is not evidence that it was reached.

## 5. Deadline and temporal controls

[paired_deadline_builder.py](../../paired_deadline_builder.py) retains deadline-invariant request fields and paired latent deadline randomness across profiles. Its fingerprint and clipping metadata distinguish deadline changes from changes in topology, endpoints or processing requirements.

[paired_temporal_builder.py](../../paired_temporal_builder.py) uses a common physical base and temporal prefixes with paired timing variables, but request timing changes with the operational horizon. Admission gains in this study cannot be attributed solely to adding graph nodes.

[same_flow_temporal_control_builder.py](../../same_flow_temporal_control_builder.py) fixes the complete six-slot request bundle and uses prefixes of a common 12-slot topology. All service windows stay in the first six slots. Extra copies enlarge supplied models without extending deadlines. This measures the current implementation's graph-size sensitivity under finite computation. Neither temporal study supports direct normalized-cost comparisons across horizons because denominators change.

## 6. New executions and outputs

Run the following commands for full campaigns. Use filters for a small execution check before launching the complete matrix.

```powershell
python run_formal_benchmark.py --plan configs/formal/formal_scale_load_master_v3.json --output outputs/new_master --yes
python run_formal_benchmark.py --plan configs/formal/formal_dwcg_saginfe_pro_v3.json --output outputs/new_pro --yes
python scripts/run_deadline_sensitivity_s40_v3.py --config configs/formal/formal_deadline_sensitivity_s40_v3.json --output-dir outputs/new_deadlines
python scripts/run_temporal_scalability_v3.py --config configs/formal/formal_temporal_scalability_v3.json --output-dir outputs/new_temporal
python scripts/run_same_flow_temporal_control_v3.py --config configs/formal/formal_same_flow_temporal_control_v3.json --output-dir outputs/new_same_flow
python scripts/run_parallel_pricing_v3.py --config configs/formal/formal_parallel_pricing_v3.json --output-dir outputs/new_parallel
python scripts/run_pricing_schedule_v3.py --config configs/formal/formal_pricing_schedule_v3.json --output-dir outputs/new_schedules
python scripts/run_pro_pricing_schedule_v3.py --config configs/formal/formal_pro_pricing_schedule_v3.json --output-dir outputs/new_pro_schedules
```

Use `--help` for filters, confirmation and resume options. Specialized launchers preserve reuse policies: schedule runners normally exclude reused HYB-S5, and the fixed-request runner normally excludes reused T6. Their explicit diagnostic flags permit fresh execution (`--include-reused-reference`, `--include-reused-t6`), which must be labeled new results. A complete new comparison requires regenerating all arms and joining instance fingerprints; withheld historical rows cannot fill missing cells.

The runner writes `runs.jsonl`, manifests, summaries and validation metadata beneath the chosen output directory. Resume is not permission to silently mix configurations. `scripts/create_*_comparison.py` retains original aggregation logic and historical assumptions; inspect its options and input schemas before adapting it. The portable new-results plotting command is:

```powershell
python scripts/plot/plot_new_results.py --input outputs/new_master/runs.jsonl outputs/new_pro/runs.jsonl --output generated/new_comparison
```

Plotting checks duplicate scientific keys, solution validity and instance fingerprints before grouping. Historical layout scripts under `scripts/plot/historical/` require withheld inputs and are documented separately.

## 7. Metrics and analysis

Admission ratio is $100A/|R|$. Physical cost and the admission-first objective follow [Model equation (11)](../model/README.md#5-admission-first-cost-aware-objective). Runtime excludes instance generation and includes model construction, initialization, master solves, pricing, worker startup/dispatch and integer recovery as applicable. Preserve the reported end-to-end field rather than substituting solver-only time.

The main summaries use mean admission and median runtime. Box plots show quartiles, whiskers extend to 1.5 IQR, seed points remain visible, black marks indicate medians and dotted red marks means; they are descriptive, not confidence intervals. Parallel speedup is calculated for each seed against its corresponding one-worker run before aggregation. A ratio of aggregate times is a different statistic.

Cost comparisons use common full-admission instances where the admitted request set is fixed. Equal admitted counts alone need not mean equal request sets. Admission-only ILP does not certify its displayed cost. Invalid solutions, incomplete pairs and failed runs must remain explicit rather than becoming zeros or being silently discarded.
