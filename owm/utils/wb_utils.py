import requests
import logging

import datetime

from owm.models import Seller
from owm.utils.db_utils import db_check_awaiting_postingnumber, db_get_status
from owm.utils.ms_utils import ms_get_product
import json

logger_info = logging.getLogger('crm3_info')
logger_error = logging.getLogger('crm3_error')

def wb_update_inventory(headers, stock):
    """
    Обновляет инвентарь на Wildberries.

    Args:
        headers: Словарь с заголовками авторизации.
        stock: Словарь с данными о товарах (vendorCode: {'stock': кол-во, 'sku': sku}).

    Returns:
        Словарь с результатом: {'code': код ответа, 'json': ответ API (JSON или сообщение об успехе/ошибке)}.
        Возвращает ошибку, если произошла ошибка.
    """
    try:
        url_cards = 'https://content-api.wildberries.ru/content/v2/get/cards/list'
        url_warehouses = 'https://marketplace-api.wildberries.ru/api/v3/warehouses'
        url_stock = 'https://marketplace-api.wildberries.ru/api/v3/stocks/{warehouseId}'

        data_cards = {
            'settings': {
                'cursor': {'limit': 100, 'nmID': None, 'updatedAt': None},
                'filter': {'withPhoto': -1}
            }
        }
        warehouse_id = None

        while True:  # Внешний цикл обработки страниц
            try:
                response = requests.post(url_cards, json=data_cards, headers=headers['wb_headers'])
                response.raise_for_status()  # Проверка статуса ответа
                response_json = response.json()

                # Обработка результата
                for item in response_json['cards']:
                    if item['vendorCode'] in stock:
                        stock[item['vendorCode']]['sku'] = item['sizes'][0]['skus'][0]

                        # Обновление данных для следующей страницы
                if 'cursor' in response_json and response_json['cursor']:
                    data_cards['settings']['cursor']['nmID'] = response_json['cursor']['nmID']
                    data_cards['settings']['cursor']['updatedAt'] = response_json['cursor']['updatedAt']
                else:
                    break  # Выход из цикла, если нет следующей страницы

            except requests.exceptions.RequestException as e:
                logging.error(f"Ошибка при запросе к API: {e}")
                return {'code': 500, 'json': f"Ошибка при запросе к API: {e}"}
            except (KeyError, IndexError) as e:
                logging.error(f"Ошибка при обработке ответа: {e}, данные:{response_json}")
                return {'code': 500, 'json': f"Ошибка при обработке ответа: {e}, данные:{response_json}"}

            if response_json.get('cursor', {}).get('total', 0) < 100:
                break  # Выходим из цикла, если total < 100

        # Получение ID склада
        try:
            warehouse_response = requests.get(url_warehouses, headers=headers['wb_headers'])
            warehouse_response.raise_for_status()
            warehouse_data = warehouse_response.json()
            warehouse_id = warehouse_data[0]['id']  # Используем первый элемент списка.
        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка при получении ID склада: {e}")
            return {'code': 500, 'json': f"Ошибка при получении ID склада: {e}"}


        sku_data = []
        for vendor_code, value in stock.items():
            if 'sku' in value and value.get('stock') is not None:  # Проверяем на None
                try:
                    stock_amount = int(value['stock'])
                    if 0 <= stock_amount <= 100000:  # Проверка на допустимые значения
                        sku_data.append({'sku': value['sku'], 'amount': stock_amount})
                    else:
                        logging.warning(f"Пропущен vendorCode {vendor_code} из-за некорректного значения остатка: {value['stock']}")
                except ValueError as e:
                    logging.error(f"Ошибка при преобразовании остатка {value['stock']} для vendorCode {vendor_code}: {e}")

        if not sku_data:  # Проверка на пустой список
          logging.warning("Список sku_data пуст. Обновление не выполнено.")
          return {"code": 400, "json": "Список sku_data пуст. Обновление не выполнено."}

        # Отправка данных на обновление
        #print(f"*" * 100)
        sttt = {'stocks': sku_data}
        #print(f"sttt {sttt}")
        #print(f"*" * 100)
        try:
            put_response = requests.put(url_stock.format(warehouseId=warehouse_id), json={'stocks': sku_data}, headers=headers['wb_headers'])
            #print(f'put_response {put_response.text}')
            put_response.raise_for_status()  # проверка
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 409:
                #  В зависимости от вашей логики: повторите запрос через некоторое время, отложите его на потом.
                return {'code': 409, 'json': f"Конфликт при обновлении: {e} {put_response.text}"}
            else:
                logging.error(f"Ошибка при обновлении инвентаря: {e}")
                raise  # Перебросьте исключение вверх
        result = {'code': put_response.status_code, 'json': put_response.json() if put_response.status_code != 204 else 'Обновление прошло успешно'}
        return result
    except Exception as e:
        logging.exception("Непредвиденная ошибка:")
        return {'code': 500, 'json': f"Непредвиденная ошибка: {e}"}

