import asyncio
import logging
from aiogram import Dispatcher, F
from aiogram.types import Message
from admin_handler import monitor
from deep_translator import GoogleTranslator
from ai_service import ai_service

logger = logging.getLogger("bot.translator")

async def translate_ru_to_kk(text: str) -> str:
    try:
        prompt = (
            "Переведи на казахский в максимально табиғи и естественном стиле. "
            "Дай самый лучший основной вариант перевода. "
            "Если нужно, добавь 1–2 альтернативных варианта.\n\n"
            f"Текст: {text}"
        )

        result = await ai_service.generate_groq(
            prompt=prompt,
            max_tokens=1000,
            temperature=0.3
        )
        return result

    except Exception as e:
        error_msg = str(e)

        # Если исчерпана квота — используем запасной переводчик
        if "429" in error_msg or "rate_limit" in error_msg.lower():
            logger.warning("Квота Groq исчерпана, использую запасной переводчик (GoogleTranslator)")
            try:
                translated = await asyncio.to_thread(
                    lambda: GoogleTranslator(source='ru', target='kk').translate(text)
                )
                return translated
            except Exception as fallback_error:
                logger.error(f"Ошибка запасного переводчика: {fallback_error}")
                return ""

        logger.error(f"Ошибка Groq при переводе (текст: {text[:20]}...): {e}")
        return ""

def register_translator_handlers(dp: Dispatcher, translation_thread_id: int):
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
