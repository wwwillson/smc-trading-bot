import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import numpy as np

# --- 頁面設定 ---
st.set_page_config(page_title="Sniper Entry 交易訊號系統", layout="wide")

# --- 影片交易邏輯說明 ---
st.title("🎯 狙擊手進場 (Sniper Entry) 交易訊號系統")
st.markdown("""
### 🧠 交易邏輯 (參考自影片: How To Find Sniper Entries Everytime)
本系統根據 SMC (Smart Money Concepts) 打造，專注於 **高盈虧比 (High R:R)** 的交易：
1. **兩大關鍵位重疊 (Confluence)**：尋找公平價值缺口 (FVG, Imbalance) 與流動性區間 (Swing High/Low) 的重疊點。
2. **極小止損 (Tight Stop Loss)**：進場後，止損設於關鍵K線的極值之外，不給市場太多容錯空間。
3. **高盈虧比 (High Reward to Risk)**：目標獲利至少設為 1:3 到 1:10，寧可勝率較低 (如 20-30%)，也要透過高賠率達到長期獲利。
""")

# --- 側邊欄設定 ---
st.sidebar.header("⚙️ 參數設定")
assets = {
    "Bitcoin (BTC/USD)": "BTC-USD",
    "Gold (XAU/USD)": "GC=F",
    "Euro (EUR/USD)": "EURUSD=X"
}
selected_asset = st.sidebar.selectbox("選擇交易標的", list(assets.keys()))
ticker = assets[selected_asset]

timeframe = st.sidebar.selectbox("選擇時間級別",["15m", "30m", "1h", "4h", "1d"], index=2)
rr_ratio = st.sidebar.slider("盈虧比 (Reward to Risk)", min_value=1.0, max_value=10.0, value=3.0, step=0.5)

# --- 抓取數據 ---
@st.cache_data(ttl=300) # 快取5分鐘
def load_data(ticker, interval):
    # 根據級別決定抓取的天數
    period = "60d" if interval in ["1h", "4h", "1d"] else "7d"
    df = yf.download(ticker, period=period, interval=interval)
    if not df.empty:
        # yfinance 回傳的多層索引欄位處理
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df.reset_index(inplace=True)
        # 統一時間欄位名稱
        df.rename(columns={'Datetime': 'Date'}, inplace=True)
        df.rename(columns={'index': 'Date'}, inplace=True)
    return df

df = load_data(ticker, timeframe)

# --- 核心邏輯：尋找 FVG 與計算進場訊號 ---
def detect_signals(df, rr):
    df['FVG_Bullish'] = False
    df['FVG_Bearish'] = False
    df['Signal'] = 0 # 1 為做多, -1 為做空
    df['Entry_Price'] = np.nan
    df['SL'] = np.nan
    df['TP'] = np.nan

    # 1. 偵測 FVG (缺口)
    for i in range(2, len(df)):
        # 牛市 FVG: 第1根的High < 第3根的Low
        if df['High'].iloc[i-2] < df['Low'].iloc[i]:
            df.at[i-1, 'FVG_Bullish'] = True
        # 熊市 FVG: 第1根的Low > 第3根的High
        elif df['Low'].iloc[i-2] > df['High'].iloc[i]:
            df.at[i-1, 'FVG_Bearish'] = True

    # 2. 尋找回踩 FVG 進場點 (簡化版的 Sniper Entry)
    active_bull_fvg = None
    active_bear_fvg = None

    for i in range(3, len(df)):
        # 記錄最新的缺口
        if df['FVG_Bullish'].iloc[i-2]:
            active_bull_fvg = {'top': df['Low'].iloc[i-1], 'bottom': df['High'].iloc[i-3], 'idx': i-2}
        if df['FVG_Bearish'].iloc[i-2]:
            active_bear_fvg = {'top': df['Low'].iloc[i-3], 'bottom': df['High'].iloc[i-1], 'idx': i-2}

        # 做多邏輯：價格跌入 Bullish FVG
        if active_bull_fvg and df['Low'].iloc[i] <= active_bull_fvg['top'] and df['Close'].iloc[i] > active_bull_fvg['bottom']:
            entry = df['Close'].iloc[i]
            sl = active_bull_fvg['bottom'] - (entry * 0.0005) # 止損設在缺口下緣再低一點
            risk = entry - sl
            tp = entry + (risk * rr)
            
            df.at[i, 'Signal'] = 1
            df.at[i, 'Entry_Price'] = entry
            df.at[i, 'SL'] = sl
            df.at[i, 'TP'] = tp
            active_bull_fvg = None # 用過即作廢

        # 做空邏輯：價格漲入 Bearish FVG
        elif active_bear_fvg and df['High'].iloc[i] >= active_bear_fvg['bottom'] and df['Close'].iloc[i] < active_bear_fvg['top']:
            entry = df['Close'].iloc[i]
            sl = active_bear_fvg['top'] + (entry * 0.0005) # 止損設在缺口上緣再高一點
            risk = sl - entry
            tp = entry - (risk * rr)

            df.at[i, 'Signal'] = -1
            df.at[i, 'Entry_Price'] = entry
            df.at[i, 'SL'] = sl
            df.at[i, 'TP'] = tp
            active_bear_fvg = None # 用過即作廢

    return df

