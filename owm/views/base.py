import datetime

import requests
from django.http import HttpResponseRedirect
from django.shortcuts import render, redirect
from django.views import View
from owm.models import Seller, Crontab, Settings
from owm.utils.base_utils import get_headers, get_store_meta
from owm.utils.ms_utils import ms_get_organization_meta as get_organization_meta
from django.core.exceptions import ObjectDoesNotExist
from owm.utils.db_utils import db_update_metadata, db_get_metadata, db_get_settings, db_update_settings
from owm.utils.ms_utils import ms_update_allstock_to_mp, ms_get_last_enterloss, ms_get_agent_meta, ms_get_organization_meta, ms_get_storage_meta, \
    ms_get_orderstatus_meta, ms_get_product
from owm.utils.oz_utils import (
    ozon_get_products,
    ozon_update_promo,
    ozon_update_price,
)
from owm.utils.ya_utils import yandex_get_products

from itertools import chain
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
import json

import logging

from asgiref.sync import sync_to_async
from celery.result import AsyncResult

logger = logging.getLogger(__name__)
from owm.utils.ya_utils import yandex_get_products
from owm.utils.base_utils import inventory_POST_to_offer_dict, inventory_update
from owm.utils.ms_utils import ms_get_all_stock as get_all_moysklad_stock
from owm.templatetags.custom_filters import sale_qty_get_row_class
from owm.utils.wb_utils import wb_get_products

from itertools import chain
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
import json

import logging

from asgiref.sync import sync_to_async
from celery.result import AsyncResult

logger = logging.getLogger(__name__)

def get_prod_meta(headers, offer_dict):
    url = f'https://api.moysklad.ru/api/remap/1.2/entity/assortment'
    response = requests.get(url, headers=headers).json()
    data = []
    for row in response['rows']:
        if row['article'] in offer_dict:
            data.append({
                "quantity": float(offer_dict[row['article']]['stock']),
                "price": float(offer_dict[row['article']]['price']) * 100,
                "assortment": {
                    "meta": row['meta']
                },
                "overhead": 0
            },)
    return data

# оприходование товара
def update_enter_moysklad(headers, offer_dict):
    url = 'https://api.moysklad.ru/api/remap/1.2/entity/enter'
    data = {
        'store': {"meta": get_store_meta(headers)},
        'organization': {'meta': get_organization_meta(headers)},
        'positions': get_prod_meta(headers, offer_dict)
    }
    responce = requests.post(url=url, json=data, headers=headers)
    print(f"responce moysklad {responce.json()}")

#обновляет толко остаток
def update_enter_yandex(headers, article, count):
    url = 'https://api.partner.market.yandex.ru/campaigns'
    response = requests.get(url, headers=headers).json()
    company_id = response['campaigns'][0]['id']
    businessId = response['campaigns'][0]['business']['id']
    url = f'https://api.partner.market.yandex.ru/businesses/{businessId}/warehouses'
    response = requests.get(url, headers=headers).json()
    warehouseId = response['result']['warehouses'][0]['id']
    url = f'https://api.partner.market.yandex.ru/campaigns/{company_id}/stats/skus'
    data = {
        'shopSkus': [article]
    }
    response = requests.post(url, headers=headers, json=data).json()
    for i in response['result']['shopSkus'][0]['warehouses'][0]['stocks']:
        if i['type'] == 'FIT':
            count += i['count']
            break
    current_time = datetime.datetime.now()
    offset = datetime.timezone(datetime.timedelta(hours=3))
    formatted_time = current_time.replace(tzinfo=offset).isoformat()
    url = f'https://api.partner.market.yandex.ru/campaigns/{company_id}/offers/stocks'
    sku = {
        'sku': article,
        'warehouseId': warehouseId,
        'items': [{
            'count': count,
            'type': 'FIT',
            'updatedAt': formatted_time
        }]
    }
    data = {
        'skus': [sku]
    }
    requests.put(url=url, json=data, headers=headers).json()

