import html
import logging
import re

from aiogram import Bot, F
from aiogram.types import Message

from services.ai_service import ai_service
from services.confluence_service import search_confluence
from web.admin_handler import monitor

logger = logging.getLogger("bot.faq")

FAQ_TAG_RE = re.compile(r"#faq", re.IGNORECASE)
MAX_PAGES = 3


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _extract_question(text: str, is_command: bool) -> str:
    if is_command:
        # "/ask вопрос" -> "вопрос"
        return text.split(maxsplit=1)[1].strip() if " " in text else ""
    # убираем тег #faq (в любом регистре) и лишние пробелы
    return FAQ_TAG_RE.sub("", text).strip()


async def _generate_answer(question: str, pages: list) -> str:
    context_blocks = "\n\n".join(
        f"### {p['title']}\n{p['excerpt']}" for p in pages
    )

    prompt = (
        "Ты — помощник IT-команды. Ответь на вопрос коллеги, используя ТОЛЬКО "
        "информацию из приведенных ниже фрагментов документации Confluence. "
        "Если ответа в материалах нет — честно скажи, что не нашел информацию "
        "по этому вопросу в документации, и посоветуй уточнить у коллег. "
        "Отвечай кратко, по делу, на русском языке, без markdown-разметки.\n\n"
        f"Вопрос: {question}\n\n"
        f"Материалы из документации:\n{context_blocks}"
    )

    return await ai_service.generate_groq(prompt=prompt, max_tokens=500, temperature=0.3)


async def handle_faq_question(bot: Bot, message: Message, question: str, confluence_config: dict) -> None:
    if not question:
        await message.reply(
            "❓ Не вижу вопроса. Пример: <code>/ask как оформить командировку</code>"
        )
        return

    thinking_msg = await message.reply("🔍 Ищу в документации...")

    try:
        pages = await search_confluence(confluence_config, question, limit=MAX_PAGES)
    except Exception as e:
        logger.exception(f"Ошибка поиска в Confluence: {e}")
        pages = []

    if not pages:
        monitor.update_status("FAQ Bot", "OK")
        await thinking_msg.delete()
        await message.reply(
            "🤷 Не нашел релевантных страниц в документации по этому вопросу. "
            "Попробуй переформулировать или уточни у коллег."
        )
        return

    try:
        answer = await _generate_answer(question, pages)
    except Exception as e:
        logger.error(f"Ошибка генерации ответа FAQ через AI: {e}")
        monitor.update_status("FAQ Bot", f"ERROR: {e}")
        await thinking_msg.delete()
        await message.reply("❌ Не удалось сгенерировать ответ. Попробуй позже.")
        return

    sources = "\n".join(
        f"• <a href='{p['url']}'>{_esc(p['title'])}</a>" for p in pages
    )
    result_text = (
        f"🤖 <b>Ответ по документации:</b>\n\n{_esc(answer)}\n\n"
        f"📚 <b>Источники:</b>\n{sources}"
    )

    monitor.update_status("FAQ Bot", "OK")
    await thinking_msg.delete()
    await message.reply(result_text, disable_web_page_preview=True)


def register_faq_handlers(dp, bot: Bot, confluence_config: dict):

    @dp.message(F.text.startswith("/ask"))
    async def ask_command(message: Message):
        question = _extract_question(message.text, is_command=True)
        await handle_faq_question(bot, message, question, confluence_config)

    @dp.message(F.text.func(lambda t: bool(t) and "#faq" in t.lower()))
    async def faq_tag(message: Message):
        question = _extract_question(message.text, is_command=False)
        await handle_faq_question(bot, message, question, confluence_config)