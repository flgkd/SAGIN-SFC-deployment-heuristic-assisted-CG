# Algorithms and orchestration

[Mathematical model](../model/README.md) · [Experimental protocol](../experiments/README.md) · [Results](../experiments/results.md)

This chapter explains the implemented framework accompanying **Scalable Full-Horizon SFC Deployment over Predictable SAGIN Topologies via Configurable Column Generation Framework**. The [model chapter](../model/README.md) defines request feasibility, column coefficients, the master problem, and reduced cost. Here the emphasis is on constructing deployments, coordinating candidate generation with optimization, and interpreting termination and solution guarantees.

Equations in this document are numbered sequentially in their order of appearance. The numbering is local to this document and independent of the manuscript.

## 1. How the components fit together

The framework has three computational stages: initialization, LP-based column generation, and finite-pool integer selection. SAGINFE can supply a complete feasible initial solution by processing requests sequentially with Per-Flow Embedding (PFE). Each accepted request contributes one complete deployment column. Every request also has a rejection column, so the restricted master problem (RMP) remains feasible even without admitted alternatives.

The LP-RMP jointly allocates weights to the current columns under shared capacities. Its dual prices define a request-specific pricing objective. Pricing seeks additional complete deployments; it does not commit shared resources. Accepted columns enlarge the pool, and the next master solve can reconsider both resource allocation and admission. This separation permits alternatives for requests initially rejected by SAGINFE, and replacements for requests it initially accepted.

SAGINFE and PFE are therefore not independent alternatives to every part of column generation. PFE is the single-request construction procedure used by SAGINFE, by heuristic-first pricing, and by PRO's selective supplementary search. These calls differ principally in the capacities supplied and the acceptance checks applied afterward. The pricing MIP is a different candidate generator, optimizing the current dual-adjusted objective. Final integer selection converts the fractional master relaxation into at most one executable deployment per request.

```text
network, requests, objective, configuration
    -> rejection columns for every request
    -> optional SAGINFE -> admitted initial columns and feasible incumbent
    -> repeat:
           optimal LP-RMP -> fixed dual prices
           configured request-wise pricing -> candidate deployments
           extraction, usage/cost/reduced-cost checks, duplicate checks
           optional selective PFE -> the same insertion checks
           update pool; assess certificate, no progress, and work limits
    -> reuse a validated integral LP selection, or solve integer RMP
    -> validate aggregate deployment; apply configured incumbent safeguard
    -> report solution together with termination reason and claim scope
```

There is no branch-and-price implementation. Pricing stops before the finite-pool integer search; columns are not generated inside its branching tree.

## 2. SAGINFE: sequential feasible construction

### 2.1 Ordering and resource commitment

SAGINFE starts with copies of the original node and directed-link capacities. It stably sorts requests by ascending chain length and then data amount. Adding two dummy stages does not change the ordering by chain length. Exact ties preserve the incoming list order. This rule gives shorter, smaller requests earlier opportunities to fit; it is a construction choice, not a proof of maximum admission.

For each request, PFE searches using the capacities left by previously accepted requests. Its tentative records use independent resource copies. If it cannot complete the entire chain and destination segment, it returns failure and the input capacities remain unchanged. On success, SAGINFE commits the final resource state returned by PFE. Earlier accepted placements and routes remain fixed for the rest of this construction pass: there is no backtracking across requests or joint rerouting at this stage.

```text
SAGINFE(network, requests, eligible_hosts, destination_candidates):
    residual_node := copy(original node capacities)
    residual_edge := copy(original directed-edge capacities)
    results := empty mapping keyed by request ID
    ordered_requests := stable_sort(requests, chain_length, data_amount)

    for r in ordered_requests:
        placements, segments, next_node, next_edge := PFE(
            network, r, residual_node, residual_edge,
            eligible_hosts, destination_candidates)
        if placements is not None and segments is not None:
            results[r] := (placements, segments)
            residual_node, residual_edge := next_node, next_edge
        else:
            results[r] := rejection
            # PFE returned the unchanged input capacity state

    return results

Adapter after construction:
    encode each admitted result as a Path with stage/segment identities
    finalize usage and calculate normalized physical cost
    validate paths and aggregate capacities; calculate objective
```

Success is tested against `None`, not dictionary truthiness; a valid result must not be rejected merely because a representation is empty. The construction's resource checks establish feasibility incrementally, and the adapter performs explicit validation before using the results. In [sgfe_solver.py](../../sgfe_solver.py), `sgfe` is the SAGINFE entry point and `pfe` is its request constructor. [formal_runner.py](../../formal_runner.py) performs the standalone evaluation and initialization plumbing.

### 2.2 Standalone output and initial columns

