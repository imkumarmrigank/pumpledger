"""Shared helpers used by every page: company selection, date-range presets,
turning a stored day dict into a sheet.DayEntry, and rolling entries up into
the Cash Reconciliation / P&L / Ledger summaries."""
from __future__ import annotations

import calendar
from datetime import date

import streamlit as st

import categorize
import db
import sheet

MONTH_NAMES = list(calendar.month_name)


def require_db():
    if not db.is_configured():
        st.error(
            'No DATABASE_URL is configured. This app needs a Postgres database (Neon) to store '
            'companies, vouchers and reconciled days — set the DATABASE_URL environment variable '
            'and reload.'
        )
        st.stop()


def pick_company() -> tuple[int, str]:
    """Sidebar company switcher, shared across every page via session_state.
    Returns (company_id, company_name); stops the page if none exists yet."""
    require_db()
    companies = db.list_companies()
    with st.sidebar:
        st.subheader('Company')
        if not companies:
            st.info('No companies yet — add one below.')
        else:
            options = {c['name']: c['id'] for c in companies}
            default_name = st.session_state.get('company_name')
            names = list(options.keys())
            index = names.index(default_name) if default_name in names else 0
            chosen = st.selectbox('Active company', names, index=index, key='company_picker')
            st.session_state['company_id'] = options[chosen]
            st.session_state['company_name'] = chosen

        with st.expander('+ Add a new company'):
            new_name = st.text_input('Company name', key='new_company_name')
            if st.button('Create company', key='create_company_btn') and new_name.strip():
                cid = db.create_company(new_name.strip())
                st.session_state['company_id'] = cid
                st.session_state['company_name'] = new_name.strip()
                st.success(f'Created "{new_name.strip()}"')
                st.rerun()

    if 'company_id' not in st.session_state:
        st.warning('Add a company in the sidebar to get started.')
        st.stop()
    return st.session_state['company_id'], st.session_state['company_name']


def date_range_picker(key_prefix: str = '') -> tuple[date, date, str]:
    """A preset + custom date-range picker. Returns (start, end, label)."""
    today = date.today()
    presets = ['This month', 'Last month', 'This quarter', 'This year', 'Custom range']
    choice = st.radio('Period', presets, horizontal=True, key=f'{key_prefix}_period')

    if choice == 'This month':
        start = today.replace(day=1)
        end = today
        label = today.strftime('%B %Y')
    elif choice == 'Last month':
        first_this = today.replace(day=1)
        last_month_end = first_this - __import__('datetime').timedelta(days=1)
        start = last_month_end.replace(day=1)
        end = last_month_end
        label = start.strftime('%B %Y')
    elif choice == 'This quarter':
        q = (today.month - 1) // 3
        start = date(today.year, q * 3 + 1, 1)
        end = today
        label = f'Q{q + 1} {today.year}'
    elif choice == 'This year':
        start = date(today.year, 1, 1)
        end = today
        label = str(today.year)
    else:
        c1, c2 = st.columns(2)
        start = c1.date_input('From', value=today.replace(day=1), key=f'{key_prefix}_start')
        end = c2.date_input('To', value=today, key=f'{key_prefix}_end')
        label = f'{start.isoformat()} to {end.isoformat()}'

    return start, end, label


def entry_from_day_data(day: int, d: dict, rules: dict) -> sheet.DayEntry:
    # expense_lines were categorized at save time, but re-run through current
    # rules so edits to the rules page retroactively re-bucket old days too
    cat = categorize.categorize_lines(d.get('expense_lines', []), rules)
    totals = categorize.column_totals(cat)
    return sheet.DayEntry(
        day=day,
        opening_cash=d.get('opening_cash'),
        hsd_unit=d.get('hsd_unit'), hsd_rate=d.get('hsd_rate'),
        ms_unit=d.get('ms_unit'), ms_rate=d.get('ms_rate'),
        lub=d.get('lub') or 0, coffee=d.get('coffee') or 0, collection=d.get('collection') or 0,
        total_expenses=d.get('stated_total_expenses'),
        bank=totals.get('Bank', 0), ptm=totals.get('PTM', 0), upi=totals.get('UPI', 0),
        tsale=totals.get('T-Sale', 0), fleet=totals.get('Fleet', 0),
        rbabu=totals.get('R-Babu', 0), ranjit=totals.get('Ranjit', 0), others=totals.get('Others', 0),
        source_images=d.get('source_images', []),
    )


def load_range(company_id: int, start: date, end: date) -> dict:
    """{date_iso: day_dict} for every reconciled day in [start, end]."""
    return db.load_days_range(company_id, start, end)


