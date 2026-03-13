import requests
import json
import os
import csv
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
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

BASE_URL = "https://openapi.koreainvestment.com:9443"

TOKEN_FILE  = "token.json"
TRADES_FILE = "trades.csv"
LOG_NAME    = "HTD_v1_4.log"

# =========================
# 전략 파라미터 (여기서 조정)
# =========================

BUY_AMOUNT = 100_000        # 종목당 매수 금액 (원)

SCAN_INTERVAL = 10          # 스캐너 루프 간격 (초)
TRAILING_INTERVAL = 3       # 트레일링 루프 간격 (초)

MIN_CHANGE_RATE = 5.0       # 최소 등락률 조건 (%)
MIN_EXEC_STRENGTH = 120.0   # 최소 체결강도

# [수정됨] 시간대별 거래량 배율 (현실적인 급등주 수치로 조정)
VOLUME_RATIO_EARLY = 0.5    # 09:05 ~ 09:30 (장 초반 50%면 대폭발)
VOLUME_RATIO_LATE  = 1.0    # 09:30 ~ 10:30 (1시간 만에 전일 거래량 100% 돌파)

STOP_LOSS_RATE = -2.0       # 손절 기준 (%)
TRAILING_TRIGGER = 3.0      # 트레일링 스탑 활성화 기준 (%)
TRAILING_DROP = 1.0         # 최고가 대비 하락 시 청산 기준 (%)

SCAN_START  = (9,  5)       # 매수 스캔 시작
SCAN_MID    = (9, 30)       # 거래량 배율 전환 시점
SCAN_END    = (10, 30)      # 매수 스캔 종료
TRAILING_END = (15, 20)     # 트레일링 종료

# =========================
# 로그 설정
# =========================

logging.basicConfig(
    filename=LOG_NAME,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# =========================
# 공통 함수 (토큰, 디스코드, 예수금, CSV 저장)
# =========================

def send_discord(msg):
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
    except Exception as e:
        logging.error(f"Discord Error: {e}")

def get_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    if datetime.now() >= datetime.strptime(data["expire"], "%Y-%m-%d %H:%M:%S"):
        return None
    return data["token"]

def get_available_cash():
    token = get_token()
    if not token: return 0
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "TTTC8908R"}
    params = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "PDNO": "005930", "ORD_UNPR": "0", "ORD_DVSN": "01", "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        return int(data["output"]["ord_psbl_cash"]) if data.get("rt_cd") == "0" else 0
    except:
        return 0

def save_trade(name, code, entry_price, exit_price, qty, reason):
    profit_amt  = (exit_price - entry_price) * qty
    profit_rate = (exit_price - entry_price) / entry_price * 100
    file_exists = os.path.exists(TRADES_FILE)
    try:
        with open(TRADES_FILE, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["날짜", "종목명", "종목코드", "매수가", "매도가", "수량", "수익금(원)", "수익률(%)", "매도사유"])
            writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name, code, entry_price, exit_price, qty, round(profit_amt, 0), round(profit_rate, 2), reason])
        logging.info(f"Trade saved: {name} {profit_rate:.2f}% {profit_amt:,.0f}원")
    except Exception as e:
        logging.error(f"CSV Save Error: {e}")

# =========================
# API 조회 함수 모음
# =========================

def get_top_stocks():
    token = get_token()
    if not token: return []
    url = f"{BASE_URL}/uapi/domestic-stock/v1/ranking/fluctuation"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "FHPST01720000", "custtype": "P"}
    params = {"fid_rsfl_rate2": "", "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20172", "fid_input_iscd": "0001", "fid_rank_sort_cls_code": "0", "fid_input_cnt_1": "0", "fid_prc_cls_code": "1", "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0", "fid_div_cls_code": "0", "fid_rsfl_rate1": ""}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        return res.json().get("output", []) if res.status_code == 200 else []
    except:
        return []

def get_current_price(stock_code):
    token = get_token()
    if not token: return None
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "FHKST01010100"}
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        return int(data["output"]["stck_prpr"]) if data.get("rt_cd") == "0" else None
    except:
        return None