As a standalone heuristic, SAGINFE returns its admitted set and complete deployments. Its objective is evaluated from actual resource usage, not from PFE's accumulated score. Rejection means this construction did not find a feasible extension against its current residual state; it does not establish that the original request is infeasible under a different allocation or order.

For column-generation initialization, `build_sgfe_initial_paths_from_results` converts accepted deployments without rerunning construction. Rejected requests receive no admitted initial column, but retain their rejection column. Selecting the SAGINFE column for each accepted request and rejection for the rest reproduces a jointly feasible integer master solution. The pool can subsequently contain mutually conflicting alternatives, because feasibility is then coordinated through master selection rather than sequential commitment.

## 3. PFE: stage-wise request embedding

### 3.1 Inputs, candidate locations, and retained records

PFE receives the network, one `Flow`, node and edge capacity dictionaries, permitted physical VNF hosts, and destination candidates. The published default adapters pass all physical nodes as potential hosts; computing feasibility determines which can execute a positive-demand stage. The destination candidates are filtered to the request's specified physical destination. Outputs are actual-stage placements, ordered segment routes, and the final capacity dictionaries, or failure with unchanged inputs.

The following notation supplements the model chapter and follows the manuscript's PFE section. It is local to the PFE construction.

| Symbol | Meaning | Code representation |
|---|---|---|
| $`N_r^T`$ | Time nodes inside the request's inclusive arrival–deadline window | Candidate-list filtering |
| $`A_{r,k}`$ | Candidate locations for extended stage $`k`$ | `all_candidates[k]` |
| $`a',a,a^*`$ | A predecessor, a target, and the selected predecessor | `a_prime`, `a`, `a_star` |
| $`\mathcal L_r^{k,a}`$ | Retained partial-deployment record ending at stage $`k`$, location $`a`$ | `records[(k,a)]` |
| $`c_r^{k,a},d_r^{k,a}`$ | Residual node and directed-edge capacities belonging to that record | Record dictionaries `c`, `D` |
| $`\xi_r^{k,a}`$ | Accumulated residual-ratio heuristic score | Record field `xi` |
| $`\lambda_{r,\mathrm{node}}^{k,a',a}`$ | Computing score for placing stage $`k`$ at $`a`$ from predecessor $`a'`$ | `_comp_ratio_cost` |
| $`u_r^{k,a'}(e)`$ | Routing edge weight for predecessor $`a'`$ | `_build_edge_ratio_costs` |
| $`\varphi_r^{k,a',a}`$ | Reconstructed route connecting predecessor to target | `seg` |
| $`\lambda_{r,\mathrm{link}}^{k,a',a}`$ | Sum of edge weights on that route | Dijkstra distance |

For default unrestricted hosts, the candidate sets are

```math
A_{r,k}=\begin{cases} \{n_{r,o}^{t_{r,o}}\}, & k=0,\\ N_r^T, & 1\le k\le |V_r|,\\ \{n_{r,d}^{t}:t_{r,o}\le t\le t_{r,d}^{\max}\}, & k=|V_r|+1. \end{cases} \qquad \text{(1)}
```

Only retained predecessor records can be extended. The source record has score zero and copies of the supplied capacities. Each later record stores its own residual dictionaries, the preceding record's key, the current stage placement, and the connecting route. Complete placements and routes are recovered once by traceback. This avoids repeatedly copying the entire deployment history, although residual capacity dictionaries are still copied for retained targets.

### 3.2 Processing feasibility and routing scores

For a predecessor record at $`a'`$ and target $`a`$, the following three statements jointly define the computing increment (2); exactly one condition applies:

```math
\lambda_{r,\mathrm{node}}^{k,a',a}=0,\qquad\text{if }f_r\delta_{v_{r,k}}=0;
```

```math
\lambda_{r,\mathrm{node}}^{k,a',a}=\frac{\psi f_r\delta_{v_{r,k}}}{c_r^{k-1,a'}(a)},\qquad\text{if }0\lt f_r\delta_{v_{r,k}}\le c_r^{k-1,a'}(a);
```

```math
\lambda_{r,\mathrm{node}}^{k,a',a}=\infty,\qquad\text{otherwise}.\qquad\text{(2)}
```

The finite positive-demand branch requires a positive denominator. A zero-capacity node can still host a zero-demand dummy stage through the first branch; an over-capacity positive demand has infinite score. These are the same three branches implemented by `_comp_ratio_cost`.

The denominator is the capacity remaining in that predecessor's partial deployment, not the original node capacity or the total normalization constant. A larger remaining capacity gives a smaller increment for the same demand. Dummy stages contribute zero, including at nodes with zero computing capacity; the code handles that case before division.

Communication arcs unable to carry $`f_r`$ under the predecessor's residual state receive infinite weight and are not traversed. Available feasible arcs have weights

