import asyncio
import logging
import aiohttp
from aiogram import Dispatcher, F
from aiogram.types import Message
from admin_handler import monitor
from deep_translator import GoogleTranslator

logger = logging.getLogger("bot.translator")

MYMEMORY_URL = "https://api.mymemory.translated.net/get"


async def translate_ru_to_kk(text: str) -> str:
    """
    Переводит текст с русского на казахский через MyMemory API (бесплатно, без ключа).
    Фоллбэк — GoogleTranslator.
    """
    try:
        params = {
            "q": text,
            "langpair": "ru|kk",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(MYMEMORY_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    translated = data.get("responseData", {}).get("translatedText", "")
                    # MyMemory возвращает ошибку текстом если лимит превышен
                    if translated and "MYMEMORY WARNING" not in translated:
                        return translated.strip()
                    logger.warning(f"MyMemory вернул предупреждение: {translated}")
    except Exception as e:
        logger.error(f"Ошибка MyMemory при переводе: {e}")

    # Фоллбэк на Google
    logger.info("Использую GoogleTranslator как fallback")
    try:
        translated = await asyncio.to_thread(
            lambda: GoogleTranslator(source='ru', target='kk').translate(text)
        )
        return translated or ""
    except Exception as e:
        logger.error(f"Ошибка GoogleTranslator fallback: {e}")
        return ""


def register_translator_handlers(dp: Dispatcher, translation_thread_id: int, api_key: str = None):
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
