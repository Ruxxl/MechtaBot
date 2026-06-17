import logging
from datetime import datetime
import aiohttp
from aiohttp import web

logger = logging.getLogger("bot.webhook")

class WebhookHandler:
    def __init__(self, bot, target_group_id: int, target_thread_id: int, github_token: str = None, repo_full_name: str = None):
        self.bot = bot
        self.target_group_id = target_group_id
        self.target_thread_id = target_thread_id
        self.github_token = github_token
        self.repo_full_name = repo_full_name
        # Хранилище последних успешных сборок (до 5 на каждый стенд)
        self.latest_builds: dict[str, list] = {}
        self.MAX_BUILDS_PER_STAND = 5
        # Маппинг имен workflow из .yml файлов на URL стендов
        self.stand_urls = {
            "deploy 1c": "http://1c.im.mdev.kz/",
            "deploy d2": "http://d2.im.mdev.kz/",
            "deploy d3": "http://d3.im.mdev.kz/",
            "deploy d4": "http://d4.im.mdev.kz/",
            "deploy preprod": "https://pp.yc.mechta.kz/",
            "deploy preprod external integrations": "http://pp.im.mdev.kz/",
            "deploy ssr prod": "https://mechta.kz/",
        }

    async def fetch_latest_build_from_api(self, workflow_name: str):
        """Запрашивает информацию о последнем успешном запуске воркфлоу через GitHub API"""
        if not self.github_token or not self.repo_full_name:
            return None

        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                # 1. Ищем ID воркфлоу по его имени
                wf_url = f"https://api.github.com/repos/{self.repo_full_name}/actions/workflows"
                async with session.get(wf_url) as resp:
                    if resp.status != 200: return None
                    data = await resp.json()
                    workflow = next((w for w in data.get('workflows', []) if w['name'].lower() == workflow_name.lower()), None)
                
                if not workflow: return None

                # 2. Берем последний успешный запуск (conclusion=success)
                runs_url = f"https://api.github.com/repos/{self.repo_full_name}/actions/workflows/{workflow['id']}/runs"
                async with session.get(runs_url, params={"status": "success", "per_page": 5}) as resp:
                    if resp.status != 200: return None
                    runs_data = await resp.json()
                    runs = runs_data.get("workflow_runs", [])
                    if not runs: return None
                    
                    result = []
                    for run in runs:
                        dt = datetime.fromisoformat(run.get("updated_at", "").replace("Z", ""))
                        result.append({
                            "commit": run.get("head_commit", {}).get("message", "").split("\n")[0],
                            "actor": run.get("actor", {}).get("login", "Unknown"),
                            "date": dt.strftime("%d.%m.%Y %H:%M"),
                            "url": run.get("html_url")
                        })
                    return result
        except Exception as e:
            logger.error(f"Ошибка GitHub API: {e}")
            return None

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
                    
                # 3. Ловим момент, когда экшен ЗАВЕРШИЛСЯ (completed)
                status = workflow_run.get("status")
                conclusion = workflow_run.get("conclusion")  # success, failure, cancelled
                
                if status != "completed":
                    return web.json_response({"status": "ignored", "message": "Workflow is still running"})
                    
                # 4. Собираем данные
                repo_name = data.get("repository", {}).get("name", "Unknown Repo")
                actor = workflow_run.get("actor", {}).get("login", "Unknown")
                run_number = workflow_run.get("run_number", 0)
                html_url = workflow_run.get("html_url", "#")
                head_commit = workflow_run.get("head_commit", {})
                commit_message = head_commit.get("message", "Описание отсутствует").split("\n")[0]
                workflow_name = workflow_run.get("name", "Unknown Workflow")
                branch = workflow_run.get("head_branch", "Unknown Branch")
                
                # Определяем имя стенда и URL: берем из маппинга (ключ) или имя воркфлоу
                workflow_key = workflow_name.lower().strip()
                stand_url = self.stand_urls.get(workflow_key)
                stand_info = workflow_name.upper() if stand_url else workflow_name
                
                # 5. Обновляем кэш стендов (только при успехе)
                if conclusion == "success":
                    build_entry = {
                        "commit": commit_message,
                        "actor": actor,
                        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
                        "url": html_url
                    }
                    builds = self.latest_builds.setdefault(stand_info, [])
                    builds.insert(0, build_entry)
                    self.latest_builds[stand_info] = builds[:self.MAX_BUILDS_PER_STAND]

                # Подготовка текста уведомления
                emoji = "🚀" if conclusion == "success" else "❌" if conclusion == "failure" else "⚠️"
                result_text = "успешно собран" if conclusion == "success" else f"завершен ({conclusion})"

                text = f"{emoji} <b>[GitHub Actions] Билд {result_text}!</b>\n\n"
                text += f"🎬 <b>Стенд:</b> {stand_info}\n"
                if stand_url:
                    text += f"🌐 <b>URL:</b> {stand_url}\n"
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
