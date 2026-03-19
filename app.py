import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as plotly_go
from datetime import datetime, timedelta
import pytz

# 設定網頁標題與寬度
st.set_page_config(page_title="90-Min Scalping Strategy Backtester", layout="wide")

st.title("📈 90-Min 開盤首 5 分鐘突破回測策略回測系統")

# --- 交易邏輯說明 ---
with st.expander("📖 查看完整交易邏輯 (點擊展開)"):
    st.markdown("""
    ### 策略步驟 (依據影片邏輯)：
    1. **關鍵點位尋找 (流動性所在)**：標記出**前一天的高點 (PDH)** 與 **前一天的低點 (PDL)**。
    2. **開盤首根 K 棒**：美東時間 (EST) 早上 9:30 開盤，標記 `9:30 - 9:35 AM` 這根 5 分鐘 K 棒的**高點 (5m_H)** 與 **低點 (5m_L)**。
    3. **等待突破**：等待價格明確突破 `5m_H` (看多) 或跌破 `5m_L` (看空)。此策略僅在開盤前 90 分鐘內 (9:30 AM - 11:00 AM) 尋找進場機會。
    4. **等待回測 (Retest)**：
        * **做多 (Long)**：價格突破 `5m_H` 後，首次回踩至 `5m_H` 附近進場。
        * **做空 (Short)**：價格跌破 `5m_L` 後，首次回彈至 `5m_L` 附近進場。
    5. **止損與止盈設定**：
        * **止損 (Stop Loss)**：做多的止損設在 `5m_L` 以下；做空的止損設在 `5m_H` 以上。
        * **止盈 (Take Profit)**：做多目標看向前高 (`PDH`)；做空目標看向前低 (`PDL`)。若盈虧比不足 1:2，則強制設為 1:2 的固定盈虧比。
    """)

# --- 側邊欄設定 ---
st.sidebar.header("⚙️ 參數設定")
asset_choice = st.sidebar.selectbox(
    "選擇交易品種",[("Bitcoin vs USD", "BTC-USD"), ("Gold vs USD", "GC=F"), ("Euro vs USD", "EURUSD=X")],
    format_func=lambda x: x[0]
)
ticker_symbol = asset_choice[1]

st.sidebar.warning("⚠️ yfinance 免費 API 限制：5分鐘 K 棒最多只能獲取過去 60 天的數據。")
end_date = st.sidebar.date_input("結束日期", datetime.today())
start_date = st.sidebar.date_input("開始日期", end_date - timedelta(days=59))

if (end_date - start_date).days > 60:
    st.sidebar.error("日期範圍不能超過 60 天，請重新選擇。")
    st.stop()

# --- 抓取資料 ---
@st.cache_data(ttl=3600)
def load_data(ticker, start, end):
    try:
        # 抓取日線抓 PDH/PDL (多抓幾天以免遇到假日)
        daily_df = yf.download(ticker, start=start - timedelta(days=10), end=end + timedelta(days=1), interval="1d")
        # 抓取 5 分鐘線
        m5_df = yf.download(ticker, start=start, end=end + timedelta(days=1), interval="5m")
        
        if m5_df.empty or daily_df.empty:
            return None, None
            
        # 轉換時區為美東時間 (紐約)
        ny_tz = pytz.timezone('America/New_York')
        m5_df.index = m5_df.index.tz_convert(ny_tz) if m5_df.index.tzinfo else m5_df.index.tz_localize('UTC').tz_convert(ny_tz)
        daily_df.index = daily_df.index.tz_convert(ny_tz) if daily_df.index.tzinfo else daily_df.index.tz_localize('UTC').tz_convert(ny_tz)
        
        # 處理 multi-index columns (yfinance 新版的問題)
        if isinstance(m5_df.columns, pd.MultiIndex):
            m5_df.columns = m5_df.columns.droplevel(1)
        if isinstance(daily_df.columns, pd.MultiIndex):
            daily_df.columns = daily_df.columns.droplevel(1)

        return daily_df, m5_df
    except Exception as e:
        st.error(f"獲取資料時發生錯誤: {e}")
        return None, None

daily_data, m5_data = load_data(ticker_symbol, start_date, end_date)

if m5_data is None:
    st.error("無法獲取資料，請檢查日期範圍或網路連線。")
    st.stop()

# --- 策略回測運算 ---
trades =[]
unique_days = pd.Series(m5_data.index.date).unique()

