from django import forms
from django.contrib.auth.forms import UserCreationForm
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

        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')

        return email