def wb_get_status_fbs(headers: dict, seller: Seller):
    '''
    получаем последние отгрузки FBS (отправления)
    '''
    result = {}

    orders_db = db_get_status(seller=seller, market='wb')
    # Получаем список заказов для 'wb'
    orders_list = orders_db.get('wb', [])
    existing_orders = {order['posting_number']: order['status'] for order in orders_list}

    current_date = datetime.datetime.now()

    # Вычисляем дату неделю назад
    one_week_ago = current_date - datetime.timedelta(weeks=4)

    # Форматируем даты в строковый формат (YYYY-MM-DD)
    current_date_str = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    one_week_ago_str = one_week_ago.strftime('%Y-%m-%dT%H:%M:%SZ')

    wb_headers = headers.get('wb_headers')
    # оприходование
    url_all_orders = 'https://marketplace-api.wildberries.ru/api/v3/orders'
    params_all_orders = {
        "limit": 1000,
        "next": 0,
        }


    try:
        all_orders = {}
        response = requests.get(url_all_orders, headers=wb_headers, params=params_all_orders)
        if response.status_code == 200:
            all_orders = response.json() # тут статусов нет
            #print(f'Z' * 40)
            #print(f'Z' * 40)
            #print(f"response_json all_orders: {all_orders}")            
            #print(f'Z' * 40)
            #print(f'Z' * 40)
        else:
            #print(f'#' * 40)
            #print(f'#' * 40)
            logger_error.error(f"[wb_get_status_fbs]: ошибка response ответа - {response.text}")
            #print(f"response_json response.text: {response.text}")
            result['error'] = response.text
            return result
    except Exception as e:
        result['error'] = f"Error in awaiting request: {e}"

    db_wb_status = db_get_status(seller=seller, market='wb', exclude_status='sold')

    db_ids = [order["posting_number"] for order in db_wb_status.get("wb", [])]

    all_status = {
        "orders": [order["id"] for order in all_orders["orders"]]
    }
    
    
    if "orders" in all_orders and isinstance(all_orders["orders"], list):
        all_status = {
            "orders": [order["id"] for order in all_orders["orders"]]
        }
    else:
        logger_error.error(f"[wb_get_status_fbs]: ключ 'orders' отсутствует в ответе: {all_orders}")
        result['error'] = f"Ключ 'orders' отсутствует в ответе: {all_orders}"
        return result
    
    new_ids = [int(oid) for oid in db_ids if int(oid) not in all_status["orders"]]

    all_status["orders"] = (new_ids + all_status["orders"])[:1000]

    #print(f'Z' * 40)
    #print(f"db_wb_status: {db_ids}")
    #print(f'Z' * 40)

    url_status_awaiting = 'https://marketplace-api.wildberries.ru/api/v3/orders/status'



    try:
        response = requests.post(url_status_awaiting, headers=wb_headers, json=all_status)
        #print(f'STATUS')    
        if response.status_code == 200:
            status = response.json()
            #print(f"response_json status: {status}")            
        else:
            #print(f"response_json status: {response.text}")
            result['error'] = response.text
            return result
    except Exception as e:
        #print(f"response_json status: {response.text}")
        result['error'] = f"Error in packag request: {e}"
        return result

    #waiting_ids = [order['id'] for order in status['orders'] if order['wbStatus'] == 'waiting']
    #sorted_ids = [order['id'] for order in status['orders'] if order['wbStatus'] == 'sorted'] #delivering?

    #filtered_orders['waiting'] = {"orders": [order for order in all_orders['orders'] if order['id'] in waiting_ids]}
    #filtered_orders['sorted'] = {"orders": [order for order in all_orders['orders'] if order['id'] in sorted_ids]}

    #print(f"awaiting {waiting_ids}")
    #print(f'%' * 40)




    status_map = {order['id']: order['wbStatus'] for order in status['orders']}
    #print(f'%' * 40)
    #print(f"status_map {status_map}")
    #print(f"awaiting {waiting_ids}")
    #print(f'%' * 40)

    filtered_status_map = {"waiting": [], "sorted": [], "sold": [], "canceled": []}

    status_list = ("waiting", "sorted", "sold", "canceled")

    # Маппинг исходных статусов к финальным ключам
    status_aliases = {
        "canceled": "canceled",
        "canceled_by_client": "canceled",
        "declined_by_client": "canceled",
        "waiting": "waiting",
        "sorted": "sorted",
        "ready_for_pickup": "sorted",
        "sold": "sold",
    }

    #print(f'1' * 40)
    #print(f"status_map {status_map}")
    #print(f'1' * 40)

    for order in all_orders['orders']:
        wb_status = status_map.get(order['id'])
        mapped_status = status_aliases.get(wb_status)
        if mapped_status in filtered_status_map:
            filtered_status_map[mapped_status].append(order)


    filtered_result = {"waiting": [], "sorted": [], "sold": [], "canceled": []}
    #print(f'%' * 40)
    #print(f"existing_orders {existing_orders}")
    #print(f'%' * 40)
    
    for current_status in status_list:
        for order in filtered_status_map[current_status]:
            posting_number = str(order["id"])
            #print(f'{posting_number} - {current_status}')
            if posting_number in existing_orders:
                #print(f'{posting_number}')
                existing_status = existing_orders[posting_number]
                if existing_status != current_status:
                    product_list = [{
                        "offer_id": order["article"],
                        "price": int(order["convertedPrice"]) / 100,
                        "quantity": 1
                    }]
                    #print(f'{order["price"]}')
                    #print(f'{product_list}\n\n')
                    filtered_result[current_status].append({
                        "posting_number": str(order["id"]),
                        "status": current_status,
                        "product_list": product_list
                    })
            else:
                if current_status == 'waiting':
                    product_list = [{
                        "offer_id": order["article"],
                        "price": int(order["convertedPrice"]) / 100,
                        "quantity": 1
                    }]
                    #print(f'{order["price"]}')
                    #print(f'{product_list}\n\n')
                    filtered_result[current_status].append({
                        "posting_number": order["id"],
                        "status": current_status,
                        "product_list": product_list
                    })
    #print(f'Z' * 40)
    #print(f"filtered_result - {filtered_result}")
    #print(f"awaiting {waiting_ids}")
    #print(f'Z' * 40)

    posting_numbers = [
        item['id']
        for status in status_list
        for item in filtered_status_map[status]
    ]
    #print(f'%' * 40)
    #print(f"filtered_status_map {filtered_status_map['waiting']}")
    #print(f'%' * 40)

    result = {}

    #print(f'%' * 40)
    #print(f"posting_numbers {posting_numbers}")
    #print(f'%' * 40)

    result = db_check_awaiting_postingnumber(posting_numbers) # key: found, not_found
    #print(f'%' * 40)
    #print(f"check_result_dict {result}")
    #print(f'%' * 40)
    result['filter_product'] = filtered_result
    return result

