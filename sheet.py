"""Read/write the Bajrang Petroleum daily-ledger workbook.

The workbook has one sheet, one row per day (row 3 = day 1, row N+2 = day N),
and a fixed set of columns A..Y. Columns E, H, L, V, W, X, Y are formulas in
the original template (e.g. E = C*D, V = SUM(N:U), W = M-V, Y = L-X) and are
never touched here — only the "input" columns are written, exactly the way
the xlsx-editing convention expects: find the designated input cells, write
only there, leave every existing formula untouched.
"""
from __future__ import annotations

import calendar
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

HEADER_LABELS = [
    'Date', 'OB', 'HSD', 'Rate', 'Amt', 'MS', 'Rate', 'Amt', 'Lub', 'Cofee',
    'Collection', 'Total', 'T-Exp', 'Bank', 'PTM', 'UPI', 'T-Sale', 'Fleet',
    'R-Babu', 'Ranjit', 'Others', 'Total', 'P-Exp', 'Total', 'Balance',
]

COL = {
    'day': 'A', 'ob': 'B', 'hsd_unit': 'C', 'hsd_rate': 'D', 'hsd_amt': 'E',
    'ms_unit': 'F', 'ms_rate': 'G', 'ms_amt': 'H', 'lub': 'I', 'coffee': 'J',
    'collection': 'K', 'total_inflow': 'L', 'total_exp': 'M',
    'bank': 'N', 'ptm': 'O', 'upi': 'P', 'tsale': 'Q', 'fleet': 'R',
    'rbabu': 'S', 'ranjit': 'T', 'others': 'U',
    'v_sum': 'V', 'w_pexp': 'W', 'x_total': 'X', 'balance': 'Y',
}
FIRST_DAY_ROW = 3


def row_for_day(day: int) -> int:
    return FIRST_DAY_ROW + day - 1


@dataclass
class DayEntry:
    day: int
    opening_cash: float | None = None   # only used for day 1 (B is a formula for every other day)
    hsd_unit: float | None = None
    hsd_rate: float | None = None
    ms_unit: float | None = None
    ms_rate: float | None = None
    lub: float = 0.0
    coffee: float = 0.0
    collection: float = 0.0
    total_expenses: float | None = None   # M — the slip's stated "T-Exp" / total expenses
    bank: float = 0.0
    ptm: float = 0.0
    upi: float = 0.0
    tsale: float = 0.0
    fleet: float = 0.0
    rbabu: float = 0.0
    ranjit: float = 0.0
    others: float = 0.0
    source_images: list = field(default_factory=list)


def load_workbook(path: str):
    return openpyxl.load_workbook(path, data_only=False)


