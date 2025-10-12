import logging
from django.db import close_old_connections
from owm.models import Crontab # Добавляем импорт модели Crontab
from owm.utils.base_utils import get_headers # Импорт get_headers
import requests # Импорт requests
import json # Импорт json для обработки ответов
from owm.utils.db_utils import db_get_promo_products # Новый импорт
from owm.utils.oz_utils import (
    ozon_get_all_price,
    ozon_get_discount_tasks,
    ozon_decline_discount_tasks,
    ozon_approve_discount_tasks,
)
from datetime import datetime, timezone, timedelta

# Здесь будут импорты моделей и других утилит, необходимых для работы с акциями
# from owm.models import SomePromotionModel, OzonApiCredentials # Пример

logger = logging.getLogger(__name__)

def _chunked(iterable, size=1000):
    """Разбивает iterable на списки размером до size элементов."""
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk

def _update_ozon_promotion_timer(seller, headers, products_to_update):
    """
    Обновляет таймер минимальной цены для товаров на Ozon.
    :param seller: Объект Seller.
    :param headers: Заголовки API.
    :param products_to_update: Список product_id для проверки и возможного обновления таймера.
    """
    logger.info(f"Обновление таймера минимальной цены на Ozon для продавца {seller.id}")
    if not products_to_update:
        logger.info("Нет товаров для обновления таймера минимальной цены на Ozon.")
        return True, {
            'status': 'skipped',
            'message': 'Нет товаров для обновления',
            'details': {'products_requested': []}
        }

    status_url = "https://api-seller.ozon.ru/v1/product/action/timer/status"
    update_url = "https://api-seller.ozon.ru/v1/product/action/timer/update"

    try:
        status_payloads = []
        statuses_flat = []
        for chunk in _chunked(products_to_update, size=1000):
            request_body = {"product_ids": chunk}
            response = requests.post(status_url, headers=headers.get('ozon_headers'), json=request_body)
            response.raise_for_status()
            response_json = response.json()
            status_payloads.append(response_json)
            statuses_flat.extend(response_json.get('statuses', []))

        now_utc = datetime.now(timezone.utc)
        threshold_dt = now_utc + timedelta(days=10)

        products_needing_update = set()
        products_skipped = set()
        products_with_status = set()

        for status_entry in statuses_flat:
            product_id = status_entry.get('product_id')
            if product_id is None:
                logger.warning(f"Пропущена запись статуса без product_id: {status_entry}")
                continue

            try:
                product_id_int = int(product_id)
            except (TypeError, ValueError):
                logger.warning(f"Некорректный product_id в статусе: {product_id}")
                continue

            products_with_status.add(product_id_int)

            expired_at_raw = status_entry.get('expired_at')
            if not expired_at_raw:
                logger.info(f"Для product_id {product_id_int} отсутствует expired_at. Помечаем на обновление.")
                products_needing_update.add(product_id_int)
                continue

            try:
                expired_at_dt = datetime.fromisoformat(expired_at_raw.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"Не удалось разобрать expired_at='{expired_at_raw}' для product_id {product_id_int}. Помечаем на обновление.")
                products_needing_update.add(product_id_int)
                continue

            if expired_at_dt <= threshold_dt:
                logger.debug(f"product_id {product_id_int}: таймер истекает {expired_at_dt}, обновляем.")
                products_needing_update.add(product_id_int)
            else:
                products_skipped.add(product_id_int)

        # Если какие-то товары не вернулись в статусах, подстрахуемся и попробуем их обновить
        missing_products = {int(pid) for pid in products_to_update if int(pid) not in products_with_status}
        if missing_products:
            logger.warning(f"Не получены статусы для product_id: {sorted(missing_products)}. Помечаем их на обновление.")
            products_needing_update.update(missing_products)

        products_needing_update = sorted(products_needing_update)
        products_skipped = sorted(products_skipped)

        if not products_needing_update:
            logger.info("Все таймеры действуют более 10 дней, обновление не требуется.")
            return True, {
                'status': 'skipped',
                'message': 'Все таймеры действуют более 10 дней',
                'details': {
                    'products_requested': products_to_update,
                    'products_marked_for_update': [],
                    'products_skipped': products_skipped
                }
            }

        update_responses = []
        for chunk in _chunked(products_needing_update, size=1000):
            request_body = {"product_ids": chunk}
            response = requests.post(update_url, headers=headers.get('ozon_headers'), json=request_body)
            response.raise_for_status()
            update_responses.append(response.json())

        logger.info(f"Успешно обновлены таймеры акций на Ozon для товаров: {products_needing_update}")
        return True, {
            'status': 'success',
            'details': {
                'products_requested': products_to_update,
                'products_marked_for_update': products_needing_update,
                'products_skipped': products_skipped,
                'update_responses': update_responses
            }
        }

    except requests.exceptions.HTTPError as http_err:
        error_msg = f"HTTP ошибка при обновлении таймера Ozon: {http_err}"
        logger.error(error_msg, exc_info=True)
        return False, {'error': error_msg, 'status_code': http_err.response.status_code}
    except requests.exceptions.RequestException as req_err:
        error_msg = f"Ошибка запроса при обновлении таймера Ozon: {req_err}"
        logger.error(error_msg, exc_info=True)
        return False, {'error': error_msg}
    except json.JSONDecodeError as json_err:
        error_msg = f"Ошибка декодирования JSON ответа Ozon: {json_err}. Ответ: {response.text}"
        logger.error(error_msg, exc_info=True)
        return False, {'error': error_msg}
    except Exception as e:
        error_msg = f"Неожиданная ошибка при обновлении таймера Ozon: {e}"
        logger.error(error_msg, exc_info=True)
        return False, {'error': error_msg}