def wb_get_products(headers):
    url_list = "https://content-api.wildberries.ru/content/v2/get/cards/list"

    data_cards = {
        'settings': {
            'cursor': {'limit': 100, 'nmID': None, 'updatedAt': None},
            'filter': {'withPhoto': -1}
        }
    }

    all_item = []
    while True:  # Внешний цикл обработки страниц
        try:
            response = requests.post(url_list, json=data_cards, headers=headers['wb_headers'])
            response.raise_for_status()  # Проверка статуса ответа
            response_json = response.json()

            # Обработка результата
            all_item.extend(response_json['cards'])

            # Обновление данных для следующей страницы
            if 'cursor' in response_json and response_json['cursor']:
                data_cards['settings']['cursor']['nmID'] = response_json['cursor']['nmID']
                data_cards['settings']['cursor']['updatedAt'] = response_json['cursor']['updatedAt']
            else:
                break  # Выход из цикла, если нет следующей страницы

        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка при запросе к API: {e}")
            return {'code': 500, 'json': f"Ошибка при запросе к API: {e}"}
        except (KeyError, IndexError) as e:
            logging.error(f"Ошибка при обработке ответа: {e}, данные:{response_json}")
            return {'code': 500, 'json': f"Ошибка при обработке ответа: {e}, данные:{response_json}"}

        if response_json.get('cursor', {}).get('total', 0) < 100:
            break  # Выходим из цикла, если total < 100
    return all_item

