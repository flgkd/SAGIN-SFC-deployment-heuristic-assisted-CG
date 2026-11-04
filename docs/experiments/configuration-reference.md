# Configuration reference

[Protocol](README.md) | [Algorithms](../algorithms/README.md) | [Reproducibility](../reproducibility.md)

## Shared formal settings

Formal plans use no overall end-to-end runtime limit. `hard_time_limit_seconds` and the algorithm-level `time_limit` are null. `disable_hard_time_limit` is an explicit override supported by cold admission ILP and HFP. Solver statuses and per-run configuration snapshots remain part of each execution record.

| Setting | Formal value / meaning |
|---|---|
| CG rounds | At most 12 |
| Exact-pricing calls | S20: 1040; S30: 1560; S40: 2080; S50: 2600, equal to 13 times request count |
| Final integer RMP | Independent 300-second cap for DWCG variants |
| Pricing schedule | HYB-S5 by default; EX, ESV and HYB-S2/S5/S7 in schedule studies |
| Solver threads | One per worker |
| Pricing workers | One by default; 1/2/4 in the parallel study |
| Formal seeds | 0-9 unless an explicit supplementary seed subset is listed |
| Reserve fields | Compatibility parameters; inactive when no overall deadline is supplied |

Per-pricing stopping rules, reduced-cost tolerances, selective-PFE limits and incumbent safeguards are described in [Algorithms](../algorithms/README.md). The linked JSON is authoritative for each executable plan. [Algorithm configurations](../algorithms/README.md#1-names-and-configurations) define the compared methods. A plan specifies executions, not proof of their completion or a snapshot of every observation in a supplied figure.

## Formal plans

| JSON configuration | Algorithms / variants | Scales | Loads | Seeds |
|---|---|---|---|---|
| [direct_ilp_acceptance_cold_s20_medium_unlimited_v3](../../configs/formal/direct_ilp_acceptance_cold_s20_medium_unlimited_v3.json) | ILP-AdmissionOnly | S20 | Medium | 0, 1, 2, 3, 5, 6 |
| [direct_ilp_acceptance_cold_s30_medium_unlimited_v3](../../configs/formal/direct_ilp_acceptance_cold_s30_medium_unlimited_v3.json) | ILP-AdmissionOnly | S30 | Medium | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [direct_ilp_full_objective_cold_s20_low_seed0_v3](../../configs/formal/direct_ilp_full_objective_cold_s20_low_seed0_v3.json) | ILP-FullObjective | S20 | Low | 0 |
| [direct_ilp_full_objective_cold_s20_low_seeds1_9_v3](../../configs/formal/direct_ilp_full_objective_cold_s20_low_seeds1_9_v3.json) | ILP-FullObjective | S20 | Low | 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [direct_ilp_full_objective_cold_s30_low_seed0_v3](../../configs/formal/direct_ilp_full_objective_cold_s30_low_seed0_v3.json) | ILP-FullObjective | S30 | Low | 0 |
| [direct_ilp_full_objective_cold_s30_low_seeds1_9_v3](../../configs/formal/direct_ilp_full_objective_cold_s30_low_seeds1_9_v3.json) | ILP-FullObjective | S30 | Low | 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_dwcg_saginfe_pro_v3](../../configs/formal/formal_dwcg_saginfe_pro_v3.json) | PRO | S20, S30, S40, S50 | Low, Medium, High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_component_ablation_s40_v3](../../configs/formal/formal_component_ablation_s40_v3.json) | Warm, HP, PRO-no-PFE | S40 | Low, Medium, High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_component_ablation_s50_extension_v3](../../configs/formal/formal_component_ablation_s50_extension_v3.json) | Warm, HP, PRO-no-PFE | S50 | Low, Medium, High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_deadline_sensitivity_s40_v3](../../configs/formal/formal_deadline_sensitivity_s40_v3.json) | SAGINFE, Pure, HFP, PRO | S40 | Medium | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_direct_ilp_acceptance_cold_s20_s30_v3](../../configs/formal/formal_direct_ilp_acceptance_cold_s20_s30_v3.json) | ILP-AdmissionOnly | S20, S30 | Low, Medium, High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_parallel_pricing_v3](../../configs/formal/formal_parallel_pricing_v3.json) | Pure / W1, Pure / W2, Pure / W4, PRO / W1, PRO / W2, PRO / W4 | S40 | High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_pricing_schedule_v3](../../configs/formal/formal_pricing_schedule_v3.json) | Pure / EX, Pure / ESV, Pure / HYB-S2, Pure / HYB-S5, Pure / HYB-S7 | S40 | Medium, High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_pro_pricing_schedule_v3](../../configs/formal/formal_pro_pricing_schedule_v3.json) | PRO / EX, PRO / ESV, PRO / HYB-S2, PRO / HYB-S5, PRO / HYB-S7 | S40 | Medium, High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_same_flow_temporal_control_hfp_extension_v3](../../configs/formal/formal_same_flow_temporal_control_hfp_extension_v3.json) | HFP | S40 | Medium | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_same_flow_temporal_control_v3](../../configs/formal/formal_same_flow_temporal_control_v3.json) | SAGINFE, Pure, PRO | S40 | Medium | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_scale_load_master_v3](../../configs/formal/formal_scale_load_master_v3.json) | SAGINFE, Pure, HFP | S20, S30, S40, S50 | Low, Medium, High | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [formal_temporal_scalability_v3](../../configs/formal/formal_temporal_scalability_v3.json) | SAGINFE, Pure, HFP, PRO | S40 | Medium | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| [main_scaling_dwcg_saginfe_s50_medium_unlimited_timeout8_v3](../../configs/formal/main_scaling_dwcg_saginfe_s50_medium_unlimited_timeout8_v3.json) | HFP | S50 | Medium | 0, 1, 2, 3, 4, 5, 7, 9 |

## Diagnostics and templates

- [Pilot configurations](../../configs/pilot/) vary CG rounds and final-RMP budgets to test execution behavior; they are not the formal comparison matrix.
- [Preflight configurations](../../configs/preflight/) exercise small filtered cases with formal parameters.
- [Version 1 templates](../../configs/templates/formal/) preserve separate compatibility examples and explicit diagnostic budgets. Use the formal plans above for the manuscript protocol.

The specialized campaign runners implement pairing and reuse rules. For commands, output records and the steps needed to reproduce a complete comparison, see [new executions and outputs](README.md#6-new-executions-and-outputs).
