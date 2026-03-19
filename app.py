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
period = st.sidebar.selectbox("載入歷史資料長度", ["5d", "1mo", "3mo", "1y", "2y"], index=1)

rr_ratio = st.sidebar.slider("設定盈虧比 (Risk:Reward)", min_value=1.0, max_value=5.0, value=2.0, step=0.1)

st.sidebar.markdown("""
### 🧠 策略說明
* **進場點:** 當價格回調至 EMA20~EMA50 區間，並出現吞噬型態時，於**「下一根K棒開盤」**進場。
* **回測機制:** 程式會往未來掃描，若先觸碰 TP 則判定 ✅止盈 (獲得+RR盈虧)；先觸碰 SL 則判定 ❌止損 (獲得 -1 R)。
""")

@st.cache_data
def load_data(ticker, period, interval):
    df = yf.download(ticker, period=period, interval=interval)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.dropna(inplace=True)
    return df

def simulate_trades(df, rr_ratio):
    # 計算 ATR 用於設定動態止損緩衝
    df['High-Low'] = df['High'] - df['Low']
    df['High-PrevClose'] = np.abs(df['High'] - df['Close'].shift(1))
    df['Low-PrevClose'] = np.abs(df['Low'] - df['Close'].shift(1))
    df['TR'] = df[['High-Low', 'High-PrevClose', 'Low-PrevClose']].max(axis=1)
    df['ATR'] = df['TR'].rolling(window=14).mean()

    # 均線模擬 Key Level
    df['EMA_20'] = df['Close'].rolling(window=20).mean()
    df['EMA_50'] = df['Close'].rolling(window=50).mean()

    # 吞噬型態判斷
    df['Bullish_Engulfing'] = (df['Close'] > df['Open']) & \
                              (df['Close'].shift(1) < df['Open'].shift(1)) & \
                              (df['Close'] > df['Open'].shift(1)) & \
                              (df['Open'] < df['Close'].shift(1))
    
    df['Bearish_Engulfing'] = (df['Close'] < df['Open']) & \
                              (df['Close'].shift(1) > df['Open'].shift(1)) & \
                              (df['Close'] < df['Open'].shift(1)) & \
                              (df['Open'] > df['Close'].shift(1))

    df['Signal'] = 0
    trade_records =[]

    # 必須留至少一根K棒來做未來進場 (所以迴圈跑到 len(df)-1)
    for i in range(1, len(df) - 1):
        
        # --- 多單邏輯 (Long) ---
        if df['EMA_20'].iloc[i] > df['EMA_50'].iloc[i]:
            if df['Bullish_Engulfing'].iloc[i] and (df['Low'].iloc[i] <= df['EMA_20'].iloc[i]):
                df.iat[i, df.columns.get_loc('Signal')] = 1
                
                # 在下一根 K 棒開盤進場
                entry_time = df.index[i+1]
                entry_price = float(df['Open'].iloc[i+1])
                sl = float(df['Low'].iloc[i] - (df['ATR'].iloc[i] * 0.5))
                tp = float(entry_price + ((entry_price - sl) * rr_ratio))
                
                # 模擬未來走勢直到打穿 SL 或是 TP
                outcome = "⏳ 持有中"
                exit_time = None
                exit_price = None
                pnl_r = 0.0  # R代表風報比單位 (虧損為-1, 獲利為+RR)
                
                for j in range(i+1, len(df)):
                    low_j = float(df['Low'].iloc[j])
                    high_j = float(df['High'].iloc[j])
                    
                    # 最保守估計：如果同一根 K棒 同時穿越止損與止盈，一律算作止損
                    if low_j <= sl and high_j >= tp:
                        outcome = "❌ 止損 (雙觸)"
                        exit_time = df.index[j]
                        exit_price = sl
                        pnl_r = -1.0
                        break
                    elif low_j <= sl:
                        outcome = "❌ 止損"
                        exit_time = df.index[j]
                        exit_price = sl
                        pnl_r = -1.0
                        break
                    elif high_j >= tp:
                        outcome = "✅ 止盈"
                        exit_time = df.index[j]
                        exit_price = tp
                        pnl_r = rr_ratio
                        break
                
                trade_records.append({
                    "訊號時間": df.index[i].strftime('%Y-%m-%d %H:%M'),
                    "方向": "LONG 🟢",
                    "進場時間": entry_time.strftime('%Y-%m-%d %H:%M'),
                    "進場價": round(entry_price, 5),
                    "止損 (SL)": round(sl, 5),
                    "止盈 (TP)": round(tp, 5),
                    "出場時間": exit_time.strftime('%Y-%m-%d %H:%M') if exit_time else "-",
                    "出場價": round(exit_price, 5) if exit_price else "-",
                    "結果": outcome,
                    "單筆獲利 (R)": round(pnl_r, 2)
                })

        # --- 空單邏輯 (Short) ---
        elif df['EMA_20'].iloc[i] < df['EMA_50'].iloc[i]:
            if df['Bearish_Engulfing'].iloc[i] and (df['High'].iloc[i] >= df['EMA_20'].iloc[i]):
                df.iat[i, df.columns.get_loc('Signal')] = -1
                
                entry_time = df.index[i+1]
                entry_price = float(df['Open'].iloc[i+1])
                sl = float(df['High'].iloc[i] + (df['ATR'].iloc[i] * 0.5))
                tp = float(entry_price - ((sl - entry_price) * rr_ratio))
                
                outcome = "⏳ 持有中"
                exit_time = None
                exit_price = None
                pnl_r = 0.0
                
                for j in range(i+1, len(df)):
                    low_j = float(df['Low'].iloc[j])
                    high_j = float(df['High'].iloc[j])
                    
                    if high_j >= sl and low_j <= tp:
                        outcome = "❌ 止損 (雙觸)"
                        exit_time = df.index[j]
                        exit_price = sl
                        pnl_r = -1.0
                        break
                    elif high_j >= sl:
                        outcome = "❌ 止損"
                        exit_time = df.index[j]
                        exit_price = sl
                        pnl_r = -1.0
                        break
                    elif low_j <= tp:
                        outcome = "✅ 止盈"
                        exit_time = df.index[j]
                        exit_price = tp
                        pnl_r = rr_ratio
                        break
                        
                trade_records.append({
                    "訊號時間": df.index[i].strftime('%Y-%m-%d %H:%M'),
                    "方向": "SHORT 🔴",
                    "進場時間": entry_time.strftime('%Y-%m-%d %H:%M'),
                    "進場價": round(entry_price, 5),
                    "止損 (SL)": round(sl, 5),
                    "止盈 (TP)": round(tp, 5),
                    "出場時間": exit_time.strftime('%Y-%m-%d %H:%M') if exit_time else "-",
                    "出場價": round(exit_price, 5) if exit_price else "-",
                    "結果": outcome,
                    "單筆獲利 (R)": round(pnl_r, 2)
                })

    trades_df = pd.DataFrame(trade_records)
    # 若有交易紀錄，則計算累計盈虧
    if not trades_df.empty:
        trades_df['累計盈虧 (R)'] = trades_df['單筆獲利 (R)'].cumsum()
        
    return df, trades_df

