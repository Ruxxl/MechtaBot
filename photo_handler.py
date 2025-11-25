from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from error_screen_analyzer import extract_text_from_image, analyze_error_text

async def handle_photo_message(
        bot,
        message,
        trigger_tags,
        create_jira_ticket
):
    caption = message.caption or ""
    caption_lower = caption.lower()

    file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"

    async with aiohttp.ClientSession() as session:
        async with session.get(file_url, ssl=False) as resp:
            photo_bytes = await resp.read()

    # ========================
    # 1) OCR: извлечение текста
    # ========================
    extracted_text = await extract_text_from_image(photo_bytes)

    # ========================
    # 2) Анализ ошибок
    # ========================
    error_info = analyze_error_text(extracted_text)

    reply_text = "📸 <b>Распознан текст:</b>\n" + (error_info["raw_text"] or "— нет текста —")

    if error_info["matched_errors"]:
        reply_text += "\n\n⚠️ <b>Возможные ошибки:</b>\n"
        for e in error_info["matched_errors"]:
            reply_text += f"• {e}\n"

    # Кнопки действий
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐞 Создать баг", callback_data="create_bug_from_photo")],
        [InlineKeyboardButton(text="🔍 Найти похожие баги", callback_data="search_similar_from_photo")],
        [InlineKeyboardButton(text="ℹ️ Помощь по ошибке", callback_data="error_help")]
    ])

    await message.reply(reply_text, reply_markup=kb)

    # сохраняем фото в message.bot_data, чтобы потом использовать
    bot_data = message.bot.get("photo_cache", {})
    bot_data[message.message_id] = photo_bytes
    message.bot["photo_cache"] = bot_data
