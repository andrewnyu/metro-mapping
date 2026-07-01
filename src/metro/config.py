"""Configuration loading. Single source of truth = ../../config.yaml."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

# Repo root = two levels up from this file (src/metro/config.py -> repo/).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class Config(dict):
    """Thin dict wrapper with dotted access, e.g. cfg['grid']['h3_resolution']."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc

    @property
    def data_dir(self) -> Path:
        d = REPO_ROOT / self["paths"]["data_dir"]
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cache_dir(self) -> Path:
        d = self.data_dir / self["paths"]["cache_subdir"]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def city_slug(self) -> str:
        """Filesystem-safe id for the current city, used in cache filenames."""
        return (
            self["city"]["place"].split(",")[0].strip().lower().replace(" ", "_")
        )

    def city_cache_slug(self) -> str:
        """Cache key for a place, including exact OSM ID when configured."""
        slug = self.city_slug()
        osm_id = self.get("city", {}).get("osm_id")
        if not osm_id:
            return slug
        safe_id = normalise_osm_id(osm_id).lower()
        return f"{slug}_{safe_id}"


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    return _wrap(raw)


def normalise_osm_id(osm_id: str) -> str:
    """Return Nominatim lookup form, accepting URLs and bare numeric IDs."""
    osm_id = str(osm_id).strip()
    if not osm_id:
        raise ValueError("OSM ID cannot be empty")
    url_match = re.search(r"openstreetmap\.org/(node|way|relation)/(\d+)", osm_id, re.I)
    if url_match:
        prefix = {"node": "N", "way": "W", "relation": "R"}[url_match.group(1).lower()]
        return f"{prefix}{url_match.group(2)}"
    type_match = re.search(r"\b(node|way|relation)\b\D*(\d+)", osm_id, re.I)
    if type_match:
        prefix = {"node": "N", "way": "W", "relation": "R"}[type_match.group(1).lower()]
        return f"{prefix}{type_match.group(2)}"
    short_match = re.fullmatch(r"([NWRnwr])\D*(\d+)", osm_id)
    if short_match:
        return f"{short_match.group(1).upper()}{short_match.group(2)}"
    if osm_id.isdigit():
        return f"R{osm_id}"
    raise ValueError("OSM ID must look like R123, W123, N123, or an OpenStreetMap URL")


def _wrap(obj: Any) -> Any:
    if isinstance(obj, dict):
        return Config({k: _wrap(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_wrap(v) for v in obj]
    return obj


def merge_overrides(cfg: Config, overrides: dict | None) -> Config:
    """Return a deep copy of cfg with `overrides` applied (used by the app sliders)."""
    if not overrides:
        return cfg
    merged = copy.deepcopy(dict(cfg))
    _deep_update(merged, overrides)
    return _wrap(merged)


def _deep_update(base: dict, upd: dict) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
