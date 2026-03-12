# Hantoo

## ⚙️ 설치
```bash
git clone https://github.com/SYS159/Hantoo.git
pip install requests schedule
cd Hantoo
```
## 🚀 실행
```bash
nohup python3 -u M_v1_1.py > M_v1_1.log 2>&1 &
```


## 📌 Main Code

**M_v1_1** 
- 


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

pkill -f TD_v5_4.py
nohup python3 -u TD_v5_4.py > TD_v5_4.log 2>&1 &

pkill -f M_v3_4.py
nohup python3 -u  M_v3_4.py > M_v3_4.log 2>&1 &

-u가 있어야 로그 실시간으로 작성함.
```