# далее идут остальные классы и функции из исходного owm/views.py
# ... оставляем содержимое без изменений ...
def update_enter_yandex(headers, offer_dict):
    url = 'https://api.partner.market.yandex.ru/campaigns'
    response = requests.get(url, headers=headers).json()
    company_id = response['campaigns'][0]['id']
    businessId = response['campaigns'][0]['business']['id']
    url = f'https://api.partner.market.yandex.ru/businesses/{businessId}/warehouses'
    response = requests.get(url, headers=headers).json()
    warehouseId = response['result']['warehouses'][0]['id']
    articles = list(offer_dict.keys())
    url = f'https://api.partner.market.yandex.ru/campaigns/{company_id}/stats/skus'
    data = {
        'shopSkus': articles
    }
    response = requests.post(url, headers=headers, json=data).json()
    current_stocks = {}
    for item in response['result']['shopSkus']:
        article = item['shopSku']
        for warehouse in item['warehouses']:
            if warehouse['warehouseId'] == warehouseId:
                for stock in warehouse['stocks']:
                    if stock['type'] == 'FIT':
                        current_stocks[article] = stock['count']
                        break
    current_time = datetime.datetime.now()
    offset = datetime.timezone(datetime.timedelta(hours=3))
    formatted_time = current_time.replace(tzinfo=offset).isoformat()
    skus = []
    for article, data in offer_dict.items():
        add_count = float(data['stock'])
        current = current_stocks.get(article, 0)
        new_count = current + add_count
        sku = {
            'sku': article,
            'warehouseId': warehouseId,
            'items': [{
                'count': new_count,
                'type': 'FIT',
                'updatedAt': formatted_time
            }]
        }
        skus.append(sku)
    url = f'https://api.partner.market.yandex.ru/campaigns/{company_id}/offers/stocks'
    data = {
        'skus': skus
    }
    requests.put(url, json=data, headers=headers).json()

def update_enter_ozon(headers, offer_dict):
    articles = list(offer_dict.keys())
    url = 'https://api-seller.ozon.ru/v3/product/info/stocks'
    data = {
        'filter': {
            'offer_id': articles
        },
        "limit": 1000
    }
    response = requests.post(url, json=data, headers=headers).json()
    current_stocks = {}
    for item in response['result']['items']:
        article = item['offer_id']
        for stock in item['stocks']:
            if stock['type'] == 'fbs':
                current_stocks[article] = stock['present']
                break
    stocks = []
    for article, data in offer_dict.items():
        add_count = float(data['stock'])
        current = current_stocks.get(article, 0)
        new_count = current + add_count
        stocks.append({
            'offer_id': article,
            'stock': new_count
        })
    url = 'https://api-seller.ozon.ru/v1/product/import/stocks'
    data = {
        'stocks': stocks
    }
    requests.post(url, headers=headers, json=data).json()

def enter_moysklad(user, offer_dict):
    headers = get_headers(user)
    update_enter_moysklad(headers['moysklad_headers'], offer_dict)
    try:
        update_enter_yandex(headers['yandex_headers'], offer_dict)
    except Exception as ex:
        print(f'update_stock_yandex {ex}')
    try:
        update_enter_ozon(headers['ozon_headers'], offer_dict)
    except Exception as ex:
        print(f'update_stock_ozon {ex}')
    try:
        from .wb_views import update_enter_wb
        update_enter_wb(headers['wb_headers'], offer_dict)
    except Exception as ex:
        print(f'update_stock_wb {ex}')
    user.replenishment = False
    user.save()

def enter_POST_to_offer_dict(post):
    offer_dict = {}
    for key, value in post.items():
        if value == 'offer_id':
            stock = post[key+'_stock']
            if float(stock.replace(',', '.')) == 0:
                continue
            price = post[key+'_price']
            offer_dict[key] = {'stock' : stock.replace(',', '.'), 'price' : price.replace(',', '.')}
    return offer_dict

def price_POST_to_offer_dict(post):
    offer_dict = {}
    for key, value in post.items():
        if value == 'offer_id':
            min_price = post[key + '_min_price']
            offer_dict[key] = {'min_price': min_price}
    return offer_dict

class Enter(View):
    async def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        context = {}
        try:
            parser = await sync_to_async(Seller.objects.get)(user=request.user)
            headers = await sync_to_async(get_headers)(parser)
            stock = await sync_to_async(get_all_moysklad_stock)(headers['moysklad_headers'], db_get_metadata(parser))
            context['stock'] = stock
        except ObjectDoesNotExist:
            context['error'] = 'нет api'
        return await sync_to_async(render)(request, 'owm/enter.html', context)

    async def post(self, request):
        offer_dict = await sync_to_async(enter_POST_to_offer_dict)(request.POST.dict())
        parser = await sync_to_async(Seller.objects.get)(user=request.user)
        parser.replenishment = True
        await sync_to_async(parser.save)()
        await sync_to_async(enter_moysklad)(parser, offer_dict)
        return HttpResponseRedirect('store')

