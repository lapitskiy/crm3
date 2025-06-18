# Используйте официальный образ Python
FROM python:3.10

# Установите рабочую директорию в контейнере
WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Установка необходимых пакетов и локалей
RUN apt-get update && apt-get install -y \
    locales \
    build-essential \
    libatlas-base-dev \
    liblzma-dev \
    libbz2-dev \
    libffi-dev && \
    echo "ru_RU.UTF-8 UTF-8" >> /etc/locale.gen && \
    locale-gen && \
    locale -a > /tmp/locales.txt

ENV LANG=ru_RU.UTF-8
ENV LANGUAGE=ru_RU:ru
ENV LC_ALL=ru_RU.UTF-8

# Скопируйте файлы зависимостей и установите их
COPY requirements.txt .
RUN cat requirements.txt
RUN pip install --no-cache-dir -v -r requirements.txt

# Скопируйте остальные файлы вашего приложения в контейнер
COPY . /app


ENV PYTHONPATH=/app