import fnmatch
import time
import requests
import datetime
import uuid
import xlsxwriter

import os
from django.conf import settings

import pandas as pd

from owm.utils.db_utils import db_get_metadata, db_create_customerorder, db_get_awaiting
from owm.utils.ms_utils import ms_create_customerorder, ms_get_organization_meta, ms_get_agent_meta, ms_update_allstock_to_mp, ms_delivering_order, \
    ms_cancel_order
from owm.models import Crontab
from owm.utils.oz_utils import ozon_get_awaiting_fbs, ozon_get_status_fbs
from owm.utils.wb_utils import wb_get_awaiting_fbs
from owm.utils.ya_utils import yandex_get_awaiting_fbs

import logging
logger_info = logging.getLogger('crm3_info')
logger_error = logging.getLogger('crm3_error')

def get_headers(seller):
    headers = {}
    print(f'seller {seller}')
    moysklad_api = seller.moysklad_api
    yandex_api = seller.yandex_api
    wildberries_api = seller.wildberries_api
    ozon_api = seller.ozon_api
    ozon_id = seller.client_id

    if moysklad_api:
        headers['moysklad_headers'] = {
            "Authorization": f"Bearer {moysklad_api}"
        }

    # Yandex API
    if yandex_api:
        headers['yandex_headers'] = {
            "Api-Key": yandex_api,
            "Content-Type": "application/json"
        }

        # Получение кампаний через Yandex API
        url = 'https://api.partner.market.yandex.ru/campaigns'
        response = requests.get(url, headers=headers['yandex_headers'])
        if response.status_code == 200:
            campaigns_data = response.json()
            campaigns = campaigns_data.get('campaigns', [])
            if not campaigns:
                raise Exception("No campaigns found in response.")

            # Извлечение company_id и businessId
            headers['yandex_id'] = {
                'company_id': campaigns[0]['id'],
                'businessId': campaigns[0]['business']['id']
            }
        else:
            error_message = response.text
            raise Exception(f"Error {response.status_code}: {error_message}")

        # Получение складов через Yandex API
        warehouse_url = f"https://api.partner.market.yandex.ru/businesses/{headers['yandex_id']['businessId']}/warehouses"
        response = requests.get(warehouse_url, headers=headers['yandex_headers'])
        if response.status_code == 200:
            warehouses_data = response.json()
            warehouses = warehouses_data.get('result', {}).get('warehouses', [])
            if not warehouses:
                raise Exception("No warehouses found in response.")
            headers['yandex_id']['warehouseId'] = warehouses[0]['id']
        else:
            error_message = response.text
            raise Exception(f"Error {response.status_code}: {error_message}")

    # Ozon API
    if ozon_api and ozon_id:
        headers['ozon_headers'] = {
            'Client-Id': ozon_id,
            'Api-Key': ozon_api
        }

    # Wildberries API
    if wildberries_api:
        headers['wb_headers'] = {
            'Authorization': wildberries_api
        }
    time.sleep(1)
    return headers



def get_store_meta(headers):
    url = 'https://api.moysklad.ru/api/remap/1.2/entity/store'
    response = requests.get(url, headers=headers).json()
    return response['rows'][0]['meta']


def sort_stock_and_invent(invent_dict, stock):
    #print(f"invent_dict {invent_dict}")
    #print(f"stock {stock}")
    loss_dict = {}
    enter_dict = {}
    print(f'invent_dict TYT {invent_dict}')
    print(f'sort_stock_and_invent stock {stock}')
    for key, value in stock.items():
        #if key in invent_dict:
            #print(f"float(value['stock'] {float(value['stock'])} > {float(invent_dict[key]['stock'])}")
        #print(f"key {key};   value {value};   invent dict {invent_dict[key]}")
        if key in invent_dict:
            if float(value['stock']) > float(invent_dict[key]['stock']):
                loss_dict[key] = {}
                loss_dict[key]['stock'] = float(value['stock'])-float(invent_dict[key]['stock'])
            if float(value['stock']) < float(invent_dict[key]['stock']):
                enter_dict[key] = {}
                enter_dict[key]['stock'] = float(invent_dict[key]['stock'])-float(value['stock'])
            if float(value['stock']) == float(invent_dict[key]['stock']):
                print('значения равны, обновления нет смысла делать')
    result  = {
        'enter_dict': enter_dict,
        'loss_dict': loss_dict
        }
    return result

