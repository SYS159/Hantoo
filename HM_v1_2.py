import requests
import json
import os
import csv
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

# =========================
# 환경 변수
# =========================

load_dotenv()

APP_KEY = os.getenv("APP_KEY")
APP_SECRET = os.getenv("APP_SECRET")
ACCOUNT = os.getenv("ACCOUNT")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

BASE_URL = "https://openapi.koreainvestment.com:9443"

TOKEN_FILE       = "token.json"
TRADES_FILE      = "trades.csv"
WEEKLY_SENT_FILE = "weekly_sent.json"
LOG_NAME         = "HM_v1_2.log"

# =========================
# 로그 설정
# =========================

logging.basicConfig(
    filename=LOG_NAME,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# =========================
# 디스코드 알림
# =========================

def send_discord(msg):

    try:
        requests.post(
            DISCORD_WEBHOOK,
            json={"content": msg},
            timeout=10
        )
    except Exception as e:
        logging.error(f"Discord Error: {e}")

# =========================
# 토큰 관리
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
    # 오늘 자정(23:59)을 만료 시간으로 설정
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


# =========================
# 잔고 조회
# =========================

def check_balance():

    now = datetime.now()

    # 한국 주식 장 시간 체크 (09:00 ~ 15:30)
    if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30)):
        logging.info("Market closed - skip balance check")
        return

    token = get_token()

    if token is None:
        logging.error("Token not available")
        return

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"

    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
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
        "PRCS_DVSN": "01"
    }

    try:

        res = requests.get(url, headers=headers, params=params, timeout=10)

        if res.status_code != 200:
            logging.error(f"HTTP Error: {res.status_code} {res.text}")
            return

        data = res.json()

        # API 오류 방어
        if "output2" not in data or len(data["output2"]) == 0:
            logging.error(f"Balance API error: {data}")
            return

        total = data["output2"][0]["tot_evlu_amt"]
        profit = data["output2"][0]["evlu_pfls_rt"]

        msg = f"📊 계좌 현황 ({now.strftime('%H:%M')})\n\n"
        msg += f"총 평가금액\n{int(total):,}원\n\n"
        msg += f"총 수익률\n{profit}%\n\n"
        msg += "보유 종목\n"

        for stock in data.get("output1", []):

            name = stock.get("prdt_name", "Unknown")
            qty = stock.get("hldg_qty", "0")
            rate = stock.get("evlu_pfls_rt", "0")

            msg += f"{name} {qty}주 {rate}%\n"

        send_discord(msg)

        logging.info("Balance check success")

    except Exception as e:

        logging.error(f"Balance Error: {e}")


# =========================
# 장 상태 알림
# =========================

def market_open():

    send_discord("🔔 한국 주식시장 개장 (09:00)")


def market_close():

    send_discord("🔔 한국 주식시장 마감 (15:30)")


# =========================
# 주간 리포트
# =========================

def get_monday_of_week(date):
    """해당 날짜의 주 월요일 반환"""
    return (date - timedelta(days=date.weekday())).strftime("%Y-%m-%d")


def is_weekly_report_sent():
    """이번주 리포트 전송 여부 확인"""

    if not os.path.exists(WEEKLY_SENT_FILE):
        return False

    with open(WEEKLY_SENT_FILE) as f:
        data = json.load(f)

    this_monday = get_monday_of_week(datetime.now())

    return data.get("last_sent_week") == this_monday


def mark_weekly_report_sent():
    """이번주 리포트 전송 완료 기록"""

    this_monday = get_monday_of_week(datetime.now())

    with open(WEEKLY_SENT_FILE, "w") as f:
        json.dump({"last_sent_week": this_monday}, f)


def send_weekly_report():
    """trades.csv 읽어서 주간 리포트 디스코드 전송"""

    if not os.path.exists(TRADES_FILE):
        logging.info("trades.csv 없음 - 주간 리포트 스킵")
        return

    now = datetime.now()

    # 지난 월요일 ~ 어제까지 (지난 한 주)
    this_monday = datetime.now() - timedelta(days=now.weekday())
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
            f"📋 주간 매매 리포트\n"
            f"({last_monday.strftime('%m/%d')} ~ {last_sunday.strftime('%m/%d')})\n\n"
            f"지난 주 매매 내역이 없습니다."
        )
        send_discord(msg)
        mark_weekly_report_sent()
        return

    # 집계
    total_profit   = sum(float(t["수익금(원)"]) for t in trades)
    total_trades   = len(trades)
    win_trades     = [t for t in trades if float(t["수익금(원)"]) > 0]
    lose_trades    = [t for t in trades if float(t["수익금(원)"]) <= 0]
    win_rate       = len(win_trades) / total_trades * 100
    avg_profit     = total_profit / total_trades

    best  = max(trades, key=lambda t: float(t["수익금(원)"]))
    worst = min(trades, key=lambda t: float(t["수익금(원)"]))

    msg  = f"📋 주간 매매 리포트\n"
    msg += f"({last_monday.strftime('%m/%d')} ~ {last_sunday.strftime('%m/%d')})\n\n"
    msg += f"총 매매 횟수: {total_trades}회\n"
    msg += f"승률: {win_rate:.1f}% ({len(win_trades)}승 {len(lose_trades)}패)\n"
    msg += f"총 수익금: {total_profit:+,.0f}원\n"
    msg += f"평균 수익금: {avg_profit:+,.0f}원\n\n"
    msg += f"🏆 최고 매매\n"
    msg += f"{best['종목명']} {float(best['수익률(%)']):.2f}% ({float(best['수익금(원)']):+,.0f}원)\n\n"
    msg += f"💀 최저 매매\n"
    msg += f"{worst['종목명']} {float(worst['수익률(%)']):.2f}% ({float(worst['수익금(원)']):+,.0f}원)\n\n"
    msg += "📊 전체 내역\n"

    for t in trades:
        emoji = "🟢" if float(t["수익금(원)"]) > 0 else "🔴"
        msg += f"{emoji} {t['종목명']} {float(t['수익률(%)']):+.2f}% ({float(t['수익금(원)']):+,.0f}원) [{t['매도사유']}]\n"

    send_discord(msg)
    mark_weekly_report_sent()
    logging.info("Weekly report sent")


# =========================
# 스케줄러
# =========================

def scheduler():

    last_hour  = -1
    open_sent  = False
    close_sent = False
    last_date  = ""

    while True:

        now = datetime.now()

        try:

            # 날짜 바뀌면 플래그 초기화
            today = now.strftime("%Y-%m-%d")
            if today != last_date:
                open_sent  = False
                close_sent = False
                last_date  = today

            # 장 시작
            if now.hour == 9 and now.minute == 0 and not open_sent:
                market_open()
                open_sent = True

            # 장 종료
            if now.hour == 15 and now.minute == 30 and not close_sent:
                market_close()
                close_sent = True

            # 정각 알림
            if now.minute == 0 and now.hour != last_hour:

                check_balance()

                # 09:00 에 주간 리포트 체크 (매일 - 이번주 미전송 시 전송)
                if now.hour == 9 and not is_weekly_report_sent():
                    send_weekly_report()

                last_hour = now.hour

        except Exception as e:

            logging.error(f"Scheduler Error: {e}")

        time.sleep(20)


# =========================
# 시작
# =========================

if __name__ == "__main__":

    logging.info("Bot Start")

    get_token()   # 시작 즉시 토큰 발급

    scheduler()