from django import template

register = template.Library()

@register.filter
def sale_qty_get_row_class(percent):
    try:
        # Преобразуем в число, если это строка
        if isinstance(percent, str):
            percent = float(percent)
        elif percent is None:
            percent = 0
    except (ValueError, TypeError):
        percent = 0
    
    if percent < 30:
        return ["#ffc0d3", "#fddbe5"]
    elif percent < 60:
        return ["#eef3ac", "#f6f8d1"]
    else:
        return ["#bff5a6", "#e4fada"]

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, {})