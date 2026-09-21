from __future__ import annotations

from datetime import date

import streamlit as st

import common
import db

st.set_page_config(page_title='Ledger Reconciliation', layout='wide', page_icon='⛽')

if db.is_configured():
    db.init_db()

company_id, company_name = common.pick_company()

st.title(f'⛽ {company_name}')
st.caption('Voucher reconciliation, ledgers and reports.')

days = db.load_days(company_id)
if days:
    dates = sorted(days.keys())
    c1, c2, c3 = st.columns(3)
    c1.metric('Days reconciled', len(days))
    c2.metric('First date', dates[0])
    c3.metric('Last date', dates[-1])
else:
    st.info('No reconciled days yet for this company — start on the **Upload & Review** page.')

st.divider()
st.markdown("""
**Pages** (sidebar):
- **Upload & Review** — upload voucher photos, OCR them, review/correct against the image, and save the day. Re-uploading the same photo is detected and flagged instead of reprocessed.
- **Cash Reconciliation** — the rolled-up Opening Cash / HSD / MS / Bank / PTM / UPI / ... / Cash Balance summary for any date range.
- **Ledgers** — every expense line and every deposit line, separately, filterable by date range.
- **Profit & Loss** — revenue vs. expenses for a month, quarter, year, or custom range.
- **Export** — download a reconciled workbook for one or more months, in the original format.
- **Rules** — the keyword rules this company's expense lines are categorized with.
""")
