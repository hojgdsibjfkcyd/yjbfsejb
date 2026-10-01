FROM python:3.10-slim

WORKDIR /app

# نصب ابزارها و دانلود نسخه‌های پایدار Caddy و Xray Core
RUN apt-get update && apt-get install -y wget unzip curl procps && \
    # دانلود Caddy
    wget -q https://github.com/caddyserver/caddy/releases/download/v2.7.6/caddy_2.7.6_linux_amd64.tar.gz && \
    tar -zxvf caddy_2.7.6_linux_amd64.tar.gz caddy && \
    mv caddy /usr/local/bin/caddy && \
    chmod +x /usr/local/bin/caddy && \
    rm caddy_2.7.6_linux_amd64.tar.gz && \
    # دانلود Xray
    wget -q https://github.com/XTLS/Xray-core/releases/download/v1.8.11/Xray-linux-64.zip && \
    unzip -q Xray-linux-64.zip -d /usr/local/bin/xray && \
    chmod +x /usr/local/bin/xray/xray && \
    rm Xray-linux-64.zip && \
    apt-get clean

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
