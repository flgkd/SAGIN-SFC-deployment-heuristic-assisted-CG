import unittest

from result_schema import OptimalityClaims, RMPResult


class ResultOptimalityScopeTest(unittest.TestCase):
    def test_restricted_rmp_optimality_does_not_imply_global(self):
        result = RMPResult(
            accepted_count=1,
            rejected_count=0,
            optimality=OptimalityClaims(
                restricted_pool_integer_optimal=True,
                global_original_integer_optimal=False,
            ),
        )
        self.assertTrue(result.optimality.restricted_pool_integer_optimal)
        self.assertFalse(result.optimality.global_original_integer_optimal)


if __name__ == "__main__":
    unittest.main()