# инветаризируем (оприходуем и списываем) товары на мойсклад и обновляем остатки на маркетплейсах
def inventory_update(user: object, invent_dict: dict):
    context = {}
    parser_data = {
        'moysklad_api': user.moysklad_api,
        'yandex_api': user.yandex_api,
        'wildberries_api': user.wildberries_api,
        'ozon_api': user.ozon_api,
    }
    headers = get_headers(parser_data)
    stock = get_all_moysklad_stock(headers['moysklad_headers'])
    stock_dict = sort_stock_and_invent(invent_dict, stock)
    #print(f'enter_dict, loss_dict {enter_dict} and {loss_dict}')
    response = update_inventory_moysklad(headers['moysklad_headers'], stock_dict)
    # если мойсклад обновил, то делаем на озоне синхронизацию
    context['moysklad'] = {
        'code': response.status_code,
        'json': response.json()
    }


    if response.status_code == 200:
        stock = get_all_moysklad_stock(headers['moysklad_headers']) # вызываем снова, так как остатки изменились

        # Оставляем только пересечение ключей
        common_keys = invent_dict.keys() & stock.keys()
        stock = {key: stock[key] for key in common_keys}
        #print(f'\n\nstock сравнение {stock}\n\n')
        context['ozon'] = update_inventory_ozon(headers, stock)
        #print(f'OZON UPDATE?')
        context['yandex'] = update_inventory_yandex(headers, stock)
        context['wb'] = update_inventory_wb(headers['wb_headers'], stock)
    return context



# инвентаризация товара мой склад
# MS MS MSM SM MSMSMSMSMSMS



# оприходование и списание на основе двух словарей
def update_inventory_moysklad(headers, stock_dict):
    enter_dict = stock_dict['enter_dict']
    loss_dict = stock_dict['loss_dict']
    uuid_suffix = str(uuid.uuid4())[:8]

    # оприходование
    url = 'https://api.moysklad.ru/api/remap/1.2/entity/enter'
    data = {
        'name': f'owm-{uuid_suffix}',
        'store': {"meta": get_store_meta(headers)},
        'organization': {'meta': get_organization_meta(headers)},
        'positions': get_inventory_row_data(headers, enter_dict)
    }
    responce = requests.post(url=url, json=data, headers=headers)
    #print(f"responce moysklad {responce.json()}")

    # списание
    url = 'https://api.moysklad.ru/api/remap/1.2/entity/loss'
    data = {
        'store': {"meta": get_store_meta(headers)},
        'organization': {'meta': get_organization_meta(headers)},
        'positions': get_inventory_row_data(headers, loss_dict)
    }
    responce = requests.post(url=url, json=data, headers=headers)
    #print(f"responce status {type(responce.status_code)}")
    return responce

def get_inventory_row_data(headers, offer_dict):
    # url = f'https://api.moysklad.ru/api/remap/1.2/entity/assortment?filter=article={article}'
    url = f'https://api.moysklad.ru/api/remap/1.2/entity/assortment'
    response = requests.get(url, headers=headers).json()
    #print(f'get_prod_meta {response[']}')
    #print(f'get_inventory_row_data offer_dict {offer_dict}')
    data = []
    for row in response['rows']:
        #print(f'TYT')
        if row['article'] in offer_dict:
            #print(f'TYT1')
            data.append({
                "quantity": float(offer_dict[row['article']]['stock']),
                "assortment": {
                    "meta": row['meta']
                },
            }, )
        # print(f"{row['article']}")
    #print(f'get_inventory_row_data data {data}')
    # meta = response['rows'][0]['meta']
    # data = [
    #     {
    #         "quantity": count,
    #         "price": price * 100,
    #         "assortment": {
    #             "meta": meta
    #         },
    #         "overhead": 0
    #     },
    # ]
    return data

    # url = 'https://api.moysklad.ru/api/remap/1.2/entity/inventory'
    # data = {
    #     'store': {"meta": get_store_meta(headers)},
    #     'organization': {'meta': get_organization_meta(headers)},
    #     'positions': get_inventory_row_data(headers, offer_dict)
    # }
    # responce = requests.post(url=url, json=data, headers=headers)
    #print(f"responce moysklad {responce.json()}")

