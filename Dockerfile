FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app.py .

# Persistent data volume
VOLUME ["/data"]

# Temp downloads dir
RUN mkdir -p /tmp/tg_dl

EXPOSE 8080

CMD ["python", "app.py"]
