import redis
import json
from django.conf import settings
from datetime import datetime

REDIS_HOST = settings.REDIS_HOST
REDIS_PORT = settings.REDIS_PORT
REDIS_PASSWORD = settings.REDIS_PASSWORD
REDIS_DB_CELERY_RESULTS = settings.CELERY_RESULT_BACKEND.split('/')[-1] # Получаем номер БД из настроек Celery

# Подключение к Redis
# Используем отдельное подключение для истории задач, чтобы не конфликтовать с Celery_Result_Backend
r = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db=REDIS_DB_CELERY_RESULTS)

MAX_TASK_HISTORY = 100 # Максимальное количество сохраняемых задач

def save_task_history(cron_id, task_info):
    """
    Сохраняет информацию о задаче в Redis для конкретного cron_id.
    :param cron_id: ID объекта Crontab.
    :param task_info: Словарь с информацией о задаче (task_id, status, result, start_time, end_time, error).
    """
    key = f'autoupdate:task_history:{cron_id}'
    task_info_json = json.dumps(task_info, default=str) # default=str для сериализации datetime
    r.lpush(key, task_info_json)
    r.ltrim(key, 0, MAX_TASK_HISTORY - 1) # Обрезаем список до MAX_TASK_HISTORY

def get_task_history(cron_id):
    """
    Извлекает историю задач из Redis для конкретного cron_id.
    :param cron_id: ID объекта Crontab.
    :return: Список словарей с информацией о задачах.
    """
    key = f'autoupdate:task_history:{cron_id}'
    raw_history = r.lrange(key, 0, -1)
    history = []
    for item in raw_history:
        history.append(json.loads(item))
    return history




