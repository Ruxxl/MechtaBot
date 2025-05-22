import asyncio
import aiohttp
import ssl
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# === Конфигурация токенов и Jira данных ===
BOT_TOKEN = '7440372138:AAHIcrjUjo0lXixcSRLWJedMg229pHg6h08'
JIRA_EMAIL = 'ruslan.issin@mechta.kz'
JIRA_API_TOKEN = 'ATATT3xFfGF0KjLA2DYqN-1YerpIy8iN-oASBZlPk0UxRPZ-JO2EddPSG_dx78SzkY-sWv5FHFzIJNJijMQJ05Rl_t1rNNP4mOUZIwXU099Bv-R3L2gVnXh8dMh5uDG9956sms2vjfEChmUDzM0D3JLz1bkZ08ryEfyT0r_sxFobC8DtIJmDpU4=57BBD2C7'
JIRA_PROJECT_KEY = 'AS'
JIRA_URL = 'https://mechtamarket.atlassian.net'

# === Настройка бота ===
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# === Теги, при которых создаётся тикет ===
TRIGGER_TAGS = ['#bug', '#jira']
def clean_summary(text: str, tags: list[str]) -> str:
    for tag in tags:
        pattern = re.compile(re.escape(tag), re.IGNORECASE)
        text = pattern.sub('', text)
    return ' '.join(text.split()).strip()

# === Обработка входящих сообщений ===
@dp.message(F.text)
async def handle_message(message: Message):
    if any(tag in message.text.lower() for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        success = await create_jira_ticket(message.text, message.from_user.full_name)
        if success:
            await message.reply("✅ Задача успешно создана!")
        else:
            await message.reply("❌ Ошибка при создании задачи в Jira.")

# === Отправка запроса в Jira ===
async def create_jira_ticket(text: str, author: str) -> bool:
    url = f"{JIRA_URL}/rest/api/3/issue"
    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {"Content-Type": "application/json"}
    cleaned_text = clean_summary(text, TRIGGER_TAGS)
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "parent": {"key": "AS-1679"},
            "summary": cleaned_text[:255],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": text
                            }
                        ]
                    }
                ]
            },
            "issuetype": {"name": "Подзадача"}
            # Или "Bug", если у тебя другой тип
        }
    }


    # 🔒 Отключение SSL-проверки (только временно!)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(url, json=payload, headers=headers, ssl=ssl_context) as response:
            if response.status == 201:
                print("✅ Jira тикет успешно создан.")
                return True
            else:
                error = await response.text()
                print(f"❌ Ошибка при создании Jira тикета: {response.status} — {error}")
                return False

# === Запуск бота ===
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