class Inventory(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        context = {}
        parser = Seller.objects.get(user=request.user)
        headers = get_headers(parser)
        stock = get_all_moysklad_stock(headers['moysklad_headers'], db_get_metadata(parser))
        context['stock'] = stock
        return render(request, 'owm/inventory.html', context)

    def post(self, request):
        context = {}
        invent_dict = inventory_POST_to_offer_dict(request.POST.dict())
        user = Seller.objects.get(user=request.user)
        context['resp'] = inventory_update(user, invent_dict)
        return render(request, 'owm/inventory.html', context)

class Autoupdate(View):
    async def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        context = {}
        user_company = request.user.userprofile.company
        seller = await sync_to_async(Seller.objects.filter(company=user_company).first)()
        if seller:
            crontab = await sync_to_async(Crontab.objects.filter(seller=seller, name='autoupdate').first)()
            if crontab:
                task_history = await sync_to_async(get_task_history)(crontab.id)
                context['task_history'] = task_history
            else:
                context['message'] = 'Настройки автообновления не найдены для текущего продавца.'
        else:
            context['error'] = 'Продавец не найден для текущего пользователя.'
        return await sync_to_async(render)(request, 'owm/autoupdate/autoupdate.html', context)

class MSMatchingArticle(View):
    def get(self, request, *args, **kwargs):
        context = {}
        if not request.user.is_authenticated:
            return redirect('login')
        user_company = request.user.userprofile.company
        seller = Seller.objects.filter(company=user_company).first()
        settings = db_get_settings(seller=seller, type='matching')
        if seller:
            headers = get_headers(seller)
            ms_arcticle = ms_get_product(headers)
            ms_extracted_data = [
                {"offer_id": item.get("article", "").lower(), "barcodes": [item["barcodes"][0]['ean13']]}
                for item in ms_arcticle['response']['rows']
            ]
            ozon_article = ozon_get_products(headers)
            ozon_extracted_data = [
                {"offer_id": item["offer_id"], "barcodes": item["barcodes"]}
                for item in ozon_article["items"]
            ]
            wb_article = wb_get_products(headers)
            wb_extracted_data = [
                {"offer_id": item["vendorCode"], "barcodes": item["sizes"][0]['skus']}
                for item in wb_article
            ]
            yandex_article = yandex_get_products(headers)
            yandex_extracted_data = [
                {
                    "offer_id": item["offer"]["offerId"],
                    "barcodes": item["offer"]["barcodes"]
                }
                for item in yandex_article
                if "offer" in item and "offerId" in item["offer"] and "barcodes" in item["offer"]
            ]
            all_articles = set(
                chain.from_iterable(
                    [dataset_item.get("offer_id") for dataset_item in dataset if "offer_id" in dataset_item]
                    for dataset in [ozon_extracted_data, ms_extracted_data, wb_extracted_data, yandex_extracted_data]
                )
            )
            combined_data = []
            for current_offer_id in sorted(all_articles):
                ozon_item = next((item for item in ozon_extracted_data if item.get("offer_id") == current_offer_id), None)
                ms_item = next((item for item in ms_extracted_data if item.get("offer_id") == current_offer_id), None)
                wb_item = next((item for item in wb_extracted_data if item.get("offer_id") == current_offer_id), None)
                yandex_item = next((item for item in yandex_extracted_data if item.get("offer_id") == current_offer_id), None)
                barcode_sets = []
                for item in [ozon_item, ms_item, wb_item, yandex_item]:
                    if item and "barcodes" in item:
                        barcodes = item["barcodes"]
                        if isinstance(barcodes, list):
                            barcode_sets.append(set(
                                barcode if isinstance(barcode, str) else str(barcode) for barcode in barcodes
                            ))
                has_match = False
                if len(barcode_sets) == 4:
                    intersection = set.intersection(*barcode_sets)
                    has_match = len(intersection) > 0
                combined_data.append({
                    "offer_id": current_offer_id,
                    "ozon": ozon_item,
                    "ms": ms_item,
                    "wb": wb_item,
                    "yandex": yandex_item,
                    "has_match": has_match,
                })
            intersection_key = settings.get('intersection')
            if intersection_key in ['ozon', 'wb', 'yandex']:
                combined_data = [
                    item for item in combined_data
                    if item.get(intersection_key) is not None
                ]
            sorted_combined_data = sorted(combined_data, key=sort_offer_id_key)
            context = {
                "combined_data": sorted_combined_data,
            }
        return render(request, 'owm/ms/ms_matching_article.html', context)

    def post(self, request):
        pass

def sort_offer_id_key(item):
    offer_id = item["offer_id"]
    num_part_str = ''.join(filter(str.isdigit, offer_id.split('_')[0]))
    num_part = int(num_part_str) if num_part_str else 0
    text_part = offer_id[len(str(num_part)):]
    return (text_part, num_part)

class AutoupdateSettings(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        user_company = request.user.userprofile.company
        seller = Seller.objects.filter(company=user_company).first()
        context = {}
        obj = Crontab.objects.filter(seller=seller, name='autoupdate').first()
        if obj:
            context['active_yandex'] = obj.yandex
            context['active_ozon'] = obj.ozon
            context['active_wb'] = obj.wb
            try:
                headers = get_headers(seller)
            except Exception as e:
                print("AutoupdateSettings Error occurred:", e)
            cron_data = {
                'cron_dict': obj.crontab_dict,
            }
        else:
            Crontab.objects.create(seller=seller, name='autoupdate')
            context['settings'] = False
            print(f"Created new Crontab")
        return render(request, 'owm/autoupdate/autoupdate_settings.html', context)

    def post(self, request):
        context = {}
        user_company = request.user.userprofile.company
        seller = Seller.objects.filter(company=user_company).first()
        form_type = request.POST.get("form_type")
        crontab = Crontab.objects.get(seller=seller, name='autoupdate')
        if form_type == "save_settings":
            sync_checkbox = request.POST.get('sync_checkbox', False)
            sync_checkbox_ozon = request.POST.get('sync_checkbox_ozon', False)
            sync_checkbox_yandex = request.POST.get('sync_checkbox_yandex', False)
            sync_checkbox_wb = request.POST.get('sync_checkbox_wb', False)
            if sync_checkbox == 'on':
                crontab.active = True
                context['active'] = True
                print("Checkbox is checked")
            else:
                crontab.active = False
                context['active'] = False
                print("Checkbox is not checked")
            if sync_checkbox_ozon == 'on':
                crontab.ozon = True
                context['active_ozon'] = True
            else:
                crontab.ozon = False
                context['active_ozon'] = False
            if sync_checkbox_yandex == 'on':
                crontab.yandex = True
                context['active_yandex'] = True
            else:
                crontab.yandex = False
                context['active_ozon'] = False
            if sync_checkbox_wb == 'on':
                crontab.wb = True
                context['active_wb'] = True
            else:
                crontab.wb = False
                context['active_wb'] = False
            crontab.save()
        elif form_type == "sync_start":
            try:
                headers = get_headers(seller)
            except Exception as e:
                print("Error occurred:", e)
            context['update_data'] = ms_update_allstock_to_mp(headers=headers, seller=seller)
            codes = [context['update_data']['ozon']['code'], context['update_data']['yandex']['code'], context['update_data']['wb']['code']]
            if all(code in (200, 204, 409) for code in codes):
                result_dict = ms_get_last_enterloss(headers=headers)
                crontab.crontab_dict = result_dict
                crontab.save()
        context['sync_update'] = True
        return render(request, 'owm/autoupdate/autoupdate_settings.html', context)

class SettingsApi(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)
        api_list_current_user = Seller.objects.filter(company=user_company).first()
        return render(request, 'owm/settings/settings_api.html', {'api_list_current_user': api_list_current_user})

    def post(self, request, *args, **kwargs):
        try:
            moysklad_api = request.POST.get('moysklad_api')
            yandex_api = request.POST.get('yandex_api')
            wildberries_api = request.POST.get('wildberries_api')
            client_id = request.POST.get('client_id')
            ozon_api = request.POST.get('ozon_api')
            user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)
            user_api_object = Seller.objects.filter(company=user_company)
            if user_api_object:
                user_api_object.update(
                    moysklad_api=moysklad_api,
                    yandex_api=yandex_api,
                    wildberries_api=wildberries_api,
                    client_id=client_id,
                    ozon_api=ozon_api,
                )
            else:
                Seller.objects.update_or_create(
                    company=user_company,
                    moysklad_api=moysklad_api,
                    yandex_api=yandex_api,
                    wildberries_api=wildberries_api,
                    client_id=client_id,
                    ozon_api=ozon_api,
                )
            return HttpResponseRedirect('')
        except Exception as ex:
            print('exc ', str(ex))
            return render(request, 'owm/settings/settings_api.html')

class SettingsContragent(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        context = {}

        user_company = request.user.userprofile.company
        seller = Seller.objects.filter(company=user_company).first()

        if seller:
            headers = get_headers(seller)

            meta_all = db_get_metadata(seller=seller)

            required_keys = ['ms_ozon_contragent', 'ms_wb_contragent', 'ms_yandex_contragent', 'ms_organization']

            for key in required_keys:
                if key in meta_all:
                    context[key] = meta_all[key]

            context['agentlist'] = ms_get_agent_meta(headers)

            context['orglist'] = ms_get_organization_meta(headers)
        else:
            context['DoesNotExist'] = True
        return render(request, 'owm/settings/settings_contragent.html', context)

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        context = {}
        metadata={}
        metadata['ms_organization'] = {'id': request.POST.get('organization_select'), 'name': request.POST.get('hidden-organization')}
        metadata['ms_wb_contragent'] = {'id': request.POST.get('wb_select'), 'name': request.POST.get('hidden-wb')}
        metadata['ms_ozon_contragent'] = {'id': request.POST.get('ozon_select'), 'name': request.POST.get('hidden-ozon')}
        metadata['ms_yandex_contragent'] = {'id': request.POST.get('yandex_select'), 'name': request.POST.get('hidden-yandex')}

        seller = Seller.objects.get(company__userprofile__user=request.user)

        db_update_metadata(seller=seller, metadata=metadata)

        return redirect('settings_contragent')

class SettingsStorage(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        context = {}

        user_company = request.user.userprofile.company

        seller = Seller.objects.filter(company=user_company).first()

        headers = get_headers(seller)

        metadata = db_get_metadata(seller=seller.id)

        required_keys = ['ms_storage_ozon', 'ms_storage_wb', 'ms_storage_yandex']

        for key in required_keys:

            if key in metadata:

                context[key] = metadata[key]

        context['storagelist'] = ms_get_storage_meta(headers)

        return render(request, 'owm/settings/settings_storage.html', context)

    def post(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect('login')

        context = {}

        metadata={}

        metadata['ms_storage_wb'] = {'id': request.POST.get('wb_select'), 'name': request.POST.get('hidden-wb')}

        metadata['ms_storage_ozon'] = {'id': request.POST.get('ozon_select'), 'name': request.POST.get('hidden-ozon')}

        metadata['ms_storage_yandex'] = {'id': request.POST.get('yandex_select'), 'name': request.POST.get('hidden-yandex')}

        user_company = request.user.userprofile.company

        seller = Seller.objects.filter(company=user_company).first()

        db_update_metadata(seller=seller, metadata=metadata)

        return redirect('settings_storage')

class SettingsStatus(View):

    def get(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect('login')

        context = {}

        user_company = request.user.userprofile.company

        seller = Seller.objects.filter(company=user_company).first()

        headers = get_headers(seller)

        metadata = db_get_metadata(seller=seller.id)

        required_keys = ['ms_status_awaiting',

                         'ms_status_shipped',

                         'ms_status_completed',

                         'ms_status_cancelled']

        for key in required_keys:

            if key in metadata:

                context[key] = metadata[key]

        context['statuslist'] = ms_get_orderstatus_meta(headers)

        return render(request, 'owm/settings/settings_status.html', context)

    def post(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect('login')

        user_company = request.user.userprofile.company

        seller = Seller.objects.filter(company=user_company).first()

        metadata={}

        metadata['ms_status_awaiting'] = {'id': request.POST.get('awaiting_select'), 'name': request.POST.get('hidden-awaiting')}

        metadata['ms_status_shipped'] = {'id': request.POST.get('shipped_select'), 'name': request.POST.get('hidden-shipped')}

        metadata['ms_status_completed'] = {'id': request.POST.get('completed_select'), 'name': request.POST.get('hidden-completed')}

        metadata['ms_status_cancelled'] = {'id': request.POST.get('cancelled_select'), 'name': request.POST.get('hidden-cancelled')}

        db_update_metadata(seller=seller, metadata=metadata)

        return redirect('settings_status')

class SettingsMatchingArticle(View):

    def get(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect('login')

        context = {}

        user_company = request.user.userprofile.company

        seller = Seller.objects.filter(company=user_company).first()

        if seller:

            parser_data = {

                'moysklad_api': seller.moysklad_api,

                'yandex_api': seller.yandex_api,

                'wildberries_api': seller.wildberries_api,

                'ozon_api': seller.ozon_api,

                'ozon_id': seller.client_id,

            }

            headers = get_headers(parser_data)

            db_settings = db_get_settings(seller=seller.id, type='matching')

            if db_settings:

                context = db_settings

            else:

                settings_dict = {'ms': False, 'ozon': False, 'wb': False, 'yandex': False, 'intersection': 'off'}

                Settings.objects.create(seller=seller, type='matching', settings_dict=settings_dict)

                context = settings_dict

        else:

            context['DoesNotExist'] = True

        print(f'context {context}')

        return render(request, 'owm/settings/settings_matching.html', context)

    def post(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect('login')

        context = {}

        sett={}

        sett['ms'] = (request.POST.get('ms') == 'True')

        sett['wb'] = (request.POST.get('wb') == 'True')

        sett['ozon'] = (request.POST.get('ozon') == 'True')

        sett['yandex'] = (request.POST.get('yandex') == 'True')

        sett['intersection'] = request.POST.get('intersection')

        user_company = request.user.userprofile.company

        seller = Seller.objects.filter(company=user_company).first()

        db_update_settings(seller=seller, type='matching', settings_dict=sett)

        return redirect('settings_matching_article')


class PriceYandex(View):

    def get(self, request, *args, **kwargs):

        context = {}

        parser = Seller.objects.get(user=request.user)

        headers = get_headers(parser)

        price = get_all_price_yandex(headers)

        context['price'] = price

        return render(request, 'owm/price_yandex.html', context)

    def post(self, request, *args, **kwargs):

        context = {}

        parser = Seller.objects.get(user=request.user)

        offer_dict = price_POST_to_offer_dict(request.POST.dict())

        update_price_ozon(parser, offer_dict)

        return render(request, 'owm/price_wb.html', context)

##### AJAX

@csrf_exempt

def ajax_request_promo(request):

    """Handle AJAX promo price update"""

    if not request.user.is_authenticated:

        return JsonResponse({'success': False, 'error': 'Not authenticated'}, status=401)

    user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)

    seller = Seller.objects.filter(company=user_company).first()

    if not seller:

        return JsonResponse({'success': False, 'error': 'Seller not found'}, status=404)

    headers = get_headers(seller)

    if request.method == "POST":

        try:

            data = json.loads(request.body)

            market = data.get('market')

            promo_data = {

                'offer_id': data.get('offer_id'),

                'yourprice': data.get('yourprice'),

                'minprice': data.get('minprice'),

                'min_price_fbs': data.get('min_price_fbs'),

                'min_price_limit_count': data.get('min_price_limit_count'),

                'min_price_promo': data.get('min_price_promo'),

                'limit_count_value': data.get('limit_count_value'),

                'use_fbs': data.get('use_fbs', False),

                'use_limit_count': data.get('use_limit_count', False),

                'use_promo': data.get('use_promo', False),

                'autoupdate_promo': data.get('autoupdate_promo', False),

                'auto_update_days_limit_promo': data.get('auto_update_days_limit_promo', False),

                'use_discount': data.get('use_discount', False),

                'min_price_discount': data.get('min_price_discount'),

                'market': data.get('market')

            }

            #print(f"promo_data: {promo_data}")

            if market == 'ozon':

                promo_result = ozon_update_promo(promo_data=promo_data, seller=seller, headers=headers)

                promo_payload = json.loads(promo_result.content.decode())

                if not promo_payload.get('success', False):

                    return promo_result

                price_payload = {

                    'offer_id': data.get('offer_id'),

                    'yourprice': data.get('yourprice'),

                    'minprice': data.get('minprice'),

                }

                if price_payload['yourprice'] or price_payload['minprice']:

                    price_result = ozon_update_price(

                        price_data=price_payload,

                        seller=seller,

                        headers=headers,

                    )

                    price_payload_response = json.loads(price_result.content.decode())

                    if not price_payload_response.get('success', False):

                        return price_result

            return JsonResponse({'success': True})

        except Exception as e:

            print(f"data: {data}")

            return JsonResponse({'success': False, 'error': str(e)})

    else:

        return JsonResponse({'success': False, 'error': 'Invalid request method'})


