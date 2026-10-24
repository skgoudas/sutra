FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[adk,server,vertex,jwt,otel]" \
    && useradd --system --uid 10001 --no-create-home sutra

USER 10001
EXPOSE 8080
CMD ["python", "examples/it_helpdesk_server.py"]
