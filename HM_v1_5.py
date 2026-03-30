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
WEEKLY_FILE       = "weekly_info.json"
LOG_NAME          = "HM_v1_5.log"

# =========================
# 로그 설정
# =========================

logging.basicConfig(
    filename=LOG_NAME,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8"
)

def send_discord(msg):
    try:
        res = requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
        if res.status_code not in [200, 204]:
            logging.error(f"Discord 전송 실패: {res.status_code} - {res.text}")
    except Exception as e:
        logging.error(f"Discord Error: {e}")

# =========================
# API 공통 함수
# =========================

def load_token():
    if not os.path.exists(TOKEN_FILE): return None
    with open(TOKEN_FILE) as f: data = json.load(f)
    if datetime.now() >= datetime.strptime(data["expire"], "%Y-%m-%d %H:%M:%S"): return None
    return data["token"]

def save_token(token):
    expire = datetime.now().replace(hour=23, minute=59, second=0)
    with open(TOKEN_FILE, "w") as f:
        json.dump({"token": token, "expire": expire.strftime("%Y-%m-%d %H:%M:%S")}, f)

def get_token():
    token = load_token()
    if token: return token
    url = f"{BASE_URL}/oauth2/tokenP"
    data = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    try:
        res = requests.post(url, json=data, timeout=10)
        if res.status_code != 200:
            logging.error(res.text)
            return None
        token = res.json()["access_token"]
        save_token(token)
        return token
    except Exception as e:
        logging.error(f"Token Error: {e}")
        return None

def is_market_holiday():
    now = datetime.now()
    today_str = now.strftime("%Y%m%d")
    if now.weekday() >= 5: return True

    token = get_token()
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/chk-holiday"
    headers = {"content-type": "application/json", "authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "CTCA0903R", "custtype": "P"}
    params = {"BASS_DT": today_str, "CTX_AREA_NK100": "", "CTX_AREA_FK100": ""}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        for item in res.json().get("output", []):
            if item["bass_dt"] == today_str: return item["opnd_yn"] == "N" 
    except Exception as e:
        logging.error(f"Holiday Check Error: {e}")
    return False

def get_total_asset():
    """순수하게 현재 총 자산 금액만 가져옵니다 (저장 기능 없음)"""
    token = get_token()
    if not token: return 0
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "TTTC8434R"}
    params = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        if "output2" in data and len(data["output2"]) > 0:
            return float(data["output2"][0]["tot_evlu_amt"])
    except Exception as e:
        logging.error(f"Asset Check Error: {e}")
    return 0

# =========================
# 📅 주간 데이터 통합 관리 시스템
# =========================

