import requests
import datetime

import traceback
import sys

from dateutil.relativedelta import relativedelta

from owm.models import Seller
from owm.utils.db_utils import db_check_awaiting_postingnumber, db_get_status
from owm.utils.ms_utils import ms_get_product

import locale
import pymorphy2

import logging

import uuid
import xlsxwriter

from typing import Dict, Any
from django.conf import settings
import os

from collections import defaultdict

import json
from django.http import JsonResponse

logger_info = logging.getLogger('crm3_info')
logger_error = logging.getLogger('crm3_error')

def ozon_update_inventory(headers,stock):
    warehouseID = ozon_get_warehouse(headers)
    url = 'https://api-seller.ozon.ru/v2/products/stocks'
    ozon_stocks = []
    #print(f'update_inventory_ozon stock {stock}')
    invalid_offer_ids = []

    for key, value in stock.items():
        if value and 'stock' in value:
            if value['stock'] < 0:
                invalid_offer_ids.append(key)
                value['stock'] = 0  # Замена значения на 0
            dict_ = {
                'offer_id': key,
                'stock': value['stock'],
                'warehouse_id': warehouseID['ozon_warehouses']
                }
            ozon_stocks.append(dict_)
        else:
            print(f"Пропущен ключ {key} из-за отсутствия данных 'stock' или пустого словаря.")
    result_json = []

    #print(f'ozon_stocks {ozon_stocks}')

    for i in range(0,len(ozon_stocks),100):
        data = {
            'stocks': ozon_stocks[i:i+99],
        }
    #print('#####')
    #print('#####')
    #print('#####')
    #print(f'ozon_data #### {data}')
    #print(f'data stock {data}')
        response = requests.post(url, headers=headers['ozon_headers'], json=data)
        resp = response.json()
        #print(f'#####')
        #print(f' resp { resp}')
        result_json.append(resp['result'])
    context = {
        'json': result_json,
        'code': response.status_code,
        'invalid': invalid_offer_ids
    }
    #print(f'OZON response {response.json()}')
    return context

def ozon_get_warehouse(headers):
    result = {}
    url = 'https://api-seller.ozon.ru/v1/warehouse/list'
    response = requests.post(url, headers=headers['ozon_headers']).json()
    #print(f'OZON get_warehouse {response}')
    result['ozon_warehouses'] = response['result'][0]['warehouse_id']
    return result

def ozon_get_awaiting_fbs(headers: dict):
    '''
    получаем последние отгрузки FBS (отправления)
    '''
    result = {}

    current_date = datetime.datetime.now()

    # Вычисляем дату неделю назад
    one_week_ago = current_date - datetime.timedelta(weeks=4)

    # Форматируем даты в строковый формат (YYYY-MM-DD)
    current_date_str = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    one_week_ago_str = one_week_ago.strftime('%Y-%m-%dT%H:%M:%SZ')

    ozon_headers = headers.get('ozon_headers')
    # оприходование
    url_awaiting = 'https://api-seller.ozon.ru/v3/posting/fbs/unfulfilled/list'
    params_awaiting = {
        "filter": {
            "delivering_date_from": one_week_ago_str,
            "delivering_date_to": current_date_str,
            "is_quantum": False,
            "status": 'awaiting_approve',
            "warehouse_id": []
        },
        "dir": "DESC",
        "limit": 1000,
        "offset": 0,
        "with": {
            "analytics_data": False,
            "barcodes": False,
            "financial_data": False,
            "translit": False
        }
        }
    # awaiting_deliver - ожидает отгрузки
    url_packag = 'https://api-seller.ozon.ru/v3/posting/fbs/list'
    params_packag = {
        "filter": {
            "is_quantum": False,
            "last_changed_status_date": {
                "from": one_week_ago_str,
                "to": current_date_str
            },
            # "order_id": 0,
            "since": one_week_ago_str,
            "status": 'awaiting_packaging',  # awaiting_deliver
            "to": current_date_str,
        },
        "dir": "DESC",
        "limit": 1000,
        "offset": 0,
        "with": {
            "analytics_data": False,
            "barcodes": False,
            "financial_data": False,
            "translit": False
        }
    }

    params_deliver = {
        "filter": {
            "is_quantum": False,
            "last_changed_status_date": {
                "from": one_week_ago_str,
                "to": current_date_str
            },
            #"order_id": 0,
            "since": one_week_ago_str,
            "status": 'awaiting_deliver', #awaiting_deliver
            "to": current_date_str,
        },
        "dir": "DESC",
        "limit": 1000,
        "offset": 0,
        "with": {
            "analytics_data": False,
            "barcodes": False,
            "financial_data": False,
            "translit": False
        }
        }

    try:
        response = requests.post(url_packag, headers=ozon_headers, json=params_packag)
        if response.status_code == 200:
            packag = response.json()
            #print(f"response_json (awaiting): {awaiting}")
        else:
            result['error'] = response.text
            print(f"ozon_get_awaiting_fbs response.text (awaiting): {response.text}")
    except Exception as e:
        result['error'] = f"Error in awaiting request: {e}"

    try:
        response = requests.post(url_packag, headers=ozon_headers, json=params_deliver)
        if response.status_code == 200:
            deliver = response.json()
            #print(f"response_json (packag): {deliver}")
        else:
            result['error'] = response.text
    except Exception as e:
        result['error'] = f"Error in packag request: {e}"


    current_product = []
    #print(f'*' * 40)
    #print(f"packag {packag}")
    #print(f"awaiting {awaiting}")
    #print(f'*' * 40)

    awaiting_packag = deliver['result']['postings']
    awaiting_packag.extend(packag['result']['postings'])

    for pack in awaiting_packag:
        product_list = []
        #print(f'pack {pack}')
        posting_number = pack['posting_number']
        status = pack['status']
        #print(f"Posting Number: {posting_number}")
        for product in pack['products']:
            price = product['price']
            offer_id = product['offer_id']
            quantity =  product['quantity']
            # "sku": 1728663479,
            product_list.append({
                "offer_id": offer_id,
                "price": price,
                "quantity": quantity
                })
        current_product.append(
            {'posting_number': posting_number,
             'status': status,
             'product_list': product_list
             })


    posting_numbers = [item['posting_number'] for item in current_product]
    check_result_dict = db_check_awaiting_postingnumber(posting_numbers)
    check_result_dict['current_product'] = current_product
    return check_result_dict

