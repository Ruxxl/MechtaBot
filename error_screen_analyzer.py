import pytesseract
from PIL import Image
import io
import re

ERROR_PATTERNS = {
    "server_error": [
        r"ошибка сервера",
        r"server error",
        r"internal server error",
        r"500",
        r"bad gateway",
    ],

    "network_issue": [
        r"проверьте интернет",
        r"нет подключения",
        r"network error",
        r"timeout",
        r"connection timed out",
    ],

    "client_error": [
        r"bad request",
        r"400",
        r"request error",
    ],

    "auth_error": [
        r"unauthorized",
        r"403",
        r"доступ запрещен",
    ],

    "not_found": [
        r"not found",
        r"404",
        r"страница не найдена",
    ],
}


RECOMMENDATIONS = {
    "server_error": "❗ Похоже на ошибку сервера. Проверь логи backend и последний деплой.",
    "network_issue": "📶 Похоже на сетевую ошибку. Проверь интернет, VPN, статус API.",
    "client_error": "⚠️ Похоже на некорректный запрос. Проверь параметры API / тело запроса.",
    "auth_error": "🔐 Ошибка авторизации. Пользователь может быть не авторизован.",
    "not_found": "🔎 Ресурс не найден. Проверь URL или роутинг.",
    "unknown": "🤔 Ошибка не идентифицирована. Лучше оформить баг вручную.",
}


def extract_text_from_image(file_bytes: bytes) -> str:
    """OCR распознавание текста на скриншоте"""
    image = Image.open(io.BytesIO(file_bytes))
    text = pytesseract.image_to_string(image, lang="eng+rus")  # русский+английский
    return text


def analyze_error_text(text: str) -> dict:
    """Поиск паттернов и определение типа ошибки"""
    found_categories = []

    for category, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                found_categories.append(category)
                break

    if not found_categories:
        return {
            "type": "unknown",
            "recommendation": RECOMMENDATIONS["unknown"],
            "found_patterns": [],
        }

    # Берём первую найденную категорию — самая вероятная
    category = found_categories[0]

    return {
        "type": category,
        "recommendation": RECOMMENDATIONS[category],
        "found_patterns": found_categories,
    }

