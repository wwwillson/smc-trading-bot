import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta

# 設定網頁版面
st.set_page_config(page_title="SMC 交易策略視覺化", layout="wide")

# --- UI 與側邊欄設定 ---
st.title("📈 SMC 交易策略回測與訊號提示 (參考影片邏輯)")

st.sidebar.header("⚙️ 參數設定")
asset_dict = {
    "Bitcoin (BTC/USD)": "BTC-USD",
    "Gold (XAU/USD)": "GC=F",
    "Euro (EUR/USD)": "EURUSD=X"
}
asset_choice = st.sidebar.selectbox("選擇交易商品", list(asset_dict.keys()))
ticker = asset_dict[asset_choice]

tf_choice = st.sidebar.selectbox("選擇時間級別 (Timeframe)", ["1d", "1h", "15m"])
# 注意：yfinance 的 15m 資料最多只能抓取近 60 天
days_to_fetch = st.sidebar.slider("載入歷史天數", 5, 60, 30)

# --- 抓取市場資料 ---
@st.cache_data(ttl=300)
def load_data(ticker, interval, days):
    end_date = pd.Timestamp.now()
    start_date = end_date - timedelta(days=days)
    df = yf.download(ticker, start=start_date, end=end_date, interval=interval)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.dropna(inplace=True)
    return df

df = load_data(ticker, tf_choice, days_to_fetch)

# --- 影片交易邏輯核心演算法 (SMC FVG & Pullback) ---
def identify_fvg_and_signals(df):
    signals = []
    fvgs =[]
    
    # 步驟 4 & 5: 尋找回撤產生的 FVG (合理價值缺口) 作為 Key Level
    for i in range(1, len(df) - 2):
        c1 = df.iloc[i-1]
        c2 = df.iloc[i]
        c3 = df.iloc[i+1]
        
        # 尋找做空缺口 (Bearish FVG) - 伴隨向下結構破壞
        if c3['High'] < c1['Low'] and c2['Close'] < c2['Open']:
            fvgs.append({
                'type': 'Bearish',
                'start_index': df.index[i+1],
                'top': c1['Low'],
                'bottom': c3['High'],
                'sl': c1['High'], # 止損設為結構高點
                'active': True
            })
            
        # 尋找做多缺口 (Bullish FVG) - 伴隨向上結構破壞
        elif c3['Low'] > c1['High'] and c2['Close'] > c2['Open']:
            fvgs.append({
                'type': 'Bullish',
                'start_index': df.index[i+1],
                'top': c3['Low'],
                'bottom': c1['High'],
                'sl': c1['Low'], # 止損設為結構低點
                'active': True
            })
            
    # 步驟 6: 等待第二次回撤觸發進場 (價格回踩進入 Key Level)
    for i in range(3, len(df)):
        current_time = df.index[i]
        row = df.iloc[i]
        
        for fvg in fvgs:
            if not fvg['active'] or current_time <= fvg['start_index']:
                continue
                
            # 做空進場：價格向上回測觸碰到 Bearish FVG 的底部
            if fvg['type'] == 'Bearish' and row['High'] >= fvg['bottom']:
                fvg['active'] = False # 觸發後標記失效
                entry_price = fvg['bottom']
                sl = fvg['sl']
                tp = entry_price - (sl - entry_price) * 2 # 固定 1:2 盈虧比
                signals.append({'time': current_time, 'type': 'Sell', 'entry': entry_price, 'sl': sl, 'tp': tp})
                
            # 做多進場：價格向下回測觸碰到 Bullish FVG 的頂部
            elif fvg['type'] == 'Bullish' and row['Low'] <= fvg['top']:
                fvg['active'] = False
                entry_price = fvg['top']
                sl = fvg['sl']
                tp = entry_price + (entry_price - sl) * 2 # 固定 1:2 盈虧比
                signals.append({'time': current_time, 'type': 'Buy', 'entry': entry_price, 'sl': sl, 'tp': tp})
                
    return fvgs, signals

fvgs, signals = identify_fvg_and_signals(df)

