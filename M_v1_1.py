import requests
import os
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("APP_KEY")
APP_SECRET = os.getenv("APP_SECRET")
ACCOUNT = os.getenv("ACCOUNT")
PRODUCT = os.getenv("PRODUCT")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

BASE_URL = "https://openapi.koreainvestment.com:9443"

access_token = None
token_time = 0


def issue_token():

    url = f"{BASE_URL}/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }

    res = requests.post(url, json=body)

    data = res.json()

    print("토큰 발급:", data)

    return data["access_token"]


def get_token():

    global access_token, token_time

    if access_token is None:
        access_token = issue_token()
        token_time = time.time()

    elif time.time() - token_time > 60 * 60 * 23:
        print("토큰 재발급")
        access_token = issue_token()
        token_time = time.time()

    return access_token


def check_balance():

    token = get_token()

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"

    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "TTTC8434R"
    }

    params = {
        "CANO": ACCOUNT,
        "ACNT_PRDT_CD": PRODUCT,
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

    res = requests.get(url, headers=headers, params=params)

    data = res.json()

    stocks = data.get("output1", [])

    message = f"📊 자산 조회 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"

    if len(stocks) == 0:
        message += "보유 종목 없음"
    else:
        for s in stocks:
            name = s["prdt_name"]
            qty = s["hldg_qty"]
            profit = s["evlu_pfls_rt"]

            message += f"{name} | {qty}주 | {profit}%\n"

    send_discord(message)


def send_discord(msg):

    data = {
        "content": msg
    }

    requests.post(WEBHOOK, json=data)

def wait_until_next_hour():

    now = datetime.now()

    seconds = (60 - now.minute - 1) * 60 + (60 - now.second)

    print(f"다음 정각까지 {seconds}초 대기")

    time.sleep(seconds)

def main():

    print("자산 봇 시작")

    wait_until_next_hour()   # 첫 정각 맞추기

    while True:

        try:
            check_balance()

        except Exception as e:
            print("오류:", e)

        wait_until_next_hour()


if __name__ == "__main__":
    main()