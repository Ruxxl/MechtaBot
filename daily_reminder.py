import os
import aiohttp
from aiogram import types
from aiogram.enums import ParseMode

# Храним состояние между запусками функции
not_released_versions = set()
notified_versions = set()


async def jira_release_check(
    bot,
    TESTERS_CHANNEL_ID,
    JIRA_EMAIL,
    JIRA_API_TOKEN,
    JIRA_PROJECT_KEY,
    JIRA_URL,
    logger
):
    logger.info("🔎 Проверяю релизы Jira...")

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

    try:
        async with aiohttp.ClientSession(auth=auth) as session:

            # 1️⃣ Получаем все версии проекта
            async with session.get(
                f"{JIRA_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}/versions"
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(
                        f"Ошибка получения релизов: {resp.status}, body={text}"
                    )
                    return

                versions = await resp.json()

            # 2️⃣ Обрабатываем версии
            for version in versions:
                name = version.get("name")
                released = version.get("released", False)
                version_id = version.get("id")

                # Если версия еще не выпущена, запоминаем её и идем дальше
                if not released:
                    not_released_versions.add(name)
                    continue

                # Если версия была в списке невыпущенных и мы о ней еще не уведомляли
                if name in not_released_versions and name not in notified_versions:
                    notified_versions.add(name)

                    logger.info(f"🚀 Релиз выпущен: {name}")

                    # Запрашиваем задачи этого релиза. 
                    # Важно: добавляем subtasks в fields, чтобы посчитать баги
                    jql = f'project="{JIRA_PROJECT_KEY}" AND fixVersion={version_id}'
                    search_url = (
                        f"{JIRA_URL}/rest/api/3/search/jql"
                        f"?jql={jql}&fields=key,summary,subtasks&maxResults=200"
                    )

                    async with session.get(search_url) as resp_issues:
                        if resp_issues.status != 200:
                            issues = []
                        else:
                            data = await resp_issues.json()
                            issues = data.get("issues", [])

                    # 3️⃣ Считаем подзадачи (ваши баги)
                    total_bugs = 0
                    for i in issues:
                        subtasks = i["fields"].get("subtasks", [])
                        total_bugs += len(subtasks)

                    # Формируем список основных задач
                    issues_text = "\n".join(
                        f'• <a href="{JIRA_URL}/browse/{i["key"]}">'
                        f'{i["key"]} — {i["fields"]["summary"]}</a>'
                        for i in issues
                    ) or "Задачи не найдены."

                    # 4️⃣ Формируем итоговое сообщение
                    message = (
                        "🎉 <b>Релиз выпущен!</b>\n\n"
                        f"📦 <b>{name}</b>\n\n"
                        f"🐞 <b>Багов зарегано: {total_bugs}</b>\n\n"
                        "📝 <b>Задачи релиза:</b>\n"
                        f"{issues_text}"
                    )

                    # 5️⃣ Отправка (с фото или без)
                    if os.path.exists("release.jpg"):
                        photo = types.FSInputFile("release.jpg")
                        await bot.send_photo(
                            TESTERS_CHANNEL_ID,
                            photo=photo,
                            caption=message,
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await bot.send_message(
                            TESTERS_CHANNEL_ID,
                            message,
                            parse_mode=ParseMode.HTML
                        )

                    logger.info(f"Уведомление о релизе отправлено: {name}")

    except Exception as e:
        logger.exception("Ошибка в jira_release_check", exc_info=e)