# создание dict из POST запроса для инвенаризации (inventory)
def inventory_POST_to_offer_dict(post_data):
    offer_dict = {}

    # Обрабатываем все данные из словаря post_data
    for key, value in post_data.items():
        if key.endswith("_checked"):  # Проверяем только чекбоксы
            offer_id = key.replace("_checked", "")  # Извлекаем offer_id
            is_checked = value == "on"
            stock_value = post_data.get(f"{offer_id}_stock", None)  # Получаем значение stock

            if is_checked:
                offer_dict[offer_id] = {'stock' : f"{float(stock_value):.1f}".replace(',', '.')}
    return offer_dict



def get_all_price_wb(headers):
    result = {}
    opt_price = get_moysklad_opt_price(headers['moysklad_headers'])
    #print(f"opt_price {opt_price['rows'][0]['buyPrice']['value']}")
    #print(f"opt_price {opt_price['rows'][0]['article']}")
    opt_price_clear = {}
    for item in opt_price['rows']:
        #opt_price_clear['article'] = item['article']
        #print(f"opt_price {item['buyPrice']['value']/100}")
        opt_price_clear[item['article']] = {
            'opt_price' : int(float(item['buyPrice']['value']) / 100),
            }

    # продажи за последние 30 дней
    #url = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
    url = "https://statistics-api.wildberries.ru/api/v3/supplier/reportDetailByPeriod"
    dateTo = datetime.datetime.now()
    dateFrom = dateTo - datetime.timedelta(days=+30)
    dateTo = dateTo.strftime('%Y-%m-%d')
    dateFrom = dateFrom.strftime('%Y-%m-%d')
    print(f"date RFC3339 {dateTo}") #.isoformat()
    data = {
        'dateFrom': dateFrom, #lastmonth_date.strftime('%Y-%m')
        'dateTo': dateTo,
        'limit': 100
        }
    print(f"data wb {data}")
    response = requests.get(url, headers=headers['wb_headers'], json=data).json()
    print(f"date resp wb json {response}")
    realization = {}
    if response:
        if response['code'] == 503:
            result['error'] = response['message']

        if response['code'] == 200:
            for item in response['result']['rows']:
                if item['offer_id'] in realization:
                    realization[item['offer_id']]['sale_qty'] = realization[item['offer_id']]['sale_qty'] + item['sale_qty']
                else:
                    realization[item['offer_id']] = {'sale_qty': item['sale_qty']}
    #print(f"realization {realization}")
    #print(f"date resp {response}")

    url = "https://discounts-prices-api.wb.ru/api/v2/list/goods/filter"
    data = {
            'limit': 10,
            'offset': 0
            }
    response = requests.get(url, headers=headers['wb_headers'], json=data).json()
    print(f"response wb {response}")
    result = {}
    for item in response['result']['items']:
        if item['offer_id'] not in opt_price_clear:
            continue
        if item['offer_id'] not in realization:
            realization[item['offer_id']] = {'sale_qty': 0}
        delivery_price = float(item['price']['marketing_seller_price'])/100 * float(item['commissions']['sales_percent_fbs'])
        delivery_price = delivery_price + float(item['commissions']['fbs_direct_flow_trans_min_amount']) \
                         + float(item['commissions']['fbs_deliv_to_customer_amount']) + \
                         float(item['price']['marketing_seller_price'])/100*1 # эквайринг 1% и 10% для средней цены
        delivery_price = delivery_price + 15 # средняя цена доставки товара
        if item['offer_id'] == 'renata_371':
            print(f"371 {item}")
        #print(f"opt_price {item['offer_id']}")
        profit_price = int(float(item['price']['marketing_seller_price'])) - \
                       int(delivery_price) - opt_price_clear[item['offer_id']]['opt_price']
        profit_percent = profit_price / opt_price_clear[item['offer_id']]['opt_price'] * 100
        min_price = int(delivery_price) + (opt_price_clear[item['offer_id']]['opt_price']/100*30) + opt_price_clear[item['offer_id']]['opt_price']

        #print(f"offer_id {item}")
        result[item['offer_id']] = {
            'price': int(float(item['price']['price'])),
            'min_price': int(min_price),
            'marketing_seller_price': int(float(item['price']['marketing_seller_price'])),
            'delivery_price': int(delivery_price),
            'opt_price': opt_price_clear[item['offer_id']]['opt_price'],
            'profit_price': profit_price,
            'profit_percent': int(profit_percent),
            'sale_qty': realization[item['offer_id']]['sale_qty']
        }

    #print(f'result ozon price {result}')
    return result

