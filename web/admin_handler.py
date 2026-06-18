import logging
from datetime import datetime
from typing import Optional, Dict, List, Callable
from aiohttp import web
from collections import deque

logger = logging.getLogger("bot.admin")


class AdminMonitor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.error_log = deque(maxlen=50)
        self.services_status = {}
        self.start_time = datetime.now()
        self.last_runs: Dict[str, dict] = {}  # action_id -> {"time", "ok", "message"}

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            message = record.getMessage()
            if "Conflict: terminated by other getUpdates request" in message:
                return
            error_entry = {
                "timestamp": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "module": record.name,
                "message": message,
                "traceback": self.format(record),
            }
            self.error_log.appendleft(error_entry)

    def update_status(self, service_name: str, status: str):
        self.services_status[service_name] = {
            "status": status,
            "last_update": datetime.now().strftime("%H:%M:%S"),
        }

    def record_run(self, action_id: str, ok: bool, message: str):
        self.last_runs[action_id] = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "ok": ok,
            "message": message,
        }


monitor = AdminMonitor()


class AdminAction:
    def __init__(self, action_id: str, label: str, tab: str, handler: Callable, description: str = ""):
        self.id = action_id
        self.label = label
        self.tab = tab
        self.handler = handler
        self.description = description


TAB_TITLES = {
    "overview": "📊 Обзор",
    "jira": "🧩 Jira",
    "calendar": "📅 Календарь",
    "github": "🚀 GitHub",
    "ai": "🤖 AI",
    "errors": "🐞 Ошибки",
}
TAB_ORDER = ["overview", "jira", "calendar", "github", "ai", "errors"]


class AdminHandler:
    def __init__(self, bot_username: str, admin_token: Optional[str] = None):
        self.bot_username = bot_username
        self.admin_token = admin_token
        self.actions: Dict[str, AdminAction] = {}
        self.panel_extras: Dict[str, Callable[[], str]] = {}

    def register_action(self, action_id: str, label: str, tab: str, handler: Callable, description: str = ""):
        self.actions[action_id] = AdminAction(action_id, label, tab, handler, description)

    def register_panel_extra(self, tab: str, render_fn: Callable[[], str]):
        """Доп. HTML-блок для вкладки (например, таблица стендов), вычисляется при каждом рендере."""
        self.panel_extras[tab] = render_fn

    def _check_token(self, request: web.Request) -> bool:
        if not self.admin_token:
            return True
        supplied = request.query.get("token") or request.headers.get("X-Admin-Token")
        return supplied == self.admin_token

    # ---------- routes ----------

    async def handle_dashboard(self, request: web.Request):
        if not self._check_token(request):
            return web.Response(text="🔒 Доступ запрещен. Добавьте ?token=...", status=401)
        return web.Response(text=self._render_page(), content_type="text/html")

    async def handle_state(self, request: web.Request):
        if not self._check_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        uptime = datetime.now() - monitor.start_time
        return web.json_response({
            "uptime": str(uptime).split(".")[0],
            "services": monitor.services_status,
            "errors": list(monitor.error_log),
            "last_runs": monitor.last_runs,
        })

    async def handle_run_action(self, request: web.Request):
        if not self._check_token(request):
            return web.json_response({"ok": False, "message": "unauthorized"}, status=401)
        action_id = request.match_info.get("action_id")
        action = self.actions.get(action_id)
        if not action:
            return web.json_response({"ok": False, "message": "Неизвестное действие"}, status=404)
        try:
            result = await action.handler()
            message = result if isinstance(result, str) else "Выполнено успешно ✅"
            monitor.record_run(action_id, True, message)
            return web.json_response({"ok": True, "message": message})
        except Exception as e:
            logger.exception(f"Ошибка ручного запуска действия {action_id}")
            monitor.record_run(action_id, False, str(e))
            return web.json_response({"ok": False, "message": f"Ошибка: {e}"}, status=500)

    # ---------- rendering ----------

    def _render_page(self) -> str:
        uptime = datetime.now() - monitor.start_time

        actions_by_tab: Dict[str, List[AdminAction]] = {}
        for action in self.actions.values():
            actions_by_tab.setdefault(action.tab, []).append(action)

        tabs_nav = "".join(
            f'<button class="tab-btn{" active" if i == 0 else ""}" data-tab="{tab}">{TAB_TITLES.get(tab, tab)}</button>'
            for i, tab in enumerate(TAB_ORDER)
        )

        overview_html = self._render_overview()
        jira_html = self._tab_content("jira", actions_by_tab)
        calendar_html = self._tab_content("calendar", actions_by_tab)
        github_html = self._tab_content("github", actions_by_tab)
        ai_html = self._tab_content("ai", actions_by_tab)
        errors_html = self._tab_content("errors", actions_by_tab) + self._render_errors()

        body = f"""
        <header class="topbar">
          <div class="brand">🤖 MechtaBot</div>
          <div class="brand-sub">@{self.bot_username} · Uptime {str(uptime).split('.')[0]}</div>
        </header>
        <nav class="tabs">{tabs_nav}</nav>
        <main class="content">
          <section class="tab-panel active" data-tab="overview">{overview_html}</section>
          <section class="tab-panel" data-tab="jira">{jira_html}</section>
          <section class="tab-panel" data-tab="calendar">{calendar_html}</section>
          <section class="tab-panel" data-tab="github">{github_html}</section>
          <section class="tab-panel" data-tab="ai">{ai_html}</section>
          <section class="tab-panel" data-tab="errors">{errors_html}</section>
        </main>
        <div id="toast"></div>
        """

        token_qs = f"?token={self.admin_token}" if self.admin_token else ""
        script = self._js_template().replace("__TOKEN_QS__", token_qs)

        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MechtaBot Admin</title>
