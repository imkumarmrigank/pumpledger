"""Bucket expense-line labels into the ledger's named payment columns
(Bank / PTM / UPI / T-Sale / Fleet / R-Babu / Ranjit / Others) using an
editable keyword-rules file. Anything that matches no rule is left
uncategorized — it stays in the sheet's residual P-Exp bucket, which is a
formula (T-Exp minus the named columns), not something entered by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import db

RULES_PATH = Path(__file__).parent / 'rules.json'

COLUMNS = ["Bank", "PTM", "UPI", "T-Sale", "Fleet", "R-Babu", "Ranjit", "Others"]


def _load_local_file(path: Path = RULES_PATH) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_rules(path: Path = RULES_PATH) -> dict:
    """Rules live in Postgres when DATABASE_URL is set (so edits made in the
    deployed app persist across restarts); rules.json in the repo is only the
    seed used the first time the table is empty, and the local-dev fallback."""
    if db.is_configured():
        data = db.load_rules()
        if data is None:
            data = _load_local_file(path)
            db.save_rules(data)
        return data
    return _load_local_file(path)


def save_rules(data: dict, path: Path = RULES_PATH) -> None:
    if db.is_configured():
        db.save_rules(data)
        return
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def categorize(label: str, rules: dict | None = None) -> str | None:
    """Return the column name the label matches, or None for the residual
    petty-cash bucket."""
    rules = rules or load_rules()
    columns = rules.get('columns', COLUMNS)
    patterns = rules.get('rules', {})
    low = label.lower()
    for col in columns:
        for pat in patterns.get(col, []):
            if re.search(pat, low, re.IGNORECASE):
                return col
    return None


def categorize_lines(expense_lines: list, rules: dict | None = None) -> list[dict]:
    """expense_lines: list of objects with .label and .amount (or dicts with
    those keys). Returns a list of {label, amount, column} dicts — column is
    None for the residual bucket."""
    rules = rules or load_rules()
    out = []
    for line in expense_lines:
        label = line.label if hasattr(line, 'label') else line['label']
        amount = line.amount if hasattr(line, 'amount') else line['amount']
        out.append({'label': label, 'amount': amount, 'column': categorize(label, rules)})
    return out


def column_totals(categorized: list[dict]) -> dict[str, float]:
    """Sum amounts per column (Bank/PTM/UPI/.../Others), plus 'Petty' for the
    residual (unmatched) lines."""
    totals = {c: 0.0 for c in COLUMNS}
    totals['Petty'] = 0.0
    for row in categorized:
        col = row['column'] or 'Petty'
        totals[col] = totals.get(col, 0.0) + (row['amount'] or 0.0)
    return totals