def load_existing_snapshot(path: str) -> dict[int, dict]:
    """Cached (already-computed) values per day from the ORIGINAL file, used
    as the 'what the sheet currently says' baseline for reconciliation."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    snap = {}
    for day in range(1, 32):
        row = row_for_day(day)
        vals = {name: ws[f'{letter}{row}'].value for name, letter in COL.items()}
        if all(v is None for k, v in vals.items() if k != 'day'):
            continue
        snap[day] = vals
    return snap


def write_day(ws, entry: DayEntry) -> None:
    row = row_for_day(entry.day)
    ws[f"{COL['day']}{row}"] = entry.day
    if entry.day == 1 and entry.opening_cash is not None:
        ws[f"{COL['ob']}{row}"] = entry.opening_cash
    if entry.hsd_unit is not None:
        ws[f"{COL['hsd_unit']}{row}"] = entry.hsd_unit
    if entry.hsd_rate is not None:
        ws[f"{COL['hsd_rate']}{row}"] = entry.hsd_rate
    if entry.ms_unit is not None:
        ws[f"{COL['ms_unit']}{row}"] = entry.ms_unit
    if entry.ms_rate is not None:
        ws[f"{COL['ms_rate']}{row}"] = entry.ms_rate
    ws[f"{COL['lub']}{row}"] = entry.lub or None
    ws[f"{COL['coffee']}{row}"] = entry.coffee or None
    ws[f"{COL['collection']}{row}"] = entry.collection or None
    if entry.total_expenses is not None:
        ws[f"{COL['total_exp']}{row}"] = entry.total_expenses
    ws[f"{COL['bank']}{row}"] = entry.bank or None
    ws[f"{COL['ptm']}{row}"] = entry.ptm or None
    ws[f"{COL['upi']}{row}"] = entry.upi or None
    ws[f"{COL['tsale']}{row}"] = entry.tsale or None
    ws[f"{COL['fleet']}{row}"] = entry.fleet or None
    ws[f"{COL['rbabu']}{row}"] = entry.rbabu or None
    ws[f"{COL['ranjit']}{row}"] = entry.ranjit or None
    ws[f"{COL['others']}{row}"] = entry.others or None


def build_reconciled_workbook(base_path: str, out_path: str, entries: dict[int, DayEntry]) -> None:
    """Copy the original file (preserving every formula/format we don't
    touch) and overwrite the input cells for each reconciled day."""
    shutil.copyfile(base_path, out_path)
    wb = load_workbook(out_path)
    ws = wb[wb.sheetnames[0]]
    for entry in entries.values():
        write_day(ws, entry)
    wb.save(out_path)


def computed_inflow_total(entry: DayEntry, opening_cash_for_calc: float) -> float:
    hsd_amt = (entry.hsd_unit or 0) * (entry.hsd_rate or 0)
    ms_amt = (entry.ms_unit or 0) * (entry.ms_rate or 0)
    return opening_cash_for_calc + hsd_amt + ms_amt + entry.lub + entry.coffee + entry.collection


def computed_named_total(entry: DayEntry) -> float:
    return (entry.bank + entry.ptm + entry.upi + entry.tsale + entry.fleet
            + entry.rbabu + entry.ranjit + entry.others)


def computed_balance(entry: DayEntry, opening_cash_for_calc: float) -> float:
    inflow = computed_inflow_total(entry, opening_cash_for_calc)
    named = computed_named_total(entry)
    total_exp = entry.total_expenses or 0.0
    pexp = total_exp - named
    total_out = named + pexp
    return inflow - total_out


def _populate_month_sheet(ws, title: str, year: int, month: int, entries: dict[int, DayEntry]) -> None:
    """Write one month's worth of day-rows (with the same formula structure
    as the original hand-built template) into a fresh sheet."""
    ws['A1'] = title
    ws['A1'].font = Font(bold=True, size=13)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEADER_LABELS))
    ws['A1'].alignment = Alignment(horizontal='center')

    header_fill = PatternFill('solid', fgColor='DDEBF7')
    for i, label in enumerate(HEADER_LABELS, start=1):
        c = ws.cell(row=2, column=i, value=label)
        c.font = Font(bold=True)
        c.fill = header_fill

    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        row = FIRST_DAY_ROW + day - 1
        entry = entries.get(day)
        ws[f"{COL['day']}{row}"] = day
        if day == 1:
            ws[f"{COL['ob']}{row}"] = entry.opening_cash if entry and entry.opening_cash is not None else None
        else:
            ws[f"{COL['ob']}{row}"] = f"=Y{row - 1}"
        if entry:
            ws[f"{COL['hsd_unit']}{row}"] = entry.hsd_unit
            ws[f"{COL['hsd_rate']}{row}"] = entry.hsd_rate
            ws[f"{COL['ms_unit']}{row}"] = entry.ms_unit
            ws[f"{COL['ms_rate']}{row}"] = entry.ms_rate
            ws[f"{COL['lub']}{row}"] = entry.lub or None
            ws[f"{COL['coffee']}{row}"] = entry.coffee or None
            ws[f"{COL['collection']}{row}"] = entry.collection or None
            ws[f"{COL['total_exp']}{row}"] = entry.total_expenses
            ws[f"{COL['bank']}{row}"] = entry.bank or None
            ws[f"{COL['ptm']}{row}"] = entry.ptm or None
            ws[f"{COL['upi']}{row}"] = entry.upi or None
            ws[f"{COL['tsale']}{row}"] = entry.tsale or None
            ws[f"{COL['fleet']}{row}"] = entry.fleet or None
            ws[f"{COL['rbabu']}{row}"] = entry.rbabu or None
            ws[f"{COL['ranjit']}{row}"] = entry.ranjit or None
            ws[f"{COL['others']}{row}"] = entry.others or None
        ws[f"{COL['hsd_amt']}{row}"] = f"=C{row}*D{row}"
        ws[f"{COL['ms_amt']}{row}"] = f"=F{row}*G{row}"
        ws[f"{COL['total_inflow']}{row}"] = f"=B{row}+E{row}+H{row}+I{row}+J{row}+K{row}"
        ws[f"{COL['v_sum']}{row}"] = f"=SUM(N{row}:U{row})"
        ws[f"{COL['w_pexp']}{row}"] = f"=M{row}-V{row}"
        ws[f"{COL['x_total']}{row}"] = f"=SUM(V{row}:W{row})"
        ws[f"{COL['balance']}{row}"] = f"=L{row}-X{row}"

    total_row = FIRST_DAY_ROW + num_days
    first, last = FIRST_DAY_ROW, total_row - 1
    ws[f"A{total_row}"] = 'Total'
    ws[f"A{total_row}"].font = Font(bold=True)
    for key in ('hsd_unit', 'hsd_amt', 'ms_unit', 'ms_amt', 'lub', 'coffee', 'collection',
                'total_inflow', 'bank', 'ptm', 'upi', 'tsale', 'fleet', 'rbabu', 'ranjit',
                'others', 'v_sum', 'w_pexp'):
        letter = COL[key]
        ws[f"{letter}{total_row}"] = f"=SUM({letter}{first}:{letter}{last})"
    ws[f"{COL['x_total']}{total_row}"] = f"=SUM(N{total_row}:W{total_row})"
    ws[f"{COL['balance']}{total_row}"] = f"=L{total_row}-X{total_row}"

    ws.column_dimensions['A'].width = 12
    for col in 'BCDEFGHIJKLMNOPQRSTUVWXY':
        ws.column_dimensions[col].width = 11


def build_export_workbook(out_path: str, company_name: str, entries_by_month: dict[tuple[int, int], dict[int, DayEntry]]) -> None:
    """One sheet per (year, month), named e.g. 'Sep 2026', each with the same
    day-row layout and formulas as the original hand-built template."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for (year, month), entries in sorted(entries_by_month.items()):
        sheet_name = f"{calendar.month_abbr[month]} {year}"[:31]
        ws = wb.create_sheet(sheet_name)
        title = f"{company_name}-{calendar.month_abbr[month]}-{str(year)[-2:]}"
        _populate_month_sheet(ws, title, year, month, entries)
    wb.save(out_path)
