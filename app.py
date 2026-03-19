import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import pytz
from datetime import timedelta

# 設定網頁標題與寬度
st.set_page_config(page_title="5-Min Scalping Strategy", layout="wide")

st.title("📈 開盤 5 分鐘突破回踩剝頭皮策略 (5-Min Scalping)")

# --- 側邊欄設定 ---
st.sidebar.header("交易設定")
instrument = st.sidebar.selectbox(
    "選擇交易品種",["BTC-USD (比特幣)", "GC=F (黃金期貨)", "EURUSD=X (歐元/美元)"]
)

# 對應 yfinance 的 Ticker
ticker_map = {
    "BTC-USD (比特幣)": "BTC-USD",
    "GC=F (黃金期貨)": "GC=F",
    "EURUSD=X (歐元/美元)": "EURUSD=X"
}
ticker = ticker_map[instrument]

# 選擇日期 (yfinance 5m 數據只能抓最近 60 天)
selected_date = st.sidebar.date_input("選擇交易日期 (請選平日)", pd.Timestamp.today() - timedelta(days=1))

# --- 顯示交易邏輯 ---
with st.expander("📖 查看完整交易邏輯 (點擊展開)", expanded=True):
    st.markdown("""
    ### 策略步驟 (基於美東時間 EST)：
    1. **標記前日流動性 (PDH & PDL)**：找出前一個交易日 9:30 AM - 4:00 PM 的最高點(PDH)與最低點(PDL)。這將是我們的止盈目標。
    2. **標記開盤 5 分鐘區間**：標記當日 9:30 AM - 9:35 AM K線的最高點 (5M_H) 與最低點 (5M_L)。
    3. **等待突破 (Breakout)**：價格必須明確突破 5M_H 或跌破 5M_L。
    4. **等待回踩 (Retest) 並進場**：
        * **做多**：突破 5M_H 後，價格回踩觸碰 5M_H 進場做多。
        * **做空**：跌破 5M_L 後，價格回踩觸碰 5M_L 進場做空。
    5. **設定止損止盈 (SL/TP)**：
        * **做多**：止損設於 5M_L 下方，止盈目標為 PDH (需滿足至少 1:2 盈虧比)。
        * **做空**：止損設於 5M_H 上方，止盈目標為 PDL (需滿足至少 1:2 盈虧比)。
    """)

# --- 獲取與處理數據 ---
@st.cache_data(ttl=3600)
def fetch_data(ticker, date):
    # 抓取選定日期前幾天的數據以計算前日高低點
    start_date = date - timedelta(days=3) # 往前抓確保有前一個交易日
    end_date = date + timedelta(days=2)
    
    df = yf.download(ticker, start=start_date, end=end_date, interval="5m")
    if df.empty:
        return df
    
    # 將索引轉換為美東時間 (EST)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df.index = df.index.tz_convert('America/New_York')
    
    # 移除 MultiIndex column (如果是新版 yfinance)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
        
    return df

df = fetch_data(ticker, selected_date)

if df.empty:
    st.error("無法獲取該日期的 5 分鐘數據，請選擇最近 60 天內的平日（不含週末）。")
