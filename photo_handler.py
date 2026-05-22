import os
import logging
from typing import Callable
from aiogram import Bot, types
from text_handler import get_thread_prefix, THREAD_PREFIXES

logger = logging.getLogger(__name__)

async def handle_photo_message(
    bot: Bot,
    message: types.Message,
    trigger_tags: list[str],
    create_jira_ticket: Callable
) -> None:
    caption = message.caption or ""
    caption_lower = caption.lower()

    if not any(tag in caption_lower for tag in trigger_tags):
        return

    await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
    prefix = get_thread_prefix(message, THREAD_PREFIXES)

    # Создаём Jira задачу
    success, issue_key = await create_jira_ticket(
        text=caption,
        author=message.from_user.full_name,
        file_bytes=None, # Обрабатывается внутри create_jira_issue через file_id
        filename=None,
        thread_prefix=prefix
    )

    if success:
        await message.reply(
            f"✅ Задача <b>{issue_key}</b> создана!\n"
            f"🔗 <a href='{os.getenv('JIRA_URL')}/browse/{issue_key}'>{os.getenv('JIRA_URL')}/browse/{issue_key}</a>"
        )
    else:
        await message.reply("❌ Ошибка при создании задачи в Jira.")
