from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable

import yaml
from sqlalchemy.orm import Session

from app.core.config import settings


@dataclass(frozen=True)
class PluginSpec:
    name: str
    callable_path: str
    enabled_setting: str | None = None


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_plugins(path: str) -> list[PluginSpec]:
    cfg = _load_yaml(path)
    out: list[PluginSpec] = []
    for p in cfg.get("plugins", []) or []:
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        callable_path = p.get("callable")
        if not name or not callable_path:
            continue
        out.append(
            PluginSpec(
                name=name,
                callable_path=callable_path,
                enabled_setting=p.get("enabled_setting"),
            )
        )
    return out


def resolve_callable(callable_path: str) -> Callable[[Session], Any]:
    # format: module.submodule:function_name
    if ":" not in callable_path:
        raise ValueError("callable must be module:function")
    mod, fn = callable_path.split(":", 1)
    m = importlib.import_module(mod)
    f = getattr(m, fn)
    if not callable(f):
        raise ValueError("resolved object not callable")
    return f


def run_plugins(db: Session, *, config_path: str) -> list[dict[str, Any]]:
    specs = load_plugins(config_path)
    results: list[dict[str, Any]] = []
    for s in specs:
        try:
            enabled = True
            if s.enabled_setting:
                enabled = bool(getattr(settings, s.enabled_setting, False))
            if not enabled:
                results.append(
                    {
                        "plugin": s.name,
                        "skipped": True,
                        "reason": f"{s.enabled_setting}=false",
                    }
                )
                continue
            f = resolve_callable(s.callable_path)
            results.append({"plugin": s.name, "result": f(db)})
        except Exception as e:
            db.rollback()
            results.append({"plugin": s.name, "error": str(e)})
    return results
