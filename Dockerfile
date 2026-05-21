# Imagen oficial de Microsoft Playwright para Python — trae Chromium +
# todas las libs de sistema pre-instaladas (libnss, libgconf, etc.).
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la app (.env y secrets/ NO se copian — vienen de variables de
# entorno en Railway; ver .dockerignore)
COPY . .

# Encoding utf-8 en consola + bind público para Railway
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    HOST=0.0.0.0

# Railway inyecta $PORT (típicamente 8080). Nuestra app lo lee de config.PORT.
EXPOSE 8080

CMD ["python", "app.py"]