<style>{self._css()}</style>
</head>
<body>
{body}
<script>{script}</script>
</body>
</html>"""

    def _tab_content(self, tab: str, actions_by_tab: Dict[str, List[AdminAction]]) -> str:
        html = self._render_action_cards(actions_by_tab.get(tab, []))
        extra_fn = self.panel_extras.get(tab)
        if extra_fn:
            html += extra_fn()
        return html

    def _render_overview(self) -> str:
        return f"""
        <div class="card">
          <h2>Состояние сервисов</h2>
          <div id="status-grid" class="status-grid">
            {self._status_cards_html()}
          </div>
        </div>
        """

    def _status_cards_html(self) -> str:
        if not monitor.services_status:
            return "<p>Пока нет данных о статусах.</p>"
        cards = ""
        for name, info in monitor.services_status.items():
            dot_class = "dot-ok" if info["status"].lower() == "ok" else "dot-fail"
            cards += f"""
            <div class="status-card">
              <div class="status-dot {dot_class}"></div>
              <div>
                <div class="status-name">{name}</div>
                <div class="status-meta">{info['status']} · {info['last_update']}</div>
              </div>
            </div>
            """
        return cards

    def _render_action_cards(self, actions: List[AdminAction]) -> str:
        if not actions:
            return ""
        cards = ""
        for action in actions:
            last_run = monitor.last_runs.get(action.id)
            if last_run:
                cls = "ok" if last_run["ok"] else "fail"
                last_html = f'<div id="result-{action.id}" class="run-result {cls}">{last_run["message"]} ({last_run["time"]})</div>'
            else:
                last_html = f'<div id="result-{action.id}" class="run-result"></div>'
            cards += f"""
            <div class="card action-card">
              <div class="action-header">
                <h3>{action.label}</h3>
                <button class="run-btn" data-action="{action.id}">▶️ Проверить сейчас</button>
              </div>
              <p class="action-desc">{action.description}</p>
              {last_html}
            </div>
            """
        return cards

    def _render_errors(self) -> str:
        if not monitor.error_log:
            return '<div class="card"><p>Ошибок пока не зафиксировано. Работаем штатно! ✨</p></div>'
        items = ""
        for err in monitor.error_log:
            items += f"""
            <div class="error-item">
              <div class="error-head">
                <span class="error-module">{err['module']}</span>
                <span class="error-time">{err['timestamp']}</span>
              </div>
              <code>{err['message']}</code>
            </div>
            """
        return f'<div class="card"><h2>Последние ошибки (<span id="error-count">{len(monitor.error_log)}</span>)</h2><div class="error-list">{items}</div></div>'

    def _css(self) -> str:
        return """
        :root {
          --bg: #0f1115; --card: #171a21; --border: #262b35;
          --text: #e6e8eb; --muted: #8b93a1;
          --accent: #6c8cff; --ok: #3ddc84; --fail: #ff5c5c;
        }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, Segoe UI, Roboto, sans-serif; }
        .topbar { padding: 20px 28px; border-bottom: 1px solid var(--border); display: flex; align-items: baseline; gap: 14px; }
        .brand { font-size: 20px; font-weight: 600; }
        .brand-sub { color: var(--muted); font-size: 13px; }
        .tabs { display: flex; gap: 4px; padding: 0 24px; border-bottom: 1px solid var(--border); overflow-x: auto; }
        .tab-btn { background: none; border: none; color: var(--muted); padding: 14px 16px; cursor: pointer; font-size: 14px; border-bottom: 2px solid transparent; white-space: nowrap; }
        .tab-btn.active { color: var(--text); border-bottom-color: var(--accent); }
        .content { padding: 24px; max-width: 980px; margin: 0 auto; }
        .tab-panel { display: none; }
        .tab-panel.active { display: block; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
        .card h2 { margin: 0 0 14px; font-size: 16px; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
        .status-card { display: flex; align-items: center; gap: 10px; background: #1d212b; border-radius: 8px; padding: 10px 12px; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .dot-ok { background: var(--ok); }
        .dot-fail { background: var(--fail); }
        .status-name { font-size: 13px; font-weight: 500; }
        .status-meta { font-size: 12px; color: var(--muted); }
        .action-card .action-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
        .action-card h3 { margin: 0; font-size: 15px; }
        .action-desc { color: var(--muted); font-size: 13px; margin: 8px 0 0; }
        .run-btn { background: var(--accent); color: white; border: none; border-radius: 8px; padding: 8px 14px; font-size: 13px; cursor: pointer; white-space: nowrap; }
        .run-btn:disabled { opacity: 0.6; cursor: default; }
        .run-result { font-size: 13px; margin-top: 10px; color: var(--muted); min-height: 16px; }
        .run-result.ok { color: var(--ok); }
        .run-result.fail { color: var(--fail); }
        .error-list { max-height: 480px; overflow-y: auto; }
        .error-item { border-bottom: 1px solid var(--border); padding: 10px 0; }
        .error-head { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
        .error-module { color: var(--fail); font-weight: 600; }
        code { font-size: 12px; word-break: break-word; }
        .data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .data-table th, .data-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
        .data-table th { color: var(--muted); font-weight: 500; }
        #toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 18px; border-radius: 8px; font-size: 13px; opacity: 0; transform: translateY(10px); transition: all .2s; pointer-events: none; }
        #toast.show { opacity: 1; transform: translateY(0); }
        #toast.ok { background: var(--ok); color: #06291a; }
        #toast.fail { background: var(--fail); color: #2a0000; }
        """

    def _js_template(self) -> str:
        return """
const TOKEN_QS = "__TOKEN_QS__";

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.querySelector(`.tab-panel[data-tab="${btn.dataset.tab}"]`).classList.add('active');
  });
});

