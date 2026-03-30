import requests
import json
import os
import csv
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

# =========================
# 환경 변수 및 설정
# =========================

load_dotenv()

APP_KEY = os.getenv("APP_KEY")
APP_SECRET = os.getenv("APP_SECRET")
ACCOUNT = os.getenv("ACCOUNT")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

BASE_URL = "https://openapi.koreainvestment.com:9443"

TOKEN_FILE        = "token.json"
TRADES_FILE       = "trades.csv"
WEEKLY_FILE       = "weekly_info.json" # 💡 기존 파일들 지우고 하나로 통합
LOG_NAME          = "HM_v1_4.log"      # 버전 업

# =========================
# 로그 설정
# =========================

logging.basicConfig(
    filename=LOG_NAME,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8"
)

# =========================
# 디스코드 알림
# =========================

def send_discord(msg):
    try:
        res = requests.post(
            DISCORD_WEBHOOK,
            json={"content": msg},
            timeout=10
        )
        if res.status_code not in [200, 204]:
            logging.error(f"Discord 전송 실패: {res.status_code} - {res.text}")
    except Exception as e:
        logging.error(f"Discord Error: {e}")

# =========================
# 토큰 및 휴장일 관리
# =========================

def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    expire = datetime.strptime(data["expire"], "%Y-%m-%d %H:%M:%S")
    if datetime.now() >= expire:
        return None
    return data["token"]

def save_token(token):
    expire = datetime.now().replace(hour=23, minute=59, second=0)
    with open(TOKEN_FILE, "w") as f:
        json.dump({
            "token": token,
            "expire": expire.strftime("%Y-%m-%d %H:%M:%S")
        }, f)

def get_token():
    token = load_token()
    if token:
        return token
    url = f"{BASE_URL}/oauth2/tokenP"
    data = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    try:
        res = requests.post(url, json=data, timeout=10)
        if res.status_code != 200:
            logging.error(res.text)
            return None
        token = res.json()["access_token"]
        save_token(token)
        logging.info("Token reissued")
        return token
    except Exception as e:
        logging.error(f"Token Error: {e}")
        return None

