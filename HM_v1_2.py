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
WEEKLY_START_FILE = "weekly_start.json"  # 이 줄을 추가합니다.
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
        res = requests.post(
            DISCORD_WEBHOOK,
            json={"content": msg},
            timeout=10
        )
        # 디스코드 전송이 성공(200 또는 204)하지 않았다면 에러 로그 기록
        if res.status_code not in [200, 204]:
            logging.error(f"Discord 전송 실패: {res.status_code} - {res.text}")
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

def get_saved_start_balance(current_total):
    """주간 시작 금액(월요일 09시 기준)을 파일에서 관리합니다."""
    this_monday = get_monday_of_week(datetime.now())
    
    # 1. 파일이 아예 없으면 말씀하신 100만 원으로 최초 1회 세팅
    if not os.path.exists(WEEKLY_START_FILE):
        save_weekly_start_balance(1000000, this_monday)
        return 1000000.0

    with open(WEEKLY_START_FILE, "r") as f:
        data = json.load(f)

    now = datetime.now()
    
    # 2. 월요일 09시가 지났고, 이번 주 기록이 아니라면 현재 잔고로 새로 갱신
    is_new_week = (data.get("week_monday") != this_monday)
    is_after_monday_open = (now.weekday() == 0 and now.hour >= 9) or (now.weekday() > 0)
    
    if is_new_week and is_after_monday_open:
        save_weekly_start_balance(current_total, this_monday)
        return float(current_total)
        
    return float(data.get("start_balance", current_total))

def save_weekly_start_balance(balance, monday_date_str):
    with open(WEEKLY_START_FILE, "w") as f:
        json.dump({
            "week_monday": monday_date_str,
            "start_balance": float(balance)
        }, f)

def is_market_holiday():
    """오늘이 한국 거래소 휴장일인지 확인합니다."""
    now = datetime.now()
    today_str = now.strftime("%Y%m%d")
    
    # 주말이면 API 조회 없이 바로 휴일로 판단
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
        "BASS_DT": today_str, # 조회 기준일
        "CTX_AREA_NK100": "",
        "CTX_AREA_FK100": ""
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        
        # 'opnd_yn': 'Y'면 개장, 'N'이면 휴장
        for item in data.get("output", []):
            if item["bass_dt"] == today_str:
                return item["opnd_yn"] == "N"
                
    except Exception as e:
        logging.error(f"Holiday Check Error: {e}")
        
    return False # 에러 발생 시 일단 평일로 간주 (알림이 오는 게 안 오는 것보다 낫기 때문)

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
        "CTX_AREA_FK100": "",  # ✅ 추가 (필수)
        "CTX_AREA_NK100": ""   # ✅ 추가 (필수)
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

        total = float(data["output2"][0]["tot_evlu_amt"])
        
        # 주간 시작 금액 불러오기 (이번 주 월요일 9시 기준 갱신 또는 유지)
        start_balance = get_saved_start_balance(total)

        # 이번 주 기준 누적 수익률 계산
        if start_balance > 0:
            profit_amt = total - start_balance
            profit_rate = round((profit_amt / start_balance) * 100, 2)
        else:
            profit_amt = 0.0
            profit_rate = 0.0

        # =========================
        # 디스코드 메시지 포맷팅
        # =========================
        msg = f"📊 [실시간 자산 리포트 - {now.strftime('%H:%M')}]\n"
        
        # 보유 종목 내역 추가
        holdings = data.get("output1", [])
        if holdings:
            for stock in holdings:
                name = stock.get("prdt_name", "Unknown")
                rate = float(stock.get("evlu_pfls_rt", "0"))
                amt = float(stock.get("evlu_amt", "0")) # 종목별 평가금액
                
                # 수익률에 따른 이모지 설정
                if rate > 0:
                    icon = "📈"
                elif rate < 0:
                    icon = "📉"
                else:
                    icon = "➖"
                
                # 디스코드 인용구(>)를 사용하여 들여쓰기 효과
                msg += f"> {icon} {name}: {rate:+.2f}% (`{int(amt):,}원`)\n"
        else:
            msg += "> 텅~ (현재 보유 종목이 없습니다)\n"

        msg += "\n"
        msg += f"💰 현재 총 자산: `{int(total):,}원`\n"
        msg += f"📅 주간 성적: `{int(profit_amt):+,}원` (`{profit_rate:+.2f}%`)\n"
        msg += "*(기준: 이번주 월요일 09:00)*"
        
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
    is_holiday_today = False # 오늘 휴장 여부 저장 변수

    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        try:
            # 날짜가 바뀌면 오늘이 휴장일인지 새로 확인
            if today != last_date:
                is_holiday_today = is_market_holiday()
                open_sent  = False
                close_sent = False
                last_date  = today
                logging.info(f"오늘 휴장 여부 체크 결과: {is_holiday_today}")

            # 오늘이 휴장일이 아닐 때만 실행
            if not is_holiday_today:
                # 장 시작 알림
                if now.hour == 9 and now.minute == 0 and not open_sent:
                    market_open()
                    open_sent = True

                # 장 종료 알림
                if now.hour == 15 and now.minute == 30 and not close_sent:
                    market_close()
                    close_sent = True

                # 정각 잔고 조회
                if now.minute == 0 and now.hour != last_hour:
                    check_balance()
                    
                    # 주간 리포트 (월요일 9시)
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
    # ✅ 환경 변수 로딩 테스트 (터미널에서 직접 확인)
    # print(f"디스코드 웹훅 로드 확인: {DISCORD_WEBHOOK}")
    # print(f"계좌번호 로드 확인: {ACCOUNT}")
    get_token()   # 시작 즉시 토큰 발급
    check_balance() # ✅ 테스트를 위해 시작 직후 잔고 조회 1회 실행
    scheduler()