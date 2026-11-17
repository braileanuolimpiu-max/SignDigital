from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    UserCreationForm,
)
from django.contrib.auth.models import User

from .models import (
    email_sent_recently,
    forget_wrong_tries,
    remember_sent_email,
    remember_wrong_try,
    too_many_wrong_tries,
)


class SignupForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, label='First name')
    last_name = forms.CharField(max_length=150, label='Last name')
    email = forms.EmailField()

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email']

    def clean_email(self):
        email = self.cleaned_data['email']
        email = email.strip()
        email = email.lower()

        if User.objects.filter(email__iexact=email, is_active=True).exists():
            raise forms.ValidationError('An account with this email already exists.')

        return email


class LoginForm(AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')

        if username is None or password is None:
            return super().clean()

        username_key = username.lower()
        if too_many_wrong_tries(username_key):
            raise forms.ValidationError(
                'Too many wrong tries. Wait 15 minutes and try again.'
            )

        user = User.objects.filter(username=username, is_active=False).first()
        if user is not None and user.check_password(password):
            raise forms.ValidationError(
                'Please confirm your email first. Open the link we sent you when you signed up.'
            )

        try:
            cleaned_data = super().clean()
        except forms.ValidationError:
            remember_wrong_try(username_key)
            raise

        forget_wrong_tries(username_key)
        return cleaned_data


class ResendConfirmationForm(forms.Form):
    email = forms.EmailField()


class LimitedPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        if email_sent_recently(email, 'reset'):
            return []

        remember_sent_email(email, 'reset')
        return super().get_users(email)


class NameForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, label='First name')
    last_name = forms.CharField(max_length=150, label='Last name')

    class Meta:
        model = User
        fields = ['first_name', 'last_name']


class DeleteAccountForm(forms.Form):
    password = forms.CharField(label='Your password', widget=forms.PasswordInput)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError('The password is wrong.')
        return password


class ChangePasswordForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].widget.attrs.pop('autofocus', None)
