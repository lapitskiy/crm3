


import requests
import inspect
import copy

from typing import Any, Dict, List

from collections import OrderedDict

from django.db import models


from owm.utils.db_utils import db_get_metadata, db_update_customerorder

import logging
logger_info = logging.getLogger('crm3_info')
logger_error = logging.getLogger('crm3_error')

# бывший get_moysklad_opt_price
def ms_get_product(headers):
    moysklad_headers = headers.get('moysklad_headers')
    url = "https://api.moysklad.ru/api/remap/1.2/entity/product"
    params = [
        ("limit", 1000)
    ]
    result = {}
    response = requests.get(url, headers=moysklad_headers, params=params)

    if response.status_code == 200:
        result['status_code'] = response.status_code
        result['response'] = response.json()
    else:
        result['status_code'] = response.status_code
        result['response'] = response.text
        logger_error.error(f"error ms_get_product: {response.text}")
    return result

def ms_get_organization_meta(headers) -> list:
    result = []
    #url = "https://api.moysklad.ru/api/remap/1.2/entity/metadata?filter=type=organization;type=counterparty"
    url = 'https://api.moysklad.ru/api/remap/1.2/entity/organization/'
    moysklad_headers = headers.get('moysklad_headers')
    try:
        response = requests.get(url, headers=moysklad_headers)
        if response.status_code == 200:
            response_json = response.json()
            result = [{'id': organization['id'], 'name': organization['name']} for organization in response_json['rows']]

                #"href": "https://api.moysklad.ru/api/remap/1.2/entity/group/081922b2-d269-11e4-90a2-8ecb0004410f",
                #"metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/group/metadata",
        else:
            print(f"error ms_get_organization_meta response.text: {response.text}")
    except Exception as e:
        print(f"error ms_get_organization_meta : {e}")
    return result


