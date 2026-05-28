import asyncio
import logging
from aiogram import Dispatcher, F
from aiogram.types import Message
import google.generativeai as genai

logger = logging.getLogger("bot.translator")

async def translate_ru_to_kk(text: str, api_key: str) -> str:
    """
    Выполняет перевод текста с русского на казахский с помощью Google Gemini.
    """
    if not api_key:
        logger.error("GEMINI_API_KEY не задан. Перевод невозможен.")
        return ""

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = (
            "Ты — профессиональный переводчик. Переведи следующий текст с русского на казахский язык. "
            "Соблюдай официальный стиль, если это уместно. Ответ должен содержать ТОЛЬКО текст перевода, "
            "без кавычек и лишних пояснений.\n\n"
            f"Текст: {text}"
        )

        # Запускаем генерацию в отдельном потоке, чтобы не блокировать event loop
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Ошибка Gemini при переводе: {e}")
        return ""

def register_translator_handlers(dp: Dispatcher, translation_thread_id: int, api_key: str):
    """
    Регистрирует хендлер, который слушает только один конкретный thread_id.
    """
    @dp.message(F.message_thread_id == translation_thread_id, F.text & ~F.text.startswith("/"))
    async def handle_translation(message: Message):
        text = message.text.strip()
        if not text: return

        translated_text = await translate_ru_to_kk(text, api_key)
        
        # Отправляем ответ только если перевод успешен и результат отличается от оригинала
        if translated_text and translated_text.lower() != text.lower():
            try:
                await message.reply(translated_text)
            except Exception as e:
                logger.error(f"Не удалось отправить перевод: {e}")