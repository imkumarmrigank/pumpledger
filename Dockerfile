FROM python:3.11-slim

# Tesseract is a system binary, not a Python package — this is why the app
# needs a Docker deploy on Render rather than its native Python buildpack.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render sets $PORT; default to 8501 for local `docker run`.
ENV PORT=8501
EXPOSE 8501

CMD streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
