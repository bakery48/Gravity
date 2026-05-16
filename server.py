#!/usr/bin/env python3
"""
AntiGravity Mobile Bridge Server (Windows)

スマホから受信したメッセージを Antigravity アプリのチャット欄に
キーボード入力で打ち込み、Antigravity が出力する MD ファイルの
差分をリアルタイムでスマホに返すブリッジサーバー。

- API キー不要（Antigravity 本体を使うため）
- メッセージ送信: ウィンドウをフォアグラウンド化 → クリップボード貼付 → Enter
- 返答取得: output.md を 500ms 間隔で監視し差分を SSE で配信
"""

import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 依存ライブラリ
# ---------------------------------------------------------------------------
try:
    import pyautogui
    import pygetwindow as gw
    import pyperclip
except ImportError:
    print("[ERROR] 必要パッケージが未インストールです:")
    print("  pip install -r requirements.txt")
    exit(1)

pyautogui.FAILSAFE = False

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.json"
with open(CONFIG_PATH, encoding="utf-8") as _f:
    _cfg = json.load(_f)

PORT                  = _cfg.get("port", 8765)
OUTPUT_MD             = Path(_cfg.get("output_md_path", "./output.md")).resolve()
WINDOW_SUBSTR         = _cfg.get("window_title_substring", "Antigravity")
SEND_KEY              = _cfg.get("send_key", "enter")
PASTE_DELAY_MS        = _cfg.get("paste_delay_ms", 150)
# チャット入力欄のクリック位置（ウィンドウ右端・下端からのオフセット px）
CHAT_FROM_RIGHT       = _cfg.get("chat_click_from_right", 200)
CHAT_FROM_BOTTOM      = _cfg.get("chat_click_from_bottom", 60)
STATIC_DIR            = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# グローバル状態
# ---------------------------------------------------------------------------
msg_queue: queue.Queue = queue.Queue()
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

_md_prev_content = ""
_md_prev_mtime   = 0.0

# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------
def push_sse(payload: dict):
    data = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)

# ---------------------------------------------------------------------------
# Antigravity ウィンドウへの入力
# ---------------------------------------------------------------------------
def send_to_antigravity(text: str) -> tuple[bool, str]:
    """Antigravity ウィンドウをアクティブ化してテキストを貼付＋送信キー押下"""
    try:
        wins = [w for w in gw.getAllWindows()
                if WINDOW_SUBSTR.lower() in (w.title or "").lower() and w.title.strip()]
        if not wins:
            return False, f"ウィンドウが見つかりません (タイトルに '{WINDOW_SUBSTR}' を含む)"

        win = wins[0]

        # 最小化されていたら復元
        try:
            if win.isMinimized:
                win.restore()
        except Exception:
            pass

        # フォアグラウンドへ
        try:
            win.activate()
        except Exception:
            # Windows で activate に失敗するケースの回避策
            try:
                win.minimize(); time.sleep(0.1); win.restore()
            except Exception:
                pass

        time.sleep(PASTE_DELAY_MS / 1000)

        # チャット入力欄をクリック（ウィンドウ右端・下端からのオフセット位置）
        try:
            click_x = win.left + win.width  - CHAT_FROM_RIGHT
            click_y = win.top  + win.height - CHAT_FROM_BOTTOM
            pyautogui.click(click_x, click_y)
            print(f"[送信] クリック位置: ({click_x}, {click_y})")
            time.sleep(0.1)
        except Exception as e:
            print(f"[送信] クリック失敗（続行）: {e}")

        # クリップボード経由で貼付（日本語含む多バイト対応）
        prev_clip = ""
        try:
            prev_clip = pyperclip.paste()
        except Exception:
            pass

        pyperclip.copy(text)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(PASTE_DELAY_MS / 1000)

        # 送信キー
        if SEND_KEY.lower() == "ctrl+enter":
            pyautogui.hotkey("ctrl", "enter")
        else:
            pyautogui.press("enter")

        # クリップボード復元（非同期で）
        def _restore():
            time.sleep(0.5)
            try:
                pyperclip.copy(prev_clip)
            except Exception:
                pass
        threading.Thread(target=_restore, daemon=True).start()

        return True, "ok"
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------------------
# 入力ワーカー
# ---------------------------------------------------------------------------
def input_worker():
    while True:
        text = msg_queue.get()
        if text is None:
            break
        print(f"[送信] メッセージ受信: {text!r}")
        ok, info = send_to_antigravity(text)
        if not ok:
            print(f"[送信] 失敗: {info}")
            push_sse({"type": "error", "text": f"[入力失敗] {info}"})
        else:
            print(f"[送信] Antigravity へ入力完了")
            push_sse({"type": "thinking"})

# ---------------------------------------------------------------------------
# MD ファイル監視
# ---------------------------------------------------------------------------
def md_watcher():
    global _md_prev_content, _md_prev_mtime

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

            diff = _extract_diff(_md_prev_content, new_content)
            _md_prev_content = new_content
            _md_prev_mtime   = mtime

            if diff:
                push_sse({"type": "md_update", "text": diff})
        except Exception:
            pass

def _extract_diff(old: str, new: str) -> str:
    """新規追加分を抽出。append 以外（全書き換え）の場合は新内容全体を返す。"""
    if new.startswith(old):
        return new[len(old):].strip()
    return new.strip()

# ---------------------------------------------------------------------------
# HTTP ハンドラ
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now():%H:%M:%S}] {fmt % args}")

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif p == "/api/md":
            self._md()
        elif p == "/api/events":
            self._sse()
        elif p == "/api/windows":
            self._windows()
        else:
            self._send(404, "text/plain", b"Not Found")

    def do_POST(self):
        if self.path == "/api/send":
            self._send_msg()
        else:
            self._send(404, "text/plain", b"Not Found")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

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

    def _md(self):
        content = OUTPUT_MD.read_text(encoding="utf-8") if OUTPUT_MD.exists() else ""
        self._send(200, "text/plain; charset=utf-8", content.encode())

    def _windows(self):
        titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
        self._json(200, {"windows": titles})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors(); self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=100)
        with sse_lock:
            sse_clients.append(q)
        try:
            self.wfile.write(b'data: {"type":"connected"}\n\n'); self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=25)
                    self.wfile.write(payload.encode()); self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n"); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

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
        self._cors(); self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main():
    threading.Thread(target=md_watcher,   daemon=True).start()
    threading.Thread(target=input_worker, daemon=True).start()

    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "127.0.0.1"

    print(f"\n=== AntiGravity Mobile Bridge ===")
    print(f"  PC         : http://localhost:{PORT}")
    print(f"  スマホ (LAN): http://{local_ip}:{PORT}")
    print(f"  対象ウィンドウ: タイトルに '{WINDOW_SUBSTR}' を含むもの")
    print(f"  送信キー    : {SEND_KEY}")
    print(f"  監視 MD     : {OUTPUT_MD}")
    print(f"  Ctrl+C で停止\n")

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止します...")
        msg_queue.put(None)
        srv.shutdown()

if __name__ == "__main__":
    main()
