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

def yandex_get_orders_fbs(headers: dict):
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
    current_date_str = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
    one_month_ago_str = one_month_ago.strftime('%Y-%m-%dT%H:%M:%SZ')

    yandex_headers = headers.get('yandex_headers')
    campaignId = headers.get('yandex_id', {}).get('company_id')


    url = f'https://api.partner.market.yandex.ru/campaigns/{campaignId}/orders'

    try:
        orders = []
        current_page = 1
        total_pages = 1

        while current_page <= total_pages:
            params['page'] = current_page
            response = requests.get(url, headers=yandex_headers)
            if response.status_code == 200:
                response_json = response.json()
                orders.extend(response_json.get('orders', []))
                total_pages = response_json.get('pager', {}).get('pagesCount', 1)
                current_page += 1
            else:
                logger_error.error(f"yandex_get_awaiting_fbs: ошибка ответа - {response.text}")
                result['error'] = response.text
                break

        else:
            logger_error.error(f"yandex_get_awaiting_fbs: ошибка ответа - {response.text}")
            print(f"response_json response.text: {response.text}")
            result['error'] = response.text
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
    
    for order in all_orders['orders']:
        yandex_status = order.get('status')
        mapped_status = status_aliases.get(yandex_status)
        if mapped_status in filtered_status_map:
            filtered_status_map[mapped_status].append(order)
    
    filtered_result = {"waiting": [], "sorted": [], "sold": [], "canceled": []}
    
    
    for current_status in status_list:
        for order in filtered_status_map[current_status]:
            posting_number = str(order["id"])
            if posting_number in existing_orders:
                #print(f'{posting_number}')
                existing_status = existing_orders[posting_number]
                if existing_status != current_status:
                    product_list = [{
                        "offer_id": order["article"],
                        "price": int(order["convertedPrice"]) / 100,
                        "quantity": 1
                    }]
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
                    filtered_result[current_status].append({
                        "posting_number": order["id"],
                        "status": current_status,
                        "product_list": product_list
                    })
    
    for order in orders:
        product_list = []
        for product in order['items']:
            id_list.append(order['id'])
            product_list.append({
                "offer_id": product['offerId'],
                "price": int(product['buyerPrice']+product['subsidy']),
                "quantity": product['count']
                })

        status = 'sorted' if order['substatus'] == 'SHIPPED' else status_aliases.get(order['status'], order['status'])

        filtered_result.append(
            {'posting_number': order['id'],
             'status': status,
             'substatus': order['substatus'],
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
