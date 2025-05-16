# 1) 베이스 이미지
FROM python:3.10-slim

# 2) 시스템 패키지 설치 (ffmpeg, font, MySQL client 등)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-nanum \
      libgl1-mesa-glx \
      default-libmysqlclient-dev \
      dos2unix && \
    rm -rf /var/lib/apt/lists/*

# 3) 작업 디렉터리
WORKDIR /app

# 4) 파이썬 의존성 복사 & 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5) 애플리케이션 코드 복사
COPY . .

RUN dos2unix run_flask.sh && chmod +x run_flask.sh

# 6) static/uploads, outputs, snaps 디렉터리 생성
RUN mkdir -p static/uploads static/outputs static/snaps

# 7) 포트 노출
EXPOSE 5000

# 8) 엔트리포인트
CMD ["bash","run_flask.sh"]
