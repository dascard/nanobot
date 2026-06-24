#!/usr/bin/env python3
"""Nanobot 本地全链路测试——chat + push 收发于一体。

用法:
    python tests/local_test.py "你好"              # chat 对话
    python tests/local_test.py "总结群聊" -g 1031796336  # 群聊上下文
    python tests/local_test.py --listen             # 启动 push 监听 + chat REPL

chat 和 push 的输出全部保存到 tests/chat_resp/
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

BASE = os.environ.get("NANOBOT_URL", "http://127.0.0.1:8765")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_resp")


# ═══════════════════ chat ═══════════════════

def _chat(query: str, session: str = None) -> dict:
    sid = session or "private_chat_repl"
    body = {"user_id": "local_test", "session_id": sid,
            "sender_name": "本地测试", "query": query,
            "stream": False, "classification_request": True}
    req = urllib.request.Request(f"{BASE}/api/v1/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════ push receiver ═══════════════════

class _PushHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length else ""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(OUT, exist_ok=True)

        # 存原始
        with open(os.path.join(OUT, f"{ts}_push_raw.txt"), "w") as f:
            f.write(body)

        try:
            data = json.loads(body)
            msg = data.get("message", "")
            with open(os.path.join(OUT, f"{ts}_push.json"), "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            leak = any(kw in msg for kw in
                ["工具调用纪律", "你是 Nanobot", "判断规则", "TimingGate", "Available Functions"])
            flag = " ⚠️ LEAK" if leak else ""
            print(f"\n[PUSH] {data.get('target_type')}/{data.get('target_id')} "
                  f"len={len(msg)}{flag}")
            preview = msg[:200].replace("\n", "\\n")
            print(f"       {preview}...")
        except json.JSONDecodeError:
            print(f"\n[PUSH] non-JSON body len={len(body)}")


def _start_listener(port: int = 9999):
    srv = HTTPServer(("127.0.0.1", port), _PushHandler)
    print(f"[listen] push receiver on :{port}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()


# ═══════════════════ output ═══════════════════

def _save(text: str, prefix: str = "chat") -> str:
    os.makedirs(OUT, exist_ok=True)
    ts = time.strftime("%H%M%S")
    is_html = text.lstrip()[:15].lower().startswith(("<!doctype", "<html", "<article"))
    ext = ".html" if is_html else ".txt"
    path = os.path.join(OUT, f"{prefix}_{ts}{ext}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _show(text: str):
    if not text:
        return
    is_html = text.lstrip()[:15].lower().startswith(("<!doctype", "<html", "<article"))
    path = _save(text, "chat")
    tag = "HTML" if is_html else "text"
    print(f"[{tag} {len(text)}c -> {path}]")
    if not is_html:
        print(text[:500] if len(text) <= 500 else text[:500] + f"\n... [{len(text)-500} more chars]")


# ═══════════════════ main ═══════════════════

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("query", nargs="?", default=None)
    p.add_argument("--group", "-g", default=None)
    p.add_argument("--listen", "-l", action="store_true", help="启动 push 监听")
    p.add_argument("--raw", action="store_true")
    args = p.parse_args()

    if args.listen:
        _start_listener()

    if args.query:
        session = f"group_{args.group}" if args.group else None
        r = _chat(args.query, session)
        if args.raw:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        elif r.get("error"):
            print(f"ERROR: {r}")
        elif r.get("status") == "silent":
            print("[silent]")
        else:
            _show(r.get("answer", ""))
    elif args.listen:
        print("push 监听中，直接输入消息进入 chat 模式:\n")
        try:
            while True:
                q = input("> ").strip()
                if not q:
                    continue
                r = _chat(q)
                a = r.get("answer", "")
                if r.get("status") == "silent":
                    print("<- [silent]\n")
                else:
                    print("<- ", end="")
                    _show(a)
                    print()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
    else:
        print(f"Nanobot — {BASE}")
        print("chat:  python tests/local_test.py \"你好\"")
        print("push:  python tests/local_test.py --listen")
