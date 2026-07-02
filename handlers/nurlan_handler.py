import logging
from typing import Optional

from aiogram import types

logger = logging.getLogger("bot.nurlan")

TARGET_USERNAME = "nurlanseo"
REPLY_TEXT = "Иди работай, хватит мешать"


def is_from_target_user(message: types.Message) -> bool:
    """Проверяет, что сообщение написано пользователем TARGET_USERNAME
    (без учета регистра, т.к. username в Telegram регистронезависим)."""
    username: Optional[str] = message.from_user.username if message.from_user else None
    return bool(username) and username.lower() == TARGET_USERNAME.lower()


async def handle_target_user_message(message: types.Message) -> None:
    try:
        await message.reply(REPLY_TEXT)
        logger.info(f"Ответил {TARGET_USERNAME} в чате {message.chat.id} (топик {message.message_thread_id})")
    except Exception as e:
        logger.error(f"Ошибка ответа {TARGET_USERNAME}: {e}")