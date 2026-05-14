import os
import google.generativeai as genai
import logging
import json
from aiohttp import web

logger = logging.getLogger("bot.ai_generator")

# Инициализация Gemini
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    logger.warning("⚠️ GEMINI_API_KEY не найден в переменных окружения!")

async def handle_generate_tests(request):
    try:
        data = await request.json()
        requirements = data.get("requirements", "")
        config = data.get("config", {}) # Получаем настройки из HTML
        
        # Собираем инструкции на основе конфига
        lang = "Русский" if config.get("lang") == "ru" else "English"
        max_cases = config.get("maxCases", 10)
        
        prompt = f"""
        Ты профессиональный Lead QA Engineer. 
        На основе требований составь список тест-кейсов.
        
        ТРЕБОВАНИЯ:
        {requirements}
        
        ПАРАМЕТРЫ:
        - Язык: {lang}
        - Максимум кейсов: {max_cases}
        - Формат: {config.get('format', 'standard')}
        - Негативные сценарии: {'Включить' if config.get('negative') else 'Нет'}
        - Граничные значения: {'Включить' if config.get('boundary') else 'Нет'}
        
        Ответ верни СТРОГО в формате JSON массива объектов:
        [
          {{
            "id": "TC-1",
            "name": "Название",
            "priority": "High/Medium/Low",
            "preconditions": "Предусловия",
            "steps": ["Шаг 1", "Шаг 2"],
            "expected": "Результат",
            "type": "positive/negative"
          }}
        ]
        """

        response = await model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        # Добавляем CORS заголовки, чтобы браузер не блокировал запрос
        return web.Response(
            text=response.text, 
            content_type='application/json',
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            }
        )
    
    except Exception as e:
        logger.error(f"Ошибка в ai_generator: {e}")
        return web.json_response({"error": str(e)}, status=500)
