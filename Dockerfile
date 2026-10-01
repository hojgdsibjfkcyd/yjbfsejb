FROM python:3.10-slim

WORKDIR /app

# نصب پیش‌نیازها و دانلود Xray Core
RUN apt-get update && apt-get install -y wget unzip curl && \
    wget https://github.com/XTLS/Xray-core/releases/download/v1.8.11/Xray-linux-64.zip && \
    unzip Xray-linux-64.zip -d /usr/local/bin/xray && \
    chmod +x /usr/local/bin/xray/xray && \
    rm Xray-linux-64.zip && \
    apt-get clean

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