def wb_get_finance_report(headers: dict, period: str):
    opt_price = ms_get_product()
    opt_price_clear = {}
    for item in opt_price['rows']:
        #opt_price_clear['article'] = item['article']
        #print(f"opt_price {item['buyPrice']['value']/100}")
        opt_price_clear[item['article']] = {
            'opt_price' : int(float(item['buyPrice']['value']) / 100),
            }

    response = wb_get_finance_responce(headers=headers['wb_headers'])

    count_dicts = len(response)
    print(f"Количество словарей: {count_dicts}")

    print(f'response {response}')



    translated_keys = {
        'date_from': 'Дата начала',
        'date_to': 'Дата окончания',
        #'rrd_id': 'ID записи отчета',
        #'gi_id': 'ID товарной позиции',
        'dlv_prc': 'Процент доставки',
        #'fix_tariff_date_from': 'Начало действия фиксированного тарифа',
        #'fix_tariff_date_to': 'Окончание действия фиксированного тарифа',
        'subject_name': 'Наименование товара',
        'nm_id': 'Код товара',
        #'brand_name': 'Бренд',
        'sa_name': 'Краткое имя SA',
        'ts_name': 'Имя TS',
        'barcode': 'Штрихкод',
        'doc_type_name': 'Тип документа',
        'quantity': 'Количество',
        'retail_price': 'Розничная цена',
        'retail_amount': 'Розничная сумма',
        'sale_percent': 'Процент продаж',
        'commission_percent': 'Процент комиссии',
        'supplier_oper_name': 'Операция поставщика',
        #'order_dt': 'Дата заказа',
        #'sale_dt': 'Дата продажи',
        #'rr_dt': 'Дата отчета',
        'shk_id': 'ID SHK',
        'retail_price_withdisc_rub': 'Цена с учетом скидки, RUB',
        'delivery_amount': 'Сумма доставки',
        'return_amount': 'Сумма возврата',
        'delivery_rub': 'Стоимость доставки, RUB',
        #'gi_box_type_name': 'Тип упаковки',
        'product_discount_for_report': 'Скидка на товар для отчета',
        'rid': 'RID',
        'ppvz_spp_prc': 'PPVZ SPP PRC',
        'ppvz_kvw_prc_base': 'Основа PPVZ KVW PRC',
        'ppvz_kvw_prc': 'PPVZ KVW PRC',
        #'sup_rating_prc_up': 'Повышение рейтинга поставщика',
        'is_kgvp_v2': 'Is KGVP V2',
        'ppvz_sales_commission': 'Комиссия WB',
        'ppvz_for_pay': 'К выплате',
        'ppvz_reward': 'Комиссия ПВЗ',
        'acquiring_fee': 'Комиссия за эквайринг',
        'acquiring_percent': 'Процент эквайринга',
        'payment_processing': 'Обработка платежей',
        'acquiring_bank': 'Банк эквайринга',
        'ppvz_vw': 'Вознаграждение WB',
        'ppvz_vw_nds': 'PPVZ VW НДС',
        'declaration_number': 'Номер декларации',
        'bonus_type_name': 'Тип бонуса',
        'sticker_id': 'ID стикера',
        'site_country': 'Страна сайта',
        'srv_dbs': 'SRV DBS',
        'penalty': 'Штраф',
        'additional_payment': 'Дополнительная оплата',
        'rebill_logistic_cost': 'Стоимость перевозки при пересчете',
        'storage_fee': 'Плата за хранение',
        'deduction': 'Вычет',
        'acceptance': 'Принятие',
        'assembly_id': 'ID сборки',
        'srid': 'SRID',
    }

    filtered_response = [{key: item.get(key, None) for key in translated_keys.keys()} for item in response]

    df = pd.DataFrame(filtered_response)

    result = {}
    result['path'] = {}

    uuid_suffix = str(uuid.uuid4())[:6]
    prefix = 'stock_wb'
    path = os.path.join(settings.MEDIA_ROOT, 'owm/report/')
    url_path = os.path.join(settings.MEDIA_URL, 'owm/report/', f'stock_wb_all_{uuid_suffix}.xlsx')
    root_path = os.path.join(settings.MEDIA_ROOT, 'owm/report/', f'stock_wb_all_{uuid_suffix}.xlsx')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    delete_files_with_prefix(path, prefix)
    #df.rename(columns=translated_keys, inplace=True)
    df.to_excel(root_path, index=False)

    result['path']['all'] = os.path.join(settings.MEDIA_URL, 'owm/report/', f'{url_path}')


    category_translation = {
        'Логистика': 'logistic',
        'Продажа': 'sale',
        'Возмещение': 'reimbursement',
        'Хранение': 'storage',
        'приемка': 'acceptance',
        'Возврат': 'return'
    }

    category_dfs = {
        category: df[df['supplier_oper_name'].str.contains(category, na=False)]
        for category in category_translation.keys()
    }

    summed_totals = {}
    offer_id_result = {}
    for index, row in category_dfs['Продажа'].iterrows():
        offer_id = row['sa_name']
        opt = opt_price_clear[offer_id]['opt_price']
        new_entry = {
            'name': row['subject_name'],
            'for_pay': int(row['ppvz_for_pay'],), # к выплате
            'quantity': int(row['quantity'],),  # Сумма продаж (возвратов)
            'opt': int(opt)
            }
        net_profit = new_entry['for_pay'] - (opt * new_entry['quantity']) #чистая без опта
        net_profit_perc = (net_profit / (opt * new_entry['quantity'])) * 100 if opt * new_entry['quantity'] != 0 else 0
        posttax_profit = net_profit - (new_entry['for_pay'] * 0.06)
        posttax_profit_perc = (posttax_profit / (opt * new_entry['quantity'])) * 100 if opt * new_entry['quantity'] != 0 else 0
        new_entry.update({
            'net_profit': net_profit,
            'net_profit_perc': int(net_profit_perc),
            'posttax_profit': posttax_profit,
            'posttax_profit_perc': int(posttax_profit_perc),
        })
        if offer_id in offer_id_result:
            offer_id_result[offer_id].append(new_entry)
        else:
            offer_id_result[offer_id] = [new_entry]

    # print(f'result ozon price {result}')
    # seller_price_per_instance Цена продавца с учётом скидки.
    # 'item': {'offer_id': 'cer_black_20', 'barcode': 'OZN1249002486', 'sku': 1249002486},
    sorted_report = dict(sorted(offer_id_result.items(), key=lambda item: (item[0][:3], item[0][3:])))

    # Итерация по результатам и вычисление суммы total_price
    for offer_id, entries in offer_id_result.items():
        for_pay_sum = sum(entry['for_pay'] for entry in entries)
        net_profit_sum = sum(entry['net_profit'] for entry in entries)
        posttax_profit_sum = sum(entry['posttax_profit'] for entry in entries)
        total_quantity = sum(entry['quantity'] for entry in entries)

        # Расчет средней цены продажи
        average_sales_price = for_pay_sum / total_quantity if total_quantity > 0 else 0

        average_percent_posttax = sum(entry['posttax_profit_perc'] for entry in entries) / len(entries) if entries else 0

        # Сохраняем результаты в словарь
        summed_totals[offer_id] = {
            "for_pay_sum": int(for_pay_sum),
            "net_profit_sum": int(net_profit_sum),
            "posttax_profit_sum": int(posttax_profit_sum),
            "average_sales_price": int(average_sales_price),
            "average_percent_posttax": int(average_percent_posttax),
            "total_quantity": int(total_quantity),
        }

    #print(f'summed_totals {summed_totals}')
    all_for_pay_sum = sum(value["for_pay_sum"] for value in summed_totals.values())
    all_return_total = 0
    all_return_total = int(category_dfs['Возврат']["retail_amount"].sum())
    all_totals = {
        "all_for_pay_sum": all_for_pay_sum,
        "all_net_profit_sum": sum(value["net_profit_sum"] for value in summed_totals.values()),
        "all_posttax_profit_sum": sum(value["posttax_profit_sum"] for value in summed_totals.values()),
        "all_quantity": sum(value["total_quantity"] for value in summed_totals.values()),
        "all_return_total": all_return_total
    }
    all_totals = {
        key: f"{value:,}" if isinstance(value, (int, float)) else value
        for key, value in all_totals.items()
    }

    for category, english_name in category_translation.items():
        # Создаём путь для каждого файла
        category_path = os.path.join(settings.MEDIA_ROOT,'owm/report/',f'stock_wb_{english_name}_{uuid_suffix}.xlsx')
        # Сохраняем DataFrame в Excel
        result['path'][f'{english_name}'] = os.path.join(settings.MEDIA_URL, 'owm/report/', f'stock_wb_{english_name}_{uuid_suffix}.xlsx')
        if category in category_dfs:
            category_dfs[category].to_excel(category_path, index=False)
            print(f"Файл для категории '{category}' сохранён как {result['path'][f'{english_name}']}")
        else:
            print(f"Категория '{category}' отсутствует в данных.")


    result['translated_keys'] = translated_keys
    result['date'] = date
    if isinstance(response, list):
        for item in response:
            if isinstance(item, dict) and item.get('code') == 8:
                result['code'] = 8
                break
        else:
            result['code'] = 0
    # Выводим отсортированный словарь
    result['sorted_report'] = sorted_report
    result['all_totals'] = all_totals
    result['summed_totals'] = summed_totals
    return result

