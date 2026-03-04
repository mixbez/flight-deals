FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY main.py .
COPY config.json .

# Create state.json if needed (will be overwritten by volume mount)
RUN echo '{"users": {}, "pending": {}, "revoked": {}, "last_update_id": 0}' > state.json

# Run the bot
CMD ["python3", "main.py"]
