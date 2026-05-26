"""
Telegram relay server for Ulsan AIS Alert.

Render deployment:
1. Create a new Web Service on Render.
2. Set environment variable TELEGRAM_BOT_TOKEN to your BotFather token.
3. Optional: set RELAY_API_KEY and put the same key in the desktop app if you later add header auth.
4. Start command: gunicorn telegram_relay_server_render:app

Requirements:
flask
requests
gunicorn
"""

import os
import json
import time
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RELAY_API_KEY = os.environ.get("RELAY_API_KEY", "").strip()


def require_token():
    if not TELEGRAM_BOT_TOKEN:
        return False, jsonify({"ok": False, "error": "TELEGRAM_BOT_TOKEN is not set"}), 500
    return True, None, None


def check_api_key():
    # 현재 PC 앱은 API KEY 없이도 붙도록 열어두었습니다.
    # 공개 배포 단계에서는 RELAY_API_KEY를 켜고, PC 앱에도 같은 헤더를 넣는 방식으로 잠그는 것을 권장합니다.
    if not RELAY_API_KEY:
        return True
    return request.headers.get("X-Relay-Key") == RELAY_API_KEY


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "ulsan-ais-telegram-relay"})


@app.route("/telegram/bot-info", methods=["GET"])
def telegram_bot_info():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    ok, resp, status = require_token()
    if not ok:
        return resp, status

    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=15)
    data = r.json()
    if not data.get("ok"):
        return jsonify({"ok": False, "error": data.get("description", "getMe failed")}), 502

    result = data.get("result", {})
    return jsonify({
        "ok": True,
        "username": result.get("username", ""),
        "first_name": result.get("first_name", ""),
    })


@app.route("/telegram/find-chat-id", methods=["POST"])
def telegram_find_chat_id():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    ok, resp, status = require_token()
    if not ok:
        return resp, status

    payload = request.get_json(silent=True) or {}
    link_code = str(payload.get("link_code") or "").strip()
    if not link_code:
        return jsonify({"ok": False, "error": "link_code is required"}), 400

    # Telegram getUpdates는 최근 업데이트만 가져옵니다.
    # 사용자가 봇에서 START를 누른 직후 호출하는 흐름이면 충분히 안정적으로 찾을 수 있습니다.
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", timeout=20)
    data = r.json()
    if not data.get("ok"):
        return jsonify({"ok": False, "error": data.get("description", "getUpdates failed")}), 502

    for update in reversed(data.get("result", [])):
        message = update.get("message") or update.get("edited_message") or {}
        text = str(message.get("text") or "")
        if link_code not in text:
            continue

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id:
            return jsonify({
                "ok": True,
                "chat_id": str(chat_id),
                "matched_text": text[:80],
            })

    return jsonify({"ok": False, "error": "chat_id not found. Press START in the bot chat and try again."}), 404


@app.route("/telegram/send", methods=["POST"])
def telegram_send():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    ok, resp, status = require_token()
    if not ok:
        return resp, status

    payload = request.get_json(silent=True) or {}
    chat_id = str(payload.get("chat_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    link_url = str(payload.get("link_url") or "").strip()

    if not chat_id or not text:
        return jsonify({"ok": False, "error": "chat_id and text are required"}), 400

    tg_payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }

    if link_url:
        tg_payload["reply_markup"] = json.dumps({
            "inline_keyboard": [[
                {"text": "실시간 위치 보기", "url": link_url}
            ]]
        }, ensure_ascii=False)

    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=tg_payload,
        timeout=20,
    )
    data = r.json()
    if not data.get("ok"):
        return jsonify({"ok": False, "error": data.get("description", "sendMessage failed")}), 502

    return jsonify({"ok": True, "telegram_result": data.get("result", {})})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
