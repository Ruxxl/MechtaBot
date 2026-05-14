import os
import google.generativeai as genai
import logging
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
    """Эндпоинт для генерации тест-кейсов через Gemini AI"""
    try:
        data = await request.json()
        requirements = data.get("requirements", "No requirements provided")
        
        prompt = f"""
        Ты профессиональный Lead QA Engineer. 
        На основе следующих требований составь список тест-кейсов в формате JSON.
        
        Требования: {requirements}
        
        Ответ должен быть СТРОГО в формате JSON массива:
        [
          {{
            "id": "TC-1",
            "name": "Название теста",
            "priority": "High/Medium/Low",
            "steps": ["Шаг 1", "Шаг 2"],
            "expected": "Ожидаемый результат"
          }}
        ]
        Никакого лишнего текста, только JSON.
        """

        response = await model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        return web.Response(text=response.text, content_type='application/json')
    
    except Exception as e:
        logger.error(f"Ошибка в ai_generator: {e}")
        return web.json_response({"error": str(e)}, status=500)
