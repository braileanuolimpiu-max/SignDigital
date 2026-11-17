from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import LimitedPasswordResetForm, LoginForm

login_page = {
    'title': 'Log in',
    'button': 'Log in',
    'other_text': 'No account yet?',
    'other_link': 'signup',
    'other_link_text': 'Sign up',
    'show_resend': True,
    'show_forgot': True,
}

forgot_page = {
    'title': 'Forgot your password?',
    'button': 'Send me a link',
    'other_text': 'Remembered it?',
    'other_link': 'login',
    'other_link_text': 'Log in',
}

forgot_sent_page = {
    'title': 'Check your email',
    'message': 'If an account uses this email, we sent it a link to choose a new password.',
    'link': reverse_lazy('login'),
    'link_text': 'Go to log in',
}

new_password_page = {
    'title': 'Choose a new password',
    'button': 'Save the new password',
    'other_text': 'Remembered it?',
    'other_link': 'login',
    'other_link_text': 'Log in',
}

password_changed_page = {
    'title': 'Your password was changed',
    'message': 'You can log in with your new password now.',
    'link': reverse_lazy('login'),
    'link_text': 'Go to log in',
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
    path('account/', views.account, name='account'),
    path(
        'forgot-password/',
        auth_views.PasswordResetView.as_view(
            template_name='auth.html',
            form_class=LimitedPasswordResetForm,
            email_template_name='emails/password_reset.txt',
            html_email_template_name='emails/password_reset.html',
            subject_template_name='emails/password_reset_subject.txt',
            extra_email_context={'site_url': settings.SITE_URL},
            extra_context=forgot_page,
        ),
        name='password_reset',
    ),
    path(
        'forgot-password/sent/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='sign_finished.html',
            extra_context=forgot_sent_page,
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='auth.html',
            extra_context=new_password_page,
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='sign_finished.html',
            extra_context=password_changed_page,
        ),
        name='password_reset_complete',
    ),
]
