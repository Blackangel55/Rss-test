FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Health-check port (Koyeb web services probe this; the bot itself uses polling)
EXPOSE 8000

CMD ["python", "bot.py"]
