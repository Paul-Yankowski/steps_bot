"""
Telegram-бот для шагового марафона.

Логика:
1. Кто-то в чате присылает фото (скриншот шагов).
2. Бот скачивает фото максимального размера, прогоняет через OCR (ocr.py).
3. Бот отвечает на скриншот с распознанным числом и кнопками:
      ✅ Верно      -> сразу сохраняет в Google Sheets
      ✏️ Исправить  -> просит прислать правильное число текстом
4. Если за сегодня для этого участника уже есть запись — предупреждает
   и спрашивает подтверждение перезаписи (защита от случайного дублирования).
5. Каждая запись хранит статус (auto/confirmed/manual) и ссылку на
   исходное сообщение — для разрешения споров.

ВАЖНО: у бота должен быть отключен Privacy Mode (см. README), иначе
он не увидит сообщения других участников в группе.
"""

import logging
import os
from datetime import datetime, date

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

from ocr import recognize_steps
from sheets import StepsSheet

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]


def resolve_credentials_path() -> str:
    """
    Определяет путь к JSON-ключу сервисного аккаунта Google.

    Поддерживает два способа задания:
      - GOOGLE_CREDENTIALS_JSON — содержимое JSON-ключа целиком в переменной
        окружения (удобно для платформ без "секретных файлов", например
        Railway) — записывается во временный файл.
      - GOOGLE_CREDENTIALS_PATH — путь к уже существующему файлу на диске
        (локальный запуск, Raspberry Pi, Render Secret Files).
    """
    raw_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if raw_json:
        json.loads(raw_json)  # валидация, чтобы упасть с понятной ошибкой сразу
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(raw_json)
        tmp.close()
        logger.info("Using Google credentials from GOOGLE_CREDENTIALS_JSON env var")
        return tmp.name

    return os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")


GOOGLE_CREDENTIALS_PATH = resolve_credentials_path()

sheet = StepsSheet(GOOGLE_CREDENTIALS_PATH, SPREADSHEET_ID)

# Временное хранилище незавершённых распознаваний: {message_id: {...}}
pending: dict[int, dict] = {}
# Кто сейчас должен прислать ручное исправление: {user_id: message_id}
awaiting_manual: dict[int, int] = {}


def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name


