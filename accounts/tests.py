import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import LoginAttempt, SentEmail

from documents.models import Document
from documents.tests import make_jpeg, make_overlay, make_png, placeholder

PASSWORD = 'a-long-password-123'


class SignupTests(TestCase):
    def test_signup_page_opens(self):
        response = self.client.get('/signup/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Create an account')

    def test_signup_creates_an_account_that_waits_for_the_email(self):
        response = self.client.post('/signup/', {
            'username': 'ion',
            'first_name': 'Ion',
            'last_name': 'Pop',
            'email': 'Ion@Example.com',
            'password1': PASSWORD,
            'password2': PASSWORD,
        })

        self.assertContains(response, 'Check your email')
        user = User.objects.get()
        self.assertEqual(user.email, 'ion@example.com')
        self.assertFalse(user.is_active)
        self.assertNotEqual(user.password, PASSWORD)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ion@example.com'])

        response = self.client.get('/')
        self.assertRedirects(response, '/login/?next=/')

    def test_signup_saves_the_full_name(self):
        sign_up(self.client)

        self.assertEqual(User.objects.get().get_full_name(), 'Ion Pop')

    def test_signup_needs_the_name(self):
        response = self.client.post('/signup/', {
            'username': 'ion',
            'email': 'ion@example.com',
            'password1': PASSWORD,
            'password2': PASSWORD,
        })

        self.assertContains(response, 'First name')
        self.assertEqual(User.objects.count(), 0)

    def test_email_can_be_used_only_once(self):
        User.objects.create_user('ion', 'ion@example.com', PASSWORD)

        response = self.client.post('/signup/', {
            'username': 'maria',
            'first_name': 'Maria',
            'last_name': 'Stan',
            'email': 'ION@example.com',
            'password1': PASSWORD,
            'password2': PASSWORD,
        })

        self.assertContains(response, 'An account with this email already exists.')
        self.assertEqual(User.objects.count(), 1)

    def test_weak_password_is_rejected(self):
        response = self.client.post('/signup/', {
            'username': 'ion',
            'first_name': 'Ion',
            'last_name': 'Pop',
            'email': 'ion@example.com',
            'password1': '123',
            'password2': '123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)


def sign_up(client, username='ion', email='ion@example.com'):
    return client.post('/signup/', {
        'username': username,
        'first_name': 'Ion',
        'last_name': 'Pop',
        'email': email,
        'password1': PASSWORD,
        'password2': PASSWORD,
    })


def confirmation_link(email):
    found = re.search(r'/confirm/[^/]+/[^/]+/', email.body)
    return found.group(0)


class EmailConfirmationTests(TestCase):
    def test_link_confirms_the_email_and_logs_in(self):
        sign_up(self.client)

        response = self.client.get(confirmation_link(mail.outbox[0]), follow=True)

        self.assertRedirects(response, '/')
        self.assertContains(response, 'Your email is confirmed.')
        self.assertTrue(User.objects.get().is_active)

    def test_link_works_only_once(self):
        sign_up(self.client)
        link = confirmation_link(mail.outbox[0])
        self.client.get(link)
        self.client.post('/logout/')

        response = self.client.get(link)

        self.assertContains(response, 'This link does not work')

    def test_wrong_link_does_not_work(self):
        sign_up(self.client)

        response = self.client.get('/confirm/MQ/wrong-token/')
        self.assertContains(response, 'This link does not work')

        response = self.client.get('/confirm/not-base64!/wrong-token/')
        self.assertContains(response, 'This link does not work')
        self.assertFalse(User.objects.get().is_active)

    def test_login_before_confirming_asks_to_confirm(self):
        sign_up(self.client)

        response = self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        self.assertContains(response, 'Please confirm your email first.')
        response = self.client.get('/')
        self.assertRedirects(response, '/login/?next=/')

    def test_wrong_password_on_unconfirmed_account_says_nothing_about_it(self):
        sign_up(self.client)

        response = self.client.post('/login/', {'username': 'ion', 'password': 'wrong'})

        self.assertNotContains(response, 'Please confirm your email first.')
        self.assertContains(response, 'Please enter a correct username and password.')

    def test_login_page_links_to_resend(self):
        response = self.client.get('/login/')

        self.assertContains(response, '/resend-confirmation/')

    def test_resend_sends_a_new_link_that_works(self):
        sign_up(self.client)
        SentEmail.objects.update(created_at=timezone.now() - timedelta(minutes=6))
        mail.outbox = []

        response = self.client.post('/resend-confirmation/', {'email': 'ION@example.com'})

        self.assertContains(response, 'Check your email')
        self.assertEqual(len(mail.outbox), 1)
        self.client.get(confirmation_link(mail.outbox[0]))
        self.assertTrue(User.objects.get().is_active)

    def test_resend_right_after_signup_sends_nothing(self):
        sign_up(self.client)
        mail.outbox = []

        response = self.client.post('/resend-confirmation/', {'email': 'ion@example.com'})

        self.assertContains(response, 'Check your email')
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_to_unknown_email_says_the_same_and_sends_nothing(self):
        response = self.client.post('/resend-confirmation/', {'email': 'nobody@example.com'})

        self.assertContains(response, 'If an account with this email is waiting for confirmation')
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_does_nothing_for_a_confirmed_account(self):
        User.objects.create_user('ion', 'ion@example.com', PASSWORD)

        self.client.post('/resend-confirmation/', {'email': 'ion@example.com'})

        self.assertEqual(len(mail.outbox), 0)

    def test_unconfirmed_account_does_not_block_the_email(self):
        sign_up(self.client, username='stranger', email='ion@example.com')

        response = sign_up(self.client, username='ion', email='ion@example.com')

        self.assertContains(response, 'Check your email')
        user = User.objects.get()
        self.assertEqual(user.username, 'ion')

    def test_failed_email_does_not_keep_the_account(self):
        with patch('accounts.views.send_confirmation_email', side_effect=OSError):
            response = sign_up(self.client)

        self.assertContains(response, 'The confirmation email could not be sent.')
        self.assertEqual(User.objects.count(), 0)


def reset_link(email):
    found = re.search(r'/reset/[^/]+/[^/]+/', email.body)
    return found.group(0)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ion', 'ion@example.com', PASSWORD)

    def test_login_page_links_to_forgot_password(self):
        response = self.client.get('/login/')

        self.assertContains(response, '/forgot-password/')

    def test_reset_link_lets_you_choose_a_new_password(self):
        response = self.client.post('/forgot-password/', {'email': 'ion@example.com'}, follow=True)
        self.assertContains(response, 'Check your email')
        self.assertEqual(len(mail.outbox), 1)

        response = self.client.get(reset_link(mail.outbox[0]), follow=True)
        self.assertContains(response, 'Choose a new password')
        form_url = response.redirect_chain[-1][0]

        response = self.client.post(form_url, {
            'new_password1': 'a-brand-new-password-456',
            'new_password2': 'a-brand-new-password-456',
        }, follow=True)
        self.assertContains(response, 'Your password was changed')

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('a-brand-new-password-456'))

    def test_reset_link_uses_the_site_address(self):
        self.client.post('/forgot-password/', {'email': 'ion@example.com'})

        self.assertIn(settings.SITE_URL + '/reset/', mail.outbox[0].body)

    def test_reset_link_works_only_once(self):
        self.client.post('/forgot-password/', {'email': 'ion@example.com'})
        link = reset_link(mail.outbox[0])
        response = self.client.get(link, follow=True)
        form_url = response.redirect_chain[-1][0]
        self.client.post(form_url, {
            'new_password1': 'a-brand-new-password-456',
            'new_password2': 'a-brand-new-password-456',
        })

        response = self.client.get(link, follow=True)

        self.assertContains(response, 'This link does not work.')

    def test_second_reset_within_five_minutes_sends_nothing(self):
        self.client.post('/forgot-password/', {'email': 'ion@example.com'})
        response = self.client.post('/forgot-password/', {'email': 'ION@example.com'}, follow=True)

        self.assertContains(response, 'Check your email')
        self.assertEqual(len(mail.outbox), 1)

    def test_reset_can_be_asked_again_after_five_minutes(self):
        self.client.post('/forgot-password/', {'email': 'ion@example.com'})
        SentEmail.objects.update(created_at=timezone.now() - timedelta(minutes=6))

        self.client.post('/forgot-password/', {'email': 'ion@example.com'})

        self.assertEqual(len(mail.outbox), 2)

    def test_unknown_email_says_the_same_and_sends_nothing(self):
        response = self.client.post('/forgot-password/', {'email': 'nobody@example.com'}, follow=True)

        self.assertContains(response, 'Check your email')
        self.assertEqual(len(mail.outbox), 0)

    def test_unconfirmed_account_gets_no_reset_email(self):
        self.user.is_active = False
        self.user.save()

        self.client.post('/forgot-password/', {'email': 'ion@example.com'})

        self.assertEqual(len(mail.outbox), 0)


class LoginTests(TestCase):
    def setUp(self):
        User.objects.create_user('ion', 'ion@example.com', PASSWORD)

    def test_homepage_needs_login(self):
        response = self.client.get('/')

        self.assertRedirects(response, '/login/?next=/')

    def test_good_password_logs_in(self):
        response = self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        self.assertRedirects(response, '/')
        response = self.client.get('/')
        self.assertContains(response, 'Log out')

    def test_wrong_password_does_not_log_in(self):
        response = self.client.post('/login/', {'username': 'ion', 'password': 'wrong'})

        self.assertEqual(response.status_code, 200)
        response = self.client.get('/')
        self.assertRedirects(response, '/login/?next=/')

    def test_login_does_not_redirect_to_other_sites(self):
        response = self.client.post('/login/?next=https://evil.example.com/', {
            'username': 'ion',
            'password': PASSWORD,
        })

        self.assertRedirects(response, '/')

    def test_logout(self):
        self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        response = self.client.post('/logout/')

        self.assertRedirects(response, '/login/')
        response = self.client.get('/')
        self.assertRedirects(response, '/login/?next=/')


class LoginLimitTests(TestCase):
    def setUp(self):
        User.objects.create_user('ion', 'ion@example.com', PASSWORD)

    def wrong_tries(self, count):
        for number in range(count):
            self.client.post('/login/', {'username': 'ion', 'password': 'wrong'})

    def test_four_wrong_tries_still_let_you_in(self):
        self.wrong_tries(4)

        response = self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        self.assertRedirects(response, '/')

    def test_five_wrong_tries_lock_the_username(self):
        self.wrong_tries(5)

        response = self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        self.assertContains(response, 'Too many wrong tries.')
        response = self.client.get('/')
        self.assertRedirects(response, '/login/?next=/')

    def test_lock_ends_after_fifteen_minutes(self):
        self.wrong_tries(5)
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=16))

        response = self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        self.assertRedirects(response, '/')

    def test_good_login_clears_the_wrong_tries(self):
        self.wrong_tries(4)
        self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})
        self.client.post('/logout/')

        self.wrong_tries(4)
        response = self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        self.assertRedirects(response, '/')

    def test_capital_letters_count_as_the_same_username(self):
        for number in range(5):
            self.client.post('/login/', {'username': 'ION', 'password': 'wrong'})

        response = self.client.post('/login/', {'username': 'ion', 'password': PASSWORD})

        self.assertContains(response, 'Too many wrong tries.')


