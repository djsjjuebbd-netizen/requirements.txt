import os
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, request
from pymongo import MongoClient
from telebot import TeleBot, types


load_dotenv()

# Mana buni qo'shish kerak:
app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
admin_str = os.getenv("ADMIN_IDS", os.getenv("OWNER_ID", "6968399046"))
ADMIN_IDS = {int(i.strip()) for i in admin_str.replace(" ", "").split(",") if i.strip().isdigit()}
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "telegram_kino_bot").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "kino-bot-secret").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
KINO_CHANNEL_URL = os.getenv("KINO_CHANNEL_URL", "https://t.me/JavaMediaUz").strip()
PORT = int(os.getenv("PORT", "5000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env yoki Render Environment ichida bo'lishi kerak.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS yoki OWNER_ID .env ichida bo'lishi kerak. Masalan: ADMIN_IDS=123456789")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI .env yoki Render Environment ichida bo'lishi kerak.")

mongo_client = MongoClient(MONGODB_URI)
mongo_db = mongo_client[MONGODB_DB]
users_col = mongo_db["users"]
channels_col = mongo_db["mandatory_channels"]
movies_col = mongo_db["movies"]

bot = TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=False)
app = Flask(__name__)
admin_states: dict[int, dict[str, Any]] = {}

@bot.message_handler(commands=["start"])
def start(message: types.Message) -> None:
    print(">>> START BOSILDI!", message.from_user.id)
    save_user(message.from_user)
    if not require_subscription(message):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) == 2:
        show_movie_by_code(message.chat.id, args[1].strip())
        return
        
    bot.send_message(
        message.chat.id,
        "ASSALOM ALEYKUM\n\nXUSH KELIBSIZ\n\nKINO KODINI YUBORING",
        reply_markup=start_keyboard(),
    )



STYLE_PRIMARY = "primary"
STYLE_SUCCESS = "success"
STYLE_DANGER = "danger"


def rbtn(text: str, style: str = STYLE_PRIMARY) -> types.KeyboardButton:
    return types.KeyboardButton(text, style=style)


def ibtn(text: str, style: str = STYLE_PRIMARY, **kwargs: Any) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text, style=style, **kwargs)


def now() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    users_col.create_index("user_id", unique=True, partialFilterExpression={"user_id": {"$exists": True, "$gt": None}})
    channels_col.create_index("chat_id")
    movies_col.create_index("code", unique=True)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def save_user(user: types.User) -> None:
    users_col.update_one(
        {"user_id": user.id},
        {
            "$set": {
                "user_id": user.id,
                "first_name": user.first_name or "",
                "username": user.username or "",
                "updated_at": now(),
            },
            "$setOnInsert": {"created_at": now()},
        },
        upsert=True,
    )


def get_channels() -> list[dict[str, Any]]:
    return list(channels_col.find().sort("_id", -1))


def normalize_public_chat(value: str) -> tuple[str, str, str]:
    raw = value.strip()
    username = raw.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").strip("/")
    if username.startswith("+"):
        raise ValueError("Oddiy kanal/guruh uchun @username yuboring.")
    username = username.lstrip("@")
    if not username:
        raise ValueError("Username bo'sh bo'lmasin. Masalan: @kanalim")
    return f"@{username}", f"https://t.me/{username}", username


def normalize_request_channel(value: str) -> tuple[str, str, str]:
    raw = value.strip()
    if "|" in raw:
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) < 2:
            raise ValueError("Format: -1001234567890|https://t.me/+invite|Kanal nomi")
        chat_id = parts[0]
        url = parts[1]
        title = parts[2] if len(parts) > 2 and parts[2] else "Zayafka kanal"
        return chat_id, url, title
    return normalize_public_chat(raw)


def admin_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(rbtn("🟢 ➕ Majburiy obuna qo'shish", STYLE_SUCCESS))
    kb.row(rbtn("🔴 ➖ Majburiy obuna ayirish", STYLE_DANGER))
    kb.row(rbtn("🟣 📢 Foydalanuvchilarga habar", STYLE_PRIMARY))
    kb.row(rbtn("🔵 🎬 Kino qo'shish", STYLE_PRIMARY), rbtn("🟡 📊 Statistika", STYLE_SUCCESS))
    kb.row(rbtn("⚪️ Bekor qilish", STYLE_DANGER))
    return kb


def back_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(rbtn("⬅️ Orqaga", STYLE_PRIMARY), rbtn("⚪️ Bekor qilish", STYLE_DANGER))
    return kb


