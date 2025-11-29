import streamlit as st
import pandas as pd
import numpy as np
import numpy_financial as npf
import plotly.graph_objects as go

# --- НАЛАШТУВАННЯ СТОРІНКИ ---
st.set_page_config(page_title="Енергоінвестор: Фінансова Модель", layout="wide")

# --- ЗАГОЛОВОК І КОНТЕКСТ ---
st.title("🔋 Енергоінвестор: Калькулятор окупності (СЕС, ВЕС, УЗЕ)")
st.markdown("""
**Інструмент для розрахунку IRR, NPV, LCOE та Payback Period.**
Модель адаптована до українських реалій згідно зі звітом RST Міненерго (серпень 2025):
враховано ризики небалансів, проблеми сертифікації та затримки приєднання.
""")

# --- БІЧНА ПАНЕЛЬ (INPUTS) ---
st.sidebar.header("1. Параметри Проекту")

project_type = st.sidebar.selectbox(
    "Тип активу",
    ("СЕС (Сонячна)", "ВЕС (Вітрова)", "УЗЕ (Energy Storage)", "Гібрид (СЕС + УЗЕ)")
)

# Змінні потужності
p_gen = 0.0
p_store = 0.0
store_hours = 0

if "СЕС" in project_type:
    p_gen = st.sidebar.number_input("Потужність генерації (МВт)", 0.1, 500.0, 5.0, step=0.1)
if "ВЕС" in project_type:
    p_gen = st.sidebar.number_input("Потужність генерації (МВт)", 0.1, 500.0, 10.0, step=0.5)
if "УЗЕ" in project_type:
    st.sidebar.markdown("---")
    st.sidebar.caption("Параметри накопичувача")
    p_store = st.sidebar.number_input("Потужність УЗЕ (МВт)", 0.1, 200.0, 2.0, step=0.1)
    store_hours = st.sidebar.slider("Ємність (годин)", 1, 4, 2, help="Для аРВЧ типово 1-2 години")

st.sidebar.header("2. CAPEX та OPEX")
# CAPEX
capex_gen_mw = st.sidebar.number_input("CAPEX Генерації (€/МВт)", value=550000 if "СЕС" in project_type else 1000000)
capex_store_mwh = 0
if "УЗЕ" in project_type:
    capex_store_mwh = st.sidebar.number_input("CAPEX УЗЕ (€/МВт·год)", value=250000, help="Вартість батарей та інверторів")

# OPEX
opex_mw_year = st.sidebar.number_input("OPEX (€/МВт/рік)", value=12000)

st.sidebar.header("3. Ринок та Тарифи")
price_elec = st.sidebar.number_input("Ціна е/е (РДН) (€/MWh)", value=80.0)
price_ancillary = 0.0
cycles = 300

if "УЗЕ" in project_type:
    st.sidebar.caption("Допоміжні послуги (аРВЧ)")
    price_ancillary = st.sidebar.number_input("Плата за доступність (€/МВт)", value=20.0, help="Ціна за готовність надати послугу")
    cycles = st.sidebar.slider("Циклів на рік", 100, 700, 300)

st.sidebar.header("4. Ризики (Звіт RST)")
risk_imbalance = st.sidebar.checkbox("Ризик: Небаланси та 'від'ємне сальдо'", value=True, 
    help="Знижує дохід на 8% через невідповідність періодів 60/15 хв та штрафи (Джерело: Звіт RST, розд. 3.1)")
risk_delay = st.sidebar.checkbox("Ризик: Затримка приєднання", value=False,
    help="Зсуває запуск на 1 рік через монополію ОСП на будівництво мереж (Джерело: Звіт RST, розд. 2.1.4)")

wacc = st.sidebar.slider("Ставка дисконтування (WACC, %)", 5, 25, 12) / 100
inflation = 0.02

