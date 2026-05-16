#!/usr/bin/env python3
"""
AntiGravity Mobile Bridge Server
スマホから同一LAN上のAntiGravityチャットへアクセスするためのブリッジサーバー
"""

import json
import os
import queue
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import anthropic
except ImportError:
    print("[ERROR] anthropic パッケージが必要です: pip install anthropic")
    exit(1)

# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

config = load_config()
PORT = config.get("port", 8765)
OUTPUT_MD = Path(config.get("output_md_path", "./output.md"))
API_KEY = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = config.get("model", "claude-sonnet-4-6")
SYSTEM_PROMPT = config.get("system_prompt", "You are a helpful assistant.")

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# グローバル状態
# ---------------------------------------------------------------------------
conversation_history = []        # Claude API用の会話履歴
history_lock = threading.Lock()

message_queue = queue.Queue()    # スマホからのメッセージキュー
sse_clients = []                 # SSE接続中のクライアント
sse_lock = threading.Lock()

# ---------------------------------------------------------------------------
# SSE通知
# ---------------------------------------------------------------------------
def push_sse(event_data: dict):
    payload = f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)

# ---------------------------------------------------------------------------
# AntiGravity: メッセージ処理スレッド
# ---------------------------------------------------------------------------
def antigravity_worker():
    client = anthropic.Anthropic(api_key=API_KEY)
    while True:
        user_message = message_queue.get()
        if user_message is None:
            break

        with history_lock:
            conversation_history.append({"role": "user", "content": user_message})
            messages_snapshot = list(conversation_history)

        push_sse({"type": "thinking"})

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=8096,
                system=SYSTEM_PROMPT,
                messages=messages_snapshot,
            )
            reply = response.content[0].text
        except Exception as e:
            reply = f"[エラー] {e}"

        with history_lock:
            conversation_history.append({"role": "assistant", "content": reply})

        write_output_md()
        push_sse({"type": "update"})

def write_output_md():
    """会話履歴全体をMDファイルに書き出す"""
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# AntiGravity Chat\n"]
    with history_lock:
        for msg in conversation_history:
            role_label = "**あなた**" if msg["role"] == "user" else "**AntiGravity**"
            lines.append(f"{role_label}\n\n{msg['content']}\n\n---\n")
    OUTPUT_MD.write_text("".join(lines), encoding="utf-8")

# ---------------------------------------------------------------------------
# HTTPハンドラ
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {fmt % args}")

    # ---- ルーティング ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/history":
            self._handle_history()
        elif path == "/api/md":
            self._handle_md()
        elif path == "/api/events":
            self._handle_sse()
        else:
            self._send(404, "text/plain", b"Not Found")

    def do_POST(self):
        if self.path == "/api/send":
            self._handle_send()
        elif self.path == "/api/reset":
            self._handle_reset()
        else:
            self._send(404, "text/plain", b"Not Found")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ---- エンドポイント実装 ----
    def _handle_send(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            text = data.get("message", "").strip()
        except Exception:
            self._send(400, "application/json", b'{"error":"invalid json"}')
            return

        if not text:
            self._send(400, "application/json", b'{"error":"empty message"}')
            return

        message_queue.put(text)
        self._send_json({"status": "queued"})

    def _handle_reset(self):
        with history_lock:
            conversation_history.clear()
        if OUTPUT_MD.exists():
            OUTPUT_MD.write_text("# AntiGravity Chat\n", encoding="utf-8")
        push_sse({"type": "update"})
        self._send_json({"status": "reset"})

    def _handle_history(self):
        with history_lock:
            data = list(conversation_history)
        self._send_json(data)

    def _handle_md(self):
        if OUTPUT_MD.exists():
            content = OUTPUT_MD.read_text(encoding="utf-8")
        else:
            content = "# AntiGravity Chat\n\nまだ会話がありません。"
        self._send(200, "text/plain; charset=utf-8", content.encode("utf-8"))

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors_headers()
        self.end_headers()

        client_q = queue.Queue(maxsize=50)
        with sse_lock:
            sse_clients.append(client_q)

        try:
            # 接続直後に現在の状態を送る
            self.wfile.write(b"data: {\"type\":\"connected\"}\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = client_q.get(timeout=30)
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # keepalive
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with sse_lock:
                if client_q in sse_clients:
                    sse_clients.remove(client_q)

    def _serve_file(self, path: Path, content_type: str):
        if not path.exists():
            self._send(404, "text/plain", b"Not Found")
            return
        self._send(200, content_type, path.read_bytes())

    # ---- ヘルパー ----
    def _send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(200, "application/json; charset=utf-8", body)

    def _send(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    if API_KEY == "YOUR_ANTHROPIC_API_KEY_HERE" or not API_KEY:
        print("[WARNING] config.json の anthropic_api_key を設定するか、")
        print("          環境変数 ANTHROPIC_API_KEY を設定してください。")

    worker = threading.Thread(target=antigravity_worker, daemon=True)
    worker.start()

    server = HTTPServer(("0.0.0.0", PORT), Handler)

    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"\n=== AntiGravity Mobile Bridge ===")
    print(f"  PC上のURL  : http://localhost:{PORT}")
    print(f"  スマホからのURL: http://{local_ip}:{PORT}")
    print(f"  出力MDファイル : {OUTPUT_MD.resolve()}")
    print(f"  Ctrl+C で停止\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを停止します...")
        message_queue.put(None)
        server.shutdown()

if __name__ == "__main__":
    main()