def cash_reconciliation_summary(day_rows: dict, rules: dict) -> dict:
    """Aggregate a set of {date_iso: day_dict} into the 'Cash Reconciliation'
    rollup: opening cash of the first day, closing balance of the last day,
    and sums of every inflow/expense column in between — mirrors the
    single-day formulas (L = B+E+H+I+J+K, X = V+W, Y = L-X) applied to a
    whole range instead of one row."""
    if not day_rows:
        return None
    dates_sorted = sorted(day_rows.keys())
    first_d, last_d = dates_sorted[0], dates_sorted[-1]

    sums = dict(hsd_amt=0.0, ms_amt=0.0, coffee=0.0, lub=0.0, collection=0.0,
                bank=0.0, ptm=0.0, upi=0.0, tsale=0.0, fleet=0.0, rbabu=0.0, ranjit=0.0,
                others=0.0, pump_expenses=0.0, total_expenses=0.0)

    for date_iso in dates_sorted:
        d = day_rows[date_iso]
        day_num = int(date_iso.split('-')[2])
        entry = entry_from_day_data(day_num, d, rules)
        hsd_amt = (entry.hsd_unit or 0) * (entry.hsd_rate or 0)
        ms_amt = (entry.ms_unit or 0) * (entry.ms_rate or 0)
        named = sheet.computed_named_total(entry)
        petty = (entry.total_expenses or 0) - named
        sums['hsd_amt'] += hsd_amt
        sums['ms_amt'] += ms_amt
        sums['coffee'] += entry.coffee
        sums['lub'] += entry.lub
        sums['collection'] += entry.collection
        sums['bank'] += entry.bank
        sums['ptm'] += entry.ptm
        sums['upi'] += entry.upi
        sums['tsale'] += entry.tsale
        sums['fleet'] += entry.fleet
        sums['rbabu'] += entry.rbabu
        sums['ranjit'] += entry.ranjit
        sums['others'] += entry.others
        sums['pump_expenses'] += petty
        sums['total_expenses'] += entry.total_expenses or 0

    opening_cash = day_rows[first_d].get('opening_cash') or 0
    last_day_num = int(last_d.split('-')[2])
    last_entry = entry_from_day_data(last_day_num, day_rows[last_d], rules)
    closing_balance = sheet.computed_balance(last_entry, day_rows[last_d].get('opening_cash') or 0)

    total_inflow = (opening_cash + sums['hsd_amt'] + sums['ms_amt'] + sums['coffee']
                    + sums['lub'] + sums['collection'])
    total_outflow = (sums['bank'] + sums['ptm'] + sums['upi'] + sums['tsale'] + sums['fleet']
                      + sums['rbabu'] + sums['ranjit'] + sums['others'] + sums['pump_expenses'])

    return {
        'first_date': first_d, 'last_date': last_d, 'num_days': len(dates_sorted),
        'opening_cash': opening_cash, **sums,
        'total_inflow': total_inflow, 'total_outflow': total_outflow,
        'cash_balance': total_inflow - total_outflow,
        'closing_balance_last_day': closing_balance,
    }


def all_expense_lines(day_rows: dict) -> list[dict]:
    """Flat 'Expenses ledger': every individual expense line across the
    range, tagged with its date and bucket."""
    out = []
    for date_iso, d in sorted(day_rows.items()):
        for line in d.get('expense_lines', []):
            out.append({
                'Date': date_iso, 'Label': line.get('label'), 'Amount': line.get('amount'),
                'Bucket': line.get('column') or 'Petty',
            })
    return out


def all_deposit_lines(day_rows: dict) -> list[dict]:
    """Flat 'Deposits ledger': the inflow side of every day — fuel sales,
    lube, coffee, and any collection/tank-sell items."""
    out = []
    for date_iso, d in sorted(day_rows.items()):
        if d.get('hsd_unit') or d.get('hsd_rate'):
            out.append({'Date': date_iso, 'Label': 'HSD sales', 'Amount': (d.get('hsd_unit') or 0) * (d.get('hsd_rate') or 0)})
        if d.get('ms_unit') or d.get('ms_rate'):
            out.append({'Date': date_iso, 'Label': 'MS sales', 'Amount': (d.get('ms_unit') or 0) * (d.get('ms_rate') or 0)})
        if d.get('lub'):
            out.append({'Date': date_iso, 'Label': 'Lube', 'Amount': d['lub']})
        if d.get('coffee'):
            out.append({'Date': date_iso, 'Label': 'Coffee', 'Amount': d['coffee']})
        if d.get('collection'):
            out.append({'Date': date_iso, 'Label': 'Collection', 'Amount': d['collection']})
    return out
