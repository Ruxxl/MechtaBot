import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.environ.get('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.environ.get('JIRA_PARENT_KEY', 'AS-3047')
JIRA_URL = os.environ.get('JIRA_URL', 'https://mechtamarket.atlassian.net')

ADMIN_ID = int(os.environ.get('ADMIN_ID', '998292747'))
TESTERS_CHANNEL_ID = int(os.environ.get('TESTERS_CHANNEL_ID', '-1002196628724'))

ICS_URL = os.environ.get(
    'ICS_URL',
    "https://calendar.yandex.ru/export/ics.xml?private_token=dba95cc621742f7b9ba141889e288d2e0987fae3&tz_id=Asia/Almaty"
)
CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', 60))
NOTIFY_MINUTES = int(os.environ.get('NOTIFY_MINUTES', 60))
EVENT_PHOTO_PATH = os.environ.get('EVENT_PHOTO_PATH', "event.jpg")
