import asyncio
import logging
from aiogram import Dispatcher, F
from aiogram.types import Message
from deep_translator import GoogleTranslator

logger = logging.getLogger("bot.translator")

async def translate_ru_to_kk(text: str) -> str:
    """
    Выполняет перевод текста с русского на казахский в отдельном потоке.
    """
    try:
        # Используем asyncio.to_thread для запуска синхронной библиотеки в асинхронной среде
        translated = await asyncio.to_thread(
            lambda: GoogleTranslator(source='ru', target='kk').translate(text)
        )
        return translated
    except Exception as e:
        logger.error(f"Ошибка при переводе текста: {e}")
        return ""

def register_translator_handlers(dp: Dispatcher, translation_thread_id: int):
    """
    Регистрирует хендлер, который слушает только один конкретный thread_id.
    """
    @dp.message(F.message_thread_id == translation_thread_id, F.text & ~F.text.startswith("/"))
    async def handle_translation(message: Message):
        text = message.text.strip()
        if not text:
            return

        translated_text = await translate_ru_to_kk(text)
        
        # Отправляем ответ только если перевод успешен и результат отличается от оригинала
        if translated_text and translated_text.lower() != text.lower():
            try:
                await message.reply(translated_text)
            except Exception as e:
                logger.error(f"Не удалось отправить перевод: {e}")