def ozon_get_status_fbs(headers: Dict[str, Any], seller: Seller):
    '''
    получаем последние статусы заказов FBS
    '''
    result = {}
    current_date = datetime.datetime.now()
    one_week_ago = current_date - datetime.timedelta(weeks=4)
    current_date_str = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    one_week_ago_str = one_week_ago.strftime('%Y-%m-%dT%H:%M:%SZ')

    orders_db = db_get_status(seller=seller, market='ozon')
    # Получаем список заказов для 'ozon'
    orders_list = orders_db.get('ozon', [])
    existing_orders = {order['posting_number']: order['status'] for order in orders_list}

    ozon_headers = headers.get('ozon_headers')
    url_orders = 'https://api-seller.ozon.ru/v3/posting/fbs/list'

    params = {
        "filter": {
            "is_quantum": False,
            "last_changed_status_date": {
                "from": one_week_ago_str,
                "to": current_date_str
            },
            # "order_id": 0,
            "since": one_week_ago_str,
            #"status": 'awaiting_packaging',  # awaiting_deliver
            "to": current_date_str,
        },
        "dir": "DESC",
        "limit": 1000,
        "offset": 0,
        "with": {
            "analytics_data": False,
            "barcodes": False,
            "financial_data": False,
            "translit": False
        }
    }

    matching_orders = {}
    try:
        response = requests.post(url_orders, headers=ozon_headers, json=params)
        if response.status_code == 200:
            json_orders = response.json()
            #print(f"json_orders: {json_orders}")
            #exit()
            #json_orders =
            matching_orders['awaiting'] = []
            matching_orders['delivering'] = []
            matching_orders['cancelled'] = []
            matching_orders['delivered'] = []
            awaiting = []
            delivering = []
            delivered = []
            cancelled = []
            try:
                #print(f"ZZZZZZZZZZZZZ")
                #print(type(json_orders))
                #print(f"ZZZZZZZZZZZZZ")
                #print(f"status")
                for order in json_orders['result']['postings']:

                    #print(type(order))
                    #print(f"posting number: {order}")

                    posting_number = order['posting_number']
                    status = order['status']
                    #print(f"status {status}")
                    substatus = order['substatus']
                    #if posting_number == '70611105-0207-1':
                    #    print(f"TYT")
                    #    print(f"TYT: {order}")
                    #if posting_number in existing_orders:
                        #print(f"status {status}\n")
                    #print(f"\n\n")
                    #print(f"status {status}\n")
                    #print(f"existing_orders[posting_number] {existing_orders.get(posting_number)}\n")
                    if posting_number in existing_orders:
                        existing_status = existing_orders[posting_number]
                        if existing_status not in status:
                            if 'awaiting' in status:
                                awaiting.append({
                                    'posting_number': posting_number,
                                    'status': status,
                                    'substatus': substatus
                                })
                            if 'delivering' in status:
                                delivering.append({
                                    'posting_number': posting_number,
                                    'status': status,
                                    'substatus': substatus
                                })
                            if 'delivered' in status:
                                delivered.append({
                                    'posting_number': posting_number,
                                    'status': status,
                                    'substatus': substatus
                                })
                            if 'cancelled' in status:
                                #print(f"TYTTTTT CCCCCC")
                                cancelled.append({
                                    'posting_number': posting_number,
                                    'status': status,
                                    'substatus': substatus
                                })
            except Exception as e:
                print(f"Error during processing: {e}")
                exc_type, exc_value, exc_tb = sys.exc_info()
                tb = traceback.extract_tb(exc_tb)[-1]  # Последний вызов в трассировке
                filename = tb.filename
                lineno = tb.lineno
                print(f"[{type(e).__name__}] {e} (файл: {filename}, строка: {lineno})")
            matching_orders['awaiting'] = awaiting
            matching_orders['delivering'] = delivering
            matching_orders['delivered'] = delivered
            matching_orders['cancelled'] = cancelled
        else:
            result['error'] = response.text
            print(f"Error ozon_get_status_fbs response.text (awaiting): {response.text}")
    except Exception as e:
        result['error'] = f"Error in awaiting request: {e}"
    return matching_orders

