import aiohttp
import ssl
import re
from logger_config import setup_logger
logger = setup_logger(__name__)

TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'
THREAD_PREFIXES = {1701: '[Back]', 1703: '[Front]'}

def clean_summary(text: str, tags: list[str]) -> str:
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()

def get_thread_prefix(message) -> str:
    return THREAD_PREFIXES.get(getattr(message, "message_thread_id", None), '')

async def create_jira_ticket(JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_PARENT_KEY,
                             text: str, author: str, bot, ADMIN_ID, TESTERS_CHANNEL_ID,
                             file_bytes=None, filename=None, thread_prefix=""):
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
            "parent": {"key": JIRA_PARENT_KEY},
            "summary": summary,
            "description": {"type": "doc", "version": 1,
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"[Telegram] Автор: {author}\n{text}"}]}]},
            "issuetype": {"name": "Подзадача"}
        }
    }

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(create_url, json=payload, headers=headers, ssl=ssl_context) as response:
            if response.status != 201:
                error = await response.text()
                logger.error("❌ Ошибка при создании задачи: %s — %s", response.status, error)
                return False, None
            result = await response.json()
            issue_key = result["key"]
            notify_text = f"📨 Создан новый баг!\n🔑 <b>{issue_key}</b>\n👤 Автор: <b>{author}</b>\n\n🔗 <a href=\"{JIRA_URL}/browse/{issue_key}\">Открыть задачу</a>\n\n📝 <b>Описание:</b>\n{text}"
            try:
                await bot.send_message(ADMIN_ID, notify_text)
                await bot.send_message(TESTERS_CHANNEL_ID, notify_text, parse_mode="HTML")
            except Exception as e:
                logger.exception("Не удалось отправить уведомления: %s", e)

            if file_bytes and filename:
                attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
                attach_headers = {"X-Atlassian-Token": "no-check"}
                data = aiohttp.FormData()
                data.add_field('file', file_bytes, filename=filename, content_type='image/jpeg')
                async with session.post(attach_url, data=data, headers=attach_headers, ssl=ssl_context) as attach_response:
                    if attach_response.status in (200, 201):
                        logger.info("📎 Фото прикреплено к задаче %s", issue_key)
                    else:
                        error = await attach_response.text()
                        logger.error("❌ Ошибка при вложении: %s — %s", attach_response.status, error)
            return True, issue_key
