from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User

from .models import forget_wrong_tries, remember_wrong_try, too_many_wrong_tries


class SignupForm(UserCreationForm):
    email = forms.EmailField()

    class Meta:
        model = User
        fields = ['username', 'email']

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
