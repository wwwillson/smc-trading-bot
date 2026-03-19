import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta

# 設定網頁版面
st.set_page_config(page_title="SMC 交易策略回測系統", layout="wide")

# --- UI 與側邊欄設定 ---
st.title("📈 SMC 交易策略回測與訊號提示 (含止盈止損紀錄)")

st.sidebar.header("⚙️ 參數設定")
asset_dict = {
    "Bitcoin (BTC/USD)": "BTC-USD",
    "Gold (XAU/USD)": "GC=F",
    "Euro (EUR/USD)": "EURUSD=X"
}
asset_choice = st.sidebar.selectbox("選擇交易商品", list(asset_dict.keys()))
ticker = asset_dict[asset_choice]

# 修改預設時間，為了看半年資料預設推薦 1h 或 1d
tf_choice = st.sidebar.selectbox("選擇時間級別", ["1h", "1d", "15m"], help="注意: Yahoo Finance 15m 資料最多僅支援近 60 天。看半年請選 1h 或 1d。")
days_to_fetch = st.sidebar.slider("載入歷史天數", 30, 180, 180) # 預設半年 (180天)

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

# --- SMC 回測核心演算法 (加入 TP/SL 結果判定) ---
def backtest_strategy(df):
    fvgs = []
    trades =[]
    
    # 步驟 4 & 5: 尋找 FVG 作為 Key Level
    for i in range(1, len(df) - 2):
        c1 = df.iloc[i-1]
        c2 = df.iloc[i]
        c3 = df.iloc[i+1]
        
        if c3['High'] < c1['Low'] and c2['Close'] < c2['Open']: # Bearish FVG
            fvgs.append({
                'type': 'Bearish', 'start_index': df.index[i+1], 'start_idx_num': i+1,
                'top': c1['Low'], 'bottom': c3['High'], 'sl': c1['High'], 'active': True
            })
        elif c3['Low'] > c1['High'] and c2['Close'] > c2['Open']: # Bullish FVG
            fvgs.append({
                'type': 'Bullish', 'start_index': df.index[i+1], 'start_idx_num': i+1,
                'top': c3['Low'], 'bottom': c1['High'], 'sl': c1['Low'], 'active': True
            })
            
    # 步驟 6: 確認進場與向後掃描出場結果
    for i in range(3, len(df)):
        current_time = df.index[i]
        row = df.iloc[i]
        
        for fvg in fvgs:
            if not fvg['active'] or i <= fvg['start_idx_num']:
                continue
                
            # 觸發做空
            if fvg['type'] == 'Bearish' and row['High'] >= fvg['bottom']:
                fvg['active'] = False
                entry_price = fvg['bottom']
                sl = fvg['sl']
                tp = entry_price - (sl - entry_price) * 2 # 1:2 RR
                trades.append({'entry_time': current_time, 'entry_idx': i, 'type': 'Sell', 'entry': entry_price, 'sl': sl, 'tp': tp, 'status': '⏳ 進行中', 'exit_time': None})
                
            # 觸發做多
            elif fvg['type'] == 'Bullish' and row['Low'] <= fvg['top']:
                fvg['active'] = False
                entry_price = fvg['top']
                sl = fvg['sl']
                tp = entry_price + (entry_price - sl) * 2 # 1:2 RR
                trades.append({'entry_time': current_time, 'entry_idx': i, 'type': 'Buy', 'entry': entry_price, 'sl': sl, 'tp': tp, 'status': '⏳ 進行中', 'exit_time': None})

    # 模擬未來走勢，判斷是否打到 TP 或 SL
    for trade in trades:
        for j in range(trade['entry_idx'] + 1, len(df)):
            future_row = df.iloc[j]
            if trade['type'] == 'Buy':
                if future_row['Low'] <= trade['sl']:
                    trade['status'] = '❌ 觸發止損 (Hit SL)'
                    trade['exit_time'] = df.index[j]
                    break
                elif future_row['High'] >= trade['tp']:
                    trade['status'] = '✅ 觸發止盈 (Hit TP)'
                    trade['exit_time'] = df.index[j]
                    break
            elif trade['type'] == 'Sell':
                if future_row['High'] >= trade['sl']:
                    trade['status'] = '❌ 觸發止損 (Hit SL)'
                    trade['exit_time'] = df.index[j]
                    break
                elif future_row['Low'] <= trade['tp']:
                    trade['status'] = '✅ 觸發止盈 (Hit TP)'
                    trade['exit_time'] = df.index[j]
                    break

    return fvgs, trades

