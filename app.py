import os
import uuid
import json
import base64
import sqlite3
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ======== تنظیمات اصلی =========
ADMIN_USERNAME = os.environ.get("ADMIN_USER", "pablo")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "pablo123")
XRAY_PORT = 10000
DB_PATH = "/app/users.db"
XRAY_CONFIG_PATH = "/app/xray_config.json"
# ================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        uuid TEXT UNIQUE NOT NULL,
        quota_gb REAL DEFAULT 0,
        used_bytes INTEGER DEFAULT 0,
        expire_days INTEGER DEFAULT 30,
        created_at TEXT,
        enabled INTEGER DEFAULT 1
    )''')
    conn.commit()
    conn.close()

def get_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY id DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def build_xray_config():
    users = get_users()
    clients = []
    for u in users:
        if u['enabled'] == 1:
            clients.append({"id": u['uuid'], "email": u['name']})

    config = {
        "log": {"loglevel": "warning"},
        "api": {
            "tag": "api",
            "services": ["StatsService"]
        },
        "stats": {},
        "inbounds": [
            {
                "port": XRAY_PORT,
                "listen": "127.0.0.1",
                "protocol": "vless",
                "settings": {
                    "clients": clients,
                    "decryption": "none"
                },
                "streamSettings": {
                    "network": "ws",
                    "wsSettings": {"path": "/ws"}
                },
                "tag": "vless-inbound"
            },
            {
                "listen": "127.0.0.1",
                "port": 10001,
                "protocol": "dokodemo-door",
                "settings": {
                    "address": "127.0.0.1"
                },
                "tag": "api"
            }
        ],
        "outbounds": [{"protocol": "freedom"}],
        "routing": {
            "rules": [
                {
                    "inboundTag": ["api"],
                    "outboundTag": "api",
                    "type": "field"
                }
            ]
        }
    }
    with open(XRAY_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

def restart_xray():
    build_xray_config()
    try:
        subprocess.run(["pkill", "-f", "xray"], check=False)
        time.sleep(1)
    except Exception:
        pass
    subprocess.Popen(
        ["/usr/local/bin/xray/xray", "run", "-c", XRAY_CONFIG_PATH],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def start_caddy():
    port = os.environ.get("PORT", "8080")
    caddyfile_content = f""":{{port}} {{
        rewrite /ws/* /ws
        reverse_proxy /ws 127.0.0.1:10000
        reverse_proxy 127.0.0.1:8888
    }}"""
    with open("/app/Caddyfile", "w") as f:
        f.write(caddyfile_content.replace("{port}", port))
    
    try:
        subprocess.run(["pkill", "-f", "caddy"], check=False)
    except:
        pass
    subprocess.Popen(
        ["/usr/local/bin/caddy", "run", "--config", "/app/Caddyfile", "--adapter", "caddyfile"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def update_stats_loop():
    while True:
        time.sleep(20)
        try:
            res = subprocess.run(
                ["/usr/local/bin/xray/xray", "api", "statsquery", "--server=127.0.0.1:10001"],
                capture_output=True, text=True, check=False
            )
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                stats_list = data.get("stat", [])
                user_traffic = {}
                for item in stats_list:
                    name = item.get("name", "")
                    value = int(item.get("value", 0))
                    if "user>>>" in name:
                        parts = name.split(">>>")
                        email = parts[1]
                        user_traffic[email] = user_traffic.get(email, 0) + value
                
                if user_traffic:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    for email, bytes_used in user_traffic.items():
                        c.execute("UPDATE users SET used_bytes=? WHERE name=?", (bytes_used, email))
                    conn.commit()
                    conn.close()
        except Exception:
            pass

def make_all_vless_configs(user, host):
    created_dt = datetime.fromisoformat(user['created_at'])
    elapsed_days = (datetime.now() - created_dt).days
    days_left = max(0, user['expire_days'] - elapsed_days)
    
    used_gb = round(user['used_bytes'] / (1024**3), 2)
    quota_gb = user['quota_gb']
    remaining_gb = max(0.0, round(quota_gb - used_gb, 2))
    u_uuid = user['uuid']
    name = user['name']

    status_tag = f"{used_gb}G/{quota_gb}G ({remaining_gb}G) | {days_left}d"

    configs = []

    # 1. کانفیگ مستقیم و پایدار (Chrome)
    r1 = urllib.parse.quote(f"pablo-{name} [Direct] | {status_tag}")
    c1 = f"vless://{u_uuid}@{host}:443?path=%2Fws%2F{u_uuid}&security=tls&alpn=http%2F1.1&encryption=none&insecure=0&host={host}&fp=chrome&type=ws&allowInsecure=0&sni={host}#{r1}"
    configs.append({"title": "🚀 کانفیگ اصلی (Chrome TLS)", "tag": "پیشنهادی برای همه اپراتورها", "config": c1})

    # 2. کانفیگ EarlyData ضد فیلتر (مخصوص همراه اول)
    r2 = urllib.parse.quote(f"pablo-{name} [EarlyData] | {status_tag}")
    c2 = f"vless://{u_uuid}@{host}:443?path=%2Fws%2F{u_uuid}%3Fed%3D2560&security=tls&alpn=http%2F1.1&encryption=none&insecure=0&host={host}&fp=chrome&type=ws&allowInsecure=0&sni={host}#{r2}"
    configs.append({"title": "⚡ کانفیگ EarlyData (ضد فیلتر)", "tag": "عالی برای همراه اول و پکت‌لاس", "config": c2})

    # 3. کانفیگ فایرفاکس / مالتی ALPN (مخصوص مخابرات و ADSL)
    r3 = urllib.parse.quote(f"pablo-{name} [Firefox] | {status_tag}")
    c3 = f"vless://{u_uuid}@{host}:443?path=%2Fws%2F{u_uuid}&security=tls&alpn=h2%2Chttp%2F1.1&encryption=none&insecure=0&host={host}&fp=firefox&type=ws&allowInsecure=0&sni={host}#{r3}"
    configs.append({"title": "🛡️ کانفیگ Firefox / H2", "tag": "عالی برای وای‌فای، مخابرات و ایرانسل", "config": c3})

    # 4. کانفیگ سافاری و iOS
    r4 = urllib.parse.quote(f"pablo-{name} [Safari] | {status_tag}")
    c4 = f"vless://{u_uuid}@{host}:443?path=%2Fws%2F{u_uuid}&security=tls&alpn=http%2F1.1&encryption=none&insecure=0&host={host}&fp=safari&type=ws&allowInsecure=0&sni={host}#{r4}"
    configs.append({"title": "📱 کانفیگ Safari / iOS", "tag": "مناسب دستگاه‌های اپل و رایتل", "config": c4})

    # 5. کانفیگ پورت 80 بدون TLS (برای زمان اختلال شدید اینترنت)
    r5 = urllib.parse.quote(f"pablo-{name} [HTTP-80] | {status_tag}")
    c5 = f"vless://{u_uuid}@{host}:80?path=%2Fws%2F{u_uuid}&security=none&encryption=none&host={host}&type=ws#{r5}"
    configs.append({"title": "🌐 کانفیگ بدون TLS (پورت 80)", "tag": "زمان قطعی شدید TLS", "config": c5})

    return configs

# ============ روت‌ها ============

@app.route('/')
def home():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username', '')
        p = request.form.get('password', '')
        if u == ADMIN_USERNAME and p == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="نام کاربری یا رمز عبور اشتباه است")
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'admin' not in session:
        return redirect(url_for('login'))
    users = get_users()
    total_gb = sum(u['quota_gb'] for u in users)
    total_used = sum(u['used_bytes'] for u in users) / (1024**3)
    return render_template('dashboard.html',
                           users=users,
                           total_users=len(users),
                           total_gb=round(total_gb, 2),
                           total_used=round(total_used, 2))

@app.route('/api/add_user', methods=['POST'])
def add_user():
    if 'admin' not in session:
        return jsonify({"error": "unauthorized"}), 401
    data = request.json or {}
    name = data.get('name', '').strip()
    quota = float(data.get('quota', 10))
    days = int(data.get('days', 30))

    if not name:
        return jsonify({"error": "نام الزامی است"}), 400

    user_uuid = str(uuid.uuid4())
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (name, uuid, quota_gb, expire_days, created_at) VALUES (?,?,?,?,?)",
            (name, user_uuid, quota, days, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        restart_xray()
        return jsonify({"status": "ok", "uuid": user_uuid})
    except sqlite3.IntegrityError:
        return jsonify({"error": "این نام کاربری قبلاً ثبت شده است"}), 400

@app.route('/api/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if 'admin' not in session:
        return jsonify({"error": "unauthorized"}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    restart_xray()
    return jsonify({"status": "ok"})

@app.route('/api/toggle_user/<int:user_id>', methods=['POST'])
def toggle_user(user_id):
    if 'admin' not in session:
        return jsonify({"error": "unauthorized"}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT enabled FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if row:
        new_val = 0 if row[0] == 1 else 1
        c.execute("UPDATE users SET enabled=? WHERE id=?", (new_val, user_id))
        conn.commit()
    conn.close()
    restart_xray()
    return jsonify({"status": "ok"})

@app.route('/sub/<user_uuid>')
def subscription(user_uuid):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE uuid=?", (user_uuid,))
    user = c.fetchone()
    conn.close()

    if not user or user['enabled'] == 0:
        return "User not found or disabled", 404

    host = request.host.split(':')[0]
    all_configs = make_all_vless_configs(user, host)
    raw_text = "\n".join([item['config'] for item in all_configs])
    encoded = base64.b64encode(raw_text.encode()).decode()
    return Response(encoded, mimetype='text/plain')

@app.route('/api/user_config/<int:user_id>')
def user_config(user_id):
    if 'admin' not in session:
        return jsonify({"error": "unauthorized"}), 401
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    if not user:
        return jsonify({"error": "not found"}), 404

    host = request.host.split(':')[0]
    configs = make_all_vless_configs(user, host)
    sub_link = f"{request.host_url}sub/{user['uuid']}"
    return jsonify({"configs": configs, "sub": sub_link})

if __name__ == '__main__':
    init_db()
    start_caddy()
    threading.Thread(target=restart_xray, daemon=True).start()
    threading.Thread(target=update_stats_loop, daemon=True).start()
    app.run(host='127.0.0.1', port=8888)