if not df.empty:
    df_signals = detect_signals(df, rr_ratio)

    # --- 最新訊號提示 ---
    latest_signal = df_signals.iloc[-1]
    if latest_signal['Signal'] == 1:
        st.success(f"🟢 **最新狙擊手作多訊號出現！** 價格: {latest_signal['Entry_Price']:.4f} | 止損: {latest_signal['SL']:.4f} | 止盈: {latest_signal['TP']:.4f}")
    elif latest_signal['Signal'] == -1:
        st.error(f"🔴 **最新狙擊手作空訊號出現！** 價格: {latest_signal['Entry_Price']:.4f} | 止損: {latest_signal['SL']:.4f} | 止盈: {latest_signal['TP']:.4f}")
    else:
        st.info("🕒 目前市場監控中，尚未出現符合嚴格條件的 Sniper Entry 訊號...")

    # --- 繪製互動式圖表 (Plotly) ---
    fig = go.Figure()

    # K線圖
    fig.add_trace(go.Candlestick(
        x=df_signals['Date'],
        open=df_signals['Open'],
        high=df_signals['High'],
        low=df_signals['Low'],
        close=df_signals['Close'],
        name='K線',
        increasing_line_color='green',
        decreasing_line_color='red'
    ))

    # 標示進場、止損、止盈訊號
    for i, row in df_signals.iterrows():
        if row['Signal'] == 1: # 做多
            # 進場點
            fig.add_annotation(x=row['Date'], y=row['Entry_Price'], text="🔼 BUY", showarrow=True, arrowhead=1, arrowcolor="green", font=dict(color="white", size=12), bgcolor="green")
            # 止損線 (紅色)
            fig.add_shape(type="line", x0=row['Date'], y0=row['SL'], x1=df_signals['Date'].iloc[-1], y1=row['SL'], line=dict(color="red", width=2, dash="dash"))
            fig.add_annotation(x=df_signals['Date'].iloc[-1], y=row['SL'], text=f"SL: {row['SL']:.4f}", font=dict(color="red"), xanchor="left")
            # 止盈線 (綠色)
            fig.add_shape(type="line", x0=row['Date'], y0=row['TP'], x1=df_signals['Date'].iloc[-1], y1=row['TP'], line=dict(color="green", width=2, dash="dash"))
            fig.add_annotation(x=df_signals['Date'].iloc[-1], y=row['TP'], text=f"TP: {row['TP']:.4f}", font=dict(color="green"), xanchor="left")
            
        elif row['Signal'] == -1: # 做空
            # 進場點
            fig.add_annotation(x=row['Date'], y=row['Entry_Price'], text="🔽 SELL", showarrow=True, arrowhead=1, arrowcolor="red", font=dict(color="white", size=12), bgcolor="red")
            # 止損線 (紅色)
            fig.add_shape(type="line", x0=row['Date'], y0=row['SL'], x1=df_signals['Date'].iloc[-1], y1=row['SL'], line=dict(color="red", width=2, dash="dash"))
            fig.add_annotation(x=df_signals['Date'].iloc[-1], y=row['SL'], text=f"SL: {row['SL']:.4f}", font=dict(color="red"), xanchor="left")
            # 止盈線 (綠色)
            fig.add_shape(type="line", x0=row['Date'], y0=row['TP'], x1=df_signals['Date'].iloc[-1], y1=row['TP'], line=dict(color="green", width=2, dash="dash"))
            fig.add_annotation(x=df_signals['Date'].iloc[-1], y=row['TP'], text=f"TP: {row['TP']:.4f}", font=dict(color="green"), xanchor="left")

    fig.update_layout(
        title=f"{selected_asset} 價格圖表與交易訊號",
        yaxis_title="價格",
        xaxis_title="時間",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=700,
        margin=dict(l=50, r=100, b=50, t=50) # 右側留空間給標籤
    )

    st.plotly_chart(fig, use_container_width=True)

    # 顯示數據明細
    st.subheader("📊 近期觸發之交易訊號紀錄")
    signals_only = df_signals[df_signals['Signal'] != 0].tail(10)[['Date', 'Close', 'Signal', 'Entry_Price', 'SL', 'TP']]
    signals_only['Signal'] = signals_only['Signal'].map({1: '做多 (BUY)', -1: '做空 (SELL)'})
    st.dataframe(signals_only.style.format({'Close': '{:.4f}', 'Entry_Price': '{:.4f}', 'SL': '{:.4f}', 'TP': '{:.4f}'}))

else:
    st.error("無法抓取數據，請稍後再試。")