def add_subscription_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(ibtn("🔵 Oddiy kanal", STYLE_PRIMARY, callback_data="add_sub:channel"))
    kb.add(ibtn("🟢 Guruh", STYLE_SUCCESS, callback_data="add_sub:group"))
    kb.add(ibtn("🟣 Zayafka kanal", STYLE_PRIMARY, callback_data="add_sub:request"))
    return kb


def broadcast_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(ibtn("🟢 Text habar", STYLE_SUCCESS, callback_data="broadcast:text"))
    kb.add(ibtn("🟣 Rasm bilan text habar", STYLE_PRIMARY, callback_data="broadcast:photo"))
    return kb


def start_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(ibtn("🎬 KINO KODLARI", STYLE_PRIMARY, url=KINO_CHANNEL_URL))
    return kb


def subscription_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for index, channel in enumerate(get_channels(), start=1):
        label = {"channel": "🔵 Kanal", "group": "🟢 Guruh", "request": "🟣 Zayafka"}.get(channel["kind"], "🔵 Obuna")
        style = STYLE_SUCCESS if channel["kind"] == "group" else STYLE_PRIMARY
        kb.add(ibtn(f"{label} {index}", style, url=channel["url"]))
    kb.add(ibtn("✅ Tekshirish", STYLE_SUCCESS, callback_data="check_subs"))
    return kb


def delete_subscription_keyboard() -> types.InlineKeyboardMarkup | None:
    channels = get_channels()
    if not channels:
        return None
    kb = types.InlineKeyboardMarkup()
    for channel in channels:
        kb.add(ibtn(f"🔴 O'chirish: {channel['title']}", STYLE_DANGER, callback_data=f"del_sub:{channel['_id']}"))
    return kb


def check_subscriptions(user_id: int) -> bool:
    for channel in get_channels():
        try:
            member = bot.get_chat_member(channel["chat_id"], user_id)
            if member.status in {"left", "kicked"}:
                return False
        except Exception:
            return False
    return True


def require_subscription(message: types.Message) -> bool:
    if check_subscriptions(message.from_user.id):
        return True
    bot.send_message(
        message.chat.id,
        "🔐 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling, keyin ✅ Tekshirish tugmasini bosing.",
        reply_markup=subscription_keyboard(),
    )
    return False


def send_movie(chat_id: int, movie: dict[str, Any]) -> None:
    caption = movie.get("caption") or ""
    if movie["content_type"] == "video":
        bot.send_video(chat_id, movie["file_id"], caption=caption)
    elif movie["content_type"] == "document":
        bot.send_document(chat_id, movie["file_id"], caption=caption)
    elif movie["content_type"] == "photo":
        bot.send_photo(chat_id, movie["file_id"], caption=caption)
    else:
        bot.send_message(chat_id, movie.get("text") or caption or "Kino topildi.")


def show_movie_by_code(chat_id: int, code: str) -> None:
    movie = movies_col.find_one({"code": code})
    if not movie:
        bot.send_message(chat_id, "❌ Bunday kodli kino topilmadi. Kodni tekshirib qayta yuboring.")
        return
    send_movie(chat_id, movie)


def broadcast_text(text: str) -> tuple[int, int]:
    ok = failed = 0
    for user in users_col.find({}, {"user_id": 1}):
        try:
            bot.send_message(user["user_id"], text)
            ok += 1
        except Exception:
            failed += 1
    return ok, failed


def broadcast_photo(file_id: str, caption: str) -> tuple[int, int]:
    ok = failed = 0
    for user in users_col.find({}, {"user_id": 1}):
        try:
            bot.send_photo(user["user_id"], file_id, caption=caption)
            ok += 1
        except Exception:
            failed += 1
    return ok, failed
    
@bot.message_handler(commands=["admin"])
def admin_panel(message):
    print(">>> ADMIN BOSILDI!", message.from_user.id)
    
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "Siz admin emassiz!")
        return
        
    # MANA BU QATOR PANELNI TELEGRAMDA CHIQARIB BERADI:
    bot.send_message(
        message.chat.id,
        "Xush kelibsiz, Admin panel:",
        reply_markup=admin_menu()  # <--- Mana shu yerda sizning admin_menu() funksiyangiz chaqirilishi kerak!
    )


@bot.message_handler(func=lambda message: message.text in {"⬅️ Orqaga", "⚪️ Bekor qilish"})
def cancel_state(message: types.Message) -> None:
    if is_admin(message.from_user.id):
        admin_states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, "🛠 Admin panel", reply_markup=admin_menu())


@bot.message_handler(func=lambda message: is_admin(message.from_user.id) and message.text == "🟢 ➕ Majburiy obuna qo'shish")
def add_subscription_menu(message: types.Message) -> None:
    bot.send_message(message.chat.id, "Qaysi turdagi majburiy obuna qo'shilsin?", reply_markup=add_subscription_keyboard())


