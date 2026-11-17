"""Unit tests for branch distance helpers (no live routing API calls)."""
from __future__ import annotations

import asyncio
import math
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))

from utils.branch_distance import (
    calculate_branch_distances,
    format_distance_label,
    haversine_km,
    validate_coordinates,
)


class BranchDistanceTests(unittest.TestCase):
    def test_validate_coordinates(self):
        self.assertEqual(validate_coordinates(17.4, 78.5), (17.4, 78.5))
        with self.assertRaises(ValueError):
            validate_coordinates(100, 0)
        with self.assertRaises(ValueError):
            validate_coordinates("x", 1)

    def test_haversine_known_short_distance(self):
        # ~same point
        d0 = haversine_km(17.4578, 78.5369, 17.4578, 78.5369)
        self.assertAlmostEqual(d0, 0.0, places=3)
        # Malkajgiri area → Balaji Nagar area (roughly a few km)
        d = haversine_km(17.4578, 78.5369, 17.4495, 78.4988)
        self.assertGreater(d, 1.0)
        self.assertLess(d, 15.0)

    def test_format_labels(self):
        self.assertEqual(format_distance_label(4.84, approximate=False), "4.8 km away")
        self.assertEqual(format_distance_label(4.84, approximate=True), "Approx. 4.8 km away")
        self.assertEqual(format_distance_label(12.56, approximate=False), "12.6 km away")
        self.assertEqual(format_distance_label(float("nan"), approximate=False), "")

    def test_calculate_haversine_fallback_without_api_key(self):
        async def _run():
            with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "", "BRANCH_DISTANCE_MODE": "routing"}, clear=False):
                with patch("utils.branch_distance._CACHE", {}):
                    payload = await calculate_branch_distances(
                        17.44,
                        78.50,
                        [
                            {"id": "b1", "latitude": 17.4578, "longitude": 78.5369},
                            {"id": "b2"},  # missing coords
                        ],
                    )
            return payload

        payload = asyncio.run(_run())
        self.assertEqual(payload["unit"], "km")
        self.assertEqual(payload["distance_type"], "straight_line")
        by_id = {r["branch_id"]: r for r in payload["results"]}
        self.assertEqual(by_id["b1"]["source"], "haversine")
        self.assertTrue(str(by_id["b1"]["distance_label"]).startswith("Approx."))
        self.assertIsNotNone(by_id["b1"]["distance_km"])
        self.assertIsNone(by_id["b2"]["distance_km"])
        self.assertEqual(by_id["b2"]["source"], "unavailable")
        self.assertTrue(by_id["b1"].get("nearest"))

    def test_routing_mocked_matrix(self):
        async def _run():
            fake = {"b1": 4.8, "b2": 7.2}

            async def fake_google(origin, destinations):
                return {d[0]: fake[d[0]] for d in destinations if d[0] in fake}

            with patch.dict(
                os.environ,
                {
                    "GOOGLE_MAPS_API_KEY": "test-key",
                    "BRANCH_DISTANCE_MODE": "routing",
                    "BRANCH_DISTANCE_CACHE_TTL": "0",
                },
                clear=False,
            ):
                with patch("utils.branch_distance._CACHE", {}):
                    with patch(
                        "utils.branch_distance._google_driving_distances",
                        new=AsyncMock(side_effect=fake_google),
                    ):
                        return await calculate_branch_distances(
                            17.4,
                            78.5,
                            [
                                {"id": "b1", "latitude": 17.45, "longitude": 78.53},
                                {"id": "b2", "latitude": 17.46, "longitude": 78.54},
                            ],
                        )

        payload = asyncio.run(_run())
        self.assertEqual(payload["distance_type"], "driving")
        by_id = {r["branch_id"]: r for r in payload["results"]}
        self.assertEqual(by_id["b1"]["source"], "routing")
        self.assertEqual(by_id["b1"]["distance_label"], "4.8 km away")
        self.assertFalse(str(by_id["b1"]["distance_label"]).startswith("Approx."))
        self.assertEqual(by_id["b2"]["distance_km"], 7.2)


if __name__ == "__main__":
    unittest.main()
