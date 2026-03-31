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
LOG_NAME    = "HTD_v2_2.log"
ASSET_FILE  = "weekly_asset.json"
POSITIONS_FILE = "positions.json"
SOLD_FILE = "sold_today.json"

# =========================
# 전략 파라미터 (V2.2: 아침 20분 단기 결전 모드)
# =========================
BUY_AMOUNT = 450_000        
MIN_PRICE = 1000            

SCAN_INTERVAL = 10          
TRAILING_INTERVAL = 3       

MIN_CHANGE_RATE = 4.0       
MAX_CHANGE_RATE = 15.0      
MIN_REAL_CHANGE_RATE = 2.0  

EXCLUDE_KEYWORDS = ["인버스", "스팩", "ETN", "ETF", "타이거", "코덱스", "KODEX", "TIGER"]

VOLUME_RATIO = 0.02   

STOP_LOSS_RATE = -2.0       
TRAILING_TRIGGER = 2.0      
TRAILING_DROP = 1.5         

SCAN_START  = (9,  2)       
SCAN_END    = (9, 10)       
TRAILING_END = (15, 20)   

# =========================
# 로그 설정 및 전역 변수
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

sold_today = set()      
sold_lock = threading.Lock()

# =========================
# 장부(포지션 & 블랙리스트) 저장/불러오기 함수
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
    if not os.path.exists(POSITIONS_FILE): return
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
                    "ts": ts,
                    "selling": False # 재시작 시 매도 상태 초기화 (락 해제)
                }
        if positions:
            logging.info(f"💾 이전 보유 장부 복구 완료: {len(positions)}개 감시 재개")
    except Exception as e:
        logging.error(f"Positions Load Error: {e}")

