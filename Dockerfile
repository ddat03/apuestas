FROM python:3.11-slim

WORKDIR /app

COPY deportes_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY deportes_bot/ ./deportes_bot/

CMD ["python", "deportes_bot/bot_server.py"]
