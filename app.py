import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import numpy as np
from datetime import timedelta

# --- 頁面設定 ---
st.set_page_config(page_title="EUR/USD 流動性清掃狙擊策略", layout="wide")
st.title("🎯 EUR/USD 流動性清掃狙擊策略 (M15 Sweep + M1 Entry)")

with st.expander("📖 策略邏輯與圖表說明"):
    st.markdown("""
    * **流動性清掃 (Liquidity Sweep)**：尋找過去 20 根 15分鐘K線的前高或前低。
    * **假突破 (Fakeout)**：當前 M15 K線刺穿前高/前低，但收盤卻收在區間內（留下長引線）。
    * **精準入場 (Sniper Entry)**：切換至 1分鐘圖，當 M1 價格反向突破 M15 的開盤價時立刻進場。
    * **圖表標示**：
        * **虛線**：標示出被清掃的「前高/前低」水平位。
        * **半透明圓圈**：標示假突破發生的瞬間。
        * **紅色區塊**：止損風險區 (Risk)。
        * **綠色區塊**：止盈獲利區 (Reward)。
    """)

st.sidebar.header("⚙️ 參數設定")
data_source = st.sidebar.radio("資料來源",["Yahoo Finance (限制最近7天)", "上傳 CSV (可回測半年)"])
rr_ratio = st.sidebar.slider("風險報酬比 (R:R)", min_value=1.0, max_value=5.0, value=3.0, step=0.5)

@st.cache_data
def load_yf_data():
    m1_data = yf.download("EURUSD=X", period="7d", interval="1m")
    m15_data = yf.download("EURUSD=X", period="7d", interval="15m")
    
    m1_data.columns =['_'.join(col).strip() if isinstance(col, tuple) else col for col in m1_data.columns]
    m15_data.columns =['_'.join(col).strip() if isinstance(col, tuple) else col for col in m15_data.columns]
    
    cols_rename = {c: c.split('_')[0] for c in m1_data.columns}
    m1_data.rename(columns=cols_rename, inplace=True)
    m15_data.rename(columns=cols_rename, inplace=True)
    
    m1_data.dropna(inplace=True)
    m15_data.dropna(inplace=True)
    return m1_data, m15_data

@st.cache_data
def process_csv_data(df):
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df.set_index('Datetime', inplace=True)
    m1_data = df
    m15_data = m1_data.resample('15min').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'})
    m15_data.dropna(inplace=True)
    return m1_data, m15_data

m1_data, m15_data = None, None

if data_source == "Yahoo Finance (限制最近7天)":
    m1_data, m15_data = load_yf_data()
else:
    uploaded_file = st.sidebar.file_uploader("上傳 1分鐘 K線 CSV", type="csv")
    if uploaded_file is not None:
        raw_df = pd.read_csv(uploaded_file)
        m1_data, m15_data = process_csv_data(raw_df)
    else:
        st.warning("請上傳 CSV 檔案以開始回測。")
        st.stop()

