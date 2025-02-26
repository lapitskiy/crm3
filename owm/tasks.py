from crm3.celery import app
from .models import Crontab
from django.db import close_old_connections
# Создаем отдельный цикл событий в отдельном потоке
from celery import group
import logging
from .utils.base_utils import autoupdate_sync_inventory

logger_info = logging.getLogger('crm3_info')

@app.task
def sync_inventory_owm():
    """
    Основная задача, которая диспетчеризует задачи для всех активных cron.
    """
    #logger_info.info(f"Starting autoupdate for cron_id")
    return run_sync_inventory()


def run_sync_inventory():
        crontabs = Crontab.objects.filter(name="autoupdate")
        #logger_info.info(f"Found {crontabs.count()} active crontabs for autoupdate.")
        task_group = group([run_autoupdate.s(cron.id) for cron in crontabs])
        result = task_group.apply_async()
        # Закрываем старые соединения с базой данных для очистки ресурсов
        close_old_connections()
        return result

@app.task
def run_autoupdate(cron_id):
    return autoupdate_sync_inventory(cron_id=cron_id)






