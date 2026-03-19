import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import pytz
import requests

# 設定網頁佈局
st.set_page_config(page_title="NY Session Trading Strategy", layout="wide")

st.title("📈 紐約盤開盤突破 (NY ORB) 交易機器人")
st.markdown("""
### 解決了 Yahoo 封鎖問題的新版本 🚀
- **加密貨幣 (BTC/ETH)**：使用 Binance 公開 API，**無須 API Key，永久免費穩定**。
- **傳統金融 (Gold/EUR)**：使用 Twelve Data API，需在左側輸入免費 API Key。
---
**交易邏輯 (參考影片策略)**
1. **亞洲盤/倫敦盤**: 建立並清掃流動性。
2. **紐約開盤區間 (NY ORB)**: 標記美東時間 **09:30 - 09:45** 的高低點。
3. **進場條件**: 價格突破該區間，模擬進場，並設定 **1.5 倍盈虧比 (Risk-Reward)** 的止損與止盈。
""")

# 側邊欄設定
st.sidebar.header("⚙️ 交易設定")
data_source = st.sidebar.radio("選擇市場",["🟢 加密貨幣 (Binance 免費數據)", "🟡 外匯與黃金 (需 API Key)"])

if data_source == "🟢 加密貨幣 (Binance 免費數據)":
    asset_dict = {
        "Bitcoin (BTC/USDT)": "BTCUSDT",
        "Ethereum (ETH/USDT)": "ETHUSDT",
        "Solana (SOL/USDT)": "SOLUSDT"
    }
    selected_asset = st.sidebar.selectbox("選擇交易標的", list(asset_dict.keys()))
    ticker = asset_dict[selected_asset]
    api_key = None
else:
    st.sidebar.markdown("👉[點此前往 Twelve Data 免費註冊獲取 Key](https://twelvedata.com/)")
    api_key = st.sidebar.text_input("輸入 Twelve Data API Key", type="password")
    asset_dict = {
        "Gold (XAU/USD)": "XAU/USD",
        "Euro (EUR/USD)": "EUR/USD"
    }
    selected_asset = st.sidebar.selectbox("選擇交易標的", list(asset_dict.keys()))
    ticker = asset_dict[selected_asset]

days_to_fetch = st.sidebar.slider("載入最近天數", min_value=1, max_value=3, value=2)

# --- 數據獲取函數 (快取 5 分鐘) ---
@st.cache_data(ttl=300)
def load_binance_data(symbol, days):
    limit = min(days * 288, 1000) # 5m K線每天288根，API最多支援1000根
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit={limit}"
    res = requests.get(url)
    if res.status_code != 200:
        return None
    data = res.json()
    df = pd.DataFrame(data, columns=['datetime', 'Open', 'High', 'Low', 'Close', 'Volume', '_', '_', '_', '_', '_', '_'])
    df['datetime'] = pd.to_datetime(df['datetime'], unit='ms')
    df.set_index('datetime', inplace=True)
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = df[col].astype(float)
    # 轉換為紐約時間
    df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
    return df

@st.cache_data(ttl=300)
def load_twelvedata_data(symbol, days, key):
    limit = min(days * 288, 1000)
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize={limit}&timezone=America/New_York&apikey={key}"
    res = requests.get(url)
    data = res.json()
    if 'values' not in data:
        st.error(f"API 錯誤: {data.get('message', '請確認 API Key 是否正確')}")
        return None
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'})
    df = df.sort_index(ascending=True)
    df.index = df.index.tz_localize('America/New_York')
    return df

# --- 載入數據 ---
with st.spinner('正在從 API 獲取即時數據...'):
    if data_source == "🟢 加密貨幣 (Binance 免費數據)":
        df = load_binance_data(ticker, days_to_fetch)
    else:
        if not api_key:
            st.warning("⚠️ 必須在左側側邊欄輸入 Twelve Data API Key 才能載入外匯/黃金數據。")
            st.stop()
        df = load_twelvedata_data(ticker, days_to_fetch, api_key)

if df is None or df.empty:
    st.error("無法獲取數據，請稍後再試。")
    st.stop()

# --- 選擇日期與策略邏輯 ---
available_dates = sorted(list(set(df.index.date)), reverse=True)
selected_date = st.sidebar.selectbox("選擇查看日期", available_dates)

df_day = df[df.index.date == selected_date]
if df_day.empty:
    st.warning("該日期無可用的交易數據。")
    st.stop()

