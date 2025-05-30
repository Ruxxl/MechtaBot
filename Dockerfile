# Используем официальный образ Python в качестве базового
FROM python:3.12-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем скрипт в контейнер
COPY test.py .

# (Если есть зависимости, добавь строку ниже)
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# Указываем команду по умолчанию для запуска скрипта
CMD ["python", "test.py"]
