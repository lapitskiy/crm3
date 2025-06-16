from django.urls import path

from .views import Enter, Inventory, PriceOzon, PriceWb, PriceYandex, FinanceOzon, PostavkaOzon, FinanceWb, AutoupdateSettings, OtpravlenieOzon, PromotionOzon, SettingsApi, \
    SettingsContragent, SettingsStorage, SettingsStatus, MSMatchingArticle, SettingsMatchingArticle, ajax_request_promo

urlpatterns = [
    path('', SettingsApi.as_view(), name='settings_api'),
    path('settings_contragent/', SettingsContragent.as_view(), name='settings_contragent'),
    path('settings_storage/', SettingsStorage.as_view(), name='settings_storage'),
    path('settings_status/', SettingsStatus.as_view(), name='settings_status'),

    path('ms_matching_article/', MSMatchingArticle.as_view(), name='ms_matching_article'),
    path('settings_matching_article/', SettingsMatchingArticle.as_view(), name='settings_matching_article'),


    path('inventory/', Inventory.as_view(), name='inventory'),
    path('autoupdatesettings/', AutoupdateSettings.as_view(), name='autoupdate_settings'),
    path('price_ozon/', PriceOzon.as_view(), name='price_ozon'),
    path('promotion_ozon/', PromotionOzon.as_view(), name='promotion_ozon'),        
    path('finance_ozon/', FinanceOzon.as_view(), name='finance_ozon'),
    path('postavka_ozon/', PostavkaOzon.as_view(), name='postavka_ozon'),
    path('otpravlenie_ozon/', OtpravlenieOzon.as_view(), name='otpravlenie_ozon'),
    path('price_wb/', PriceWb.as_view(), name='price_wb'),
    path('finance_wb/', FinanceWb.as_view(), name='finance_wb'),
    path('wb_promo/', WbPromo.as_view(), name='wb_promo'),        
    path('price_yandex/', PriceYandex.as_view(), name='price_yandex'),
    path('enter/', Enter.as_view(), name='enter'),
    path('ajax-request-promo/', ajax_request_promo, name='ajax_request_promo'),
    
]



