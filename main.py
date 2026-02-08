import yfinance as yf
import pandas as pd
import requests
import os
import matplotlib.pyplot as plt
import io
import json
from datetime import datetime, date, timedelta
import pytz

# --- 設定區 ---
TICKERS = {
    "US": "NKE",
    "TW": "9910.TW"
}
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_stock_data(ticker_symbol):
    """抓取數據：收盤價、基本資料、行事曆"""
    print(f"Fetching data for {ticker_symbol}...")
    stock = yf.Ticker(ticker_symbol)
    
    # 抓取半年 (6mo) 用於繪圖與計算
    hist = stock.history(period="6mo")
    
    # 基本資料 (使用 get 避免報錯)
    try:
        info = stock.info
    except:
        info = {}
    
    # 嘗試抓取行事曆 (較準確的財報日)
    earnings_date = None
    try:
        cal = stock.calendar
        if isinstance(cal, dict) and 'Earnings Date' in cal:
            earnings_date = cal['Earnings Date'][0]
        elif isinstance(cal, pd.DataFrame) and not cal.empty:
            earnings_date = cal.iloc[0, 0]
    except:
        pass

    return stock, hist, info, earnings_date

def calculate_correlation(hist_us, hist_tw):
    """計算近 30 天相關係數 (修復版：移除時區避免 nan)"""
    try:
        # 1. 取出收盤價
        us_close = hist_us['Close']
        tw_close = hist_tw['Close']

        # 2. 移除時區資訊 (關鍵修復)
        us_close.index = us_close.index.tz_localize(None).normalize()
        tw_close.index = tw_close.index.tz_localize(None).normalize()

        # 3. 合併數據 (sort=True 消除警告)
        df = pd.concat([us_close, tw_close], axis=1, keys=['US', 'TW'], sort=True).dropna()

        # 4. 取最近 30 筆交易日計算相關係數
        if len(df) < 10: return 0 
        corr = df.tail(30).corr().iloc[0, 1]
        return corr
    except Exception as e:
        print(f"Correlation Error: {e}")
        return 0

def generate_chart(hist_us, hist_tw):
    """繪製績效比較圖，回傳圖片 buffer"""
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # 移除時區以便繪圖對齊
    hist_us.index = hist_us.index.tz_localize(None)
    hist_tw.index = hist_tw.index.tz_localize(None)
    
    # 正規化數據 (以第一天為基準 0%)
    if len(hist_us) > 0 and len(hist_tw) > 0:
        us_norm = (hist_us['Close'] / hist_us['Close'].iloc[0] - 1) * 100
        tw_norm = (hist_tw['Close'] / hist_tw['Close'].iloc[0] - 1) * 100
        
        ax.plot(us_norm.index, us_norm, label='Nike (NKE)', color='#ff4d4d', linewidth=2)
        ax.plot(tw_norm.index, tw_norm, label='Feng Tay (9910)', color='#4da6ff', linewidth=2)
    
    ax.set_title("Nike vs Feng Tay: 6-Month Performance Comparison (%)", fontsize=14, color='white')
    ax.set_ylabel("Change (%)", color='white')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # 儲存圖片到記憶體
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close()
    return buf

def get_smart_earnings_date(earnings_date_obj, info_dict):
    """
    智能推算下次財報日：
    如果抓到的日期是過去的，則自動加 91 天(一季)直到它是未來日期。
    """
    today = date.today()
    raw_date = None

    # 1. 嘗試從 calendar 對象獲取
    if earnings_date_obj:
        raw_date = earnings_date_obj.date() if isinstance(earnings_date_obj, datetime) else earnings_date_obj
    
    # 2. 如果失敗，嘗試從 info 獲取 timestamp
    elif info_dict.get('earningsTimestamp'):
        raw_date = datetime.fromtimestamp(info_dict.get('earningsTimestamp')).date()

    if not raw_date:
        return "未定/未知"

    # 3. 判斷邏輯
    if raw_date >= today:
        return str(raw_date)  # 未來日期，直接回傳
    else:
        # 過去日期，開始推算
        estimated_next = raw_date + timedelta(days=91)
        # 如果加了一季還是過去，繼續加，直到變成未來
        while estimated_next < today:
            estimated_next += timedelta(days=91)
        return f"{estimated_next} (預估)"

