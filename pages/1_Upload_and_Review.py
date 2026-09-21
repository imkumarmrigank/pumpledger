from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

import categorize
import common
import db
import ocr
import sheet

st.set_page_config(page_title='Upload & Review', layout='wide', page_icon='📤')
company_id, company_name = common.pick_company()
st.title('📤 Upload & Review')

CATEGORY_OPTIONS = categorize.COLUMNS + ['Petty']


@st.cache_data(show_spinner=False)
def _extract_cached(content: bytes, name: str):
    return ocr.extract(content, source_label=name)


def draft_from_extraction(v, rules):
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
        'warnings': list(v.warnings),
    }


def fmt(v):
    return '—' if v is None else f'{v:,.2f}'


def diff_flag(a, b, tol=2.0):
    if a is None or b is None:
        return None
    return abs(a - b) > tol


# ---------------------------------------------------------------------------
# 1. Upload — duplicates are detected by content hash and rejected up front
# ---------------------------------------------------------------------------

st.subheader('1. Upload voucher photos')
uploaded = st.file_uploader(
    'Photo(s) of the daily voucher', type=['jpg', 'jpeg', 'png'], accept_multiple_files=True
)
if uploaded:
    added, dupes = 0, []
    for uf in uploaded:
        content = uf.getvalue()
        file_hash = db.hash_bytes(content)
        existing = db.find_image_by_hash(company_id, file_hash)
        if existing:
            when = (f"{existing['year']:04d}-{existing['month']:02d}-{existing['day']:02d}"
                    if existing.get('year') else 'an unreviewed upload')
            dupes.append((uf.name, when, existing['status']))
            continue
        db.save_image(company_id, file_hash, uf.name, content, uf.type, None, None, None, status='pending')
        added += 1
    if added:
        st.success(f'Added {added} new image(s) to the review queue below.')
    for name, when, status in dupes:
        st.warning(f'⚠️ "{name}" is already in the system (uploaded before, {status} — for {when}). Not added again.')

st.divider()

# ---------------------------------------------------------------------------
# 2. Review queue
# ---------------------------------------------------------------------------

st.subheader('2. Review queue')
images = db.list_images(company_id)
pending = [im for im in images if im['status'] == 'pending']
reviewed = [im for im in images if im['status'] == 'reviewed']

c1, c2 = st.columns(2)
c1.metric('Pending review', len(pending))
c2.metric('Reviewed & saved', len(reviewed))

if not images:
    st.info('Upload a photo above to get started.')
    st.stop()

queue = pending + reviewed
labels = [
    f"{'🟡 pending' if im['status']=='pending' else '✅ reviewed'} · {im['filename']} "
    f"({im['year']}-{im['month']:02d}-{im['day']:02d})" if im.get('year')
    else f"{'🟡 pending' if im['status']=='pending' else '✅ reviewed'} · {im['filename']} (no date yet)"
    for im in queue
]
idx = st.selectbox('Image', range(len(queue)), format_func=lambda i: labels[i])
image_row = queue[idx]

content, content_type = db.get_image_content(image_row['id'])

img_col, form_col = st.columns([1, 2])
with img_col:
    st.image(content, caption=image_row['filename'], use_container_width=True)

rules = categorize.load_rules(company_id)
v = _extract_cached(content, image_row['filename'])
draft = draft_from_extraction(v, rules)

# if this image was already reconciled into a saved day, prefill from that
# saved data instead of a fresh OCR pass, so edits aren't lost on revisit
existing_day = None
if image_row.get('year'):
    existing_day = db.load_days(company_id, image_row['year'], image_row['month']).get(
        f"{image_row['year']:04d}-{image_row['month']:02d}-{image_row['day']:02d}"
    )
working = existing_day or draft