def get_volume_ratio(stock_code):
    token = get_token()
    if not token: return 0
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "FHKST01010400"}
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code, "fid_org_adj_prc": "0", "fid_period_div_code": "D"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        output = res.json().get("output", [])
        if len(output) < 2: return 0
        today_vol = int(output[0].get("acml_vol", 0))
        prev_vol = int(output[1].get("acml_vol", 0))
        return today_vol / prev_vol if prev_vol > 0 else 0
    except:
        return 0

# =========================
# 매수 / 매도 주문 함수
# =========================

def buy_market(stock_code, stock_name, qty):
    token = get_token()
    if not token: return False
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "TTTC0802U"}
    data = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "PDNO": stock_code, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"}
    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        return res.json().get("rt_cd") == "0"
    except:
        return False

def sell_market(stock_code, stock_name, qty):
    token = get_token()
    if not token: return False
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "TTTC0801U"}
    data = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "PDNO": stock_code, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"}
    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        return res.json().get("rt_cd") == "0"
    except:
        return False

def cancel_order(order_no):
    token = get_token()
    if not token: return False
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-rvsecncl"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "TTTC0803U"}
    data = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "KRX_FWDG_ORD_ORG_NO": "", "ORGN_ODNO": order_no, "RVSE_CNCL_DVSN_CD": "02", "ORD_DVSN": "00", "ORD_QTY": "0", "ORD_UNPR": "0"}
    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        return res.json().get("rt_cd") == "0"
    except:
        return False

def is_executed(order_no):
    token = get_token()
    if not token: return True
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "TTTC8036R"}
    params = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        for item in res.json().get("output", []):
            if item["odno"] == order_no:
                return int(item.get("ncnl_qty", 1)) == 0
        return True 
    except:
        return False

