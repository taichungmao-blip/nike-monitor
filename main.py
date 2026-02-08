import yfinance as yf
import pandas as pd
import requests
import os
import matplotlib.pyplot as plt
import io
import json  # 修正：補上這個模組
from datetime import datetime, date
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
    
    # 基本資料
    try:
        info = stock.info
    except:
        info = {}
    
    # 嘗試抓取行事曆 (較準確的財報日)
    try:
        cal = stock.calendar
        if isinstance(cal, dict) and 'Earnings Date' in cal:
            earnings_date = cal['Earnings Date'][0]
        elif isinstance(cal, pd.DataFrame) and not cal.empty:
            earnings_date = cal.iloc[0, 0]
        else:
            earnings_date = None
    except:
        earnings_date = None

    return stock, hist, info, earnings_date

def calculate_correlation(hist_us, hist_tw):
    """計算近 30 天相關係數 (修復版)"""
    try:
        # 1. 取出收盤價
        us_close = hist_us['Close']
        tw_close = hist_tw['Close']

        # 2. 移除時區資訊 (關鍵修復：確保時區一致)
        us_close.index = us_close.index.tz_localize(None).normalize()
        tw_close.index = tw_close.index.tz_localize(None).normalize()

        # 3. 合併數據 (加入 sort=True 消除警告)
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
    # 防呆：確保不除以 0 或空值
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
    earnings_date_obj = data['earnings_date']

    # 處理財報日期顯示
    today = date.today()
    earnings_str = "未定"
    if earnings_date_obj:
        e_date = earnings_date_obj.date() if isinstance(earnings_date_obj, datetime) else earnings_date_obj
        earnings_str = str(e_date)
        if e_date < today: earnings_str += " (已發布)"
    elif nke.get('earningsTimestamp'):
        e_date = datetime.fromtimestamp(nke.get('earningsTimestamp')).date()
        earnings_str = str(e_date)
        if e_date < today: earnings_str += " (上季)"

    # 處理殖利率
    try:
        # 使用 dividendRate (現金股利) / currentPrice (股價)
        if tw.get('dividendRate') and data['tw_hist']['Close'].iloc[-1]:
            tw_yield = (tw['dividendRate'] / data['tw_hist']['Close'].iloc[-1]) * 100
        else:
            tw_yield = 0
    except:
        tw_yield = 0

    # 相關性文字
    if pd.isna(corr): corr_text = "數據不足"
    elif corr > 0.7: corr_text = "🔗 高度連動 (跟漲跟跌)"
    elif corr > 0.3: corr_text = "📈 中度正相關"
    elif corr < -0.3: corr_text = "📉 負相關 (背離)"
    else: corr_text = "💔 脫鉤/無明顯相關"

    # 獲取最新價格
    nke_price = data['nke_hist']['Close'].iloc[-1]
    nke_prev = data['nke_hist']['Close'].iloc[-2]
    nke_pct = (nke_price - nke_prev) / nke_prev * 100
    
    tw_price = data['tw_hist']['Close'].iloc[-1]
    tw_prev = data['tw_hist']['Close'].iloc[-2]
    tw_pct = (tw_price - tw_prev) / tw_prev * 100

    # 建立 Embed 訊息
    embed = {
        "title": "👟 豐泰 (9910) vs Nike (NKE) 每日深度追蹤",
        "description": f"策略觀點：Nike 走勢為豐泰領先指標。相關係數顯示兩者目前為 **{format_number(corr)}** ({corr_text})。",
        "color": 3447003,
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

    # 修正：使用 json.dumps 並以 multipart/form-data 傳送
    files = {
        'file': ('chart.png', chart_buffer, 'image/png')
    }
    
    # 這是 Discord Webhook 傳送圖片 + Embed 的標準寫法
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
    nke_s, nke_h, nke_i, nke_e = get_stock_data(TICKERS["US"])
    tw_s, tw_h, tw_i, tw_e = get_stock_data(TICKERS["TW"])
    
    corr = calculate_correlation(nke_h, tw_h)
    chart = generate_chart(nke_h, tw_h)
    
    data = {
        'nke_hist': nke_h, 'nke_info': nke_i, 'earnings_date': nke_e,
        'tw_hist': tw_h, 'tw_info': tw_i,
        'correlation': corr
    }
    
    send_discord_notification(data, chart)

if __name__ == "__main__":
    main()
