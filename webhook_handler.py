import logging
from aiohttp import web

logger = logging.getLogger("bot.webhook")

class WebhookHandler:
    def __init__(self, bot, target_group_id: int, target_thread_id: int):
        self.bot = bot
        self.target_group_id = target_group_id
        self.target_thread_id = target_thread_id

    async def handle_notification(self, request: web.Request):
        """
        Обработчик вебхуков. Ловит завершение GitHub Actions для ветки predprod.
        """
        try:
            user_agent = request.headers.get('User-Agent', '')
            
            if 'GitHub-Hookshot' in user_agent:
                data = await request.json()
                
                # 1. Проверка на пинг от GitHub
                if "zen" in data:
                    logger.info("🍏 Пинг от GitHub Webhook получен!")
                    return web.json_response({"status": "success", "message": "Pong"})
                
                # 2. Ловим событие изменения воркфлоу
                workflow_run = data.get("workflow_run")
                if not workflow_run:
                    return web.json_response({"status": "ignored", "message": "Not a workflow_run event"})
                    
                # 3. Фильтруем строго по ветке predprod
                branch = workflow_run.get("head_branch")
                if branch != "predprod":
                    logger.info(f"Игнорируем экшен для ветки {branch}, ждем только predprod.")
                    return web.json_response({"status": "ignored", "message": f"Branch {branch} ignored"})
                    
                # 4. Проверяем статус. Ловим момент, когда экшен ЗАВЕРШИЛСЯ (completed)
                status = workflow_run.get("status")
                conclusion = workflow_run.get("conclusion")  # success, failure, cancelled
                
                if status != "completed":
                    return web.json_response({"status": "ignored", "message": "Workflow is still running"})
                    
                # 5. Если билд успешно завершен — собираем сообщение
                if conclusion == "success":
                    repo_name = data.get("repository", {}).get("name", "Unknown Repo")
                    actor = workflow_run.get("actor", {}).get("login", "Unknown")
                    run_number = workflow_run.get("run_number", 0)
                    html_url = workflow_run.get("html_url", "#")
                    head_commit = workflow_run.get("head_commit", {})
                    commit_message = head_commit.get("message", "Описание отсутствует").split("\n")[0]
                    workflow_name = workflow_run.get("name", "Unknown Workflow")
                    
                    text = f"🚀 <b>[GitHub Actions] Билд успешно собран!</b>\n\n"
                    text += f"🎬 <b>Стенд:</b> {workflow_name}\n"
                    text += f"📦 <b>Репозиторий:</b> {repo_name}\n"
                    text += f"🌿 <b>Ветка:</b> <code>{branch}</code>\n"
                    text += f"🛠 <b>Билд:</b> <a href=\"{html_url}\">#{run_number}</a>\n"
                    text += f"👤 <b>Инициатор:</b> @{actor}\n"
                    text += f"📝 <b>Описание:</b> <i>{commit_message}</i>"
                    
                    # Отправляем в Telegram
                    await self.bot.send_message(
                        chat_id=self.target_group_id,
                        text=text,
                        message_thread_id=self.target_thread_id,
                        disable_web_page_preview=True
                    )
                    return web.json_response({"status": "success", "message": "Notification sent"})
                
                return web.json_response({"status": "ignored", "message": f"Workflow finished with conclusion: {conclusion}"})

            else:
                # Старый ручной JSON для тестов через curl
                data = await request.json()
                message_text = data.get("text")
                thread_id = data.get("thread_id", self.target_thread_id)
                
                if not message_text:
                    return web.json_response({"status": "error", "message": "Missing text"}, status=400)
                    
                await self.bot.send_message(
                    chat_id=self.target_group_id, 
                    text=message_text, 
                    message_thread_id=int(thread_id)
                )
                return web.json_response({"status": "success"})

        except Exception as e:
            logger.exception(f"Ошибка вебхука: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)