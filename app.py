import os
import uuid
import base64
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/generate', methods=['POST'])
def generate_configs():
    data = request.json or {}
    
    domain = data.get('domain', '').strip()
    clean_ip = data.get('clean_ip', '').strip() or domain
    user_uuid = data.get('uuid', '').strip() or str(uuid.uuid4())
    path = data.get('path', '/?ed=2560').strip()
    port = data.get('port', '443')
    protocol = data.get('protocol', 'vless')
    name_prefix = data.get('name', 'Cloudflare').strip()

    if not domain:
        return jsonify({"status": "error", "message": "دامنه الزامی است"}), 400

    configs = []

    # 1. کانفیگ مستقیم با ساب‌دامنه
    c1 = f"{protocol}://{user_uuid}@{domain}:{port}?path={path}&security=tls&encryption=none&host={domain}&type=ws&sni={domain}#{name_prefix}-Direct"
    configs.append(c1)

    # 2. کانفیگ با آی‌پی تمیز
    if clean_ip and clean_ip != domain:
        c2 = f"{protocol}://{user_uuid}@{clean_ip}:{port}?path={path}&security=tls&encryption=none&host={domain}&type=ws&sni={domain}#{name_prefix}-CleanIP"
        configs.append(c2)

    # 3. کانفیگ بدون TLS پورت 80
    c3 = f"{protocol}://{user_uuid}@{clean_ip}:80?path={path}&security=none&encryption=none&host={domain}&type=ws#{name_prefix}-HTTP-80"
    configs.append(c3)

    return jsonify({
        "status": "success",
        "configs": configs,
        "uuid": user_uuid
    })

@app.route('/sub')
def sub():
    raw_configs = request.args.get('data', '')
    if not raw_configs:
        return "No configs provided", 400
    
    encoded = base64.b64encode(raw_configs.encode('utf-8')).decode('utf-8')
    return Response(encoded, mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
