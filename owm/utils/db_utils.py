import contextlib
from owm.models import Awaiting, Awaiting_product, Metadata, Settings, Seller
from typing import Any, Dict

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


def db_get_status(seller: Seller, market: str) -> Dict[str, Any]:
    """
    Извлекает все отпралвения для указанного продавца (seller)
    """
    records = Awaiting.objects.filter(seller=seller, market=market)
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




