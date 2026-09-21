from __future__ import annotations

import glob
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import categorize
import db
import ocr
import sheet
import store

APP_DIR = Path(__file__).parent
DEFAULT_WORKBOOK = APP_DIR.parent / 'Bajrang Peproleum-Sept-26.xlsx'  # only present in local dev
IMAGES_DIR = APP_DIR.parent / 'images'                                # only present in local dev
UPLOADS_DIR = APP_DIR / 'data' / 'uploads'
EXPORT_DIR = APP_DIR / 'data' / 'exports'
WORKBOOK_DIR = APP_DIR / 'data' / 'workbook'

st.set_page_config(page_title='Bajrang Petroleum — Voucher Reconciliation', layout='wide')

if db.is_configured():
    db.init_db()

CATEGORY_OPTIONS = categorize.COLUMNS + ['Petty']


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _extract(path: str, mtime: float):
    return ocr.extract(path)


def extract_cached(path: str):
    return _extract(path, os.path.getmtime(path))


def day_from_date_str(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%d-%m-%Y').day
    except ValueError:
        return None


def draft_from_extraction(v: 'ocr.ExtractedVoucher', rules: dict) -> dict:
    lines = categorize.categorize_lines(v.expense_lines, rules)
    return {
        'date_str': v.date_str,
        'opening_cash': v.opening_cash,
        'hsd_unit': v.hsd_unit, 'hsd_rate': v.hsd_rate,
        'ms_unit': v.ms_unit, 'ms_rate': v.ms_rate,
        'lub': sum(r.amount or 0 for r in v.extra_inflow if 'lub' in r.label.lower()),
        'coffee': sum(r.amount or 0 for r in v.extra_inflow if 'coff' in r.label.lower()),
        'collection': sum(
            r.amount or 0 for r in v.extra_inflow
            if 'lub' not in r.label.lower() and 'coff' not in r.label.lower()
        ),
        'stated_total_inflow': v.stated_total_inflow,
        'stated_total_expenses': v.stated_total_expenses,
        'cash_in_hand': v.cash_in_hand,
        'expense_lines': lines,
        'extra_inflow_raw': [
            {'label': r.label, 'unit': r.unit, 'rate': r.rate, 'amount': r.amount} for r in v.extra_inflow
        ],
        'warnings': list(v.warnings),
        'source_images': [v.source_path],
    }


def entry_from_day_data(day: int, d: dict) -> sheet.DayEntry:
    totals = categorize.column_totals(d.get('expense_lines', []))
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


def fmt(v):
    if v is None:
        return '—'
    return f'{v:,.2f}'


def diff_flag(a, b, tol=2.0):
    if a is None or b is None:
        return None
    return abs(a - b) > tol


# ---------------------------------------------------------------------------
# session bootstrap
# ---------------------------------------------------------------------------

if 'days' not in st.session_state:
    st.session_state.days = store.load()  # dict[str(day)] -> day dict

st.title('⛽ Bajrang Petroleum — Voucher Reconciliation')
st.caption(
    'Upload a daily voucher photo, review what was read off it against the original slip, '
    'then export a reconciled copy of your monthly Excel ledger.'
)

tab_review, tab_all, tab_rules, tab_export = st.tabs(
    ['📋 Review by day', '📊 All days / totals', '⚙️ Categorization rules', '⬇️ Export']
)

# ---------------------------------------------------------------------------
# sidebar — bring in images
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header('1. Bring in images')
    use_folder = IMAGES_DIR.exists() and st.checkbox(
        f'Include images/ folder ({IMAGES_DIR.name})', value=True
    )
    uploaded = st.file_uploader(
        'Upload voucher photo(s)', type=['jpg', 'jpeg', 'png'], accept_multiple_files=True
    )
    if uploaded:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        for uf in uploaded:
            dest = UPLOADS_DIR / uf.name
            with open(dest, 'wb') as f:
                f.write(uf.getbuffer())

    all_paths = []
    if use_folder and IMAGES_DIR.exists():
        all_paths += sorted(glob.glob(str(IMAGES_DIR / '*.jp*g'))) + sorted(glob.glob(str(IMAGES_DIR / '*.png')))
    if UPLOADS_DIR.exists():
        all_paths += sorted(glob.glob(str(UPLOADS_DIR / '*.jp*g'))) + sorted(glob.glob(str(UPLOADS_DIR / '*.png')))
    all_paths = sorted(set(all_paths))

    st.caption(f'{len(all_paths)} image(s) available.')
    run_ocr = st.button('🔍 Scan images', type='primary', use_container_width=True)

    st.divider()
    st.header('2. Workbook')
    workbook_file = st.file_uploader('Upload your monthly ledger .xlsx', type=['xlsx'])
    if workbook_file is not None:
        WORKBOOK_DIR.mkdir(parents=True, exist_ok=True)
        workbook_path = str(WORKBOOK_DIR / workbook_file.name)
        with open(workbook_path, 'wb') as f:
            f.write(workbook_file.getbuffer())
    elif DEFAULT_WORKBOOK.exists():
        workbook_path = str(DEFAULT_WORKBOOK)
        st.caption(f'Using local file: {DEFAULT_WORKBOOK.name}')
    else:
        workbook_path = ''
        st.warning('Upload your workbook to compare against and export from.')

# ---------------------------------------------------------------------------
# scan images -> group into per-day drafts (in session, not yet saved)
# ---------------------------------------------------------------------------

if 'drafts_by_image' not in st.session_state:
    st.session_state.drafts_by_image = {}

if run_ocr:
    rules = categorize.load_rules()
    with st.spinner(f'Reading {len(all_paths)} image(s)…'):
        for p in all_paths:
            v = extract_cached(p)
            st.session_state.drafts_by_image[p] = draft_from_extraction(v, rules)
    st.success(f'Scanned {len(all_paths)} image(s).')

# group scanned drafts by day number
drafts_by_day: dict[int, list[dict]] = {}
unresolved_dates: list[dict] = []
for p, d in st.session_state.drafts_by_image.items():
    day = day_from_date_str(d.get('date_str'))
    if day is None:
        unresolved_dates.append(d)
    else:
        drafts_by_day.setdefault(day, []).append(d)

# ---------------------------------------------------------------------------
# TAB: review by day
# ---------------------------------------------------------------------------

with tab_review:
    saved_days = {int(k) for k in st.session_state.days.keys()}
    all_days = sorted(set(drafts_by_day.keys()) | saved_days)

    if not all_days:
        st.info('Use the sidebar to scan images, or run **Scan images** to pull in the images/ folder.')
    else:
        col_pick, col_status = st.columns([1, 3])
        with col_pick:
            status_icons = {
                d: ('✅' if d in saved_days else '🆕') for d in all_days
            }
            day = st.selectbox(
                'Day', all_days, format_func=lambda d: f'{status_icons[d]} Day {d} (Sep {d:02d})'
            )

        day_key = str(day)
        existing_saved = st.session_state.days.get(day_key)
        candidate_drafts = drafts_by_day.get(day, [])

        if len(candidate_drafts) > 1:
            st.warning(
                f'{len(candidate_drafts)} images matched Day {day} (probably a duplicate upload). '
                'Showing the first; the others are listed below the image.'
            )

        working = existing_saved or (candidate_drafts[0] if candidate_drafts else None)

        if working is None:
            st.info('No image scanned for this day yet — it only has existing data in the original workbook.')
        else:
            img_col, form_col = st.columns([1, 2])

            with img_col:
                srcs = working.get('source_images', [])
                for sp in srcs:
                    if Path(sp).exists():
                        st.image(sp, caption=Path(sp).name, use_container_width=True)
                if len(candidate_drafts) > 1:
                    st.caption('Other images matched to this day:')
                    for extra in candidate_drafts[1:]:
                        for sp in extra.get('source_images', []):
                            st.caption(f'· {Path(sp).name}')
                if working.get('warnings'):
                    for w in working['warnings']:
                        st.caption(f'⚠️ {w}')

            with form_col:
                st.subheader('Details / Inflow')
                c1, c2, c3 = st.columns(3)
                opening_cash = c1.number_input(
                    'Opening cash', value=float(working.get('opening_cash') or 0.0), step=1.0, key=f'oc_{day}'
                )
                stated_inflow = c2.number_input(
                    'Slip states — Total inflow', value=float(working.get('stated_total_inflow') or 0.0),
                    step=1.0, key=f'sti_{day}'
                )
                cash_in_hand = c3.number_input(
                    'Slip states — Cash in hand', value=float(working.get('cash_in_hand') or 0.0),
                    step=1.0, key=f'cih_{day}'
                )

                c1, c2, c3, c4 = st.columns(4)
                hsd_unit = c1.number_input('HSD litres', value=float(working.get('hsd_unit') or 0.0), key=f'hu_{day}')
                hsd_rate = c2.number_input('HSD rate', value=float(working.get('hsd_rate') or 0.0), key=f'hr_{day}')
                ms_unit = c3.number_input('MS litres', value=float(working.get('ms_unit') or 0.0), key=f'mu_{day}')
                ms_rate = c4.number_input('MS rate', value=float(working.get('ms_rate') or 0.0), key=f'mr_{day}')
                st.caption(
                    f'→ HSD amount {fmt(hsd_unit * hsd_rate)}  ·  MS amount {fmt(ms_unit * ms_rate)}'
                )

                c1, c2, c3 = st.columns(3)
                lub = c1.number_input('Lube', value=float(working.get('lub') or 0.0), key=f'lub_{day}')
                coffee = c2.number_input('Coffee', value=float(working.get('coffee') or 0.0), key=f'cof_{day}')
                collection = c3.number_input(
                    'Collection (tank sell / CSP / other)', value=float(working.get('collection') or 0.0),
                    key=f'col_{day}'
                )

                st.subheader('Expenses')
                stated_expenses = st.number_input(
                    'Slip states — Total expenses (T-Exp)', value=float(working.get('stated_total_expenses') or 0.0),
                    step=1.0, key=f'ste_{day}'
                )

                exp_df = pd.DataFrame(working.get('expense_lines', []))
                if exp_df.empty:
                    exp_df = pd.DataFrame(columns=['label', 'amount', 'column'])
                exp_df['column'] = exp_df['column'].fillna('Petty')
                edited = st.data_editor(
                    exp_df,
                    column_config={
                        'label': st.column_config.TextColumn('Expense line'),
                        'amount': st.column_config.NumberColumn('Amount', format='%.2f'),
                        'column': st.column_config.SelectboxColumn('Bucket', options=CATEGORY_OPTIONS),
                    },
                    num_rows='dynamic',
                    use_container_width=True,
                    key=f'exp_editor_{day}',
                )
                edited_lines = edited.replace({'column': {'Petty': None}}).to_dict('records')

            # -------- validation --------
            st.divider()
            st.subheader('Validation')

            draft_d = {
                'date_str': working.get('date_str'), 'opening_cash': opening_cash,
                'hsd_unit': hsd_unit, 'hsd_rate': hsd_rate, 'ms_unit': ms_unit, 'ms_rate': ms_rate,
                'lub': lub, 'coffee': coffee, 'collection': collection,
                'stated_total_inflow': stated_inflow, 'stated_total_expenses': stated_expenses,
                'cash_in_hand': cash_in_hand, 'expense_lines': edited_lines,
                'source_images': working.get('source_images', []), 'warnings': working.get('warnings', []),
            }
            entry = entry_from_day_data(day, draft_d)

            computed_inflow = sheet.computed_inflow_total(entry, opening_cash)
            computed_named = sheet.computed_named_total(entry)
            computed_bal = sheet.computed_balance(entry, opening_cash)
            sum_expense_lines = sum((l.get('amount') or 0) for l in edited_lines)

            vcols = st.columns(4)
            def show_check(col, label, computed, stated):
                mismatch = diff_flag(computed, stated)
                with col:
                    st.metric(label, fmt(computed), delta=(f'slip: {fmt(stated)}' if stated else None))
                    if mismatch:
                        st.error(f'Differs from slip by {fmt(abs(computed - stated))}')
                    elif stated:
                        st.success('Matches slip')

            show_check(vcols[0], 'Total inflow (computed)', computed_inflow, stated_inflow or None)
            show_check(vcols[1], 'Expense lines total', sum_expense_lines, stated_expenses or None)
            show_check(vcols[2], 'Closing balance (computed)', computed_bal, cash_in_hand or None)

            prev = st.session_state.days.get(str(day - 1))
            with vcols[3]:
                if prev:
                    prev_entry = entry_from_day_data(day - 1, prev)
                    prev_bal = sheet.computed_balance(prev_entry, prev.get('opening_cash') or 0.0)
                    st.metric('Prev. day closing balance', fmt(prev_bal), delta=f'today opens: {fmt(opening_cash)}')
                    if diff_flag(prev_bal, opening_cash):
                        st.error('Opening cash does not carry over from previous day')
                    else:
                        st.success('Carries over correctly')
                else:
                    st.caption('No confirmed previous day to check carry-forward against yet.')

            # -------- compare against existing workbook --------
            snap = None
            if Path(workbook_path).exists():
                try:
                    snap_all = sheet.load_existing_snapshot(workbook_path)
                    snap = snap_all.get(day)
                except Exception as e:  # noqa: BLE001
                    st.caption(f'(could not read existing workbook: {e})')

            if snap:
                st.subheader('vs. existing workbook')
                rows = [
                    ('HSD litres', entry.hsd_unit, snap.get('hsd_unit')),
                    ('HSD rate', entry.hsd_rate, snap.get('hsd_rate')),
                    ('MS litres', entry.ms_unit, snap.get('ms_unit')),
                    ('MS rate', entry.ms_rate, snap.get('ms_rate')),
                    ('Lub', entry.lub, snap.get('lub')),
                    ('Coffee', entry.coffee, snap.get('coffee')),
                    ('Collection', entry.collection, snap.get('collection')),
                    ('Total expenses (M)', entry.total_expenses, snap.get('total_exp')),
                    ('Bank (N)', entry.bank, snap.get('bank')),
                    ('PTM (O)', entry.ptm, snap.get('ptm')),
                    ('UPI (P)', entry.upi, snap.get('upi')),
                    ('T-Sale (Q)', entry.tsale, snap.get('tsale')),
                    ('Fleet (R)', entry.fleet, snap.get('fleet')),
                    ('R-Babu (S)', entry.rbabu, snap.get('rbabu')),
                    ('Ranjit (T)', entry.ranjit, snap.get('ranjit')),
                    ('Others (U)', entry.others, snap.get('others')),
                ]
                cmp_df = pd.DataFrame(rows, columns=['Field', 'New (from image)', 'Existing in workbook'])
                cmp_df['Differs'] = cmp_df.apply(
                    lambda r: bool(diff_flag(r['New (from image)'], r['Existing in workbook'])), axis=1
                )
                st.dataframe(
                    cmp_df.style.apply(
                        lambda r: ['background-color: #ffe4e4' if r['Differs'] else '' for _ in r], axis=1
                    ),
                    use_container_width=True, hide_index=True,
                )

            if st.button('💾 Save this day', type='primary', key=f'save_{day}'):
                draft_d['day'] = day
                st.session_state.days[day_key] = draft_d
                store.save_day(day_key, draft_d)
                st.success(f'Day {day} saved.')
                st.rerun()

# ---------------------------------------------------------------------------
# TAB: all days / totals
# ---------------------------------------------------------------------------

with tab_all:
    st.subheader('All reviewed days')
    days_data = st.session_state.days
    if not days_data:
        st.info('No days saved yet — review and save at least one day first.')
    else:
        rows = []
        for k in sorted(days_data.keys(), key=int):
            d = days_data[k]
            day = int(k)
            entry = entry_from_day_data(day, d)
            computed_inflow = sheet.computed_inflow_total(entry, d.get('opening_cash') or 0.0)
            named = sheet.computed_named_total(entry)
            rows.append({
                'Day': day, 'Date': d.get('date_str'),
                'Opening': d.get('opening_cash'),
                'HSD amt': (entry.hsd_unit or 0) * (entry.hsd_rate or 0),
                'MS amt': (entry.ms_unit or 0) * (entry.ms_rate or 0),
                'Total inflow': computed_inflow,
                'Slip inflow': d.get('stated_total_inflow'),
                'Total expenses': entry.total_expenses,
                'Named (N:U)': named,
                'Images': len(d.get('source_images', [])),
            })
        df = pd.DataFrame(rows).sort_values('Day')
        st.dataframe(df, use_container_width=True, hide_index=True)

        totals = df[['Opening', 'HSD amt', 'MS amt', 'Total inflow', 'Total expenses', 'Named (N:U)']].sum()
        st.subheader('Totals (reviewed days only)')
        mcols = st.columns(len(totals))
        for c, (name, val) in zip(mcols, totals.items()):
            c.metric(name, fmt(val))

        present = set(int(k) for k in days_data.keys())
        missing = sorted(set(range(1, 31)) - present)
        if missing:
            st.warning(f'No reviewed data yet for day(s): {", ".join(map(str, missing))}')

# ---------------------------------------------------------------------------
# TAB: rules
# ---------------------------------------------------------------------------

with tab_rules:
    st.subheader('Categorization rules')
    st.caption(
        'Each named payment column is matched by regex keywords, checked top-to-bottom — first match wins. '
        'A line that matches nothing stays in the petty-cash residual bucket (the sheet computes that '
        'automatically; it is never entered directly).'
    )
    rules_data = categorize.load_rules()
    edited_rules = {}
    for col in rules_data.get('columns', categorize.COLUMNS):
        patterns = rules_data.get('rules', {}).get(col, [])
        text = st.text_area(col, value='\n'.join(patterns), height=80, key=f'rule_{col}')
        edited_rules[col] = [ln.strip() for ln in text.split('\n') if ln.strip()]

    if st.button('💾 Save rules'):
        rules_data['rules'] = edited_rules
        categorize.save_rules(rules_data)
        st.success('Rules saved. Re-open a day (or re-scan) to apply them.')

# ---------------------------------------------------------------------------
# TAB: export
# ---------------------------------------------------------------------------

with tab_export:
    st.subheader('Export reconciled workbook')
    st.caption(
        'Builds a copy of your original workbook with every reviewed day\'s cells overwritten — all existing '
        'formulas (HSD/MS amounts, totals, running balance) are left exactly as they are, so the sheet '
        'recalculates itself the moment you open it in Excel or Google Sheets.'
    )
    st.write(f'Reviewed days ready to export: **{len(st.session_state.days)}**')

    if st.button('⬇️ Generate reconciled workbook', type='primary'):
        if not Path(workbook_path).exists():
            st.error(f'Workbook not found at {workbook_path}')
        elif not st.session_state.days:
            st.error('No reviewed days to export yet.')
        else:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            out_name = Path(workbook_path).stem + '-Reconciled.xlsx'
            out_path = EXPORT_DIR / out_name
            entries = {
                int(k): entry_from_day_data(int(k), d) for k, d in st.session_state.days.items()
            }
            sheet.build_reconciled_workbook(workbook_path, str(out_path), entries)
            st.success(f'Built {out_name}')
            with open(out_path, 'rb') as f:
                st.download_button(
                    'Download reconciled .xlsx', data=f.read(), file_name=out_name,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
