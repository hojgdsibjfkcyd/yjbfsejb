import os
import uuid
import json
import base64
import sqlite3
import subprocess
import threading
import time
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
        "inbounds": [{
            "port": XRAY_PORT,
            "listen": "0.0.0.0",
            "protocol": "vless",
            "settings": {
                "clients": clients,
                "decryption": "none"
            },
            "streamSettings": {
                "network": "ws",
                "wsSettings": {"path": "/pablo"}
            }
        }],
        "outbounds": [{"protocol": "freedom"}]
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

# ============ روت‌های پنل ============

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
        return render_template('login.html', error="نام کاربری یا رمز اشتباه است")
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
        return jsonify({"error": "این نام قبلاً ثبت شده"}), 400

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

    if not user:
        return "User not found", 404

    host = request.host.split(':')[0]
    config = (
        f"vless://{user_uuid}@{host}:443"
        f"?encryption=none&security=tls&sni={host}"
        f"&type=ws&host={host}&path=%2Fpablo"
        f"#PabloRail-{user['name']}"
    )
    encoded = base64.b64encode(config.encode()).decode()
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
    config = (
        f"vless://{user['uuid']}@{host}:443"
        f"?encryption=none&security=tls&sni={host}"
        f"&type=ws&host={host}&path=%2Fpablo"
        f"#PabloRail-{user['name']}"
    )
    sub_link = f"{request.host_url}sub/{user['uuid']}"
    return jsonify({"config": config, "sub": sub_link})

# ============ شروع برنامه ============
if __name__ == '__main__':
    init_db()
    threading.Thread(target=restart_xray, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
