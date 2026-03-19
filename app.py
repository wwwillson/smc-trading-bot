import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import numpy as np
from datetime import timedelta

# --- 頁面設定 ---
st.set_page_config(page_title="4H Candle Box 交易策略回測", layout="wide")

# --- 策略說明 ---
st.title("📈 4H Candle Box 交易策略回測與視覺化")
with st.expander("📖 查看影片交易邏輯說明", expanded=False):
    st.markdown("""
    ### 策略核心邏輯 (基於影片內容)
    本程式嚴格遵守影片中的「只交易一根 4 小時 K 棒」邏輯：
    1. **決定方向 (Bias)**：每天取第一根 4 小時 K 棒（程式取每日開盤前 4 小時的數據組合）。如果收盤 > 開盤為**看漲**；收盤 < 開盤為**看跌**。
    2. **畫出區間 (The Box)**：將這根 4H K 棒的高低點標出，並以斐波那契劃分區間 (0%, 25%, 50%, 75%, 100%)。
    3. **尋找進場點 (Optimum Zone)**：
        * **做多 (Buy)**：當價格回撤到頂部往下的 25%~50% 區間 (Optimum Zone) 時進場做多。
        * **做空 (Sell)**：當價格反彈到底部往上的 25%~50% 區間 (Optimum Zone) 時進場做空。
    4. **止損與止盈 (SL & TP)**：
        * **止損 (SL)**：放在危險區 (Danger Zone)，即 75% 的位置。
        * **止盈 (TP)**：固定設定為 1:2 的盈虧比 (Risk-Reward Ratio)。
    """)

# --- 側邊欄設定 ---
st.sidebar.header("⚙️ 參數設定")
asset_dict = {
    "Bitcoin vs USD (BTC-USD)": "BTC-USD",
    "Gold vs USD (XAU-USD)": "GC=F",
    "Euro vs USD (EUR-USD)": "EURUSD=X"
}
selected_asset_name = st.sidebar.selectbox("選擇交易標的", list(asset_dict.keys()))
selected_asset = asset_dict[selected_asset_name]

days_to_fetch = st.sidebar.slider("選擇回測天數 (最高 360 天)", min_value=7, max_value=360, value=30, step=1)

# --- 獲取數據 ---
@st.cache_data(ttl=3600)
def load_data(ticker, days):
    # yfinance 1h 數據最高支援 730 天
    data = yf.download(ticker, period=f"{days}d", interval="1h")
    data.dropna(inplace=True)
    return data

data = load_data(selected_asset, days_to_fetch)

# --- 回測邏輯實作 ---
def run_backtest(df):
    trades =[]
    
    # 確保索引為時區感知或無時區
    df['Date_Only'] = df.index.date
    grouped = df.groupby('Date_Only')
    
    for date, group in grouped:
        if len(group) < 8: # 確保一天有足夠的 K 棒 (至少 4H基準 + 後續走勢)
            continue
            
        # 取前 4 根 1H K 棒組合成「4H基準 K 棒」
        ref_candle = group.iloc[:4]
        trading_session = group.iloc[4:]
        
        ref_open = ref_candle['Open'].iloc[0]
        ref_close = ref_candle['Close'].iloc[-1]
        ref_high = ref_candle['High'].max()
        ref_low = ref_candle['Low'].min()
        ref_range = ref_high - ref_low
        
        if ref_range == 0: continue
        
        # 判斷方向
        bias = "Bullish" if ref_close > ref_open else "Bearish"
        
        # 計算區間
        level_25 = ref_high - (ref_range * 0.25)
        level_50 = ref_high - (ref_range * 0.50)
        level_75 = ref_high - (ref_range * 0.75)
        
        entry_price = None
        sl_price = None
        tp_price = None
        trade_status = "Missed" # Missed, TP, SL
        exit_time = None
        pnl = 0
        
        for idx, row in trading_session.iterrows():
            # 還沒進場，尋找進場點
            if entry_price is None:
                if bias == "Bullish":
                    # 價格回撤進入 25% ~ 50% 區間
                    if row['Low'] <= level_25 and row['High'] >= level_50:
                        entry_price = level_25 # 假設觸碰到 25% 邊界就進場
                        sl_price = level_75
                        tp_price = entry_price + (entry_price - sl_price) * 2 # 1:2 RR
                elif bias == "Bearish":
                    # 價格反彈進入底部算起 25%~50% (即從上往下看的 50%~75%)
                    # 轉換為 Bearish 視角：Optimum 是從 Low 往上的 25%~50%
                    bear_level_25 = ref_low + (ref_range * 0.25)
                    bear_level_50 = ref_low + (ref_range * 0.50)
                    bear_level_75 = ref_low + (ref_range * 0.75)
                    
                    if row['High'] >= bear_level_25 and row['Low'] <= bear_level_50:
                        entry_price = bear_level_25
                        sl_price = bear_level_75
                        tp_price = entry_price - (sl_price - entry_price) * 2 # 1:2 RR
                        
            # 已經進場，檢查止盈或止損
            else:
                if bias == "Bullish":
                    if row['Low'] <= sl_price:
                        trade_status = "Hit SL"
                        exit_time = idx
                        pnl = -1 # 用 R 為單位 (損失 1R)
                        break
                    elif row['High'] >= tp_price:
                        trade_status = "Hit TP"
                        exit_time = idx
                        pnl = 2 # 獲利 2R
                        break
                elif bias == "Bearish":
                    if row['High'] >= sl_price:
                        trade_status = "Hit SL"
                        exit_time = idx
                        pnl = -1
                        break
                    elif row['Low'] <= tp_price:
                        trade_status = "Hit TP"
                        exit_time = idx
                        pnl = 2
                        break
                        
        if entry_price is not None:
            trades.append({
                "Date": date,
                "Bias": bias,
                "Entry Time": trading_session.index[0], # 簡化顯示
                "Exit Time": exit_time if exit_time else "End of Day",
                "Entry Price": round(float(entry_price), 2),
                "SL": round(float(sl_price), 2),
                "TP": round(float(tp_price), 2),
                "Outcome": trade_status if exit_time else "Closed at EOD",
                "PnL (R)": pnl
            })
            
    return pd.DataFrame(trades)

