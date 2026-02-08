import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime
import pytz

# 設定目標股票
# NKE: Nike (美股)
# 9910.TW: 豐泰 (台股)
TICKERS = {
    "US": "NKE",
    "TW": "9910.TW"
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_stock_data(ticker_symbol):
    """
    抓取股票數據：收盤價、基本面數據、下一次財報日
    """
    stock = yf.Ticker(ticker_symbol)
    
    # 取得歷史股價 (過去 60 天，用於計算相關性)
    hist = stock.history(period="60d")
    
    # 取得基本資料
    info = stock.info
    
    return stock, hist, info

def calculate_correlation(hist_us, hist_tw):
    """
    計算美股與台股近 30 天的收盤價相關係數
    """
    # 統一索引格式並合併數據
    df_us = hist_us['Close'].rename("NKE")
    df_tw = hist_tw['Close'].rename("9910")
    
    # 因為時區不同，我們用日期對齊 (inner join)
    df_combined = pd.concat([df_us, df_tw], axis=1).dropna()
    
    # 計算近 30 筆交易日的相關係數
    correlation = df_combined.tail(30).corr().iloc[0, 1]
    return correlation

def format_number(num):
    if num is None:
        return "N/A"
    return f"{num:.2f}"

def send_discord_notification(data):
    """
    發送 Discord 訊息
    """
    if not DISCORD_WEBHOOK_URL:
        print("Error: Discord Webhook URL not found.")
        return

    # 解構數據
    nke_info = data['nke_info']
    tw_info = data['tw_info']
    nke_hist = data['nke_hist']
    tw_hist = data['tw_hist']
    corr = data['correlation']

    # 計算漲跌幅
    nke_price = nke_hist['Close'].iloc[-1]
    nke_prev = nke_hist['Close'].iloc[-2]
    nke_chg = (nke_price - nke_prev) / nke_prev * 100

    tw_price = tw_hist['Close'].iloc[-1]
    tw_prev = tw_hist['Close'].iloc[-2]
    tw_chg = (tw_price - tw_prev) / tw_prev * 100

    # 判斷相關性強度
    corr_text = ""
    if corr > 0.7: corr_text = "高度正相關 (連動強)"
    elif corr > 0.3: corr_text = "中度正相關"
    else: corr_text = "低相關或脫鉤"

    # Nike 財報指引 (替代指標：分析師目標價與評級)
    target_price = nke_info.get('targetMeanPrice', 'N/A')
    recommendation = nke_info.get('recommendationKey', 'N/A').upper()
    
    # 下次財報日期 (嘗試抓取)
    try:
        next_earnings = datetime.fromtimestamp(nke_info.get('earningsTimestamp', 0)).strftime('%Y-%m-%d')
    except:
        next_earnings = "未定/未知"

    # 獲取當前台灣時間
    tw_tz = pytz.timezone('Asia/Taipei')
    now = datetime.now(tw_tz).strftime('%Y-%m-%d %H:%M')

    # 建構 Embed 訊息內容
    embed = {
        "title": f"👟 豐泰 (9910) vs Nike (NKE) 每日追蹤",
        "description": f"報告時間 (TW): {now}\n**策略觀點**: Nike 為豐泰最大客戶，請密切關注美股收盤後的連動效應。",
        "color": 3447003, # 藍色
        "fields": [
            {
                "name": f"🇺🇸 Nike (NKE) - 美股剛收盤",
                "value": f"股價: **${format_number(nke_price)}** ({nke_chg:+.2f}%)\n本益比 (PE): {format_number(nke_info.get('trailingPE'))}\n下次財報: {next_earnings}\n分析師評級: {recommendation}\n目標均價: ${target_price}",
                "inline": True
            },
            {
                "name": f"🇹🇼 豐泰 (9910) - 昨日收盤",
                "value": f"股價: **NT${format_number(tw_price)}** ({tw_chg:+.2f}%)\n本益比 (PE): {format_number(tw_info.get('trailingPE'))}\n殖利率: {format_number(tw_info.get('dividendYield', 0)*100)}%",
                "inline": True
            },
            {
                "name": "🔗 兩者連動性分析 (近30日)",
                "value": f"**相關係數: {format_number(corr)}**\n評價: `{corr_text}`\n(若 Nike 大漲且相關係數高，今日豐泰開高機率大)",
                "inline": False
            }
        ],
        "footer": {
            "text": "由 GitHub Actions 自動生成 | 價值投資分析助手"
        }
    }

    payload = {
        "embeds": [embed]
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if response.status_code == 204:
        print("Discord notification sent successfully.")
    else:
        print(f"Failed to send Discord notification: {response.status_code}")

def main():
    print("Starting stock analysis...")
    
    # 1. 獲取數據
    nke_stock, nke_hist, nke_info = get_stock_data(TICKERS["US"])
    tw_stock, tw_hist, tw_info = get_stock_data(TICKERS["TW"])
    
    # 2. 計算相關性
    correlation = calculate_correlation(nke_hist, tw_hist)
    
    # 3. 準備數據包
    data = {
        'nke_hist': nke_hist,
        'nke_info': nke_info,
        'tw_hist': tw_hist,
        'tw_info': tw_info,
        'correlation': correlation
    }
    
    # 4. 發送通知
    send_discord_notification(data)

if __name__ == "__main__":
    main()
