from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metro.config import load_config
from scripts import export_webapp


class ManifestTests(unittest.TestCase):
    def test_register_exports_preserves_saved_cities_by_default(self):
        cfg = load_config()
        cebu = {"slug": "cebu_city", "name": "Cebu City"}
        iloilo = {"slug": "iloilo_city", "name": "Iloilo City"}

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(export_webapp, "WEBAPP_DATA", Path(tmp)):
                export_webapp.register_exports(cfg, [cebu], replace=True)
                manifest = export_webapp.register_exports(cfg, [iloilo])

        self.assertEqual(
            [city["slug"] for city in manifest["cities"]],
            ["cebu_city", "iloilo_city"],
        )

    def test_register_exports_only_replaces_when_explicit(self):
        cfg = load_config()
        cebu = {"slug": "cebu_city", "name": "Cebu City"}
        iloilo = {"slug": "iloilo_city", "name": "Iloilo City"}

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(export_webapp, "WEBAPP_DATA", Path(tmp)):
                export_webapp.register_exports(cfg, [cebu], replace=True)
                manifest = export_webapp.register_exports(
                    cfg, [iloilo], replace=True,
                )

        self.assertEqual(
            [city["slug"] for city in manifest["cities"]],
            ["iloilo_city"],
        )


if __name__ == "__main__":
    unittest.main()
