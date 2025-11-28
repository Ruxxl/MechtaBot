import aiohttp
import ssl
import os
import logging
from typing import Optional, Tuple
from aiogram import Bot, types

logger = logging.getLogger(__name__)

async def handle_photo_message(
    bot: Bot,
    message: types.Message,
    trigger_tags: list[str],
    create_jira_ticket
) -> None:
    """
    Обработка фото-сообщения Telegram:
    - скачивает фото
    - создает задачу Jira, если есть тег
    """
    caption = message.caption or ""
    caption_lower = caption.lower()
    logger.info(f"📸 Получено фото: {caption}")

    if not any(tag in caption_lower for tag in trigger_tags):
        return

    await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")

    file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession() as session:
        async with session.get(file_url, ssl=ssl_context) as resp:
            if resp.status != 200:
                await message.reply("❌ Не удалось скачать фото с Telegram.")
                return
            photo_bytes = await resp.read()

    # Создаём Jira задачу
    success, issue_key = await create_jira_ticket(
        caption,
        message.from_user.full_name,
        file_bytes=photo_bytes,
        filename="telegram_photo.jpg",
        thread_prefix=getattr(message, "thread_prefix", "")
    )

    if success:
        await message.reply(
            f"✅ Задача <b>{issue_key}</b> создана!\n"
            f"🔗 <a href='{os.getenv('JIRA_URL')}/browse/{issue_key}'>{os.getenv('JIRA_URL')}/browse/{issue_key}</a>"
        )
    else:
        await message.reply("❌ Ошибка при создании задачи в Jira.")