```math
u_r^{k,a'}(e)=\begin{cases} \dfrac{(1-\psi)f_r}{d_r^{k-1,a'}(e)}, & e\in E_{\mathrm{com}}^T,\\ 0, & e\in E_{\mathrm{sto}}^T. \end{cases} \qquad \text{(3)}
```

```math
\lambda_{r,\mathrm{link}}^{k,a',a} =\sum_{e\in\varphi_r^{k,a',a}}u_r^{k,a'}(e). \qquad \text{(4)}
```

An unreachable target has infinite routing score. All usable weights are nonnegative, so `dijkstra_from` performs one single-source search per predecessor and reuses distances for every target at the next stage. It can stop when all targets have been settled. It does not run a new shortest-path search for every predecessor–target pair. A target equal to its predecessor has a zero-length, zero-score route, permitting consecutive stages at the same time node.

The manuscript describes routing within the service-window subgraph. Code searches the full directed TEG but restricts stage candidates to that window. Because time never decreases, a route from an in-window predecessor to an in-window target cannot leave through a later slot and return. The extra graph scope can increase search work but does not create a backward-time feasible extension.

### 3.3 State transition and resource updates

For each target, PFE compares the extensions from all retained predecessors:

```math
\xi_r^{k,a}=\min_{a'\in A_{r,k-1}} \left\{\xi_r^{k-1,a'}+\lambda_{r,\mathrm{node}}^{k,a',a} +\lambda_{r,\mathrm{link}}^{k,a',a}\right\}. \qquad \text{(5)}
```

Predecessors without records are excluded. When the minimum is finite, PFE retains one minimizing extension. The implementation replaces the current best only on a strictly smaller score, so equal-score ties retain the first encountered extension. The chosen predecessor's capacities are copied before deducting the new stage and route:

```math
c_r^{k,a}(n^t)=c_r^{k-1,a^*}(n^t) -f_r\delta_{v_{r,k}}\mathbf1\{n^t=a\}. \qquad \text{(6)}
```

```math
d_r^{k,a}(e)=d_r^{k-1,a^*}(e) -f_r\mathbf1\{e\in\varphi_r^{k,a^*,a}\}, \qquad e\in E_{\mathrm{com}}^T. \qquad \text{(7)}
```

The indicator is 1 when its condition holds and 0 otherwise. Each virtual segment contributes its own deductions; repeated use by later segments is therefore charged again. Only the used direction is decremented. Infinite-capacity storage is not deducted. Competing targets have independent state copies, so considering one tentative route cannot consume another target's capacity.

```text
PFE(network, r, input_node, input_edge, hosts, destinations):
    form source, processing, and destination candidate sets
    retain source record(score=0, copied capacities, no parent)
    for k = 1 through number_of_processing_stages + 1:
        best := empty map from target to extension
        for predecessor a' with a retained record at stage k-1:
            build feasible edge weights from that record's capacities
            distances, route_predecessors := Dijkstra(a', stage-k targets)
            for target a in stage-k candidates:
                check route reachability and processing capacity at a
                total := predecessor score + routing score + processing score
                if finite and strictly better than the saved score for a:
                    reconstruct connecting route and save this extension
        if best is empty:
            return failure with input_node, input_edge unchanged
        for each target with a saved extension:
            copy its selected predecessor's capacity dictionaries
            deduct processing demand and directed communication usages
            retain score, parent, placement delta, and ordered route
    choose the lowest-score destination record
    traceback parent records to obtain all stage placements and segments
    return deployment and that record's final capacities
```

PFE itself returns the construction result; adapters encode `Path` objects and validate stage/route identities and aggregate usage. Pricing conversion additionally passes through `extract_path_from_sp`, which reconstructs ordered routes and can reject inconsistent selected arcs. Failure at any of these stages must not be interpreted as proof of request infeasibility.

### 3.4 Why the procedure remains heuristic

The retained state has a dynamic-programming structure, but only one record survives at each stage–location pair. Two partial deployments ending at the same pair may consume different node/link resources. A lower current score is not a dominance proof over those resource vectors. Discarding another record can remove the only feasible continuation, or a continuation that would eventually have lower cost. PFE also uses a shortest route under its current weights instead of retaining all routes with different future resource consequences.

Three quantities must remain distinct:

| Quantity | What it measures | Where used |
|---|---|---|
| $`\xi_r^{k,a}`$ | Sum of residual-capacity ratios along a partial construction | PFE record comparison |
| $`C_p^r`$ | Actual computing and communication usage normalized by original full-horizon totals | Master objective and solution evaluation |
| $`\zeta_p^r`$ | Admission value minus physical cost, request dual, and resource dual charges | Column acceptance and pricing certification |

PFE does not put master duals into (2)–(5). When used for pricing, it constructs a deployment first and evaluates its reduced cost afterward. Changing dual prices can change whether that same deployment is useful without changing the PFE output itself.

