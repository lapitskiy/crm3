
import logging
import json
from django.shortcuts import render, redirect
from django.views import View
from django.http import JsonResponse, HttpResponse
from asgiref.sync import sync_to_async
from django.views.decorators.csrf import csrf_exempt

from ..models import Seller, Crontab
from ..utils.base_utils import get_headers
from ..utils.oz_utils import (
    ozon_get_finance,
    ozon_get_all_price,
    ozon_get_postavka,
    ozon_update_inventory,
    ozon_get_awaiting_fbs as get_otpravlenie_ozon,
)
from ..utils.db_utils import db_get_promo_products
from ..templatetags.custom_filters import sale_qty_get_row_class
from django.core.exceptions import ObjectDoesNotExist

# Импорты для OtpravlenieOzon.post
from owm.utils.base_utils import get_reserv_from_mp
from owm.utils.ms_utils import ms_update_allstock_to_mp as update_stock_mp_from_ms
from .base import price_POST_to_offer_dict
from ..utils.ms_utils import ms_get_last_enterloss as autoupdate_get_last_sync_acquisition_writeoff_ms

logger = logging.getLogger(__name__)

class OtpravlenieOzon(View):

    async def get(self, request, *args, **kwargs):

        context = {}

        try:

            parser = await sync_to_async(Seller.objects.get)(user=request.user)

            parser_data = {

                'moysklad_api': parser.moysklad_api,

                'yandex_api': parser.yandex_api,

                'wildberries_api': parser.wildberries_api,

                'ozon_api': parser.ozon_api,

                'ozon_id': parser.client_id,

            }

            headers = await sync_to_async(get_headers)(parser_data)

            result = await get_otpravlenie_ozon(headers)

            context['otpravlenie'] = result['awaiting']

            context['packag'] = result['packag']

        except ObjectDoesNotExist:

            context['error'] = 'нет api'

        return await sync_to_async(render)(request, 'owm/otpravlenie_ozon.html', context)

    async def post(self, request):

        context = {}

        form_type = request.POST.get("form_type")

        crontab = await sync_to_async(Crontab.objects.get)(seller__user=request.user, name='autoupdate')

        if form_type == "save_settings":

            sync_checkbox = request.POST.get('sync_checkbox', False)

            sync_checkbox_ozon = request.POST.get('sync_checkbox_ozon', False)

            sync_checkbox_yandex = request.POST.get('sync_checkbox_yandex', False)

            sync_checkbox_wb = request.POST.get('sync_checkbox_wb', False)

            if sync_checkbox == 'on':

                crontab.active = True

                context['active'] = True

            else:

                crontab.active = False

                context['active'] = False

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

            mp_reserv = request.POST.get('mp_reserv', False)

            if mp_reserv == 'on':

                reserv_dict = get_reserv_from_mp(headers=headers)

            else:

                parser = await sync_to_async(lambda: crontab.seller)()

                parser_data = {

                    'moysklad_api': parser.moysklad_api,

                    'yandex_api': parser.yandex_api,

                    'wildberries_api': parser.wildberries_api,

                    'ozon_api': parser.ozon_api,

                    'ozon_id': parser.client_id,

                }

                headers = await sync_to_async(get_headers)(parser_data)

                context['update_data'] = update_stock_mp_from_ms(headers=headers)

                codes = [context['update_data']['wb']['code'], context['update_data']['wb']['code'], context['update_data']['yandex']['code']]

                if all(code in (200, 204) for code in codes):

                    context['sync_update'] = True

                    result_dict = await autoupdate_get_last_sync_acquisition_writeoff_ms(headers=headers)

                    crontab.crontab_dict = result_dict

                    await sync_to_async(crontab.save)()

        return await sync_to_async(render)(request, 'owm/autoupdate_settings.html', context)

class PostavkaOzon(View):

    def get(self, request, *args, **kwargs):

        context = {}

        user_company = request.user.userprofile.company

        parser = Seller.objects.filter(company=user_company).first()

        headers = get_headers(parser)

        data = ozon_get_postavka(headers)

        context['row'] = data['row']

        context['path'] = data['path']

        context['code'] = data['code']

        return render(request, 'owm/postavka_ozon.html', context)

    def post(self, request, *args, **kwargs):

        context = {}

        parser = Seller.objects.get(user=request.user)

        offer_dict = price_POST_to_offer_dict(request.POST.dict())

        # Функция update_price_ozon отсутствует в oz_utils.py
        # TODO: Реализовать обновление цен для Ozon

        return render(request, 'owm/finance_ozon.html', context)