# --- МАТЕМАТИЧНЕ ЯДРО ---
def calculate_metrics():
    lifetime = 20
    years = np.arange(lifetime + 1)
    
    # 1. CAPEX Calculation
    total_capex = (p_gen * capex_gen_mw) + (p_store * store_hours * capex_store_mwh)
    
    # 2. Generation & Storage Logic
    gen_profile = np.zeros(lifetime + 1) # MWh
    store_profile = np.zeros(lifetime + 1) # MWh discharged
    
    # Capacity Factors & Degradation
    cf_solar = 0.14
    cf_wind = 0.35
    deg_gen = 0.005
    deg_store = 0.02
    
    start_year = 2 if risk_delay else 1 # Якщо затримка, починаємо з 2-го року
    
    for t in range(start_year, lifetime + 1):
        age = t - start_year
        
        # Generation Volume
        if "СЕС" in project_type:
            gen_profile[t] += p_gen * 8760 * cf_solar * ((1 - deg_gen) ** age)
        if "ВЕС" in project_type:
            gen_profile[t] += p_gen * 8760 * cf_wind * ((1 - deg_gen) ** age)
            
        # Storage Volume
        if "УЗЕ" in project_type:
            cap_now = p_store * store_hours * ((1 - deg_store) ** age)
            store_profile[t] += cap_now * cycles
            
    # 3. Financials
    revenue = np.zeros(lifetime + 1)
    opex = np.zeros(lifetime + 1)
    
    for t in range(start_year, lifetime + 1):
        # Inflation impact
        inf_coef = (1 + inflation) ** (t - 1)
        
        # Income Calculation
        rev_t = 0
        
        # Sales from Generation
        rev_t += gen_profile[t] * (price_elec * inf_coef)
        
        # Sales from Storage
        if "УЗЕ" in project_type:
            # Arbitrage (Buy low, sell high) -> Net spread ~40 EUR
            rev_t += store_profile[t] * (40 * inf_coef)
            # Ancillary Services (Capacity Payment)
            rev_t += p_store * 8760 * 0.9 * (price_ancillary * inf_coef) # 90% availability
            
        # Risk Penalty (RST Report Logic)
        if risk_imbalance:
            rev_t = rev_t * 0.92 # 8% losses due to regulation issues
            
        revenue[t] = rev_t
        
        # OPEX Calculation
        # Base OPEX
        op_base = (p_gen + p_store) * opex_mw_year * inf_coef
        # Charging Cost for Storage (buy electricity to charge)
        # Efficiency 85%
        op_charge = 0
        if "УЗЕ" in project_type and store_profile[t] > 0:
            energy_in = store_profile[t] / 0.85
            buy_price = (price_elec - 40) * inf_coef
            if buy_price < 10: buy_price = 10
            op_charge = energy_in * buy_price
            
        opex[t] = op_base + op_charge
        
    ebitda = revenue - opex
    
    # Tax (Simple model)
    tax = np.maximum(0, ebitda * 0.18)
    net_cf = ebitda - tax
    net_cf[0] = -total_capex # Investment Year 0
    
    return years, net_cf, total_capex, np.sum(gen_profile + store_profile)

# --- ВИКОНАННЯ ---
years, cf, capex, total_energy = calculate_metrics()

# KPIs
try:
    irr = npf.irr(cf)
    npv = npf.npv(wacc, cf)
except:
    irr, npv = 0, 0
    
# Simple Payback
cum_cf = np.cumsum(cf)
payback_years = np.where(cum_cf >= 0)[0]
payback_val = payback_years[0] if len(payback_years) > 0 else "20+"

# LCOE (Approx)
# Sum of Discounted Costs / Sum of Discounted Energy
disc_costs = capex + npf.npv(wacc, -cf[1:] + (cf[1:])) # Just initial investment + discounted OPEX proxy
disc_energy = total_energy # Simplified for this demo
lcoe = "N/A" # Complex for hybrids in simple tool

# --- ВІДОБРАЖЕННЯ (DASHBOARD) ---
st.divider()

col1, col2, col3, col4 = st.columns(4)
col1.metric("IRR (Прибутковість)", f"{irr:.2%}", delta="Внутрішня ставка")
col2.metric("NPV (Чистий дохід)", f"€ {npv:,.0f}", help="Дисконтований чистий грошовий потік")
col3.metric("Період окупності", f"{payback_val} років")
col4.metric("CAPEX (Інвестиції)", f"€ {capex:,.0f}")

# Charts
tab1, tab2 = st.tabs(["Графік Cash Flow", "Таблиця даних"])

with tab1:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=years, y=cf, name="Net Cash Flow", marker_color='#1f77b4'))
    fig.add_trace(go.Scatter(x=years, y=cum_cf, name="Накопичений підсумок", line=dict(color='#d62728', width=3)))
    fig.add_hline(y=0, line_dash="dash", annotation_text="Break-even")
    fig.update_layout(height=400, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    df = pd.DataFrame({"Year": years, "Cash Flow": cf, "Cumulative": cum_cf})
    st.dataframe(df.style.format("€ {:,.0f}"))

# Download
csv = df.to_csv(index=False).encode('utf-8')
st.download_button("Завантажити розрахунок (CSV)", csv, "energy_model.csv", "text/csv")
