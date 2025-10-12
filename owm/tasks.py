from crm3.celery import app
from .models import Crontab
from django.db import close_old_connections
# Создаем отдельный цикл событий в отдельном потоке
from celery import group
import logging
from .utils.base_utils import autoupdate_sync_inventory
from .utils.promotions_utils import autoupdate_sync_promotions, autoupdate_sync_discount_tasks
from .utils.redis_utils import save_task_history, get_task_history
from datetime import datetime # Для времени начала/окончания задачи


logger_info = logging.getLogger('crm3_info')

@app.task
def dispatch_scheduled_marketplace_syncs():
    """
    Основная задача, которая диспетчеризует задачи для всех активных cron.
    """
    #logger_info.info(f"Starting autoupdate for cron_id")
    return orchestrate_user_marketplace_updates.delay()


@app.task
def orchestrate_user_marketplace_updates():
        crontabs = Crontab.objects.filter(name="autoupdate")
        #logger_info.info(f"Found {crontabs.count()} active crontabs for autoupdate.")
        task_group = group([run_autoupdate.s(cron.id) for cron in crontabs])
        result = task_group.apply_async()
        # Закрываем старые соединения с базой данных для очистки ресурсов
        close_old_connections()
        return result


@app.task
def dispatch_discount_task_syncs():
    crontabs = Crontab.objects.filter(name="autoupdate")
    task_group = group([run_discount_tasks_autoupdate.s(cron.id) for cron in crontabs])
    result = task_group.apply_async()
    close_old_connections()
    return result

@app.task(bind=True) # Добавляем bind=True, чтобы получить доступ к self (для task.request.id)
def run_autoupdate(self, cron_id):
    start_time = datetime.now()
    task_status = 'SUCCESS'
    task_result = {}
    overall_error_messages = [] # Используем список для сбора всех сообщений об ошибках

    try:
        # 1. Синхронизация инвентаря (товаров)
        inventory_sync_success, inventory_sync_details = autoupdate_sync_inventory(cron_id=cron_id)
        task_result['inventory_sync'] = inventory_sync_details
        if not inventory_sync_success:
            task_status = 'FAILURE'
            overall_error_messages.append(f"[cron_id: {cron_id}] Inventory sync failed. Details: {inventory_sync_details.get('error', 'Unknown inventory error')}")
            logger_info.error(f"[cron_id: {cron_id}] Inventory sync failed. Details: {inventory_sync_details}")

        # 2. Синхронизация акций (ТЕПЕРЬ НЕ ЗАВИСИТ ОТ ИНВЕНТАРИЗАЦИИ)
        promotions_sync_success = False
        promotions_sync_details = {'status': 'not_run', 'reason': 'Unknown state'}
        try:
            promotions_sync_success, promotions_sync_details = autoupdate_sync_promotions(cron_id=cron_id)
            task_result['promotions_sync'] = promotions_sync_details
            if not promotions_sync_success:
                task_status = 'FAILURE' # Если промоакции не удались, общая задача все равно является неудачной
                overall_error_messages.append(f"[cron_id: {cron_id}] Promotions sync failed. Details: {promotions_sync_details.get('error', 'Unknown promotions error')}")
                logger_info.error(f"[cron_id: {cron_id}] Promotions sync failed. Details: {promotions_sync_details}")
        except Exception as promo_e:
            promotions_sync_details = {'error': str(promo_e), 'status': 'exception'}
            task_result['promotions_sync'] = promotions_sync_details
            task_status = 'FAILURE'
            overall_error_messages.append(f"[cron_id: {cron_id}] Promotions sync raised an exception: {str(promo_e)}")
            logger_info.error(f"[cron_id: {cron_id}] Promotions sync raised an exception: {str(promo_e)}", exc_info=True)


    except Exception as e:
        task_status = 'FAILURE'
        overall_error_messages.append(f"[cron_id: {cron_id}] An unexpected error occurred during task execution: {str(e)}")
        logger_info.error(f"[cron_id: {cron_id}] Task failed due to unexpected error: {str(e)}", exc_info=True)
        task_result = {'overall_exception': str(e)}

    finally:
        end_time = datetime.now()
        final_error_message = "; ".join(overall_error_messages) if overall_error_messages else None

        task_info = {
            'task_id': self.request.id,
            'cron_id': cron_id,
            'status': task_status,
            'result': task_result,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'error_message': final_error_message # Используем объединенные сообщения об ошибках
        }
        save_task_history(cron_id, task_info)
        return task_result # Celery все еще нужен возвращаемое значение для его бэкенда результатов


@app.task(bind=True)
def run_discount_tasks_autoupdate(self, cron_id):
    start_time = datetime.now()
    task_status = 'SUCCESS'
    task_result = {}
    error_message = None

    try:
        discount_sync_success, discount_sync_details = autoupdate_sync_discount_tasks(cron_id=cron_id)
        task_result['discount_sync'] = discount_sync_details
        if not discount_sync_success:
            task_status = 'FAILURE'
            error_message = discount_sync_details.get('error', 'Unknown discount sync error')
    except Exception as e:
        task_status = 'FAILURE'
        error_message = str(e)
        task_result = {'overall_exception': str(e)}

    finally:
        end_time = datetime.now()
        task_info = {
            'task_id': self.request.id,
            'cron_id': cron_id,
            'status': task_status,
            'result': task_result,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'error_message': error_message,
        }
        save_task_history(cron_id, task_info)
        return task_result






