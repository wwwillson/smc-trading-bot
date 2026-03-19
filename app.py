import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import pytz
import requests
import time

# 設定網頁佈局
st.set_page_config(page_title="NY Session Trading Strategy 1-Year Backtest", layout="wide")

st.title("📈 紐約盤開盤突破 (NY ORB) 1年回測系統")
st.markdown("""
- **1年期量化回測**：自動抓取過去 365 天 (約 10 萬根 5m K線) 數據，計算真實的 Risk Reward (R) 期望值。
- **防封鎖下載機制**：加入智能延遲與進度條，安全繞過 API 請求限制。
""")

# 側邊欄設定
st.sidebar.header("⚙️ 交易設定")
asset_dict = {
    "Bitcoin (BTC/USDT)": "BTCUSDT",
    "Ethereum (ETH/USDT)": "ETHUSDT",
    "Gold 黃金 (PAXG/USDT)": "PAXGUSDT",  # PAXG 1:1 錨定黃金
    "Euro 歐元 (EUR/USDT)": "EURUSDT"    # 歐元兌美元
}
selected_asset = st.sidebar.selectbox("選擇交易標的", list(asset_dict.keys()))
ticker = asset_dict[selected_asset]

# 讓使用者可以自由選擇要回測的天數，預設 365 天
backtest_days = st.sidebar.slider("選擇回測天數", min_value=30, max_value=365, value=365, step=30)