def get_this_monday():
    now = datetime.now()
    return (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")

def get_weekly_data():
    default_data = {
        "week_monday": get_this_monday(),
        "start_balance": 0.0,
        "official_set_week": "",
        "report_sent_week": ""
    }
    if not os.path.exists(WEEKLY_FILE): return default_data
    try:
        with open(WEEKLY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default_data

def update_weekly_data(key, value):
    data = get_weekly_data()
    data[key] = value
    try:
        with open(WEEKLY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Weekly Data Save Error: {e}")

# =========================
# 잔고 조회
# =========================

def check_balance():
    now = datetime.now()
    token = get_token()
    if token is None: return

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = {"authorization": f"Bearer {token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "TTTC8434R"}
    params = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        if "output2" not in data or len(data["output2"]) == 0: return

        total = float(data["output2"][0]["tot_evlu_amt"])
        
        # 💡 [수정됨] 파일에 기록하는 로직을 완전히 제거하고 순수하게 읽어오기만 함
        start_balance = float(get_weekly_data().get("start_balance", 0))

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
                if qty <= 0: continue
                
                active_holdings_found = True
                name = stock.get("prdt_name", "Unknown")
                rate = float(stock.get("evlu_pfls_rt", "0"))
                amt = float(stock.get("evlu_amt", "0"))
                
                if rate > 0: icon = "📈"
                elif rate < 0: icon = "📉"
                else: icon = "➖"
                
                msg += f"> {icon} **{name}**: {rate:+.2f}% (`{int(amt):,}원`)\n"
                
        if not active_holdings_found:
            return

        msg += "\n"
        msg += f"💰 **현재 총 자산:** `{int(total):,}원`\n"
        msg += f"📅 **주간 성적:** `{int(profit_amt):+,}원` (`{profit_rate:+.2f}%`)\n"
        msg += "*(기준: 이번주 최초 개장일 08:30)*"
        
        send_discord(msg)

    except Exception as e:
        logging.error(f"Balance Error: {e}")

# =========================
# 리포트 발송
# =========================

def send_weekly_report():
    if not os.path.exists(TRADES_FILE): return

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
        msg = f"📋 **주간 매매 리포트**\n> ({last_monday.strftime('%m/%d')} ~ {last_sunday.strftime('%m/%d')})\n\n지난 주 매매 내역이 없습니다."
        send_discord(msg)
        return

    total_profit   = sum(float(t["수익금(원)"]) for t in trades)
    total_trades   = len(trades)
    win_trades     = [t for t in trades if float(t["수익금(원)"]) > 0]
    lose_trades    = [t for t in trades if float(t["수익금(원)"]) <= 0]
    win_rate       = len(win_trades) / total_trades * 100
    avg_profit     = total_profit / total_trades

    best  = max(trades, key=lambda t: float(t["수익금(원)"]))
    worst = min(trades, key=lambda t: float(t["수익금(원)"]))

    msg  = f"📋 **주간 매매 리포트**\n({last_monday.strftime('%m/%d')} ~ {last_sunday.strftime('%m/%d')})\n\n"
    msg += f"**총 매매 횟수:** {total_trades}회\n"
    msg += f"**승률:** {win_rate:.1f}% ({len(win_trades)}승 {len(lose_trades)}패)\n"
    msg += f"**총 수익금:** `{total_profit:+,.0f}원`\n"
    msg += f"**평균 수익금:** `{avg_profit:+,.0f}원`\n\n"
    msg += f"🏆 **최고 매매**\n> {best['종목명']} {float(best['수익률(%)']):.2f}% (`{float(best['수익금(원)']):+,.0f}원`)\n\n"
    msg += f"💀 **최저 매매**\n> {worst['종목명']} {float(worst['수익률(%)']):.2f}% (`{float(worst['수익금(원)']):+,.0f}원`)\n\n"
    msg += "📊 **전체 내역**\n"

    for t in trades:
        emoji = "🟢" if float(t["수익금(원)"]) > 0 else "🔴"
        msg += f"> {emoji} {t['종목명']} {float(t['수익률(%)']):+.2f}% (`{float(t['수익금(원)']):+,.0f}원`) [{t['매도사유']}]\n"

    send_discord(msg)

def send_daily_report():
    if not os.path.exists(TRADES_FILE): return
    today_str = datetime.now().strftime("%Y-%m-%d")
    trades_today = []

    try:
        with open(TRADES_FILE, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["날짜"].startswith(today_str):
                    trades_today.append(row)
    except Exception as e:
        return

    msg = f"🌅 **[오늘의 매매 결산 - {today_str}]**\n\n"
    if not trades_today:
        msg += "오늘은 거래가 발생하지 않았습니다. 푹 쉬세요! ☕"
        send_discord(msg)
        return

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

# =========================
# 장 상태 알림
# =========================

def market_open():
    send_discord("🔔 **한국 주식시장 개장 (09:00)**")

def market_close():
    send_discord("🔔 **한국 주식시장 마감 (15:30)**")

# =========================
# 스케줄러 메인 루프
# =========================

def scheduler():
    last_notified_time = "" 
    open_sent  = False
    close_sent = False
    last_date  = ""
    weekly_job_done = False # 💡 8시 30분 작업 완료 플래그
    is_holiday_today = False 

    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_time_str = now.strftime("%H:%M")

        try:
            # 날짜가 바뀌면 하루 한 번 플래그 리셋 및 휴일 체크
            if today != last_date:
                is_holiday_today = is_market_holiday()
                open_sent  = False
                close_sent = False
                weekly_job_done = False 
                last_date  = today
                logging.info(f"오늘 휴장 여부 체크 결과: {is_holiday_today}")

            if not is_holiday_today:
                
                # 🌟 [핵심 변경] 08:30 정각 : 첫 개장일 주간 리포트 & 기준가 공식 세팅
                if now.hour == 8 and now.minute == 30 and not weekly_job_done:
                    this_monday = get_this_monday()
                    data = get_weekly_data()

                    # 1. 지난주 리포트 발송 (이번 주에 아직 안 보냈다면 1회 발송)
                    if data.get("report_sent_week") != this_monday:
                        send_weekly_report()
                        update_weekly_data("report_sent_week", this_monday)

                    # 2. 이번 주 기준점 공식 세팅 (이번 주에 아직 안 했다면 1회 세팅)
                    if data.get("official_set_week") != this_monday:
                        total = get_total_asset()
                        if total > 0:
                            update_weekly_data("week_monday", this_monday)
                            update_weekly_data("official_set_week", this_monday)
                            update_weekly_data("start_balance", total)
                            send_discord(f"📅 **[이번 주 자산 기준점 세팅 완료]**\n이번 주 수익률 기준 자산이 `{int(total):,}원`으로 세팅되었습니다. (08:30 기준)")

                    weekly_job_done = True

                # 09:00 : 장 시작 알림
                if now.hour == 9 and now.minute == 0 and not open_sent:
                    market_open()
                    open_sent = True

                # 15:30 : 장 종료 알림 및 일일 리포트
                if now.hour == 15 and now.minute == 30 and not close_sent:
                    market_close()
                    send_daily_report() 
                    close_sent = True

                # 잔고 알림 로직 (기준가 세팅과 분리되어 스팸 방지됨)
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
    logging.info("HM_v1_5 Bot Start")
    get_token()   
    print("Bot is running...")

    start_msg = "🚀 **HM 자동매매 봇(V1.5)이 가동되었습니다!**\n알림 로직 개선 및 08:30 리포트가 적용되었습니다."
    send_discord(start_msg)

    # 💡 [중간 투입 방어] 봇을 처음 켰는데 기준가가 아예 0원이라면 임시 세팅
    data = get_weekly_data()
    if data.get("start_balance", 0) == 0:
        total = get_total_asset()
        if total > 0:
            update_weekly_data("start_balance", total)
            send_discord(f"🔄 **[기준점 임시 세팅]**\n봇이 중간에 실행되어 현재 자산(`{int(total):,}원`)을 임시 기준점으로 잡습니다.")

    check_balance()
    scheduler()