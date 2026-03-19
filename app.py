import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pytz
import requests

# 設定網頁佈局
st.set_page_config(page_title="NY Session Trading Strategy", layout="wide")

st.title("📈 紐約盤開盤區間與交易時段反轉策略")
st.markdown("""
### 交易邏輯 (參考影片策略)
1. **亞洲盤 (Asian Session)**: 建立流動性的盤整區間 (美東時間 20:00 - 03:00)。
2. **倫敦盤 (London Session)**: 清掃亞洲盤流動性，製造假突破 (美東時間 03:00 - 08:00)。
3. **紐約盤 (New York Session)**: 高交易量，真正的反轉趨勢或延續。
4. **紐約開盤區間 (NY Opening Range)**: 標記美東時間 **09:30 - 09:45** 的高低點。
5. **進場條件**: 
   - 價格突破 09:30 - 09:45 區間，產生失衡 (Displacement)。
   - 等待價格回踩該區間或公允價值缺口 (FVG)。
   - (程式以突破後回踩作為模擬進場點，止盈為 1.5 倍風險報酬比)
""")

# 側邊欄設定
st.sidebar.header("交易設定")
asset_dict = {
    "Bitcoin (BTC/USD)": "BTC-USD",
    "Gold (XAU/USD)": "GC=F",
    "Euro (EUR/USD)": "EURUSD=X"
}
selected_asset = st.sidebar.selectbox("選擇交易標的", list(asset_dict.keys()))
ticker = asset_dict[selected_asset]

days_to_fetch = st.sidebar.slider("載入最近天數的數據", min_value=1, max_value=7, value=3)

# 獲取數據 (使用 5 分鐘 K 線)
@st.cache_data(ttl=300)
def load_data(ticker, days):
    # 建立自訂的 Session 偽裝成瀏覽器，避免被 Yahoo 阻擋
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    # 抓取數據，並傳入我們自訂的 session
    data = yf.download(ticker, period=f"{days}d", interval="5m", session=session)
    
    if data.empty:
        return data
    
    # 處理 multi-index columns (yfinance 新版有時候會有)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)
        
    # 轉換時區為美東時間 (New York)
    if data.index.tz is None:
        data.index = data.index.tz_localize('UTC').tz_convert('America/New_York')
    else:
        data.index = data.index.tz_convert('America/New_York')
        
    return data

df = load_data(ticker, days_to_fetch)

if df.empty:
    st.error("無法獲取數據，請稍後再試。")
    st.stop()

# 選擇要查看的日期 (預設為最新交易日)
available_dates = sorted(list(set(df.index.date)), reverse=True)
selected_date = st.sidebar.selectbox("選擇查看日期", available_dates)

# 過濾出選擇的日期的數據
df_day = df[df.index.date == selected_date]

if df_day.empty:
    st.warning("該日期無可用的交易數據。")
    st.stop()

# 計算 09:30 - 09:45 開盤區間 (ORB)
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
    
    # 尋找交易訊號 (突破後的回踩進場)
    df_post_orb = df_day[df_day.index >= orb_end]
    
    for i in range(len(df_post_orb)):
        current_close = float(df_post_orb['Close'].iloc[i])
        current_time = df_post_orb.index[i]
        
        # 做多訊號邏輯：價格突破 ORB 上緣
        if current_close > orb_high:
            entry_price = current_close
            sl_price = orb_low  # 止損設在區間下緣
            risk = entry_price - sl_price
            tp_price = entry_price + (risk * 1.5)  # 1.5倍盈虧比
            
            trade_signal = {
                'Type': 'BUY (做多)',
                'Time': current_time,
                'Entry': entry_price,
                'SL': sl_price,
                'TP': tp_price
            }
            signal_msg = f"🟢 **出現做多訊號！** \n\n突破區間高點！\n- **時間**: {current_time.strftime('%H:%M')}\n- **進場價位**: {entry_price:.4f}\n- **止損 (SL)**: {sl_price:.4f}\n- **止盈 (TP)**: {tp_price:.4f}"
            break
            
        # 做空訊號邏輯：價格跌破 ORB 下緣
        elif current_close < orb_low:
            entry_price = current_close
            sl_price = orb_high  # 止損設在區間上緣
            risk = sl_price - entry_price
            tp_price = entry_price - (risk * 1.5)  # 1.5倍盈虧比
            
            trade_signal = {
                'Type': 'SELL (做空)',
                'Time': current_time,
                'Entry': entry_price,
                'SL': sl_price,
                'TP': tp_price
            }
            signal_msg = f"🔴 **出現做空訊號！** \n\n跌破區間低點！\n- **時間**: {current_time.strftime('%H:%M')}\n- **進場價位**: {entry_price:.4f}\n- **止損 (SL)**: {sl_price:.4f}\n- **止盈 (TP)**: {tp_price:.4f}"
            break
    
    if not trade_signal:
        signal_msg = "⏳ 今日走勢尚在區間內，未出現明顯的突破進場訊號。"

