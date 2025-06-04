FROM python:3.12-slim

WORKDIR /app

COPY test.py .

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "test.py"]
