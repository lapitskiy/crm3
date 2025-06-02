import fnmatch
import time
import requests
import datetime
import uuid

import xlsxwriter

#TEST

import os
from django.conf import settings

import pandas as pd

from owm.utils.db_utils import db_get_metadata, db_create_customerorder, db_get_status, redis_lock
from owm.utils.ms_utils import ms_create_customerorder, ms_get_organization_meta, ms_get_agent_meta, ms_update_allstock_to_mp, \
    ms_cancel_order, ms_create_delivering, ms_create_sold, ms_create_canceled
from owm.models import Crontab
from owm.utils.oz_utils import ozon_get_awaiting_fbs, ozon_get_status_fbs
from owm.utils.wb_utils import wb_get_status_fbs
from owm.utils.ya_utils import yandex_get_orders_fbs

import logging


logger_info = logging.getLogger('crm3_info')
logger_error = logging.getLogger('crm3_error')


def get_headers(seller):
    headers = {}
    #print(f'seller {seller}')
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
    cron_active_mp['wb'] = True
    cron_active_mp['yandex'] = True
    ms_update = False

    if cron_active_mp['ozon']:

        ozon_awaiting_fbs_dict = ozon_get_awaiting_fbs(headers)
        
        ozon_found_product = ozon_awaiting_fbs_dict['found']
        ozon_notfound_product = ozon_awaiting_fbs_dict['not_found']
        
        ozon_current_product = ozon_awaiting_fbs_dict['current_product']

        ozon_status_fbs_dict = ozon_get_status_fbs(headers=headers, seller=seller) # получаем статусы с озона и сравниваем в базе

        if ozon_notfound_product:
           not_found_product = {key: product for key in ozon_awaiting_fbs_dict['not_found'] for product in ozon_current_product if key in product.get('posting_number', '')}
           ms_result = ms_create_customerorder(headers=headers, not_found_product=not_found_product, seller=seller, market='ozon')
           if ms_result:
               db_create_customerorder(not_found_product, market='ozon', seller=seller)
               ms_update = True

        if ozon_awaiting_fbs_dict['found']:
           #print(f"ozon_status_fbs_dict {ozon_status_fbs_dict}")
           #found_product = {key: ozon_current_product[key] for key in ozon_awaiting_fbs_dict['found'] if key in ozon_current_product}
           if ozon_status_fbs_dict:
               #if ozon_status_fbs_dict['awaiting']:
                   #print(f"ozon_status_fbs_dict {ozon_status_fbs_dict['awaiting']}")
                   # вернуть в ozon_get_status_fbs
                   # if posting_number in existing_orders and existing_orders[posting_number] != status:
                   #ms_create_delivering(headers=headers, seller=seller, market='ozon', orders=ozon_status_fbs_dict['awaiting'])
               if ozon_status_fbs_dict.get('delivering'): # доставлется (отгружено)                                      
                   # вернуть в ozon_get_status_fbs
                   # if posting_number in existing_orders and existing_orders[posting_number] != status:
                   ms_create_delivering(headers=headers, seller=seller, market='ozon', orders=ozon_status_fbs_dict['delivering'])
               if ozon_status_fbs_dict.get('delivered'):                   
                   ms_create_sold(headers=headers, seller=seller, market='ozon', orders=ozon_status_fbs_dict['delivered'])
               if ozon_status_fbs_dict.get('cancelled'):
                   ms_create_canceled(headers=headers, seller=seller, market='ozon', orders=ozon_status_fbs_dict['cancelled'])

           #print(f'*' * 40)
           #print(f'found_product {found_product}')
           #print(f'*' * 40)
    """
    WB
    """
    if cron_active_mp['wb']:

        wb_fbs_dict = wb_get_status_fbs(headers=headers, seller=seller) #здесь получаются все статусы
        #print(f'*' * 40)
        #print(f'wb_fbs_dict {wb_fbs_dict}')
        #print(f'*' * 40)
        

        if 'error' not in wb_fbs_dict:

            wb_found_product = wb_fbs_dict['found']
            wb_notfound_product = wb_fbs_dict['not_found']
            wb_all_status = wb_fbs_dict['filter_product']
            wb_waiting_product = wb_all_status['waiting']
            #print(f'1' * 40)
            #print(f'wb_found_product {wb_found_product}')
            #print(f'1' * 40)

            if wb_notfound_product:
               #print(f'*' * 40)
               not_found_product = {key: product for key in wb_notfound_product for product in wb_waiting_product if key == product.get('posting_number', '')}
               #print(f'*' * 40)
               if not_found_product:
                   #print(f'3' * 40)
                   #print(f'not_found_product {not_found_product}')
                   #print(f'3' * 40)
                   ms_result = ms_create_customerorder(headers=headers, not_found_product=not_found_product, seller=seller, market='wb')
                   if ms_result and 'error' not in ms_result:
                       db_create_customerorder(not_found_product, market='wb', seller=seller)
                       ms_update = True
            if wb_found_product:
               #found_product = {key: wb_current_product[key] for key in wb_status_fbs_dict['found'] if key in wb_filter_product}

               #print(f'*' * 40)
               #print(f'wb_found_product {wb_found_product}')
               #print(f'* ' * 40)
               
               if wb_all_status:
                   #print(f'wb_all_status {wb_all_status}')
                   if wb_all_status.get('sorted'): # доставлется (отгружено)         
                       #print('sorted')              
                       ms_create_delivering(headers=headers, seller=seller, market='wb', orders=wb_all_status['sorted'])
                       #print('доставлется')
                   if wb_all_status.get('sold'):
                       ms_create_sold(headers=headers, seller=seller, market='wb', orders=wb_all_status['sold'])
                       #print('продано')
                   if wb_all_status.get('canceled'):
                       ms_create_canceled(headers=headers, seller=seller, market='wb', orders=wb_all_status['canceled'])
                       #print('отменено')

    """
    YANDEX
    """
    if cron_active_mp['yandex']:

        yandex_fbs_dict = yandex_get_orders_fbs(headers=headers, seller=seller)
       
        #print(f'yandex_fbs_dict {yandex_fbs_dict}')
        #print(f'Y' * 40)

        if 'error' not in yandex_fbs_dict:
            yandex_all_product = yandex_fbs_dict['filter_product']
            yandex_waiting_product = yandex_all_product['waiting']
            yandex_found_product = yandex_fbs_dict['found']
            yandex_notfound_product = yandex_fbs_dict['not_found']            

        if yandex_notfound_product:
            #print(f'*' * 40\
            #print(f'yandex_all_product {yandex_all_product}')
            not_found_product = {key: product for key in yandex_notfound_product for product in yandex_waiting_product if
                                 key == product.get('posting_number', '')}
            if not_found_product:                
                #print(f'*' * 40)
                #print(f'not_found_product {not_found_product}')
                #print(f'*' * 40)
                
                ms_result = ms_create_customerorder(headers=headers, not_found_product=not_found_product, seller=seller, market='yandex')
                if ms_result:
                    db_create_customerorder(not_found_product, market='yandex', seller=seller)
                    ms_update = True
        if yandex_found_product:
            #found_product = {key: yandex_current_product[key] for key in yandex_found_product if key in yandex_all_product}
            
            if yandex_all_product:
                if yandex_all_product.get('sorted'): # доставлется (отгружено)         
                    #print('sorted')              
                    ms_create_delivering(headers=headers, seller=seller, market='yandex', orders=yandex_all_product['sorted'])
                    #print('доставлется')
                if yandex_all_product.get('sold'):
                    ms_create_sold(headers=headers, seller=seller, market='yandex', orders=yandex_all_product['sold'])
                    #print('продано')
                if yandex_all_product.get('canceled'):
                    ms_create_canceled(headers=headers, seller=seller, market='yandex', orders=yandex_all_product['canceled'])
                    #print('отменено')
            #print(f'*' * 40)
            #print(f'found_product {found_product}')
            #print(f'*' * 40)

    if ms_update:
        ms_update_allstock_to_mp(headers=headers, seller=seller)

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
        return 'Cron not found'

    lock_key = f"lock:autoupdate_sync_inventory:{cron.seller.id}"

    with redis_lock(lock_key, timeout=300) as acquired:
        if not acquired:
            print(f"[SKIP] Already running for seller {cron.seller.id}")
            return 'Already running'

        cron_active_mp = {
            'yandex': cron.yandex,
            'ozon': cron.ozon,
            'wb': cron.wb,
        }
        if any(cron_active_mp.values()):
            headers = get_headers(cron.seller)
            result_update_awaiting = update_awaiting_deliver_from_owm(
                headers=headers,
                seller=cron.seller,
                cron_active_mp=cron_active_mp
            )
            return result_update_awaiting
        else:
            return 'No active'
