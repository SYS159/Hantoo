# Hantoo

## ⚙️ 설치
```bash
git clone https://github.com/SYS159/Hantoo.git
pip install requests schedule
cd Hantoo
```
## 🚀 실행
```bash
ps -ef | grep python
nohup python3 -u HM_v1_2.py > /dev/null 2>&1 &
nohup python3 -u HTD_v1_4.py > /dev/null 2>&1 &
```

## 정지
```bash
pkill -f HM_v1_2.py
pkill -f HTD_v1_4.py
```


## 📌 Main Code

**HM_v1_1** 
- 1시간마다 자산 확인 (장이 열려있을때만)
- 장시작, 장종료 알림

**HM_v1_2**
- 매주 월요일 CSV파일 읽어서 주간보고

**HTD_v1_1**
- 급등주 오르면 5%이상 오르면 구매
- +3% 트레일링 -1%
- -2% 손절

**HTD_v1_2**
```bash
[매수 - 09:05 ~ 10:30]
등락률 +5% 이상
체결강도 120 이상
거래량 배율 09:05~09:30 → 2배 이상
           09:30~10:30 → 3배 이상
예수금 10만원 이상
→ 10만원어치 매수

[매도 - 트레일링]
진입 후 -2% → 손절
진입 후 +3% 도달 → 트레일링 활성화
최고가 대비 -1% → 익절

[강제청산 - 15:20]
보유 종목 전부 시장가 청산

[CSV 파일 생성]
날짜                 종목명   종목코드  매수가  매도가  수량  수익금(원)  수익률(%)  매도사유
```

**HTD_v1_3.py**
- 슬리피지 방지

**HTD_v1_4.py**
- 손절은 시장가
- 트레일링 루프 백그라운드로
- API 과호출 수정

---

## 💻 자주 쓰는 명령어

**파일 올리기 (push)**
```bash
git add 파일명.py
git commit -m "메시지"
git push
```

**파일 내리기 (pull)**
```bash
git pull
```