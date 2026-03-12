import requests
import json
import os
import time
import logging
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

# =========================
# 환경 변수
# =========================

load_dotenv()

APP_KEY = os.getenv("APP_KEY")
APP_SECRET = os.getenv("APP_SECRET")
ACCOUNT = os.getenv("ACCOUNT")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

BASE_URL = "https://openapi.koreainvestment.com:9443"

TOKEN_FILE = "token.json"
LOG_NAME = "HTD_v1_1.log"

# =========================
# 전략 파라미터 (여기서 조정)
# =========================

BUY_AMOUNT = 100_000        # 종목당 매수 금액 (원)

SCAN_INTERVAL = 10          # 스캐너 루프 간격 (초)
TRAILING_INTERVAL = 3       # 트레일링 루프 간격 (초)

MIN_CHANGE_RATE = 5.0       # 최소 등락률 조건 (%)
MIN_VOLUME_RATIO = 5.0      # 전일 대비 최소 거래량 배율
MIN_EXEC_STRENGTH = 120.0   # 최소 체결강도

STOP_LOSS_RATE = -2.0       # 손절 기준 (%)
TRAILING_TRIGGER = 3.0      # 트레일링 스탑 활성화 기준 (%)
TRAILING_DROP = 1.0         # 최고가 대비 하락 시 청산 기준 (%)

SCAN_START = (9, 5)         # 스캔 시작 시간 (시, 분)
SCAN_END = (15, 20)         # 스캔 종료 시간 (시, 분)

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
            DISCORD_WEBHOOK_URL,
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
# 트레일링 스탑 클래스
# =========================

class TrailingStop:

    def __init__(self, entry_price):
        self.entry_price = entry_price
        self.high_price = entry_price
        self.trailing_active = False

    def update(self, current_price):

        rate = (current_price - self.entry_price) / self.entry_price * 100

        if not self.trailing_active:

            # 손절
            if rate <= STOP_LOSS_RATE:
                return "STOP_LOSS", rate

            # 트레일링 활성화
            if rate >= TRAILING_TRIGGER:
                self.trailing_active = True
                self.high_price = current_price

        if self.trailing_active:

            if current_price > self.high_price:
                self.high_price = current_price

            drop_from_high = (self.high_price - current_price) / self.high_price * 100

            if drop_from_high >= TRAILING_DROP:
                return "TRAILING_STOP", rate

        return "HOLD", rate

# =========================
# 포지션 관리
# =========================

positions = {}          # {"종목코드": {"name": ..., "qty": ..., "ts": TrailingStop}}
positions_lock = threading.Lock()


def get_available_cash():
    """예수금 조회"""

    token = get_token()

    if not token:
        return 0

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order"

    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "TTTC8908R"
    }

    params = {
        "CANO": ACCOUNT[:8],
        "ACNT_PRDT_CD": ACCOUNT[8:],
        "PDNO": "005930",
        "ORD_UNPR": "0",
        "ORD_DVSN": "01",
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N"
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)

        if res.status_code != 200:
            return 0

        data = res.json()

        if data.get("rt_cd") != "0":
            return 0

        return int(data["output"]["ord_psbl_cash"])

    except Exception as e:
        logging.error(f"Cash Error: {e}")
        return 0

# =========================
# 한투 API - 등락률 순위 조회
# =========================

def get_top_stocks():

    token = get_token()

    if not token:
        return []

    url = f"{BASE_URL}/uapi/domestic-stock/v1/ranking/fluctuation"

    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "FHPST01720000",
        "custtype": "P"
    }

    params = {
        "fid_rsfl_rate2": "",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20172",
        "fid_input_iscd": "0001",
        "fid_rank_sort_cls_code": "0",
        "fid_input_cnt_1": "0",
        "fid_prc_cls_code": "1",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": ""
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)

        if res.status_code != 200:
            logging.error(f"Top stocks HTTP Error: {res.status_code}")
            return []

        data = res.json()

        if data.get("rt_cd") != "0":
            logging.error(f"Top stocks API Error: {data.get('msg1')}")
            return []

        return data.get("output", [])

    except Exception as e:
        logging.error(f"Top stocks Error: {e}")
        return []

# =========================
# 한투 API - 개별 종목 현재가 조회
# =========================

def get_current_price(stock_code):

    token = get_token()

    if not token:
        return None

    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"

    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "FHKST01010100"
    }

    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": stock_code
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)

        if res.status_code != 200:
            return None

        data = res.json()

        if data.get("rt_cd") != "0":
            return None

        return int(data["output"]["stck_prpr"])

    except Exception as e:
        logging.error(f"Price Error ({stock_code}): {e}")
        return None

# =========================
# 한투 API - 시장가 매수
# =========================

def buy_market(stock_code, stock_name, qty):

    token = get_token()

    if not token:
        return False

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"

    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "TTTC0802U"
    }

    data = {
        "CANO": ACCOUNT[:8],
        "ACNT_PRDT_CD": ACCOUNT[8:],
        "PDNO": stock_code,
        "ORD_DVSN": "01",       # 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0"
    }

    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)

        if res.status_code != 200:
            logging.error(f"Buy HTTP Error: {res.status_code}")
            return False

        result = res.json()

        if result.get("rt_cd") != "0":
            logging.error(f"Buy API Error ({stock_name}): {result.get('msg1')}")
            return False

        logging.info(f"Buy Success: {stock_name} {qty}주")
        return True

    except Exception as e:
        logging.error(f"Buy Error ({stock_name}): {e}")
        return False

