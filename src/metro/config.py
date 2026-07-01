"""Configuration loading. Single source of truth = ../../config.yaml."""
from __future__ import annotations

import copy
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


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    return _wrap(raw)


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
