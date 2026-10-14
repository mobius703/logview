"""Local browser-based log viewer. Run: python app.py"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_READ_BYTES = 4 * 1024 * 1024
PICKER_LOCK = threading.Lock()


def pick_file(initial_path=""):
    # A separate process gives Tk its own main thread and clean lifetime.
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        initial = Path(initial_path).expanduser() if initial_path else Path.home()
        folder = initial if initial.is_dir() else initial.parent
        selected = filedialog.askopenfilename(
            parent=root, title="表示するログファイルを選択",
            initialdir=str(folder) if folder.is_dir() else str(Path.home()),
            filetypes=[("すべてのファイル", "*"), ("ログファイル", "*.log"), ("テキストファイル", "*.txt")],
        )
        print(json.dumps({"path": selected}, ensure_ascii=True))
    finally:
        root.destroy()


def read_tail(path, limit=100, encoding="utf-8"):
    if not 1 <= limit <= 10000:
        raise ValueError("表示行数は1～10000を指定してください。")
    if encoding not in ("utf-8", "cp932", "utf-16"):
        raise ValueError("未対応の文字コードです。")
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        position = size
        chunks = []
        total = 0
        newlines = 0
        # UTF-16 needs aligned decoding, so read the bounded window in full.
        while position > 0 and total < MAX_READ_BYTES:
            amount = min(65536, position, MAX_READ_BYTES - total)
            position -= amount
            stream.seek(position)
            block = stream.read(amount)
            chunks.append(block)
            total += amount
            newlines += block.count(b"\n")
            if encoding != "utf-16" and newlines > limit:
                break
        raw = b"".join(reversed(chunks))
        actual_encoding = encoding
        if encoding == "utf-16":
            stream.seek(0)
            actual_encoding = "utf-16-be" if stream.read(2) == b"\xfe\xff" else "utf-16-le"
            if position % 2:
                raw = raw[1:]
        content = raw.decode(actual_encoding, errors="replace")
        lines = content.splitlines()
        if position and lines:
            lines = lines[1:]  # Discard the potentially partial first line.
        result = lines[-limit:]
        if position == 0 and result:
            result[0] = result[0].lstrip("\ufeff")
        return {"text": "\n".join(result), "lines": len(result), "size": size,
                "limited": position > 0 and total >= MAX_READ_BYTES and len(lines) < limit}


HTML = r'''<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ログビューア</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101722;color:#e1e7ef;font:15px system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:24px}h1{font-size:24px;margin:0 0 20px}.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end}label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:#b8c6d8}.path{flex:1;min-width:240px}input,select,button{font:inherit;border:1px solid #43546c;border-radius:7px;background:#202d40;color:#fff;padding:10px}button{cursor:pointer}button:hover{background:#334967}input[type=number]{width:115px}#status{min-height:24px;margin:16px 0;color:#92b9de}pre{margin:0;background:#080e17;border:1px solid #29394e;border-radius:8px;padding:18px;height:calc(100vh - 235px);min-height:250px;overflow:auto;white-space:pre;font:14px/1.65 Consolas,monospace}.check{flex-direction:row;align-items:center;padding-bottom:10px}small{color:#a8b9cd}
</style><main><h1>ログビューア</h1><div class="controls">
<label class="path">ログファイル<input id="path" placeholder="ファイルを選択、または絶対パスを入力"></label>
<button id="choose">ファイルを選択</button><label>最大行数<input id="limit" type="number" min="1" max="10000" value="100"></label>
<label>文字コード<select id="encoding"><option value="utf-8">UTF-8</option><option value="cp932">Shift_JIS (CP932)</option><option value="utf-16">UTF-16</option></select></label>
<button id="toggle">一時停止</button><label class="check"><input type="checkbox" id="follow" checked>末尾に追従</label></div>
<div id="status" role="status">ファイルを選択してください。</div><pre id="log" tabindex="0" aria-label="ログ内容"></pre>
<p><small>約1秒ごとに更新 · 最大10,000行 · 1回の読み込みは末尾4 MiBまで</small></p></main>
<script>
const $=id=>document.getElementById(id);let paused=false,busy=false,revision=0;
async function api(url){const response=await fetch(url,{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error);return data}
async function refresh(){if(paused||busy||!$('path').value.trim())return;const version=revision;busy=true;try{const q=new URLSearchParams({path:$('path').value.trim(),limit:$('limit').value,encoding:$('encoding').value});const data=await api('/api/tail?'+q);if(version!==revision)return;if($('log').textContent!==data.text){$('log').textContent=data.text;if($('follow').checked)$('log').scrollTop=$('log').scrollHeight}$('status').textContent=`更新 ${new Date().toLocaleTimeString()} · ${data.lines}行 · ${data.size.toLocaleString()} bytes`+(data.limited?' · 読み込み上限に達しました':'')}catch(e){if(version===revision)$('status').textContent=e.message}finally{busy=false}}
$('choose').onclick=async()=>{const button=$('choose');button.disabled=true;button.textContent='選択中…';try{const response=await fetch('/api/pick',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:$('path').value.trim()})});const data=await response.json();if(!response.ok)throw Error(data.error);if(data.path){$('path').value=data.path;revision++;$('log').textContent='';refresh()}}catch(e){$('status').textContent=e.message}finally{button.disabled=false;button.textContent='ファイルを選択'}};
for(const id of ['path','limit','encoding'])$(id).onchange=()=>{revision++;if(id==='path')$('log').textContent='';refresh()};
$('toggle').onclick=()=>{paused=!paused;revision++;$('toggle').textContent=paused?'再開':'一時停止';if(paused)$('status').textContent='一時停止中';else refresh()};setInterval(refresh,1000);
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        host = self.headers.get("Host")
        if (host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Origin") not in (None, f"http://{host}")):
            self.send_error(403)
            return
        if self.path != "/api/pick":
            self.send_error(404)
            return
        if not PICKER_LOCK.acquire(blocking=False):
            self.respond(json.dumps({"error": "ファイル選択ダイアログは既に開いています。"}).encode(), status=409)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("リクエストが不正です。")
            request = json.loads(self.rfile.read(length))
            path = request.get("path", "")
            if not isinstance(path, str):
                raise ValueError("パスが不正です。")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--pick-file", path],
                capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode:
                raise RuntimeError("ファイル選択ダイアログを開けませんでした。Tkinterを含むPythonで起動してください。")
            data = json.loads(result.stdout)
            self.respond(json.dumps(data).encode())
        except (OSError, ValueError, RuntimeError) as error:
            self.respond(json.dumps({"error": str(error)}).encode(), status=400)
        finally:
            PICKER_LOCK.release()

    def do_GET(self):
        # Only serve requests addressed to this loopback server.
        if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            self.send_error(403)
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/":
                self.respond(HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            path = query.get("path", [""])[0]
            if url.path == "/api/tail":
                data = read_tail(path, int(query.get("limit", ["100"])[0]), query.get("encoding", ["utf-8"])[0])
            else:
                self.send_error(404)
                return
            self.respond(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        except (OSError, ValueError) as error:
            self.respond(json.dumps({"error": str(error)}, ensure_ascii=False).encode("utf-8"), status=400)

    def respond(self, body, content_type="application/json; charset=utf-8", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="ブラウザでローカルログをリアルタイム表示")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--pick-file", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.pick_file is not None:
        pick_file(args.pick_file)
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"ログビューア: {url}\n終了するには Ctrl+C を押してください。", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
