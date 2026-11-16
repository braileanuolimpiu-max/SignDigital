from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User


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

        user = User.objects.filter(username=username, is_active=False).first()
        if user is not None and user.check_password(password):
            raise forms.ValidationError(
                'Please confirm your email first. Open the link we sent you when you signed up.'
            )

        return super().clean()


class ResendConfirmationForm(forms.Form):
    email = forms.EmailField()
