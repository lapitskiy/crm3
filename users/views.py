from django.shortcuts import render
from django.contrib import messages
from django.shortcuts import render, redirect
from .forms import UserRegisterForm, UserLoginForm, UserRegisterCompany
from django.contrib.auth import login, logout
from django.views.generic import ListView, TemplateView, DetailView
from plugins.utils import RelatedMixin
from django.contrib.auth.models import User

#from django.views.decorators.csrf import csrf_protect
#from django.utils.decorators import method_decorator
#from django.views.decorators.csrf import csrf_exempt


from django.contrib.auth.mixins import LoginRequiredMixin

class UserHomeView(LoginRequiredMixin, RelatedMixin, ListView):
    model = User
    template_name = 'users/users_index.html'
    paginate_by = 10
    context_object_name = 'user'
    related_module_name = 'users' #relatedmixin module

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Все пользователи'
        print('user context: ', context)
        return context

#UsersHomeViewPermit = permission_required('users.view', raise_exception=True)(UserHomeView.as_view())


class UsersSettingsView(RelatedMixin, TemplateView):
    template_name = 'users/users_settingsPrev.html'
    related_module_name = 'users' #relatedmixin module

    def get(self, request, *args, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Настройки'
        return self.render_to_response(context)


def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Вы успешно зарегистрировались')
            return redirect('login')
        else:
            messages.error(request, 'Ошибка регистрации')
    else:
        print('tyt')
        form = UserRegisterForm()
    return render(request, 'users/register.html', {"form": form})

def register_company(request):
    if request.method == 'POST':
        form = UserRegisterCompany(request.POST)
        #print(f'form registr {form}')
        if form.is_valid():
            print(f'VALID')
            form.save()
            messages.success(request, 'Вы успешно зарегистрировались')
            return redirect('login')
        else:
            print(f'ERROR: {form.errors}')
            messages.error(request, 'Ошибка регистрации')
    else:
        form = UserRegisterCompany()
        #print(f'form {form}')
    return render(request, 'users/register_company.html', {"form": form})

def user_login(request):
    context = {}
    context['error'] = ''
    superuser = User.objects.filter(is_superuser=True).first()
    if superuser is None:
        context['error'] = 'superuser_is_none'

    if request.method == 'POST':
        form = UserLoginForm(data=request.POST)
        print(request.COOKIES)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            response.set_cookie('test_cookie', '123')  # проверим, ставятся ли куки
            print('session key:', request.session.session_key)
            return redirect('news_home')
    else:
        context['form'] = UserLoginForm()
    return render(request, 'users/login.html', context)


def user_logout(request):
    logout(request)
    return redirect('login')