def get_all_price_yandex(headers):
    # калулутяор fbs fby https: // dev - market - partner - api.docs - viewer.yandex.ru / ru / reference / tariffs / calculateTariffs

    result = {}
    company_id = headers['yandex_id']['company_id']
    businessId = headers['yandex_id']['businessId']
    warehouseId = headers['yandex_id']['warehouseId']
    opt_price = get_moysklad_opt_price(headers['moysklad_headers'])
    # print(f"opt_price {opt_price['rows'][0]['buyPrice']['value']}")
    # print(f"opt_price {opt_price['rows'][0]['article']}")
    opt_price_clear = {}
    for item in opt_price['rows']:
        # opt_price_clear['article'] = item['article']
        # print(f"opt_price {item['buyPrice']['value']/100}")
        opt_price_clear[item['article']] = {
            'opt_price': int(float(item['buyPrice']['value']) / 100),
        }

    # продажи за последние 30 дней
    # url = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
    url = f"https://api.partner.market.yandex.ru/campaigns/{company_id}/offer-prices"
    data = {
        'limit': 100
    }
    response = requests.get(url, headers=headers['yandex_headers'], json=data).json()
    print(f"resp yandex json {response}")
    for item in response['result']['offers']:
        result[item['id']] = {
            'price': int(float(item['price']['price'])),
            'min_price': int(min_price),
            'marketing_seller_price': int(float(item['price']['marketing_seller_price'])),
            'delivery_price': int(delivery_price),
            'opt_price': opt_price_clear[item['offer_id']]['opt_price'],
            'profit_price': profit_price,
            'profit_percent': int(profit_percent),
            'sale_qty': realization[item['offer_id']]['sale_qty']
        }

    if response:
        if response['status'] == 503:
            result['error'] = response['message']

    return result

# обновление цены товара озон
def update_price_ozon(obj, offer_dict):
    url = 'https://api-seller.ozon.ru/v1/product/import/prices'
    parser_data = {
        'moysklad_api': obj.moysklad_api,
        'yandex_api': obj.yandex_api,
        'wildberries_api': v.wildberries_api,
        'ozon_api': obj.ozon_api,
    }
    headers = get_headers(parser_data)
    ozon_price = []
    for key, value in offer_dict.items():
        ozon_price.append({
            'auto_action_enabled': 'ENABLED',
            'min_price': str(value['min_price']),
            'price': str(int(value['min_price']) * 1.3),
            'offer_id': key,
            'old_price': str((int(value['min_price']) * 1.3) * 1.5),
            'price_strategy_enabled': 'DISABLED'
            })

    for i in range(0, len(ozon_price), 1000): # 1000
        data = {
            'prices': ozon_price[i:i+999],
            #'prices': ozon_price[i:i + 2],
        }
        response = requests.post(url, headers=headers['ozon_headers'], json=data)
        print(f'data {data}')
        break
    #print(f'ozon price response {response.status_code}')
    print(f'ozon price json {response.json()}')

def get_finance_wb(headers: dict, period: str):
    opt_price = get_moysklad_opt_price(headers['moysklad_headers'])
    opt_price_clear = {}
    for item in opt_price['rows']:
        #opt_price_clear['article'] = item['article']
        #print(f"opt_price {item['buyPrice']['value']/100}")
        opt_price_clear[item['article']] = {
            'opt_price' : int(float(item['buyPrice']['value']) / 100),
            }

    url = "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"
    now = datetime.datetime.now()
    # Вычисляем первый день предыдущего месяца
    first_day_of_last_month = datetime.datetime(now.year, now.month, 1) - datetime.timedelta(days=1)
    first_day_of_last_month = first_day_of_last_month.replace(day=1)
    # Вычисляем последний день предыдущего месяца
    last_day_of_last_month = first_day_of_last_month.replace(day=1) + datetime.timedelta(days=32)
    last_day_of_last_month = last_day_of_last_month.replace(day=1) - datetime.timedelta(days=1)

    #date = {
    #    "dateFrom": first_day_of_last_month.strftime('%Y-%m-%d'),
    #    "dateTo": last_day_of_last_month.strftime('%Y-%m-%d'),
    #    "limit": 10000
    #}


    date = {
        "dateFrom": "2024-11-01",
        "dateTo": "2024-11-28"
    }

    print(f'date {date}')

    response = requests.get(url, headers=headers['wb_headers'], params=date).json()

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