# --- 核心交易演算法 ---
def run_strategy(m1, m15, rr):
    trades =[]
    
    for i in range(20, len(m15) - 1):
        # 獲取過去 20 根 K 線作為尋找前高/前低的區間
        window_df = m15.iloc[i-20:i]
        
        # 找出前高和前低的數值與發生的時間點
        prev_high = window_df['High'].max()
        prev_high_time = window_df['High'].idxmax()
        
        prev_low = window_df['Low'].min()
        prev_low_time = window_df['Low'].idxmin()
        
        current_m15 = m15.iloc[i]
        next_m15 = m15.iloc[i+1]
        
        sweep_type = None
        sl_price = 0
        swept_level = 0
        swept_time = None
        
        # 判斷作空 Fakeout (向上清掃前高流動性)
        if current_m15['High'] > prev_high and current_m15['Close'] < prev_high:
            sweep_type = 'Short'
            sl_price = current_m15['High']
            swept_level = prev_high
            swept_time = prev_high_time
            
        # 判斷作多 Fakeout (向下清掃前低流動性)
        elif current_m15['Low'] < prev_low and current_m15['Close'] > prev_low:
            sweep_type = 'Long'
            sl_price = current_m15['Low']
            swept_level = prev_low
            swept_time = prev_low_time
            
        if sweep_type:
            trigger_time_start = m15.index[i+1]
            trigger_time_end = trigger_time_start + timedelta(minutes=15)
            fakeout_candle_time = m15.index[i] # 假突破發生的那根 K 棒時間
            
            m1_window = m1[(m1.index >= trigger_time_start) & (m1.index < trigger_time_end)]
            if m1_window.empty: continue
            
            m15_open_price = next_m15['Open']
            entry_price, entry_time = None, None
            
            for j in range(len(m1_window)):
                m1_candle = m1_window.iloc[j]
                if sweep_type == 'Short' and m1_candle['Close'] < m15_open_price:
                    entry_price, entry_time = m1_candle['Close'], m1_window.index[j]
                    break
                elif sweep_type == 'Long' and m1_candle['Close'] > m15_open_price:
                    entry_price, entry_time = m1_candle['Close'], m1_window.index[j]
                    break
            
            if entry_price:
                risk = abs(entry_price - sl_price)
                tp_price = entry_price - (risk * rr) if sweep_type == 'Short' else entry_price + (risk * rr)
                
                future_m1 = m1[m1.index > entry_time]
                outcome, exit_time, pnl = 'Running', None, 0
                
                for k in range(len(future_m1)):
                    f_high, f_low = future_m1['High'].iloc[k], future_m1['Low'].iloc[k]
                    if sweep_type == 'Short':
                        if f_high >= sl_price: outcome, exit_time, pnl = 'SL Hit', future_m1.index[k], -1; break
                        elif f_low <= tp_price: outcome, exit_time, pnl = 'TP Hit', future_m1.index[k], rr; break
                    else:
                        if f_low <= sl_price: outcome, exit_time, pnl = 'SL Hit', future_m1.index[k], -1; break
                        elif f_high >= tp_price: outcome, exit_time, pnl = 'TP Hit', future_m1.index[k], rr; break
                            
                trades.append({
                    'Type': sweep_type,
                    'Swept Level': swept_level,
                    'Swept Time': swept_time,
                    'Fakeout Time': fakeout_candle_time,
                    'Entry Time': entry_time,
                    'Entry Price': entry_price,
                    'SL': sl_price,
                    'TP': tp_price,
                    'Outcome': outcome,
                    'Exit Time': exit_time,
                    'P&L (R)': pnl
                })
                
    return pd.DataFrame(trades)

