import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from datetime import timedelta

# --- 頁面設定 ---
st.set_page_config(page_title="EUR/USD 狙擊手交易策略回測", layout="wide")

st.title("🎯 EUR/USD 狙擊手交易策略 (M15 Sweep + M1 Entry)")

# --- 策略說明 ---
with st.expander("📖 查看影片中的交易邏輯 (3 Steps Strategy)"):
    st.markdown("""
    此策略還原自影片中的「流動性清掃 (Liquidity Sweep) 狙擊策略」：
    * **Step 1 (M15)**：標記 15 分鐘圖上的波段高點與低點。
    * **Step 2 (M15)**：等待價格突破高/低點，但**收盤價收回原區間 (Wick Rejection/假突破)**。
    * **Step 3 (M1)**：切換到 1 分鐘圖。觀察假突破後的「下一根 M15 開盤價線」。
        * **做空 (Short)**：M1 價格向上衝後，**收盤價跌破**該 M15 開盤價線即進場。止損設在假突破的高點，止盈設為 1:3 盈虧比。
        * **做多 (Long)**：M1 價格向下探後，**收盤價突破**該 M15 開盤價線即進場。止損設在假突破的低點，止盈設為 1:3 盈虧比。
    """)

# --- 側邊欄設定 ---
st.sidebar.header("⚙️ 參數設定")
data_source = st.sidebar.radio("資料來源",["Yahoo Finance (限制最近7天)", "上傳 CSV (可回測半年)"])

# 風險報酬比設定
rr_ratio = st.sidebar.slider("風險報酬比 (R:R)", min_value=1.0, max_value=5.0, value=3.0, step=0.5)

@st.cache_data
def load_yf_data():
    # 獲取 1 分鐘資料 (限制7天)
    m1_data = yf.download("EURUSD=X", period="7d", interval="1m")
    # 獲取 15 分鐘資料 (限制60天，但配合1m只用7天)
    m15_data = yf.download("EURUSD=X", period="7d", interval="15m")
    
    # 扁平化 MultiIndex columns (yfinance 新版的問題)
    m1_data.columns =['_'.join(col).strip() if isinstance(col, tuple) else col for col in m1_data.columns]
    m15_data.columns =['_'.join(col).strip() if isinstance(col, tuple) else col for col in m15_data.columns]
    
    # 重命名回標準名稱
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
    # 從 1m 重採樣成 15m
    m15_data = m1_data.resample('15min').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'})
    m15_data.dropna(inplace=True)
    return m1_data, m15_data

# 獲取資料
m1_data, m15_data = None, None

if data_source == "Yahoo Finance (限制最近7天)":
    st.info("💡 提示：免費 Yahoo API 僅提供過去 7 天的 1 分鐘數據。如需半年回測，請使用 CSV 上傳。")
    m1_data, m15_data = load_yf_data()
else:
    uploaded_file = st.sidebar.file_uploader("上傳 1分鐘 K線 CSV (需包含 Datetime, Open, High, Low, Close)", type="csv")
    if uploaded_file is not None:
        raw_df = pd.read_csv(uploaded_file)
        m1_data, m15_data = process_csv_data(raw_df)
    else:
        st.warning("請上傳 CSV 檔案以開始回測。")
        st.stop()

