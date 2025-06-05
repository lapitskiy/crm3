from django.db import models
from django.contrib.auth.models import User

# python manage.py makemigrations
# python manage.py migrate
from users.models import Company

class Seller(models.Model):
    company = models.OneToOneField(Company, on_delete=models.CASCADE, null=True, blank=True)
    moysklad_api = models.CharField(max_length=512, unique=True, verbose_name='API мойсклад')
    yandex_api = models.CharField(max_length=512, unique=True, verbose_name='API Яндекс')
    wildberries_api = models.CharField(max_length=512, unique=True, verbose_name='API wildberries')
    client_id = models.CharField(max_length=512, unique=True, verbose_name='Client Id Ozon')
    ozon_api = models.CharField(max_length=512, unique=True, verbose_name='API Ozon')
    stock_update_at = models.DateTimeField(blank=True, null=True, verbose_name='Последняя инвентаризация')

class Crontab(models.Model):
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, verbose_name='Связанный парсер')
    name = models.CharField(max_length=150, null=True, blank=True)
    yandex = models.BooleanField(default=False, verbose_name='yandex')
    ozon = models.BooleanField(default=False, verbose_name='ozon')
    wb = models.BooleanField(default=False, verbose_name='wb')
    crontab_dict = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['seller', 'name'], name='unique_crontab')
        ]

class Awaiting(models.Model):
    '''
    status:
    awaiting_deliver - ожидает отгрузки
    '''
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, verbose_name='Связанный парсер')
    posting_number = models.CharField(max_length=30, null=False, unique=True)
    status = models.CharField(max_length=30, null=False)
    market = models.CharField(max_length=30, null=False) #ozon, wb, yandex

class Awaiting_product(models.Model):
    awaiting = models.ForeignKey(Awaiting, on_delete=models.CASCADE, verbose_name='Связанный заказ')
    offer_id = models.CharField(max_length=50, null=False)
    price = models.IntegerField(null=False, verbose_name='Цена')
    quantity = models.IntegerField(null=False, verbose_name='Количество')


# Ваша логика верная: 
# 1. Первый класс — настройки для продавца и маркетплейса (PromoMarketSettings).
# 2. Второй класс — настройки для каждого товара на этом маркетплейсе (PromoProduct).

class PromoMarketSettings(models.Model):
    """
    Настройки акций для продавца на конкретном маркетплейсе.
    """
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name='promo_market_settings')
    market = models.CharField(max_length=50, verbose_name='Маркетплейс')

    class Meta:
        unique_together = ('seller', 'market')
        verbose_name = 'Настройки акций маркетплейса'
        verbose_name_plural = 'Настройки акций маркетплейсов'

class PromoProduct(models.Model):
    """
    Настройки участия товара в акциях для конкретного продавца и маркетплейса.
    """
    promo_market = models.ForeignKey(PromoMarketSettings, on_delete=models.CASCADE, related_name='products')
    offer_id = models.CharField(max_length=50, verbose_name='ID товара')
    min_price_fbs = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Мин. цена FBS')
    disable_fbs = models.BooleanField(default=False, verbose_name='Отключить FBS')
    min_price_promo = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Мин. цена для акции')
    disable_promo = models.BooleanField(default=False, verbose_name='Отключить участие в акции')

    class Meta:
        unique_together = ('promo_market', 'offer_id')
        verbose_name = 'Настройки акции товара'
        verbose_name_plural = 'Настройки акций товаров'

# ...existing code...

class Metadata(models.Model):
    '''
    name:
    ms_storage_ozon - склад для озон
    ms_storage_wb - склад для wb
    ms_storage_yandex - склад для yandex
    ms_organization - юр.лицо или название компании
    ms_ozon_contragent - метадата данные контрагент озон
    ms_yandex_contragent - метадата данные контрагент яндекс
    ms_wb_contragent - метадата данные контрагент wb
    ms_status_awaiting - мета статус заказа покупателя
    ms_status_shipped - мета статус заказа покупателя
    ms_status_completed - мета статус заказа покупателя
    ms_status_cancelled - мета статус заказа покупателя
    '''
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, verbose_name='Связанный парсер')
    name = models.CharField(max_length=50, null=False)
    metadata_dict = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['seller', 'name'], name='unique_metadata')
        ]

class Settings(models.Model):
    '''
        type:
        matching - сопоставление default = {'ms': False 'ozon': False, 'wb': False, 'yandex': False, 'intersection': 'ms'}
    '''
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, verbose_name='Связанный парсер')
    type = models.CharField(max_length=150, null=True, blank=True)
    settings_dict = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['seller', 'type'], name='unique_settings')
        ]




