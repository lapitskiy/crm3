import requests
import datetime

from owm.utils.db_utils import db_check_awaiting_postingnumber

import logging


def yandex_update_inventory(headers, stock):
    #print(f"head {headers}")
    headers_ya = headers['yandex_headers']
    company_id = headers['yandex_id']['company_id']
    businessId = headers['yandex_id']['businessId']
    warehouseId = headers['yandex_id']['warehouseId']
    current_time = datetime.datetime.now()
    offset = datetime.timezone(datetime.timedelta(hours=3))  # Указываем смещение +03:00
    formatted_time = current_time.replace(tzinfo=offset).isoformat()
    url = f'https://api.partner.market.yandex.ru/campaigns/{company_id}/offers/stocks'
    sku = []
    for key, value in stock.items():
        sku.append({
            'sku': key,
            'warehouseId': warehouseId,
            'items': [{
                'count': int(value['stock']),
                'type': 'FIT',
                'updatedAt': formatted_time
            }]
        })
    data = {
        'skus': sku
    }
    #print(f"skus {data['skus'][0]}")
    response = requests.put(url=url, json=data, headers=headers_ya)
    context = {
        'code': response.status_code,
        'json': response.json()
    }
    return context

def yandex_get_awaiting_fbs(headers: dict):
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

    yandex_headers = headers.get('yandex_headers')
    campaignId = headers.get('yandex_id', {}).get('company_id')


    url = f'https://api.partner.market.yandex.ru/campaigns/{campaignId}/orders'
    params = {
        "status": "PROCESSING",
        "substatus": ['STARTED', 'READY_TO_SHIP'],
        }


    try:
        all_orders = {}
        response = requests.get(url, headers=yandex_headers, params=params)
        if response.status_code == 200:
            all_orders = response.json()
            print(f'Z' * 40)
            print(f'Z' * 40)
            print(f"yandex_get_awaiting_fbs response_json all_orders: {all_orders}")
            print(f'Z' * 40)
            print(f'Z' * 40)
        else:
            logger_error.error(f"yandex_get_awaiting_fbs: ошибка ответа - {response.text}")
            print(f"response_json response.text: {response.text}")
            result['error'] = response.text
    except Exception as e:
        result['error'] = f"Error in awaiting request: {e}"

    orders = all_orders.get('orders', [])
    #print(f'Z' * 40)
    #print(f'Z' * 40)
    #print(f" orders { orders }")
    #print(f'Z' * 40)
    #print(f'Z' * 40)
    filtered_result = []
    id_list = []
    for order in orders:
        product_list = []
        for product in order['items']:
            id_list.append(order['id'])
            product_list.append({
                "offer_id": product['offerId'],
                "price": int(product['buyerPrice']+product['subsidy']),
                "quantity": product['count']
                })

        order['substatus'] = 'awaiting_delivery' if order['substatus'] in ['STARTED', 'READY_TO_SHIP'] else order['substatus']

        filtered_result.append(
            {'posting_number': order['id'],
             'status': order['substatus'],
             'product_list': product_list
             })

    check_result_dict = db_check_awaiting_postingnumber(id_list)
    check_result_dict['filter_product'] = filtered_result

    return check_result_dict

def yandex_get_products(headers):

    headers_ya = headers['yandex_headers']
    company_id = headers['yandex_id']['company_id']
    businessId = headers['yandex_id']['businessId']
    warehouseId = headers['yandex_id']['warehouseId']

    current_time = datetime.datetime.now()
    offset = datetime.timezone(datetime.timedelta(hours=3))  # Указываем смещение +03:00
    formatted_time = current_time.replace(tzinfo=offset).isoformat()
    url = f"https://api.partner.market.yandex.ru/businesses/{businessId}/offer-mappings"

    all_items = []
    page_token = None

    while True:
        params = {"limit": 200}
        if page_token:
            params["page_token"] = page_token

        response = requests.post(url=url, headers=headers_ya, json={}, params=params).json()
        #print(f"response: {response}")
        if response.get("status") == "ERROR":
            print(f"Ошибка: {response}")
            break

        # Сохраняем товары
        offers = response.get("result", {}).get("offerMappings", [])
        all_items.extend(offers)
        # Переходим на следующую страницу
        page_token = response.get("result", {}).get("paging", {}).get("nextPageToken")

        # Если страницы закончились, выходим из цикла
        if not page_token:
            break

        # Задержка между запросами для соблюдения лимита
        #time.sleep(1)
    #print(f"all_items {all_items}")
    return all_items