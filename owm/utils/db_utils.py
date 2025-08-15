import contextlib
from contextlib import contextmanager
from owm.models import Awaiting, Awaiting_product, Metadata, Settings, Seller
from typing import Any, Dict

import redis
from owm.models import PromoMarket, PromoProduct

redis_client = redis.Redis(
    host='redis',     # имя сервиса в docker-compose
    port=6379,
    db=0,
    password='Billkill13',
    decode_responses=True  # удобно, чтобы строки не были в байтах
)

@contextmanager
def redis_lock(lock_name, timeout=180):
    lock = redis_client.lock(lock_name, timeout=timeout)
    have_lock = lock.acquire(blocking=False)
    try:
        yield have_lock
    finally:
        if have_lock and lock.locked():
            try:
                lock.release()
            except redis.exceptions.LockNotOwnedError:
                pass

DATABASE_URL = "postgresql+asyncpg://crm3:Billkill13@postgres:5432/postgres"



# Создаем движок

#engine = create_async_engine(DATABASE_URL, future=True)
# Создаем сессию
#AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Базовый класс для моделей SQLAlchemy
#Base = declarative_base()

'''
@contextlib.asynccontextmanager
async def get_db_session() -> AsyncGenerator:
    """
    Контекстный менеджер для управления сессией базы данных.
    """
    try:
        session = AsyncSessionLocal()
        yield session
    finally:
        await session.close()
        await engine.dispose()

@contextlib.asynccontextmanager
async def get_http_session():
    """
    Контекстный менеджер для управления сессией aiohttp.
    """
    async with aiohttp.ClientSession() as session:
        try:
            yield session
        finally:
            await session.close()
'''


def db_get_metadata(seller) -> Dict[str, Any]:
    """
    Извлекает метаданные для указанного продавца (seller) из модели Metadata.
    """

    result = {}

    metadata_record = Metadata.objects.filter(seller=seller).all()
    if metadata_record:
        for meta in metadata_record:
            result[meta.name] = meta.metadata_dict

    return result

def db_update_metadata(seller, metadata) -> Dict[str, Any]:
    """
    обновляем метаданные для указанного продавца (seller)
    """

    #print(f'metadata 2: {metadata}')
    for key, meta_dict in metadata.items():
        metadata_record = Metadata.objects.filter(seller=seller, name=key).first()

        if metadata_record:
            metadata_record.metadata_dict = meta_dict
            metadata_record.save()
        else:
            Metadata.objects.create(
                seller=seller,
                name=key,
                metadata_dict=meta_dict)

def db_check_awaiting_postingnumber(posting_numbers: list):
    found_records = Awaiting.objects.filter(posting_number__in=posting_numbers)
    found_posting_numbers = set(found_records.values_list('posting_number', flat=True))

    #print(f'P' * 40)
    #print(f'posting_numbers {posting_numbers}')
    #print(f'found_posting_numbers {found_posting_numbers}')
    #print(f'P' * 40)

    not_found_records = [pn for pn in posting_numbers if str(pn) not in found_posting_numbers]
    result = {'found': found_posting_numbers, 'not_found': not_found_records}
    return result

def db_create_customerorder(not_found_product: dict, market: str, seller: Seller):
    # product = {posting_number: {status:'', product_list: [{offer_id: price: quantity:}] }}
    try:
        for posting_number, products in not_found_product.items():
            # Создаем запись в таблице OwmAwaiting
            awaiting_record = Awaiting.objects.create(
                seller=seller,
                posting_number=posting_number,
                status=products['status'],
                market=market
                )

            for product in products['product_list']:
                #print(f'#' * 40)
                #print(f"product {product}")
                #print(f'#' * 40)
                # Создаем запись в таблице OwmAwaitingProduct
                Awaiting_product.objects.create(
                    awaiting=awaiting_record,
                    offer_id=product['offer_id'],
                    price=int(float(product['price'])),
                    quantity=product['quantity']
                    )
    except Exception as e:
        # Логируем ошибку или поднимаем исключение, если нужно
        print(f"Error occurred: {e}")
        raise

def db_update_customerorder(posting_number: str, status: str, seller: Seller):
    try:
        # Пытаемся найти запись с таким posting_number и продавцом
        awaiting_record = Awaiting.objects.filter(posting_number=posting_number, seller=seller).first()

        if awaiting_record:
            # Обновляем только статус
            awaiting_record.status = status
            awaiting_record.save()
            print(f"[OK] Обновлён статус записи posting_number={posting_number} → {status}")
        else:
            print(f"[WARN] Запись с posting_number={posting_number} не найдена. Обновление не выполнено.")

    except Exception as e:
        print(f"[ERROR] При обновлении заказа: {e}")
        raise