def sell_smart(stock_code, stock_name, qty):
    retry_count = 0
    while retry_count < 6:
        current_price = get_current_price(stock_code)
        if not current_price: break
        
        token = get_token()
        url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "TTTC0801U"}
        data = {"CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "PDNO": stock_code, "ORD_DVSN": "00", "ORD_QTY": str(qty), "ORD_UNPR": str(current_price)}
        
        try:
            res = requests.post(url, headers=headers, json=data, timeout=10)
            res_data = res.json()
            if res_data.get("rt_cd") != "0": break
            
            order_no = res_data["output"]["odno"]
            
            executed = False
            for _ in range(10):
                time.sleep(1)
                if is_executed(order_no):
                    executed = True
                    break
            
            if executed: return True
            cancel_order(order_no)
            retry_count += 1
        except:
            break
            
    logging.warning(f"{stock_name} 지정가 실패 -> 시장가 던짐")
    return sell_market(stock_code, stock_name, qty)

# =========================
# 비동기 매도 & 알림 처리 래퍼 (블로킹 방지)
# =========================
def execute_async_sell(code, name, qty, entry_price, trigger_price, signal):
    """
    메인 루프를 멈추지 않고 뒤에서 조용히 매도와 기록을 처리하는 함수
    """
    # [적용 4번] 손절이면 고민 없이 즉시 시장가 투하, 익절이면 스마트하게 지정가 추적
    if signal == "STOP_LOSS":
        success = sell_market(code, name, qty)
        label, emoji = "손절", "🔴"
    else:
        success = sell_smart(code, name, qty)
        label, emoji = "익절 (트레일링)", "🟡"

    if success:
        # 매도 직후의 가격을 탈출가로 기록 (스마트 매도로 인해 실제 체결가와 미세한 차이는 있을 수 있음)
        final_price = get_current_price(code) or trigger_price
        rate = (final_price - entry_price) / entry_price * 100
        
        save_trade(name, code, entry_price, final_price, qty, label)
        msg = (
            f"{emoji} {label} 완료\n"
            f"종목: {name} ({code})\n"
            f"진입가: {entry_price:,}원\n"
            f"청산가: {final_price:,}원\n"
            f"수익률: {rate:.2f}%"
        )
        send_discord(msg)
        logging.info(f"Position closed ({label}): {name} {rate:.2f}%")

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
            if rate <= STOP_LOSS_RATE: return "STOP_LOSS", rate
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

positions = {}          
positions_lock = threading.Lock()

# =========================
# 스캐너 루프
# =========================

def scanner_loop():
    logging.info("Scanner loop started")
    while True:
        try:
            now = datetime.now()
            start = now.replace(hour=SCAN_START[0], minute=SCAN_START[1], second=0)
            end   = now.replace(hour=SCAN_END[0], minute=SCAN_END[1], second=0)
            mid   = now.replace(hour=SCAN_MID[0], minute=SCAN_MID[1], second=0)

            if not (start <= now <= end):
                time.sleep(SCAN_INTERVAL)
                continue

            required_volume_ratio = VOLUME_RATIO_EARLY if now < mid else VOLUME_RATIO_LATE
            stocks = get_top_stocks()

            for stock in stocks:
                with positions_lock:
                    current_codes = set(positions.keys())

                code = stock.get("mksc_shrn_iscd", "")
                name = stock.get("hts_kor_isnm", "")
                change_rate = float(stock.get("prdy_ctrt", "0"))
                exec_strength = float(stock.get("seln_cntg_csnu", "0"))

                if code in current_codes: continue
                if change_rate < MIN_CHANGE_RATE: continue
                if exec_strength < MIN_EXEC_STRENGTH: continue

                # [적용 2번] API 초당 호출 건수 제한 방어
                time.sleep(0.2) 

                volume_ratio = get_volume_ratio(code)
                if volume_ratio < required_volume_ratio: continue

                available_cash = get_available_cash()
                if available_cash < BUY_AMOUNT: break

                current_price = get_current_price(code)
                if not current_price or current_price <= 0: continue

                qty = BUY_AMOUNT // current_price
                if qty <= 0: continue

                if buy_market(code, name, qty):
                    ts = TrailingStop(entry_price=current_price)
                    with positions_lock:
                        positions[code] = {"name": name, "qty": qty, "entry_price": current_price, "ts": ts}
                    
                    msg = f"🟢 신규 진입\n종목: {name}\n진입가: {current_price:,}원\n수량: {qty}주\n현재 보유: {len(positions)}개"
                    send_discord(msg)
                    logging.info(f"Position opened: {name} @ {current_price}")

                time.sleep(0.5) 

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
            now = datetime.now()
            trail_end = now.replace(hour=TRAILING_END[0], minute=TRAILING_END[1], second=0)

            # 장 마감 일괄 청산
            if now >= trail_end:
                with positions_lock: codes = list(positions.keys())
                for code in codes:
                    with positions_lock:
                        if code not in positions: continue
                        pos = positions[code]
                    current_price = get_current_price(code)
                    if sell_market(code, pos["name"], pos["qty"]):
                        with positions_lock:
                            if code in positions: del positions[code]
                        save_trade(pos["name"], code, pos["entry_price"], current_price, pos["qty"], "장마감 강제청산")
                        send_discord(f"🏁 장 마감 청산: {pos['name']}")
                time.sleep(TRAILING_INTERVAL)
                continue

            # 실시간 감시
            with positions_lock: codes = list(positions.keys())
            for code in codes:
                with positions_lock:
                    if code not in positions: continue
                    pos = positions[code]

                current_price = get_current_price(code)
                if not current_price: continue

                signal, rate = pos["ts"].update(current_price)
                if signal == "HOLD": continue

                # [적용 1번] 매도를 결정했으면 즉시 감시 목록에서 지우고 스레드에 일을 던짐
                with positions_lock:
                    if code in positions:
                        del positions[code]

                threading.Thread(
                    target=execute_async_sell,
                    args=(code, pos["name"], pos["qty"], pos["entry_price"], current_price, signal),
                    daemon=True
                ).start()

                time.sleep(0.1)

        except Exception as e:
            logging.error(f"Trailing Error: {e}")
        time.sleep(TRAILING_INTERVAL)

# =========================
# 시작
# =========================

if __name__ == "__main__":
    logging.info("HTD Bot Start")
    send_discord("🚀 HTD 자동매매 봇 시작 (V1.4 - 스레드 최적화)")

    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t1.start()

    t2 = threading.Thread(target=trailing_loop, daemon=True)
    t2.start()

    t1.join()
    t2.join()