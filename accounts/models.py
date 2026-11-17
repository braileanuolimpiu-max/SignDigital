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


EMAIL_AGAIN_AFTER = timedelta(minutes=5)


class SentEmail(models.Model):
    email = models.EmailField()
    kind = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)


def email_sent_recently(email, kind):
    since = timezone.now() - EMAIL_AGAIN_AFTER
    return SentEmail.objects.filter(email=email.lower(), kind=kind, created_at__gte=since).exists()


def remember_sent_email(email, kind):
    SentEmail.objects.create(email=email.lower(), kind=kind)

    too_old = timezone.now() - EMAIL_AGAIN_AFTER
    SentEmail.objects.filter(created_at__lt=too_old).delete()