# 顯示訊號
st.subheader("💡 即時交易訊號")
st.info(signal_msg)

# 繪製 Plotly K線圖
fig = go.Figure()

# 加入 K 線
fig.add_trace(go.Candlestick(
    x=df_day.index,
    open=df_day['Open'],
    high=df_day['High'],
    low=df_day['Low'],
    close=df_day['Close'],
    name="5m K線"
))

# 標示 ORB 區間 (09:30 - 09:45)
if orb_high is not None and orb_low is not None:
    # 高點線
    fig.add_hline(y=orb_high, line_dash="dash", line_color="orange", annotation_text="ORB High (區間高點)")
    # 低點線
    fig.add_hline(y=orb_low, line_dash="dash", line_color="orange", annotation_text="ORB Low (區間低點)", annotation_position="bottom right")

    # 若有交易訊號，在圖上畫出止盈止損線和進場點
    if trade_signal:
        fig.add_vline(x=trade_signal['Time'], line_width=2, line_dash="dot", line_color="blue")
        
        # 進場點
        fig.add_trace(go.Scatter(
            x=[trade_signal['Time']], 
            y=[trade_signal['Entry']], 
            mode='markers+text',
            marker=dict(color='blue', size=10),
            text=["進場 (Entry)"],
            textposition="middle right",
            name="進場點"
        ))
        
        # 止損線 (紅色)
        fig.add_hline(y=trade_signal['SL'], line_width=2, line_color="red", annotation_text=f"止損 SL: {trade_signal['SL']:.4f}")
        
        # 止盈線 (綠色)
        fig.add_hline(y=trade_signal['TP'], line_width=2, line_color="green", annotation_text=f"止盈 TP: {trade_signal['TP']:.4f}")

# 設置圖表版面
fig.update_layout(
    title=f"{selected_asset} - 5分鐘 K線圖 (美東時間)",
    yaxis_title="價格 (USD)",
    xaxis_title="美東時間 (EST)",
    height=700,
    xaxis_rangeslider_visible=False,
    template="plotly_dark"
)

# 畫出不同交易時段的背景色 (Asian, London, NY)
# 亞洲盤 (前一日 20:00 - 03:00) / 倫敦盤 (03:00 - 08:00) / 紐約盤 (08:00 - 17:00)
session_colors =[
    ("00:00", "03:00", "rgba(255, 255, 0, 0.05)", "Asian Session (End)"),
    ("03:00", "08:00", "rgba(0, 255, 255, 0.05)", "London Session"),
    ("08:00", "17:00", "rgba(255, 0, 255, 0.05)", "New York Session")
]

for start_time, end_time, color, name in session_colors:
    s_time = datetime.combine(selected_date, datetime.strptime(start_time, "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
    e_time = datetime.combine(selected_date, datetime.strptime(end_time, "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
    
    fig.add_vrect(
        x0=s_time, x1=e_time,
        fillcolor=color, opacity=1, layer="below", line_width=0,
        annotation_text=name, annotation_position="top left"
    )

st.plotly_chart(fig, use_container_width=True)

st.markdown("""
---
**免責聲明**：此程式僅用於展示影片中提及之策略邏輯（開盤區間突破與盈虧比計算），所產生的交易訊號僅供教學與學術研究參考，**不構成任何金融投資建議**。
""")