function showToast(message, ok) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = ok ? 'show ok' : 'show fail';
  setTimeout(() => { toast.className = ''; }, 5000);
}

document.querySelectorAll('.run-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const actionId = btn.dataset.action;
    const resultEl = document.getElementById('result-' + actionId);
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = '⏳ Выполняется...';
    try {
      const res = await fetch(`/admin/api/run/${actionId}${TOKEN_QS}`, { method: 'POST' });
      const data = await res.json();
      if (resultEl) {
        resultEl.textContent = data.message;
        resultEl.className = 'run-result ' + (data.ok ? 'ok' : 'fail');
      }
      showToast(data.message, data.ok);
    } catch (e) {
      showToast('Ошибка сети: ' + e, false);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
});

async function refreshState() {
  try {
    const res = await fetch(`/admin/api/state${TOKEN_QS}`);
    const data = await res.json();
    const grid = document.getElementById('status-grid');
    if (grid) {
      grid.innerHTML = Object.entries(data.services).map(([name, info]) => `
        <div class="status-card">
          <div class="status-dot ${info.status.toLowerCase() === 'ok' ? 'dot-ok' : 'dot-fail'}"></div>
          <div>
            <div class="status-name">${name}</div>
            <div class="status-meta">${info.status} · ${info.last_update}</div>
          </div>
        </div>
      `).join('');
    }
    const errCount = document.getElementById('error-count');
    if (errCount) errCount.textContent = data.errors.length;
  } catch (e) { /* тихо игнорируем */ }
}
setInterval(refreshState, 15000);
"""