st.write("### ⏳ 正在計算回測結果...")
if not data.empty:
    trades_df = run_backtest(data)
    
    if not trades_df.empty:
        # --- 計算統計數據 ---
        total_trades = len(trades_df)
        wins = len(trades_df[trades_df['Outcome'] == 'Hit TP'])
        losses = len(trades_df[trades_df['Outcome'] == 'Hit SL'])
        total_pnl = trades_df['PnL (R)'].sum()
        win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
        
        # --- 顯示指標看板 ---
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("總交易次數", total_trades)
        col2.metric("勝率 (僅計 SL/TP)", f"{win_rate:.2f}%")
        col3.metric("總盈虧 (單位: R)", f"{total_pnl} R")
        col4.metric("目前市場趨勢", trades_df.iloc[-1]['Bias'])

        # --- 繪製最新交易圖表 ---
        st.write("---")
        st.write("### 📊 最新交易視覺化 (最後一筆進場交易)")
        
        last_trade_date = trades_df.iloc[-1]['Date']
        # 抓取那天的數據來畫圖
        chart_data = data[data.index.date == last_trade_date]
        
        fig = go.Figure(data=[go.Candlestick(x=chart_data.index,
                        open=chart_data['Open'],
                        high=chart_data['High'],
                        low=chart_data['Low'],
                        close=chart_data['Close'],
                        name="市場價格")])
        
        # 標示出進場、止損、止盈線
        entry = trades_df.iloc[-1]['Entry Price']
        sl = trades_df.iloc[-1]['SL']
        tp = trades_df.iloc[-1]['TP']
        bias = trades_df.iloc[-1]['Bias']
        outcome = trades_df.iloc[-1]['Outcome']
        
        fig.add_hline(y=entry, line_dash="dash", line_color="blue", annotation_text="Entry 進場點")
        fig.add_hline(y=sl, line_dash="dash", line_color="red", annotation_text="SL 止損")
        fig.add_hline(y=tp, line_dash="dash", line_color="green", annotation_text="TP 止盈")
        
        # 繪製前 4 小時的背景色區塊 (基準K棒)
        fig.add_vrect(x0=chart_data.index[0], x1=chart_data.index[3], 
                      fillcolor="gray", opacity=0.2, layer="below", line_width=0,
                      annotation_text="4H 基準 K 棒", annotation_position="top left")
        
        fig.update_layout(title=f"{selected_asset_name} - {last_trade_date} ({bias}) | 結果: {outcome}",
                          xaxis_title="時間",
                          yaxis_title="價格",
                          height=600,
                          template="plotly_dark")
        
        st.plotly_chart(fig, use_container_width=True)

        # --- 顯示交易表格 ---
        st.write("---")
        st.write("### 📝 完整交易紀錄明細")
        
        # 美化表格
        def color_outcome(val):
            color = '#4CAF50' if val == 'Hit TP' else '#F44336' if val == 'Hit SL' else 'white'
            return f'color: {color}; font-weight: bold'
            
        styled_df = trades_df.style.map(color_outcome, subset=['Outcome'])
        st.dataframe(styled_df, use_container_width=True)

    else:
        st.warning("在所選的時間範圍內沒有找到符合策略的交易訊號。")
else:
    st.error("無法獲取數據，請檢查網路連線或稍後再試。")
