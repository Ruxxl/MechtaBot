# release_notifier.py
import os
import asyncio
import aiohttp
import ssl
from aiogram import types
from aiogram.enums import ParseMode
import logging

async def jira_release_check(bot, TESTERS_CHANNEL_ID, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_URL, logger):
    """
    Проверяет релизы Jira и отправляет уведомление в Telegram с фото или текстом.
    """
    if "notified_releases" not in globals():
        global notified_releases
        notified_releases = set()

    logger.info("Проверяю релизы Jira...")

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.get(f"{JIRA_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}/versions") as resp:
                logger.info(f"Ответ Jira (versions): {resp.status}")
                if resp.status != 200:
                    logger.error(f"Ошибка получения релизов: {resp.status}")
                    return
                versions = await resp.json()

            RELEASE_NAME = "[iOS] Релиз Детали заказа"
            release = next((r for r in versions if r["name"] == RELEASE_NAME), None)

            if not release:
                logger.warning(f"Релиз '{RELEASE_NAME}' не найден")
                return

            if release.get("released") and RELEASE_NAME not in notified_releases:
                notified_releases.add(RELEASE_NAME)

                version_id = release.get("id")
                jql = f'project="{JIRA_PROJECT_KEY}" AND fixVersion={version_id}'
                search_url = f"{JIRA_URL}/rest/api/3/search/jql?jql={jql}&fields=key,summary&maxResults=200"

                async with session.get(search_url) as resp_issues:
                    if resp_issues.status != 200:
                        logger.error(f"Ошибка получения задач релиза: {resp_issues.status}")
                        issues = []
                    else:
                        data = await resp_issues.json()
                        issues = data.get("issues", [])

                # Формируем текст с задачами
                issues_text = "\n".join(
                    f'<a href="{JIRA_URL}/browse/{i["key"]}">{i["fields"]["summary"]}</a>'
                    for i in issues
                ) if issues else "Задачи не найдены."

                message = f"🎉 Релиз <b>{RELEASE_NAME}</b> выпущен!\n\n📝 Задачи релиза:\n{issues_text}"

                try:
                    if os.path.exists("release.jpg"):
                        photo = types.FSInputFile("release.jpg")
                        await bot.send_photo(TESTERS_CHANNEL_ID, photo=photo, caption=message, parse_mode=ParseMode.HTML)
                    else:
                        await bot.send_message(TESTERS_CHANNEL_ID, message, parse_mode=ParseMode.HTML)

                    logger.info(f"Уведомление о релизе отправлено: {RELEASE_NAME}")
                except Exception as e:
                    logger.exception(f"Ошибка отправки уведомления о релизе: {e}")

    except Exception as e:
        logger.exception("Ошибка в jira_release_check: %s", e)
