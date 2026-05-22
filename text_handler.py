# text_handler.py
import re
import logging
from aiogram.types import Message

logger = logging.getLogger(__name__)

def clean_summary(text: str, tags: list) -> str:
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()


def get_thread_prefix(message: Message, thread_prefixes: dict) -> str:
    """Возвращает префикс подзадачи по thread_id"""
    return thread_prefixes.get(message.message_thread_id, '')


async def process_text_message(
        message: Message,
        TRIGGER_TAGS: list,
        CHECK_TAG: str,
        THREAD_PREFIXES: dict,
        create_jira_ticket,
        bot,
        JIRA_URL: str
):
    text = message.text or ""
    text_lower = text.lower()

    if CHECK_TAG in text_lower:
        await message.reply("✅ Бот работает и готов принимать задачи.")
        return

    # Обнаружены триггеры (#bug / #jira)
    if any(tag in text_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")

        prefix = get_thread_prefix(message, THREAD_PREFIXES)

        success, issue_key = await create_jira_ticket(
            text=text,
            author=message.from_user.full_name,
            file_bytes=None,
            filename=None,
            thread_prefix=prefix
        )

        if success:
            await message.reply(
                f"✅ Задача <b>{issue_key}</b> создана!\n"
                f"🔗 <a href='{JIRA_URL}/browse/{issue_key}'>{JIRA_URL}/browse/{issue_key}</a>"
            )
        else:
            await message.reply("❌ Ошибка при создании задачи в Jira.")
