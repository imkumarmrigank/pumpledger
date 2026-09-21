from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date

import streamlit as st

import categorize
import common
import db
import sheet

st.set_page_config(page_title='Export', layout='wide', page_icon='⬇️')
company_id, company_name = common.pick_company()
st.title('⬇️ Export reconciled workbook')

days = db.load_days(company_id)
if not days:
    st.info('No reconciled days yet for this company.')
    st.stop()

months_present = sorted({(int(k[:4]), int(k[5:7])) for k in days.keys()})
month_labels = {ym: f"{calendar.month_name[ym[1]]} {ym[0]}" for ym in months_present}

st.caption('Pick one or more months to include — each becomes its own sheet in the same format as the original.')
chosen = st.multiselect(
    'Months', months_present, default=months_present, format_func=lambda ym: month_labels[ym]
)

if st.button('Generate workbook', type='primary') and chosen:
    rules = categorize.load_rules(company_id)
    entries_by_month = {}
    for year, month in chosen:
        month_days = db.load_days(company_id, year, month)
        entries = {}
        for date_iso, d in month_days.items():
            day_num = int(date_iso.split('-')[2])
            entries[day_num] = common.entry_from_day_data(day_num, d, rules)
        entries_by_month[(year, month)] = entries

    safe_name = ''.join(c if c.isalnum() or c in ' -_' else '' for c in company_name).strip() or 'Company'
    out_dir = __import__('pathlib').Path(__file__).parent.parent / 'data' / 'exports'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{safe_name}-Reconciled.xlsx"
    sheet.build_export_workbook(str(out_path), company_name, entries_by_month)

    with open(out_path, 'rb') as f:
        st.download_button(
            '📥 Download .xlsx', data=f.read(), file_name=out_path.name,
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    st.success(f'Built {out_path.name} — {len(chosen)} month(s), {sum(len(e) for e in entries_by_month.values())} day(s) total.')
