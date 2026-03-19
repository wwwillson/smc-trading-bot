import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
import pytz
from datetime import timedelta, datetime

# 設定網頁標題與寬度
st.set_page_config(page_title="5-Min Scalping Backtest", layout="wide")

st.title("📈 開盤 5 分鐘突破回踩策略 - 自動回測系統")

# --- 側邊欄設定 ---
st.sidebar.header("交易與回測設定")
instrument = st.sidebar.selectbox(
    "選擇交易品種",["BTC-USD (比特幣)", "GC=F (黃金期貨)", "EURUSD=X (歐元/美元)", "^GSPC (標普500)"]
)

# 對應 yfinance 的 Ticker
ticker_map = {
    "BTC-USD (比特幣)": "BTC-USD",
    "GC=F (黃金期貨)": "GC=F",
    "EURUSD=X (歐元/美元)": "EURUSD=X",
    "^GSPC (標普500)": "^GSPC"
}
ticker = ticker_map[instrument]

st.sidebar.markdown("---")
st.sidebar.markdown("⚠️ **注意：** 免費的 Yahoo Finance API 限制 `5分鐘K線` 最多只能下載 **最近 60 天** 的數據。")
backtest_days = st.sidebar.slider("選擇回測天數", min_value=1, max_value=60, value=30)
fixed_risk = st.sidebar.number_input("單筆交易固定風險 (美金)", min_value=10, max_value=1000, value=100)
min_rr = st.sidebar.slider("最低進場盈虧比 (R/R Ratio)", min_value=1.0, max_value=3.0, value=1.5, step=0.1)

# --- 獲取與處理數據 ---
@st.cache_data(ttl=3600)
def fetch_data(ticker, days):
    # 下載數據 (往前多抓5天以確保第一天有前日高低點)
    df = yf.download(ticker, period=f"{days + 5}d", interval="5m")
    if df.empty:
        return df
    
    # 轉換為美東時間 (EST)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df.index = df.index.tz_convert('America/New_York')
    
    # 移除 MultiIndex column (如果是新版 yfinance)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
        
    return df

with st.spinner('正在獲取歷史 5 分鐘數據並執行回測...'):
    df = fetch_data(ticker, backtest_days)

if df.empty:
    st.error("無法獲取數據，請稍後再試。")
    st.stop()

# --- 執行回測邏輯 ---
trades =[]
unique_days = pd.Series(df.index.date).unique()

for i in range(1, len(unique_days)):
    prev_day_str = unique_days[i-1].strftime('%Y-%m-%d')
    curr_day_str = unique_days[i].strftime('%Y-%m-%d')
    
    # 1. 取得前日 PDH / PDL (09:30 - 16:00)
    prev_day_data = df.loc[f"{prev_day_str} 09:30":f"{prev_day_str} 16:00"]
    if prev_day_data.empty: continue
    pdh = float(prev_day_data['High'].max())
    pdl = float(prev_day_data['Low'].min())
    
    # 2. 取得今日 5M High / 5M Low (09:30 - 09:35)
    first_5m_data = df.loc[f"{curr_day_str} 09:30":f"{curr_day_str} 09:35"]
    if first_5m_data.empty: continue
    m5_high = float(first_5m_data['High'].max())
    m5_low = float(first_5m_data['Low'].min())
    
    # 3. 進場掃描 (09:35 - 11:00)
    trading_session = df.loc[f"{curr_day_str} 09:35":f"{curr_day_str} 11:00"]
    
    signal = None
    entry_price = 0
    sl_price = 0
    tp_price = 0
    entry_time = None
    breakout_up = False
    breakout_down = False
    
    for idx, row in trading_session.iterrows():
        # 尋找突破
        if row['Close'] > m5_high:
            breakout_up = True
        elif row['Close'] < m5_low:
            breakout_down = True
            
        # 尋找回踩並檢查盈虧比
        if breakout_up and signal is None:
            if row['Low'] <= m5_high:
                risk = m5_high - m5_low
                reward = pdh - m5_high
                if risk > 0 and (reward / risk) >= min_rr:
                    signal, entry_price, sl_price, tp_price, entry_time = "LONG", m5_high, m5_low, pdh, idx
                    break
        elif breakout_down and signal is None:
            if row['High'] >= m5_low:
                risk = m5_high - m5_low
                reward = m5_low - pdl
                if risk > 0 and (reward / risk) >= min_rr:
                    signal, entry_price, sl_price, tp_price, entry_time = "SHORT", m5_low, m5_high, pdl, idx
                    break

    # 4. 出場掃描 (進場後 ~ 16:00 判斷打到 TP 或 SL)
    if signal:
        # 【修正錯誤】：將 Timestamp 轉換為字串，避免與後半段的字串混合切片時發生時區解析衝突
        entry_time_str = entry_time.strftime('%Y-%m-%d %H:%M:%S')
        exit_session = df.loc[entry_time_str:f"{curr_day_str} 16:00:00"]
        
        outcome = "未結算/平倉"
        exit_time = exit_session.index[-1]
        exit_price = float(exit_session.iloc[-1]['Close'])
        pnl_usd = 0
        
        for e_idx, e_row in exit_session.iterrows():
            if e_idx == entry_time: continue # 略過進場當下那一根K棒
            
            if signal == "LONG":
                if e_row['Low'] <= sl_price:
                    outcome, exit_price, exit_time = "🔴 止損 (Loss)", sl_price, e_idx
                    break
                elif e_row['High'] >= tp_price:
                    outcome, exit_price, exit_time = "🟢 止盈 (Win)", tp_price, e_idx
                    break
            elif signal == "SHORT":
                if e_row['High'] >= sl_price:
                    outcome, exit_price, exit_time = "🔴 止損 (Loss)", sl_price, e_idx
                    break
                elif e_row['Low'] <= tp_price:
                    outcome, exit_price, exit_time = "🟢 止盈 (Win)", tp_price, e_idx
                    break
        
        # 計算盈虧
        risk_dist = abs(entry_price - sl_price)
        reward_dist = abs(tp_price - entry_price)
        rr_ratio = reward_dist / risk_dist if risk_dist > 0 else 0
        
        if outcome == "🟢 止盈 (Win)":
            pnl_usd = fixed_risk * rr_ratio
        elif outcome == "🔴 止損 (Loss)":
            pnl_usd = -fixed_risk
        else: # 收盤未打到強制平倉
            if signal == "LONG":
                pnl_usd = fixed_risk * ((exit_price - entry_price) / risk_dist)
            else:
                pnl_usd = fixed_risk * ((entry_price - exit_price) / risk_dist)

        trades.append({
            "日期": curr_day_str,
            "方向": signal,
            "進場時間": entry_time.strftime('%H:%M'),
            "出場時間": exit_time.strftime('%H:%M'),
            "進場價": round(entry_price, 4),
            "止盈 (TP)": round(tp_price, 4),
            "止損 (SL)": round(sl_price, 4),
            "預期盈虧比": round(rr_ratio, 2),
            "結果": outcome,
            "實際盈虧 ($)": round(pnl_usd, 2)
        })

