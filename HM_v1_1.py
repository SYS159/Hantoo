import requests
import json
import os
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

TOKEN_FILE = "token.json"
LOG_NAME = "HM_v1_1.log"

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

    expire = datetime.now() + timedelta(hours=23)

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
# 스케줄러
# =========================

def scheduler():

    last_hour = -1
    open_sent = False
    close_sent = False

    while True:

        now = datetime.now()

        try:

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

                last_hour = now.hour

        except Exception as e:

            logging.error(f"Scheduler Error: {e}")

        time.sleep(20)


# =========================
# 시작
# =========================

if __name__ == "__main__":

    logging.info("Bot Start")

    scheduler()