from django.urls import path
from .views import *

urlpatterns = [
    path('', HomeNews.as_view(), name='news_home'),
    path('category/<int:category_id>/', NewsByCategory.as_view(), name='category'),
    # path('news/<int:news_id>/', view_news, name='view_news'),
    path('news/<int:pk>/', ViewNews.as_view(), name='view_news'),
    path('add-news/', CreateNews.as_view(), name='add_news'),
]