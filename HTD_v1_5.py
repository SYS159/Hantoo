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
LOG_NAME    = "HTD_v1_5.log"
ASSET_FILE  = "weekly_asset.json"
POSITIONS_FILE = "positions.json" # 현재 보유 종목 기록 장부

# =========================
# 전략 파라미터 (상승 초입 스나이핑 세팅)
# =========================
BUY_AMOUNT = 200_000        # 종목당 매수 금액 (원)

SCAN_INTERVAL = 10          
TRAILING_INTERVAL = 3       

# 💡 [핵심 타점] 어제 종가 대비 3.5% ~ 7.0% 구간만 노림 (추격 매수 절대 금지)
MIN_CHANGE_RATE = 3.5       
MAX_CHANGE_RATE = 7.0      

# 💡 [핵심 타점 2] 오늘 아침 시가(Open) 대비 최소 2% 이상 상승 중이어야 함 (갭 띄우고 흐르는 놈 방지)
MIN_REAL_CHANGE_RATE = 2.0  

EXCLUDE_KEYWORDS = ["인버스", "스팩", "ETN", "ETF", "타이거", "코덱스", "KODEX", "TIGER"]

VOLUME_RATIO_EARLY = 0.1    # 09:02 ~ 09:20: 전일 거래량의 10% 돌파
VOLUME_RATIO_LATE  = 0.3    # 09:20 ~ 09:40: 전일 거래량의 30% 돌파

STOP_LOSS_RATE = -2.0       # 칼손절
TRAILING_TRIGGER = 3.0      # 3% 수익부터 트레일링 시작
TRAILING_DROP = 1.5         # 고점 대비 1.5% 빠지면 익절

SCAN_START  = (9,  2)       
SCAN_MID    = (9, 20)       
SCAN_END    = (9, 40)      
TRAILING_END = (15, 20)     

# =========================
# 로그 설정
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_NAME, encoding='utf-8'), 
        logging.StreamHandler()                          
    ]
)

positions = {}          
positions_lock = threading.Lock()

# =========================
# 장부(포지션) 저장 & 불러오기 함수
# =========================
def save_positions():
    data_to_save = {}
    with positions_lock:
        for code, pos in positions.items():
            data_to_save[code] = {
                "name": pos["name"],
                "qty": pos["qty"],
                "entry_price": pos["entry_price"],
                "high_price": pos["ts"].high_price,
                "trailing_active": pos["ts"].trailing_active
            }
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Positions Save Error: {e}")

def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with positions_lock:
            for code, info in data.items():
                ts = TrailingStop(info["entry_price"], info.get("high_price"), info.get("trailing_active", False))
                positions[code] = {
                    "name": info["name"],
                    "qty": info["qty"],
                    "entry_price": info["entry_price"],
                    "ts": ts
                }
        if positions:
            logging.info(f"💾 이전 매수 장부 복구 완료: {len(positions)}개 종목 감시 재개")
    except Exception as e:
        logging.error(f"Positions Load Error: {e}")

# =========================
# 공통 함수 
# =========================
def check_weekly_reset():
    now = datetime.now()
    if now.weekday() == 0 and now.hour == 9 and 0 <= now.minute < 5:
        current_cash = get_available_cash() 
        with open(ASSET_FILE, "w") as f:
            json.dump({"base_asset": current_cash, "date": str(now.date())}, f)
        send_discord(f"📅 주간 수익률 초기화 완료\n기준 자산: {current_cash:,}원")
        logging.info(f"Weekly reset: {current_cash}원")

def send_discord(msg):
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
    except:
        pass

def get_token():
    if not os.path.exists(TOKEN_FILE): return None
    with open(TOKEN_FILE) as f: data = json.load(f)
    if datetime.now() >= datetime.strptime(data["expire"], "%Y-%m-%d %H:%M:%S"): return None
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
    params = {"fid_rsfl_rate2": "", "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20172", "fid_input_iscd": "0000", "fid_rank_sort_cls_code": "0", "fid_input_cnt_1": "0", "fid_prc_cls_code": "1", "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0", "fid_div_cls_code": "0", "fid_rsfl_rate1": ""}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        return res.json().get("output", []) if res.status_code == 200 else []
    except:
        return []

def get_current_price(stock_code):
    """현재가만 가져오는 함수 (트레일링 스탑용)"""
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

def get_detailed_price(stock_code):
    """💡 [신규] 현재가와 오늘 아침 시가를 동시에 가져오는 함수 (스캐너용)"""
    token = get_token()
    if not token: return None, None
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "FHKST01010100"}
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        if data.get("rt_cd") == "0":
            current = int(data["output"]["stck_prpr"])
            open_p = int(data["output"]["stck_oprc"])
            return current, open_p
        return None, None
    except:
        return None, None

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
            # 💡 [수정] 10초 대기에서 5초 대기로 단축!
            for _ in range(5): 
                time.sleep(1)
                if is_executed(order_no):
                    executed = True
                    break
            
            if executed: return True
            
            # 5초간 안 팔리면 즉시 취소하고 다음 가격으로 재시도
            cancel_order(order_no)
            retry_count += 1
            logging.info(f"[{stock_name}] 매도 미체결 -> 가격 재조정 중 ({retry_count}/6)")
        except:
            break
            
    logging.warning(f"{stock_name} 지정가 실패 -> 최종 시장가 던짐")
    return sell_market(stock_code, stock_name, qty)

