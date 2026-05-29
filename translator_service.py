import asyncio
import logging
from aiogram import Dispatcher, F
from aiogram.types import Message
from google import genai
from admin_handler import monitor
from deep_translator import GoogleTranslator

logger = logging.getLogger("bot.translator")

# Кэшируем клиент, чтобы не создавать его при каждом сообщении
_client = None

def get_client(api_key: str):
    global _client
    if _client is None:
        _client = genai.Client(api_key=api_key)
    return _client

async def translate_ru_to_kk(text: str, api_key: str) -> str:
    """
    Выполняет перевод текста с русского на казахский с помощью Google Gemini.
    """
    try:
        client = get_client(api_key)

        prompt = (
            "Ты — профессиональный переводчик. Переведи следующий текст с русского на казахский язык. "
            "Соблюдай официальный стиль, если это уместно. Ответ должен содержать ТОЛЬКО текст перевода, "
            "без кавычек и лишних пояснений.\n\n"
            f"Текст: {text}"
        )

        # Запускаем генерацию в отдельном потоке, чтобы не блокировать event loop
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.0-flash",
            contents=prompt
        )
        
        if response and response.text:
            return response.text.strip()
            
        return ""
    except Exception as e:
        error_msg = str(e)
        # Если исчерпана квота (429), используем запасной вариант
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
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

def register_translator_handlers(dp: Dispatcher, translation_thread_id: int, api_key: str):
    """
    Регистрирует хендлер, который слушает только один конкретный thread_id.
    """
    @dp.message(F.message_thread_id == translation_thread_id, F.text & ~F.text.startswith("/"))
    async def handle_translation(message: Message):
        monitor.update_status("Translator Service", "OK")
        text = message.text.strip()
        if not text: return

        translated_text = await translate_ru_to_kk(text, api_key)
        
        # Отправляем ответ только если перевод успешен и результат отличается от оригинала
        if translated_text and translated_text.lower() != text.lower():
            try:
                await message.reply(translated_text)
            except Exception as e:
                logger.error(f"Не удалось отправить перевод: {e}")