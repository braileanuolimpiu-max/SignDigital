import smtplib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMessage
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .forms import ChangePasswordForm, DeleteAccountForm, NameForm, ResendConfirmationForm, SignupForm
from .models import email_sent_recently, remember_sent_email

EMAIL_FAILED_MESSAGE = 'The confirmation email could not be sent. Try again later.'


def show_message(request, title, message):
    context = {
        'title': title,
        'message': message,
        'link': reverse('login'),
        'link_text': 'Go to log in',
    }
    return render(request, 'sign_finished.html', context)


def send_confirmation_email(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = settings.SITE_URL + reverse('confirm_email', args=[uid, token])

    html = render_to_string('emails/confirm_email.html', {'user': user, 'link': link})
    email = EmailMessage(
        subject='Confirm your email for SignElectric',
        body=html,
        to=[user.email],
    )
    email.content_subtype = 'html'
    email.send()


def delete_unconfirmed_accounts(email):
    unconfirmed = User.objects.filter(email__iexact=email, is_active=False, last_login__isnull=True)
    for user in unconfirmed:
        user.delete()


def signup(request):
    if request.user.is_authenticated:
        return redirect('homepage')

    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            delete_unconfirmed_accounts(form.cleaned_data['email'])

            user = form.save(commit=False)
            user.is_active = False
            user.save()

            try:
                send_confirmation_email(user)
                remember_sent_email(user.email, 'confirm')
                return show_message(
                    request,
                    'Check your email',
                    f'We sent a link to {user.email}. Open it to finish creating your account.',
                )
            except (smtplib.SMTPException, OSError):
                user.delete()
                form.add_error(None, EMAIL_FAILED_MESSAGE)
    else:
        form = SignupForm()

    context = {
        'form': form,
        'title': 'Create an account',
        'button': 'Sign up',
        'other_text': 'Already have an account?',
        'other_link': 'login',
        'other_link_text': 'Log in',
    }
    return render(request, 'auth.html', context)


def confirm_email(request, uid, token):
    user = None
    try:
        user_id = urlsafe_base64_decode(uid).decode()
        user = User.objects.filter(pk=user_id).first()
    except (ValueError, UnicodeDecodeError):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        return show_message(
            request,
            'This link does not work',
            'It was already used or it expired. Log in, or ask for a new link from the log in page.',
        )

    user.is_active = True
    user.save()
    login(request, user)
    messages.success(request, 'Your email is confirmed. Welcome!')
    return redirect('homepage')


def resend_confirmation(request):
    if request.method == 'POST':
        form = ResendConfirmationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            user = User.objects.filter(email__iexact=email, is_active=False, last_login__isnull=True).first()

            if user is not None and not email_sent_recently(email, 'confirm'):
                try:
                    send_confirmation_email(user)
                    remember_sent_email(email, 'confirm')
                except (smtplib.SMTPException, OSError):
                    form.add_error(None, EMAIL_FAILED_MESSAGE)

            if not form.errors:
                return show_message(
                    request,
                    'Check your email',
                    'If an account with this email is waiting for confirmation, we sent it a new link.',
                )
    else:
        form = ResendConfirmationForm()

    context = {
        'form': form,
        'title': 'Send the link again',
        'button': 'Send',
        'other_text': 'Already confirmed?',
        'other_link': 'login',
        'other_link_text': 'Log in',
    }
    return render(request, 'auth.html', context)


def delete_account_documents(user):
    for document in user.documents.all():
        try:
            document.delete_with_files()
        except OSError:
            return False
    return True


@login_required
def account(request):
    name_form = NameForm(instance=request.user)
    password_form = ChangePasswordForm(request.user)
    delete_form = DeleteAccountForm(request.user)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'name':
            name_form = NameForm(request.POST, instance=request.user)
            if name_form.is_valid():
                name_form.save()
                messages.success(request, 'Your name was saved.')
                return redirect('account')

        if action == 'password':
            password_form = ChangePasswordForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password was changed.')
                return redirect('account')

        if action == 'delete':
            delete_form = DeleteAccountForm(request.user, request.POST)
            if delete_form.is_valid():
                if not delete_account_documents(request.user):
                    messages.error(request, 'Your documents could not be deleted right now. Try again in a minute.')
                    return redirect('account')

                user = request.user
                logout(request)
                user.delete()
                return show_message(
                    request,
                    'Your account was deleted',
                    'Your account and all your documents are gone.',
                )

    context = {
        'name_form': name_form,
        'password_form': password_form,
        'delete_form': delete_form,
    }
    return render(request, 'account.html', context)