def autoupdate_sync_promotions(cron_id):
    """
    Функция для синхронизации акций Ozon по заданному cron_id.
    """
    logger.info(f"[cron_id: {cron_id}] Starting Ozon promotions sync.")
    try:
        cron = Crontab.objects.select_related('seller').get(id=cron_id)
        seller = cron.seller
    except Crontab.DoesNotExist:
        logger.error(f"autoupdate_sync_promotions: Crontab с id {cron_id} не найден.")
        return False, {'error': f'Crontab с id {cron_id} не найден.'}
    except Exception as e:
        logger.error(f"Ошибка при получении Crontab или Seller для cron_id {cron_id}: {e}", exc_info=True)
        return False, {'error': f'Ошибка при получении данных продавца: {str(e)}'}

    try:
        # Получаем заголовки API
        headers = get_headers(seller)
        if not headers or 'ozon_headers' not in headers:
            return False, {'error': 'Не удалось получить заголовки Ozon API.'}

        promo_products = db_get_promo_products(seller)

        # Получаем mapping offer_id -> product_id из Ozon
        ozon_products_info = ozon_get_all_price(headers)
        if 'error' in ozon_products_info:
            logger.error(f"[cron_id: {cron_id}] Ошибка получения информации о товарах Ozon: {ozon_products_info['error']}")
            return False, {'error': f"Ошибка получения информации о товарах Ozon: {ozon_products_info['error']}"}

        offer_id_to_product_id = {}
        for offer_id, item in ozon_products_info.items():
            if not isinstance(item, dict):
                logger.warning(f"[cron_id: {cron_id}] Некорректные данные для offer_id {offer_id}: {item}")
                continue
            product_id = item.get('product_id')
            if product_id is not None:
                offer_id_to_product_id[str(offer_id)] = product_id
            else:
                logger.warning(f"[cron_id: {cron_id}] Не найден product_id в данных Ozon для offer_id {offer_id}: {item}")

        # Отбираем product_id с auto_update_days_limit_promo=True и получаем их Ozon product_id
        products_for_timer_update = []
        skipped_timer_offer_ids = []
        for offer_id, data in promo_products.items():
            if data.get('auto_update_days_limit_promo') is True:
                product_id = offer_id_to_product_id.get(offer_id)
                if product_id:
                    products_for_timer_update.append(int(product_id))
                else:
                    skipped_timer_offer_ids.append(offer_id)
        print(f"products_for_timer_update {products_for_timer_update}")
        if skipped_timer_offer_ids:
            logger.warning(f"Пропущены offer_id для обновления таймера (не найден соответствующий product_id на Ozon): {skipped_timer_offer_ids}")

        timer_update_success, timer_update_details = _update_ozon_promotion_timer(seller, headers, products_for_timer_update)

        sync_details = {
            'ozon_timer_update': timer_update_details
        }

        overall_success = timer_update_success

        logger.info(f"[cron_id: {cron_id}] Ozon promotions sync completed successfully.")
        return overall_success, {'status': 'success' if overall_success else 'failure', 'details': sync_details}
    except Exception as e:
        logger.error(f"[cron_id: {cron_id}] Error during Ozon promotions sync: {e}", exc_info=True)
        return False, {'error': str(e)}
    finally:
        close_old_connections()


