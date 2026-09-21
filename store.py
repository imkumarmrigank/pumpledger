"""Persistence for reviewed/confirmed days.

Uses Neon Postgres (via db.py) when DATABASE_URL is set — required on Render,
since its filesystem is ephemeral and wiped on every deploy/restart. Falls
back to a local JSON file for plain local dev with no database configured.
"""
from __future__ import annotations

import json
from pathlib import Path

import db

STORE_PATH = Path(__file__).parent / 'data' / 'store.json'


def _load_local() -> dict:
    if not STORE_PATH.exists():
        return {}
    with open(STORE_PATH, encoding='utf-8') as f:
        return json.load(f)


def _save_local(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def load() -> dict:
    if db.is_configured():
        return db.load_days()
    return _load_local()


def save(data: dict) -> None:
    """Bulk save — used by the one-off compilation script. Writes every
    day individually so it works the same way against Postgres or the local
    file."""
    if db.is_configured():
        for k, v in data.items():
            db.save_day(int(k), v)
        return
    _save_local(data)


def save_day(day_key: str, day_data: dict) -> None:
    if db.is_configured():
        db.save_day(int(day_key), day_data)
        return
    data = _load_local()
    data[day_key] = day_data
    _save_local(data)


def delete_day(day_key: str) -> None:
    if db.is_configured():
        db.delete_day(int(day_key))
        return
    data = _load_local()
    data.pop(day_key, None)
    _save_local(data)
