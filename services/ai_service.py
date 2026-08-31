import base64
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("bot.ai_service")

VISION_SYSTEM_PROMPT = (
    "Ты — помощник для IT-команды. Анализируешь скриншоты.\n"
    "Всегда отвечай в таком формате:\n"
    "1) Сначала кратко (1-2 предложения) опиши, что изображено на скриншоте "
    "(какой экран, какой раздел, что происходит).\n"
    "2) Если на скриншоте есть ошибка (exception, stack trace, HTTP-код ошибки "
    "вроде 4xx/5xx, красный текст в консоли/DevTools, баг в интерфейсе) — "
    "опиши какая именно ошибка, в чём вероятная причина, и предложи конкретные шаги "
    "для исправления.\n"
    "3) Если явных ошибок нет — просто укажи, что выглядит штатно, без лишних слов.\n"
    "Не пиши общих фраз вроде 'на скриншоте видно интерфейс' без конкретики. "
    "Указывай конкретные элементы: текст ошибок, коды статусов, названия полей и т.д., "
    "если они видны.\n"
    "Отвечай на русском языке, без длинных вступлений."
)

class AIService:
    def __init__(self):
        self.gemini_client = None
        self.gemini_model = "gemini-flash-lite-latest"

    def init_gemini(self, api_key: str, model: str = "gemini-flash-lite-latest"):
        if api_key:
            try:
                self.gemini_client = genai.Client(api_key=api_key)
                self.gemini_model = model
                logger.info("✅ Gemini service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to init Gemini: {e}")

    async def generate_gemini(self, prompt: str, temperature: float = 0.7, max_tokens: int = 500):
        if not self.gemini_client:
            raise ValueError("Gemini client not initialized")

        response = await self.gemini_client.aio.models.generate_content(
            model=self.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return (response.text or "").strip()

    async def analyze_image_gemini(self, image_b64: str, caption: str = "", max_tokens: int = 1024):
        """Анализирует изображение через Gemini Vision."""
        if not self.gemini_client:
            raise ValueError("Gemini client not initialized")

        image_bytes = base64.b64decode(image_b64)
        user_text = caption if caption else "Проанализируй этот скриншот. Если есть ошибки — объясни причину и предложи решение."

        response = await self.gemini_client.aio.models.generate_content(
            model=self.gemini_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                f"{VISION_SYSTEM_PROMPT}\n\n{user_text}",
            ],
            config=types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        return (response.text or "").strip()

# Создаем экземпляр для импорта в другие файлы
ai_service = AIService()