def wb_get_realized(headers: dict, ms_product: dict) -> dict:
    response = wb_get_realized_responce(headers=headers)
    
    realization = wb_create_realized_data(response=response, opt_price=ms_product['opt_price'])
    return realization


# 1 - data["sale"] - содержит вы выплаты wb
# 2 - 
def wb_create_realized_data(response: list, opt_price: dict) -> dict:

        #print(f"responce {response}")

        realization = {}
        price_accumulator = {}
        price_groups = {}

        # Собираем все реализации в одну продажу, чтобы понять стоимость продажи, чистую итд
        srid_map = {}

        # Support both dict and list response types

        for item in response:
            srid = item.get('srid')
            if not srid:
                continue
            offerid = item.get('sa_name')
            supplier_oper_name = item.get('supplier_oper_name')

            if srid not in srid_map:
                srid_map[srid] = {
                    "offerid": None,
                    "sale_raw": None,
                    "delivery": 0,
                    "reimbursement": 0,
                    "storage": 0,
                    "acceptance": 0,
                    "return": 0,
                    "for_pay": 0
                }
            # Устанавливаем offerid только если он не пустой и еще не установлен
            if offerid and srid_map[srid]["offerid"] is None:
                srid_map[srid]["offerid"] = offerid

            # Продажа
            if supplier_oper_name == "Продажа":
                srid_map[srid]["sale_raw"] = item                

            # Логистика
            elif supplier_oper_name == "Логистика":
                srid_map[srid]["delivery"] += float(item.get("delivery_rub", 0) or 0)

            # Возмещение издержек по перевозке/по складским операциям с товаром
            elif supplier_oper_name == "Возмещение издержек по перевозке/по складским операциям с товаром":
                srid_map[srid]["reimbursement"] += float(item.get("rebill_logistic_cost", 0) or 0)

            # Хранение
            elif supplier_oper_name == "Хранение":
                srid_map[srid]["storage"] += float(item.get("storage_fee", 0) or 0)

            # Платная приемка
            elif supplier_oper_name == "Платная приемка":
                srid_map[srid]["acceptance"] += float(item.get("acceptance", 0) or 0)

            # Возмещение за выдачу и возврат товаров на ПВЗ
            elif supplier_oper_name == "Возмещение за выдачу и возврат товаров на ПВЗ":
                srid_map[srid]["return"] += float(item.get("ppvz_reward", 0) or 0)

            # После сбора всех данных, вычисляем for_pay если есть продажа
            # ВАЖНО: вычислять for_pay нужно только после обработки всех строк (после основного цикла for)
            # Поэтому здесь вычислять НЕ нужно, а только после цикла пройтись по srid_map и вычислить for_pay для тех, где есть sale

        # После основного цикла: вычисляем for_pay для всех srid, где есть sale
        for srid, data in srid_map.items():
            if data["sale_raw"]:
                sale_item = data["sale_raw"]
                ppvz_for_pay = float(sale_item.get("ppvz_for_pay", 0) or 0)
                delivery = data["delivery"]
                reimbursement = data["reimbursement"]
                storage = data["storage"]
                acceptance = data["acceptance"]
                return_val = data["return"]
                data["for_pay"] = (
                    ppvz_for_pay - delivery - reimbursement - storage - acceptance - return_val
                )

        # Подсчет элементов с offerid == None и sale_raw == None
        # Создаем словарь для srid с offerid == None и sale_raw == None
        srid_unknow = {
            "unknow_data": {},
            "all_count": {}
        }
        srid_to_remove = []
        for srid, data in srid_map.items():
            if data["offerid"] is None and data["sale_raw"] is None:
                srid_unknow["unknow_data"][srid] = data
            # Суммируем все значения по ключам
                for key, value in data.items():
                    if isinstance(value, (int, float)):
                        srid_unknow["all_count"][key] = srid_unknow["all_count"].get(key, 0) + value
                srid_to_remove.append(srid)
        # Удаляем такие srid из srid_map
        for srid in srid_to_remove:
            srid_map.pop(srid, None)


        # Выведем первые 2 элементов srid_map как json
        #print(json.dumps(dict(list(srid_map.items())[:2]), ensure_ascii=False, indent=2))
        
        # commission_percent Размер кВВ, %
        # ppvz_reward Возмещение за выдачу и возврат товаров на ПВЗ
        # acquiring_percent Размер комиссии за эквайринг/Комиссии за организацию платежей, %
        # Склад поставщика in office_name
        
        # Группируем продажи по offerid
        # Группируем продажи по offerid и по типу склада (mystore/wbstore)
        offerid_sales = {}
        for srid, data in srid_map.items():
            offerid = data.get("offerid")
            if not offerid:
                continue
            office_name = data["sale_raw"].get("office_name") if data.get("sale_raw") else ""
            store_type = "mystore" if "Склад поставщика" in (office_name or "") else "wbstore"

            if offerid not in offerid_sales:
                offerid_sales[offerid] = {
                    "mystore": {
                    "delivery": [],
                    "reimbursement": [],
                    "storage": [],
                    "acceptance": [],
                    "return": [],
                    "commission_percent": [],
                    "sales": [],
                    "sale_qty": 0  # счетчик количества продаж
                    },
                    "wbstore": {
                    "delivery": [],
                    "reimbursement": [],
                    "storage": [],
                    "acceptance": [],
                    "return": [],
                    "commission_percent": [],
                    "sales": [],
                    "sale_qty": 0  # счетчик количества продаж
                    },
                }

            store = offerid_sales[offerid][store_type]
            # Добавляем саму продажу (товар) в список sales
            if data.get("sale_raw"):
                store["sales"].append({
                    **data["sale_raw"],
                    "details": {
                    "delivery": data.get("delivery", 0),
                    "reimbursement": data.get("reimbursement", 0),
                    "storage": data.get("storage", 0),
                    "acceptance": data.get("acceptance", 0),
                    "return": data.get("return", 0),
                    "for_pay": data.get("for_pay", 0),
                    }
                })
            # Увеличиваем счетчик количества продаж
            try:
                qty = int(data["sale_raw"].get("quantity", 1))
            except Exception:
                qty = 1
            store["sale_qty"] += qty

            for key in ("delivery", "reimbursement", "storage", "acceptance", "return"):
                store[key].append(data.get(key, 0) or 0)
                if data.get("sale_raw"):
                    commission_percent = data["sale_raw"].get("commission_percent")
                    if commission_percent is not None:
                        try:
                            store["commission_percent"].append(float(commission_percent))
                        except Exception:
                            pass
                        
        # Усредняем значения по каждому offerid и типу склада, округляя до двух знаков после запятой
        for offerid in offerid_sales:
            for store_type in ("mystore", "wbstore"):
                store = offerid_sales[offerid][store_type]
                for key in ("delivery", "reimbursement", "storage", "acceptance", "return", "commission_percent"):
                    values = store[key]
                    store[key] = round(sum(values) / len(values), 2) if values else 0
            # sales оставляем списком товаров

        # Теперь offerid_sales = {'offerid1': {srid1: {...}, srid2: {...}}, 'offerid2': {...}, ...}
        # Если нужно значения в list:
        # offerid_sales_list = {offerid: list(sales.values()) for offerid, sales in offerid_sales.items()}
        


        
        print(json.dumps(dict(list(offerid_sales.items())[:3]), ensure_ascii=False, indent=2))
        exit()
        
        
        # тут 
        for item in response.get('result', {}).get('rows', []):
            offer_id = item['item'].get('sa_name')
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
                      
    
# https://dev.wildberries.ru/openapi/financial-reports-and-accounting#tag/Finansovye-otchyoty/paths/~1api~1v5~1supplier~1reportDetailByPeriod/get    
def wb_get_realized_responce(headers: dict):    
    url = "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"
    now = datetime.datetime.now()
    # Вычисляем первый день предыдущего месяца
    first_day_of_last_month = datetime.datetime(now.year, now.month, 1) - datetime.timedelta(days=1)
    first_day_of_last_month = first_day_of_last_month.replace(day=1)
    # Вычисляем последний день предыдущего месяца
    last_day_of_last_month = first_day_of_last_month.replace(day=1) + datetime.timedelta(days=32)
    last_day_of_last_month = last_day_of_last_month.replace(day=1) - datetime.timedelta(days=1)

    date = {
        "dateFrom": first_day_of_last_month.strftime('%Y-%m-%d'),
        "dateTo": last_day_of_last_month.strftime('%Y-%m-%d'),
        "limit": 100000
    }
    try:
        response_raw = requests.get(url, headers=headers['wb_headers'], params=date, timeout=10)
        try:
            response = response_raw.json()
        except Exception as e:
            logger_error.error(f"Ошибка декодирования JSON: {str(e)}, response: {response_raw.text}")
            return {
                "error": f"Ошибка декодирования JSON: {str(e)}",
                "response": response_raw.text
            }
    except requests.exceptions.Timeout:
        logger_error.warning("Wildberries API timeout")
        return {"error": "Wildberries API timeout"}
    except requests.exceptions.RequestException as e:
        logger_error.warning(f"Wildberries API general request error: {e}")
        return {"error": f"Wildberries API general request error: {e}"}

    return response
