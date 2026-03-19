FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY main.py .

# Create correct config.json with default values
RUN printf '{\n  "aviasales_token": "",\n  "telegram_bot_token": "",\n  "admin_chat_id": "",\n  "webhook_host": "",\n  "webhook_port": 443,\n  "listen_port": 8080,\n  "webhook_path": "/webhook-flightdeals"\n}\n' > config.json

# Create data directory for persistent state (will be mounted as volume)
RUN mkdir -p /app/data

# Run the bot
CMD ["python3", "main.py"]