class FinanceOzon(View):

    def get(self, request, *args, **kwargs):

        logger.info("FinanceOzon.get called")

        context = {}

        if not request.user.is_authenticated:

            logger.warning("FinanceOzon.get: User not authenticated, redirecting to login.")

            return redirect('login')

        user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)

        logger.info(f"FinanceOzon.get: User company: {user_company}")

        seller = Seller.objects.filter(company=user_company).first()

        logger.info(f"FinanceOzon.get: Seller found: {seller is not None}")

        headers = get_headers(seller)

        logger.info(f"FinanceOzon.get: Headers obtained: {list(headers.keys()) if headers else 'None'}")

        data = ozon_get_finance(headers, period='month')

        logger.info(f"FinanceOzon.get: Data from ozon_get_finance: {'error' in data}")

        if 'error' not in data:

            context['report'] = data['sorted_report']

            context['summed_totals'] = data['summed_totals']

            context['all_totals'] = data['all_totals']

            context['header_data'] = data['header_data']

        else:

            logger.error(f"FinanceOzon.get: Error from ozon_get_finance: {data['error']}") 

            context['error'] = data

        logger.info("FinanceOzon.get: Rendering response.")

        return render(request, 'owm/finance_ozon.html', context)

    def post(self, request, *args, **kwargs):

        context = {}

        parser = Seller.objects.get(user=request.user)

        offer_dict = price_POST_to_offer_dict(request.POST.dict())

        # Функция update_price_ozon отсутствует в oz_utils.py
        # TODO: Реализовать обновление цен для Ozon

        return render(request, 'owm/finance_ozon.html', context)
    
class PromotionOzon(View):

    def get(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect('login')

        try:

            user_company = getattr(getattr(request.user, 'userprofile', None), 'company', None)

            seller = Seller.objects.filter(company=user_company).first()

            if not seller:

                print(f"Seller not found for company: {user_company}")

                return render(request, 'owm/promotion_ozon.html', {'error': 'Seller not found'})

            context = {}

            headers = get_headers(seller)

            print(f"Headers obtained: {list(headers.keys()) if headers else 'None'}")

            db_promo_data = db_get_promo_products(seller=seller)

            print(f"DB promo data obtained: {len(db_promo_data) if db_promo_data else 0} items")

            context['db_data'] = db_promo_data

            price = ozon_get_all_price(headers)

            print(f"Price data obtained: {len(price) if price and isinstance(price, dict) else 'Error'}")

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

                context['price'] = price #dict(list(price.items())[:1]) # price

            return render(request, 'owm/promotion_ozon.html', context)

        except Exception as e:

            print(f"Error in PromotionOzon view: {str(e)}")

            import traceback

            traceback.print_exc()

            return render(request, 'owm/promotion_ozon.html', {'error': f'Error: {str(e)}'})

    def post(self, request, *args, **kwargs):

        context = {}

        parser = Seller.objects.get(user=request.user)

        offer_dict = price_POST_to_offer_dict(request.POST.dict())

        # Функция update_price_ozon отсутствует в oz_utils.py
        # TODO: Реализовать обновление цен для Ozon

        return render(request, 'owm/price_ozon.html', context)
    
class PriceOzon(View):

    def get(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect('login')

        user_company = request.user.userprofile.company

        seller = Seller.objects.filter(company=user_company).first()

        context = {}

        headers = get_headers(seller)

        price = ozon_get_all_price(headers)

        context['price'] = price

        return render(request, 'owm/price_ozon.html', context)

    def post(self, request, *args, **kwargs):

        context = {}

        parser = Seller.objects.get(user=request.user)

        offer_dict = price_POST_to_offer_dict(request.POST.dict())

        # Функция update_price_ozon отсутствует в oz_utils.py
        # TODO: Реализовать обновление цен для Ozon

        return render(request, 'owm/price_ozon.html', context)