# --- 網頁佈局 ---
col1, col2 = st.columns([1, 3])

with col1:
    st.markdown("### 📖 影片完整交易邏輯")
    st.markdown("""
    此程式將影片中的 6 個步驟程式化呈現：
    1. **確認趨勢 (Trend)**: 尋找連續的市場結構破壞 (BOS)。
    2. **等待弱勢 (Weakness)**: 趨勢末端價格無法實體突破前高/低，留下引線假突破。
    3. **強勢反轉 (Strength)**: 出現強烈反向實體K線，破壞近期結構 (ChoCh)。
    4. **尋找第一次回撤**: 在強勢反轉段中尋找 **合理價值缺口 (FVG)**。
    5. **標記關鍵區間 (Key Level)**: 畫出 FVG 區域 (圖中彩色半透明區塊)。
    6. **確認進場 (Entry)**: 當價格**回測進入該 FVG 區間時**觸發訊號。程式自動給出：
       - **止損 (SL)** 於結構極點外。
       - **止盈 (TP)** 以 1:2 盈虧比計算出目標價位。
    """)
    
    st.markdown("---")
    st.markdown("### 🚨 最新交易訊號提示")
    if signals:
        latest = signals[-1]
        signal_color = "🟢 多單 (Buy)" if latest['type'] == 'Buy' else "🔴 空單 (Sell)"
        st.success(f"**方向**: {signal_color}")
        st.info(f"**進場價位 (Entry)**: {latest['entry']:.4f}")
        st.error(f"**止損位 (SL)**: {latest['sl']:.4f}")
        st.warning(f"**止盈位 (TP)**: {latest['tp']:.4f}")
        st.write(f"觸發時間: `{latest['time']}`")
    else:
        st.write("目前載入的區間內無符合條件的交易訊號。")

with col2:
    # --- Plotly 圖表繪製 ---
    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線'
    )])

    # 繪製尚未填補的關鍵區間 (FVG Key Levels)
    active_fvgs = [fvg for fvg in fvgs if fvg['active']][-10:] # 只顯示近10個避免畫面雜亂
    for fvg in active_fvgs:
        color = "rgba(255, 0, 0, 0.2)" if fvg['type'] == 'Bearish' else "rgba(0, 255, 0, 0.2)"
        fig.add_shape(type="rect", x0=fvg['start_index'], y0=fvg['bottom'], x1=df.index[-1], y1=fvg['top'],
                      fillcolor=color, line_width=0, layer="below")

    # 繪製歷史交易訊號點
    buy_times =[s['time'] for s in signals if s['type'] == 'Buy']
    buy_prices =[s['entry'] for s in signals if s['type'] == 'Buy']
    sell_times =[s['time'] for s in signals if s['type'] == 'Sell']
    sell_prices =[s['entry'] for s in signals if s['type'] == 'Sell']

    fig.add_trace(go.Scatter(x=buy_times, y=buy_prices, mode='markers', 
                             marker=dict(symbol='triangle-up', size=15, color='lime', line=dict(width=1, color='white')), name='做多訊號'))
    fig.add_trace(go.Scatter(x=sell_times, y=sell_prices, mode='markers', 
                             marker=dict(symbol='triangle-down', size=15, color='red', line=dict(width=1, color='white')), name='做空訊號'))

    # 若有最新訊號，在畫面上畫出明確的 TP 與 SL 架位線
    if signals:
        latest = signals[-1]
        fig.add_hline(y=latest['sl'], line_dash="dash", line_color="red", annotation_text=f"止損 SL: {latest['sl']:.4f}", annotation_position="top right")
        fig.add_hline(y=latest['entry'], line_dash="solid", line_color="white", annotation_text=f"進場 Entry: {latest['entry']:.4f}", annotation_position="bottom right", opacity=0.5)
        fig.add_hline(y=latest['tp'], line_dash="dash", line_color="lime", annotation_text=f"止盈 TP: {latest['tp']:.4f}", annotation_position="bottom right")

    fig.update_layout(xaxis_rangeslider_visible=False, height=700, template="plotly_dark", margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig, use_container_width=True)