class DocumentOwnerTests(TestCase):
    def setUp(self):
        self.ion = User.objects.create_user('ion', 'ion@example.com', PASSWORD)
        self.maria = User.objects.create_user('maria', 'maria@example.com', PASSWORD)

    def tearDown(self):
        for document in Document.objects.all():
            document.delete_with_files()

    def test_upload_belongs_to_the_user(self):
        self.client.force_login(self.ion)

        self.client.post('/', {'file': make_png(), 'mode': 'solo'})

        document = Document.objects.get()
        self.assertEqual(document.owner, self.ion)

    def test_other_user_cannot_use_the_document(self):
        self.client.force_login(self.ion)
        self.client.post('/', {'file': make_png(), 'mode': 'solo'})
        document = Document.objects.get()

        self.client.force_login(self.maria)

        response = self.client.get(f'/editor/{document.uid}/')
        self.assertRedirects(response, '/')
        response = self.client.post(f'/editor/{document.uid}/export/', {'overlay_1': make_overlay()})
        self.assertEqual(response.status_code, 410)

    def test_logged_out_requests_get_nothing(self):
        self.client.force_login(self.ion)
        self.client.post('/', {'file': make_png(), 'mode': 'solo'})
        document = Document.objects.get()
        self.client.logout()

        response = self.client.post(f'/editor/{document.uid}/export/')
        self.assertEqual(response.status_code, 410)

        response = self.client.post(f'/editor/{document.uid}/autoplace/')
        self.assertEqual(response.status_code, 410)

    def test_sign_link_works_without_an_account(self):
        self.client.force_login(self.ion)
        self.client.post('/', {'file': make_png(), 'mode': 'multi'})
        document = Document.objects.get()
        self.client.post(f'/editor/{document.uid}/send/', {
            'placeholders': json.dumps([placeholder('Ana', 'ana@example.com')]),
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(),
        })
        self.client.post('/logout/')

        document = Document.objects.get()
        signer = document.signers.get()
        response = self.client.get(f'/sign/{signer.token}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ana')
