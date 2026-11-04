"""Small, solver-free construction example. No historical records are used."""
from pathlib import Path as FilePath
import sys

sys.path.insert(0, str(FilePath(__file__).resolve().parents[1]))
from config_schema import FlowGenerationConfig, InstanceConfig, TopologyConfig, get_candidate_scale
from data_model import Path
from instance_builder import build_instance
from objective import compute_path_physical_cost, objective_spec_from_network
from sgfe_solver import sgfe, build_sgfe_initial_paths_from_results
from validation import validate_instance, validate_path, validate_solution_capacity


def main():
    config = InstanceConfig(
        TopologyConfig(get_candidate_scale('S20'), time_slots=6, seed=0),
        FlowGenerationConfig(seed=0, num_flows=8, demand_multiplier=0.4),
        scenario_seed=999,
    )
    instance = build_instance(config)
    validate_instance(instance, config)
    network, flows = instance.network, list(instance.flows)
    embeddings = sgfe(network, flows, network.nodes, network.nodes, verbose=False)
    columns = build_sgfe_initial_paths_from_results(network, flows, embeddings)
    selected = {}
    by_id = {flow.flow_id: flow for flow in flows}
    for index, column in enumerate(columns):
        flow = by_id[column['flow_id']]
        path = Path(flow_id=flow.flow_id, path_id=index, path_is_empty=False,
                    path_stage_placement=column['stage_placement'],
                    path_segment_routing=column['segment_routing'],
                    path_cost=column['physical_cost'])
        path.finalize_usage(flow=flow)
        validate_path(network, flow, path)
        selected[flow.flow_id] = path
    validate_solution_capacity(network, flows, selected)
    spec = objective_spec_from_network(network)
    cost = sum(compute_path_physical_cost(network, by_id[k], p, spec) for k, p in selected.items())
    assert 0 <= cost <= 1 + 1e-9
    print('New diagnostic instance: seed=999, S20, T=6, requests=8')
    print('Validated admission: {}/{}; cost={:.6f}; objective={:.6f}'.format(
        len(selected), len(flows), cost, spec.acceptance_reward * len(selected) - cost))
    print('Diagnostic smoke only; not a manuscript result.')


if __name__ == '__main__':
    main()
