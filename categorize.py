"""Bucket expense-line labels into the ledger's named payment columns
(Bank / PTM / UPI / T-Sale / Fleet / R-Babu / Ranjit / Others) using an
editable, per-company keyword-rules file. Anything that matches no rule is
left uncategorized — it stays in the sheet's residual P-Exp bucket, which is
a formula (T-Exp minus the named columns), not something entered by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import db

SEED_RULES_PATH = Path(__file__).parent / 'rules.json'

COLUMNS = ["Bank", "PTM", "UPI", "T-Sale", "Fleet", "R-Babu", "Ranjit", "Others"]


def _seed() -> dict:
    with open(SEED_RULES_PATH, encoding='utf-8') as f:
        return json.load(f)


def load_rules(company_id: int) -> dict:
    """Every company gets its own rules, seeded from rules.json the first
    time (a brand-new company starts from the same sensible defaults, then
    the owner tunes it as new vendor/driver names show up)."""
    data = db.load_rules(company_id)
    if data is None:
        data = _seed()
        db.save_rules(company_id, data)
    return data


def save_rules(company_id: int, data: dict) -> None:
    db.save_rules(company_id, data)


def categorize(label: str, rules: dict) -> str | None:
    """Return the column name the label matches, or None for the residual
    petty-cash bucket."""
    columns = rules.get('columns', COLUMNS)
    patterns = rules.get('rules', {})
    low = label.lower()
    for col in columns:
        for pat in patterns.get(col, []):
            if re.search(pat, low, re.IGNORECASE):
                return col
    return None


def categorize_lines(expense_lines: list, rules: dict) -> list[dict]:
    """expense_lines: list of objects with .label and .amount (or dicts with
    those keys). Returns a list of {label, amount, column} dicts — column is
    None for the residual bucket."""
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