def db_delete_customerorder(posting_number: str, seller: Seller):
    try:
        # Пытаемся найти запись с таким posting_number и продавцом
        awaiting_record = Awaiting.objects.filter(posting_number=posting_number, seller=seller).first()

        if awaiting_record:
            awaiting_record.delete()
            print(f"[OK] Удалена запись posting_number={posting_number}")
        else:
            print(f"[WARN] Запись с posting_number={posting_number} не найдена. Удаление не выполнено.")

    except Exception as e:
        print(f"[ERROR] При удалении заказа: {e}")
        raise


def db_get_status(seller: Seller, market: str, exclude_status: str = None) -> Dict[str, Any]:
    """
    Извлекает все отпралвения для указанного продавца (seller)
    """

    if exclude_status:
        records = Awaiting.objects.filter(
            seller=seller,
            market=market
        ).exclude(status=exclude_status)
    else:
        records = Awaiting.objects.filter(
            seller=seller,
            market=market
        )

    result = {}
    orders_list = []
    for record in records:
            orders_list.append({
            'posting_number': record.posting_number,
            'status': record.status
        })
    result[market] = orders_list
    return result


def db_get_settings(seller, type) -> Dict[str, Any]:
    result = {}
    settings = Settings.objects.filter(seller=seller, type=type).first()
    if settings:
        result = settings.settings_dict
    return result

def db_update_settings(seller, type, settings_dict):
    settings = Settings.objects.filter(seller=seller, type=type).first()
    if settings:
        settings.settings_dict = settings_dict
        settings.save()
        
def db_update_promo_products(seller, promo_data: dict) -> bool:
    """
    Обновляет настройки акций товара для продавца.
    promo_data: {
        "market": str,
        "offer_id": str,
        "yourprice": int,
        "minprice": int,
        "min_price_fbs": int,
        "min_price_limit_count": int,
        "min_price_promo": int,
        "limit_count_value": int,
        "use_fbs": bool,
        "use_limit_count": bool,
        "use_promo": bool,
        "autoupdate_promo": bool
    }
    """

    try:
        print(f"promo_data {promo_data}")
        market = promo_data.get("market")
        offer_id = promo_data.get("offer_id")

        if not market or not offer_id:
            return False

        promo_market, _ = PromoMarket.objects.get_or_create(seller=seller, market=market)

        promo_product, _ = PromoProduct.objects.get_or_create(
            promo_market=promo_market,
            offer_id=offer_id
        )

        # Обновляем все поля, если они есть в словаре
        
        for field in [
            "yourprice", "minprice", "min_price_fbs", "min_price_limit_count",
            "min_price_promo", "limit_count_value", "use_fbs",
            "use_limit_count", "use_promo", "autoupdate_promo"
        ]:
            if field in promo_data:
                value = promo_data[field]
                if field == "limit_count_value":
                    # Если значение пустое или None, выставляем 1
                    if value in ("", None):
                        value = 1
                setattr(promo_product, field, value)

        promo_product.save()
        return True
    except Exception as e:
        print(f"[ERROR] db_update_promo_products: {e}")
        return False

def db_get_promo_products(seller) -> dict:
    """
    Получает все настройки акций товаров для текущего продавца.
    Возвращает dict promo_data с offer_id в качестве ключа.
    """
    result = {}
    promo_markets = PromoMarket.objects.filter(seller=seller)
    for promo_market in promo_markets:
        promo_products = PromoProduct.objects.filter(promo_market=promo_market)
        for product in promo_products:
            result[product.offer_id] = {
                "market": promo_market.market,
                "offer_id": product.offer_id,
                "yourprice": product.yourprice,
                "minprice": product.minprice,
                "min_price_fbs": product.min_price_fbs,
                "min_price_limit_count": product.min_price_limit_count,
                "min_price_promo": product.min_price_promo,
                "limit_count_value": product.limit_count_value,
                "use_fbs": product.use_fbs,
                "use_limit_count": product.use_limit_count,
                "use_promo": product.use_promo,
                "autoupdate_promo": product.autoupdate_promo,
            }
    return result