# --- 數據獲取函數 (利用迴圈抓取長達 1 年的資料) ---
@st.cache_data(ttl=3600)
def load_historical_data(symbol, days):
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    all_data =[]
    
    # 幣安免翻牆公開資料庫
    url = "https://data-api.binance.vision/api/v3/klines"
    
    # 計算大約需要發送幾次 API 請求 (每天288根K線，每次最多抓1000根)
    total_requests = int((days * 288) / 1000) + 1
    req_count = 0
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    while start_time < end_time:
        params = {
            "symbol": symbol,
            "interval": "5m",
            "limit": 1000,
            "startTime": start_time,
            "endTime": end_time
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if not data:
                    break
                all_data.extend(data)
                
                # 更新下一次抓取的時間戳記
                start_time = data[-1][0] + 1
                
                # 更新進度條
                req_count += 1
                progress = min(req_count / total_requests, 1.0)
                progress_bar.progress(progress)
                status_text.text(f"正在下載 {selected_asset} 歷史數據... 處理進度: {int(progress*100)}% (請稍候約10-20秒)")
                
                # 加入 0.1 秒延遲，防止被幣安 API 封鎖 (Rate Limit)
                time.sleep(0.1)
            else:
                break
        except Exception:
            break

    progress_bar.empty()
    status_text.empty()

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data).iloc[:, :6]
    df.columns =['datetime', 'Open', 'High', 'Low', 'Close', 'Volume']
    df['datetime'] = pd.to_datetime(df['datetime'], unit='ms')
    
    for col in['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = df[col].astype(float)
        
    df.set_index('datetime', inplace=True)
    df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
    df = df[~df.index.duplicated(keep='first')] # 移除重複值
    return df

# 載入歷史資料
df = load_historical_data(ticker, days=backtest_days)

if df.empty:
    st.error("❌ 獲取數據失敗，請確認網路連線。")
    st.stop()

# --- 建立 UI 兩個分頁 ---
tab1, tab2 = st.tabs([f"📊 即時交易圖表 (單日)", f"📋 近 {backtest_days} 天回測報告 (自動判定)"])

# ==========================================
# 分頁 1: 即時圖表邏輯
# ==========================================
with tab1:
    available_dates = sorted(list(set(df.index.date)), reverse=True)
    selected_date = st.selectbox("選擇查看日期", available_dates)

    df_day = df[df.index.date == selected_date]
    
    orb_start = datetime.combine(selected_date, datetime.strptime("09:30", "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
    orb_end = datetime.combine(selected_date, datetime.strptime("09:45", "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))

    df_orb = df_day[(df_day.index >= orb_start) & (df_day.index < orb_end)]
    
    orb_high = orb_low = None
    trade_signal = None
    signal_msg = "🕒 該日期的美東時間 09:30-09:45 尚未到達，還無法確立開盤區間。"

    if not df_orb.empty:
        if df_day.index[-1] >= orb_end:
            orb_high = float(df_orb['High'].max())
            orb_low = float(df_orb['Low'].min())
            df_post_orb = df_day[df_day.index >= orb_end]
            
            for i in range(len(df_post_orb)):
                current_close = float(df_post_orb['Close'].iloc[i])
                current_time = df_post_orb.index[i]
                
                if current_close > orb_high:
                    entry_price = current_close
                    sl_price = orb_low
                    tp_price = entry_price + ((entry_price - sl_price) * 1.5)
                    trade_signal = {'Type': 'BUY', 'Time': current_time, 'Entry': entry_price, 'SL': sl_price, 'TP': tp_price}
                    signal_msg = f"🟢 **出現做多訊號！** (突破高點) | 進場: `{entry_price:.4f}` | 止損: `{sl_price:.4f}` | 止盈: `{tp_price:.4f}`"
                    break
                elif current_close < orb_low:
                    entry_price = current_close
                    sl_price = orb_high
                    tp_price = entry_price - ((sl_price - entry_price) * 1.5)
                    trade_signal = {'Type': 'SELL', 'Time': current_time, 'Entry': entry_price, 'SL': sl_price, 'TP': tp_price}
                    signal_msg = f"🔴 **出現做空訊號！** (跌破低點) | 進場: `{entry_price:.4f}` | 止損: `{sl_price:.4f}` | 止盈: `{tp_price:.4f}`"
                    break
            if not trade_signal:
                signal_msg = "⏳ 今日開盤區間已確立，目前走勢尚在區間內，等待突破..."
        else:
             signal_msg = "🕒 目前正在紐約盤開盤區間內 (09:30-09:45)，請等待區間結束。"

    st.info(signal_msg)

    # 畫圖
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df_day.index, open=df_day['Open'], high=df_day['High'], low=df_day['Low'], close=df_day['Close'], name="5m K線"))

    if orb_high is not None and orb_low is not None:
        fig.add_hline(y=orb_high, line_dash="dash", line_color="orange", annotation_text="ORB High")
        fig.add_hline(y=orb_low, line_dash="dash", line_color="orange", annotation_text="ORB Low", annotation_position="bottom right")
        if trade_signal:
            fig.add_vline(x=trade_signal['Time'], line_dash="dot", line_color="white")
            fig.add_trace(go.Scatter(x=[trade_signal['Time']], y=[trade_signal['Entry']], mode='markers', marker=dict(color='yellow', size=10), name="Entry"))
            fig.add_hline(y=trade_signal['SL'], line_color="red", annotation_text="SL (止損)")
            fig.add_hline(y=trade_signal['TP'], line_color="green", annotation_text="TP (止盈 1.5R)")

    session_colors =[("00:00", "03:00", "rgba(255, 255, 0, 0.05)", "Asian"), ("03:00", "08:00", "rgba(0, 255, 255, 0.05)", "London"), ("08:00", "17:00", "rgba(255, 0, 255, 0.05)", "New York")]
    for start_t, end_t, color, name in session_colors:
        s_time = datetime.combine(selected_date, datetime.strptime(start_t, "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
        e_time = datetime.combine(selected_date, datetime.strptime(end_t, "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
        fig.add_vrect(x0=s_time, x1=e_time, fillcolor=color, opacity=1, layer="below", line_width=0, annotation_text=name, annotation_position="top left")

    fig.update_layout(height=700, xaxis_rangeslider_visible=False, template="plotly_dark", margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig, use_container_width=True)

# ==========================================
# 分頁 2: 大數據自動回測與盈虧判定表
# ==========================================
with tab2:
    st.subheader(f"📊 {selected_asset} 近 {backtest_days} 天回測報告")
    st.caption("回測邏輯：09:45確立區間後，突破做多跌破做空，止盈為 1.5 倍風險(R)，止損為區間另一端。若至美東時間 16:00 皆未觸碰止盈止損，則無條件收盤平倉。")
    
    results =[]
    dates = pd.Series(df.index.date).unique()
    
    # 執行回測迴圈
    for d in dates:
        df_d = df[df.index.date == d]
        
        orb_s = datetime.combine(d, datetime.strptime("09:30", "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
        orb_e = datetime.combine(d, datetime.strptime("09:45", "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
        ny_end = datetime.combine(d, datetime.strptime("16:00", "%H:%M").time()).replace(tzinfo=pytz.timezone('America/New_York'))
        
        df_orb = df_d[(df_d.index >= orb_s) & (df_d.index < orb_e)]
        if len(df_orb) < 3: # 無完整 ORB 數據則跳過 (可能當天週末沒開盤或缺漏)
            continue
            
        o_high = df_orb['High'].max()
        o_low = df_orb['Low'].min()
        
        # 避免最高點等於最低點導致除以0的錯誤
        if o_high == o_low:
            continue
            
        df_post = df_d[(df_d.index >= orb_e) & (df_d.index <= ny_end)]
        
        entered = False
        t_type = ""
        e_price = 0
        sl = tp = 0
        e_time = None
        
        # 1. 尋找進場點
        for idx, row in df_post.iterrows():
            if row['Close'] > o_high:
                entered, t_type, e_price, sl = True, "Long (做多)", row['Close'], o_low
                tp = e_price + (e_price - sl) * 1.5
                e_time = idx
                break
            elif row['Close'] < o_low:
                entered, t_type, e_price, sl = True, "Short (做空)", row['Close'], o_high
                tp = e_price - (sl - e_price) * 1.5
                e_time = idx
                break
                
        # 2. 判定出場 (止損/止盈/收盤)
        if entered:
            df_eval = df_post[df_post.index > e_time]
            outcome = "Pending (未平倉)"
            ex_price = 0
            ex_time = None
            pnl = 0
            
            for e_idx, e_row in df_eval.iterrows():
                if t_type == "Long (做多)":
                    if e_row['Low'] <= sl:  # 打到止損
                        outcome, ex_price, ex_time, pnl = "🔴 虧損 (打到止損)", sl, e_idx, -1.0
                        break
                    elif e_row['High'] >= tp: # 打到止盈
                        outcome, ex_price, ex_time, pnl = "🟢 獲利 (打到止盈)", tp, e_idx, 1.5
                        break
                else: # 做空邏輯
                    if e_row['High'] >= sl:
                        outcome, ex_price, ex_time, pnl = "🔴 虧損 (打到止損)", sl, e_idx, -1.0
                        break
                    elif e_row['Low'] <= tp:
                        outcome, ex_price, ex_time, pnl = "🟢 獲利 (打到止盈)", tp, e_idx, 1.5
                        break
            
            # 若到 16:00 仍未打到止盈止損，則收盤平倉
            if outcome == "Pending (未平倉)" and not df_eval.empty:
                ex_price = df_eval.iloc[-1]['Close']
                ex_time = df_eval.index[-1]
                if t_type == "Long (做多)":
                    pnl = (ex_price - e_price) / (e_price - sl)
                else:
                    pnl = (e_price - ex_price) / (sl - e_price)
                
                outcome = "🟢 獲利 (時間到平倉)" if pnl > 0 else "🔴 虧損 (時間到平倉)"

            results.append({
                "日期": str(d),
                "方向": t_type,
                "進場時間": e_time.strftime('%H:%M'),
                "進場價": round(e_price, 4),
                "止損價 (SL)": round(sl, 4),
                "止盈價 (TP)": round(tp, 4),
                "出場時間": ex_time.strftime('%H:%M') if ex_time else "-",
                "出場價": round(ex_price, 4) if ex_price else "-",
                "結果": outcome,
                "盈虧 (R)": round(pnl, 2)
            })

    # 輸出表格
    if results:
        df_results = pd.DataFrame(results)
        
        # 按照日期由新到舊排序
        df_results = df_results.sort_values(by="日期", ascending=False).reset_index(drop=True)
        
        # 計算統計數據
        total_trades = len(df_results)
        wins = len(df_results[df_results['盈虧 (R)'] > 0])
        win_rate = (wins / total_trades) * 100
        net_r = df_results['盈虧 (R)'].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("1年內總進場次數", f"{total_trades} 次")
        col2.metric("總勝率 (Win Rate)", f"{win_rate:.1f} %")
        col3.metric("總淨利期望值 (Total Risk Reward)", f"{net_r:.2f} R")
        
        # 標色顯示表格
        def color_outcome(val):
            if '獲利' in str(val): return 'color: #00FF00'
            elif '虧損' in str(val): return 'color: #FF4B4B'
            return ''
            
        st.dataframe(df_results.style.map(color_outcome, subset=['結果']), height=600, use_container_width=True)
    else:
        st.info(f"過去 {backtest_days} 天內無符合交易條件的紀錄。")