def autoupdate_sync_discount_tasks(cron_id):
    logger.info(f"[cron_id: {cron_id}] Starting Ozon discount tasks sync.")
    try:
        cron = Crontab.objects.select_related('seller').get(id=cron_id)
        seller = cron.seller
    except Crontab.DoesNotExist:
        logger.error(f"autoupdate_sync_discount_tasks: Crontab с id {cron_id} не найден.")
        return False, {'error': f'Crontab с id {cron_id} не найден.'}
    except Exception as e:
        logger.error(f"Ошибка при получении Crontab или Seller для cron_id {cron_id}: {e}", exc_info=True)
        return False, {'error': f'Ошибка при получении данных продавца: {str(e)}'}

    try:
        headers = get_headers(seller)
        if not headers or 'ozon_headers' not in headers:
            return False, {'error': 'Не удалось получить заголовки Ozon API.'}

        promo_products = db_get_promo_products(seller)
        auto_accept_offer_ids = {
            offer_id: data
            for offer_id, data in promo_products.items()
            if data.get('use_discount')
        }

        if not auto_accept_offer_ids:
            return True, {
                'status': 'skipped',
                'reason': 'no_auto_discount_products'
            }

        discount_tasks_response = ozon_get_discount_tasks(headers)
        if not discount_tasks_response.get('success'):
            return False, {
                'error': discount_tasks_response.get('error', 'Failed to fetch discount tasks'),
                'details': discount_tasks_response
            }

        tasks = discount_tasks_response.get('result', [])
        if not tasks:
            logger.info(
                "[cron_id: %s] discount tasks: nothing to process",
                cron_id,
            )
            return True, {
                'status': 'success',
                'processed': 0,
                'declined': 0,
                'approved': 0
            }

        decline_payload = []
        approve_payload = []

        for task in tasks:
            offer_id = task.get('offer_id')
            if not offer_id:
                continue

            promo_settings = auto_accept_offer_ids.get(offer_id)
            if not promo_settings:
                continue

            min_price_discount = promo_settings.get('min_price_discount')
            if min_price_discount is None:
                continue

            requested_price = task.get('requested_price')
            try:
                requested_price_value = float(requested_price)
            except (TypeError, ValueError):
                continue

            try:
                min_price_discount_value = float(min_price_discount)
            except (TypeError, ValueError):
                continue

            approved_price = round(requested_price_value * (1 + 0.018), 2)

            task_id = task.get('id')
            if task_id is None:
                continue

            if approved_price < min_price_discount_value:
                logger.info(
                    "[cron_id: %s] discount decline reason: offer_id=%s, requested=%s, approved=%s < min_price_discount=%s",
                    cron_id,
                    offer_id,
                    requested_price_value,
                    approved_price,
                    min_price_discount_value,
                )
                decline_payload.append({'id': task_id})
                continue

            logger.info(
                "[cron_id: %s] discount approve calc - offer_id=%s, requested=%s, min_price_discount=%s, approved=%s",
                cron_id,
                offer_id,
                requested_price_value,
                min_price_discount_value,
                approved_price,
            )

            requested_quantity_min = task.get('requested_quantity_min')
            requested_quantity_max = task.get('requested_quantity_max')
            try:
                requested_quantity_min = int(requested_quantity_min)
            except (TypeError, ValueError):
                requested_quantity_min = 1
            try:
                requested_quantity_max = int(requested_quantity_max)
            except (TypeError, ValueError):
                requested_quantity_max = requested_quantity_min

            approve_payload.append({
                'id': task_id,
                'approved_price': approved_price,
                'approved_quantity_min': requested_quantity_min,
                'approved_quantity_max': requested_quantity_max
            })

        result_details = {
            'status': 'success',
            'processed': len(tasks),
            'declined': len(decline_payload),
            'approved': len(approve_payload)
        }

        overall_success = True

        if decline_payload:
            logger.info(
                "[cron_id: %s] discount decline payload: %s",
                cron_id,
                decline_payload,
            )
            decline_result = ozon_decline_discount_tasks(headers, decline_payload)
            result_details['decline_result'] = decline_result
            if isinstance(decline_result, dict):
                overall_success = overall_success and decline_result.get('success', False)
            else:
                overall_success = False

        if approve_payload:
            approve_result = ozon_approve_discount_tasks(headers, approve_payload)
            result_details['approve_result'] = approve_result
            if isinstance(approve_result, dict):
                if approve_result.get('success', False):
                    overall_success = overall_success and True
                else:
                    overall_success = False
                    failed_tasks = []
                    for fail in approve_result.get('result', {}).get('fail_details', []) or []:
                        task_id = fail.get('task_id')
                        if task_id:
                            failed_tasks.append({'id': task_id})
                    if failed_tasks:
                        logger.info(
                            "[cron_id: %s] re-declining failed approve tasks: %s",
                            cron_id,
                            failed_tasks,
                        )
                        decline_again = ozon_decline_discount_tasks(headers, failed_tasks)
                        result_details['decline_after_failed_approve'] = decline_again
            else:
                overall_success = False

        return overall_success, result_details
    except Exception as e:
        logger.error(f"[cron_id: {cron_id}] Error during Ozon discount tasks sync: {e}", exc_info=True)
        return False, {'error': str(e)}
    finally:
        close_old_connections()
