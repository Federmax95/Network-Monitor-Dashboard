from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess, datetime
import psutil
import time
import re
import platform
import speedtest

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
db = SQLAlchemy(app)

# -----------------------------
# Database models
# -----------------------------
class Host(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True)


class PingResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)
    host = db.Column(db.String(50))
    latency = db.Column(db.Float)
    status = db.Column(db.String(10))

# -----------------------------
# Ping function
# -----------------------------
def ping_host(host="8.8.8.8"):
    with app.app_context():
        try:
            if platform.system().lower() == "windows":
                command = ["ping", "-n", "1", host]
            else:
                command = ["ping", "-c", "1", host]

            result = subprocess.run(command, capture_output=True, text=True, timeout=3)
            latency = None

            if result.returncode == 0:
                status = "OK"
                # Match ping time (supports different languages)
                match = re.search(r"(?:time|tempo|durata)[=<]\s?(\d+(?:\.\d+)?)\s?ms", result.stdout)
                
                if match:
                    latency = float(match.group(1))
                else:
                    latency = None
                    print("--- PING PARSE ERROR ---")
                    print("Could not find 'time=', 'tempo=' or 'durata=' in output:")
                    print(result.stdout)
                    print("------------------------")
            else:
                status = "FAIL"

        except Exception as e:
            print("Ping error:", e)
            latency = None
            status = "FAIL"

        db.session.add(PingResult(host=host, latency=latency, status=status))
        db.session.commit()

# -----------------------------
# Network speed monitoring
# -----------------------------
current_network_stats = {}

def update_network_speed():
    global current_network_stats

    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        download_speed = st.download() / 1_000_000
        upload_speed = st.upload() / 1_000_000

        ssid = "N/A"
        if platform.system().lower() == "windows":
            try:
                output = subprocess.check_output(
                    ["netsh", "wlan", "show", "interfaces"], text=True
                )
                match = re.search(r"SSID\s+: (.+)", output)
                if match and match.group(1).strip():
                    ssid = match.group(1).strip()
            except:
                pass

        current_network_stats = {
            "ssid": ssid,
            "download": round(download_speed, 2),
            "upload": round(upload_speed, 2)
        }

    except Exception as e:
        print("Speedtest error:", e)
        current_network_stats = {
            "ssid": "N/A",
            "download": 0.0,
            "upload": 0.0
        }

# -----------------------------
# Scheduled jobs
# -----------------------------
def scheduled_ping():
    with app.app_context():
        hosts = Host.query.all()
        for h in hosts:
            ping_host(h.name)

PING_INTERVAL = 60
PING_INTERVAL = 60
global scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_ping, 'interval', seconds=PING_INTERVAL, id='scheduled_ping')
scheduler.add_job(update_network_speed, 'interval', seconds=180, id='update_network')
scheduler.start()


# -----------------------------
# Routes
# -----------------------------
@app.route('/')
def dashboard():
    hosts_count = Host.query.count()
    ping_count = PingResult.query.count()

    if hosts_count == 0 and ping_count == 0:
        return render_template('add_host.html')
    
    data = PingResult.query.order_by(PingResult.timestamp.desc()).limit(30).all()
    return render_template('dashboard.html', data=data)

@app.route('/api/data')
def api_data():
    data_by_host = {}
    hosts = Host.query.all()
    
    for host in hosts:
        results = PingResult.query.filter_by(host=host.name).order_by(PingResult.timestamp.desc()).limit(30).all()
        data_by_host[host.name] = [
            {"timestamp": r.timestamp.isoformat(), "latency": r.latency, "status": r.status, "host": r.host}
            for r in results
        ]
    return jsonify(data_by_host)

@app.route('/clear', methods=['POST'])
def clear_data():
    with app.app_context():
        num_rows = PingResult.query.delete()
        db.session.commit()
        return jsonify({"message": f"{num_rows} record(s) deleted."})

@app.route('/api/network')
def api_network():
    global current_network_stats
    return jsonify(current_network_stats)

@app.route('/api/remove_host/<host>', methods=['DELETE'])
def remove_host(host):
    h = Host.query.filter_by(name=host).first()
    if not h:
        return jsonify({"error": "Host not found"}), 404
    PingResult.query.filter_by(host=host).delete()
    db.session.delete(h)
    db.session.commit()
    return jsonify({"message": f"{host} removed."})

@app.route('/api/clear_host/<host>', methods=['POST'])
def clear_host_data(host):
    deleted = PingResult.query.filter_by(host=host).delete()
    db.session.commit()
    return jsonify({"message": f"{deleted} record(s) deleted for {host}."})

@app.route('/api/add_host', methods=['POST'])
def add_host():
    data = request.json
    host_name = data.get("host")
    if not host_name:
        return jsonify({"error": "Host is empty"}), 400

    if Host.query.filter_by(name=host_name).first():
        return jsonify({"error": "Host is already monitored"}), 400
    
    new_host = Host(name=host_name)
    db.session.add(new_host)
    db.session.commit()
    return jsonify({"message": f"{host_name} added for monitoring"})

@app.route('/api/set_interval', methods=['POST'])
def set_ping_interval():
    data = request.json
    interval = data.get("interval", 10)
    try:
        scheduler.remove_job('scheduled_ping')
    except Exception as e:
        print(e)

    scheduler.add_job(scheduled_ping, 'interval', seconds=interval, id='scheduled_ping')

    return jsonify({"message": f"Ping interval updated to {interval} seconds"})



# -----------------------------
# Main
# -----------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