def save_sold_today():
    with sold_lock:
        data = {
            "date": str(datetime.now().date()),
            "codes": list(sold_today)
        }
    try:
        with open(SOLD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Sold List Save Error: {e}")

def load_sold_today():
    if not os.path.exists(SOLD_FILE): return
    try:
        with open(SOLD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == str(datetime.now().date()):
            with sold_lock:
                for code in data.get("codes", []):
                    sold_today.add(code)
            logging.info(f"🚫 오늘 매도한 블랙리스트 복구 완료: {len(sold_today)}개 종목 접근 금지")
    except Exception as e:
        logging.error(f"Sold List Load Error: {e}")

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

# 💡 누락되었던 현재가 조회 함수 복구 완료!
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

def get_real_position_info(stock_code):
    """💡 찐 평단가와 수량을 가져오고, 수동 매도(잔고 0주)까지 감지하는 완벽한 함수"""
    token = get_token()
    if not token: return None, None
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = {"authorization": f"Bearer {token}", "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "TTTC8434R"}
    params = {
        "CANO": ACCOUNT[:8], "ACNT_PRDT_CD": ACCOUNT[8:], "AFHR_FLPR_YN": "N", "OFL_YN": "", 
        "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", 
        "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        if data.get("rt_cd") == "0":
            for item in data.get("output1", []):
                if stock_code in item["pdno"]: 
                    return float(item["pchs_avg_pric"]), int(item["hldg_qty"])
            # 💡 통신은 성공했는데 리스트에 내 주식이 없다면? = 이미 다 팔려서 0주가 된 상태!
            return 0.0, 0
    except Exception as e:
        logging.error(f"잔고 조회 에러: {e}")
    return None, None

def get_detailed_price(stock_code):
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
        if not output: return 0
        
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
        data = res.json()
        if data.get("rt_cd") == "0":
            return True
        else:
            logging.error(f"🔴 시장가 매도 거절 [{stock_name}]: {data.get('msg1')}")
            return False
    except Exception as e:
        logging.error(f"시장가 매도 통신 에러: {e}")
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
        data = res.json()
        
        output_list = data.get("output", [])
        if output_list is None: 
            output_list = []
            
        for item in output_list:
            if item["odno"] == order_no:
                return int(item.get("ncnl_qty", 1)) == 0
        return True 
    except Exception as e:
        logging.error(f"체결 확인 에러: {e}")
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
            
            if res_data.get("rt_cd") != "0": 
                logging.error(f"🟡 지정가 매도 거절 [{stock_name}]: {res_data.get('msg1')}")
                break
            
            order_no = res_data["output"].get("ODNO") or res_data["output"].get("odno")
            
            executed = False
            for _ in range(5): 
                time.sleep(1)
                if is_executed(order_no):
                    executed = True
                    break
            
            if executed: return True
            cancel_order(order_no)
            retry_count += 1
        except Exception as e:
            logging.error(f"스마트 매도 내부 에러: {e}")
            break
            
    logging.warning(f"[{stock_name}] 6회 지정가 실패 -> 최종 시장가 던짐")
    return sell_market(stock_code, stock_name, qty)

def execute_async_sell(code, name, qty, entry_price, trigger_price, signal):
    try:
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
            with sold_lock: sold_today.add(code)
            save_sold_today()
            
            with positions_lock:
                if code in positions: del positions[code]
            save_positions()
            
            msg = f"{emoji} {label} 완료\n종목: {name} ({code})\n진입가: {entry_price:,.0f}원\n청산가: {final_price:,.0f}원\n수익률: {rate:.2f}%"
            send_discord(msg)
            logging.info(f"Position closed ({label}): {name} {rate:.2f}%")
        else:
            # 💡 [수동 매도 감지 기능] 증권사가 거절하면 실제 잔고가 남아있는지 팩트 체크!
            _, real_qty = get_real_position_info(code)
            
            if real_qty == 0:
                # 네가 직접 팔아서 잔고가 없는 거라면 조용히 장부에서 지워줌
                with positions_lock:
                    if code in positions: del positions[code]
                save_positions()
                with sold_lock: sold_today.add(code)
                save_sold_today()
                
                send_discord(f"👻 [{name}] 수동 매도 감지! 잔고가 없어 봇의 장부에서 안전하게 삭제했습니다.")
                logging.warning(f"{name} 잔고 0주 확인 -> 장부 삭제 완료")
            else:
                # 잔고가 남았는데 거절당한 거라면 다음 루프에 다시 시도하도록 락만 풀어줌
                with positions_lock:
                    if code in positions: positions[code]["selling"] = False
                
                err_msg = f"🚨 매도 주문 실패!\n[{name}] {label} 주문을 증권사가 거절했습니다. 봇이 다음 턴에 다시 던집니다."
                send_discord(err_msg)
                logging.error(f"❌ {name} 매도 프로세스 실패 (다음 턴 재시도 대기)")
                
    except Exception as e:
        with positions_lock:
            if code in positions: positions[code]["selling"] = False
        logging.error(f"비동기 매도 실행 중 에러 발생: {e}")

# =========================
# 트레일링 스탑 클래스
# =========================
class TrailingStop:
    def __init__(self, entry_price, high_price=None, trailing_active=False):
        self.entry_price = float(entry_price)
        self.high_price = float(high_price) if high_price is not None else float(entry_price)
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
    
    current_date = datetime.now().date() 
    
    while True:
        try:
            now = datetime.now()
            check_weekly_reset()

            if current_date != now.date():
                with sold_lock:
                    sold_today.clear()
                save_sold_today() 
                current_date = now.date()
                logging.info("🌅 날짜가 변경되어 블랙리스트가 초기화되었습니다!")
                send_discord("🌅 새 아침이 밝았습니다! 매도 블랙리스트 초기화 완료.")

            start = now.replace(hour=SCAN_START[0], minute=SCAN_START[1], second=0)
            end   = now.replace(hour=SCAN_END[0], minute=SCAN_END[1], second=0)

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

            required_volume_ratio = VOLUME_RATIO 
            stocks = get_top_stocks()

            for stock in stocks:
                with positions_lock:
                    current_codes = set(positions.keys())
                with sold_lock:
                    blacklisted_codes = set(sold_today)

                code = stock.get("mksc_shrn_iscd", "")
                name = stock.get("hts_kor_isnm", "")
                change_rate = float(stock.get("prdy_ctrt", "0"))

                if code in current_codes or code in blacklisted_codes: 
                    continue
                if any(keyword in name for keyword in EXCLUDE_KEYWORDS):
                    continue
                if not (MIN_CHANGE_RATE <= change_rate <= MAX_CHANGE_RATE): 
                    continue

                time.sleep(0.2) 

                volume_ratio = get_volume_ratio(code)
                if volume_ratio < required_volume_ratio:
                    continue

                current_price, open_price = get_detailed_price(code)
                if not current_price or current_price < MIN_PRICE or not open_price or open_price <= 0: 
                    continue

                real_change_rate = ((current_price - open_price) / open_price) * 100
                if real_change_rate < MIN_REAL_CHANGE_RATE:
                    continue

                qty = BUY_AMOUNT // current_price
                if qty <= 0: continue

                # 매수 실행
                if buy_market(code, name, qty):
                    
                    real_entry_price = None
                    real_qty = qty
                    # 💡 잔고에 완벽히 들어올 때까지 최대 10초간 5번 끈질기게 물어봄!
                    for _ in range(5):
                        time.sleep(2) 
                        real_entry_price, real_qty_api = get_real_position_info(code)
                        if real_entry_price and real_entry_price > 0 and real_qty_api and real_qty_api > 0:
                            real_qty = real_qty_api
                            break
                    
                    if not real_entry_price or real_entry_price == 0:
                        real_entry_price = float(current_price)
                        logging.warning(f"⚠️ {name} 잔고 조회 계속 실패 -> 예상가 {current_price}원 사용")

                    ts = TrailingStop(entry_price=real_entry_price)
                    with positions_lock:
                        positions[code] = {
                            "name": name, 
                            "qty": real_qty, 
                            "entry_price": real_entry_price, 
                            "ts": ts,
                            "selling": False # 락 해제 상태로 감시 시작
                        }
                    
                    save_positions()
                    
                    msg = f"🟢 신규 진입\n종목: {name}\n진입가(실제): {real_entry_price:,.0f}원\n수량: {real_qty}주\n현재 보유: {len(positions)}개"
                    send_discord(msg)
                    logging.info(f"Position opened: {name} @ {real_entry_price} (예상가 {current_price}에서 보정됨)")
                    
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
                        
                        with sold_lock:
                            sold_today.add(code)
                        save_sold_today()
                        
                        send_discord(f"🏁 장 마감 청산: {pos['name']}")
                time.sleep(TRAILING_INTERVAL)
                continue

            # 실시간 감시
            with positions_lock: codes = list(positions.keys())
            for code in codes:
                with positions_lock:
                    if code not in positions: continue
                    pos = positions[code]
                    
                    # 💡 봇이 이미 매도 주문을 넣은 상태라면 중복 주문 방지!
                    if pos.get("selling", False): 
                        continue

                current_price = get_current_price(code)
                if not current_price: continue

                signal, rate, changed = pos["ts"].update(current_price)
                
                if changed:
                    save_positions()

                if signal == "HOLD": continue

                # 💡 익절/손절 신호가 오면, "나 지금 파는 중이야!" 하고 자물쇠(Lock)를 채움
                with positions_lock:
                    if code in positions:
                        positions[code]["selling"] = True
                
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
    
    load_positions()
    load_sold_today()
    
    msg = f"🚀 HTD 자동매매 봇 시작 (V2.2 - 현재가 복구 및 수동 매도 감지 패치)\n현재 감시: {len(positions)}개 / 오늘 제외: {len(sold_today)}개"
    send_discord(msg)

    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t1.start()

    t2 = threading.Thread(target=trailing_loop, daemon=True)
    t2.start()

    t1.join()
    t2.join()