with form_col:
    if working.get('warnings'):
        for w in working['warnings']:
            st.caption(f'⚠️ {w}')

    guess_date = None
    if working.get('date_str'):
        try:
            guess_date = datetime.strptime(working['date_str'], '%d-%m-%Y').date()
        except ValueError:
            pass
    if guess_date is None and image_row.get('year'):
        guess_date = date(image_row['year'], image_row['month'], image_row['day'])
    if guess_date is None:
        guess_date = date.today()

    the_date = st.date_input('Voucher date', value=guess_date, key=f'date_{image_row["id"]}')

    st.markdown('**Details / Inflow**')
    c1, c2, c3 = st.columns(3)
    opening_cash = c1.number_input('Opening cash', value=float(working.get('opening_cash') or 0.0), key=f'oc_{image_row["id"]}')
    stated_inflow = c2.number_input('Slip Total inflow', value=float(working.get('stated_total_inflow') or 0.0), key=f'sti_{image_row["id"]}')
    cash_in_hand = c3.number_input('Slip Cash in hand', value=float(working.get('cash_in_hand') or 0.0), key=f'cih_{image_row["id"]}')

    c1, c2, c3, c4 = st.columns(4)
    hsd_unit = c1.number_input('HSD litres', value=float(working.get('hsd_unit') or 0.0), key=f'hu_{image_row["id"]}')
    hsd_rate = c2.number_input('HSD rate', value=float(working.get('hsd_rate') or 0.0), key=f'hr_{image_row["id"]}')
    ms_unit = c3.number_input('MS litres', value=float(working.get('ms_unit') or 0.0), key=f'mu_{image_row["id"]}')
    ms_rate = c4.number_input('MS rate', value=float(working.get('ms_rate') or 0.0), key=f'mr_{image_row["id"]}')
    st.caption(f'→ HSD amount {fmt(hsd_unit*hsd_rate)}  ·  MS amount {fmt(ms_unit*ms_rate)}')

    c1, c2, c3 = st.columns(3)
    lub = c1.number_input('Lube', value=float(working.get('lub') or 0.0), key=f'lub_{image_row["id"]}')
    coffee = c2.number_input('Coffee', value=float(working.get('coffee') or 0.0), key=f'cof_{image_row["id"]}')
    collection = c3.number_input('Collection (tank sell / CSP / other)', value=float(working.get('collection') or 0.0), key=f'col_{image_row["id"]}')

    st.markdown('**Expenses**')
    stated_expenses = st.number_input('Slip Total expenses (T-Exp)', value=float(working.get('stated_total_expenses') or 0.0), key=f'ste_{image_row["id"]}')

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
        num_rows='dynamic', use_container_width=True, key=f'exp_editor_{image_row["id"]}',
    )
    edited_lines = edited.replace({'column': {'Petty': None}}).to_dict('records')

st.divider()
st.subheader('Validation')

draft_d = {
    'opening_cash': opening_cash, 'hsd_unit': hsd_unit, 'hsd_rate': hsd_rate,
    'ms_unit': ms_unit, 'ms_rate': ms_rate, 'lub': lub, 'coffee': coffee, 'collection': collection,
    'stated_total_expenses': stated_expenses, 'expense_lines': edited_lines,
}
entry = common.entry_from_day_data(the_date.day, draft_d, rules)
computed_inflow = sheet.computed_inflow_total(entry, opening_cash)
computed_bal = sheet.computed_balance(entry, opening_cash)
sum_expense_lines = sum((l.get('amount') or 0) for l in edited_lines)

vcols = st.columns(3)
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

# check same-date collision with a *different* already-saved image
same_date_key = f"{the_date.year:04d}-{the_date.month:02d}-{the_date.day:02d}"
other_images_same_date = [
    im for im in images
    if im['id'] != image_row['id'] and im.get('year') == the_date.year
    and im.get('month') == the_date.month and im.get('day') == the_date.day
]
if other_images_same_date:
    st.warning(
        f"Another image ({', '.join(im['filename'] for im in other_images_same_date)}) is already "
        f"assigned to {same_date_key}. Saving this one will overwrite that day's reconciled data."
    )

if st.button('💾 Save this day', type='primary'):
    day_data = {
        'day': the_date.day, 'date_str': the_date.strftime('%d-%m-%Y'),
        'opening_cash': opening_cash, 'hsd_unit': hsd_unit, 'hsd_rate': hsd_rate,
        'ms_unit': ms_unit, 'ms_rate': ms_rate, 'lub': lub, 'coffee': coffee, 'collection': collection,
        'stated_total_inflow': stated_inflow, 'stated_total_expenses': stated_expenses,
        'cash_in_hand': cash_in_hand, 'expense_lines': edited_lines,
        'source_images': [image_row['filename']],
    }
    db.save_day(company_id, the_date.year, the_date.month, the_date.day, day_data)
    db.set_image_status(image_row['id'], 'reviewed', the_date.year, the_date.month, the_date.day)
    st.success(f'Saved {same_date_key}.')
    st.rerun()
