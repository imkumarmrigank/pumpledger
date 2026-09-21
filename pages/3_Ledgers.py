from __future__ import annotations

import pandas as pd
import streamlit as st

import common

st.set_page_config(page_title='Ledgers', layout='wide', page_icon='📒')
company_id, company_name = common.pick_company()
st.title('📒 Ledgers')

start, end, label = common.date_range_picker('ledgers')
day_rows = common.load_range(company_id, start, end)

if not day_rows:
    st.info(f'No reconciled days between {start} and {end} yet.')
    st.stop()

tab_exp, tab_dep = st.tabs(['💸 Expenses ledger', '💵 Deposits ledger'])

with tab_exp:
    rows = common.all_expense_lines(day_rows)
    df = pd.DataFrame(rows)
    if df.empty:
        st.info('No expense lines in this range.')
    else:
        buckets = ['All'] + sorted(df['Bucket'].unique().tolist())
        bucket = st.selectbox('Filter by bucket', buckets)
        view = df if bucket == 'All' else df[df['Bucket'] == bucket]
        st.dataframe(view.sort_values('Date'), hide_index=True, use_container_width=True)
        st.metric('Total expenses in range', f"{view['Amount'].sum():,.2f}")
        st.caption('By bucket:')
        st.dataframe(
            df.groupby('Bucket', as_index=False)['Amount'].sum().sort_values('Amount', ascending=False),
            hide_index=True, use_container_width=True,
        )

with tab_dep:
    rows = common.all_deposit_lines(day_rows)
    df = pd.DataFrame(rows)
    if df.empty:
        st.info('No deposit/inflow lines in this range.')
    else:
        labels = ['All'] + sorted(df['Label'].unique().tolist())
        lbl = st.selectbox('Filter by type', labels)
        view = df if lbl == 'All' else df[df['Label'] == lbl]
        st.dataframe(view.sort_values('Date'), hide_index=True, use_container_width=True)
        st.metric('Total deposits/inflow in range', f"{view['Amount'].sum():,.2f}")
        st.caption('By type:')
        st.dataframe(
            df.groupby('Label', as_index=False)['Amount'].sum().sort_values('Amount', ascending=False),
            hide_index=True, use_container_width=True,
        )
