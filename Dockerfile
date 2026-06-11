FROM python:3.12-slim

WORKDIR /app

# Install deps first so they cache across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The host (Render/Fly/Railway) injects $PORT; bind to it, fall back to 8000.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