### 3.5 Complexity and implementation qualification

Let $`n_T`$ and $`m_T`$ be the numbers of time-expanded nodes and links. The manuscript gives the following search-and-record-copying bound for one PFE call:

```math
O\!\left((|V_r|+1)n_T(n_T+m_T)\log(1+n_T)\right). \qquad \text{(8)}
```

There are at most $`n_T`$ retained predecessors per stage. Each builds weights and runs a binary-heap Dijkstra search; copying the residual dictionaries for at most $`n_T`$ retained targets adds a per-stage cost proportional to $`n_T(n_T+m_T)`$. SAGINFE adds request sorting and one such construction per request. Neither this argument nor PFE's polynomial search structure supplies a polynomial bound for solver pricing or the integer RMP.

There is a code-level qualification to the stated manuscript bound: inside `pfe`, `_reconstruct_path` is called whenever a target's best predecessor improves, before the final predecessor is known. In a worst-case counting argument, there can be $`n_T^2`$ such improvements per stage, each reconstructing a route of length up to $`n_T`$. A conservative bound for the current implementation therefore also allows a cubic term per stage:

```math
O\!\left((|V_r|+1) \left[n_T(n_T+m_T)\log(1+n_T)+n_T^3\right]\right). \qquad \text{(9)}
```

This is an implementation overhead qualification, not an experimental runtime claim or a change to the algorithm. The manuscript's derivation accounts for searches and retained-record copies but does not explicitly account for these repeated intermediate route reconstructions. It merits author review if (8) is presented as a strict bound for this exact code. See [sgfe_solver.py](../../sgfe_solver.py), `pfe`, `_reconstruct_path`, and `_traceback`, and the [PFE illustration](../../figures/paper/PFE_illustration.pdf).

## 4. Column generation and master coordination

### 4.1 Initialization and LP optimization

`ColumnPool.initialize_empty_paths` creates one rejection column per request. Optional custom initial paths are converted, costed, validated, and deduplicated by `GBSolver._add_custom_initial_paths`. Initial SAGINFE columns need not have positive reduced cost: no master dual solution exists yet, and initialization is distinct from pricing insertion.

At each round, [solve_rmp](../../restricted_master_problem.py) builds and solves a fresh LP over the current pool. The implementation rebuilds the master from the collected columns; it does not retain a persistent solver model and incrementally add a variable to it. An optimal LP solution is required before reading row duals. A nonoptimal LP stops this pricing path; available initialization/fallback behavior depends on the configuration and outer adapter.

The LP weights are convex mixtures constrained by shared resources, not deployable fractions of requests. Request, node, and link duals are frozen for the following pricing phase. All candidate comparisons in that phase must use those same duals; duals from earlier rounds are not interchangeable.

### 4.2 Feasible candidates versus improving columns

For insertion, the intended mathematical checks are

```math
p\in P_r,\qquad p\notin\hat P'_r,\qquad \zeta_p^r>\varepsilon_{\mathrm{rc}},\qquad \varepsilon_{\mathrm{rc}}=10^{-6}. \qquad \text{(10)}
```

The implementation first gates on a reported positive objective, extracts a complete `Path`, finalizes usage, recomputes reduced cost, checks cost consistency and path validity, and attempts insertion. The built-in pricing MIP enforces original single-request capacity by default, while PFE enforces it through residual-state deductions. `validate_path` verifies structure, stored usage, and cost but does not alone check every resource bound for an arbitrary external candidate. Custom pricing callbacks must preserve that capacity-feasibility contract; the master separately enforces aggregate capacity during selection.

The reduced-cost recomputation uses the actual demand-scaled usage and the current dual vector, rather than trusting the construction score or a supplied MIP objective. This check is “exact” in the sense of using the mathematical expression, with numerical tolerances. It is not a separate optimal pricing solve. A valid low-cost path can still have nonpositive reduced cost when it uses resources with high current dual prices.

`Path.build_signature` and `ColumnPool.add_path` identify duplicates by request identity, admission/rejection status, stage placement, and ordered segment routes, including storage. Two different embeddings with the same scalar cost are not duplicates. Conversely, assigning a new `path_id` does not make an identical deployment new. A returned positive column already in an optimal LP pool is an inconsistency requiring diagnosis; the coordinator records rejected-positive conditions rather than treating the failure to insert it as a convergence certificate.

### 4.3 Iteration and stopping

