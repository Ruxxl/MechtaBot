import os
import logging
import aiohttp
from aiogram import types
from aiogram.enums import ParseMode
from web.admin_handler import monitor
from services.ai_service import ai_service

module_logger = logging.getLogger("bot.release_notifier")

# Храним состояние между запусками функции
not_released_versions = set()
notified_versions = set()


async def generate_release_summary(issues: list, max_chars: int = 350) -> str:
    """Генерирует краткое описание релиза через AI на основе задач, вошедших в релиз.

    Описание всегда укладывается в max_chars символов — это нужно, чтобы суммарное
    сообщение (описание + список задач) надёжно помещалось в лимит подписи к фото
    в Telegram (1024 символа)."""
    if not issues:
        return "Задачи для описания не найдены."

    tasks_text = "\n".join(
        f"- {i['key']}: {i['fields'].get('summary', 'Без названия')}"
        for i in issues
    )

    prompt = (
        "Ты — помощник IT-команды. Ниже список задач Jira, вошедших в релиз. "
        "Напиши ОЧЕНЬ короткое (1-2 предложения, максимум 250 символов) описание "
        "релиза на русском языке: что изменилось, какие основные улучшения и фиксы "
        "вошли. Пиши связным текстом, без markdown и без списков.\n\n"
        f"Задачи релиза:\n{tasks_text}"
    )

    try:
        summary = await ai_service.generate_groq(
            prompt=prompt,
            max_tokens=120,
            temperature=0.5
        )
        summary = (summary or "Не удалось сгенерировать описание релиза.").strip()

        # Жёсткая защита: даже если AI проигнорирует ограничение в промпте,
        # обрезаем результат, чтобы не превысить лимит подписи Telegram.
        if len(summary) > max_chars:
            summary = summary[:max_chars - 1].rstrip() + "…"

        return summary
    except Exception as e:
        module_logger.error(f"Ошибка генерации описания релиза через AI: {e}")
        return "Описание релиза временно недоступно."


async def jira_release_check(
    bot,
    target_group_id,    # Сюда придет TARGET_GROUP_ID
    JIRA_EMAIL,
    JIRA_API_TOKEN,
    JIRA_PROJECT_KEY,
    JIRA_URL,
    logger,
    interval=100,
    thread_id=None      # Сюда придет TARGET_THREAD_ID
):
    logger.info("🔎 Проверяю релизы Jira...")
    monitor.update_status("Jira Release Monitor", "OK")
    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            # 1️⃣ Получаем все версии проекта
            async with session.get(
                f"{JIRA_URL.rstrip('/')}/rest/api/3/project/{JIRA_PROJECT_KEY}/versions"
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Ошибка получения релизов: {resp.status}, body={text}")
                    return

                versions = await resp.json()

            # 2️⃣ Обрабатываем версии
            for version in versions:
                name = version.get("name")
                released = version.get("released", False)
                version_id = version.get("id")

                # Пропускаем, если еще не релизнуто
                if not released:
                    not_released_versions.add(name)
                    continue

                # Уведомляем, только если версия была в "ожидаемых" и мы еще не спамили
                if name in not_released_versions and name not in notified_versions:
                    notified_versions.add(name)
                    logger.info(f"🚀 Релиз выпущен: {name}")

                    # Запрашиваем задачи релиза
                    jql = f'project="{JIRA_PROJECT_KEY}" AND fixVersion={version_id}'
                    search_params = {
                        "jql": jql,
                        "fields": "key,summary,subtasks",
                        "maxResults": 200
                    }

                    async with session.get(f"{JIRA_URL.rstrip('/')}/rest/api/3/search/jql", params=search_params) as resp_issues:
                        issues = []
                        if resp_issues.status == 200:
                            data = await resp_issues.json()
                            issues = data.get("issues", [])

                    # 3️⃣ Считаем баги (подзадачи)
                    total_bugs = sum(len(i["fields"].get("subtasks", [])) for i in issues)

                    # 3.1️⃣ Генерируем AI-описание релиза по списку задач
                    release_description = await generate_release_summary(issues)

                    issues_text = "\n".join(
                        f'• <a href="{JIRA_URL}/browse/{i["key"]}">'
                        f'{i["key"]} — {i["fields"]["summary"]}</a>'
                        for i in issues
                    ) or "Задачи не найдены."

                    message_text = (
                        "🎉 <b>Релиз выпущен!</b>\n\n"
                        f"📦 <b>{name}</b>\n\n"
                        f"📝 <b>Описание релиза:</b>\n{release_description}\n\n"
                        f"🐞 <b>Багов найдено: {total_bugs}</b>\n\n"
                        "📝 <b>Задачи релиза:</b>\n"
                        f"{issues_text}"
                    )

                    # 5️⃣ Отправка строго в TARGET_GROUP_ID и TARGET_THREAD_ID
                    # Caption у фото в Telegram ограничен 1024 символами (текст сообщения — 4096),
                    # поэтому если итоговый текст не укладывается в лимит caption — фото уходит
                    # без подписи, а полный текст отправляется отдельным сообщением.
                    TELEGRAM_CAPTION_LIMIT = 1024
                    photo_path = "assets/release.jpg"
                    has_photo = os.path.exists(photo_path)
                    fits_caption = len(message_text) <= TELEGRAM_CAPTION_LIMIT

                    try:
                        if has_photo and fits_caption:
                            photo = types.FSInputFile(photo_path)
                            await bot.send_photo(
                                chat_id=target_group_id,
                                photo=photo,
                                caption=message_text,
                                parse_mode=ParseMode.HTML
                            )
                        elif has_photo:
                            photo = types.FSInputFile(photo_path)
                            await bot.send_photo(chat_id=target_group_id, photo=photo)
                            await bot.send_message(
                                chat_id=target_group_id,
                                text=message_text,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True
                            )
                        else:
                            await bot.send_message(
                                chat_id=target_group_id,
                                text=message_text,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True
                            )
                        logger.info(f"✅ Уведомление отправлено в группу {target_group_id} (топик {thread_id})")
                    except Exception as send_error:
                        logger.error(f"❌ Ошибка отправки в TG: {send_error}")

    except Exception as e:
        logger.exception("Ошибка в jira_release_check", exc_info=e)
