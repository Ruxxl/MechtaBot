import logging
from typing import Optional, Tuple, List
from aiogram import types
from aiogram.types import Message

from main import bot, create_jira_ticket, TRIGGER_TAGS, CHECK_TAG, THREAD_PREFIXES

logger = logging.getLogger(__name__)

def get_thread_prefix(message: Message) -> str:
    """Возвращает префикс подзадачи по thread_id"""
    return THREAD_PREFIXES.get(message.message_thread_id, '')

def clean_summary(text: str, tags: List[str]) -> str:
    """Удаляет заданные теги из текста"""
    for tag in tags:
        text = text.replace(tag, '')
    return ' '.join(text.split()).strip()

async def handle_text_message(message: Message):
    text = message.text or ""
    text_lower = text.lower()
    logger.info(f"✉️ Получено сообщение: {text}")

    if CHECK_TAG in text_lower:
        await message.reply("✅ Бот работает и готов принимать задачи.")
        return

    if any(tag in text_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        success, issue_key = await create_jira_ticket(
            text,
            message.from_user.full_name,
            file_bytes=None,
            filename=None,
            thread_prefix=get_thread_prefix(message)
        )
        if success:
            await message.reply(
                f"✅ Задача <b>{issue_key}</b> создана!\n"
                f"🔗 <a href='{bot.url}/browse/{issue_key}'>{bot.url}/browse/{issue_key}</a>"
            )
        else:
            await message.reply("❌ Ошибка при создании задачи в Jira.")
