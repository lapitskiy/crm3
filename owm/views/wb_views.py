import logging
import requests
import datetime

from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views import View

from ..models import Seller
from ..utils.base_utils import get_headers
from ..templatetags.custom_filters import sale_qty_get_row_class
from ..utils.db_utils import db_get_promo_products
from ..utils.wb_utils import wb_get_all_price, wb_get_products
from ..utils.io_utils import read_min_prices, update_min_prices
# Функция update_price_ozon отсутствует в oz_utils.py
from ..views.base import price_POST_to_offer_dict

logger = logging.getLogger(__name__)


class WbPromo(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        try:
            user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)
            seller = Seller.objects.filter(company=user_company).first()

            if not seller:
                return render(request, 'owm/wb/promotion_wb.html', {'error': 'Seller not found'})

            context = {}
            headers = get_headers(seller)
            db_promo_data = db_get_promo_products(seller=seller)
            context['db_data'] = db_promo_data

            price = wb_get_all_price(headers)
            try:
                if isinstance(price, dict):
                    first_two = list(price.items())[:2]
                    logger.info("№№№№№№№№№№№")
                    logger.info("WbPromo.get: first_two_price_items=%s", first_two)
                else:
                    logger.info("WbPromo.get: price is not dict: type=%s value_preview=%s", type(price), str(price)[:500])
            except Exception as error:
                logger.warning("WbPromo.get: error logging price preview: %s", error)

            percent_color = request.GET.get('percent_color')
            if percent_color and price:
                filtered_price = {}
                for offer_id, value in price.items():
                    row_class_tuple = sale_qty_get_row_class(value.get('profit_percent_fbs'))
                    if percent_color == 'green' and row_class_tuple[0] == '#bff5a6':
                        filtered_price[offer_id] = value
                    elif percent_color == 'yellow' and row_class_tuple[0] == '#eef3ac':
                        filtered_price[offer_id] = value
                    elif percent_color == 'red' and row_class_tuple[0] == '#ffc0d3':
                        filtered_price[offer_id] = value
                context['price'] = filtered_price
            else:
                context['price'] = price

            return render(request, 'owm/wb/promotion_wb.html', context)

        except Exception as exc:
            import traceback
            traceback.print_exc()
            return render(request, 'owm/wb/promotion_wb.html', {'error': f'Error: {str(exc)}'})

    def post(self, request, *args, **kwargs):
        context = {}
        parser = Seller.objects.get(user=request.user)
        offer_dict = price_POST_to_offer_dict(request.POST.dict())
        # Функция update_price_ozon отсутствует, заменена на заглушку
        # TODO: Реализовать обновление цен для WB
        return render(request, 'owm/wb/promotion_wb.html', context)


class PriceWb(View):
    def get(self, request, *args, **kwargs):
        context = {}
        parser = Seller.objects.get(user=request.user)
        headers = get_headers(parser)
        price = wb_get_all_price(headers)
        context['price'] = price
        return render(request, 'owm/price_wb.html', context)

    def post(self, request, *args, **kwargs):
        context = {}
        parser = Seller.objects.get(user=request.user)
        offer_dict = price_POST_to_offer_dict(request.POST.dict())
        # Функция update_price_ozon отсутствует, заменена на заглушку
        # TODO: Реализовать обновление цен для WB
        return render(request, 'owm/price_wb.html', context)


