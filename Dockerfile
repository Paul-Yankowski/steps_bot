FROM python:3.12-slim

# Системные зависимости: Tesseract OCR (рус+англ) и библиотеки для OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render сам подставляет переменную PORT — приложение слушает именно её
# (см. bot_old.py: os.environ.get("PORT", 8443))
CMD ["python", "bot.py"]