# --- 轉換成 DataFrame 與顯示結果 ---
st.markdown("### 📋 回測績效總覽")

if len(trades) > 0:
    trades_df = pd.DataFrame(trades)
    
    # 計算加總數據
    total_trades = len(trades_df)
    wins = len(trades_df[trades_df['結果'] == "🟢 止盈 (Win)"])
    losses = len(trades_df[trades_df['結果'] == "🔴 止損 (Loss)"])
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    total_pnl = trades_df['實際盈虧 ($)'].sum()
    
    # 顯示總覽區塊
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("總交易次數", total_trades)
    col2.metric("勝率", f"{win_rate:.1f} %")
    col3.metric("總止盈 / 總止損", f"{wins} / {losses}")
    col4.metric("💰 總淨利 (Total PnL)", f"$ {total_pnl:.2f}", 
                delta_color="normal" if total_pnl > 0 else "inverse")

    # 畫資金曲線 (Equity Curve)
    st.markdown("#### 📈 資金成長曲線 (Cumulative PnL)")
    trades_df['累計盈虧 ($)'] = trades_df['實際盈虧 ($)'].cumsum()
    fig_equity = px.area(trades_df, x='日期', y='累計盈虧 ($)', 
                         color_discrete_sequence=['#00FF00' if total_pnl > 0 else '#FF0000'])
    st.plotly_chart(fig_equity, use_container_width=True)

    # 顯示交易明細表
    st.markdown("#### 📝 詳細交易紀錄表")
    
    def color_outcome(val):
        color = 'lightgreen' if 'Win' in val else 'lightcoral' if 'Loss' in val else 'lightyellow'
        return f'background-color: {color}; color: black;'
    
    st.dataframe(trades_df.style.applymap(color_outcome, subset=['結果']), use_container_width=True)

    # --- 挑選單日看圖 ---
    st.markdown("---")
    st.markdown("### 🔍 單日走勢圖驗證 (點擊查看進場細節)")
    selected_trade_day = st.selectbox("選擇要檢視圖表的交易日", trades_df['日期'].tolist())
    
    plot_df = df.loc[f"{selected_trade_day} 09:00":f"{selected_trade_day} 16:00"]
    trade_info = trades_df[trades_df['日期'] == selected_trade_day].iloc[0]
    
    fig = go.Figure(data=[go.Candlestick(
        x=plot_df.index,
        open=plot_df['Open'], high=plot_df['High'],
        low=plot_df['Low'], close=plot_df['Close'],
        name="Candlesticks"
    )])

    # 加入輔助線
    fig.add_hline(y=trade_info['止盈 (TP)'], line_dash="dash", line_color="green", annotation_text="TP (止盈目標)")
    fig.add_hline(y=trade_info['止損 (SL)'], line_dash="dash", line_color="red", annotation_text="SL (止損保護)")
    fig.add_hline(y=trade_info['進場價'], line_dash="solid", line_color="blue", annotation_text="Entry (進場點)")

    # 標示進場點箭頭與框
    entry_datetime = pd.to_datetime(f"{selected_trade_day} {trade_info['進場時間']}").tz_localize('America/New_York')
    fig.add_annotation(
        x=entry_datetime, y=trade_info['進場價'],
        text="⬆ LONG" if trade_info['方向'] == "LONG" else "⬇ SHORT",
        showarrow=True, arrowhead=1, arrowcolor="blue",
        arrowsize=2, arrowwidth=2, ax=0, ay= 40 if trade_info['方向']=="LONG" else -40,
        bgcolor="blue", font=dict(color="white")
    )
    
    # 止盈止損顏色區塊
    fig.add_shape(type="rect",
        x0=entry_datetime, y0=trade_info['進場價'], x1=plot_df.index[-1], y1=trade_info['止盈 (TP)'],
        fillcolor="LightGreen", opacity=0.3, line_width=0, layer="below"
    )
    fig.add_shape(type="rect",
        x0=entry_datetime, y0=trade_info['進場價'], x1=plot_df.index[-1], y1=trade_info['止損 (SL)'],
        fillcolor="LightPink", opacity=0.3, line_width=0, layer="below"
    )

    fig.update_layout(height=600, xaxis_rangeslider_visible=False, template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)

else:
    st.warning(f"在過去的 {backtest_days} 天內，找不到符合（突破、回踩且盈虧比大於 {min_rr}）的交易機會。可以嘗試調低最低盈虧比，或增加回測天數。")