```text
initialize pool and optional feasible initializer
for each allowed column-generation round:
    solve current LP-RMP; require optimal status before extracting duals
    freeze duals and select the configured pricing mode
    construct ordered per-request task list within remaining call allowance
    run configured primary pricing (heuristic-first or solver-first)
    extract, recompute, validate, and deduplicate returned candidates
    if configured and eligible, run supplementary selective PFE
    assess strict-pricing coverage and rejected-positive diagnostics
    if all-request strict certificate establishes no improving alternative:
        stop with tolerance-based LP pricing certification
    if early pricing made no progress and work remains:
        activate strict solver pricing
    otherwise apply no-progress, incomplete-phase, and work-limit rules
freeze generated pool
reuse integral LP selection or attempt final integer RMP
validate recovered solution and apply configured safeguard
```

The coordinator records iteration and solver-call budgets separately. The field `max_exact_pricing_calls` counts solver-pricing calls, including early-terminated solver calls; it does not mean all counted calls were solved exactly. Heuristic PFE calls have separate accounting. A truncated task list does not cover all requests and cannot certify full-master convergence. Under deterministic mode, an incomplete phase can restore the last completed pool checkpoint instead of retaining a timing-dependent partial batch.

No-progress behavior also depends on evidence. A strict phase with no additions may represent certified absence, timeout, incomplete coverage, extraction failure, or rejected positive columns. These cases produce different statuses. `MAX_ITERATIONS`, `DETERMINISTIC_WORK_BUDGET_EXHAUSTED`, and `INCOMPLETE_NOT_CONVERGED` must not be relabeled “optimal convergence.” The control flow is in [GBSolver.solve](../../main_solver.py); certificate accounting is in [evaluate_exact_pricing_certification](../../result_schema.py).

## 5. Configurations and pricing policies

<a name="1-names-and-configurations"></a>

### 5.1 Main algorithms and component configurations

The manuscript names are DWCG-Pure (Pure), DWCG-SAGINFE (HFP, heuristic-first pricing), and DWCG-SAGINFE-PRO (PRO). The following table describes the component combinations; concrete campaign plans supply their limits and schedule parameters.

| Configuration | Identifier | Initialization | Pricing after initialization | PRO MIP start / safeguard |
|---|---|---|---|---|
| SAGINFE | `saginfe` | Sequential PFE construction | None | Not applicable |
| DWCG-Pure | `dwcg_pure` | Rejection only | Solver pricing; main configuration HYB-S5 | No |
| Warm | `dwcg_warm` | SAGINFE | Solver pricing; hybrid | No |
| DWCG-Pure-HP / HP | `dwcg_pure_hp` | Rejection only | PFE first, strict solver fallback | No |
| DWCG-SAGINFE / HFP | `dwcg_saginfe` | SAGINFE | PFE first, strict solver fallback | No |
| PRO-no-PFE | `dwcg_saginfe_pro_no_pfe` | SAGINFE | Solver pricing; hybrid | Yes |
| DWCG-SAGINFE-PRO / PRO | `dwcg_saginfe_pro` | SAGINFE | Solver pricing; hybrid, then selective PFE | Yes |

Warm isolates initialization relative to Pure. HP and HFP arrange PFE **before** solver fallback, whereas PRO starts each pricing round with the solver and uses PFE selectively afterward. PRO-no-PFE retains SAGINFE initialization, so “no PFE” means no supplementary PFE pricing, not the absence of PFE inside the initializer. These configurations are interacting alternatives, not a sequence in which every row simply adds a component to the previous one.

[formal_schema.expected_formal_algorithm_components](../../formal_schema.py) checks allowed combinations. [formal_runner.run_dwcg_formal_adapter](../../formal_runner.py) connects those choices to the coordinator and implements the PRO return safeguard. See the [component-ablation plan](../../configs/formal/formal_component_ablation_s40_v3.json), [pricing plan](../../configs/formal/formal_pricing_schedule_v3.json), and [PRO pricing plan](../../configs/formal/formal_pro_pricing_schedule_v3.json) for reproducible settings. The direct comparison methods `direct_ilp` (ILP-FullObjective) and `direct_ilp_acceptance_cold` (ILP-AdmissionOnly) solve the original formulation, rather than participating in the column-generation workflow.

### 5.2 Strict, early, and hybrid solver pricing

The MIP in [solve_sp_detailed](../../sub_problem.py) fixes request admission and optimizes the reduced-cost expression from the model chapter. Both modes use the same feasible deployment model; their search stopping settings differ.

| Policy | Configured behavior | Certification implication |
|---|---|---|
| EX / `exact` | Strict solver pricing from the first round | May certify only when the required solves actually finish and pass checks |
| ESV / `early_stop` | Starts with early solver pricing; the coordinator switches to strict after a no-addition round | Early results alone do not certify absence |
| HYB-S5 / `hybrid` | Early pricing for rounds 1–5, then strict; no-progress can switch earlier | Strict phase remains subject to call/round/solver controls |
| HYB-S2, HYB-S7 | Same hybrid arrangement with two or seven early rounds | Same qualifications |
| `heuristic_then_exact` | PFE phase first, then strict solver fallback when required | Heuristic failure is not a certificate |