def format_number(num, is_percent=False):
    if num is None: return "N/A"
    if is_percent: return f"{num * 100:.2f}"
    return f"{num:.2f}"

def send_discord_notification(data, chart_buffer):
    if not DISCORD_WEBHOOK_URL:
        print("Error: Discord Webhook URL not found.")
        return

    # 解構數據
    nke = data['nke_info']
    tw = data['tw_info']
    corr = data['correlation']

    # 1. 處理財報日期 (使用新邏輯)
    earnings_str = get_smart_earnings_date(data['earnings_date'], nke)

    # 2. 處理殖利率 (避免 548% 錯誤)
    try:
        if tw.get('dividendRate') and data['tw_hist']['Close'].iloc[-1]:
            tw_yield = (tw['dividendRate'] / data['tw_hist']['Close'].iloc[-1]) * 100
        elif tw.get('dividendYield'):
             tw_yield = tw['dividendYield'] * 100
        else:
            tw_yield = 0
    except:
        tw_yield = 0

    # 3. 相關性文字
    if pd.isna(corr): corr_text = "數據不足"
    elif corr > 0.7: corr_text = "🔗 高度連動 (跟漲跟跌)"
    elif corr > 0.3: corr_text = "📈 中度正相關"
    elif corr < -0.3: corr_text = "📉 負相關 (背離)"
    else: corr_text = "💔 脫鉤/無明顯相關"

    # 4. 獲取最新價格與漲跌幅
    nke_close = data['nke_hist']['Close']
    tw_close = data['tw_hist']['Close']
    
    nke_price = nke_close.iloc[-1]
    nke_pct = (nke_price - nke_close.iloc[-2]) / nke_close.iloc[-2] * 100
    
    tw_price = tw_close.iloc[-1]
    tw_pct = (tw_price - tw_close.iloc[-2]) / tw_close.iloc[-2] * 100

    # 5. 建立 Embed
    embed = {
        "title": "👟 豐泰 (9910) vs Nike (NKE) 每日深度追蹤",
        "description": f"策略觀點：Nike 走勢為豐泰領先指標。相關係數顯示兩者目前為 **{format_number(corr)}** ({corr_text})。",
        "color": 3447003, # 藍色
        "fields": [
            {
                "name": "🇺🇸 Nike (美股收盤)",
                "value": f"股價: **${format_number(nke_price)}** ({nke_pct:+.2f}%)\n本益比: {format_number(nke.get('trailingPE'))}\n下次財報: {earnings_str}\n分析師評級: {nke.get('recommendationKey', 'N/A').upper()}",
                "inline": True
            },
            {
                "name": "🇹🇼 豐泰 (昨日收盤)",
                "value": f"股價: **NT${format_number(tw_price)}** ({tw_pct:+.2f}%)\n本益比: {format_number(tw.get('trailingPE'))}\n預估殖利率: {format_number(tw_yield)}%",
                "inline": True
            }
        ],
        "image": {
            "url": "attachment://chart.png"
        },
        "footer": {
            "text": f"報告生成時間 (TW): {datetime.now(pytz.timezone('Asia/Taipei')).strftime('%Y-%m-%d %H:%M')}"
        }
    }

    # 6. 發送請求 (Multipart)
    files = {
        'file': ('chart.png', chart_buffer, 'image/png')
    }
    payload_json = json.dumps({"embeds": [embed]})
    
    response = requests.post(
        DISCORD_WEBHOOK_URL, 
        data={"payload_json": payload_json},
        files=files
    )

    if response.status_code in [200, 204]:
        print("Discord notification sent successfully.")
    else:
        print(f"Failed to send: {response.status_code}, {response.text}")

def main():
    print("Starting analysis...")
    
    # 獲取數據
    nke_s, nke_h, nke_i, nke_e = get_stock_data(TICKERS["US"])
    tw_s, tw_h, tw_i, tw_e = get_stock_data(TICKERS["TW"])
    
    # 計算與繪圖
    corr = calculate_correlation(nke_h, tw_h)
    chart = generate_chart(nke_h, tw_h)
    
    # 打包數據
    data = {
        'nke_hist': nke_h, 'nke_info': nke_i, 'earnings_date': nke_e,
        'tw_hist': tw_h, 'tw_info': tw_i,
        'correlation': corr
    }
    
    # 發送通知
    send_discord_notification(data, chart)

if __name__ == "__main__":
    main()
