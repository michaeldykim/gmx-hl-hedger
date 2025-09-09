FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends     build-essential curl ca-certificates &&     rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY .env ./.env

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.app.main"]
