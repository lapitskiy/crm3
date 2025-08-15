"""ServiceCRM3 URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from plugins import settings_plugin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.views.generic import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage


urlpatterns = [
    path('', include('news.urls')),  # Сделать новости главной страницей
    path('owm/', include('owm.urls')),  # Перенести owm на /owm/
    path('admin/', admin.site.urls),
    path('plugins/', include('plugins.urls')),
    path('users/', include('users.urls')),
    path('select2/', include('django_select2.urls')),
    path("ckeditor5/", include('django_ckeditor_5.urls')),
]
for key, value in settings_plugin.PLUGIN_URLS.items():
    urlpatterns.append(path(value['path'], include(value['include'])))
#if not settings.DEBUG:
urlpatterns += staticfiles_urlpatterns()


if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [
    path("favicon.ico", RedirectView.as_view(url=staticfiles_storage.url("assets/media/logos/favicon.ico"))),]
    urlpatterns += [path('__debug__/', include(debug_toolbar.urls)),]
    urlpatterns += static(settings.MEDIA_URL, document_root = settings.MEDIA_ROOT)