# --- 核心交易演算法 ---
def run_strategy(m1, m15, rr):
    trades =[]
    
    # 計算 M15 結構高低點 (使用前 20 根 K 線的最高/最低)
    m15 = m15.copy()
    m15['Rolling_High'] = m15['High'].rolling(window=20).max().shift(1)
    m15['Rolling_Low'] = m15['Low'].rolling(window=20).min().shift(1)
    
    for i in range(20, len(m15) - 1):
        # 取得當前 M15 K線資料
        current_m15 = m15.iloc[i]
        next_m15 = m15.iloc[i+1]
        
        sweep_type = None
        sl_price = 0
        
        # 判斷作空 Fakeout (向上清掃流動性)
        if current_m15['High'] > current_m15['Rolling_High'] and current_m15['Close'] < current_m15['Rolling_High']:
            sweep_type = 'Short'
            sl_price = current_m15['High'] # 影片中的止損放在假突破頂端
            
        # 判斷作多 Fakeout (向下清掃流動性)
        elif current_m15['Low'] < current_m15['Rolling_Low'] and current_m15['Close'] > current_m15['Rolling_Low']:
            sweep_type = 'Long'
            sl_price = current_m15['Low']  # 影片中的止損放在假突破底端
            
        if sweep_type:
            # Step 3: 在 M1 尋找入場點
            trigger_time_start = m15.index[i+1]
            trigger_time_end = trigger_time_start + timedelta(minutes=15)
            
            # 獲取相對應的 M1 區間資料
            m1_window = m1[(m1.index >= trigger_time_start) & (m1.index < trigger_time_end)]
            if m1_window.empty: continue
            
            m15_open_price = next_m15['Open']
            entry_price = None
            entry_time = None
            
            # 尋找 M1 入場條件
            for j in range(len(m1_window)):
                m1_candle = m1_window.iloc[j]
                
                if sweep_type == 'Short' and m1_candle['Close'] < m15_open_price:
                    entry_price = m1_candle['Close']
                    entry_time = m1_window.index[j]
                    break
                elif sweep_type == 'Long' and m1_candle['Close'] > m15_open_price:
                    entry_price = m1_candle['Close']
                    entry_time = m1_window.index[j]
                    break
            
            if entry_price:
                # 計算 TP
                risk = abs(entry_price - sl_price)
                if sweep_type == 'Short':
                    tp_price = entry_price - (risk * rr)
                else:
                    tp_price = entry_price + (risk * rr)
                
                # 模擬入場後的走勢，判斷是打到 SL 還是 TP
                future_m1 = m1[m1.index > entry_time]
                outcome = 'Running'
                exit_time = None
                pnl = 0
                
                for k in range(len(future_m1)):
                    f_high = future_m1['High'].iloc[k]
                    f_low = future_m1['Low'].iloc[k]
                    
                    if sweep_type == 'Short':
                        if f_high >= sl_price:
                            outcome = 'SL Hit'
                            exit_time = future_m1.index[k]
                            pnl = -1  # 虧損 1R
                            break
                        elif f_low <= tp_price:
                            outcome = 'TP Hit'
                            exit_time = future_m1.index[k]
                            pnl = rr  # 獲利 RR
                            break
                    else:
                        if f_low <= sl_price:
                            outcome = 'SL Hit'
                            exit_time = future_m1.index[k]
                            pnl = -1
                            break
                        elif f_high >= tp_price:
                            outcome = 'TP Hit'
                            exit_time = future_m1.index[k]
                            pnl = rr
                            break
                            
                trades.append({
                    'Type': sweep_type,
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
        # --- 介面呈現：結果表格與總計 ---
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
        
        # 格式化表格
        display_df = trades_df.copy()
        display_df['Entry Time'] = display_df['Entry Time'].dt.strftime('%Y-%m-%d %H:%M')
        display_df['Exit Time'] = display_df['Exit Time'].dt.strftime('%Y-%m-%d %H:%M')
        display_df['Entry Price'] = display_df['Entry Price'].round(5)
        display_df['SL'] = display_df['SL'].round(5)
        display_df['TP'] = display_df['TP'].round(5)
        
        st.dataframe(display_df, use_container_width=True)
        
        # --- 圖表可視化：選擇特定交易查看細節 ---
        st.subheader("📈 交易圖表復盤 (M1 精準點位)")
        st.write("請從下方選單選擇一筆交易，圖表將自動生成該次交易的進場、止損、止盈可視化圖。")
        
        trade_options =[f"[{row['Outcome']}] {row['Type']} at {row['Entry Time']} (P&L: {row['P&L (R)']}R)" for idx, row in display_df.iterrows()]
        selected_trade_str = st.selectbox("選擇交易進行可視化", trade_options)
        
        selected_idx = trade_options.index(selected_trade_str)
        trade = trades_df.iloc[selected_idx]
        
        # 繪圖區間：進場前 60 分鐘 到 出場後 60 分鐘
        start_plot = trade['Entry Time'] - timedelta(minutes=60)
        end_plot = trade['Exit Time'] + timedelta(minutes=60) if pd.notna(trade['Exit Time']) else trade['Entry Time'] + timedelta(minutes=120)
        
        plot_df = m1_data[(m1_data.index >= start_plot) & (m1_data.index <= end_plot)]
        
        fig = go.Figure(data=[go.Candlestick(x=plot_df.index,
                        open=plot_df['Open'],
                        high=plot_df['High'],
                        low=plot_df['Low'],
                        close=plot_df['Close'],
                        name="1M K線")])
        
        # 標示進場點
        fig.add_trace(go.Scatter(
            x=[trade['Entry Time']], y=[trade['Entry Price']],
            mode='markers', marker=dict(size=15, symbol='star', color='yellow', line=dict(width=2, color='black')),
            name='Entry (進場)'
        ))
        
        # 畫 SL 和 TP 線
        fig.add_shape(type="line", x0=start_plot, y0=trade['SL'], x1=end_plot, y1=trade['SL'],
                      line=dict(color="Red", width=2, dash="dash"), name="SL")
        fig.add_shape(type="line", x0=start_plot, y0=trade['TP'], x1=end_plot, y1=trade['TP'],
                      line=dict(color="Green", width=2, dash="dash"), name="TP")
        
        # 加入文字註解
        fig.add_annotation(x=trade['Entry Time'], y=trade['SL'], text="SL (止損)", showarrow=False, yshift=10, font=dict(color="red"))
        fig.add_annotation(x=trade['Entry Time'], y=trade['TP'], text="TP (止盈)", showarrow=False, yshift=-10, font=dict(color="green"))
        
        # 圖表佈局
        fig.update_layout(
            title=f"復盤: {trade['Type']} 交易於 {trade['Entry Time']} ({trade['Outcome']})",
            yaxis_title='價格',
            xaxis_title='時間',
            template='plotly_dark',
            xaxis_rangeslider_visible=False,
            height=600
        )
        
        st.plotly_chart(fig, use_container_width=True)

st.markdown("---")
st.markdown("⚠️ **免責聲明**: 此程式僅用於量化交易邏輯演示與回測歷史資料，不構成任何財務投資建議。")
