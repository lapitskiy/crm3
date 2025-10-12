import pandas as pd
import os
from django.conf import settings

def read_min_prices(file_path=None):
    """
    Читает файл min_auto_wb.xlsx и возвращает mapping:
    { 'Артикул продавца': новая минимальная цена }
    """
    if file_path is None:
        # ожидаем, что BASE_DIR указывает на корень проекта
        file_path = os.path.join(settings.BASE_DIR, 'owm', 'temp', 'min_auto_wb.xlsx')
    # читаем первый лист
    df = pd.read_excel(file_path, sheet_name=0)
    # выбираем нужные колонки по именам заголовков
    cols = ['Артикул продавца', 'Новая минимальная цена для применения скидки по автоакции']
    df = df[cols].dropna()
    # преобразуем в словарь
    mapping = {
        str(row['Артикул продавца']): row['Новая минимальная цена для применения скидки по автоакции']
        for _, row in df.iterrows()
    }
    return mapping

def update_min_prices(updates: dict, items: list, file_path=None):
    """
    Обновляет значения в Excel-файле для существующих строк и добавляет новые, если артикула нет.
    items: список словарей с ключами article_wb, seller_article, barcode
    updates: { seller_article: new_min_price }
    """
    # Пути для чтения и записи
    read_path = file_path or os.path.join(settings.BASE_DIR, 'owm', 'temp', 'min_auto_wb.xlsx')
    write_dir = os.path.join(settings.MEDIA_ROOT, 'owm', 'temp')
    os.makedirs(write_dir, exist_ok=True)
    write_path = os.path.join(write_dir, 'min_auto_wb.xlsx')
    # Читаем Excel из исходного файла
    df = pd.read_excel(read_path, sheet_name=0)
    col_wb = 'Артикул WB'
    col_seller = 'Артикул продавца'
    col_barcode = 'Последний баркод'
    col_min = 'Новая минимальная цена для применения скидки по автоакции'
    # Убедимся, что все колонки присутствуют
    for col in (col_wb, col_barcode, col_min):
        if col not in df.columns:
            df[col] = pd.NA
    # Обновляем существующие строки
    for idx, row in df.iterrows():
        seller = str(row.get(col_seller))
        if seller in updates and updates[seller] is not None:
            df.at[idx, col_min] = updates[seller]
    # Добавляем новые строки для отсутствующих продавцов
    existing = set(df[col_seller].astype(str).tolist())
    for seller, price in updates.items():
        if seller not in existing and price is not None:
            # найти данные из items
            rec = next((it for it in items if str(it['seller_article']) == seller), {})
            new_row = {
                col_wb: rec.get('article_wb'),
                col_seller: seller,
                col_barcode: rec.get('barcode'),
                col_min: price
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    # Сохраняем изменения в папку media
    df.to_excel(write_path, index=False)
    return updates
