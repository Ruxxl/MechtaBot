import asyncio
import logging
import re
from aiogram import Dispatcher, F
from aiogram.types import Message
from web.admin_handler import monitor
from deep_translator import GoogleTranslator
from services.ai_service import ai_service

logger = logging.getLogger("bot.translator")


def _clean_translation(raw: str) -> str:
    """Убирает возможные кавычки, нумерацию и лишние пробелы в ответе модели."""
    text = raw.strip()
    # убираем обрамляющие кавычки, если модель их всё же добавила
    text = re.sub(r'^["\'«]+|["\'»]+$', '', text.strip())
    # убираем возможную нумерацию в начале строк вида "1. " или "1) "
    text = re.sub(r'(?m)^\s*\d+[\.\)]\s*', '', text)
    return text.strip()


async def translate_ru_to_kk(text: str) -> str:
    try:
        prompt = (
            "Переведи следующий текст с русского на казахский в максимально "
            "табиғи и естественном стиле.\n\n"
            "Ответ дай СТРОГО в таком формате, без нумерации, без кавычек и "
            "без лишних пояснений:\n\n"
            "<основной вариант перевода>\n\n"
            "Альтернативті вариант: <альтернативный вариант перевода>\n\n"
            "Если хорошего альтернативного варианта нет — просто не добавляй "
            "вторую часть и выведи только основной вариант.\n\n"
            f"Текст: {text}"
        )

        result = await ai_service.generate_gemini(
            prompt=prompt,
            max_tokens=1000,
            temperature=0.3
        )
        return _clean_translation(result) if result else ""

    except Exception as e:
        error_msg = str(e)

        # Если исчерпана квота — используем запасной переводчик
        if "429" in error_msg or "rate_limit" in error_msg.lower():
            logger.warning("Квота Gemini исчерпана, использую запасной переводчик (GoogleTranslator)")
            try:
                translated = await asyncio.to_thread(
                    lambda: GoogleTranslator(source='ru', target='kk').translate(text)
                )
                return translated
            except Exception as fallback_error:
                logger.error(f"Ошибка запасного переводчика: {fallback_error}")
                return ""

        logger.error(f"Ошибка Gemini при переводе (текст: {text[:20]}...): {e}")
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
