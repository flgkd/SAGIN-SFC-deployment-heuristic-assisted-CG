import os
import unittest

from data_model import Flow, Network, Path
from objective import ObjectiveSpec, compute_path_physical_cost


def gurobi_skip_reason():
    if os.environ.get("RUN_GUROBI_TESTS") != "1":
        return "Set RUN_GUROBI_TESTS=1 to opt in to licensed Gurobi integration tests."
    import gurobipy

    try:
        with gurobipy.Env(empty=True) as env:
            env.setParam("OutputFlag", 0)
            env.start()
            with gurobipy.Model(env=env):
                pass
    except gurobipy.GurobiError as exc:
        if exc.errno == gurobipy.GRB.Error.NO_LICENSE:
            return "Gurobi integration tests require a usable license: " + str(exc)
        raise
    return None


_gurobi_skip_reason = gurobi_skip_reason()
gurobi_test = unittest.skipIf(
    _gurobi_skip_reason is not None, _gurobi_skip_reason or ""
)


def tiny_network():
    network = Network()
    network.set_nodes(2)
    network.set_time_slots(2)
    for node in [0, 1]:
        for time in [0, 1]:
            network.add_time_node((node, time), 10.0)
    network.add_comm_time_edge((0, 0), (1, 0), 10.0)
    network.add_storage_time_edge((0, 0))
    network.add_storage_time_edge((1, 0))
    return network


def tiny_flow(flow_id=1):
    return Flow(flow_id, 1, 0, 0, 1, 0, ["A"], {"A": 1.0}, 1.0)


def tiny_path(network=None, flow=None, path_id=1):
    network = network or tiny_network()
    flow = flow or tiny_flow()
    path = Path(
        flow_id=flow.flow_id,
        path_id=path_id,
        path_is_empty=False,
        path_stage_placement={0: (0, 0)},
        path_segment_routing={0: [], 1: [((0, 0), (1, 0))]},
        path_cost=0.0,
    )
    path.finalize_usage(flow=flow)
    path.path_cost = compute_path_physical_cost(network, flow, path, ObjectiveSpec(network.resource_weight))
    return path
