from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box

from metro.config import load_config
from metro.pricing import (
    MODEL_FEATURES, _blend_relative_score, _top_anchor_price_surface,
    apply_price_model, build_top_market_calibrations, fit_price_model,
    infer_comparable_city_baseline,
    market_model_matrix, model_matrix, normalize_listings,
    normalize_market_observations, save_artifact,
)


class PricingTests(unittest.TestCase):
    def test_comparable_city_baseline_uses_deposit_per_cell_ratio(self):
        cfg = load_config()
        cfg["price_model"]["comparable_city_fallback"]["n_neighbors"] = 2
        target = pd.DataFrame({"bank_deposits_php": [1_000.0] * 100})
        metadata = {"donor_city_profiles": {
            "small": {
                "city": "Small", "anchor_price_per_sqm_php": 20_000,
                "bank_deposits_per_land_cell_php": 5.0,
                "anchor_confidence_log_half_width": 0.25,
            },
            "large": {
                "city": "Large", "anchor_price_per_sqm_php": 80_000,
                "bank_deposits_per_land_cell_php": 20.0,
                "anchor_confidence_log_half_width": 0.25,
            },
        }}
        result = infer_comparable_city_baseline(cfg, target, metadata)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["target_bank_deposits_per_land_cell_php"], 10.0)
        # 20k * (10/5) and 80k * (10/20) both imply the same 40k baseline.
        self.assertAlmostEqual(result["price_per_sqm_php"], 40_000)
        self.assertEqual(len(result["donors"]), 2)

    def test_listing_normalization_keeps_commercial_vacant_land_only(self):
        cfg = load_config()
        raw = pd.DataFrame([
            {"property_type": "Commercial - Vacant Lot", "latitude": 10.3,
             "longitude": 123.9, "price_php": "₱2,000,000", "lot_area_sqm": "200 sqm",
             "source": "BDO", "source_url": "https://example.test/land.pdf",
             "observed_at": "2026-05-11"},
            {"property_type": "House and Lot", "latitude": 10.3,
             "longitude": 123.9, "price_php": 4_000_000, "lot_area_sqm": 200,
             "source": "BDO", "source_url": "https://example.test/land.pdf",
             "observed_at": "2026-05-11"},
        ])
        out = normalize_listings(raw, cfg)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["price_per_sqm_php"], 10_000)

    def test_market_observations_require_commercial_vacant_land(self):
        cfg = load_config()
        raw = pd.DataFrame([
            {"market_id": "cebu-lot", "city": "Cebu City", "province": "Cebu",
             "property_type": "Commercial vacant lot", "price_per_sqm_php": 18_000,
             "underlying_listings": 12, "observed_at": "2026-04-01",
             "source": "Bank", "source_url": "https://example.test/list.pdf"},
            {"market_id": "cebu-house", "city": "Cebu City", "province": "Cebu",
             "property_type": "Residential vacant lot", "price_per_sqm_php": 30_000,
             "underlying_listings": 1, "observed_at": "2026-04-01",
             "source": "Bank", "source_url": "https://example.test/list.pdf"},
        ])
        out = normalize_market_observations(raw, cfg)
        self.assertEqual(list(out["market_id"]), ["cebu-lot"])
        frame = pd.DataFrame({
            "city_population": [1_000_000], "bank_deposits_php": [500e9],
            "bank_accounts": [2_000_000], "bank_offices": [500],
            "bank_deposits_per_capita_php": [500_000],
        })
        self.assertTrue(np.isfinite(market_model_matrix(frame).iloc[0, :5]).all())

    def test_grouped_model_training(self):
        cfg = load_config()
        rows = []
        rng = np.random.default_rng(7)
        for city_i, city in enumerate(("cebu", "davao", "iloilo")):
            for i in range(24):
                dist = i / 4
                access = np.exp(-dist / 2) * 30
                price = (20_000 + city_i * 8_000) * (0.55 + np.exp(-dist / 5))
                rows.append({
                    "city_group": city,
                    "price_per_sqm_php": price * rng.lognormal(0, 0.05),
                    "source": "test",
                    "dist_cbd_km": dist,
                    "dist_major_road_km": dist / 5,
                    "establishment_access": access,
                    "poi_weighted_density": max(0, 12 - i / 2),
                    "road_density_km": max(0, 8 - i / 4),
                    "poi_count": max(0, 10 - i // 3),
                    "builtup_score": max(0, 1 - i / 30),
                    "in_metro": i < 18,
                    "city_population": 500_000 + city_i * 300_000,
                    "bank_deposits_php": 200e9 + city_i * 100e9,
                    "bank_deposits_per_capita_php": 400_000,
                    "local_tax_revenue_php": np.nan,
                    "local_tax_revenue_per_capita_php": np.nan,
                    "real_property_tax_php": np.nan,
                    "business_tax_php": np.nan,
                })
        training = pd.DataFrame(rows)
        self.assertEqual(list(model_matrix(training).columns), list(MODEL_FEATURES))
        bundle = fit_price_model(cfg, training)
        meta = bundle["metadata"]
        self.assertEqual(meta["validation"], "nested_grouped_by_city")
        self.assertEqual(meta["n_labels"], 72)
        self.assertTrue(np.isfinite(meta["mae_php_sqm"]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.joblib"
            save_artifact(bundle, path)
            cfg["price_model"]["artifact"] = str(path)
            sample = training.head(3).copy()
            sample["land_value_score"] = [0.3, 0.6, 0.9]
            sample.loc[sample.index[-1], "in_metro"] = False
            sample = gpd.GeoDataFrame(
                sample,
                geometry=[
                    box(123.80 + i * 0.01, 10.2, 123.81 + i * 0.01, 10.21)
                    for i in range(3)
                ],
                crs="EPSG:4326",
            )
            predicted = apply_price_model(cfg, sample)
            self.assertTrue(predicted.iloc[:2]["land_price_php_sqm"].notna().all())
            self.assertTrue(pd.isna(predicted.iloc[-1]["land_price_php_sqm"]))
            priced = predicted[predicted["in_metro"]]
            self.assertTrue((priced["land_price_low_php_sqm"] <=
                             priced["land_price_php_sqm"]).all())
            self.assertTrue((priced["land_price_high_php_sqm"] >=
                             priced["land_price_php_sqm"]).all())

    def test_relative_score_blend_preserves_area_weighted_mean(self):
        cfg = load_config()
        cfg["price_model"]["relative_score_blend"] = 1.0
        gdf = gpd.GeoDataFrame(
            {"land_value_score": [1.0, 3.0]},
            geometry=[box(123.8, 10.2, 123.81, 10.21), box(123.81, 10.2, 123.82, 10.21)],
            crs="EPSG:4326",
        )
        result = _blend_relative_score(cfg, gdf, np.array([20_000.0, 20_000.0]))
        self.assertAlmostEqual(float(result.mean()), 20_000, delta=5)
        self.assertAlmostEqual(float(result[1] / result[0]), 3.0, places=2)

    def test_top_market_anchor_is_robust_and_applied_at_top_score_quantile(self):
        cfg = load_config()
        raw = pd.DataFrame([
            {
                "market_id": f"cebu-{i}", "city": "Cebu City", "province": "Cebu",
                "market_area": "Lahug", "property_type": "commercial vacant lot",
                "price_per_sqm_php": price, "underlying_listings": 1,
                "observed_at": "2026-03-31", "source": "test",
                "source_url": f"https://example.test/{i}",
            }
            for i, price in enumerate(
                [20_000, 30_000, 34_000, 59_500, 65_000, 65_500, 70_000, 90_000]
            )
        ])
        anchors = normalize_market_observations(raw, cfg)
        calibration = build_top_market_calibrations(cfg, anchors)["cebu"]
        self.assertEqual(calibration["role"], "top_market_anchor")
        self.assertAlmostEqual(calibration["price_per_sqm_php"], 59_500)
        self.assertLess(calibration["confidence_log_half_width"], 0.35)

        scores = np.linspace(0.05, 1.0, 101)
        gdf = gpd.GeoDataFrame(
            {"land_value_score": scores},
            geometry=[box(123.8, 10.2, 123.81, 10.21)] * len(scores),
            crs="EPSG:4326",
        )
        surface = _top_anchor_price_surface(cfg, gdf, calibration)
        anchor_cell = int(round(0.9 * (len(surface) - 1)))
        self.assertAlmostEqual(surface[anchor_cell], 59_500, delta=1_000)
        self.assertTrue(np.all(np.diff(surface) >= 0))


if __name__ == "__main__":
    unittest.main()