fvgs, trades = backtest_strategy(df)

# --- 網頁佈局設計 ---
col1, col2 = st.columns([1, 3])

with col1:
    st.markdown("### 📊 交易統計概況")
    total_trades = len(trades)
    wins = len([t for t in trades if 'Hit TP' in t['status']])
    losses = len([t for t in trades if 'Hit SL' in t['status']])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    
    st.metric(label="總交易次數", value=total_trades)
    st.metric(label="勝率 (勝/敗)", value=f"{win_rate:.1f}%", delta=f"{wins} 勝 / {losses} 敗")
    st.markdown("*(預設盈虧比均為 1:2)*")
    st.markdown("---")
    
    st.markdown("### 🚨 最新一筆交易狀態")
    if trades:
        latest = trades[-1]
        signal_color = "🟢 多單 (Buy)" if latest['type'] == 'Buy' else "🔴 空單 (Sell)"
        st.success(f"**方向**: {signal_color}")
        st.info(f"**進場價位**: {latest['entry']:.4f}")
        st.error(f"**止損位 (SL)**: {latest['sl']:.4f}")
        st.warning(f"**止盈位 (TP)**: {latest['tp']:.4f}")
        st.write(f"**目前狀態**: {latest['status']}")
    else:
        st.write("區間內尚無訊號。")

with col2:
    # --- 繪製 Plotly 圖表 ---
    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線'
    )])

    # 標示未失效的近10個 FVG
    active_fvgs =[fvg for fvg in fvgs if fvg['active']][-10:]
    for fvg in active_fvgs:
        color = "rgba(255, 0, 0, 0.2)" if fvg['type'] == 'Bearish' else "rgba(0, 255, 0, 0.2)"
        fig.add_shape(type="rect", x0=fvg['start_index'], y0=fvg['bottom'], x1=df.index[-1], y1=fvg['top'], fillcolor=color, line_width=0, layer="below")

    # 在圖表上標示進場點
    buy_times = [t['entry_time'] for t in trades if t['type'] == 'Buy']
    buy_prices = [t['entry'] for t in trades if t['type'] == 'Buy']
    sell_times = [t['entry_time'] for t in trades if t['type'] == 'Sell']
    sell_prices = [t['entry'] for t in trades if t['type'] == 'Sell']

    fig.add_trace(go.Scatter(x=buy_times, y=buy_prices, mode='markers', marker=dict(symbol='triangle-up', size=12, color='lime', line=dict(width=1, color='white')), name='做多進場'))
    fig.add_trace(go.Scatter(x=sell_times, y=sell_prices, mode='markers', marker=dict(symbol='triangle-down', size=12, color='red', line=dict(width=1, color='white')), name='做空進場'))

    # 畫出最新一筆交易的 SL 與 TP 架位線
    if trades:
        latest = trades[-1]
        fig.add_hline(y=latest['sl'], line_dash="dash", line_color="red", annotation_text=f"SL: {latest['sl']:.4f}")
        fig.add_hline(y=latest['entry'], line_dash="solid", line_color="white", opacity=0.5, annotation_text="Entry")
        fig.add_hline(y=latest['tp'], line_dash="dash", line_color="lime", annotation_text=f"TP: {latest['tp']:.4f}")

    fig.update_layout(xaxis_rangeslider_visible=False, height=500, template="plotly_dark", margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig, use_container_width=True)

# --- 下方顯示歷史回測完整表格 ---
st.markdown("### 📝 半年進場點與止盈止損紀錄表")
if trades:
    # 將 trades 字典轉換為 DataFrame 方便顯示
    df_trades = pd.DataFrame(trades)
    df_trades = df_trades[['entry_time', 'type', 'entry', 'tp', 'sl', 'status', 'exit_time']]
    df_trades.columns =['進場時間', '多空方向', '進場價位', '止盈點 (TP)', '止損點 (SL)', '最終結果', '出場時間']
    
    # 將 DataFrame 顯示在 Streamlit
    st.dataframe(
        df_trades.style.format({
            "進場價位": "{:.4f}",
            "止盈點 (TP)": "{:.4f}",
            "止損點 (SL)": "{:.4f}"
        }),
        use_container_width=True,
        height=300
    )
else:
    st.info("此區間內無觸發任何交易訊號。")