def ozon_get_finance(headers: dict, period: str):
    ozon_products = ozon_get_products(headers)

    sku_offer_id = {
        source["sku"]: item["offer_id"]
        for item in ozon_products['items']
        for source in item["sources"]
    }

    #print(json.dumps(prod_ozon['items'], indent=4, ensure_ascii=False))

    products = ms_get_product(headers)
    ms_opt_price_clear = {}
    #print(f"products {products}")
    if products['status_code'] != 200:
        return {'error': products}

    for item in products['response']['rows']:
        #opt_price_clear['article'] = item['article']
        #print(f"opt_price {item['buyPrice']['value']/100}")
        ms_opt_price_clear[item['article']] = {
            'opt_price': int(float(item['buyPrice']['value']) / 100) if 'buyPrice' in item and 'value' in item['buyPrice'] else 0
            }

    now = datetime.datetime.now()

    #url = "https://api-seller.ozon.ru/v2/finance/realization"
    #lastmonth_date = now - relativedelta(months=1)
    #data = {
    #    "year": lastmonth_date.year,
    #    "month": lastmonth_date.month
    #}

    #response = requests.post(url, headers=headers['ozon_headers'], json=data).json()

    #print(json.dumps(response['result']['rows'], indent=4, ensure_ascii=False))
    #print(f"response 1 {response['result']['rows']}")

    # https://docs.ozon.ru/api/seller/#operation/FinanceAPI_FinanceTransactionListV3
    url = "https://api-seller.ozon.ru/v3/finance/transaction/list"
    # Отнимаем 2 месяца
    #lastmonth_date = now - datetime.timedelta(days=now.day)

    #print(f"lastmonth_date.month {lastmonth_date.month}")


    first_day_last_month = (now.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    last_day_last_month = now.replace(day=1) - datetime.timedelta(days=1)
    first_day_last_month_iso = first_day_last_month.strftime('%Y-%m-%dT00:00:00.000Z')
    last_day_last_month_iso = last_day_last_month.strftime('%Y-%m-%dT23:59:59.999Z')
    print(f"first_day_last_month_iso {first_day_last_month_iso}")
    print(f"last_day_last_month_iso {last_day_last_month_iso}")

    page_size = 1000  # максимум, который разрешён API
    page = 1  # начинаем с первой страницы
    all_operations = []  # здесь соберём все операции

    while True:
        payload = {
            "filter": {
                "date": {
                    "from": first_day_last_month_iso,
                    "to": last_day_last_month_iso
                },
                "operation_type": [],  # или перечислите нужные
                "posting_number": "",
                "transaction_type": "all"
            },
            "page": page,
            "page_size": page_size
        }
        #data = {
        #    "year": lastmonth_date.year,
        #    "month": lastmonth_date.month
        #}

        response = requests.post(url, headers=headers['ozon_headers'], json=payload).json()
        result = response.get("result", {})
        operations = result.get("operations", [])
        all_operations.extend(operations)

        print(f"realization {page}")
        print(response["result"]["operations"][:1])
        if page >= result.get("page_count", 0) or not operations:
            break
        page += 1  # иначе берём следующую страницу

    print(f"Собрано операций: {len(all_operations)}")


    grouped_data = defaultdict(list)
    for entry in all_operations:
        raw_pn = entry['posting'].get('posting_number', 'unknown')
        #base_pn = ozon_get_base_posting_number(raw_pn)
        grouped_data[raw_pn].append(entry)

    grouped_data = dict(grouped_data)

    # Выводим результат в JSON-формате для удобства чтения
    #print(json.dumps(grouped_data, indent=4, ensure_ascii=False))
    #exit()
    #print(f"realization {grouped_data}")
    result = {}
    summed_totals = {}
    total_payoff = 0
    payoff_if = 0
    all_return_total = 0
    #print(f"sku_offer_id {sku_offer_id}")

    # - Если цена продажи sale_price будет 0, тогда пропускать этот товар для вывода в общий список продаж

    for posting_number, items in grouped_data.items():
        #print(f"№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№№")
        print(f"posting_number {posting_number}\n\n")

        sale_price = 0 # цена продажи
        payoff = 0
        new_entry = {}
        print(f'items {items}')
        for item in items:
            #print(f"^^^^^^^^^^^^^^^^^^")
            item_s = item.get('items')
            #print(f'item_s {item_s}\n')
            if item_s and len(item_s) > 0:
                sku = item_s[0].get('sku')
                offer_id = sku_offer_id.get(sku)
                opt_data = ms_opt_price_clear.get(offer_id)
                opt = opt_data.get('opt_price') if opt_data else 0
                name = item.get('name', 'unknown')
                #print(f'sku {sku} offer_id {offer_id}\n')
                #print(f'opt {opt}\n')

            accruals = item.get('accruals_for_sale', 0)
            if accruals != 0:  # учитываем только товарные строки
                sale_price += accruals
            payoff += item.get('amount', 0)  # все строки влияют на выплату
        service_fees = sale_price - payoff
        print(f'sale_price {sale_price}\n')
        print(f'payoff {payoff}\n')
        print(f'service_fees {service_fees}\n')
        print(f"^^^^^^^^^^^^^^^^^^")
        print(f"^^^^^^^^^^^^^^^^^^")
        print(f"^^^^^^^^^^^^^^^^^^")


        total_payoff += int(payoff)

        # total_payoff это сумма всех движенией за месяц, включая выплаты или возвраты за предыдыщуие заказаы и или месяцы
        # all_payoff это сумма именно заказов внутри месяца, она может быть выше, поскольу не учитывает вычиты с других заказаов, которые не привязаны
        # к конкретному заказу. в сумме выплат указывается значение total_payoff, но если суммировать все заказы, сумма будет выше, поскольку для каждого заказа не учетны эти расходы

        #print(f'total_payoff {total_payoff}')
        #print(f"^^^^^^^^^^^^^^^^^^")

        if opt != 0 and opt is not None and sale_price != 0:
            new_entry.update({
                'quantity': 1,
                'name': posting_number,
                'product_id': int(sku),
                'sale_price': int(sale_price),
                'opt': int(opt),
                'fees': int(service_fees),
                'payoff': int(payoff), # к выплате
                })
            net_profit = int(payoff) - int(opt)
            posttax_profit = net_profit - (int(payoff) * 0.06)
            net_profit_perc = (net_profit / int(opt)) * 100
            posttax_profit_perc = (posttax_profit / int(opt)) * 100
            new_entry.update({
                'net_profit': int(net_profit),
                'net_profit_perc': int(net_profit_perc),
                'posttax_profit': int(posttax_profit),
                'posttax_profit_perc': int(posttax_profit_perc),
            })

            if offer_id is not None:
                if offer_id in result:
                    result[offer_id].append(new_entry)
                else:
                    result[offer_id] = [new_entry]
            #print(f'result {offer_id} posting_number {posting_number} NEW ENTRY {new_entry}')

        #print(f'items {items}')
        #print(f"^^^^^^^^^^^^^^^^^^\n")
    print(f'total_payoff {total_payoff}')
    print(f"^^^^^^^^^^^^^^^^^^")
    #print(json.dumps(result, indent=4, ensure_ascii=False))
    # seller_price_per_instance Цена продавца с учётом скидки.
    # 'item': {'offer_id': 'cer_black_20', 'barcode': 'OZN1249002486', 'sku': 1249002486},
    sorted_report = dict(sorted(result.items(), key=lambda item: (item[0][:3], item[0][3:])))

    # Итерация по результатам и вычисление суммы total_price
    for offer_id, entries in result.items():
        #print(f'entries {entries}')
        offer_id_total_payoff = sum(entry['payoff'] for entry in entries)
        net_profit_sum = sum(entry['net_profit'] for entry in entries)
        net_profit_sum = sum(entry['net_profit'] for entry in entries)
        posttax_profit_sum = sum(entry['posttax_profit'] for entry in entries)
        total_quantity = sum(entry['quantity'] for entry in entries)

        # Расчет средней цены продажи
        average_sales_price = payoff / total_quantity if total_quantity > 0 else 0

        average_percent_posttax = sum(entry['posttax_profit_perc'] for entry in entries) / len(
            entries) if entries else 0

        # Сохраняем результаты в словарь
        summed_totals[offer_id] = {
            "payoff": int(offer_id_total_payoff),
            "net_profit_sum": int(net_profit_sum),
            "posttax_profit_sum": int(posttax_profit_sum),
            "average_sales_price": int(average_sales_price),
            "average_percent_posttax": int(average_percent_posttax),
            "total_quantity": int(total_quantity),
        }
        #print(json.dumps(entries, indent=4, ensure_ascii=False))
        #print(f"^^^^^^^^^^^^^^^^^^")
        #print(json.dumps(summed_totals[offer_id], indent=4, ensure_ascii=False))
        #print(f"#################")
        #print(f"#################")
        #print(f"#################")
        #print(f"#################")
        #print(f"#################")

    #print(f'summed_totals {summed_totals}')
    #print(json.dumps(summed_totals, indent=4, ensure_ascii=False))
    all_totals = {
        "all_total_price_sum": total_payoff, # выводим именно суммы выплаты, а не сумму всех заказов
        "all_net_profit_sum": sum(value["net_profit_sum"] for value in summed_totals.values()),
        "all_posttax_profit_sum": sum(value["posttax_profit_sum"] for value in summed_totals.values()),
        "all_quantity": sum(value["total_quantity"] for value in summed_totals.values()),
        "all_return_total": all_return_total
    }
    all_totals = {
        key: f"{value:,}" if isinstance(value, (int, float)) else value
        for key, value in all_totals.items()
    }

    locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')

    start_date = datetime.datetime.strptime(first_day_last_month_iso[:10], '%Y-%m-%d')
    stop_date = datetime.datetime.strptime(last_day_last_month_iso[:10], '%Y-%m-%d')

    month_name = start_date.strftime('%B')
    morph = pymorphy2.MorphAnalyzer()
    month_nominative = morph.parse(month_name)[0].inflect({'nomn'}).word
    day_delta = stop_date - start_date
    header_data = {}
    header_data['month'] = month_nominative.capitalize()
    header_data['day_delta'] = day_delta.days

    # Выводим отсортированный словарь
    result = {}
    result['sorted_report'] = sorted_report
    result['all_totals'] = all_totals
    result['summed_totals'] = summed_totals
    result['header_data'] = header_data
    return result

def ozon_get_base_posting_number(posting_number: str) -> str:
    """
    Возвращает «базовую» часть номера из первых двух частей, разделённых дефисом.
    Например:
    - 74707503-0159       -> 74707503-0159
    - 74707503-0159-1     -> 74707503-0159
    - 74707503-0159-other -> 74707503-0159
    """
    parts = posting_number.split('-')

    # Если в номере 2 или больше частей, вернём первые две, соединённые дефисом
    if len(parts) >= 2:
        return parts[0] + '-' + parts[1]

    # Если по какой-то причине меньше двух частей (например, совсем другой формат),
    # просто возвращаем исходный номер
    return posting_number

def ozon_get_all_price(headers):
    opt_price = ms_get_product(headers)
    if opt_price.get('error') is None:
        opt_price_clear = {}
        for item in opt_price['response']['rows']:
            #opt_price_clear['article'] = item['article']
            #print(f"opt_price {item['buyPrice']['value']/100}")
            opt_price_clear[item['article']] = {
                'opt_price' : int(float(item['buyPrice']['value']) / 100),
                }

        url = "https://api-seller.ozon.ru/v2/finance/realization"
        now = datetime.datetime.now()
        lastmonth_date = now - datetime.timedelta(days=now.day)
        data = {
            "year": lastmonth_date.year,
            "month": lastmonth_date.month
        }

        #print(f"ozon_headers: {headers['ozon_headers']}")
        
        try:
            response_raw = requests.post(url, headers=headers['ozon_headers'], json=data, timeout=10)
            try:
                response = response_raw.json()
            except Exception as e:
                return JsonResponse(
                    {"error": f"Ошибка декодирования JSON: {str(e)}", "response": response_raw.text},
                    status=503
                )
        except requests.exceptions.Timeout:
            print("Ozon API timeout")
            logger.warning("Ozon API timeout")
            response = {}
        except requests.exceptions.RequestException as e:
            print(f"Ozon API general request error: {e}")
            logger.warning(f"Ozon API general request error: {e}")
            response = {}        
                
        #print(f"utils.py | get_all_price_ozon | response: {response}")
        # Этот код обрабатывает данные о продажах товаров: для каждого offer_id он группирует цены по диапазонам ±10%.
        # В результате формируется словарь realization с итоговой статистикой по каждому offer_id.
        # Если цен нет, список avg остаётся пустым.

        realization = {}
        price_accumulator = {}
        price_groups = {}

        # Собираем все цены для каждого offer_id
        for item in response.get('result', {}).get('rows', []):
            offer_id = item['item'].get('offer_id')
            quantity = item['delivery_commission']['quantity'] if item.get('delivery_commission') and 'quantity' in item['delivery_commission'] else 0
            seller_price_per_instance = item.get('seller_price_per_instance')
            # Суммируем количество продаж
            if offer_id not in realization or realization[offer_id] is None:
                realization[offer_id] = {'sale_qty': quantity}
            else:
                realization[offer_id]['sale_qty'] = realization[offer_id].get('sale_qty', 0) + quantity

            # Суммируем цену и количество для расчёта средней цены
            if offer_id not in price_accumulator:
                price_accumulator[offer_id] = {'total_price': 0, 'count': 0}
            if seller_price_per_instance is not None:
                price_accumulator[offer_id]['total_price'] += float(seller_price_per_instance) * quantity
                price_accumulator[offer_id]['count'] += quantity

            # Собираем все цены (с учетом количества)
            if offer_id not in price_groups:
                price_groups[offer_id] = []
            if seller_price_per_instance is not None:
                price_groups[offer_id].extend([float(seller_price_per_instance)] * quantity)

        # Вычисляем среднюю цену для каждого offer_id
        for offer_id, acc in price_accumulator.items():
            avg_price = acc['total_price'] / acc['count'] if acc['count'] > 0 else 0
            if offer_id in realization:
                realization[offer_id]['avg_seller_price'] = int(avg_price)
            else:
                realization[offer_id] = {'sale_qty': 0, 'avg_seller_price': int(avg_price)}

        # Убедимся, что у всех offer_id в realization есть avg_seller_price
        for offer_id in realization:
            if 'avg_seller_price' not in realization[offer_id]:
                realization[offer_id]['avg_seller_price'] = 0

        # Группировка цен по диапазону 10% (жадно, без повторов)
        for offer_id, prices in price_groups.items():
            if not prices:
                realization[offer_id]['avg'] = []
                continue
            sorted_prices = sorted(prices)
            used = [False] * len(sorted_prices)
            groups = []
            i = 0
            while i < len(sorted_prices):
                if used[i]:
                    i += 1
                    continue
                group = [sorted_prices[i]]
                used[i] = True
                for j in range(i + 1, len(sorted_prices)):
                    if not used[j] and abs(sorted_prices[j] - group[0]) / group[0] <= 0.1:
                        group.append(sorted_prices[j])
                        used[j] = True
                groups.append(group)
                i += 1
            avg_list = [{len(g): int(sum(g) / len(g))} for g in groups if g]
            realization[offer_id]['avg'] = avg_list

        #print(f"realization: {realization}")
        #exit()

        url = "https://api-seller.ozon.ru/v5/product/info/prices"
        data = {
            "filter": {
                "visibility": "ALL",
            },
                "last_id": "",
                "limit": 1000
            }
        
        try:
            response_raw = requests.post(url, headers=headers['ozon_headers'], json=data, timeout=10)
            try:
                response = response_raw.json()
            except Exception as e:
                return JsonResponse(
                    {"error": f"Ошибка декодирования JSON: {str(e)}", "response": response_raw.text},
                    status=503
                )
        except requests.exceptions.Timeout:
            print("Ozon API timeout")
            logger_info.info("Ozon API timeout")
            response = {}
        except requests.exceptions.RequestException as e:
            print(f"Ozon API general request error: {e}")
            logger_error.error(f"Ozon API general request error: {e}")
            response = {}
        #print(f"response {response['result']['items'][0]}")
        result = {}
        if 'items' not in response:
            return {'error': response}
        for item in response['items']:
            if item['offer_id'] not in opt_price_clear:
                continue
            if item['offer_id'] not in realization:
                realization[item['offer_id']] = {'sale_qty': 0}  
                             
            marketing_seller_price = float(item['price']['marketing_seller_price']) # это минимальная цена которую тебе зачислит озон
            
            acquiring = 2
            price = float(item['price']['price'])
            min_price = float(item['price']['min_price'])
            commissions = item.get('commissions', {})
            sales_percent_fbs = commissions.get('sales_percent_fbs', 0)  # Процент комиссии за продажу (FBS)
            fbs_deliv_to_customer_amount = commissions.get('fbs_deliv_to_customer_amount', 0)  # Последняя миля (FBS)
            fbs_direct_flow_trans_max_amount = commissions.get('fbs_direct_flow_trans_max_amount', 0)  # Магистраль до (FBS)
            fbs_direct_flow_trans_min_amount = commissions.get('fbs_direct_flow_trans_min_amount', 0)  # Магистраль от (FBS)
            fbs_first_mile_max_amount = commissions.get('fbs_first_mile_max_amount', 0)  # Максимальная комиссия за обработку отправления (FBS)
            fbs_first_mile_min_amount = commissions.get('fbs_first_mile_min_amount', 0)  # Минимальная комиссия за обработку отправления (FBS)
            fbs_return_flow_amount = commissions.get('fbs_return_flow_amount', 0)  # Комиссия за возврат и отмену, обработка отправления (FBS)
            fbo_deliv_to_customer_amount = commissions.get('fbo_deliv_to_customer_amount', 0)  # Последняя миля (FBO)
            fbo_direct_flow_trans_max_amount = commissions.get('fbo_direct_flow_trans_max_amount', 0)  # Магистраль до (FBO)
            fbo_direct_flow_trans_min_amount = commissions.get('fbo_direct_flow_trans_min_amount', 0)  # Магистраль от (FBO)
            fbo_return_flow_amount = commissions.get('fbo_return_flow_amount', 0)  # Комиссия за возврат и отмену (FBO)
            sales_percent_fbo = commissions.get('sales_percent_fbo', 0)  # Процент комиссии за продажу (FBO)
            
            # Среднее значение магистрали для FBS и FBO
            fbs_direct_flow_trans = (float(fbs_direct_flow_trans_max_amount) + float(fbs_direct_flow_trans_min_amount)) / 2
            fbo_direct_flow_trans = (float(fbo_direct_flow_trans_max_amount) + float(fbo_direct_flow_trans_min_amount)) / 2
            fbs_first_mile_avg = float(fbs_first_mile_max_amount)

            opt_price_value = opt_price_clear[item['offer_id']]['opt_price']
            
            # Рассчитываем стоимость доставки для FBO, используя уже определённые переменные
            # Вознаграждение Ozon — это sales_percent_fbo
            
            fbo_delivery_total = (marketing_seller_price * float(sales_percent_fbo) / 100) \
                + (marketing_seller_price * float(acquiring) / 100) \
                + float(fbo_direct_flow_trans) \
                + float(fbo_deliv_to_customer_amount)

            # FBS: добавляем среднее между fbs_first_mile_max_amount и fbs_first_mile_min_amount
            fbs_delivery_total = (marketing_seller_price * float(sales_percent_fbs) / 100) \
                + (marketing_seller_price * float(acquiring) / 100) \
                + float(fbs_direct_flow_trans) \
                + float(fbs_deliv_to_customer_amount) \
                + fbs_first_mile_avg            

            # print(f"offer_id: {item['offer_id']}")
            # print(f"marketing_seller_price: {marketing_seller_price}")
            # print(f"min_price: {min_price}")
            # print(f"acquiring clear: {acquiring}")                                    
            # print(f"acquiring %: {(marketing_seller_price * float(acquiring) / 100)}")
            # print(f"fbs_delivery_total: {fbs_delivery_total}")
            # print(f"sales_percent_fbs: {sales_percent_fbs}")
            # print(f"fbs_deliv_to_customer_amount: {fbs_deliv_to_customer_amount}")
            # print(f"fbs_direct_flow_trans: {fbs_direct_flow_trans}")
            # print(f"fbs_first_mile_avg: {fbs_first_mile_avg}")
            # print(f"fbs_return_flow_amount: {fbs_return_flow_amount}")
            # print(f"fbo_deliv_to_customer_amount: {fbo_deliv_to_customer_amount}")
            # print(f"fbo_direct_flow_trans: {fbo_direct_flow_trans}")
            # print(f"fbo_return_flow_amount: {fbo_return_flow_amount}")
            # print(f"sales_percent_fbo: {sales_percent_fbo}")

            # Для FBO
            profit_price_fbo = int(marketing_seller_price) - int(fbo_delivery_total) - opt_price_value
            # Для FBS
            profit_price_fbs = int(marketing_seller_price) - int(fbs_delivery_total) - opt_price_value
                                                                    

            profit_percent_fbo = profit_price_fbo / opt_price_value * 100 if opt_price_value != 0 else 0
            profit_percent_fbs = profit_price_fbs / opt_price_value * 100 if opt_price_value != 0 else 0

            

            result[item['offer_id']] = {
                'product_id': int(float(item['product_id'])),
                'price': int(price),
                'min_price': int(min_price),
                'marketing_seller_price': int(marketing_seller_price),
                'opt_price': opt_price_value,
                'profit_percent_fbo': int(profit_percent_fbo),
                'profit_percent_fbs': int(profit_percent_fbs),
                'sale_qty': realization[item['offer_id']]['sale_qty'],
                'avg_seller_price': realization[item['offer_id']].get('avg_seller_price', 0),
                'avg_list': realization[item['offer_id']].get('avg', []),
                'profit_price_fbo': int(profit_price_fbo),
                'profit_price_fbs': int(profit_price_fbs),
                'fbs_delivery_total': int(fbs_delivery_total),
                'fbo_delivery_total': int(fbo_delivery_total),
                'acquiring': acquiring,
                'sales_percent_fbs': sales_percent_fbs,
                'fbs_deliv_to_customer_amount': fbs_deliv_to_customer_amount,
                'fbs_direct_flow_trans': fbs_direct_flow_trans,
                'fbs_first_mile_avg': fbs_first_mile_avg,
                'fbs_return_flow_amount': fbs_return_flow_amount,
                'fbo_deliv_to_customer_amount': fbo_deliv_to_customer_amount,
                'fbo_direct_flow_trans': fbo_direct_flow_trans,
                'fbo_return_flow_amount': fbo_return_flow_amount,
                'sales_percent_fbo': sales_percent_fbo,
            }

        #print(f'result ozon price {result}')
        return result
    else:
        result['error'] = opt_price['error']
        return result

def ozon_get_products(headers):

    url_list = "https://api-seller.ozon.ru/v3/product/list"
    url_barcode = "https://api-seller.ozon.ru/v3/product/info/list"
    data = {
        "filter": {
            "visibility": "ALL",
        },
            "last_id": "",
            "limit": 1000
        }
    response = requests.post(url_list, headers=headers['ozon_headers'], json=data).json()

    result = {}
    offer_list = []
    for item in response['result']['items']:
        offer_list.append(item['offer_id'])
    #print(f'result ozon price {result}')

    url_barcode = "https://api-seller.ozon.ru/v3/product/info/list"

    data = {
        "offer_id": offer_list,
        }

    response = requests.post(url_barcode, headers=headers['ozon_headers'], json=data).json()
    #print(f"extracted_data {extracted_data}")

    return response

def ozon_get_postavka(headers: dict):
    from owm.utils.base_utils import base_delete_files_with_prefix

    uuid_suffix = str(uuid.uuid4())[:6]

    path = os.path.join(settings.MEDIA_ROOT, 'owm/report/')
    url_path = os.path.join(settings.MEDIA_URL, 'owm/report/', f'stock_data_ozn_{uuid_suffix}.xlsx')
    file_path = os.path.join(settings.MEDIA_ROOT, 'owm/report/', f'stock_data_ozn_{uuid_suffix}.xlsx')
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    url = "https://api-seller.ozon.ru/v1/analytics/turnover/stocks"
    data = {
        "limit": 1000,
    }

    response = requests.post(url, headers=headers['ozon_headers'], json=data).json()

    rows = []
    for item in response.get('items', []):
        offer_id = item.get('offer_id')
        # Шаг 1: Считаем, сколько товара нужно на 90 дней
        total_stock_needed = 90 * item.get('ads')
        rows.append({
            'offer_id': item['offer_id'],
            'name': '',  # Имя оставляем пустым
            'stock_needed': int(round(max(0, total_stock_needed - item.get('current_stock')))) # Округляем до целого
            })

    # Создание XLSX файла

    prefix = 'stock_data'
    base_delete_files_with_prefix(path, prefix)

    workbook = xlsxwriter.Workbook(file_path)
    worksheet = workbook.add_worksheet()
    headers = ['Артикул', 'Имя', 'Количество']

    rows_sorted = sorted(rows, key=lambda x: x['offer_id'])  # Сортировка по 'offer_id'

    for col_num, header in enumerate(headers):
        worksheet.write(0, col_num, header)
    for row_num, row in enumerate(rows_sorted, start=1):
        worksheet.write(row_num, 0, row['offer_id'])  # Артикул
        worksheet.write(row_num, 1, row['name'])  # Имя (пустое)
        worksheet.write(row_num, 2, row['stock_needed'])  # Количество
    workbook.close()

    result = {}
    result['row'] = rows_sorted
    result['path'] = url_path
    result['code'] = 8 if response.get('code') == 8 else 0
    return result

def ozon_update_promo(promo_data, seller, headers):
    """
    Updates promotional data for a seller on Ozon.
    
    :param promo_data: The promotional data to update.
    :param seller: The seller for whom the promotion is being updated.
    :param headers: Headers for the API request.
    :return: Response from the Ozon API.
    """
    url = "https://api-seller.ozon.ru/v1/product/import/prices"                
    
    try:
        response_raw = requests.post(url, headers=headers['ozon_headers'], json=promo_data, timeout=10)
        try:
            response = response_raw.json()
            return JsonResponse({'success': True, 'response': response})
        
        except Exception as e:
            logger_error.error(f"Ошибка декодирования JSON: {str(e)} | response: {response_raw.text}")
            return JsonResponse({'success': False, 'error': f"Ошибка декодирования JSON: {str(e)}", "response": response_raw.text})
        except requests.exceptions.Timeout:
            logger_error.error("Ozon API timeout")
            return JsonResponse({'success': False, 'error': "Ozon API timeout"})
        except requests.exceptions.RequestException as e:
            logger_error.error(f"Ozon API general request error: {e}")
            return JsonResponse({'success': False, 'error': f"Ozon API general request error: {e}"})
    except Exception as err:
        logger_error.error(f"Other error occurred: {err}")
        return JsonResponse({'success': False, 'error': str(err)})
