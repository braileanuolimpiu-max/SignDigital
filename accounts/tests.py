import json

from django.contrib.auth.models import User
from django.test import TestCase

from documents.models import Document
from documents.tests import make_jpeg, make_overlay, make_png, placeholder

PASSWORD = 'a-long-password-123'


class SignupTests(TestCase):
    def test_signup_page_opens(self):
        response = self.client.get('/signup/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Create an account')

    def test_signup_creates_the_user_and_logs_in(self):
        response = self.client.post('/signup/', {
            'username': 'ion',
            'email': 'Ion@Example.com',
            'password1': PASSWORD,
            'password2': PASSWORD,
        })

        self.assertRedirects(response, '/')
        user = User.objects.get()
        self.assertEqual(user.email, 'ion@example.com')
        self.assertNotEqual(user.password, PASSWORD)

    def test_email_can_be_used_only_once(self):
        User.objects.create_user('ion', 'ion@example.com', PASSWORD)

        response = self.client.post('/signup/', {
            'username': 'maria',
            'email': 'ION@example.com',
            'password1': PASSWORD,
            'password2': PASSWORD,
        })

        self.assertContains(response, 'An account with this email already exists.')
        self.assertEqual(User.objects.count(), 1)

    def test_weak_password_is_rejected(self):
        response = self.client.post('/signup/', {
            'username': 'ion',
            'email': 'ion@example.com',
            'password1': '123',
            'password2': '123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)


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