else:
    # 定義時間範圍
    target_day_str = selected_date.strftime('%Y-%m-%d')
    
    # 找前一個交易日
    available_days = pd.Series(df.index.date).unique()
    available_days = [d for d in available_days if d < selected_date]
    if not available_days:
        st.warning("數據不足以計算前日高低點，請選擇更晚的日期。")
        st.stop()
    prev_day_str = available_days[-1].strftime('%Y-%m-%d')

    # 1. 計算 PDH / PDL (前日 09:30 - 16:00)
    prev_day_data = df.loc[f"{prev_day_str} 09:30":f"{prev_day_str} 16:00"]
    if not prev_day_data.empty:
        pdh = prev_day_data['High'].max()
        pdl = prev_day_data['Low'].min()
    else:
        pdh, pdl = None, None

    # 2. 計算 5M High / 5M Low (當日 09:30 - 09:35)
    first_5m_data = df.loc[f"{target_day_str} 09:30":f"{target_day_str} 09:35"]
    if not first_5m_data.empty:
        m5_high = first_5m_data['High'].max()
        m5_low = first_5m_data['Low'].min()
    else:
        m5_high, m5_low = None, None

    # 3. 交易邏輯判斷 (當日 09:35 - 11:00)
    trading_session = df.loc[f"{target_day_str} 09:35":f"{target_day_str} 11:00"]
    
    signal = None
    entry_price = None
    sl_price = None
    tp_price = None
    signal_time = None
    
    breakout_up = False
    breakout_down = False

    if m5_high and m5_low and pdh and pdl and not trading_session.empty:
        for idx, row in trading_session.iterrows():
            # 判斷突破
            if row['Close'] > m5_high:
                breakout_up = True
            elif row['Close'] < m5_low:
                breakout_down = True
            
            # 判斷回踩並進場 (這裡做機械化簡化：突破後價格觸碰原本的 5M 高低點)
            if breakout_up and signal is None:
                if row['Low'] <= m5_high: # 回踩 5M_H
                    signal = "LONG"
                    entry_price = m5_high
                    sl_price = m5_low # 簡化：止損放在 5M Low
                    tp_price = pdh
                    signal_time = idx
                    break # 進場後停止掃描
                    
            elif breakout_down and signal is None:
                if row['High'] >= m5_low: # 回踩 5M_L
                    signal = "SHORT"
                    entry_price = m5_low
                    sl_price = m5_high # 簡化：止損放在 5M High
                    tp_price = pdl
                    signal_time = idx
                    break

    # --- 繪製圖表 ---
    st.subheader(f"📊 {instrument} - {selected_date} 走勢圖")
    
    # 只顯示當日的圖表 (09:00 - 12:00 讓畫面聚焦)
    plot_df = df.loc[f"{target_day_str} 09:00":f"{target_day_str} 12:00"]
    
    if not plot_df.empty:
        fig = go.Figure(data=[go.Candlestick(
            x=plot_df.index,
            open=plot_df['Open'], high=plot_df['High'],
            low=plot_df['Low'], close=plot_df['Close'],
            name="Candlesticks"
        )])

        # 畫水平線 (PDH, PDL, 5M_H, 5M_L)
        if pdh: fig.add_hline(y=pdh, line_dash="dash", line_color="green", annotation_text="PDH (前日高點)")
        if pdl: fig.add_hline(y=pdl, line_dash="dash", line_color="red", annotation_text="PDL (前日低點)")
        if m5_high: fig.add_hline(y=m5_high, line_dash="solid", line_color="blue", annotation_text="9:30 5M High")
        if m5_low: fig.add_hline(y=m5_low, line_dash="solid", line_color="orange", annotation_text="9:30 5M Low")

        # 標示交易訊號
        if signal:
            st.success(f"🚨 **觸發交易訊號!** 方向: **{signal}** | 時間: {signal_time.strftime('%H:%M EST')}")
            col1, col2, col3 = st.columns(3)
            col1.metric("進場價 (Entry)", f"{entry_price:.5f}")
            col2.metric("止盈價 (TP)", f"{tp_price:.5f}")
            col3.metric("止損價 (SL)", f"{sl_price:.5f}")
            
            # 在圖上加上箭頭標示
            fig.add_annotation(
                x=signal_time, y=entry_price,
                text="⬆ LONG" if signal == "LONG" else "⬇ SHORT",
                showarrow=True, arrowhead=1, arrowcolor="green" if signal=="LONG" else "red",
                arrowsize=2, arrowwidth=2, ax=0, ay= 40 if signal=="LONG" else -40,
                bgcolor="green" if signal=="LONG" else "red", font=dict(color="white")
            )
            
            # 畫出止損與止盈區間區塊 (半透明)
            fig.add_shape(type="rect",
                x0=signal_time, y0=entry_price, x1=plot_df.index[-1], y1=tp_price,
                fillcolor="LightGreen", opacity=0.3, line_width=0, layer="below"
            )
            fig.add_shape(type="rect",
                x0=signal_time, y0=entry_price, x1=plot_df.index[-1], y1=sl_price,
                fillcolor="LightPink", opacity=0.3, line_width=0, layer="below"
            )
        else:
            st.info("🕒 今日 09:30 - 11:00 EST 期間無符合標準的突破回踩進場訊號。")

        # 更新圖表佈局
        fig.update_layout(
            height=600,
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
            margin=dict(l=20, r=20, t=20, b=20)
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("所選日期無當日開盤數據。")