class WbMinimalPrice(View):
    def get(self, request, *args, **kwargs):
        # Проверяем аутентификацию и получаем компанию пользователя
        if not request.user.is_authenticated:
            return redirect('login')
        user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)
        seller = Seller.objects.filter(company=user_company).first()
        if not seller:
            return render(request, 'owm/wb/wb_minimal_price.html', {'error': 'Seller not found'})
        # Получаем заголовки и товары WB
        headers = get_headers(seller)
        products = wb_get_products(headers)
        # Читаем новые минимальные цены из Excel
        min_price_map = read_min_prices()
        # Формируем список для отображения
        items = []
        for p in products:
            article_wb = p.get('nmID')
            seller_article = p.get('vendorCode')
            sizes = p.get('sizes', [])
            barcode = sizes[-1].get('skus', [None])[0] if sizes else None
            new_min_price = min_price_map.get(str(seller_article))
            items.append({
                'article_wb': article_wb,
                'seller_article': seller_article,
                'barcode': barcode,
                'new_min_price': new_min_price
            })
        return render(request, 'owm/wb/wb_minimal_price.html', {'items': items, 'updates': {}})
    def post(self, request, *args, **kwargs):
        # Проверяем аутентификацию и получаем продавца
        if not request.user.is_authenticated:
            return redirect('login')
        user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)
        seller = Seller.objects.filter(company=user_company).first()
        if not seller:
            return render(request, 'owm/wb/wb_minimal_price.html', {'error': 'Seller not found'})
        # Готовим список товаров для отображения и обновления
        headers = get_headers(seller)
        products = wb_get_products(headers)
        # Читаем текущие минимальные цены из Excel
        min_price_map = read_min_prices()
        # Собираем отправленные новые минимальные цены
        updates = {}
        for key, value in request.POST.items():
            if key.startswith('new_min_price_'):
                seller_article = key.replace('new_min_price_', '')
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    price = None
                updates[seller_article] = price
        # Формируем список товаров для отображения и обновления
        items = []
        for p in products:
            article_wb = p.get('nmID')
            seller_article = p.get('vendorCode')
            sizes = p.get('sizes', [])
            barcode = sizes[-1].get('skus', [None])[0] if sizes else None
            # Определяем новую минимальную цену: сначала из updates, иначе из исходного файла
            new_min_price = updates.get(seller_article, min_price_map.get(str(seller_article)))
            items.append({
                'article_wb': article_wb,
                'seller_article': seller_article,
                'barcode': barcode,
                'new_min_price': new_min_price
            })
        # Обновляем Excel-файл новыми минимальными ценами, добавляя отсутствующие строки
        update_min_prices(updates, items)
        # Передаём результаты обновления и исходный список в шаблон
        return render(request, 'owm/wb/wb_minimal_price.html', {'items': items, 'updates': updates, 'message': 'Цены обновлены'})



class FinanceWb(View):
    def get(self, request, *args, **kwargs):
        context = {}
        if not request.user.is_authenticated:
            return redirect('login')
            
        try:
            user_company = request.user.userprofile.company
            parser = Seller.objects.filter(company=user_company).first()
        except TypeError:
            return HttpResponse('Пользователь не аутентифицирован', status=401)

        # Функция wb_get_finance отсутствует в wb_utils.py, 
        # поэтому создаем базовый контекст для отображения
        context['path'] = {}
        context['code'] = 0
        context['date'] = datetime.datetime.now().strftime('%Y-%m-%d')
        context['report'] = {}
        context['summed_totals'] = {}
        context['all_totals'] = {}
        
        # Здесь должна быть логика получения финансовых данных WB
        # TODO: Реализовать функцию получения финансовых данных

        return render(request, 'owm/finance_wb.html', context)

    def post(self, request, *args, **kwargs):
        context = {}
        parser = Seller.objects.get(user=request.user)
        offer_dict = price_POST_to_offer_dict(request.POST.dict())
        # Функция update_price_ozon отсутствует, заменена на заглушку
        # TODO: Реализовать обновление цен для WB
        return render(request, 'owm/finance_wb.html', context)

def update_enter_wb(headers, offer_dict):
    barcodes = {}
    for article in offer_dict:
        url = 'https://suppliers-api.wildberries.ru/content/v2/get/cards/list'
        data = {
            'settings': {
                'cursor': {
                    'limit': 1
                },
                'filter': {
                    'textSearch': article
                }
            }
        }
        response = requests.post(url, json=data, headers=headers).json()
        try:
            barcode = response['cards'][0]['sizes'][0]['skus'][0]
        except (IndexError, KeyError):
            continue
        barcodes[article] = barcode
    url = 'https://suppliers-api.wildberries.ru/api/v3/warehouses'
    response = requests.get(url, headers=headers).json()
    warehouseId = response[0]['id']
    all_barcodes = list(barcodes.values())
    url = f'https://suppliers-api.wildberries.ru/api/v3/stocks/{warehouseId}'
    data = {
        'skus': all_barcodes
    }
    response = requests.post(url, json=data, headers=headers).json()
    current_amounts = {}
    for item in response['stocks']:
        current_amounts[item['sku']] = item['amount']
    stocks = []
    for article, data in offer_dict.items():
        if article not in barcodes:
            continue
        barcode = barcodes[article]
        add_count = float(data['stock'])
        current = current_amounts.get(barcode, 0)
        new_amount = current + add_count
        stocks.append({
            'sku': barcode,
            'amount': new_amount
        })
    url = f'https://suppliers-api.wildberries.ru/api/v3/stocks/{warehouseId}'
    data = {
        'stocks': stocks
    }
    response = requests.put(url, json=data, headers=headers)
    return response.json()