def base_delete_files_with_prefix(directory_path, prefix):
    """
    Удаляет все файлы в указанной папке, начинающиеся с заданного префикса.
    """
    if os.path.exists(directory_path):
        for filename in os.listdir(directory_path):
            if fnmatch.fnmatch(filename, f"{prefix}*"):  # Проверяем, начинается ли имя с префикса
                file_path = os.path.join(directory_path, filename)
                try:
                    os.unlink(file_path)  # Удаляем файл
                    print(f"Удалён файл: {file_path}")
                except Exception as e:
                    print(f"Ошибка при удалении {file_path}: {e}")
    else:
        print(f"Директория {directory_path} не существует.")



def update_awaiting_deliver_from_owm(headers, seller, cron_active_mp):
    """
        получаем данные о неотгруженных заказах с МП и добавляем их в заказы МС в резерв
    """

    """
        OZON
    """
    cron_active_mp['ozon'] = True
    cron_active_mp['wb'] = False
    cron_active_mp['yandex'] = False

    if cron_active_mp['ozon']:

        ozon_awaiting_fbs_dict = ozon_get_awaiting_fbs(headers)
        ozon_current_product = ozon_awaiting_fbs_dict['current_product']

        ozon_status_fbs_dict = ozon_get_status_fbs(headers=headers) # получаем статусы с озона и сравниваем в базе, если отличаются меняем на МС

        #print(f'ozon_status_fbs_dict {ozon_status_fbs_dict}')
        #exit()
        if ozon_awaiting_fbs_dict['not_found']:
           not_found_product = {key: product for key in ozon_awaiting_fbs_dict['not_found'] for product in ozon_current_product if key in product.get('posting_number', '')}
           ms_create_customerorder(headers=headers, not_found_product=not_found_product, seller=seller, market='ozon')
           db_create_customerorder(not_found_product, market='ozon', seller=seller)
           ms_update_allstock_to_mp(headers=headers, seller=seller)
               #if ozon_status_fbs_dict['received']:
               #    ms_received_order(headers=headers, seller=seller, market='ozon', orders=ozon_status_fbs_dict['received'])
               #if ozon_status_fbs_dict['cancelled']:
               #    ms_cancel_order(headers=headers, seller=seller, market='ozon', orders=ozon_status_fbs_dict['cancelled'])

        if ozon_awaiting_fbs_dict['found']:
           found_product = {key: ozon_current_product[key] for key in ozon_awaiting_fbs_dict['found'] if key in ozon_current_product}
           if ozon_status_fbs_dict:
               print(f'ozon_status_fbs_dict {ozon_status_fbs_dict}')
               exit()
               if ozon_status_fbs_dict['delivering']: # доставлено
                   ms_delivering_order(headers=headers, seller=seller, market='ozon', orders=ozon_status_fbs_dict['delivering'])
               if ozon_status_fbs_dict['received']:
                   pass
               if ozon_status_fbs_dict['cancelled']:
                   pass

           #print(f'*' * 40)
           #print(f'found_product {found_product}')
           #print(f'*' * 40)
    """
    WB
    """
    if cron_active_mp['wb']:

        wb_awaiting_fbs_dict = wb_get_awaiting_fbs(headers)
        wb_filter_product = wb_awaiting_fbs_dict['filter_product']

        #print(f'*' * 40)
        #print(f'wb_awaiting_fbs_dict {wb_awaiting_fbs_dict}')
        #print(f'*' * 40)
        #print(f'wb_filter_product {wb_filter_product}')
        #print(f'*' * 40)

        if wb_awaiting_fbs_dict['not_found']:
           #print(f'*' * 40)
           not_found_product = {key: product for key in wb_awaiting_fbs_dict['not_found'] for product in wb_filter_product if key == product.get('posting_number', '')}
           #print(f'not_found_product {not_found_product}')
           #print(f'*' * 40)
           ms_create_customerorder(headers=headers, not_found_product=not_found_product, seller=seller, market='wb')
           db_create_customerorder(not_found_product, market='wb')
           ms_update_allstock_to_mp(headers=headers)
        if wb_awaiting_fbs_dict['found']:
           found_product = {key: wb_current_product[key] for key in wb_awaiting_fbs_dict['found'] if key in wb_filter_product}
           #print(f'*' * 40)
           #print(f'found_product {found_product}')
           #print(f'*' * 40)
        #exit()

    """
    YANDEX
    """
    if cron_active_mp['yandex']:

        yandex_awaiting_fbs_dict = yandex_get_awaiting_fbs(headers)
        yandex_filter_product = yandex_awaiting_fbs_dict['filter_product']

        #print(f'Y' * 40)
        #print(f'wb_awaiting_fbs_dict {yandex_awaiting_fbs_dict}')
        #print(f'Y' * 40)
        #print(f'wb_filter_product {yandex_filter_product}')
        #print(f'*' * 40)

        if yandex_awaiting_fbs_dict['not_found']:
            #print(f'*' * 40)
            not_found_product = {key: product for key in yandex_awaiting_fbs_dict['not_found'] for product in yandex_filter_product if
                                 key == product.get('posting_number', '')}
            #print(f'not_found_product {not_found_product}')
            #print(f'*' * 40)
            ms_create_customerorder(headers=headers, not_found_product=not_found_product, seller=seller, market='yandex')
            db_create_customerorder(not_found_product, market='yandex')
            ms_update_allstock_to_mp(headers=headers)
        if yandex_awaiting_fbs_dict['found']:
            found_product = {key: yandex_current_product[key] for key in yandex_awaiting_fbs_dict['found'] if key in yandex_filter_product}
            #print(f'*' * 40)
            #print(f'found_product {found_product}')
            #print(f'*' * 40)
        return ''


    #customerorder_dict = await ms_check_customerorder(headers)



    #    add_result = create_customorder_ms(order_dict)



