#!/usr/bin/env python3
"""
AntiGravity Mobile Bridge Server
- ユーザー入力を antigravity_input.txt に書き込む
- Claude API が返答を output.md に追記する
- output.md の変化（差分）をSSEでスマホへリアルタイム送信
"""

import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    import anthropic
except ImportError:
    print("[ERROR] anthropic パッケージが必要です: pip install anthropic")
    exit(1)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.json"

with open(CONFIG_PATH, encoding="utf-8") as _f:
    _cfg = json.load(_f)

PORT        = _cfg.get("port", 8765)
OUTPUT_MD   = Path(_cfg.get("output_md_path", "./output.md")).resolve()
INPUT_FILE  = Path(_cfg.get("input_file", "./antigravity_input.txt")).resolve()
API_KEY     = _cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
MODEL       = _cfg.get("model", "claude-sonnet-4-6")
SYSTEM_PROMPT = _cfg.get("system_prompt", "あなたは親切なAIアシスタントです。日本語で回答してください。")

STATIC_DIR  = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# グローバル状態
# ---------------------------------------------------------------------------
conv_history: list[dict] = []
conv_lock    = threading.Lock()

msg_queue    = queue.Queue()     # スマホ→処理キュー
sse_clients: list[queue.Queue] = []
sse_lock     = threading.Lock()

# MDファイル監視用
_md_prev_content = ""
_md_prev_mtime   = 0.0

# ---------------------------------------------------------------------------
# SSE ブロードキャスト
# ---------------------------------------------------------------------------
def push_sse(payload: dict):
    data = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    with sse_lock:
        dead = [q for q in sse_clients if _try_put(q, data)]
        for q in dead:
            sse_clients.remove(q)

def _try_put(q: queue.Queue, data: str) -> bool:
    try:
        q.put_nowait(data)
        return False
    except queue.Full:
        return True  # dead

# ---------------------------------------------------------------------------
# MDファイル監視スレッド
# ---------------------------------------------------------------------------
def md_watcher():
    """output.md を 500ms ごとに監視し、差分があれば SSE 送信"""
    global _md_prev_content, _md_prev_mtime

    # 起動時の初期値
    if OUTPUT_MD.exists():
        _md_prev_content = OUTPUT_MD.read_text(encoding="utf-8")
        _md_prev_mtime   = OUTPUT_MD.stat().st_mtime

    while True:
        time.sleep(0.5)
        if not OUTPUT_MD.exists():
            continue
        try:
            mtime = OUTPUT_MD.stat().st_mtime
            if mtime == _md_prev_mtime:
                continue

            new_content = OUTPUT_MD.read_text(encoding="utf-8")
            if new_content == _md_prev_content:
                _md_prev_mtime = mtime
                continue

            diff_text = _extract_diff(_md_prev_content, new_content)
            _md_prev_content = new_content
            _md_prev_mtime   = mtime

            if diff_text:
                push_sse({"type": "md_update", "text": diff_text})

        except Exception:
            pass

def _extract_diff(old: str, new: str) -> str:
    """
    old と new の差分から表示用テキストを抽出する。
    • 追記の場合: 追加された部分を返す
    • 全書き換えの場合: new 全体を返す
    差分からマークダウン装飾（**AntiGravity** ヘッダー・区切り線）を除去する。
    """
    if new.startswith(old):
        raw = new[len(old):]
    else:
        raw = new  # 全書き換え

    # "**AntiGravity**" ヘッダー行と "---" 区切りを除去
    raw = re.sub(r"\*\*AntiGravity\*\*\s*", "", raw)
    raw = re.sub(r"^---\s*$", "", raw, flags=re.MULTILINE)
    # タイムスタンプ行 (## で始まる行) を除去
    raw = re.sub(r"^##.*$", "", raw, flags=re.MULTILINE)
    return raw.strip()

