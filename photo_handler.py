import aiohttp
import ssl
import logging
from aiogram import types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from error_screen_analyzer import extract_text_from_image, analyze_error_text

logger = logging.getLogger(__name__)


async def download_photo_bytes(bot: Bot, file_id: str) -> bytes:
    """Скачивание фото из Telegram."""
    file = await bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession() as session:
        async with session.get(file_url, ssl=ssl_context) as resp:
            if resp.status != 200:
                raise RuntimeError("Ошибка скачивания файла")
            return await resp.read()


async def handle_photo_message(
    bot: Bot,
    message: types.Message,
    trigger_tags: list[str],
    create_jira_ticket
) -> None:
    """
    Основная обработка фото:
    - скачиваем фото
    - распознаём текст (OCR)
    - определяем тип ошибки
    - предлагаем кнопки действий
    """

    caption = message.caption or ""
    caption_lower = caption.lower()
    logger.info(f"📸 Получено фото: {caption}")

    # Если не пришел тег в caption → не считаем что нужно сразу создавать баг
    auto_create = any(tag in caption_lower for tag in trigger_tags)

    try:
        # Берём самое большое фото
        file_id = message.photo[-1].file_id

        # Скачиваем
        photo_bytes = await download_photo_bytes(bot, file_id)
        logger.info("Фото успешно скачано")

        # OCR → получаем текст
        extracted_text = extract_text_from_image(photo_bytes)
        logger.info(f"OCR текст: {extracted_text}")

        # Анализируем текст
        error_info = analyze_error_text(extracted_text)
        patterns = error_info.get("found_patterns", [])
        recommendation = error_info.get("recommendation", "Нет рекомендаций")

        # Формируем ответ
        reply = "📸 <b>Распознан текст:</b>\n"
        reply += extracted_text if extracted_text else "— текст не найден —"

        if patterns:
            reply += "\n\n⚠️ <b>Найденные возможные ошибки:</b>\n"
            for p in patterns:
                reply += f"• {p}\n"

        reply += f"\n\n💡 <b>Рекомендация:</b>\n{recommendation}"

        # Кнопки действий
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🐞 Создать баг", callback_data=f"create_bug_photo:{message.message_id}")],
            [InlineKeyboardButton(text="🔍 Найти похожие бага", callback_data=f"search_bug_photo:{message.message_id}")],
            [InlineKeyboardButton(text="ℹ️ Помощь по ошибке", callback_data=f"help_bug_photo:{message.message_id}")],
        ])

        await message.reply(reply, reply_markup=kb)

        # Сохраняем фото в bot.data — чтобы callback мог его использовать
        if "photo_cache" not in bot.data:
            bot.data["photo_cache"] = {}

        bot.data["photo_cache"][message.message_id] = {
            "bytes": photo_bytes,
            "text": extracted_text,
            "analysis": error_info
        }

        logger.info("Фото кэшировано для будущих callback")

        # Если в тексте был тег → создаём баг автоматически
        if auto_create:
            await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")

            success, issue_key = await create_jira_ticket(
                caption,
                message.from_user.full_name,
                file_bytes=photo_bytes,
                filename="screenshot.jpg"
            )

            if success:
                await message.reply(f"✅ Баг создан: <b>{issue_key}</b>")
            else:
                await message.reply("❌ Не удалось автоматически создать баг.")

    except Exception as e:
        logger.exception("Ошибка обработки фото")
        await message.reply("❌ Ошибка обработки фото. Логи смотри в консоли.")
