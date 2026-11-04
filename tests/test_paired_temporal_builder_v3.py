import unittest

from calibration_runner import full_instance_fingerprint
from config_schema import (
    FlowGenerationConfig,
    InstanceConfig,
    TopologyConfig,
    get_candidate_deadline_profile,
    get_candidate_scale,
    get_candidate_sfc_profile,
    get_candidate_traffic_mix_profile,
)
from paired_temporal_builder import (
    ARRIVAL_LATENT_DENOMINATOR,
    PAIRED_TEMPORAL_TIME_SLOTS,
    build_paired_temporal_instance,
    common_temporal_prefix_fingerprint,
    full_temporal_flow_fingerprint,
    map_arrival_from_latent,
    non_temporal_flow_fingerprint,
    paired_timing_latent_fingerprint,
    physical_base_fingerprint,
    temporal_prefix_fingerprint,
)


class PairedTemporalBuilderV3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {
            (seed, time_slots): build_paired_temporal_instance(cls._config(time_slots, seed))
            for seed in range(3)
            for time_slots in PAIRED_TEMPORAL_TIME_SLOTS
        }

    @staticmethod
    def _config(time_slots, seed):
        return InstanceConfig(
            TopologyConfig(
                scale=get_candidate_scale("S40"),
                time_slots=time_slots,
                seed=0,
                dynamic_topology=True,
            ),
            FlowGenerationConfig(
                seed=0,
                num_flows=160,
                demand_multiplier=0.6,
                traffic_mix_profile=get_candidate_traffic_mix_profile("Candidate"),
                sfc_profile=get_candidate_sfc_profile("Candidate"),
                deadline_profile=get_candidate_deadline_profile("Balanced"),
                load_profile_name="Medium",
            ),
            scenario_seed=seed,
        )

    def test_exact_supported_t_set_and_wrong_t_rejected(self):
        self.assertEqual(PAIRED_TEMPORAL_TIME_SLOTS, (6, 8, 10, 12))
        with self.assertRaisesRegex(ValueError, "supports T"):
            build_paired_temporal_instance(self._config(4, 0))

    def test_all_supported_instances_have_160_flows(self):
        for result in self.results.values():
            self.assertEqual(result.instance.network.nodes_num, 40)
            self.assertEqual(len(result.instance.flows), 160)
            self.assertEqual(result.instance.instance_metadata["demand_multiplier"], 0.6)

    def test_non_temporal_flow_identity_is_identical_across_t(self):
        for seed in range(3):
            values = {
                non_temporal_flow_fingerprint(self.results[(seed, time_slots)].instance)
                for time_slots in PAIRED_TEMPORAL_TIME_SLOTS
            }
            self.assertEqual(len(values), 1)

    def test_slack_and_arrival_latents_are_identical_across_t(self):
        for seed in range(3):
            values = {
                paired_timing_latent_fingerprint(self.results[(seed, time_slots)].instance)
                for time_slots in PAIRED_TEMPORAL_TIME_SLOTS
            }
            self.assertEqual(len(values), 1)
            by_t = {
                time_slots: self.results[(seed, time_slots)].instance.flow_metadata["per_flow"]
                for time_slots in PAIRED_TEMPORAL_TIME_SLOTS
            }
            for flow_id in range(160):
                slacks = {by_t[time_slots][flow_id]["slack"] for time_slots in PAIRED_TEMPORAL_TIME_SLOTS}
                latents = {by_t[time_slots][flow_id]["arrival_latent_uint64"] for time_slots in PAIRED_TEMPORAL_TIME_SLOTS}
                self.assertEqual(len(slacks), 1)
                self.assertEqual(len(latents), 1)

    def test_slack_generation_uses_exact_authoritative_quota(self):
        exact_quota = {2: 48, 3: 64, 4: 48}
        for seed in range(3):
            by_t_counts = []
            for time_slots in PAIRED_TEMPORAL_TIME_SLOTS:
                per_flow = self.results[(seed, time_slots)].instance.flow_metadata["per_flow"]
                counts = {slack: sum(meta["slack"] == slack for meta in per_flow.values()) for slack in (2, 3, 4)}
                by_t_counts.append(counts)
            self.assertTrue(all(counts == exact_quota for counts in by_t_counts))
            self.assertEqual(len({tuple(sorted(counts.items())) for counts in by_t_counts}), 1)

    def test_aggregate_slack_counts_use_exact_quota(self):
        aggregate = {2: 0, 3: 0, 4: 0}
        for seed in range(3):
            for meta in self.results[(seed, 8)].instance.flow_metadata["per_flow"].values():
                aggregate[meta["slack"]] += 1
        self.assertEqual(aggregate, {2: 144, 3: 192, 4: 144})

    def test_changing_t_does_not_reshuffle_quota_labels(self):
        for seed in range(3):
            reference = {
                flow_id: meta["slack"]
                for flow_id, meta in self.results[(seed, 8)].instance.flow_metadata["per_flow"].items()
            }
            for time_slots in (6, 10, 12):
                other = self.results[(seed, time_slots)].instance.flow_metadata["per_flow"]
                self.assertEqual({flow_id: meta["slack"] for flow_id, meta in other.items()}, reference)

    def test_deterministic_arrival_mapping_and_deadline_validity(self):
        for (seed, time_slots), result in self.results.items():
            for flow in result.instance.flows:
                meta = result.instance.flow_metadata["per_flow"][flow.flow_id]
                expected = map_arrival_from_latent(meta["arrival_latent_uint64"], time_slots, meta["slack"])
                self.assertEqual(meta["arrival_time"], expected)
                self.assertEqual(flow.flow_src_time_node[1], expected)
                self.assertEqual(meta["deadline_time"], expected + meta["slack"])
                self.assertEqual(flow.flow_dst_max_time, meta["deadline_time"])
                self.assertLessEqual(flow.flow_dst_max_time, time_slots - 1)
                self.assertGreaterEqual(meta["arrival_time"], 0)

    def test_arrival_mapping_uses_exact_integer_boundaries(self):
        self.assertEqual(map_arrival_from_latent(0, 8, 4), 0)
        self.assertEqual(map_arrival_from_latent(ARRIVAL_LATENT_DENOMINATOR - 1, 8, 4), 3)
        self.assertEqual(map_arrival_from_latent(ARRIVAL_LATENT_DENOMINATOR // 2, 8, 4), 2)

    def test_topology_and_capacity_prefix_identity(self):
        for seed in range(3):
            physical = {
                physical_base_fingerprint(self.results[(seed, time_slots)].instance)
                for time_slots in PAIRED_TEMPORAL_TIME_SLOTS
            }
            self.assertEqual(len(physical), 1)
            self.assertEqual(
                len({common_temporal_prefix_fingerprint(self.results[(seed, time_slots)].instance, 6) for time_slots in PAIRED_TEMPORAL_TIME_SLOTS}),
                1,
            )
            self.assertEqual(
                len({common_temporal_prefix_fingerprint(self.results[(seed, time_slots)].instance, 8) for time_slots in (8, 10, 12)}),
                1,
            )
            self.assertEqual(
                len({common_temporal_prefix_fingerprint(self.results[(seed, time_slots)].instance, 10) for time_slots in (10, 12)}),
                1,
            )

    def test_t_specific_full_instance_fingerprint_differs(self):
        for seed in range(3):
            self.assertEqual(
                len({full_instance_fingerprint(self.results[(seed, time_slots)].instance) for time_slots in PAIRED_TEMPORAL_TIME_SLOTS}),
                len(PAIRED_TEMPORAL_TIME_SLOTS),
            )
            self.assertEqual(
                len({full_temporal_flow_fingerprint(self.results[(seed, time_slots)].instance) for time_slots in PAIRED_TEMPORAL_TIME_SLOTS}),
                len(PAIRED_TEMPORAL_TIME_SLOTS),
            )

    def test_changing_t_does_not_alter_non_temporal_fields(self):
        for seed in range(3):
            reference = self.results[(seed, 8)].instance.flow_metadata["per_flow"]
            for time_slots in (6, 10, 12):
                other = self.results[(seed, time_slots)].instance.flow_metadata["per_flow"]
                for flow_id in range(160):
                    for field in (
                        "source_node",
                        "destination_node",
                        "vnf_label_sequence",
                        "processing_coefficient_sequence",
                        "base_demand",
                        "actual_demand",
                        "traffic_category",
                        "processing_stage_count",
                    ):
                        self.assertEqual(other[flow_id][field], reference[flow_id][field])

    def test_reproducibility_across_repeated_builder_calls(self):
        first = build_paired_temporal_instance(self._config(8, 2)).instance
        second = build_paired_temporal_instance(self._config(8, 2)).instance
        self.assertEqual(physical_base_fingerprint(first), physical_base_fingerprint(second))
        self.assertEqual(temporal_prefix_fingerprint(first, 8), temporal_prefix_fingerprint(second, 8))
        self.assertEqual(non_temporal_flow_fingerprint(first), non_temporal_flow_fingerprint(second))
        self.assertEqual(paired_timing_latent_fingerprint(first), paired_timing_latent_fingerprint(second))
        self.assertEqual(full_instance_fingerprint(first), full_instance_fingerprint(second))

    def test_clipping_zero_for_supported_design(self):
        for result in self.results.values():
            self.assertEqual(result.instance.flow_metadata["aggregate"]["deadline"]["clipping_count"], 0)
            self.assertEqual(result.instance.instance_metadata["paired_temporal_scalability"]["deadline_clipping_count"], 0)


if __name__ == "__main__":
    unittest.main()
