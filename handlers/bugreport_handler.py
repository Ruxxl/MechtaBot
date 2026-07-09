import json
import logging
import re

from aiogram import Bot, F
from aiogram.types import Message

from services.ai_service import ai_service
from handlers.text_handler import get_thread_prefix, THREAD_PREFIXES
from web.admin_handler import monitor

logger = logging.getLogger("bot.bugreport")

# Разрешаем "/bugreport" и "/bugreport@ИмяБота"
BUGREPORT_RE = re.compile(r"^/bugreport(@\w+)?\b", re.IGNORECASE)


async def _generate_title_and_description(raw_text: str) -> tuple[str, str]:
    """Просит AI оформить заголовок и описание баг-репорта по сырому тексту
    сообщения из чата. При любой ошибке (сеть, невалидный JSON и т.п.) —
    безопасный фолбэк на исходный текст, без выдумывания фактов."""
    fallback_title = (raw_text.strip()[:80] or "Баг без описания").strip()
    fallback_description = raw_text.strip() or "Описание не указано (сообщение содержало только фото)."

    if not raw_text.strip():
        return fallback_title, fallback_description

    prompt = (
        "Ты — помощник IT-команды, оформляешь баг-репорт в Jira по сообщению "
        "коллеги из группового чата. Верни ТОЛЬКО валидный JSON без markdown и "
        "без пояснений, строго в формате: "
        '{"title": "...", "description": "..."}\n'
        "title — короткий заголовок задачи (до 80 символов) на русском языке.\n"
        "description — краткое, но информативное описание проблемы на русском "
        "языке (что сломано, где, при каких условиях, если это видно из текста). "
        "Опирайся ТОЛЬКО на переданный текст, не выдумывай деталей, которых там нет.\n\n"
        f"Сообщение коллеги:\n{raw_text}"
    )

    try:
        raw = await ai_service.generate_groq(prompt=prompt, max_tokens=400, temperature=0.3)
        cleaned = re.sub(r"^```json|^```|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
        data = json.loads(cleaned)
        title = str(data.get("title") or fallback_title).strip()[:80]
        description = str(data.get("description") or fallback_description).strip()
        return title or fallback_title, description or fallback_description
    except Exception as e:
        logger.warning(f"Не удалось получить структурированный ответ AI для /bugreport: {e}")
        return fallback_title, fallback_description


def register_bugreport_handlers(dp, bot: Bot, jira_config: dict, create_jira_issue_func):
    """
    /bugreport — команда, отправляемая ОТВЕТОМ на сообщение с багом (текст
    и/или фото). Достает текст/фото из исходного сообщения, прогоняет текст
    через Groq для аккуратного заголовка и описания, создает подзадачу в
    родительской задаче jira_config['parent'] (та же логика, что и /jira,
    см. handlers/jira_fsm.py::create_jira_issue) и отвечает ссылкой на задачу.

    create_jira_issue_func передается снаружи (main.py), чтобы не плодить
    циклический импорт с jira_fsm.py.
    """

    @dp.message(F.text.func(lambda t: bool(t) and BUGREPORT_RE.match(t.strip())))
    async def bugreport_command(message: Message):
        monitor.update_status("Bug Report", "OK")

        source = message.reply_to_message
        if not source:
            await message.reply(
                "⚠️ Команду <code>/bugreport</code> нужно отправлять <b>ответом</b> "
                "на сообщение с описанием бага (текст и/или фото)."
            )
            return

        raw_text = source.text or source.caption or ""
        # Берем фото лучшего качества из исходного сообщения.
        # Ограничение: если баг прислали альбомом из нескольких фото, реплай
        # обычно падает на одно из сообщений — в задачу уйдет только оно.
        photo_ids = [source.photo[-1].file_id] if source.photo else []

        if not raw_text.strip() and not photo_ids:
            await message.reply("⚠️ В исходном сообщении нет ни текста, ни фото — нечего оформлять.")
            return

        thinking = await message.reply("🔄 Оформляю баг-репорт и создаю задачу в Jira...")

        title, description = await _generate_title_and_description(raw_text)

        author = source.from_user.full_name if source.from_user else "Неизвестный"
        reporter = message.from_user.full_name if message.from_user else "Неизвестный"
        if reporter != author:
            description += f"\n\nЗарегистрировал через /bugreport: {reporter}"

        prefix = get_thread_prefix(message, THREAD_PREFIXES)

        try:
            issue_key = await create_jira_issue_func(
                bot=bot,
                jira_config=jira_config,
                title=title,
                description=description,
                author=author,
                priority="Medium",
                links=[],
                files=photo_ids,
                thread_prefix=prefix,
            )
        except Exception as e:
            logger.exception(f"Ошибка создания задачи через /bugreport: {e}")
            issue_key = None

        await thinking.delete()

        if issue_key:
            jira_url = jira_config['url'].rstrip('/')
            await message.reply(
                f"✅ Задача <b>{issue_key}</b> создана!\n"
                f"📝 <b>{title}</b>\n"
                f"🔗 <a href='{jira_url}/browse/{issue_key}'>{jira_url}/browse/{issue_key}</a>"
            )
        else:
            monitor.update_status("Bug Report", "ERROR")
            await message.reply("❌ Не удалось создать задачу в Jira. Попробуй еще раз чуть позже.")