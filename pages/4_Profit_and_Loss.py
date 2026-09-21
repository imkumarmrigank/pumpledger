from __future__ import annotations

import pandas as pd
import streamlit as st

import categorize
import common

st.set_page_config(page_title='Profit & Loss', layout='wide', page_icon='📈')
company_id, company_name = common.pick_company()
st.title('📈 Profit & Loss (cash basis)')
st.caption(
    "Built from the same reconciled figures as the other pages. The expense breakdown here is by "
    "*payment channel* (Bank/PTM/UPI/...), not by expense type — the vouchers only give us a finer "
    "type breakdown for the small 'Pump Expenses' (petty cash) bucket."
)

start, end, label = common.date_range_picker('pnl')
day_rows = common.load_range(company_id, start, end)

if not day_rows:
    st.info(f'No reconciled days between {start} and {end} yet.')
    st.stop()

rules = categorize.load_rules(company_id)
s = common.cash_reconciliation_summary(day_rows, rules)

revenue_rows = [
    ('HSD sales', s['hsd_amt']),
    ('MS sales', s['ms_amt']),
    ('Lube', s['lub']),
    ('Coffee', s['coffee']),
    ('Collection (tank sell / CSP / other)', s['collection']),
]
total_revenue = sum(v for _, v in revenue_rows)

expense_rows = [
    ('Bank', s['bank']), ('PTM', s['ptm']), ('UPI', s['upi']), ('Tank Sale', s['tsale']),
    ('Fleet', s['fleet']), ('Ranjit Ji', s['ranjit']), ('Rajeshwar Babu', s['rbabu']),
    ('Others', s['others']), ('Pump Expenses (petty cash)', s['pump_expenses']),
]
total_expenses = sum(v for _, v in expense_rows)
net = total_revenue - total_expenses

c1, c2, c3 = st.columns(3)
c1.metric('Total revenue', f'{total_revenue:,.2f}')
c2.metric('Total expenses', f'{total_expenses:,.2f}')
c3.metric('Net', f'{net:,.2f}', delta=None)

col1, col2 = st.columns(2)
with col1:
    st.subheader('Revenue')
    st.dataframe(pd.DataFrame(revenue_rows, columns=['Line', 'Amount']), hide_index=True, use_container_width=True)
with col2:
    st.subheader('Expenses')
    st.dataframe(pd.DataFrame(expense_rows, columns=['Line', 'Amount']), hide_index=True, use_container_width=True)

st.divider()
st.subheader('Pump Expenses (petty cash) breakdown')
lines = common.all_expense_lines(day_rows)
petty = [l for l in lines if l['Bucket'] == 'Petty']
if petty:
    pdf = pd.DataFrame(petty)
    st.dataframe(
        pdf.groupby('Label', as_index=False)['Amount'].sum().sort_values('Amount', ascending=False),
        hide_index=True, use_container_width=True,
    )
else:
    st.caption('No petty-cash lines in this range.')