# 標記 09:30 - 09:45 區間
orb_start = datetime.combine(selected_date, datetime.strptime("09:30", "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
orb_end = datetime.combine(selected_date, datetime.strptime("09:45", "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))

df_orb = df_day[(df_day.index >= orb_start) & (df_day.index < orb_end)]

orb_high = None
orb_low = None
signal_msg = "🕒 今日尚未出現符合條件的開盤區間 (09:30-09:45) 數據。"
trade_signal = None

if not df_orb.empty:
    orb_high = float(df_orb['High'].max())
    orb_low = float(df_orb['Low'].min())
    
    # 尋找突破訊號
    df_post_orb = df_day[df_day.index >= orb_end]
    
    for i in range(len(df_post_orb)):
        current_close = float(df_post_orb['Close'].iloc[i])
        current_time = df_post_orb.index[i]
        
        # 做多
        if current_close > orb_high:
            entry_price = current_close
            sl_price = orb_low
            tp_price = entry_price + ((entry_price - sl_price) * 1.5)
            trade_signal = {'Type': 'BUY', 'Time': current_time, 'Entry': entry_price, 'SL': sl_price, 'TP': tp_price}
            signal_msg = f"🟢 **出現做多訊號！** (突破區間高點)\n\n- **進場時間**: {current_time.strftime('%H:%M')}\n- **進場價位**: `{entry_price:.4f}`\n- **止損 (SL)**: `{sl_price:.4f}`\n- **止盈 (TP)**: `{tp_price:.4f}`"
            break
            
        # 做空
        elif current_close < orb_low:
            entry_price = current_close
            sl_price = orb_high
            tp_price = entry_price - ((sl_price - entry_price) * 1.5)
            trade_signal = {'Type': 'SELL', 'Time': current_time, 'Entry': entry_price, 'SL': sl_price, 'TP': tp_price}
            signal_msg = f"🔴 **出現做空訊號！** (跌破區間低點)\n\n- **進場時間**: {current_time.strftime('%H:%M')}\n- **進場價位**: `{entry_price:.4f}`\n- **止損 (SL)**: `{sl_price:.4f}`\n- **止盈 (TP)**: `{tp_price:.4f}`"
            break
            
    if not trade_signal:
        signal_msg = "⏳ 今日開盤區間已確立，走勢尚在區間內，等待突破..."

# --- 畫面顯示 ---
st.subheader(f"💡 即時交易訊號 ({selected_date})")
if "做多" in signal_msg or "做空" in signal_msg:
    st.success(signal_msg)
else:
    st.info(signal_msg)

# 繪製圖表
fig = go.Figure()

# 加入 K 線
fig.add_trace(go.Candlestick(
    x=df_day.index, open=df_day['Open'], high=df_day['High'],
    low=df_day['Low'], close=df_day['Close'], name="5m K線"
))

if orb_high is not None and orb_low is not None:
    # 畫 ORB 區間線
    fig.add_hline(y=orb_high, line_dash="dash", line_color="orange", annotation_text="ORB High 09:45")
    fig.add_hline(y=orb_low, line_dash="dash", line_color="orange", annotation_text="ORB Low 09:45", annotation_position="bottom right")

    # 畫出止損與止盈標記
    if trade_signal:
        fig.add_vline(x=trade_signal['Time'], line_width=2, line_dash="dot", line_color="white")
        
        fig.add_trace(go.Scatter(
            x=[trade_signal['Time']], y=[trade_signal['Entry']], mode='markers+text',
            marker=dict(color='yellow', size=12), text=["進場點"], textposition="middle right", name="Entry"
        ))
        
        fig.add_hline(y=trade_signal['SL'], line_width=2, line_color="red", annotation_text=f"止損 SL ({trade_signal['SL']:.4f})")
        fig.add_hline(y=trade_signal['TP'], line_width=2, line_color="green", annotation_text=f"止盈 TP 1.5R ({trade_signal['TP']:.4f})")

# 背景顏色：時段劃分
session_colors =[
    ("00:00", "03:00", "rgba(255, 255, 0, 0.05)", "Asian Session"),
    ("03:00", "08:00", "rgba(0, 255, 255, 0.05)", "London Session"),
    ("08:00", "17:00", "rgba(255, 0, 255, 0.05)", "New York Session")
]
for start_time, end_time, color, name in session_colors:
    s_time = datetime.combine(selected_date, datetime.strptime(start_time, "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
    e_time = datetime.combine(selected_date, datetime.strptime(end_time, "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
    fig.add_vrect(x0=s_time, x1=e_time, fillcolor=color, opacity=1, layer="below", line_width=0, annotation_text=name, annotation_position="top left")

fig.update_layout(
    title=f"{selected_asset} 走勢圖",
    yaxis_title="價格 (USD)", xaxis_title="美東時間 (EST)",
    height=750, xaxis_rangeslider_visible=False, template="plotly_dark"
)

st.plotly_chart(fig, use_container_width=True)
