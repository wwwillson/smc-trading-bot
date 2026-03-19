import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
import numpy as np
from datetime import timedelta
import streamlit.components.v1 as components

# --- 頁面設定 ---
st.set_page_config(page_title="專業 SMC 流動性清掃狙擊系統", layout="wide", initial_sidebar_state="expanded")
st.title("🎯 SMC 流動性清掃狙擊系統 (實盤監控 + 回測)")

# --- 自動刷新腳本 (5分鐘 = 300秒) ---
def st_autorefresh(interval_seconds):
    components.html(
        f"""
        <script>
            setTimeout(function() {{
                window.parent.location.reload();
            }}, {interval_seconds * 1000});
        </script>
        """,
        height=0
    )
st_autorefresh(300) # 每 5 分鐘自動刷新頁面

# --- 側邊欄設定 ---
st.sidebar.header("⚙️ 實盤與回測參數設定")
symbol_choice = st.sidebar.selectbox("選擇交易對",["EUR/USDT (歐元)", "BTC/USDT (比特幣)", "PAXG/USDT (實體黃金)"])
days_choice = st.sidebar.selectbox("回測歷史天數 (最長半年)",[1, 7, 30, 90, 180], index=1)
rr_ratio = st.sidebar.slider("風險報酬比 (R:R)", min_value=1.0, max_value=5.0, value=3.0, step=0.5)

symbol_map = {
    "EUR/USDT (歐元)": "EURUSDT",
    "BTC/USDT (比特幣)": "BTCUSDT",
    "PAXG/USDT (實體黃金)": "PAXGUSDT"
}
symbol = symbol_map[symbol_choice]

# --- 幣安 API 獲取資料函數 ---
def fetch_binance_klines(symbol, interval, days):
    limit = 1000
    end_time = int(pd.Timestamp.now(tz='UTC').timestamp() * 1000)
    start_time = int((pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=days)).timestamp() * 1000)
    
    all_data =[]
    while start_time < end_time:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit, "startTime": start_time, "endTime": end_time}
        res = requests.get(url, params=params)
        data = res.json()
        if not data or isinstance(data, dict): break
        all_data.extend(data)
        start_time = data[-1][0] + 1 # 找下一根K線
        
    if not all_data: return pd.DataFrame()
        
    df = pd.DataFrame(all_data, columns=['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'Quote_asset_volume', 'Number_of_trades', 'Taker_buy_base', 'Taker_buy_quote', 'Ignore'])
    df['Datetime'] = pd.to_datetime(df['Datetime'], unit='ms')
    df.set_index('Datetime', inplace=True)
    for col in ['Open', 'High', 'Low', 'Close']: df[col] = df[col].astype(float)
        
    # 轉換為台灣時間
    df.index = df.index.tz_localize('UTC').tz_convert('Asia/Taipei').tz_localize(None)
    df = df[~df.index.duplicated(keep='last')]
    return df[['Open', 'High', 'Low', 'Close']]

# 高效資料引擎：長天期歷史做快取(24小時)，實盤近1天資料每次刷新即時抓取並合併
@st.cache_data(ttl=86400)
def load_bulk_history(symbol, days):
    return fetch_binance_klines(symbol, '1m', days), fetch_binance_klines(symbol, '15m', days)

with st.spinner(f"正在從幣安即時同步 {symbol} 資料..."):
    bulk_m1, bulk_m15 = load_bulk_history(symbol, days_choice)
    live_m1 = fetch_binance_klines(symbol, '1m', 1)
    live_m15 = fetch_binance_klines(symbol, '15m', 2)
    
    # 合併大數據與實盤最新數據
    m1_data = pd.concat([bulk_m1, live_m1]).drop_duplicates()
    m1_data = m1_data[~m1_data.index.duplicated(keep='last')].sort_index()
    m15_data = pd.concat([bulk_m15, live_m15]).drop_duplicates()
    m15_data = m15_data[~m15_data.index.duplicated(keep='last')].sort_index()

# --- 核心交易演算法 (包含嚴格過濾) ---
def run_strategy(m1, m15, rr):
    trades =[]
    for i in range(20, len(m15) - 1):
        search_window = m15.iloc[i-20 : i-3] # 至少相距 4 根 K 線 (1小時以上)
        if search_window.empty: continue
        
        prev_high, prev_high_time = search_window['High'].max(), search_window['High'].idxmax()
        prev_low, prev_low_time = search_window['Low'].min(), search_window['Low'].idxmin()
        prev_high_idx, prev_low_idx = m15.index.get_loc(prev_high_time), m15.index.get_loc(prev_low_time)
        
        current_m15 = m15.iloc[i]
        next_m15 = m15.iloc[i+1]
        
        sweep_type, sl_price, swept_level, swept_time = None, 0, 0, None
        avg_range = (m15['High'].iloc[i-14:i] - m15['Low'].iloc[i-14:i]).mean()
        
        # 判斷做空 Fakeout (實體收回 + 水平線未曾破壞 + 明顯拉回)
        gap_df_high = m15.iloc[prev_high_idx+1 : i]
        if not gap_df_high.empty and gap_df_high['High'].max() < prev_high:
            if current_m15['High'] > prev_high and current_m15['Close'] < prev_high and current_m15['Open'] < prev_high:
                pullback_depth = prev_high - gap_df_high['Low'].min()
                if gap_df_high['Low'].min() < m15.iloc[prev_high_idx]['Low'] and pullback_depth > avg_range:
                    sweep_type, sl_price, swept_level, swept_time = 'Short', current_m15['High'], prev_high, prev_high_time

        # 判斷做多 Fakeout (實體收回 + 水平線未曾破壞 + 明顯拉回)
        if not sweep_type:
            gap_df_low = m15.iloc[prev_low_idx+1 : i]
            if not gap_df_low.empty and gap_df_low['Low'].min() > prev_low:
                if current_m15['Low'] < prev_low and current_m15['Close'] > prev_low and current_m15['Open'] > prev_low:
                    pullback_depth = gap_df_low['High'].max() - prev_low
                    if gap_df_low['High'].max() > m15.iloc[prev_low_idx]['High'] and pullback_depth > avg_range:
                        sweep_type, sl_price, swept_level, swept_time = 'Long', current_m15['Low'], prev_low, prev_low_time
            
        if sweep_type:
            trigger_time_start = m15.index[i+1]
            trigger_time_end = trigger_time_start + timedelta(minutes=15)
            fakeout_candle_time = m15.index[i] 
            
            m1_window = m1[(m1.index >= trigger_time_start) & (m1.index < trigger_time_end)]
            if m1_window.empty: continue
            
            m15_open_price = next_m15['Open']
            entry_price, entry_time = None, None
            
            # M1 級別精準入場
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
                    'Type': sweep_type, 'Swept Level': swept_level, 'Swept Time': swept_time,
                    'Fakeout Time': fakeout_candle_time, 'Entry Time': entry_time, 'Entry Price': entry_price,
                    'SL': sl_price, 'TP': tp_price, 'Outcome': outcome, 'Exit Time': exit_time, 'P&L (R)': pnl
                })
    return pd.DataFrame(trades)

