FROM python:3.11-slim

# ffmpeg for rendering; fonts-dejavu-core provides DejaVu Sans for caption burn-in (ass filter)
RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p output/audio output/visuals output/final config

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
