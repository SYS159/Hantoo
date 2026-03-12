# Hantoo

## ⚙️ 설치
```bash
git clone https://github.com/SYS159/Hantoo.git
pip install requests schedule
cd Hantoo
```
## 🚀 실행
```bash
nohup python3 -u HM_v1_1.py > /dev/null 2>&1 &
nohup python3 -u HTD_v1_1.py > /dev/null 2>&1 &
```


## 📌 Main Code

**HM_v1_1** 
- 1시간마다 자산 확인 (장이 열려있을때만)
- 장시작, 장종료 알림

**HTD_v1_1**
- 급등주 오르면 5%이상 오르면 구매
- +3% 트레일링 -1%
- -2% 손절

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

**라즈베리파이 명령어들**
```bash
ps -ef | grep python

pkill -f HM_v1_1.py
nohup python3 -u HM_v1_1.py > /dev/null 2>&1 &

pkill -f HTD_v1_1.py
nohup python3 -u HTD_v1_1.py > /dev/null 2>&1 &

-u가 있어야 로그 실시간으로 작성함.
```