if m1_data is not None and m15_data is not None:
    with st.spinner('正在分析走勢與計算交易訊號...'):
        trades_df = run_strategy(m1_data, m15_data, rr_ratio)
        
    if trades_df.empty:
        st.warning("在此時間段內未觸發任何符合此嚴格策略的交易訊號。")
    else:
        st.subheader("📊 交易紀錄與回測結果")
        
        total_trades = len(trades_df)
        wins = len(trades_df[trades_df['Outcome'] == 'TP Hit'])
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
        total_pnl = trades_df['P&L (R)'].sum()
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("總交易次數", total_trades)
        col2.metric("勝率", f"{win_rate:.1f}%")
        col3.metric("總盈虧 (單位: R)", f"{total_pnl:.2f} R")
        col4.metric("設定盈虧比", f"1 : {rr_ratio}")
        
        display_df = trades_df.drop(columns=['Swept Time', 'Fakeout Time']).copy()
        display_df['Entry Time'] = display_df['Entry Time'].dt.strftime('%m-%d %H:%M')
        display_df['Exit Time'] = display_df['Exit Time'].dt.strftime('%m-%d %H:%M') if not display_df['Exit Time'].isnull().all() else None
        st.dataframe(display_df, use_container_width=True)
        
        st.subheader("📈 專業交易圖表復盤 (SMC 流動性畫線)")
        
        trade_options =[f"[{row['Outcome']}] {row['Type']} at {row['Entry Time']} (P&L: {row['P&L (R)']}R)" for idx, row in display_df.iterrows()]
        selected_trade_str = st.selectbox("選擇交易進行可視化", trade_options)
        
        selected_idx = trade_options.index(selected_trade_str)
        trade = trades_df.iloc[selected_idx]
        
        # 繪圖區間：包含前高/前低的時間點，一直到出場後 60 分鐘
        start_plot = trade['Swept Time'] - timedelta(minutes=30)
        end_plot = trade['Exit Time'] + timedelta(minutes=60) if pd.notna(trade['Exit Time']) else trade['Entry Time'] + timedelta(minutes=120)
        
        plot_df = m1_data[(m1_data.index >= start_plot) & (m1_data.index <= end_plot)]
        
        fig = go.Figure(data=[go.Candlestick(x=plot_df.index,
                        open=plot_df['Open'], high=plot_df['High'],
                        low=plot_df['Low'], close=plot_df['Close'],
                        name="1M K線", increasing_line_color='lightgray', decreasing_line_color='gray')])
        
        # 1. 繪製前高/前低的水平虛線 (Liquidity Line)
        fig.add_shape(type="line", x0=trade['Swept Time'], y0=trade['Swept Level'], x1=end_plot, y1=trade['Swept Level'],
                      line=dict(color="rgba(200, 200, 200, 0.6)", width=1, dash="dot"))
        fig.add_annotation(x=trade['Swept Time'], y=trade['Swept Level'], text="Prev M15 H/L", showarrow=False, yshift=10, font=dict(color="white"))

        # 2. 繪製清掃標記 (半透明圓圈，符合截圖效果)
        circle_color = "rgba(255, 50, 50, 0.4)" if trade['Type'] == 'Short' else "rgba(50, 255, 50, 0.4)"
        fig.add_trace(go.Scatter(
            x=[trade['Fakeout Time'] + timedelta(minutes=7)], # 圓圈放在假突破K棒中間
            y=[trade['Swept Level']],
            mode='markers', marker=dict(size=25, color=circle_color, line=dict(width=0)),
            name='Liquidity Sweep (清掃)'
        ))

        # 3. 繪製 SMC 風格進出場區間色塊 (Risk & Reward Zones)
        exit_time_plot = trade['Exit Time'] if pd.notna(trade['Exit Time']) else end_plot
        
        # 紅色止損區間 (Risk)
        fig.add_shape(type="rect", x0=trade['Entry Time'], y0=trade['Entry Price'], x1=exit_time_plot, y1=trade['SL'],
                      fillcolor="rgba(255, 0, 0, 0.15)", line_width=0, layer="below")
        
        # 藍綠色止盈區間 (Reward)
        fig.add_shape(type="rect", x0=trade['Entry Time'], y0=trade['Entry Price'], x1=exit_time_plot, y1=trade['TP'],
                      fillcolor="rgba(0, 150, 255, 0.15)", line_width=0, layer="below")

        # 4. 標示精準進場點
        fig.add_trace(go.Scatter(
            x=[trade['Entry Time']], y=[trade['Entry Price']],
            mode='markers+text', marker=dict(size=10, symbol='triangle-right', color='white'),
            text=["Entry"], textposition="middle right", name='Entry'
        ))
        
        fig.update_layout(
            title=f"復盤: {trade['Type']} 交易於 {trade['Entry Time'].strftime('%m-%d %H:%M')}",
            yaxis_title='價格', xaxis_title='時間',
            template='plotly_dark', xaxis_rangeslider_visible=False, height=650,
            plot_bgcolor='#131722', paper_bgcolor='#131722' # 採用 TradingView 暗色系背景
        )
        
        st.plotly_chart(fig, use_container_width=True)
