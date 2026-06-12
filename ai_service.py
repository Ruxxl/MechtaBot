import logging
from groq import AsyncGroq
import google.generativeai as genai

logger = logging.getLogger("bot.ai_service")

class AIService:
    def __init__(self):
        self.groq_client = None
        self.gemini_model = None

    def init_groq(self, api_key: str):
        if api_key:
            try:
                self.groq_client = AsyncGroq(api_key=api_key)
                logger.info("✅ Groq service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to init Groq: {e}")

    def init_gemini(self, api_key: str, model_name: str = "gemini-1.5-flash"):
        if api_key:
            try:
                genai.configure(api_key=api_key)
                self.gemini_model = genai.GenerativeModel(model_name)
                logger.info(f"✅ Gemini service initialized ({model_name})")
            except Exception as e:
                logger.error(f"❌ Failed to init Gemini: {e}")

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

    async def generate_gemini(self, prompt: str):
        if not self.gemini_model:
            raise ValueError("Gemini client not initialized")
        
        response = await self.gemini_model.generate_content_async(prompt)
        return response.text.strip()

# Создаем экземпляр для импорта в другие файлы
ai_service = AIService()