def is_market_holiday():
    now = datetime.now()
    today_str = now.strftime("%Y%m%d")
    
    if now.weekday() >= 5: 
        return True

    token = get_token()
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/chk-holiday"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "CTCA0903R",
        "custtype": "P"
    }
    params = {
        "BASS_DT": today_str,
        "CTX_AREA_NK100": "",
        "CTX_AREA_FK100": ""
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        for item in data.get("output", []):
            if item["bass_dt"] == today_str:
                return item["opnd_yn"] == "N" 
    except Exception as e:
        logging.error(f"Holiday Check Error: {e}")
        
    return False

# =========================
# 📅 주간 데이터 통합 관리 시스템
# =========================

def get_weekly_data():
    """주간 데이터를 불러오고, 주가 바뀌었으면 자동으로 초기화합니다."""
    now = datetime.now()
    this_monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    
    default_data = {
        "week_monday": this_monday,
        "base_asset": 0,
        "start_balance": 0.0,
        "report_sent": False,
        "last_reset_date": ""
    }

    if not os.path.exists(WEEKLY_FILE):
        return default_data

    try:
        with open(WEEKLY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 저장된 날짜가 이번 주 월요일이 아니라면 초기화
        if data.get("week_monday") != this_monday:
            return default_data
            
        return data
    except Exception as e:
        logging.error(f"Weekly Data Load Error: {e}")
        return default_data

def update_weekly_data(key, value):
    """특정 주간 데이터 하나를 업데이트하고 저장합니다."""
    data = get_weekly_data()
    data[key] = value
    try:
        with open(WEEKLY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Weekly Data Save Error: {e}")

def get_saved_start_balance(current_total):
    """주간 시작 금액(start_balance)을 관리하고, 갱신 시 알림을 보냅니다."""
    data = get_weekly_data()
    now = datetime.now()
    
    is_after_monday_open = (now.weekday() == 0 and now.hour >= 9) or (now.weekday() > 0)
    
    # 이번 주 start_balance가 비어있고, 월요일 장 개장 이후라면 갱신 및 알림 전송
    if data.get("start_balance", 0.0) == 0.0 and is_after_monday_open:
        update_weekly_data("start_balance", float(current_total))
        update_weekly_data("last_reset_date", str(now.date()))
        
        # 💡 주간 초기화 완료 알림 발송
        send_discord(f"📅 **[주간 자산 기준점 세팅 완료]**\n이번 주 수익률 기준 자산이 `{int(current_total):,}원`으로 세팅되었습니다.")
        logging.info(f"Weekly balance initialized: {current_total}")
        
        return float(current_total)
        
    return float(data.get("start_balance", current_total))

# =========================
# 잔고 조회 및 알림
# =========================

def check_balance():
    now = datetime.now()
    token = get_token()

    if token is None:
        logging.error("Token not available")
        return

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "TTTC8434R"
    }
    params = {
        "CANO": ACCOUNT[:8],         
        "ACNT_PRDT_CD": ACCOUNT[8:], 
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            logging.error(f"HTTP Error: {res.status_code} {res.text}")
            return

        data = res.json()
        if "output2" not in data or len(data["output2"]) == 0:
            logging.error(f"Balance API error: {data}")
            return

        total = float(data["output2"][0]["tot_evlu_amt"])
        
        # 💡 주간 자산 가져오기 (비어있으면 여기서 초기화 & 알림 전송됨)
        start_balance = get_saved_start_balance(total)

        if start_balance > 0:
            profit_amt = total - start_balance
            profit_rate = round((profit_amt / start_balance) * 100, 2)
        else:
            profit_amt = 0.0
            profit_rate = 0.0

        msg = f"📊 **[실시간 자산 리포트 - {now.strftime('%H:%M')}]**\n"
        
        holdings = data.get("output1", [])
        active_holdings_found = False

        if holdings:
            for stock in holdings:
                qty = int(stock.get("hldg_qty", "0"))
                if qty <= 0:
                    continue
                
                active_holdings_found = True
                name = stock.get("prdt_name", "Unknown")
                rate = float(stock.get("evlu_pfls_rt", "0"))
                amt = float(stock.get("evlu_amt", "0"))
                
                if rate > 0:
                    icon = "📈"
                elif rate < 0:
                    icon = "📉"
                else:
                    icon = "➖"
                
                msg += f"> {icon} **{name}**: {rate:+.2f}% (`{int(amt):,}원`)\n"
                
        if not active_holdings_found:
            logging.info("보유 종목 없음 - 자산 리포트 알림 생략")
            return

        msg += "\n"
        msg += f"💰 **현재 총 자산:** `{int(total):,}원`\n"
        msg += f"📅 **주간 성적:** `{int(profit_amt):+,}원` (`{profit_rate:+.2f}%`)\n"
        msg += "*(기준: 이번주 월요일 09:00)*"
        
        send_discord(msg)
        logging.info("Balance check success")

    except Exception as e:
        logging.error(f"Balance Error: {e}")

# =========================
# 장 상태 알림
# =========================

def market_open():
    send_discord("🔔 **한국 주식시장 개장 (09:00)**")

def market_close():
    send_discord("🔔 **한국 주식시장 마감 (15:30)**")

# =========================
# 일일 결산 리포트 (15:30 발송용)
# =========================

def send_daily_report():
    if not os.path.exists(TRADES_FILE):
        logging.info("trades.csv 없음 - 일일 리포트 스킵")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    trades_today = []

    try:
        # csv 파일에서 오늘 날짜의 거래 내역만 추출
        with open(TRADES_FILE, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["날짜"].startswith(today_str):
                    trades_today.append(row)
    except Exception as e:
        logging.error(f"CSV Read Error: {e}")
        return

    msg = f"🌅 **[오늘의 매매 결산 - {today_str}]**\n\n"

    # 오늘 거래가 아예 없었던 경우
    if not trades_today:
        msg += "오늘은 거래가 발생하지 않았습니다. 푹 쉬세요! ☕"
        send_discord(msg)
        return

    # 오늘 거래가 있었던 경우 계산
    total_profit = sum(float(t["수익금(원)"]) for t in trades_today)
    win_trades = [t for t in trades_today if float(t["수익금(원)"]) > 0]
    lose_trades = [t for t in trades_today if float(t["수익금(원)"]) <= 0]

    msg += f"**총 매매 횟수:** {len(trades_today)}회 ({len(win_trades)}승 {len(lose_trades)}패)\n"
    msg += f"**오늘 총 수익금:** `{total_profit:+,.0f}원`\n\n"
    msg += "📊 **상세 거래 내역**\n"

    for t in trades_today:
        emoji = "🟢" if float(t["수익금(원)"]) > 0 else "🔴"
        msg += f"> {emoji} {t['종목명']} {float(t['수익률(%)']):+.2f}% (`{float(t['수익금(원)']):+,.0f}원`)\n"

    send_discord(msg)
    logging.info("Daily report sent")


# =========================
# 주간 리포트
# =========================

def is_weekly_report_sent():
    data = get_weekly_data()
    return data.get("report_sent", False)

def mark_weekly_report_sent():
    update_weekly_data("report_sent", True)

def send_weekly_report():
    if not os.path.exists(TRADES_FILE):
        logging.info("trades.csv 없음 - 주간 리포트 스킵")
        return

    now = datetime.now()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    this_monday = today_midnight - timedelta(days=today_midnight.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    trades = []
    try:
        with open(TRADES_FILE, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trade_date = datetime.strptime(row["날짜"], "%Y-%m-%d %H:%M:%S")
                if last_monday <= trade_date <= last_sunday.replace(hour=23, minute=59, second=59):
                    trades.append(row)
    except Exception as e:
        logging.error(f"CSV Read Error: {e}")
        return

    if not trades:
        msg = (
            f"📋 **주간 매매 리포트**\n"
            f"> ({last_monday.strftime('%m/%d')} ~ {last_sunday.strftime('%m/%d')})\n\n"
            f"지난 주 매매 내역이 없습니다."
        )
        send_discord(msg)
        mark_weekly_report_sent()
        return

    total_profit   = sum(float(t["수익금(원)"]) for t in trades)
    total_trades   = len(trades)
    win_trades     = [t for t in trades if float(t["수익금(원)"]) > 0]
    lose_trades    = [t for t in trades if float(t["수익금(원)"]) <= 0]
    win_rate       = len(win_trades) / total_trades * 100
    avg_profit     = total_profit / total_trades

    best  = max(trades, key=lambda t: float(t["수익금(원)"]))
    worst = min(trades, key=lambda t: float(t["수익금(원)"]))

    msg  = f"📋 **주간 매매 리포트**\n"
    msg += f"({last_monday.strftime('%m/%d')} ~ {last_sunday.strftime('%m/%d')})\n\n"
    msg += f"**총 매매 횟수:** {total_trades}회\n"
    msg += f"**승률:** {win_rate:.1f}% ({len(win_trades)}승 {len(lose_trades)}패)\n"
    msg += f"**총 수익금:** `{total_profit:+,.0f}원`\n"
    msg += f"**평균 수익금:** `{avg_profit:+,.0f}원`\n\n"
    msg += f"🏆 **최고 매매**\n"
    msg += f"> {best['종목명']} {float(best['수익률(%)']):.2f}% (`{float(best['수익금(원)']):+,.0f}원`)\n\n"
    msg += f"💀 **최저 매매**\n"
    msg += f"> {worst['종목명']} {float(worst['수익률(%)']):.2f}% (`{float(worst['수익금(원)']):+,.0f}원`)\n\n"
    msg += "📊 **전체 내역**\n"

    for t in trades:
        emoji = "🟢" if float(t["수익금(원)"]) > 0 else "🔴"
        msg += f"> {emoji} {t['종목명']} {float(t['수익률(%)']):+.2f}% (`{float(t['수익금(원)']):+,.0f}원`) [{t['매도사유']}]\n"

    send_discord(msg)
    mark_weekly_report_sent()
    logging.info("Weekly report sent")

# =========================
# 스케줄러 메인 루프
# =========================

def scheduler():
    last_notified_time = "" 
    open_sent  = False
    close_sent = False
    last_date  = ""
    is_holiday_today = False 

    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_time_str = now.strftime("%H:%M")

        try:
            if today != last_date:
                is_holiday_today = is_market_holiday()
                open_sent  = False
                close_sent = False
                last_date  = today
                logging.info(f"오늘 휴장 여부 체크 결과: {is_holiday_today}")

            if not is_holiday_today:
                
                if now.hour == 9 and now.minute == 0 and not open_sent:
                    market_open()
                    if not is_weekly_report_sent():
                        send_weekly_report()
                    open_sent = True

                if now.hour == 15 and now.minute == 30 and not close_sent:
                    market_close()
                    send_daily_report() # 💡 방금 만든 일일 결산 함수를 여기에 추가!
                    close_sent = True

                if current_time_str != last_notified_time:
                    
                    if now.hour == 9 and 0 <= now.minute <= 40 and now.minute % 2 == 0:
                        check_balance()
                        last_notified_time = current_time_str
                        
                    elif 10 <= now.hour <= 15 and now.minute == 0:
                        check_balance()
                        last_notified_time = current_time_str

        except Exception as e:
            logging.error(f"Scheduler Error: {e}")

        time.sleep(10) 

# =========================
# 봇 시작
# =========================

if __name__ == "__main__":
    logging.info("HM_v1_4 Bot Start")
    get_token()   
    
    print("Bot is running...")

    # ✅ 봇 시작 알림 전송 (여기에 추가!)
    start_msg = "🚀 **HM 자동매매 봇이 성공적으로 가동되었습니다!**\n오늘도 성공적인 투자를 기원합니다! 💸"
    send_discord(start_msg)

    # 💡 봇 켜자마자 잔고 체크. 만약 이번 주 기준 금액이 비어있다면, 
    # 이 함수 내부에서 알아서 weekly_info.json을 생성하고 알림을 쏴줍니다.
    check_balance()
    
    scheduler()