# ---------------------------------------------------------------------------
# AntiGravity 処理スレッド（Claude API → output.md 書き込み）
# ---------------------------------------------------------------------------
def antigravity_worker():
    client = anthropic.Anthropic(api_key=API_KEY)

    while True:
        user_msg = msg_queue.get()
        if user_msg is None:
            break

        # 入力ファイルに書き出す（外部ツールとの連携用）
        INPUT_FILE.write_text(user_msg + "\n", encoding="utf-8")

        with conv_lock:
            conv_history.append({"role": "user", "content": user_msg})
            snapshot = list(conv_history)

        push_sse({"type": "thinking"})

        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=8096,
                system=SYSTEM_PROMPT,
                messages=snapshot,
            )
            reply = resp.content[0].text
        except Exception as e:
            reply = f"[エラー] {e}"

        with conv_lock:
            conv_history.append({"role": "assistant", "content": reply})

        # output.md に追記 → md_watcher が差分を検知して SSE 送信
        _append_to_md(user_msg, reply)

def _append_to_md(user_msg: str, reply: str):
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not OUTPUT_MD.exists() or OUTPUT_MD.read_text(encoding="utf-8").strip() == "":
        header = "# AntiGravity Chat\n\n"
    else:
        header = ""

    block = (
        f"{header}"
        f"## {ts}\n\n"
        f"**あなた**\n\n{user_msg}\n\n"
        f"**AntiGravity**\n\n{reply}\n\n"
        f"---\n\n"
    )
    with open(OUTPUT_MD, "a", encoding="utf-8") as f:
        f.write(block)

# ---------------------------------------------------------------------------
# HTTP ハンドラ
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now():%H:%M:%S}] {fmt % args}")

    def do_GET(self):
        p = urlparse(self.path).path
        routes = {
            "/":           lambda: self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8"),
            "/index.html": lambda: self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8"),
            "/api/history":lambda: self._history(),
            "/api/md":     lambda: self._md(),
            "/api/events": lambda: self._sse(),
        }
        handler = routes.get(p)
        if handler:
            handler()
        else:
            self._send(404, "text/plain", b"Not Found")

    def do_POST(self):
        p = self.path
        if p == "/api/send":   self._send_msg()
        elif p == "/api/reset":self._reset()
        else: self._send(404, "text/plain", b"Not Found")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ---- エンドポイント ----

    def _send_msg(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            text = json.loads(body).get("message", "").strip()
        except Exception:
            return self._json(400, {"error": "invalid json"})
        if not text:
            return self._json(400, {"error": "empty message"})
        msg_queue.put(text)
        self._json(200, {"status": "queued"})

    def _reset(self):
        with conv_lock:
            conv_history.clear()
        if OUTPUT_MD.exists():
            OUTPUT_MD.write_text("", encoding="utf-8")
        global _md_prev_content, _md_prev_mtime
        _md_prev_content = ""
        _md_prev_mtime   = 0.0
        push_sse({"type": "reset"})
        self._json(200, {"status": "reset"})

    def _history(self):
        with conv_lock:
            data = list(conv_history)
        self._json(200, data)

    def _md(self):
        content = OUTPUT_MD.read_text(encoding="utf-8") if OUTPUT_MD.exists() else ""
        self._send(200, "text/plain; charset=utf-8", content.encode())

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=100)
        with sse_lock:
            sse_clients.append(q)
        try:
            self.wfile.write(b'data: {"type":"connected"}\n\n')
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=25)
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    # ---- ヘルパー ----

    def _file(self, path: Path, ct: str):
        if not path.exists():
            return self._send(404, "text/plain", b"Not Found")
        self._send(200, ct, path.read_bytes())

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self._send(code, "application/json; charset=utf-8", body)

    def _send(self, code: int, ct: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main():
    if not API_KEY or API_KEY == "YOUR_ANTHROPIC_API_KEY_HERE":
        print("[WARNING] anthropic_api_key が未設定です（config.json または環境変数 ANTHROPIC_API_KEY）")

    threading.Thread(target=md_watcher,         daemon=True).start()
    threading.Thread(target=antigravity_worker, daemon=True).start()

    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "127.0.0.1"

    print(f"\n=== AntiGravity Mobile Bridge ===")
    print(f"  PC         : http://localhost:{PORT}")
    print(f"  スマホ (LAN): http://{local_ip}:{PORT}")
    print(f"  出力 MD     : {OUTPUT_MD}")
    print(f"  入力ファイル : {INPUT_FILE}")
    print(f"  Ctrl+C で停止\n")

    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止します...")
        msg_queue.put(None)
        srv.shutdown()

if __name__ == "__main__":
    main()
