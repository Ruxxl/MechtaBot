import asyncio
import logging
from aiogram import Dispatcher, F
from aiogram.types import Message
from admin_handler import monitor
from deep_translator import YandexTranslator

logger = logging.getLogger("bot.translator")


async def translate_ru_to_kk(text: str) -> str:
    """
    Переводит текст с русского на казахский через Yandex (бесплатно, без ключа).
    """
    try:
        translated = await asyncio.to_thread(
            lambda: YandexTranslator(source='ru', target='kk').translate(text)
        )
        return translated or ""
    except Exception as e:
        logger.error(f"Ошибка YandexTranslator при переводе (текст: {text[:20]}...): {e}")
        return ""


def register_translator_handlers(dp: Dispatcher, translation_thread_id: int, api_key: str = None):
    """
    Регистрирует хендлер, который слушает только один конкретный thread_id.
    api_key оставлен для обратной совместимости с main.py, но больше не используется.
    """
    @dp.message(F.message_thread_id == translation_thread_id, F.text & ~F.text.startswith("/"))
    async def handle_translation(message: Message):
        monitor.update_status("Translator Service", "OK")
        text = message.text.strip()
        if not text:
            return

        translated_text = await translate_ru_to_kk(text)

        if translated_text and translated_text.lower() != text.lower():
            try:
                await message.reply(translated_text)
            except Exception as e:
                logger.error(f"Не удалось отправить перевод: {e}")
