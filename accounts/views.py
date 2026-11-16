from django.contrib.auth import login
from django.shortcuts import redirect, render

from .forms import SignupForm


def signup(request):
    if request.user.is_authenticated:
        return redirect('homepage')

    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('homepage')
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