def message_link(chat, message_id: int) -> str:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    # для приватных групп ссылка работает только у участников чата
    internal_id = str(chat.id).replace("-100", "")
    return f"https://t.me/c/{internal_id}/{message_id}"


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    photo = message.photo[-1]  # самое большое разрешение
    file = await photo.get_file()
    photo_bytes = await file.download_as_bytearray()

    processing_msg = await message.reply_text("🔎 Распознаю шаги...")

    steps, confidence, _raw_text = recognize_steps(bytes(photo_bytes))

    if steps is None:
        await processing_msg.edit_text(
            "❌ Не удалось распознать число шагов на скриншоте.\n"
            "Пришли, пожалуйста, число текстом в ответ на это сообщение."
        )
        awaiting_manual[user.id] = message.message_id
        pending[message.message_id] = {
            "user_id": user.id,
            "username": display_name(user),
            "chat_id": chat.id,
            "steps": None,
            "link": message_link(chat, message.message_id),
        }
        return

    today_str = date.today().isoformat()
    name = display_name(user)

    try:
        existing = sheet.get_existing(today_str, name)
    except Exception:
        logger.exception("Failed to reach Google Sheets while checking existing entry")
        await processing_msg.edit_text(
            f"👤 {name}\n👣 Распознано: {steps} шагов\n\n"
            "⚠️ Не удалось проверить таблицу (проблема с сетью/Google Sheets), "
            "но подтвердить и сохранить всё равно можно — попробую ещё раз при сохранении."
        )
        existing = None

    warn = ""
    if existing:
        warn = f"\n⚠️ За сегодня уже есть запись: {existing[0]} шагов. Подтверждение перезапишет её."

    confidence_note = "" if confidence == "keyword" else "\n(число подобрано эвристически, перепроверь внимательно)"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Верно", callback_data=f"confirm:{message.message_id}"),
            InlineKeyboardButton("✏️ Исправить", callback_data=f"fix:{message.message_id}"),
        ]
    ])

    pending[message.message_id] = {
        "user_id": user.id,
        "username": name,
        "chat_id": chat.id,
        "steps": steps,
        "link": message_link(chat, message.message_id),
    }

    await processing_msg.edit_text(
        f"👤 {name}\n👣 Распознано: {steps} шагов{confidence_note}{warn}\n\nВсё верно?",
        reply_markup=keyboard,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    action, msg_id_str = query.data.split(":")
    msg_id = int(msg_id_str)

    entry = pending.get(msg_id)
    if not entry:
        await query.answer("Данные устарели, пришли скриншот заново.", show_alert=True)
        return

    if entry["user_id"] != user.id:
        await query.answer("Подтвердить может только автор скриншота.", show_alert=True)
        return

    if action == "confirm":
        today_str = date.today().isoformat()
        try:
            sheet.upsert_entry(today_str, entry["username"], entry["steps"], "confirmed", entry["link"])
        except Exception:
            logger.exception("Failed to save confirmed entry to Google Sheets")
            await query.answer()
            await query.edit_message_text(
                f"⚠️ Не удалось сохранить в таблицу — проблема связи с Google Sheets.\n"
                f"Данные не потеряны: {entry['username']} — {entry['steps']} шагов. "
                f"Нажми ✅ ещё раз, когда связь восстановится.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Верно", callback_data=f"confirm:{msg_id}"),
                    InlineKeyboardButton("✏️ Исправить", callback_data=f"fix:{msg_id}"),
                ]]),
            )
            return

        await query.edit_message_text(
            f"✅ Сохранено: {entry['username']} — {entry['steps']} шагов ({today_str})"
        )
        pending.pop(msg_id, None)
        await query.answer()

    elif action == "fix":
        awaiting_manual[user.id] = msg_id
        await query.edit_message_text(
            f"✏️ Пришли правильное число шагов текстом в ответ на скриншот от {entry['username']}."
        )
        await query.answer()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловит ручное исправление числа после нажатия '✏️ Исправить'."""
    user = update.effective_user
    if user.id not in awaiting_manual:
        return  # обычное сообщение в чате, не для нас

    msg_id = awaiting_manual.pop(user.id)
    entry = pending.get(msg_id)
    if not entry:
        return

    text = update.effective_message.text.strip().replace(" ", "")
    if not text.isdigit():
        await update.effective_message.reply_text("Нужно просто число, например: 12500. Попробуй ещё раз.")
        awaiting_manual[user.id] = msg_id
        return

    steps = int(text)
    today_str = date.today().isoformat()
    try:
        sheet.upsert_entry(today_str, entry["username"], steps, "manual", entry["link"])
    except Exception:
        logger.exception("Failed to save manual entry to Google Sheets")
        await update.effective_message.reply_text(
            "⚠️ Не удалось сохранить в таблицу — проблема связи с Google Sheets. "
            "Попробуй прислать число ещё раз чуть позже."
        )
        awaiting_manual[user.id] = msg_id  # вернуть в ожидание, чтобы можно было повторить
        return

    await update.effective_message.reply_text(
        f"✅ Сохранено вручную: {entry['username']} — {steps} шагов ({today_str})"
    )
    pending.pop(msg_id, None)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = date.today().isoformat()
    records = sheet.ws.get_all_values()[1:]
    todays = [r for r in records if r[0] == today_str]
    if not todays:
        await update.effective_message.reply_text("Сегодня ещё никто не прислал результат.")
        return
    todays.sort(key=lambda r: int(r[2]), reverse=True)
    lines = [f"{i+1}. {r[1]} — {r[2]} шагов" for i, r in enumerate(todays)]
    await update.effective_message.reply_text("📊 Результаты за сегодня:\n" + "\n".join(lines))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Ловит все необработанные исключения, чтобы бот не падал молча."""
    logger.error("Unhandled exception while processing update: %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка при обработке. Попробуйте ещё раз через минуту."
            )
        except Exception:
            pass


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    logger.info("Bot started, polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
