import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go

# 設定網頁配置
st.set_page_config(page_title="SMC 量化交易策略儀表板", layout="wide")

# 定義交易商品字典
TICKERS = {
    "Bitcoin vs USD": "BTC-USD",
    "Gold vs USD": "GC=F",
    "Euro vs USD": "EURUSD=X"
}

# 側邊欄設計
st.sidebar.title("⚙️ 交易參數設定")
selected_asset = st.sidebar.selectbox("選擇交易商品", list(TICKERS.keys()))
ticker_symbol = TICKERS[selected_asset]

timeframe = st.sidebar.selectbox("選擇時間級別",["15m", "1h", "4h", "1d"], index=2)
period = st.sidebar.selectbox("載入歷史資料長度",["5d", "1mo", "3mo", "1y"], index=1)

rr_ratio = st.sidebar.slider("設定盈虧比 (Risk:Reward)", min_value=1.0, max_value=5.0, value=2.0, step=0.1)

# 顯示影片交易邏輯
st.sidebar.markdown("""
### 🧠 策略核心邏輯 (參考影片 SMC 概念)
1. **Step 1 (趨勢):** 尋找連續破位(BOS)的趨勢市場。
2. **Step 2 (轉弱):** 尋找假突破(Fakeout/Weakness)。
3. **Step 3 (反轉):** 反向出現強勢破位(Strong BOS)。
4. **Step 4 (回調):** 等待第一次回調確認。
5. **Step 5 (關鍵位):** 標示關鍵位置 (此程式以動態均線區間模擬 FVG/OB)。
6. **Step 6 (進場):** 第二次深度回調至關鍵位，出現**吞噬型態(Engulfing)**即進場。
""")

@st.cache_data
def load_data(ticker, period, interval):
    # 下載歷史數據
    df = yf.download(ticker, period=period, interval=interval)
    
    # 🌟【修復關鍵】: 檢查並攤平多層級的 Columns (處理新版 yfinance 格式變更的問題)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    df.dropna(inplace=True)
    return df

def calculate_strategy(df, rr_ratio):
    # 計算 ATR 用於設定動態止損
    df['High-Low'] = df['High'] - df['Low']
    df['High-PrevClose'] = np.abs(df['High'] - df['Close'].shift(1))
    df['Low-PrevClose'] = np.abs(df['Low'] - df['Close'].shift(1))
    df['TR'] = df[['High-Low', 'High-PrevClose', 'Low-PrevClose']].max(axis=1)
    df['ATR'] = df['TR'].rolling(window=14).mean()

    # 模擬關鍵位置 (Key Level) - 使用 EMA 通道作為價值區間
    df['EMA_20'] = df['Close'].rolling(window=20).mean()
    df['EMA_50'] = df['Close'].rolling(window=50).mean()

    # 尋找吞噬型態 (Engulfing Pattern)
    df['Body'] = np.abs(df['Close'] - df['Open'])
    
    # 看漲吞噬
    df['Bullish_Engulfing'] = (df['Close'] > df['Open']) & \
                              (df['Close'].shift(1) < df['Open'].shift(1)) & \
                              (df['Close'] > df['Open'].shift(1)) & \
                              (df['Open'] < df['Close'].shift(1))
    
    # 看跌吞噬
    df['Bearish_Engulfing'] = (df['Close'] < df['Open']) & \
                              (df['Close'].shift(1) > df['Open'].shift(1)) & \
                              (df['Close'] < df['Open'].shift(1)) & \
                              (df['Open'] > df['Close'].shift(1))

    # 產生訊號與紀錄 SL/TP
    df['Signal'] = 0
    df['Entry_Price'] = np.nan
    df['SL'] = np.nan
    df['TP'] = np.nan

    # 使用 index 迭代避免 Warning
    signal_idx = df.columns.get_loc('Signal')
    entry_idx = df.columns.get_loc('Entry_Price')
    sl_idx = df.columns.get_loc('SL')
    tp_idx = df.columns.get_loc('TP')

    for i in range(1, len(df)):
        # 條件：多頭趨勢回調 (價格在均線區間附近，且 EMA20 > EMA50)
        if df['EMA_20'].iloc[i] > df['EMA_50'].iloc[i]:
            if df['Bullish_Engulfing'].iloc[i] and (df['Low'].iloc[i] <= df['EMA_20'].iloc[i]):
                df.iat[i, signal_idx] = 1
                entry = float(df['Close'].iloc[i])
                sl = float(df['Low'].iloc[i] - (df['ATR'].iloc[i] * 0.5)) # 止損設在K線低點加一點緩衝
                tp = float(entry + ((entry - sl) * rr_ratio)) # 依據盈虧比計算止盈
                df.iat[i, entry_idx] = entry
                df.iat[i, sl_idx] = sl
                df.iat[i, tp_idx] = tp

        # 條件：空頭趨勢回調 (價格在均線區間附近，且 EMA20 < EMA50)
        elif df['EMA_20'].iloc[i] < df['EMA_50'].iloc[i]:
            if df['Bearish_Engulfing'].iloc[i] and (df['High'].iloc[i] >= df['EMA_20'].iloc[i]):
                df.iat[i, signal_idx] = -1
                entry = float(df['Close'].iloc[i])
                sl = float(df['High'].iloc[i] + (df['ATR'].iloc[i] * 0.5))
                tp = float(entry - ((sl - entry) * rr_ratio))
                df.iat[i, entry_idx] = entry
                df.iat[i, sl_idx] = sl
                df.iat[i, tp_idx] = tp

    return df

