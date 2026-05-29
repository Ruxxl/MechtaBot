import asyncio
import logging
from aiogram import Dispatcher, F
from aiogram.types import Message
from groq import AsyncGroq
from admin_handler import monitor
from deep_translator import GoogleTranslator

logger = logging.getLogger("bot.translator")

_client = None

def get_client(api_key: str) -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=api_key)
    return _client

async def translate_ru_to_kk(text: str, api_key: str) -> str:
    try:
        client = get_client(api_key)

        prompt = (
            "Ты — профессиональный переводчик. Переведи следующий текст с русского на казахский язык. "
            "Соблюдай официальный стиль, если это уместно. Ответ должен содержать ТОЛЬКО текст перевода, "
            "без кавычек и лишних пояснений.\n\n"
            f"Текст: {text}"
        )

        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.3
        )

        result = response.choices[0].message.content.strip()
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

def register_translator_handlers(dp: Dispatcher, translation_thread_id: int, api_key: str):
    @dp.message(F.message_thread_id == translation_thread_id, F.text & ~F.text.startswith("/"))
    async def handle_translation(message: Message):
        monitor.update_status("Translator Service", "OK")
        text = message.text.strip()
        if not text:
            return

        translated_text = await translate_ru_to_kk(text, api_key)

        if translated_text and translated_text.lower() != text.lower():
            try:
                await message.reply(translated_text)
            except Exception as e:
                logger.error(f"Не удалось отправить перевод: {e}")