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

# Создаем экземпляр для импорта в другие файлы
ai_service = AIService()