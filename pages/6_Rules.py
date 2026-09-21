from __future__ import annotations

import streamlit as st

import categorize
import common

st.set_page_config(page_title='Categorization Rules', layout='wide', page_icon='⚙️')
company_id, company_name = common.pick_company()
st.title('⚙️ Categorization rules')
st.caption(
    'Each named payment column is matched by regex keywords, checked top-to-bottom — first match wins. '
    'A line that matches nothing stays in the petty-cash residual bucket (the sheet computes that '
    'automatically; it is never entered directly). Rules are per-company.'
)

rules_data = categorize.load_rules(company_id)
edited_rules = {}
for col in rules_data.get('columns', categorize.COLUMNS):
    patterns = rules_data.get('rules', {}).get(col, [])
    text = st.text_area(col, value='\n'.join(patterns), height=80, key=f'rule_{col}')
    edited_rules[col] = [ln.strip() for ln in text.split('\n') if ln.strip()]

if st.button('💾 Save rules', type='primary'):
    rules_data['rules'] = edited_rules
    categorize.save_rules(company_id, rules_data)
    st.success('Rules saved — every page re-applies current rules on load, so past days are re-bucketed automatically.')