trades_df = run_strategy(m1_data, m15_data, rr_ratio)

# --- 🟢 實盤即時監控面板 ---
st.markdown("---")
st.subheader("⚡ 實盤即時進場資訊 (每5分鐘自動更新)")

current_price = m1_data['Close'].iloc[-1]
last_update_time = m1_data.index[-1].strftime('%Y-%m-%d %H:%M:%S')

# 判斷實盤狀態
live_status_msg = "🕒 策略持續監控 M15 級別流動性中... 目前無信號。"
live_color = "normal"

if not trades_df.empty:
    last_trade = trades_df.iloc[-1]
    if last_trade['Outcome'] == 'Running':
        live_status_msg = f"🚨 **實盤持有中！** 方向: **{last_trade['Type']}** | 進場價: **{last_trade['Entry Price']}** | 止損: **{last_trade['SL']}** | 止盈: **{last_trade['TP']}**"
        live_color = "inverse"

# 若未持有，但上一根剛發生假突破
if live_color == "normal":
    i_live = len(m15_data) - 2
    search_window = m15_data.iloc[i_live-20 : i_live-3]
    if not search_window.empty:
        p_high, p_low = search_window['High'].max(), search_window['Low'].min()
        c_m15 = m15_data.iloc[i_live]
        # 簡單檢查上一根是否假突破
        if c_m15['High'] > p_high and c_m15['Close'] < p_high and c_m15['Open'] < p_high:
            live_status_msg = "🔥 **偵測到流動性清掃 (做空預備)！** 正在 M1 尋找精準跌破開盤價入場點..."
        elif c_m15['Low'] < p_low and c_m15['Close'] > p_low and c_m15['Open'] > p_low:
            live_status_msg = "🔥 **偵測到流動性清掃 (做多預備)！** 正在 M1 尋找精準突破開盤價入場點..."

c1, c2, c3 = st.columns([1, 1, 2])
c1.metric(f"{symbol_choice} 當前價格", f"{current_price:.4f}")
c2.metric("最後更新時間 (台灣)", last_update_time)
c3.info(live_status_msg)