# =========================
# 한투 API - 시장가 매도
# =========================

def sell_market(stock_code, stock_name, qty):

    token = get_token()

    if not token:
        return False

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"

    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "TTTC0801U"
    }

    data = {
        "CANO": ACCOUNT[:8],
        "ACNT_PRDT_CD": ACCOUNT[8:],
        "PDNO": stock_code,
        "ORD_DVSN": "01",       # 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0"
    }

    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)

        if res.status_code != 200:
            logging.error(f"Sell HTTP Error: {res.status_code}")
            return False

        result = res.json()

        if result.get("rt_cd") != "0":
            logging.error(f"Sell API Error ({stock_name}): {result.get('msg1')}")
            return False

        logging.info(f"Sell Success: {stock_name} {qty}주")
        return True

    except Exception as e:
        logging.error(f"Sell Error ({stock_name}): {e}")
        return False

# =========================
# 스캐너 루프
# =========================

def scanner_loop():

    logging.info("Scanner loop started")

    while True:

        try:

            now = datetime.now()

            # 스캔 시간 체크
            start = now.replace(hour=SCAN_START[0], minute=SCAN_START[1], second=0)
            end = now.replace(hour=SCAN_END[0], minute=SCAN_END[1], second=0)

            if not (start <= now <= end):
                time.sleep(SCAN_INTERVAL)
                continue

            with positions_lock:
                current_codes = set(positions.keys())

            stocks = get_top_stocks()

            for stock in stocks:

                with positions_lock:
                    current_codes = set(positions.keys())

                code = stock.get("mksc_shrn_iscd", "")
                name = stock.get("hts_kor_isnm", "")
                change_rate = float(stock.get("prdy_ctrt", "0"))
                exec_strength = float(stock.get("seln_cntg_csnu", "0"))

                # 이미 보유 중인 종목 제외
                if code in current_codes:
                    continue

                # 복합 조건 필터
                if change_rate < MIN_CHANGE_RATE:
                    continue

                if exec_strength < MIN_EXEC_STRENGTH:
                    continue

                # 예수금 확인
                available_cash = get_available_cash()

                if available_cash < BUY_AMOUNT:
                    logging.info(f"예수금 부족 ({available_cash:,}원) - 스캔 중단")
                    break

                # 현재가 조회
                current_price = get_current_price(code)

                if not current_price or current_price <= 0:
                    continue

                # 매수 수량 계산 (BUY_AMOUNT 기준)
                qty = BUY_AMOUNT // current_price

                if qty <= 0:
                    logging.info(f"주가가 너무 높음: {name} ({current_price:,}원)")
                    continue

                # 매수 실행
                success = buy_market(code, name, qty)

                if success:
                    ts = TrailingStop(entry_price=current_price)

                    with positions_lock:
                        positions[code] = {
                            "name": name,
                            "qty": qty,
                            "entry_price": current_price,
                            "ts": ts
                        }

                    msg = (
                        f"🟢 신규 진입\n"
                        f"종목: {name} ({code})\n"
                        f"진입가: {current_price:,}원\n"
                        f"수량: {qty}주\n"
                        f"투자금: {current_price * qty:,}원\n"
                        f"현재 보유 종목 수: {len(positions)}개"
                    )

                    send_discord(msg)
                    logging.info(f"Position opened: {name} {qty}주 @ {current_price}")

                time.sleep(0.5)  # API 과호출 방지

        except Exception as e:
            logging.error(f"Scanner Error: {e}")

        time.sleep(SCAN_INTERVAL)

# =========================
# 트레일링 스탑 루프
# =========================

def trailing_loop():

    logging.info("Trailing loop started")

    while True:

        try:

            with positions_lock:
                codes = list(positions.keys())

            for code in codes:

                with positions_lock:
                    if code not in positions:
                        continue
                    pos = positions[code]

                current_price = get_current_price(code)

                if not current_price:
                    continue

                signal, rate = pos["ts"].update(current_price)

                if signal == "HOLD":
                    continue

                # 매도 실행
                success = sell_market(code, pos["name"], pos["qty"])

                if success:

                    with positions_lock:
                        if code in positions:
                            del positions[code]

                    if signal == "STOP_LOSS":
                        emoji = "🔴"
                        label = "손절"
                    else:
                        emoji = "🟡"
                        label = "익절 (트레일링)"

                    msg = (
                        f"{emoji} {label} 청산\n"
                        f"종목: {pos['name']} ({code})\n"
                        f"진입가: {pos['entry_price']:,}원\n"
                        f"청산가: {current_price:,}원\n"
                        f"수익률: {rate:.2f}%\n"
                        f"잔여 보유 종목: {len(positions)}개"
                    )

                    send_discord(msg)
                    logging.info(f"Position closed ({label}): {pos['name']} {rate:.2f}%")

                time.sleep(0.3)  # API 과호출 방지

        except Exception as e:
            logging.error(f"Trailing Error: {e}")

        time.sleep(TRAILING_INTERVAL)

# =========================
# 시작
# =========================

if __name__ == "__main__":

    logging.info("HTD Bot Start")
    send_discord("🚀 HTD 자동매매 봇 시작")

    # 스캐너 스레드
    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t1.start()

    # 트레일링 스레드
    t2 = threading.Thread(target=trailing_loop, daemon=True)
    t2.start()

    t1.join()
    t2.join()