def ms_get_agent_meta(headers: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Получает список контрагентов из МойСклад по API.

    :param headers: Словарь с заголовками, включая ключ moysklad_headers.
    :return: Список словарей с полями id и name для каждого контрагента.
    """
    result = []
    moysklad_headers = headers.get('moysklad_headers')

    if not moysklad_headers:
        print("ms_get_agent_meta: moysklad_headers не передан")
        return result

    url = 'https://api.moysklad.ru/api/remap/1.2/entity/counterparty/'
    try:
        response = requests.get(url, headers=moysklad_headers)
        if response.status_code == 200:
            response_json = response.json()
            #print(f"response_json agent: {response_json}")
            result = [
                {'id': agent['id'], 'name': agent['name']}
                for agent in response_json['rows']
            ]
            #print(f"ms_get_agent_meta (packag): {response_json}")
        else:
            print(f"error ms_get_agent_meta response.text: {response.text}")
    except Exception as e:
        print(f"error ms_get_agent_meta: {e}")
    return result

def ms_get_orderstatus_meta(headers: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Получает список state из МойСклад по API.

    :param headers: Словарь с заголовками, включая ключ moysklad_headers.
    :return: Список словарей с полями id и name для каждого контрагента.
    """
    result = []
    moysklad_headers = headers.get('moysklad_headers')

    if not moysklad_headers:
        print("ms_get_agent_meta: moysklad_headers не передан")
        return result
    #url = 'https://api.moysklad.ru/api/remap/1.2/entity/customerorder/'
    url = 'https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata'
    try:
        response = requests.get(url, headers=moysklad_headers)
        if response.status_code == 200:
            response_json = response.json()
            #print(f"response_json agent: {response_json}")
            result = [
                {'id': state['id'], 'name': state['name']}
                for state in response_json['states']
            ]
            #print(f"ms_get_agent_meta (packag): {response_json}")
        else:
            print(f"error ms_get_agent_meta response.text: {response.text}")
    except Exception as e:
        print(f"error ms_get_agent_meta: {e}")
    return result


def ms_get_storage_meta(headers: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Получает список складов из МойСклад по API.

    :param headers: Словарь с заголовками, включая ключ moysklad_headers.
    :return: Список словарей с полями id и name для каждого контрагента.
    """
    result = []
    moysklad_headers = headers.get('moysklad_headers')

    if not moysklad_headers:
        logger_error.error("ms_get_storage_meta: moysklad_headers не передан")
        return result

    url = 'https://api.moysklad.ru/api/remap/1.2/entity/store'
    try:
        response = requests.get(url, headers=moysklad_headers)
        if response.status_code == 200:
            response_json = response.json()
            result = [
                {'id': storage['id'], 'name': storage['name']}
                for storage in response_json['rows']
            ]
            #print(f"ms_get_agent_meta (packag): {response_json}")
        else:
            logger_error.error(f"ms_get_storage_meta: ошибка ответа - {response.text}")
    except Exception as e:
        logger_error.error(f"ms_get_storage_meta: исключение - {str(e)}")
    return result

# получаем последние название оприходвание и списания и пишем в базу
def ms_get_last_enterloss(headers: dict):
    result = {}

    moysklad_headers = headers.get('moysklad_headers')
    # оприходование
    url = 'https://api.moysklad.ru/api/remap/1.2/entity/enter'
    params = {
        'limit': 1,
        'order': 'created,desc'
        }
    response = requests.get(url, headers=moysklad_headers, params=params)
    if response.status_code == 200:
        response_json = response.json()
        tag = response_json
        result['enter'] = tag['rows'][0]['name']
    else:
        error_message = response.text
        raise Exception(f"Error {response.status_code}: {error_message}")

    url = 'https://api.moysklad.ru/api/remap/1.2/entity/loss'
    response = requests.get(url, headers=moysklad_headers, params=params)
    if response.status_code == 200:
        response_json = response.json()
        tag = response_json
        result['loss'] = tag['rows'][0]['name']
    else:
        error_message = response.text
        raise Exception(f"Error {response.status_code}: {error_message}")
    return result

# получаем список заказов покуателей
def ms_get_customorders(headers):
    moysklad_headers = headers.get('moysklad_headers')
    url = "https://api.moysklad.ru/api/remap/1.2/entity/customerorder"
    params = [
        ("limit", 1000)
    ]
    result = {}
    response = requests.get(url, headers=moysklad_headers, params=params)

    if response.status_code == 200:
        result['status_code'] = response.status_code
        result['response'] = response.json()
    else:
        result['status_code'] = response.status_code
        result['response'] = response.text
        logger_error.error(f"error ms_get_product: {response.text}")
    return result


async def ms_check_customerorder(headers: dict):
    result = {}

    moysklad_headers = headers.get('moysklad_headers')
    # оприходование

    url = 'https://api.moysklad.ru/api/remap/1.2/entity/customerorder'
    params = {
        'limit': 1000,
        'order': 'created,desc'
        }
    async with get_http_session() as session:
        async with session.get(url, headers=moysklad_headers, params=params) as response:
            if response.status == 200:
                response_json = await response.json()
                #print(f"customerorder response_json: {response_json}")
            else:
                error_message = await response.text()
                result['response'] = error_message
    return result


# остатки на МС отравляем на все MP
def ms_update_allstock_to_mp(headers, seller):
    '''
    Получаем остатки с МойСклад и выставляем такие же на Озон, Вб, Яндекс
    '''
    from owm.utils.oz_utils import ozon_update_inventory
    from owm.utils.wb_utils import wb_update_inventory
    from owm.utils.ya_utils import yandex_update_inventory

    context = {}

    moysklad_headers = headers.get('moysklad_headers')
    metadata = db_get_metadata(seller)

    stock = ms_get_all_stock(moysklad_headers, metadata)
    #print(f'stock {stock}')
    context['ozon'] = ozon_update_inventory(headers, stock)
    context['yandex'] = yandex_update_inventory(headers, stock)
    context['wb'] = wb_update_inventory(headers, stock)
    return context

def ms_cancel_order(headers: Dict[str, Any], posting_number: str):
    '''
    Обновляем статус и отменяем заказ на МойСклад
    '''
    pass

def ms_create_customerorder(headers: dict, not_found_product: dict, seller: models.Model, market: str):
    result = False
    mapping = {
            'ozon': {'storage': 'ms_storage_ozon', 'agent': 'ms_ozon_contragent', 'status': 'ms_status_awaiting'},
            'wb': {'storage': 'ms_storage_wb', 'agent': 'ms_wb_contragent', 'status': 'ms_status_awaiting'},
            'yandex': {'storage': 'ms_storage_yandex', 'agent': 'ms_yandex_contragent', 'status': 'ms_status_awaiting'}}

    moysklad_headers = headers.get('moysklad_headers')
    metadata = db_get_metadata(seller)
    if metadata:

        products = ms_get_product(headers)

        article_to_id = {}

        for item in products['response']['rows']:
            article = item.get('article')
            if article:
                article_to_id[article] = item['id']

        orders = copy.deepcopy(not_found_product)  # чтобы избежать изменения исходного

        # Создаём список ключей заказов, которые нужно удалить
        orders_to_delete = []

        for key, value in orders.items():
            product_list = value.get('product_list', [])

            # Если хоть один продукт не найден в article_to_id, помечаем заказ на удаление
            if any(product.get('offer_id') not in article_to_id for product in product_list):
                orders_to_delete.append(key)

        # Удаляем помеченные заказы
        for key in orders_to_delete:
            del orders[key]

        for key, value in orders.items():
            product_list = value.get('product_list', [])
            for product in product_list:
                offer_id = product.get('offer_id')
                if offer_id in article_to_id:
                    # Добавим поле "id" в словарь конкретного товара
                    product['id'] = article_to_id[offer_id]

        #print(f"result_dict {orders}")


        #organization_meta = ms_get_organization_meta(headers)
        #agent_meta = ms_get_agent_meta(headers)
        #print(f'metadata {metadata}')

        url = 'https://api.moysklad.ru/api/remap/1.2/entity/customerorder'

        organization_meta = {
            "href": f"https://api.moysklad.ru/api/remap/1.2/entity/organization/{metadata['ms_organization']['id']}",
            "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/organization/metadata",
            "type": "organization",
            "mediaType": "application/json"
        }

        agent_meta = {
            "href": f"https://api.moysklad.ru/api/remap/1.2/entity/counterparty/{metadata[mapping[market]['agent']]['id']}",
            "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/metadata",
            "type": "counterparty",
            "mediaType": "application/json"
        }

        storage_meta = {
            "href": f"https://api.moysklad.ru/api/remap/1.2/entity/store/{metadata[mapping[market]['storage']]['id']}",
            "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/metadata",
            "type": "store",
            "mediaType": "application/json"
        }

        status_meta = {
            "href": f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/states/{metadata[mapping[market]['status']]['id']}",
            "type": "state",
            "mediaType": "application/json"
        }

        data = []
        # Формирование списка заказов

        for key, order in orders.items():
            order_data = {
                "name": str(order['posting_number']),
                "vatEnabled": False,
                "applicable": True,
                "organization": {
                    "meta": organization_meta
                },
                "agent": {
                    "meta": agent_meta
                },
                "store": {
                    "meta": storage_meta
                },
                "state": {
                    "meta": status_meta
                },
                "positions": []
            }

            # Добавляем позиции для каждого продукта в заказе
            for product in order['product_list']:
                try:
                    product_id = product['id']
                except KeyError:
                    print(f"Warning: Пропущен продукт без 'id': {product}")
                    continue  # Пропускаем продукт, если у него нет 'id'

                position = {
                    "quantity": product['quantity'],
                    "price": float(product['price']) * 100,  # переводим цену в копейки
                    "discount": 0,
                    "vat": 0,
                    "assortment": {
                        "meta": {
                            "href": f"https://api.moysklad.ru/api/remap/1.2/entity/product/{product_id}",
                            "type": "product",
                            "mediaType": "application/json"
                        }
                    },
                    "reserve": product['quantity']
                }
                order_data["positions"].append(position)

            # Добавляем сформированный заказ в общий список
            data.append(order_data)



        try:
            response = requests.post(url, headers=moysklad_headers, json=data)
            #logging.info(f"[seller {seller.id}][ms_create_customerorder][response json]: {response.json()}")
            #print(f"*" * 40)
            #print(f"*" * 40)
            print(f"response_json MS: {data}")
            #print(f"*" * 40)
            #print(f"*" * 40)
        except requests.exceptions.JSONDecodeError:
            print(f"[seller {seller.id}][ms_create_customerorder][response text]: {response.text}")
            logging.error(f"[seller {seller.id}][ms_create_customerorder][response text]: {response.text}")

        # Дополнительные шаги для обработки результата
        if response.status_code != 200:
            print(f"[seller {seller.id}][ms_create_customerorder][response text]: {response.text}")
            print(f"Ошибка: сервер вернул код состояния {response.status_code}")
            return False
        else:
            return True
    else:
        raise Exception(f"Error: обновите метадату в настройках Контрагенты")
    return result

def ms_create_delivering(headers: Dict[str, Any], seller: models.Model, market: str, orders: list):
    '''
    Обновляем статус и отгружаем заказ на МойСклад
    '''

    response_data = ms_get_customorders(headers)
    ms_orders_dict = {order["name"]: order["id"] for order in response_data["response"]["rows"]}
    print(f"*" * 40)
    print(f'response ms_get_customorders: {response_data["response"]["rows"][0]}')
    print(f"*" * 40)

    result = {}

    db_mapping = {
        'ozon': {'storage': 'ms_storage_ozon', 'agent': 'ms_ozon_contragent', 'status': 'ms_status_shipped'},
        'wb': {'storage': 'ms_storage_wb', 'agent': 'ms_wb_contragent', 'status': 'ms_status_shipped'},
        'yandex': {'storage': 'ms_storage_yandex', 'agent': 'ms_yandex_contragent', 'status': 'ms_status_shipped'}}

    mp_mapping = {
        'ozon': {'delivering': 'delivering'},
        'wb': {'delivering': 'sorted'},
        'yandex': {'delivering': 'sorted'}}

    moysklad_headers = headers.get('moysklad_headers')
    metadata = db_get_metadata(seller)


    url_demand = 'https://api.moysklad.ru/api/remap/1.2/entity/demand'
    url_tempalate = 'https://api.moysklad.ru/api/remap/1.2/entity/demand/new'


    organization_meta = {
        "href": f"https://api.moysklad.ru/api/remap/1.2/entity/organization/{metadata['ms_organization']['id']}",
        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/organization/metadata",
        "type": "organization",
        "mediaType": "application/json"
    }

    agent_meta = {
        "href": f"https://api.moysklad.ru/api/remap/1.2/entity/counterparty/{metadata[db_mapping[market]['agent']]['id']}",
        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/metadata",
        "type": "counterparty",
        "mediaType": "application/json"
    }

    storage_meta = {
        "href": f"https://api.moysklad.ru/api/remap/1.2/entity/store/{metadata[db_mapping[market]['storage']]['id']}",
        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/metadata",
        "type": "store",
        "mediaType": "application/json"
    }

    #status_meta = {
    #    "href": f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/states/{metadata[mapping[market]['status']]['id']}",
    #    "type": "state",
    #    "mediaType": "application/json"
    #}

    for order in orders:
        print(f"posting number {order['posting_number']}")
        print(f"[ms_utils {inspect.currentframe().f_lineno}][ms_create_delivering][{market}] {order['posting_number']} - {ms_orders_dict[order['posting_number']]}")
        order_data = {
            "customerOrder": {
                "meta": {
                  "href": f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/{ms_orders_dict[order['posting_number']]}",
                  "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata",
                  "type": "customerorder",
                  "mediaType": "application/json"
                }
            },
        }
        # Добавляем сформированный заказ в общий список
        try:
            response = requests.put(url_tempalate, headers=moysklad_headers, json=order_data)
            demand_template = response.json()
            demand_template['name'] = order['posting_number']
            response = requests.post(url_demand, headers=moysklad_headers, json=demand_template)

            #logging.info(f"[seller {seller.id}][ms_create_customerorder][response json]: {response.json()}")
            #print(f"*" * 40)
            #print(f"response_json ms_delivering_order: {response.json()}")
            #print(f"*" * 40)
        except requests.exceptions.JSONDecodeError:
            logging.error(f"[seller {seller.id}][ms_create_customerorder][response text]: {response.text}")
        # Дополнительные шаги для обработки результата
        if response.status_code != 200:
            print(f"[ms_utils {inspect.currentframe().f_lineno}] Ошибка: сервер вернул код состояния {response.status_code}\n {response.text}")
            error = response.json()
            code = (
                error.get('errors', [{}])[0].get('code')
                if isinstance(error.get('errors'), list) and error.get('errors')
                else None
            )
            if code == 3006:
                db_update_customerorder(order['posting_number'], mp_mapping[market]['delivering'], seller)
        else:

            db_update_customerorder(order['posting_number'], mp_mapping[market]['delivering'], seller)

            url_status = f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/{ms_orders_dict[order['posting_number']]}"
            status_meta = {
                "href": f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/states/{metadata[db_mapping[market]['status']]['id']}",
                "type": "state",
                "mediaType": "application/json"
            }
            update_data = {
                "state": {
                    "meta": status_meta
                },
            }
            try:
                # меняем статус после добавления отгрузки
                response = requests.put(url_status, headers=moysklad_headers, json=update_data)
            except requests.exceptions.JSONDecodeError:
                logging.error(f"[seller {seller.id}][ms_create_customerorder][response text]: {response.text}")
            if response.status_code != 200:
                print(f">>>[ms_create_delivering][update_data status] - Сервер вернул код состояния {response.status_code}")
                print(f">>>[ms_create_delivering][update_data status] - Ошибка: {response.text}")
                print(f">>>[ms_create_delivering][update_data status] - Metadata: {metadata[db_mapping[market]['status']]['id']}")
            else:
                pass
    return result


def ms_received_order(headers: Dict[str, Any], posting_number: str):
    '''
    Обновляем статус, что заказ доставлен на МС
    '''
    pass




def ms_get_all_stock(headers, metadata):
    '''
    получаем с МойСклад список всех ОСТАТКОВ
    '''
    mapping = {
            'ozon': {'storage': 'ms_storage_ozon'},
            'wb': {'storage': 'ms_storage_wb'},
            'yandex': {'storage': 'ms_storage_yandex'}
    }

    stock_tuple = {}

    url = "https://api.moysklad.ru/api/remap/1.2/report/stock/all"
    store_id = f"https://api.moysklad.ru/api/remap/1.2/entity/store/{metadata[mapping['ozon']['storage']]['id']}"

    params = [
        ("filter", "quantityMode=all"),
        ("filter", f"store={store_id}")
    ]
    response = requests.get(url, headers=headers, params=params).json()
    #print(f'ms_get_all_stock response {response}')
    for stock in response['rows']:
        try:
            stock_tuple[stock['article']] = {
                'stock': int(stock['stock']) - int(stock['reserve']),
                'stock_clear': int(stock['stock']),
                'reserve': int(stock['reserve']),
                'price': stock['salePrice'] / 100
            }
        except KeyError as e:
            print(f"Пропущена запись из-за отсутствующего ключа: {e}")
            continue
    sorted_stock_tuple = OrderedDict(sorted(stock_tuple.items()))
    return sorted_stock_tuple