Strict pricing uses relative MIP gap 0 and absolute gap $`10^{-7}`$ by default. Early pricing uses `SolutionLimit=1` and relative gap 0.1. The first feasible incumbent need not have positive reduced cost: `SolutionLimit` does not test the reduced-cost sign. The pricing wrapper returns its variables only when the incumbent objective exceeds the acceptance threshold, and the coordinator checks the extracted column again.

**ESV implementation qualification.** The current `GBSolver.solve` branch changes `sp_strategy` to `exact` whenever an early round adds no columns, including when the initial strategy was `early_stop`. Thus an ESV configuration is not an unconditional “early-only, no-fallback” run. The plan records the configured strategy, while `pricing_phase_history` records actual phases. This behavior should be considered when interpreting a strict early-only description of ESV; the documentation follows the executable control path without altering configurations or results.

Hybrid switching concerns rounds, not an individual solver call's elapsed time or an optimality test. An early column can improve the pool even when pricing is far from optimal. Conversely, a strict call can stop at a limit or fail, and then its absence of a returned column does not establish absence of an improving deployment.

### 5.3 Heuristic-first pricing and fallback

HP and HFP call `solve_sp_with_sgfe` serially across requests under the current dual state. Each call runs PFE from original capacities, converts the deployment to pricing-compatible stage and arc records, and evaluates the current reduced cost. It does not start from SAGINFE's depleted residual network.

The coordinator's default fallback rule is **phase-wide**: if the whole heuristic phase adds no columns, it invokes strict solver pricing for the requests. It is not an immediate per-request exact solve whenever one PFE call fails. If the heuristic phase adds columns, the default moves on to another master round. The optional `run_exact_after_heuristic_success` flag can request solver pricing even after heuristic success; its default is false. `heuristic_disable_after_misses` can disable heuristic phases after consecutive misses; the default is unset. These switches are implementation capabilities, not additional mechanisms silently enabled in every published configuration.

With unchanged capacities and tie order, repeated PFE calls can produce the same deployment. Its current dual value may differ, but duplicate detection still prevents reinserting the same column. This helps explain why exact fallback and duplicate accounting matter even when PFE is very fast.

## 6. PRO: selective PFE and incumbent protection

### 6.1 Supplementing solver-first pricing

PRO initializes with SAGINFE, uses the configured solver schedule as primary pricing, then may call PFE for a small subset of requests. `run_selective_pfe_phase` excludes requests for which primary pricing already **inserted** a column in that phase. It prioritizes larger LP rejection weights, fewer admitted alternatives, and greater LP fractionality, with request ID resolving remaining ties.

The policy helper also has a `dual_bottleneck_exposure` field, but the current coordinator supplies zero for it. It must not be described as an active dual-bottleneck scoring heuristic. Likewise, eligibility is broad: lack of a newly inserted primary column itself qualifies a request. Selectivity primarily comes from ordering and call caps, rather than an assertion that only currently rejected requests may be tried.

The default caps in [SelectivePFEConfig](../../dwcg_saginfe_pro.py) are

```math
K_{\mathrm{round}}=\min\{\lceil0.05|R|\rceil,10\}, \qquad K_{\mathrm{total}}=\min\{\lceil0.20|R|\rceil,40\}. \qquad \text{(11)}
```

Here the two $`K`$ values are maximum supplementary PFE calls per round and per solve. The selected prefix is limited by both. Calls can yield failure, duplicates, non-improving candidates, or new positive columns; they are not synonymous with useful columns. Counters distinguish these outcomes.

The supplementary stage is skipped if initialization already admits all requests or if the active remaining time allowances trigger recovery/deadline protection. The helper defaults include five-second safety margins. Each call starts from independent original capacities, uses the PFE score, and undergoes the same current-dual reduced-cost and duplicate checks as other candidates. There is no repair that removes an already committed request: new alternatives compete through the next master solve.

### 6.2 MIP start and return safeguard

Both PRO and PRO-no-PFE request a final integer RMP MIP start reproducing the initializer. `build_final_rmp_mip_start` matches initial deployment signatures to the final pool and assigns a rejection column to each initially rejected request. It supplies a complete zero/one assignment only when the necessary columns exist. A MIP start gives the solver a feasible incumbent opportunity; it does not constrain the final search to preserve the same requests or routes.

The formal adapter separately compares the validated returned pool solution with the retained SAGINFE result, preferring more admissions and then lower normalized cost. If the solver returns no selection, the adapter can retain the initial solution. A malformed selection raises a validation error before this comparison; it is not automatically repaired or replaced. The comparison operates on the validated summary and retained initialization; it is not implied merely by having inserted warm-start columns. Warm and HFP do not automatically inherit this PRO-specific return comparison.