"""
async 
Auto Update function
Auto Update function
Auto Update function
Auto Update function
Auto Update function
Auto Update function
"""


def autoupdate_sync_inventory(cron_id):
    try:
        cron = Crontab.objects.select_related('seller').get(id=cron_id)
    except Crontab.DoesNotExist:
        logger_error.error(f"autoupdate_sync_inventory: Crontab с id {cron_id} не найден.")

    if cron:
        cron_active_mp = {
            'yandex': cron.yandex,
            'ozon': cron.ozon,
            'wb': cron.wb,
        }
        if any(cron_active_mp.values()):
            headers = get_headers(cron.seller)
            result_update_awaiting = update_awaiting_deliver_from_owm(headers=headers, seller=cron.seller, cron_active_mp=cron_active_mp)
            return result_update_awaiting
        else:
            return 'No active'

'''
context['update_data'] = update_stock_mp_from_ms(headers=headers)
codes = [context['update_data']['code'], context['wb']['code'], context['yandex']['code']]
if all(code in (200, 204) for code in codes):
    context['sync_update'] = True
'''


        #if row[7] is not None:
        #    result_dict = await autoupdate_get_last_sync_acquisition_writeoff_ms(headers=headers)
        #    if result_dict['enter'] == cron_dict['enter'] and result_dict['loss'] == cron_dict['loss']:
                # запускаем тут автообноление синхронизация
        #        pass
        #    else:
        #        context['update_data'] = update_stock_mp_from_ms(headers=headers)
        #        codes = [context['update_data']['code'], context['wb']['code'], context['yandex']['code']]
                # Проверка, все ли значения равны 200 или 204
        #        if all(code in (200, 204) for code in codes):
        #            context['sync_update'] = True
        #            update_stmt = (update(crontab_table).where(crontab_table.c.id == cron_id).values(cron_dict=result_dict))
    #await session.execute(update_stmt)
    #await session.commit()



    #return row_list
    #for item in row_list:
    #    print(f"autoupdate_sync_inventory success")
    #    await autoupdate_get_last_sync_acquisition_writeoff_ms(headers=item['headers'], cron_data=item['cron_data'])