@bot.message_handler(func=lambda message: is_admin(message.from_user.id) and message.text == "🔴 ➖ Majburiy obuna ayirish")
def remove_subscription_menu(message: types.Message) -> None:
    kb = delete_subscription_keyboard()
    if not kb:
        bot.send_message(message.chat.id, "Majburiy obuna ro'yxati bo'sh.", reply_markup=admin_menu())
        return
    bot.send_message(message.chat.id, "O'chiriladigan majburiy obunani tanlang:", reply_markup=kb)


@bot.message_handler(func=lambda message: is_admin(message.from_user.id) and message.text == "🟣 📢 Foydalanuvchilarga habar")
def broadcast_menu_handler(message: types.Message) -> None:
    bot.send_message(message.chat.id, "Qanday habar yuborasiz?", reply_markup=broadcast_keyboard())


@bot.message_handler(func=lambda message: is_admin(message.from_user.id) and message.text == "🔵 🎬 Kino qo'shish")
def add_movie_start(message: types.Message) -> None:
    admin_states[message.from_user.id] = {"step": "movie_code"}
    bot.send_message(message.chat.id, "🎬 Kino kodini yozing. Masalan: 777", reply_markup=back_menu())


@bot.message_handler(func=lambda message: is_admin(message.from_user.id) and message.text == "🟡 📊 Statistika")
def stats(message: types.Message) -> None:
    users = users_col.count_documents({})
    channels = channels_col.count_documents({})
    movies = movies_col.count_documents({})
    bot.send_message(
        message.chat.id,
        f"📊 Statistika\n\n👥 Foydalanuvchilar: {users}\n🔐 Majburiy obuna: {channels}\n🎬 Kinolar: {movies}",
        reply_markup=admin_menu(),
    )


@bot.callback_query_handler(func=lambda call: call.data == "check_subs")
def check_subs_callback(call: types.CallbackQuery) -> None:
    save_user(call.from_user)
    if check_subscriptions(call.from_user.id):
        bot.answer_callback_query(call.id, "Obuna tasdiqlandi.")
        bot.send_message(
            call.message.chat.id,
            "ASSALOMU ALEYKUM\n\nXUSH KELIBSIZ\n\nKINO KODINI YUBORING",
            reply_markup=start_keyboard(),
        )
    else:
        bot.answer_callback_query(call.id, "Hali hammasiga obuna bo'lmadingiz.", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("add_sub:"))
