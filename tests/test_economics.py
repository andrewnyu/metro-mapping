from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from metro.config import load_config
from metro.economics import attach_area_features, normalize_area_name


class EconomicsTests(unittest.TestCase):
    def test_normalize_area_name(self):
        self.assertEqual(normalize_area_name("City of Cebu, Philippines"), "cebu")
        self.assertEqual(normalize_area_name("Cebu City"), "cebu")

    def test_attaches_metro_values_and_per_capita(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "economic.csv"
            pd.DataFrame([{
                "core_city": "Cebu", "deposit_scope": "metro",
                "deposit_area": "Metro Cebu", "period": "2025-12-31",
                "bank_deposits_php": 700_000_000_000,
                "bank_accounts": 3_000_000, "bank_offices": 480,
                "city_population": 1_000_000,
                "local_tax_revenue_php": 10_000_000_000,
            }]).to_csv(path, index=False)
            cfg = load_config()
            cfg["economics"]["reference_file"] = str(path)
            out = attach_area_features(cfg, pd.DataFrame({"x": [1, 2]}))
            self.assertTrue((out["bank_deposits_php"] == 700_000_000_000).all())
            self.assertTrue((out["bank_deposits_per_capita_php"] == 700_000).all())
            self.assertEqual(out.attrs["economics"]["deposit_scope"], "metro")


if __name__ == "__main__":
    unittest.main()
