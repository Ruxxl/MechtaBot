import asyncio
import aiohttp
import ssl
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.environ.get('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.environ.get('JIRA_PARENT_KEY', 'AS-1679')
JIRA_URL = os.environ.get('JIRA_URL', 'https://mechtamarket.atlassian.net')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

TRIGGER_TAGS = ['#bug', '#jira']
THREAD_PREFIXES = {1701: '[Back]', 1703: '[Front]'}

def clean_summary(text: str, tags: list[str]) -> str:
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()

def get_thread_prefix(message: Message) -> str:
    return THREAD_PREFIXES.get(message.message_thread_id, '')

@dp.message(F.photo)
async def handle_photo(message: Message):
    caption = message.caption or ""
    caption_lower = caption.lower()
    print(f"📸 Получено фото: {caption}")

    if any(tag in caption_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, ssl=ssl_context) as photo_response:
                if photo_response.status == 200:
                    photo_bytes = await photo_response.read()
                    success, issue_key = await create_jira_ticket(
                        caption,
                        message.from_user.full_name,
                        file_bytes=photo_bytes,
                        filename="telegram_photo.jpg",
                        thread_prefix=get_thread_prefix(message)
                    )
                    if success:
                        await message.reply(f"✅ Задача <b>{issue_key}</b> создана!
🔗 {JIRA_URL}/browse/{issue_key}")
                    else:
                        await message.reply("❌ Ошибка при создании задачи в Jira.")
                else:
                    await message.reply("❌ Не удалось скачать фото с Telegram.")

@dp.message(F.text)
async def handle_text(message: Message):
    text = message.text or ""
    text_lower = text.lower()
    print(f"✉️ Получено сообщение: {text}")

    if any(tag in text_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        success, issue_key = await create_jira_ticket(
            text,
            message.from_user.full_name,
            file_bytes=None,
            filename=None,
            thread_prefix=get_thread_prefix(message)
        )
        if success:
            await message.reply(f"✅ Задача <b>{issue_key}</b> создана!🔗 {JIRA_URL}/browse/{issue_key}")
        else:
            await message.reply("❌ Ошибка при создании задачи в Jira.")

async def create_jira_ticket(text: str, author: str, file_bytes: bytes = None, filename: str = None, thread_prefix: str = "") -> tuple[bool, str | None]:
    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    cleaned_text = clean_summary(text, TRIGGER_TAGS)

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    create_url = f"{JIRA_URL}/rest/api/3/issue"
    headers = {"Content-Type": "application/json"}
    summary = f"[Telegram] {cleaned_text}".strip()[:255]
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": f"[Telegram] Автор: {author}\n{text}"}]
                }]
            },
            "issuetype": {"name": "Баг"}
        }
    }

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(create_url, json=payload, headers=headers, ssl=ssl_context) as response:
            if response.status != 201:
                error = await response.text()
                print(f"❌ Ошибка при создании задачи: {response.status} — {error}")
                return False, None

            result = await response.json()
            issue_key = result["key"]
            print(f"✅ Задача {issue_key} создана")

        if file_bytes and filename:
            attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
            attach_headers = {"X-Atlassian-Token": "no-check"}
            data = aiohttp.FormData()
            data.add_field('file', file_bytes, filename=filename, content_type='image/jpeg')

            async with session.post(attach_url, data=data, headers=attach_headers, ssl=ssl_context) as attach_response:
                if attach_response.status in (200, 201):
                    print(f"📎 Фото прикреплено к задаче {issue_key}")
                else:
                    error = await attach_response.text()
                    print(f"❌ Ошибка при вложении: {attach_response.status} — {error}")
                    return False, None

        return True, issue_key

async def main():
    print("🚀 Бот запущен и ждет сообщений")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