def add_sub_callback(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Ruxsat yo'q.", show_alert=True)
        return

    kind = call.data.split(":", 1)[1]
    admin_states[call.from_user.id] = {"step": "add_sub", "kind": kind}
    bot.answer_callback_query(call.id)

    if kind == "request":
        text = (
            "🟣 Zayafka kanal ma'lumotini yuboring.\n\n"
            "Public kanal bo'lsa: @kanal_username\n"
            "Private invite bo'lsa: -1001234567890|https://t.me/+invite|Kanal nomi\n\n"
            "Bot kanal/guruhda admin bo'lishi kerak."
        )
    elif kind == "group":
        text = "🟢 Guruh username yuboring. Masalan: @guruh_username\nBot guruhda admin bo'lishi kerak."
    else:
        text = "🔵 Kanal username yuboring. Masalan: @kanal_username\nBot kanalda admin bo'lishi kerak."
    bot.send_message(call.message.chat.id, text, reply_markup=back_menu())


@bot.callback_query_handler(func=lambda call: call.data.startswith("del_sub:"))
def del_sub_callback(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Ruxsat yo'q.", show_alert=True)
        return
    try:
        sub_id = ObjectId(call.data.split(":", 1)[1])
    except Exception:
        bot.answer_callback_query(call.id, "ID xato.", show_alert=True)
        return
    channels_col.delete_one({"_id": sub_id})
    bot.answer_callback_query(call.id, "O'chirildi.")
    bot.send_message(call.message.chat.id, "🔴 Majburiy obuna o'chirildi.", reply_markup=admin_menu())


@bot.callback_query_handler(func=lambda call: call.data.startswith("broadcast:"))
def broadcast_callback(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Ruxsat yo'q.", show_alert=True)
        return

    mode = call.data.split(":", 1)[1]
    admin_states[call.from_user.id] = {"step": f"broadcast_{mode}"}
    bot.answer_callback_query(call.id)

    if mode == "photo":
        bot.send_message(call.message.chat.id, "🟣 Rasm yuboring. Caption ichiga text habarni yozing.", reply_markup=back_menu())
    else:
        bot.send_message(call.message.chat.id, "🟢 Admin text yozing, men uni barcha foydalanuvchilarga yuboraman.", reply_markup=back_menu())


@bot.message_handler(content_types=["text", "photo", "video", "document"])
def main_handler(message: types.Message) -> None:
    save_user(message.from_user)

    if is_admin(message.from_user.id) and message.from_user.id in admin_states:
        handle_admin_state(message)
        return

    if not require_subscription(message):
        return

    if message.content_type == "text":
        show_movie_by_code(message.chat.id, message.text.strip())
    else:
        bot.reply_to(message, "🎬 Kino kodini text ko'rinishida yuboring.")


def handle_admin_state(message: types.Message) -> None:
    state = admin_states.get(message.from_user.id, {})
    step = state.get("step")

    if step == "add_sub":
        if message.content_type != "text":
            bot.reply_to(message, "Username yoki linkni text qilib yuboring.")
            return
        try:
            if state["kind"] == "request":
                chat_id, url, title = normalize_request_channel(message.text)
            else:
                chat_id, url, title = normalize_public_chat(message.text)
        except ValueError as exc:
            bot.reply_to(message, f"❌ {exc}")
            return
        channels_col.insert_one({"kind": state["kind"], "title": title, "chat_id": chat_id, "url": url, "created_at": now()})
        admin_states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, "✅ Majburiy obuna qo'shildi.", reply_markup=admin_menu())
        return

    if step == "broadcast_text":
        if message.content_type != "text":
            bot.reply_to(message, "Text habar yuboring.")
            return
        bot.send_message(message.chat.id, "📢 Habar yuborish boshlandi...")
        ok, failed = broadcast_text(message.text)
        admin_states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, f"✅ Yuborildi: {ok}\n❌ Yuborilmadi: {failed}", reply_markup=admin_menu())
        return

    if step == "broadcast_photo":
        if message.content_type != "photo":
            bot.reply_to(message, "Rasm yuboring. Textni captionga yozing.")
            return
        bot.send_message(message.chat.id, "📢 Rasmli habar yuborish boshlandi...")
        ok, failed = broadcast_photo(message.photo[-1].file_id, message.caption or "")
        admin_states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, f"✅ Yuborildi: {ok}\n❌ Yuborilmadi: {failed}", reply_markup=admin_menu())
        return

    if step == "movie_code":
        if message.content_type != "text":
            bot.reply_to(message, "Kino kodini text qilib yozing.")
            return
        code = message.text.strip()
        if not code:
            bot.reply_to(message, "Kod bo'sh bo'lmasin.")
            return
        admin_states[message.from_user.id] = {"step": "movie_content", "code": code}
        bot.send_message(message.chat.id, "Endi kino faylini yuboring: video, document, photo yoki text.", reply_markup=back_menu())
        return

    if step == "movie_content":
        code = state["code"]
        content_type = message.content_type
        file_id = text = caption = None
        if content_type == "video":
            file_id, caption = message.video.file_id, message.caption
        elif content_type == "document":
            file_id, caption = message.document.file_id, message.caption
        elif content_type == "photo":
            file_id, caption = message.photo[-1].file_id, message.caption
        elif content_type == "text":
            text = message.text
        else:
            bot.reply_to(message, "Faqat video, document, photo yoki text yuboring.")
            return

        movies_col.update_one(
            {"code": code},
            {
                "$set": {
                    "code": code,
                    "content_type": content_type,
                    "file_id": file_id,
                    "text": text,
                    "caption": caption,
                    "updated_at": now(),
                },
                "$setOnInsert": {"created_at": now()},
            },
            upsert=True,
        )
        admin_states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, f"✅ Kino saqlandi. Kod: <code>{code}</code>", reply_markup=admin_menu())


@app.route("/")
def healthcheck():
    return {"ok": True, "service": "telegram-kino-bot", "db": MONGODB_DB}

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        print(">>> KELGAN UPDATE:", json_string)
        try:
            update = types.Update.de_json(json_string)
            bot.process_new_updates([update])
        except Exception as e:
            print(">>> XATOLIK YUZ BERDI:", e)
        return {"ok": True}
    else:
        abort(403)

def setup_webhook() -> None:
    if PUBLIC_BASE_URL:
        webhook_url = f"{PUBLIC_BASE_URL}/webhook/{WEBHOOK_SECRET}"
        bot.remove_webhook()
        bot.set_webhook(webhook_url)


def run() -> None:
    init_db()
    if PUBLIC_BASE_URL:
        setup_webhook()
        app.run(host="0.0.0.0", port=PORT)
    else:
        bot.remove_webhook()
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


init_db()
setup_webhook()


if __name__ == "__main__":
    run()
