import logging
from groq import AsyncGroq

logger = logging.getLogger("bot.ai_service")

class AIService:
    def __init__(self):
        self.groq_client = None

    def init_groq(self, api_key: str):
        if api_key:
            try:
                self.groq_client = AsyncGroq(api_key=api_key)
                logger.info("✅ Groq service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to init Groq: {e}")

    async def generate_groq(self, prompt: str, model: str = "llama-3.3-70b-versatile", temperature: float = 0.7, max_tokens: int = 500):
        if not self.groq_client:
            raise ValueError("Groq client not initialized")
        
        response = await self.groq_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature
        )
        return response.choices[0].message.content.strip()

    async def analyze_image_groq(self, image_b64: str, caption: str = "", max_tokens: int = 1024):
        """Анализирует изображение через Groq Vision (llama-4-scout)."""
        if not self.groq_client:
            raise ValueError("Groq client not initialized")

        user_content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            },
            {
                "type": "text",
                "text": caption if caption else "Проанализируй этот скриншот. Если есть ошибки — объясни причину и предложи решение.",
            },
        ]

        response = await self.groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — помощник для IT-команды. Анализируешь скриншоты.\n"
                        "Если видишь ошибку (exception, stack trace, HTTP error, баг UI) — "
                        "объясни причину и дай конкретные шаги для исправления.\n"
                        "Если это просто интерфейс или код — кратко опиши что видишь и предложи помощь.\n"
                        "Отвечай на русском языке. Без длинных вступлений — сразу к делу."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

# Создаем экземпляр для импорта в другие файлы
ai_service = AIService()