for current_day in unique_days:
    # 找前一天的日線資料 (PDH, PDL)
    past_daily = daily_data[daily_data.index.date < current_day]
    if past_daily.empty:
        continue
    prev_day_data = past_daily.iloc[-1]
    pdh = prev_day_data['High']
    pdl = prev_day_data['Low']
    
    # 擷取當天的 5分K
    day_m5 = m5_data[m5_data.index.date == current_day]
    
    # 尋找 9:30 AM 的第一根 K 棒
    open_candle = day_m5[(day_m5.index.hour == 9) & (day_m5.index.minute == 30)]
    if open_candle.empty:
        continue
    
    first_5m_h = float(open_candle['High'].iloc[0])
    first_5m_l = float(open_candle['Low'].iloc[0])
    
    # 狀態追蹤
    trade_active = False
    broken_up = False
    broken_down = False
    
    entry_price = 0
    sl = 0
    tp = 0
    trade_type = ""
    entry_time = None
    
    # 迴圈檢查當天後續的 K 棒
    for idx, candle in day_m5.iterrows():
        # 如果超過 11:00 AM 且還沒進場，則今天放棄
        if idx.time() > pd.to_datetime('11:00').time() and not trade_active:
            break
            
        c_high = float(candle['High'])
        c_low = float(candle['Low'])
        
        # 1. 檢查突破
        if not trade_active:
            if c_high > first_5m_h and not broken_up:
                broken_up = True
            elif c_low < first_5m_l and not broken_down:
                broken_down = True
                
            # 2. 檢查回測並進場 (這裡簡化為碰到點位即進場)
            # 做多：已突破上緣，且價格回踩至 5m_h 附近或以下
            if broken_up and c_low <= first_5m_h and idx.time() > pd.to_datetime('09:35').time():
                trade_active = True
                trade_type = "Long"
                entry_price = first_5m_h
                sl = first_5m_l
                # 止盈邏輯：如果 PDH 太近或在進場點之下，改用 2R (1:2 盈虧比)
                risk = entry_price - sl
                tp = pdh if pdh > (entry_price + risk) else entry_price + (risk * 2)
                entry_time = idx
                
            # 做空：已跌破下緣，且價格反彈至 5m_l 附近或以上
            elif broken_down and c_high >= first_5m_l and idx.time() > pd.to_datetime('09:35').time():
                trade_active = True
                trade_type = "Short"
                entry_price = first_5m_l
                sl = first_5m_h
                risk = sl - entry_price
                tp = pdl if pdl < (entry_price - risk) else entry_price - (risk * 2)
                entry_time = idx
                
        # 3. 若已進場，檢查是否打到止盈或止損
        else:
            exit_time = idx
            result = ""
            pnl = 0
            
            if trade_type == "Long":
                if c_low <= sl:
                    result = "Loss (打到止損)"
                    pnl = -1 # 假設風險為 1R (虧損1單位)
                elif c_high >= tp:
                    result = "Win (打到止盈)"
                    pnl = (tp - entry_price) / (entry_price - sl) # 獲利 R 數
            elif trade_type == "Short":
                if c_high >= sl:
                    result = "Loss (打到止損)"
                    pnl = -1
                elif c_low <= tp:
                    result = "Win (打到止盈)"
                    pnl = (entry_price - tp) / (sl - entry_price)
                    
            if result:
                trades.append({
                    "Date": current_day,
                    "Type": trade_type,
                    "Entry Time": entry_time.strftime("%H:%M"),
                    "Entry Price": round(entry_price, 2),
                    "TP (止盈)": round(tp, 2),
                    "SL (止損)": round(sl, 2),
                    "Exit Time": exit_time.strftime("%H:%M"),
                    "Result": result,
                    "PnL (R)": round(pnl, 2),
                    "5m_H": round(first_5m_h, 2),
                    "5m_L": round(first_5m_l, 2),
                    "PDH": round(pdh, 2),
                    "PDL": round(pdl, 2)
                })
                break # 一天只做一單結束

trades_df = pd.DataFrame(trades)

# --- 顯示結果與表格 ---
st.subheader("📊 回測結果與交易紀錄")
if not trades_df.empty:
    total_trades = len(trades_df)
    winning_trades = len(trades_df[trades_df["PnL (R)"] > 0])
    win_rate = winning_trades / total_trades * 100
    total_pnl = trades_df["PnL (R)"].sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("總交易次數", total_trades)
    col2.metric("勝率", f"{win_rate:.1f}%")
    col3.metric("總盈虧加總 (Risk Multiples)", f"{total_pnl:.2f} R")
    
    # 顯示 DataFrame 表格
    st.dataframe(trades_df[["Date", "Type", "Entry Time", "Entry Price", "TP (止盈)", "SL (止損)", "Exit Time", "Result", "PnL (R)"]], use_container_width=True)
    
    # --- 互動式圖表繪製 ---
    st.subheader("📈 交易圖表可視化")
    selected_date = st.selectbox("選擇有發生交易的日期來檢視圖表：", trades_df["Date"])
    
    day_trade_info = trades_df[trades_df["Date"] == selected_date].iloc[0]
    plot_df = m5_data[m5_data.index.date == selected_date]
    
    fig = plotly_go.Figure(data=[plotly_go.Candlestick(x=plot_df.index,
                    open=plot_df['Open'], high=plot_df['High'],
                    low=plot_df['Low'], close=plot_df['Close'], name="5m K棒")])
    
    # 畫出 PDH, PDL, 5m_H, 5m_L, TP, SL 參考線
    fig.add_hline(y=day_trade_info["PDH"], line_dash="dot", line_color="blue", annotation_text="PDH (前高)")
    fig.add_hline(y=day_trade_info["PDL"], line_dash="dot", line_color="blue", annotation_text="PDL (前低)")
    fig.add_hline(y=day_trade_info["5m_H"], line_dash="dash", line_color="orange", annotation_text="9:30 5m High")
    fig.add_hline(y=day_trade_info["5m_L"], line_dash="dash", line_color="purple", annotation_text="9:30 5m Low")
    
    # 標示進場與止盈損
    fig.add_hline(y=day_trade_info["Entry Price"], line_color="white", annotation_text=f"Entry ({day_trade_info['Type']})")
    fig.add_hline(y=day_trade_info["TP (止盈)"], line_color="green", annotation_text="TP (止盈)")
    fig.add_hline(y=day_trade_info["SL (止損)"], line_color="red", annotation_text="SL (止損)")
    
    # 美化圖表
    fig.update_layout(
        title=f"{ticker_symbol} - {selected_date} 交易訊號圖表",
        yaxis_title="Price",
        xaxis_title="Time (EST)",
        xaxis_rangeslider_visible=False,
        height=600,
        template="plotly_dark"
    )
    
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("所選日期範圍內沒有符合策略邏輯的交易訊號。")