# 實盤近況圖表 (只顯示最近 60 根 M15)
st.write("▼ 近期實盤走勢追蹤 (M15)")
plot_live = m15_data.tail(60)
fig_live = go.Figure(data=[go.Candlestick(x=plot_live.index, open=plot_live['Open'], high=plot_live['High'], low=plot_live['Low'], close=plot_live['Close'], name="15M K線", increasing_line_color='lightgray', decreasing_line_color='gray')])
if not trades_df.empty and trades_df.iloc[-1]['Outcome'] == 'Running':
    lt = trades_df.iloc[-1]
    fig_live.add_shape(type="line", x0=lt['Swept Time'], y0=lt['Swept Level'], x1=plot_live.index[-1], y1=lt['Swept Level'], line=dict(color="rgba(200, 200, 200, 0.6)", width=1, dash="dot"))
    fig_live.add_shape(type="rect", x0=lt['Entry Time'], y0=lt['Entry Price'], x1=plot_live.index[-1], y1=lt['SL'], fillcolor="rgba(255, 0, 0, 0.15)", line_width=0, layer="below")
    fig_live.add_shape(type="rect", x0=lt['Entry Time'], y0=lt['Entry Price'], x1=plot_live.index[-1], y1=lt['TP'], fillcolor="rgba(0, 150, 255, 0.15)", line_width=0, layer="below")
    fig_live.add_trace(go.Scatter(x=[lt['Entry Time']], y=[lt['Entry Price']], mode='markers+text', marker=dict(size=12, symbol='triangle-right', color='white'), text=["Live Entry"], textposition="middle right", name='Live Entry'))

fig_live.update_layout(yaxis_title='價格', xaxis_title='台灣時間', template='plotly_dark', xaxis_rangeslider_visible=False, height=450, margin=dict(l=0, r=0, t=30, b=0), plot_bgcolor='#131722', paper_bgcolor='#131722')
fig_live.update_xaxes(type='date')
st.plotly_chart(fig_live, use_container_width=True)

# --- 📊 歷史回測與圖表覆盤 ---
st.markdown("---")
st.subheader("📊 歷史交易紀錄與回測統計 (台灣時間)")

if trades_df.empty:
    st.warning("選定時間段內未觸發完美交易訊號。")
else:
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
    st.dataframe(display_df.sort_index(ascending=False), use_container_width=True)
    
    st.subheader("📈 歷史交易專業圖表覆盤 (SMC)")
    trade_options = [f"[{row['Outcome']}] {row['Type']} at {row['Entry Time']} (P&L: {row['P&L (R)']}R)" for idx, row in display_df.iterrows()]
    selected_trade_str = st.selectbox("選擇歷史交易進行可視化覆盤", trade_options)
    
    trade = trades_df.iloc[trade_options.index(selected_trade_str)]
    
    start_plot = trade['Swept Time'] - timedelta(minutes=120)
    end_plot = trade['Exit Time'] + timedelta(minutes=180) if pd.notna(trade['Exit Time']) else trade['Entry Time'] + timedelta(minutes=240)
    
    plot_m15 = m15_data[(m15_data.index >= start_plot) & (m15_data.index <= end_plot)]
    
    fig = go.Figure(data=[go.Candlestick(x=plot_m15.index, open=plot_m15['Open'], high=plot_m15['High'], low=plot_m15['Low'], close=plot_m15['Close'], name="15M K線", increasing_line_color='lightgray', decreasing_line_color='gray')])
    
    fig.add_shape(type="line", x0=trade['Swept Time'], y0=trade['Swept Level'], x1=end_plot, y1=trade['Swept Level'], line=dict(color="rgba(200, 200, 200, 0.6)", width=1, dash="dot"))
    fig.add_annotation(x=trade['Swept Time'], y=trade['Swept Level'], text="Prev M15 H/L", showarrow=False, yshift=10, font=dict(color="white"))

    circle_color = "rgba(255, 50, 50, 0.4)" if trade['Type'] == 'Short' else "rgba(50, 255, 50, 0.4)"
    fig.add_trace(go.Scatter(x=[trade['Fakeout Time'] + timedelta(minutes=7.5)], y=[trade['Swept Level']], mode='markers', marker=dict(size=25, color=circle_color, line=dict(width=0)), name='Liquidity Sweep (清掃)'))

    exit_time_plot = trade['Exit Time'] if pd.notna(trade['Exit Time']) else end_plot
    fig.add_shape(type="rect", x0=trade['Entry Time'], y0=trade['Entry Price'], x1=exit_time_plot, y1=trade['SL'], fillcolor="rgba(255, 0, 0, 0.15)", line_width=0, layer="below")
    fig.add_shape(type="rect", x0=trade['Entry Time'], y0=trade['Entry Price'], x1=exit_time_plot, y1=trade['TP'], fillcolor="rgba(0, 150, 255, 0.15)", line_width=0, layer="below")

    fig.add_trace(go.Scatter(x=[trade['Entry Time']], y=[trade['Entry Price']], mode='markers+text', marker=dict(size=10, symbol='triangle-right', color='white'), text=["Entry (M1)"], textposition="middle right", name='M1 Entry'))
    
    fig.update_layout(title=f"復盤: {trade['Type']} 交易於 {trade['Entry Time'].strftime('%m-%d %H:%M')}", yaxis_title='價格', xaxis_title='台灣時間', template='plotly_dark', xaxis_rangeslider_visible=False, height=650, plot_bgcolor='#131722', paper_bgcolor='#131722')
    fig.update_xaxes(type='date')
    st.plotly_chart(fig, use_container_width=True)
