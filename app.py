import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import numpy as np

# 設定網頁版面
st.set_page_config(page_title="SMC 聰明錢交易策略系統", layout="wide")

# --- UI：側邊欄設定 ---
st.sidebar.header("⚙️ 交易設定")
asset_choice = st.sidebar.selectbox(
    "選擇交易標的",["Bitcoin (BTC/USD)", "Gold (XAU/USD)", "Euro (EUR/USD)"]
)

# 對應 Yahoo Finance 的代碼
ticker_map = {
    "Bitcoin (BTC/USD)": "BTC-USD",
    "Gold (XAU/USD)": "GC=F",
    "Euro (EUR/USD)": "EURUSD=X"
}
ticker = ticker_map[asset_choice]

timeframe = st.sidebar.selectbox("時間框架 (Timeframe)",["15m", "1h", "1d"], index=1)
period_map = {"15m": "5d", "1h": "1mo", "1d": "1y"}

# 盈虧比設定
rr_ratio = st.sidebar.number_input("盈虧比 (Risk/Reward Ratio)", min_value=1.0, max_value=5.0, value=2.0, step=0.5)

# --- 下載數據 ---
@st.cache_data(ttl=300) # 緩存5分鐘
def load_data(ticker, period, interval):
    # 【修正】使用 yf.Ticker().history() 避免新版 yfinance 產生多層索引 (MultiIndex) 問題
    stock = yf.Ticker(ticker)
    df = stock.history(period=period, interval=interval)
    df.dropna(inplace=True)
    return df

with st.spinner('獲取最新市場數據中...'):
    df = load_data(ticker, period_map[timeframe], timeframe)

# --- 交易邏輯演算法 (簡易版 SMC) ---
def identify_smc_signals(df, window=5):
    # 尋找波段高低點 (Swing Highs / Swing Lows)
    df['Swing_High'] = df['High'][(df['High'] == df['High'].rolling(window=window*2+1, center=True).max())]
    df['Swing_Low'] = df['Low'][(df['Low'] == df['Low'].rolling(window=window*2+1, center=True).min())]
    
    # 填補波段數據以便對比
    df['Last_SH'] = df['Swing_High'].ffill()
    df['Last_SL'] = df['Swing_Low'].ffill()
    
    signals =[]
    in_position = False
    
    # 模擬影片邏輯：Market Structure Shift (市場結構轉變)
    for i in range(1, len(df)):
        # 【修正】確保取出的數值絕對是 float 純數字，避免 Series 比較錯誤
        current_close = float(df['Close'].iloc[i])
        
        last_sh_val = df['Last_SH'].iloc[i-1]
        last_sl_val = df['Last_SL'].iloc[i-1]
        last_sh = float(last_sh_val) if not pd.isna(last_sh_val) else np.nan
        last_sl = float(last_sl_val) if not pd.isna(last_sl_val) else np.nan
        
        # 做多訊號 (Bullish Market Shift)
        if current_close > last_sh and not in_position and not np.isnan(last_sh):
            entry_price = current_close
            # 【修正】加入 max(0, i-window) 避免索引變成負數
            sl_price = float(df['Low'].iloc[max(0, i-window):i].min()) 
            risk = entry_price - sl_price
            tp_price = entry_price + (risk * rr_ratio) # 止盈依據盈虧比計算
            
            if risk > 0:
                signals.append((df.index[i], 'Buy', entry_price, sl_price, tp_price))
                in_position = True
                
        # 做空訊號 (Bearish Market Shift)
        elif current_close < last_sl and not in_position and not np.isnan(last_sl):
            entry_price = current_close
            sl_price = float(df['High'].iloc[max(0, i-window):i].max()) 
            risk = sl_price - entry_price
            tp_price = entry_price - (risk * rr_ratio)
            
            if risk > 0:
                signals.append((df.index[i], 'Sell', entry_price, sl_price, tp_price))
                in_position = True
                
        # 簡單的平倉邏輯 (觸及SL或TP) 允許下一次訊號
        if in_position:
            last_signal = signals[-1]
            curr_low = float(df['Low'].iloc[i])
            curr_high = float(df['High'].iloc[i])
            
            if last_signal[1] == 'Buy':
                if curr_low <= last_signal[3] or curr_high >= last_signal[4]:
                    in_position = False
            elif last_signal[1] == 'Sell':
                if curr_high >= last_signal[3] or curr_low <= last_signal[4]:
                    in_position = False

    return df, signals

