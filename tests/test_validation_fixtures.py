from data_model import Flow, Network, Path
from objective import ObjectiveSpec, compute_path_physical_cost


def tiny_network():
    network = Network()
    network.set_nodes(3)
    network.set_time_slots(3)
    for node in range(3):
        for time in range(3):
            network.add_time_node((node, time), 10.0)
    network.add_comm_time_edge((0, 0), (1, 0), 10.0)
    network.add_comm_time_edge((1, 0), (2, 0), 10.0)
    network.add_storage_time_edge((0, 0))
    network.add_storage_time_edge((1, 0))
    network.add_storage_time_edge((2, 0))
    network.add_storage_time_edge((0, 1))
    network.add_storage_time_edge((1, 1))
    network.add_storage_time_edge((2, 1))
    return network


def repeated_flow(flow_id=1, demand=1.0):
    return Flow(flow_id, 2, 0, 0, 2, 1, ["A", "A"], {"A": 1.0}, demand)


def valid_repeated_path(network=None, flow=None, path_id=1):
    network = network or tiny_network()
    flow = flow or repeated_flow()
    path = Path(
        flow_id=flow.flow_id,
        path_id=path_id,
        path_is_empty=False,
        path_stage_placement={0: (1, 0), 1: (1, 0)},
        path_segment_routing={
            0: [((0, 0), (1, 0))],
            1: [],
            2: [((1, 0), (2, 0))],
        },
        path_cost=0.0,
    )
    path.finalize_usage(flow=flow)
    path.path_cost = compute_path_physical_cost(network, flow, path, ObjectiveSpec(network.resource_weight))
    return path
