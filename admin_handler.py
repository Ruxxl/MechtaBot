import logging
from datetime import datetime
from aiohttp import web
from collections import deque

class AdminMonitor(logging.Handler):
    def __init__(self):
        super().__init__()
        # Храним последние 50 ошибок
        self.error_log = deque(maxlen=50)
        self.services_status = {}
        self.start_time = datetime.now()

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            message = record.getMessage()
            
            # Игнорируем специфическую ошибку конфликта сессий (две копии бота)
            if "Conflict: terminated by other getUpdates request" in message:
                return

            error_entry = {
                "timestamp": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "module": record.name,
                "message": message,
                "traceback": self.format(record)
            }
            self.error_log.appendleft(error_entry)

    def update_status(self, service_name: str, status: str):
        self.services_status[service_name] = {
            "status": status,
            "last_update": datetime.now().strftime("%H:%M:%S")
        }

monitor = AdminMonitor()

class AdminHandler:
    def __init__(self, bot_username: str):
        self.bot_username = bot_username

    async def handle_dashboard(self, request: web.Request):
        """Рендерит простую HTML страницу админки"""
        uptime = datetime.now() - monitor.start_time
        
        # Формируем строки для таблицы сервисов
        services_html = ""
        for name, info in monitor.services_status.items():
            status_color = "green" if info["status"].lower() == "ok" else "red"
            services_html += f"""
            <tr>
                <td>{name}</td>
                <td style="color: {status_color}">{info['status']}</td>
                <td>{info['last_update']}</td>
            </tr>
            """

        # Формируем строки для лога ошибок
        errors_html = ""
        for err in monitor.error_log:
            errors_html += f"""
            <div style="border-bottom: 1px solid #ccc; padding: 10px;">
                <strong style="color: #d9534f;">[{err['timestamp']}] {err['module']}</strong><br>
                <code>{err['message']}</code>
            </div>
            """

        if not errors_html:
            errors_html = "<p>Ошибок пока не зафиксировано. Работаем штатно! ✨</p>"

        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>MechtaBot Admin</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: sans-serif; margin: 40px; background: #f4f4f9; }}
                .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
                table {{ width: 100%; border-collapse: collapse; }}
                th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #eee; }}
                h1 {{ color: #333; }}
                pre {{ background: #eee; padding: 10px; overflow-x: auto; font-size: 12px; }}
            </style>
        </head>
        <body>
            <h1>🤖 MechtaBot Dashboard</h1>
            <div class="card">
                <p><strong>Uptime:</strong> {str(uptime).split('.')[0]}</p>
                <p><strong>Бот:</strong> @{self.bot_username}</p>
            </div>

            <div class="card">
                <h2>Статус функционалов</h2>
                <table>
                    <tr><th>Модуль</th><th>Статус</th><th>Обновлено</th></tr>
                    {services_html}
                </table>
            </div>

            <div class="card">
                <h2>Последние ошибки (50)</h2>
                <div style="max-height: 400px; overflow-y: auto;">
                    {errors_html}
                </div>
            </div>
        </body>
        </html>
        """
        return web.Response(text=html_template, content_type='text/html')