df, signals = identify_smc_signals(df)

# --- 繪製 K 線圖與標示 ---
fig = go.Figure(data=[go.Candlestick(x=df.index,
                open=df['Open'], high=df['High'],
                low=df['Low'], close=df['Close'],
                name='K線')])

# 取出最後一個訊號來畫止損止盈線
latest_signal = None
if signals:
    latest_signal = signals[-1]
    
    # 在圖表上標示所有訊號點
    for sig in signals:
        if sig[1] == 'Buy':
            fig.add_trace(go.Scatter(x=[sig[0]], y=[sig[2]], mode='markers', 
                                     marker=dict(symbol='triangle-up', color='green', size=15), name='買入訊號'))
        else:
            fig.add_trace(go.Scatter(x=[sig[0]], y=[sig[2]], mode='markers', 
                                     marker=dict(symbol='triangle-down', color='red', size=15), name='賣出訊號'))

    # 為最新訊號繪製 Entry, SL, TP 參考線與區塊
    date, sig_type, entry, sl, tp = latest_signal
    
    # 畫止盈區間
    fig.add_hrect(y0=entry, y1=tp, fillcolor="green", opacity=0.1, line_width=0, annotation_text=f"TP 止盈 ({tp:.4f})")
    # 畫止損區間
    fig.add_hrect(y0=entry, y1=sl, fillcolor="red", opacity=0.1, line_width=0, annotation_text=f"SL 止損 ({sl:.4f})")
    # 入場線
    fig.add_hline(y=entry, line_dash="dash", line_color="blue", annotation_text=f"Entry 進場 ({entry:.4f})")

fig.update_layout(title=f"{asset_choice} 即時交易圖表與 SMC 訊號",
                  yaxis_title='價格', xaxis_title='時間',
                  template='plotly_dark', height=600,
                  xaxis_rangeslider_visible=False) # 關閉底部的時間滑桿讓版面更乾淨

# --- UI：主畫面 ---
st.title("📈 SMC 聰明錢概念交易系統")
st.markdown("參考影片中的 SMC (Smart Money Concepts) 交易方式，本系統具備以下功能與邏輯。")

col1, col2 = st.columns([1, 2.5])

with col1:
    st.subheader("📝 影片交易邏輯還原")
    st.markdown("""
    1. **判斷結構 (Market Structure)**：尋找市場的波段高點 (Swing High) 與波段低點 (Swing Low)。
    2. **流動性清掃與結構轉變 (Market Shift)**：當價格強勢突破近期的波段高/低點，代表趨勢可能發生反轉。
    3. **進場點 (POI/Entry)**：在結構破壞後的下一根 K 線進場（模擬影片中的回調或破位確認）。
    4. **止損配置 (Stop Loss)**：多單止損設在近期波段低點下方；空單設在波段高點上方。
    5. **止盈配置 (Take Profit)**：嚴格執行風險回報比（左側面板可動態調整）。
    """)
    
    if latest_signal:
        st.success(f"**最新交易提示 ({latest_signal[0].strftime('%Y-%m-%d %H:%M')})**")
        if latest_signal[1] == 'Buy':
            st.markdown(f"""
            🟢 **方向**：做多 (Buy)  
            💵 **進場價**：{latest_signal[2]:.4f}  
            🛑 **止損價 (SL)**：{latest_signal[3]:.4f}  
            🎯 **止盈價 (TP)**：{latest_signal[4]:.4f}
            """)
        else:
            st.markdown(f"""
            🔴 **方向**：做空 (Sell)  
            💵 **進場價**：{latest_signal[2]:.4f}  
            🛑 **止損價 (SL)**：{latest_signal[3]:.4f}  
            🎯 **止盈價 (TP)**：{latest_signal[4]:.4f}
            """)
    else:
        st.info("目前無最新交易訊號，等待 Market Shift 發生...")

with col2:
    st.plotly_chart(fig, use_container_width=True)

st.warning("⚠️ **免責聲明**：本程式為教育與演算法展示用途，將主觀的 SMC 邏輯簡化為程式碼。實際交易請配合多時間框架與嚴格資金控管，不構成財務建議。")
