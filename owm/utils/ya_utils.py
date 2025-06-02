import requests
import datetime

from owm.models import Seller
from owm.utils.db_utils import db_check_awaiting_postingnumber, db_get_status

import json

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


def yandex_get_orders_fbs(headers: dict, seller: Seller):
    '''
    получаем последние отгрузки FBS (отправления)
    '''
    result = {}

    orders_db = db_get_status(seller=seller, market='yandex')
    orders_list = orders_db.get('yandex', [])
    existing_orders = {order['posting_number']: order['status'] for order in orders_list}

    current_date = datetime.datetime.now()

    # Вычисляем дату месяц назад
    one_month_ago = current_date - datetime.timedelta(weeks=4)

    # Форматируем даты в строковый формат (YYYY-MM-DD)
    #current_date_str = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    #one_month_ago_str = one_month_ago.strftime('%Y-%m-%dT%H:%M:%SZ')

    yandex_headers = headers.get('yandex_headers')
    campaignId = headers.get('yandex_id', {}).get('company_id')


    url = f'https://api.partner.market.yandex.ru/campaigns/{campaignId}/orders'

    try:
        orders = []
        current_page = 1
        total_pages = 1
        params = {}

        while current_page <= total_pages:
            params['page'] = current_page
            response = requests.get(url, headers=yandex_headers, params=params)
            if response.status_code == 200:
                response_json = response.json()
                orders.extend(response_json.get('orders', []))
                total_pages = response_json.get('pager', {}).get('pagesCount', 1)
                current_page += 1
            else:
                logging.error(f"yandex_get_awaiting_fbs: ошибка ответа - {response.text}")
                result['error'] = response.text
                break
    except Exception as e:
        result['error'] = f"Error in awaiting request: {e}"
    

    #print(f'Z' * 40)
    #print(f'Z' * 40)
    #print(f" orders { orders }")
    #print(f'Z' * 40)
    #print(f'Z' * 40)
    
    filtered_status_map = {"waiting": [], "sorted": [], "sold": [], "canceled": []}

    status_list = ("waiting", "sorted", "sold", "canceled")

    # Маппинг исходных статусов к финальным ключам
    status_aliases = {
    "CANCELLED": "canceled",
    "DELIVERED": "sold",
    "DELIVERY": "sorted",
    "PICKUP": "sorted",
    "PROCESSING": "waiting",
    }
    
    #print(f"waiting {json.dumps(orders[:8], indent=2, ensure_ascii=False)}")
    #print(f"orders count: {len(orders)}")        
    #print(f"orders: {orders}")        
    
    
    for order in orders:
        yandex_status = order.get('status')
        yandex_substatus = order.get('substatus')        
        mapped_status = 'sorted' if yandex_substatus == 'SHIPPED' else status_aliases.get(yandex_status)        
        if mapped_status in filtered_status_map:
            filtered_status_map[mapped_status].append(order)
        
    #print(f"filtered_status_map waiting: {filtered_status_map['waiting']}")    
    #print(f'* ' * 40)
    #print(f"filtered_status_map sorted: {filtered_status_map['sorted']}")    
    #print(f'* ' * 40)
    #print(f"waiting {json.dumps(filtered_status_map['waiting'][:8], indent=2, ensure_ascii=False)}")
    #print(f"sorted {json.dumps(filtered_status_map['sorted'][:4], indent=2, ensure_ascii=False)}")
    #print(f"sold {json.dumps(filtered_status_map['sold'][:4], indent=2, ensure_ascii=False)}")
    #print(f"canceled {json.dumps(filtered_status_map['canceled'][:4], indent=2, ensure_ascii=False)}")    
    
    
    filtered_result = {"waiting": [], "sorted": [], "sold": [], "canceled": []}
    
    
    for current_status in status_list:
        for order in filtered_status_map[current_status]:
            posting_number = str(order["id"])
            if posting_number in existing_orders:
                #print(f'{posting_number}')
                existing_status = existing_orders[posting_number]
                if existing_status != current_status:
                    product_list = []
                    for item in order.get('items', []):
                        product_list.append({
                            "offer_id": item.get("offerId") or item.get("offer_id"),
                            "price": item.get('price', 0),
                            "quantity": item.get('count', 1)
                        })
                    filtered_result[current_status].append({
                        "posting_number": str(order["id"]),
                        "status": current_status,
                        "product_list": product_list
                    })
            else:
                if current_status == 'waiting':                    
                    #print(f"order {order}")
                    product_list = []
                    for item in order.get('items', []):
                        product_list.append({
                            "offer_id": item.get("offerId") or item.get("offer_id"),
                            "price": item.get('price', 0),
                            "quantity": item.get('count', 1)
                        })
                    filtered_result[current_status].append({
                        "posting_number": order["id"],
                        "status": current_status,
                        "product_list": product_list
                    })
    
    
    result = {}
    
    posting_numbers = [
    item['id']
    for status in status_list
    for item in filtered_status_map[status]
    ]
    
    result = db_check_awaiting_postingnumber(posting_numbers)
    result['filter_product'] = filtered_result
    return result

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
