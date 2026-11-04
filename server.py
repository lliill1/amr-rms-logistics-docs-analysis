"""Запуск: python server.py   (или run_web.bat в Windows). Слушает только 127.0.0.1."""
import argparse
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"DocAnalyzer AMR/RMS: {url}  (документация API: {url}/api/docs)")
    uvicorn.run("api.app:create_app", factory=True, host="127.0.0.1", port=args.port, log_level="warning")
