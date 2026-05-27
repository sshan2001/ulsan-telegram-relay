import os
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RELAY_API_KEY = os.environ.get("RELAY_API_KEY", "").strip()

# Render 무료 인스턴스에서는 파일 저장이 영구 보장되지는 않습니다.
# 그래도 실행 중 사용자 통계 확인용으로는 충분히 동작합니다.
USERS_FILE = Path(os.environ.get("USERS_FILE", "telegram_users.json"))


def now_text():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def check_api_key():
    """PC 프로그램/관리자 페이지 접근용 API Key 확인.
    - PC 프로그램: X-Relay-Key 헤더
    - 브라우저 관리자 확인: ?key=...
    """
    if not RELAY_API_KEY:
        return True

    key = (
        request.headers.get("X-Relay-Key")
        or request.headers.get("X-Relay-API-Key")
        or request.args.get("key")
        or ""
    ).strip()

    return key == RELAY_API_KEY


def load_users():
    if not USERS_FILE.exists():
        return {}

    try:
        with USERS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass

    return {}


def save_users(users):
    try:
        with USERS_FILE.open("w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def upsert_user(chat_id, **fields):
    if not chat_id:
        return

    chat_id = str(chat_id)
    users = load_users()

    user = users.get(chat_id, {})
    if not user:
        user = {
            "chat_id": chat_id,
            "first_seen_at": now_text(),
        }

    user.update(fields)
    user["last_seen_at"] = now_text()
    users[chat_id] = user
    save_users(users)


def telegram_api(method, payload=None, timeout=20):
    if not TELEGRAM_BOT_TOKEN:
        return {
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN is not configured",
        }, 500

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    try:
        if payload is None:
            response = requests.get(url, timeout=timeout)
        else:
            response = requests.post(url, json=payload, timeout=timeout)

        try:
            data = response.json()
        except Exception:
            return {
                "ok": False,
                "error": "telegram response is not json",
                "status_code": response.status_code,
                "text": response.text[:500],
            }, response.status_code

        return data, response.status_code

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }, 500


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "ok": True,
        "service": "ulsan-ais-telegram-relay",
        "endpoints": [
            "/health",
            "/telegram/bot-info",
            "/telegram/find-chat-id",
            "/telegram/send",
            "/admin/stats?key=YOUR_RELAY_API_KEY",
        ],
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "ulsan-ais-telegram-relay",
        "time": now_text(),
    })


@app.route("/telegram/bot-info", methods=["GET"])
def telegram_bot_info():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data, status_code = telegram_api("getMe", payload=None, timeout=20)

    if not data.get("ok"):
        return jsonify({
            "ok": False,
            "error": data.get("description") or data.get("error") or "getMe failed",
            "telegram": data,
        }), status_code

    result = data.get("result", {})
    return jsonify({
        "ok": True,
        "id": result.get("id"),
        "username": result.get("username"),
        "first_name": result.get("first_name"),
    })


@app.route("/telegram/find-chat-id", methods=["POST"])
def telegram_find_chat_id():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    link_code = str(body.get("link_code") or body.get("connect_code") or "").strip()

    if not link_code:
        return jsonify({"ok": False, "error": "link_code is required"}), 400

    data, status_code = telegram_api("getUpdates", payload=None, timeout=30)

    if not data.get("ok"):
        return jsonify({
            "ok": False,
            "error": data.get("description") or data.get("error") or "getUpdates failed",
            "telegram": data,
        }), status_code

    updates = data.get("result", [])

    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message") or {}
        text = str(message.get("text") or "")

        if link_code not in text:
            continue

        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id:
            previous = load_users().get(str(chat_id), {})
            upsert_user(
                chat_id,
                bot_username=chat.get("username") or "",
                first_name=chat.get("first_name") or "",
                last_name=chat.get("last_name") or "",
                chat_type=chat.get("type") or "",
                last_link_code=link_code,
                last_linked_at=now_text(),
                linked_count=previous.get("linked_count", 0) + 1,
            )

            return jsonify({
                "ok": True,
                "chat_id": str(chat_id),
                "first_name": chat.get("first_name"),
                "username": chat.get("username"),
            })

    return jsonify({
        "ok": False,
        "error": "chat_id not found. Press START in the bot chat and try again.",
    }), 404


@app.route("/telegram/send", methods=["POST"])
def telegram_send():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    chat_id = str(body.get("chat_id") or "").strip()
    text = str(body.get("text") or "").strip()
    link_url = str(body.get("link_url") or "").strip()

    if not chat_id:
        return jsonify({"ok": False, "error": "chat_id is required"}), 400

    if not text:
        return jsonify({"ok": False, "error": "text is required"}), 400

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }

    if link_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {
                    "text": "실시간 위치 보기",
                    "url": link_url,
                }
            ]]
        }

    data, status_code = telegram_api("sendMessage", payload=payload, timeout=30)

    if data.get("ok"):
        prev = load_users().get(str(chat_id), {})
        upsert_user(
            chat_id,
            last_message_at=now_text(),
            message_count=prev.get("message_count", 0) + 1,
            last_send_ok=True,
            last_send_error="",
        )

        return jsonify({
            "ok": True,
            "telegram": data.get("result", {}),
        })

    prev = load_users().get(str(chat_id), {})
    upsert_user(
        chat_id,
        last_message_at=now_text(),
        message_count=prev.get("message_count", 0),
        last_send_ok=False,
        last_send_error=data.get("description") or data.get("error") or "send failed",
    )

    return jsonify({
        "ok": False,
        "error": data.get("description") or data.get("error") or "send failed",
        "telegram": data,
    }), status_code


@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    users = load_users()
    user_list = list(users.values())

    total_users = len(user_list)
    send_ok_users = sum(1 for u in user_list if u.get("last_send_ok") is True)
    send_fail_users = sum(1 for u in user_list if u.get("last_send_ok") is False)

    user_list.sort(key=lambda u: u.get("last_seen_at", ""), reverse=True)

    return jsonify({
        "ok": True,
        "service": "ulsan-ais-telegram-relay",
        "time": now_text(),
        "total_users": total_users,
        "send_ok_users": send_ok_users,
        "send_fail_users": send_fail_users,
        "users": user_list,
    })


@app.route("/admin/users", methods=["GET"])
def admin_users():
    if not check_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    users = load_users()
    return jsonify({
        "ok": True,
        "users": users,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
