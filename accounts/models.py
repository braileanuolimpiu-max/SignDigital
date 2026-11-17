from datetime import timedelta

from django.db import models
from django.utils import timezone

MAX_WRONG_TRIES = 5
LOCKED_FOR = timedelta(minutes=15)


class LoginAttempt(models.Model):
    username = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)


def too_many_wrong_tries(username):
    since = timezone.now() - LOCKED_FOR
    count = LoginAttempt.objects.filter(username=username, created_at__gte=since).count()
    return count >= MAX_WRONG_TRIES


def remember_wrong_try(username):
    LoginAttempt.objects.create(username=username)

    too_old = timezone.now() - LOCKED_FOR
    LoginAttempt.objects.filter(created_at__lt=too_old).delete()


def forget_wrong_tries(username):
    LoginAttempt.objects.filter(username=username).delete()
