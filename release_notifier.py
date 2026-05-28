import os
import aiohttp
from aiogram import types
from aiogram.enums import ParseMode
from admin_handler import monitor

# Храним состояние между запусками функции
not_released_versions = set()
notified_versions = set()

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

                    issues_text = "\n".join(
                        f'• <a href="{JIRA_URL}/browse/{i["key"]}">'
                        f'{i["key"]} — {i["fields"]["summary"]}</a>'
                        for i in issues
                    ) or "Задачи не найдены."

                    message_text = (
                        "🎉 <b>Релиз выпущен!</b>\n\n"
                        f"📦 <b>{name}</b>\n\n"
                        f"🐞 <b>Багов найдено: {total_bugs}</b>\n\n"
                        "📝 <b>Задачи релиза:</b>\n"
                        f"{issues_text}"
                    )

                    # 5️⃣ Отправка строго в TARGET_GROUP_ID и TARGET_THREAD_ID
                    try:
                        if os.path.exists("release.jpg"):
                            photo = types.FSInputFile("release.jpg")
                            await bot.send_photo(
                                chat_id=target_group_id,
                                photo=photo,
                                caption=message_text,
                                parse_mode=ParseMode.HTML
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