# UI 標題
st.title(f"📊 {selected_asset} SMC 策略分析與回測")
st.write(f"當前資料週期: **{period}**, K棒級別: **{timeframe}**, 設定盈虧比: **1 : {rr_ratio}**")

with st.spinner('載入資料與計算回測中...'):
    df, trades_df = load_data(ticker_symbol, period, timeframe), None
    if not df.empty:
        df, trades_df = simulate_trades(df, rr_ratio)

# 顯示回測績效儀表板
if trades_df is not None and not trades_df.empty:
    completed_trades = trades_df[trades_df["結果"].str.contains("止盈|止損")]
    total_trades = len(completed_trades)
    
    win_trades = len(completed_trades[completed_trades["結果"].str.contains("止盈")])
    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    total_pnl = trades_df['單筆獲利 (R)'].sum()

    st.markdown("### 🏆 策略回測績效統計 (已平倉)")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("完成交易總筆數", f"{total_trades} 筆")
    col2.metric("獲利筆數 (打到止盈)", f"{win_trades} 筆")
    col3.metric("歷史勝率", f"{win_rate:.1f} %")
    col4.metric("總累計盈虧 (R)", f"{total_pnl:.1f} R", help="R 代表單筆設定的風險資金單位。例如每次願虧 $100，+5R 代表淨賺 $500。")
    st.divider()

# 使用 Plotly 繪圖
fig = go.Figure()

# 畫 K 線圖
fig.add_trace(go.Candlestick(x=df.index,
                open=df['Open'], high=df['High'],
                low=df['Low'], close=df['Close'],
                name='價格'))

# 加入均線
fig.add_trace(go.Scatter(x=df.index, y=df['EMA_20'], line=dict(color='orange', width=1.5), name='EMA 20'))
fig.add_trace(go.Scatter(x=df.index, y=df['EMA_50'], line=dict(color='blue', width=1.5), name='EMA 50'))

# 標示圖表上的買賣箭頭
buy_signals = df[df['Signal'] == 1]
sell_signals = df[df['Signal'] == -1]
fig.add_trace(go.Scatter(x=buy_signals.index, y=buy_signals['Low'] - (buy_signals['ATR']*0.5), 
                         mode='markers', marker=dict(symbol='triangle-up', color='green', size=15), 
                         name='買入訊號'))
fig.add_trace(go.Scatter(x=sell_signals.index, y=sell_signals['High'] + (sell_signals['ATR']*0.5), 
                         mode='markers', marker=dict(symbol='triangle-down', color='red', size=15), 
                         name='賣出訊號'))

# 圖表外觀設定
fig.update_layout(
    height=600,
    template="plotly_dark",
    xaxis_rangeslider_visible=False,
    title="即時走勢圖 (箭頭標示出現訊號的 K 棒，實際進場點在下一根開盤)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig, use_container_width=True)

# 顯示最詳細的表格
st.subheader("📋 每一筆交易詳細追蹤與累計盈虧清單")
if trades_df is not None and not trades_df.empty:
    # 以顏色來高亮結果
    def color_outcome(val):
        if '✅' in str(val): return 'color: #00FF00'
        elif '❌' in str(val): return 'color: #FF4444'
        elif '⏳' in str(val): return 'color: #FFA500'
        return ''

    st.dataframe(trades_df.style.map(color_outcome, subset=['結果']), height=400, use_container_width=True)
else:
    st.info("目前選定期間內無符合條件之訊號。可以嘗試調長『歷史資料長度』或是切換較小的『時間級別』。")
