import logging
from typing import Optional

from aiogram import Bot, types

logger = logging.getLogger("bot.personal_photo")


async def handle_personal_photo(
    bot: Bot,
    message: types.Message,
    target_group_id: int,
    target_thread_id: Optional[int] = None,
) -> None:
    """
    Пересылает фото, присланное боту в личные сообщения, в основной чат
    (с указанием автора, без участия Jira-логики).
    """
    try:
        photo = message.photo[-1]  # лучшее качество
        author = message.from_user.full_name if message.from_user else "Неизвестный"
        original_caption = message.caption or ""

        caption = f"📸 Фото от <b>{author}</b>"
        if original_caption:
            caption += f"\n\n{original_caption}"

        await bot.send_photo(
            chat_id=target_group_id,
            message_thread_id=target_thread_id,
            photo=photo.file_id,
            caption=caption,
        )

        await message.reply("✅ Фото отправлено в основной чат.")
        logger.info(f"Фото от {author} переслано в группу {target_group_id} (топик {target_thread_id})")

    except Exception as e:
        logger.error(f"Ошибка пересылки личного фото в группу: {e}")
        await message.reply("❌ Не удалось отправить фото в основной чат.")


async def handle_personal_text(
    bot: Bot,
    message: types.Message,
    target_group_id: int,
    target_thread_id: Optional[int] = None,
) -> None:
    """
    Пересылает текстовое сообщение, присланное боту в личные сообщения,
    в основной чат (с указанием автора, без участия Jira-логики).
    """
    try:
        author = message.from_user.full_name if message.from_user else "Неизвестный"
        text = message.text or ""

        forward_text = f"💬 Сообщение от <b>{author}</b>\n\n{text}"

        await bot.send_message(
            chat_id=target_group_id,
            message_thread_id=target_thread_id,
            text=forward_text,
        )

        await message.reply("✅ Сообщение отправлено в основной чат.")
        logger.info(f"Текст от {author} переслан в группу {target_group_id} (топик {target_thread_id})")

    except Exception as e:
        logger.error(f"Ошибка пересылки личного текста в группу: {e}")
        await message.reply("❌ Не удалось отправить сообщение в основной чат.")