For a fixed instance, write $`\mathcal S_0`$ for the initial solution, $`\mathcal S_{\mathrm{pool}}`$ for a valid recovered pool solution, and $`\mathcal J`$ for the model's admission-first objective. The intended safeguard is

```math
\mathcal S_{\mathrm{out}}\in \underset{\mathcal S\in\{\mathcal S_0,\mathcal S_{\mathrm{pool}}\}}{\mathrm{arg\,max}} \mathcal J(\mathcal S). \qquad \text{(12)}
```

If the pool solution is unavailable, return $`\mathcal S_0`$. This protects the initialized objective among valid compared solutions; it does not guarantee a strict improvement. In the code, the adapter compares admitted counts and physical costs directly, while `better_incumbent` and `incumbent_safeguard_metadata` expose the same preference and reporting concepts. Result analysis should distinguish the final solver incumbent from the returned safeguarded result.

## 7. Parallel request pricing

For fixed network data and a fixed LP dual vector, each pricing problem has only one request's variables and original single-request bounds. Therefore different requests can be solved independently. Shared resources are not concurrently decremented by workers; their competition is represented by the frozen prices and subsequently resolved by the master.

[plan_pricing_tasks](../../parallel_pricing_schedule.py) creates the ordered logical task list. In deterministic mode, requests are sorted by ID, and the remaining solver-call allowance truncates this list **before** execution. The coordinator uses `ProcessPoolExecutor`, submitting at most the configured number of concurrent tasks and replenishing them as workers finish. Each task has its own solver state and receives the request, network, duals, objective, and solver settings. Per-call seeds are derived from the algorithm seed, iteration, phase, and request identity through [deterministic_seeds.py](../../deterministic_seeds.py).

Workers return sparse selected-variable dictionaries and metadata; they do not write to the master or shared `ColumnPool`. The coordinator collects results and processes them in the predefined task order, so arrival order does not determine path IDs or insertion order. It then completes validation and pool updates before solving the next master. Launched calls consume the logical work budget regardless of their completion order or whether they yield a column. Incomplete phases and failures are recorded; deterministic checkpoint restoration prevents a partial interrupted phase from masquerading as a completed deterministic round.

Only solver-based pricing uses this worker pool. SAGINFE initialization, LP master optimization, candidate coordination, selective PFE, and final integer selection remain sequential. HFP's heuristic phase is also serial, although its exact fallback can use solver-pricing parallelism. Published parallel experiments use one solver thread per worker, so worker count and solver thread count describe different controls.

Pricing-stage speedup excludes much of that serial work; end-to-end speedup includes it. Task imbalance, process startup, serialization, solver variability, and limited remaining parallel work can reduce gains. The tested campaign preserved admitted counts and ordered column pools across worker counts. The existing [serial/parallel smoke test](../../tests_gurobi/test_serial_parallel_equivalence.py) compares entry configurations with one worker; it is not itself a multiworker concurrency test. The campaign evidence does not imply universal equivalence for arbitrary wall-clock-limited runs or linear end-to-end scalability.

## 8. Final integer selection and guarantees

### 8.1 Reuse or solve

After pricing stops, the coordinator checks the last available LP selection for numerical integrality: every weight must be below $`10^{-6}`$ or above $`1-10^{-6}`$. If so, it reuses that selection and validates the resulting deployment. New columns may have been added after that LP was solved. Assigning them zero preserves feasibility, but reusing the old solution does not certify optimality over the enlarged final pool. The implementation records `INTEGRAL_RMP_LP` and does not infer a final integer-RMP optimality claim from reuse alone.

Otherwise, `solve_rmp(..., is_integer=True)` rebuilds the master with binary weights over the final pool. It chooses exactly one deployment or rejection per request, with aggregate computing and communication constraints unchanged. A finite-pool MIP incumbent can be feasible without being proven optimal. The coordinator's default final-RMP relative gap is $`10^{-4}`$; an `OPTIMAL` status is interpreted under solver tolerances, rather than as an exact-arithmetic proof. If a time limit is reached, incumbent availability, objective bound, gap, and status remain important separate outputs.

The published formal protocol has no overall end-to-end runtime limit, while CG round/call controls and the independent 300-second integer-recovery cap remain in force. The generic coordinator also supports an overall deadline and recovery reserve. These controls are described in the [experimental protocol](../experiments/README.md); no limits are changed by this documentation. Recovery cannot be assumed to run after every exit path, and a solver result with no incumbent cannot be reported as a feasible pool selection. PRO's retained initializer supplies its explicit fallback.

### 8.2 What can be claimed

