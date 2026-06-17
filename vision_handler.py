import base64
import logging
from aiogram import Bot
from aiogram.types import Message
from ai_service import ai_service
from admin_handler import monitor

logger = logging.getLogger("bot.vision")


async def handle_vision_message(bot: Bot, message: Message) -> None:
    """
    Скачивает фото, отправляет в Groq Vision (llama-4-scout),
    возвращает анализ пользователю.
    """
    caption = message.caption or ""
    photo = message.photo[-1]  # наилучшее качество

    thinking_msg = await message.reply("🔍 Анализирую скриншот...")

    try:
        # Скачиваем фото в память
        file_info = await bot.get_file(photo.file_id)
        downloaded = await bot.download_file(file_info.file_path)
        image_bytes = downloaded.read()

        # Кодируем в base64
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

        # Анализируем через Groq Vision
        result = await ai_service.analyze_image_groq(
            image_b64=image_b64,
            caption=caption,
        )

        monitor.update_status("Vision Service", "OK")
        await thinking_msg.delete()
        await message.reply(result)

    except Exception as e:
        logger.error(f"Ошибка анализа изображения: {e}")
        monitor.update_status("Vision Service", f"ERROR: {e}")
        await thinking_msg.delete()
        await message.reply("❌ Не удалось проанализировать скриншот. Попробуй ещё раз.")