from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from metro.config import load_config
from metro.landvalue import COMPONENT_NAMES, effective_weights
from metro.weight_training import fit_weight_model


class WeightTrainingTests(unittest.TestCase):
    def test_positive_weight_training_respects_domain_floors(self):
        cfg = load_config()
        rng = np.random.default_rng(4)
        X = rng.uniform(0.05, 1.0, size=(8, len(COMPONENT_NAMES)))
        target_weights = np.array([0.15, 0.30, 0.25, 0.20, 0.10])
        prices = np.exp(9.8 + 1.2 * X.dot(target_weights))
        training = pd.DataFrame({
            "market_area": [f"market-{i}" for i in range(len(X))],
            "price_per_sqm_php": prices,
            "listing_observations": [2] * len(X),
            "price_source_url": [f"https://example.test/price/{i}" for i in range(len(X))],
            "location_source_url": [f"https://example.test/map/{i}" for i in range(len(X))],
        })
        for i, name in enumerate(COMPONENT_NAMES):
            training[f"norm_{name}"] = X[:, i]

        metadata = fit_weight_model(cfg, training)
        learned = metadata["weights"]
        floors = cfg["weight_model"]["minimum_weights"]
        self.assertAlmostEqual(sum(learned.values()), 1.0, places=6)
        for name in COMPONENT_NAMES:
            self.assertGreaterEqual(learned[name] + 1e-8, floors[name])
        self.assertEqual(metadata["validation"], "leave_one_market_area_out")

        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "weights.json"
            artifact.write_text(json.dumps(metadata))
            cfg["weight_model"]["artifact"] = str(artifact)
            loaded = effective_weights(cfg)
        self.assertAlmostEqual(sum(loaded.values()), 1.0, places=6)
        self.assertAlmostEqual(loaded["access_major_road"], learned["access_major_road"])


if __name__ == "__main__":
    unittest.main()
