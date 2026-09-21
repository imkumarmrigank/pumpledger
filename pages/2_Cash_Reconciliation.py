from __future__ import annotations

import pandas as pd
import streamlit as st

import categorize
import common

st.set_page_config(page_title='Cash Reconciliation', layout='wide', page_icon='💰')
company_id, company_name = common.pick_company()
st.title('💰 Cash Reconciliation')

start, end, label = common.date_range_picker('cashrecon')
day_rows = common.load_range(company_id, start, end)

if not day_rows:
    st.info(f'No reconciled days between {start} and {end} yet.')
    st.stop()

rules = categorize.load_rules(company_id)
s = common.cash_reconciliation_summary(day_rows, rules)

st.caption(f'{s["num_days"]} day(s) reconciled, {s["first_date"]} to {s["last_date"]}')

left_rows = [
    ('Opening Cash', '', '', s['opening_cash']),
    ('HSD', '', '', s['hsd_amt']),
    ('MS', '', '', s['ms_amt']),
    ('Coffee', '', '', s['coffee']),
    ('Lubricant', '', '', s['lub']),
    ('Collection', '', '', s['collection']),
]
right_rows = [
    ('Bank', s['bank']),
    ('PTM', s['ptm']),
    ('UPI', s['upi']),
    ('Tank Sale', s['tsale']),
    ('Fleet', s['fleet']),
    ('Ranjit Ji', s['ranjit']),
    ('Rajeshwar Babu', s['rbabu']),
    ('Others', s['others']),
    ('Pump Expenses', s['pump_expenses']),
]

col1, col2 = st.columns(2)
with col1:
    st.markdown(f"**Cash reconciliation — {label}**")
    df1 = pd.DataFrame(left_rows, columns=['Details', 'Unit', 'Rate', 'Amount'])
    st.dataframe(df1, hide_index=True, use_container_width=True)
    st.markdown(f"**Total: {s['total_inflow']:,.2f}**")
with col2:
    st.markdown('**Particulars**')
    df2 = pd.DataFrame(right_rows, columns=['Particulars', 'Amount'])
    st.dataframe(df2, hide_index=True, use_container_width=True)
    st.markdown(f"**Total: {s['total_outflow']:,.2f}**")

st.divider()
c1, c2 = st.columns(2)
c1.metric('Cash Balance', f"{s['cash_balance']:,.2f}")
c2.metric("Last day's closing balance (cross-check)", f"{s['closing_balance_last_day']:,.2f}")
if abs(s['cash_balance'] - s['closing_balance_last_day']) > 2:
    st.warning('Cash Balance and the last reconciled day\'s closing balance disagree — check for a gap or a duplicate day in this range.')
else:
    st.success('Cash Balance matches the last reconciled day\'s closing balance.')
