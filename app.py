import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import numpy as np
from datetime import timedelta

# --- 頁面設定 ---
st.set_page_config(page_title="EUR/USD 流動性清掃狙擊策略", layout="wide")
st.title("🎯 EUR/USD 流動性清掃狙擊策略 (M15 Sweep + M1 Entry)")

with st.expander("📖 最新嚴格進場規則與 API 說明"):
    st.markdown("""
    ### 嚴格過濾邏輯 (符合真實 SMC Sweep)
    1. **時間顯示**：所有時間已自動轉換為 **台灣時間 (UTC+8)**。
    2. **強制實體收回 (Body Rejection)**：突破前高/前低的那根 15 分鐘 K 線，其 **開盤價(Open)** 與 **收盤價(Close)** 都必須收在水平線以內，確保突破的部分「純粹只有引線 (Wick)」。
    3. **水平線完整性**：前波高低點產生後，中間間隔的 1 小時內，價格不能曾經刺穿過該水平線，保證這是一次新鮮的流動性清掃。
    4. **精準入場 (Sniper Entry)**：切換至 1分鐘圖，當 M1 價格反向突破 M15 的開盤價時立刻進場。

    ### ❓ 關於免費 1 分鐘 API 的選擇
    金融市場中「超過 7 天以上的 1 分鐘歷史數據」通常是付費級別的，以下是常見選項：
    * **Yahoo Finance (本程式預設)**：免註冊最方便，但官方嚴格限制 `1m` 資料最多只能抓 **最近 7 天**。
    * **Alpha Vantage / OANDA API**：可以抓幾個月的 1m 外匯資料，但**必須去官網免費註冊取得 API Key**，且免費用戶有每分鐘呼叫次數限制。
    * **Binance (幣安 API)**：如果願意回測加密貨幣 (如 `EUR/USDT`)，幣安的 API 是完全免費且可抓取數年的 1m 資料。
    * **👑 最佳解 (CSV上傳)**：從 MT4 / MT5 或 TradingView 匯出半年的 1 分鐘 K 線存成 CSV，丟進左側上傳區，這是專業量化回測最無延遲、最推薦的作法。
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

    # 轉換為台灣時間
    if m1_data.index.tz is not None:
        m1_data.index = m1_data.index.tz_convert('Asia/Taipei').tz_localize(None)
    if m15_data.index.tz is not None:
        m15_data.index = m15_data.index.tz_convert('Asia/Taipei').tz_localize(None)

    return m1_data, m15_data

@st.cache_data
def process_csv_data(df):
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df.set_index('Datetime', inplace=True)
    
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC').tz_convert('Asia/Taipei').tz_localize(None)
    else:
        df.index = df.index.tz_convert('Asia/Taipei').tz_localize(None)

    m1_data = df
    m15_data = m1_data.resample('15min').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'})
    m15_data.dropna(inplace=True)
    return m1_data, m15_data

m1_data, m15_data = None, None

if data_source == "Yahoo Finance (限制最近7天)":
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
    
    for i in range(20, len(m15) - 1):
        # 尋找前高低點的視窗：至少相距 4 根 K 線 (確保有1小時以上的獨立性)
        search_window = m15.iloc[i-20 : i-3] 
        if search_window.empty: continue
        
        # 找前高
        prev_high = search_window['High'].max()
        prev_high_time = search_window['High'].idxmax()
        prev_high_idx = m15.index.get_loc(prev_high_time)
        
        # 找前低
        prev_low = search_window['Low'].min()
        prev_low_time = search_window['Low'].idxmin()
        prev_low_idx = m15.index.get_loc(prev_low_time)
        
        current_m15 = m15.iloc[i]
        next_m15 = m15.iloc[i+1]
        
        sweep_type = None
        sl_price = 0
        swept_level = 0
        swept_time = None
        
        avg_range = (m15['High'].iloc[i-14:i] - m15['Low'].iloc[i-14:i]).mean()
        
        # ---------------------------------------------
        # 🟢 判斷做空 Fakeout (清掃前高流動性)
        # ---------------------------------------------
        gap_df_high = m15.iloc[prev_high_idx+1 : i]
        # 確保區隔期間「從未」有人破壞過這條前高水平線
        if not gap_df_high.empty and gap_df_high['High'].max() < prev_high:
            # 🌟 新增判斷：當前 K 刺穿前高，但實體(Open與Close)皆收在水平線以內！
            if current_m15['High'] > prev_high and current_m15['Close'] < prev_high and current_m15['Open'] < prev_high:
                pullback_depth = prev_high - gap_df_high['Low'].min()
                # 確保拉回深度夠深
                if gap_df_high['Low'].min() < m15.iloc[prev_high_idx]['Low'] and pullback_depth > avg_range:
                    sweep_type = 'Short'
                    sl_price = current_m15['High']
                    swept_level = prev_high
                    swept_time = prev_high_time

        # ---------------------------------------------
        # 🔴 判斷做多 Fakeout (清掃前低流動性)
        # ---------------------------------------------
        if not sweep_type: # 只有沒做空時才檢查做多
            gap_df_low = m15.iloc[prev_low_idx+1 : i]
            # 確保區隔期間「從未」有人破壞過這條前低水平線
            if not gap_df_low.empty and gap_df_low['Low'].min() > prev_low:
                # 🌟 新增判斷：當前 K 跌破前低，但實體(Open與Close)皆收在水平線以上！
                if current_m15['Low'] < prev_low and current_m15['Close'] > prev_low and current_m15['Open'] > prev_low:
                    pullback_depth = gap_df_low['High'].max() - prev_low
                    # 確保反彈深度夠深
                    if gap_df_low['High'].max() > m15.iloc[prev_low_idx]['High'] and pullback_depth > avg_range:
                        sweep_type = 'Long'
                        sl_price = current_m15['Low']
                        swept_level = prev_low
                        swept_time = prev_low_time
            
        if sweep_type:
            trigger_time_start = m15.index[i+1]
            trigger_time_end = trigger_time_start + timedelta(minutes=15)
            fakeout_candle_time = m15.index[i] 
            
            # 切換至 M1 進行精準入場計算
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
    with st.spinner('正在分析走勢與嚴格過濾無效訊號...'):
        trades_df = run_strategy(m1_data, m15_data, rr_ratio)
        
    if trades_df.empty:
        st.warning("在此時間段內未觸發符合「水平線未被破壞」且「實體完全收回」的完美交易訊號。")
    else:
        st.subheader("📊 交易紀錄與回測結果 (台灣時間)")
        
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
        
        st.subheader("📈 專業交易圖表復盤 (M15 大級別視角)")
        
        trade_options =[f"[{row['Outcome']}] {row['Type']} at {row['Entry Time']} (P&L: {row['P&L (R)']}R)" for idx, row in display_df.iterrows()]
        selected_trade_str = st.selectbox("選擇交易進行可視化", trade_options)
        
        selected_idx = trade_options.index(selected_trade_str)
        trade = trades_df.iloc[selected_idx]
        
        start_plot = trade['Swept Time'] - timedelta(minutes=120)
        end_plot = trade['Exit Time'] + timedelta(minutes=180) if pd.notna(trade['Exit Time']) else trade['Entry Time'] + timedelta(minutes=240)
        
        plot_m15 = m15_data[(m15_data.index >= start_plot) & (m15_data.index <= end_plot)]
        
        fig = go.Figure(data=[go.Candlestick(x=plot_m15.index,
                        open=plot_m15['Open'], high=plot_m15['High'],
                        low=plot_m15['Low'], close=plot_m15['Close'],
                        name="15M K線", increasing_line_color='lightgray', decreasing_line_color='gray')])
        
        # 畫水平流動性線
        fig.add_shape(type="line", x0=trade['Swept Time'], y0=trade['Swept Level'], x1=end_plot, y1=trade['Swept Level'],
                      line=dict(color="rgba(200, 200, 200, 0.6)", width=1, dash="dot"))
        fig.add_annotation(x=trade['Swept Time'], y=trade['Swept Level'], text="Prev M15 H/L", showarrow=False, yshift=10, font=dict(color="white"))

        # 清掃圓圈標記
        circle_color = "rgba(255, 50, 50, 0.4)" if trade['Type'] == 'Short' else "rgba(50, 255, 50, 0.4)"
        fig.add_trace(go.Scatter(
            x=[trade['Fakeout Time'] + timedelta(minutes=7.5)], 
            y=[trade['Swept Level']],
            mode='markers', marker=dict(size=25, color=circle_color, line=dict(width=0)),
            name='Liquidity Sweep (清掃)'
        ))

        # Risk & Reward 區塊
        exit_time_plot = trade['Exit Time'] if pd.notna(trade['Exit Time']) else end_plot
        
        fig.add_shape(type="rect", x0=trade['Entry Time'], y0=trade['Entry Price'], x1=exit_time_plot, y1=trade['SL'],
                      fillcolor="rgba(255, 0, 0, 0.15)", line_width=0, layer="below")
        fig.add_shape(type="rect", x0=trade['Entry Time'], y0=trade['Entry Price'], x1=exit_time_plot, y1=trade['TP'],
                      fillcolor="rgba(0, 150, 255, 0.15)", line_width=0, layer="below")

        fig.add_trace(go.Scatter(
            x=[trade['Entry Time']], y=[trade['Entry Price']],
            mode='markers+text', marker=dict(size=10, symbol='triangle-right', color='white'),
            text=["Entry (M1)"], textposition="middle right", name='M1 Entry'
        ))
        
        fig.update_layout(
            title=f"復盤: {trade['Type']} 交易於 {trade['Entry Time'].strftime('%m-%d %H:%M')} (台灣時間)",
            yaxis_title='價格', xaxis_title='台灣時間 (UTC+8)',
            template='plotly_dark', xaxis_rangeslider_visible=False, height=650,
            plot_bgcolor='#131722', paper_bgcolor='#131722'
        )
        
        fig.update_xaxes(type='date')
        
        st.plotly_chart(fig, use_container_width=True)