def execute_async_sell(code, name, qty, entry_price, trigger_price, signal):
    if signal == "STOP_LOSS":
        success = sell_market(code, name, qty)
        label, emoji = "손절", "🔴"
    else:
        success = sell_smart(code, name, qty)
        label, emoji = "익절 (트레일링)", "🟡"

    if success:
        final_price = get_current_price(code) or trigger_price
        rate = (final_price - entry_price) / entry_price * 100
        
        save_trade(name, code, entry_price, final_price, qty, label)
        msg = f"{emoji} {label} 완료\n종목: {name} ({code})\n진입가: {entry_price:,}원\n청산가: {final_price:,}원\n수익률: {rate:.2f}%"
        send_discord(msg)
        logging.info(f"Position closed ({label}): {name} {rate:.2f}%")

# =========================
# 트레일링 스탑 클래스
# =========================
class TrailingStop:
    def __init__(self, entry_price, high_price=None, trailing_active=False):
        self.entry_price = entry_price
        self.high_price = high_price if high_price is not None else entry_price
        self.trailing_active = trailing_active

    def update(self, current_price):
        state_changed = False
        rate = (current_price - self.entry_price) / self.entry_price * 100
        
        if not self.trailing_active:
            if rate <= STOP_LOSS_RATE: return "STOP_LOSS", rate, state_changed
            if rate >= TRAILING_TRIGGER:
                self.trailing_active = True
                self.high_price = current_price
                state_changed = True
                
        if self.trailing_active:
            if current_price > self.high_price:
                self.high_price = current_price
                state_changed = True
            drop_from_high = (self.high_price - current_price) / self.high_price * 100
            if drop_from_high >= TRAILING_DROP:
                return "TRAILING_STOP", rate, state_changed
                
        return "HOLD", rate, state_changed

# =========================
# 스캐너 루프
# =========================
def scanner_loop():
    logging.info("Scanner loop started")
    while True:
        try:
            now = datetime.now()
            check_weekly_reset()

            start = now.replace(hour=SCAN_START[0], minute=SCAN_START[1], second=0)
            end   = now.replace(hour=SCAN_END[0], minute=SCAN_END[1], second=0)
            mid   = now.replace(hour=SCAN_MID[0], minute=SCAN_MID[1], second=0)

            if not (start <= now <= end):
                time.sleep(SCAN_INTERVAL)
                continue

            available_cash = get_available_cash()
            if available_cash == 0:
                logging.warning("⚠️ 예수금 조회 실패 또는 잔고 0원! API 세팅(.env)을 확인하세요.")
                time.sleep(SCAN_INTERVAL)
                continue
            elif available_cash < BUY_AMOUNT:
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

                if code in current_codes: continue
                
                # 💡 [필터 1] 금지어 포함 시 즉시 탈락
                if any(keyword in name for keyword in EXCLUDE_KEYWORDS):
                    continue
                
                # 💡 [필터 2] 등락률 3.5 ~ 7.0% 밖이면 패스 (추격 매수 방지)
                if not (MIN_CHANGE_RATE <= change_rate <= MAX_CHANGE_RATE): 
                    continue

                time.sleep(0.2) 

                # 💡 [필터 3] 거래량 확인
                volume_ratio = get_volume_ratio(code)
                if volume_ratio < required_volume_ratio:
                    continue

                # 💡 [필터 4] 시가 및 현재가 동시 조회 (찐상승 필터링)
                current_price, open_price = get_detailed_price(code)
                if not current_price or current_price <= 0 or not open_price or open_price <= 0: 
                    continue

                # 시가 대비 상승률 계산
                real_change_rate = ((current_price - open_price) / open_price) * 100
                
                if real_change_rate < MIN_REAL_CHANGE_RATE:
                    # 로그 확인용: 갭 띄우고 흐르는 종목이 탈락하는 걸 볼 수 있음
                    # logging.info(f"[{name}] ❌ 탈락: 시초가 갭 띄우고 지지부진 (시가대비 {real_change_rate:.2f}%)")
                    continue

                qty = BUY_AMOUNT // current_price
                if qty <= 0: continue

                # 💡 매수 실행
                if buy_market(code, name, qty):
                    ts = TrailingStop(entry_price=current_price)
                    with positions_lock:
                        positions[code] = {"name": name, "qty": qty, "entry_price": current_price, "ts": ts}
                    
                    save_positions() # 장부에 즉시 기록
                    
                    msg = f"🟢 신규 진입\n종목: {name}\n진입가: {current_price:,}원\n수량: {qty}주\n현재 보유: {len(positions)}개"
                    send_discord(msg)
                    logging.info(f"Position opened: {name} @ {current_price} (시가대비 +{real_change_rate:.2f}%)")
                    
                    available_cash -= (current_price * qty)
                    if available_cash < BUY_AMOUNT:
                        break 

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
                        
                        save_positions()
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

                signal, rate, changed = pos["ts"].update(current_price)
                
                if changed:
                    save_positions()

                if signal == "HOLD": continue

                with positions_lock:
                    if code in positions: del positions[code]
                
                save_positions()

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
    
    # 💡 시작 시 장부에서 기억 복원
    load_positions()
    
    msg = f"🚀 HTD 자동매매 봇 시작 (V1.5 - 시가돌파 스나이퍼)\n현재 보유 중인 종목: {len(positions)}개"
    send_discord(msg)

    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t1.start()

    t2 = threading.Thread(target=trailing_loop, daemon=True)
    t2.start()

    t1.join()
    t2.join()