# 載入並計算資料
st.title(f"📊 {selected_asset} SMC 策略分析圖表")
st.write(f"當前資料週期: **{period}**, K棒級別: **{timeframe}**, 設定盈虧比: **1 : {rr_ratio}**")

with st.spinner('載入資料與計算訊號中...'):
    df = load_data(ticker_symbol, period, timeframe)
    df = calculate_strategy(df, rr_ratio)

# 使用 Plotly 繪圖
fig = go.Figure()

# 加入 K 線圖
fig.add_trace(go.Candlestick(x=df.index,
                open=df['Open'], high=df['High'],
                low=df['Low'], close=df['Close'],
                name='價格'))

# 加入均線 (作為動態 Key Level 參考)
fig.add_trace(go.Scatter(x=df.index, y=df['EMA_20'], line=dict(color='orange', width=1.5), name='EMA 20 (第一防線)'))
fig.add_trace(go.Scatter(x=df.index, y=df['EMA_50'], line=dict(color='blue', width=1.5), name='EMA 50 (趨勢線)'))

# 標示買賣訊號與 SL/TP
buy_signals = df[df['Signal'] == 1]
sell_signals = df[df['Signal'] == -1]

# 買入訊號圖標
fig.add_trace(go.Scatter(x=buy_signals.index, y=buy_signals['Low'] - (buy_signals['ATR']*0.5), 
                         mode='markers', marker=dict(symbol='triangle-up', color='green', size=15), 
                         name='買入訊號 (Long)'))

# 賣出訊號圖標
fig.add_trace(go.Scatter(x=sell_signals.index, y=sell_signals['High'] + (sell_signals['ATR']*0.5), 
                         mode='markers', marker=dict(symbol='triangle-down', color='red', size=15), 
                         name='賣出訊號 (Short)'))

# 在圖表上標示最近一次訊號的 SL 和 TP 線
recent_signals = df[df['Signal'] != 0]
if not recent_signals.empty:
    last_signal = recent_signals.iloc[-1]
    last_idx = recent_signals.index[-1]
    entry = float(last_signal['Entry_Price'])
    sl = float(last_signal['SL'])
    tp = float(last_signal['TP'])
    signal_type = "買入 (Long)" if last_signal['Signal'] == 1 else "賣出 (Short)"
    
    st.success(f"🚨 **最新訊號提示:** 於 {last_idx.strftime('%Y-%m-%d %H:%M')} 出現 **{signal_type}** 訊號！\n"
               f"**進場價:** {entry:.5f} | **止損 (SL):** {sl:.5f} | **止盈 (TP):** {tp:.5f}")

    # 畫水平線代表止盈止損
    fig.add_hline(y=sl, line_dash="dash", line_color="red", annotation_text=f"止損 SL: {sl:.4f}", annotation_position="top right")
    fig.add_hline(y=tp, line_dash="dash", line_color="green", annotation_text=f"止盈 TP: {tp:.4f}", annotation_position="bottom right")
    fig.add_hline(y=entry, line_dash="solid", line_color="yellow", annotation_text=f"進場 Entry: {entry:.4f}", annotation_position="bottom right")

# 圖表外觀設定
fig.update_layout(
    height=700,
    template="plotly_dark",
    xaxis_rangeslider_visible=False,
    title="圖表出現綠色▲/紅色▼代表符合「回調至關鍵區+吞噬型態」的進場條件",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig, use_container_width=True)

# 在最下方顯示歷史訊號表格
st.subheader("📋 歷史訊號紀錄清單")
if not recent_signals.empty:
    display_df = recent_signals[['Close', 'Signal', 'Entry_Price', 'SL', 'TP']].copy()
    display_df['Signal'] = display_df['Signal'].apply(lambda x: 'LONG 🟢' if x == 1 else 'SHORT 🔴')
    display_df.index = display_df.index.strftime('%Y-%m-%d %H:%M')
    st.dataframe(display_df.sort_index(ascending=False))
else:
    st.write("目前選定期間內無符合條件之訊號。")
