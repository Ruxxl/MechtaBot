# release_notifier.py
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
    """
    Трекает все релизы в статусе "Не выпущено".
    Если релиз становится "Выпущено" — отправляет уведомление.
    """

    logger.info("🔎 Проверяю релизы Jira...")

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.get(
                f"{JIRA_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}/versions"
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка получения релизов: {resp.status}")
                    return

                versions = await resp.json()

        for version in versions:
            name = version.get("name")
            released = version.get("released", False)
            version_id = version.get("id")

            # 1️⃣ Запоминаем все "не выпущенные"
            if not released:
                not_released_versions.add(name)
                continue

            # 2️⃣ Если был не выпущен и стал выпущен
            if released and name in not_released_versions and name not in notified_versions:
                notified_versions.add(name)

                logger.info(f"🚀 Релиз выпущен: {name}")

                # Получаем задачи релиза
                jql = f'project="{JIRA_PROJECT_KEY}" AND fixVersion={version_id}'
                search_url = (
                    f"{JIRA_URL}/rest/api/3/search/jql"
                    f"?jql={jql}&fields=key,summary&maxResults=200"
                )

                async with session.get(search_url) as resp_issues:
                    if resp_issues.status != 200:
                        issues = []
                    else:
                        data = await resp_issues.json()
                        issues = data.get("issues", [])

                issues_text = "\n".join(
                    f'• <a href="{JIRA_URL}/browse/{i["key"]}">{i["key"]} — {i["fields"]["summary"]}</a>'
                    for i in issues
                ) or "Задачи не найдены."

                message = (
                    f"🎉 <b>Релиз выпущен!</b>\n\n"
                    f"📦 <b>{name}</b>\n\n"
                    f"📝 <b>Задачи релиза:</b>\n{issues_text}"
                )

                try:
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
                except Exception as e:
                    logger.exception(f"Ошибка отправки уведомления: {e}")

    except Exception as e:
        logger.exception("Ошибка в jira_release_check", exc_info=e)
