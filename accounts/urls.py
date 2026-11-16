from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

login_page = {
    'title': 'Log in',
    'button': 'Log in',
    'other_text': 'No account yet?',
    'other_link': 'signup',
    'other_link_text': 'Sign up',
    'show_resend': True,
}

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='auth.html',
            authentication_form=LoginForm,
            redirect_authenticated_user=True,
            extra_context=login_page,
        ),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('confirm/<str:uid>/<str:token>/', views.confirm_email, name='confirm_email'),
    path('resend-confirmation/', views.resend_confirmation, name='resend_confirmation'),
]