| Evidence | Supported conclusion | What it does not establish |
|---|---|---|
| Valid PFE or SAGINFE deployment | Feasibility under the tested capacities | Maximum admission or minimum deployment cost |
| Optimal current LP-RMP | Optimality of that restricted relaxation | Full-column LP or original integer optimality |
| Candidate with recomputed positive reduced cost | An eligible alternative exists, if valid and new | Optimal pricing or a strict next-round objective increase |
| Complete strict pricing certificate at one optimal dual vector | No improving full-master LP column within the stated tolerance | Global integer optimality |
| Optimal final integer RMP | Optimality over the generated pool, under solver tolerances | Optimality over ungenerated deployments |
| Valid time-limited integer incumbent | A feasible finite-pool deployment | Restricted-pool optimality |
| Certified original ILP-AdmissionOnly | Maximum admitted count | Minimum cost among maximum-admission solutions |
| Certified original ILP-FullObjective | Original admission-first objective optimum | A property transferable to larger untested instances |

The mathematical full-LP certificate requires every request to have a nonpositive pricing optimum (or a suitable valid upper bound) at the same current duals. The implemented checker is stricter about statuses: all expected strict calls must launch, complete, and report optimality, with no positive results, timeouts, failures, or rejected-positive inconsistencies. Proven infeasibility of an admitted-only subproblem is not currently counted as an optimal call. A time-limited nonpositive incumbent is only a lower bound on a maximization pricing optimum and cannot exclude a better column.

The code explicitly keeps `global_original_integer_optimal=False` for finite-pool recovery. Full-master LP certification followed by optimal restricted integer selection generally still leaves an integer gap or missing integer-useful columns. The implementation has no branch-and-price procedure to close that distinction. Preserve termination reasons and `OptimalityClaims` when reporting results, rather than deducing global optimality from a solver's `OPTIMAL` status without identifying which model it solved.

## 9. Algorithm-to-code navigation

| Component | Responsibility | Main entry points |
|---|---|---|
| SAGINFE | Stable request order and shared residual commitment | [sgfe_solver.py](../../sgfe_solver.py): `sgfe` |
| PFE | Stage candidates, predecessor scores, directed shortest paths, capacity states, traceback | [sgfe_solver.py](../../sgfe_solver.py): `pfe`, `dijkstra_from`, `_comp_ratio_cost`, `_build_edge_ratio_costs`, `_traceback` |
| Initial and heuristic pricing columns | Encode embeddings, calculate physical cost, verify pricing reduced cost | [sgfe_solver.py](../../sgfe_solver.py): `build_sgfe_initial_paths_from_results`, `embedding_to_initial_path_dict`, `solve_sp_with_sgfe` |
| Column identity | Usage aggregation, canonical stage/segment signatures, duplicate rejection | [data_model.py](../../data_model.py): `Path`; [column_pool.py](../../column_pool.py): `ColumnPool.add_path` |
| LP/integer master | Shared resource rows, column weights, row dual extraction, integer incumbent | [restricted_master_problem.py](../../restricted_master_problem.py): `solve_rmp` |
| Pricing MIP and route extraction | Dual-adjusted single-request optimization and complete ordered routes | [sub_problem.py](../../sub_problem.py): `solve_sp_detailed`, `extract_path_from_sp` |
| CG coordinator | Mode selection, insertion checks, budgets, certificates, final recovery | [main_solver.py](../../main_solver.py): `GBSolver.solve`, `_call_sp`, `_parallel_call_sp` |
| PRO policies | Selection caps/priorities, MIP-start helpers, incumbent comparison metadata | [dwcg_saginfe_pro.py](../../dwcg_saginfe_pro.py): `select_critical_flows`, `mip_start_from_incumbent`, `better_incumbent` |
| Experiment wiring and returned safeguard | Resolve variant, initialize, configure coordinator, validate and compare outputs | [formal_runner.py](../../formal_runner.py): `run_saginfe_formal_adapter`, `run_dwcg_formal_adapter` |
| Parallel reproducibility | Ordered logical calls and deterministic seeds | [parallel_pricing_schedule.py](../../parallel_pricing_schedule.py): `plan_pricing_tasks`; [deterministic_seeds.py](../../deterministic_seeds.py) |
| Validation and claims | Structural/capacity validation and certificate scope | [validation.py](../../validation.py): `validate_path`, `validate_solution_capacity`; [result_schema.py](../../result_schema.py): `evaluate_exact_pricing_certification`, `OptimalityClaims` |

Useful focused checks include [stage-indexed chains](../../tests/test_stage_indexed_sfc.py), [pricing certification](../../tests/test_pricing_certification_logic.py), [no-progress control](../../tests/test_pricing_no_progress_controller.py), [configuration naming](../../tests/test_algorithm_naming.py), [deterministic pricing order](../../tests/test_deterministic_pricing_order.py), and [final RMP scope](../../tests_gurobi/test_final_rmp_scope.py). These checks complement inspection of the actual solver and adapter control paths; they do not turn heuristic construction into an exact algorithm.
