import io
import json
import os
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from .models import Document, Signer


def make_pdf():
    page1 = Image.new('RGB', (600, 800), 'white')
    page2 = Image.new('RGB', (600, 800), 'white')
    page3 = Image.new('RGB', (600, 800), 'white')

    buffer = io.BytesIO()
    page1.save(buffer, 'PDF', save_all=True, append_images=[page2, page3])
    return SimpleUploadedFile('contract.pdf', buffer.getvalue())


def make_png():
    image = Image.new('RGB', (400, 300), 'red')

    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return SimpleUploadedFile('photo.png', buffer.getvalue())


def make_jpeg():
    image = Image.new('RGB', (400, 300), 'white')

    buffer = io.BytesIO()
    image.save(buffer, 'JPEG')
    return SimpleUploadedFile('page.jpg', buffer.getvalue(), content_type='image/jpeg')


def placeholder(name, email):
    return {
        'name': name,
        'email': email,
        'color': '#1e3a8a',
        'page_number': 1,
        'left': 100,
        'top': 100,
        'width': 200,
        'height': 50,
        'font_size': 40,
    }


class SignElectricTestCase(TestCase):
    def tearDown(self):
        for document in Document.objects.all():
            document.delete_with_files()

    def upload(self, file, mode='solo'):
        return self.client.post('/', {'file': file, 'mode': mode})

    def send(self, placeholders):
        self.upload(make_png(), 'multi')
        return self.client.post('/send/', {
            'sender_email': 'boss@example.com',
            'placeholders': json.dumps(placeholders),
            'page_1': make_jpeg(),
        })


class HomepageTests(SignElectricTestCase):
    def test_page_opens(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sign only by me')

    def test_pdf_upload_creates_one_page_per_pdf_page(self):
        response = self.upload(make_pdf())

        document = Document.objects.get()
        self.assertRedirects(response, f'/editor/{document.uid}/')
        self.assertEqual(document.filename, 'contract')
        self.assertEqual(document.pages.count(), 3)

    def test_image_upload_creates_one_page(self):
        self.upload(make_png())

        document = Document.objects.get()
        self.assertEqual(document.pages.count(), 1)

    def test_multiple_signing_button_sets_the_mode(self):
        self.upload(make_png(), 'multi')

        document = Document.objects.get()
        self.assertEqual(document.mode, 'multi')

    def test_txt_file_is_rejected(self):
        response = self.upload(SimpleUploadedFile('notes.txt', b'hello'))

        self.assertContains(response, 'is not allowed')
        self.assertEqual(Document.objects.count(), 0)

    def test_fake_pdf_shows_an_error(self):
        self.upload(SimpleUploadedFile('fake.pdf', b'not a pdf'))

        response = self.client.get('/')

        self.assertContains(response, 'The PDF is damaged')
        self.assertEqual(Document.objects.count(), 0)

    def test_too_big_file_is_rejected(self):
        with self.settings(MAX_UPLOAD_SIZE=10):
            response = self.upload(make_pdf())

        self.assertContains(response, 'The file is too big')
        self.assertEqual(Document.objects.count(), 0)

    def test_new_upload_deletes_the_old_one(self):
        self.upload(make_pdf())
        self.upload(make_png())

        document = Document.objects.get()
        self.assertEqual(document.filename, 'photo')


class CleanupTests(SignElectricTestCase):
    def setUp(self):
        self.upload(make_pdf())
        self.document = Document.objects.get()

    def test_document_stays_one_minute_after_leaving(self):
        self.document.left_at = timezone.now() - timedelta(minutes=1)
        self.document.save()

        self.client.get('/')

        self.assertEqual(Document.objects.count(), 1)

    def test_document_is_deleted_three_minutes_after_leaving(self):
        self.document.left_at = timezone.now() - timedelta(minutes=3)
        self.document.save()

        self.client.get('/')

        self.assertEqual(Document.objects.count(), 0)
        self.assertFalse(os.path.exists(self.document.folder_path))

    def test_locked_folder_is_skipped_and_deleted_later(self):
        self.document.left_at = timezone.now() - timedelta(minutes=3)
        self.document.save()

        with patch('shutil.rmtree', side_effect=PermissionError):
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Document.objects.count(), 1)

        self.client.get('/')
        self.assertEqual(Document.objects.count(), 0)

    def test_document_is_deleted_after_eleven_silent_minutes(self):
        self.document.last_seen = timezone.now() - timedelta(minutes=11)
        self.document.save()

        self.client.get('/')

        self.assertEqual(Document.objects.count(), 0)


class EditorTests(SignElectricTestCase):
    def test_owner_can_open_the_editor(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertEqual(response.status_code, 200)

    def test_stranger_is_sent_to_the_homepage(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        stranger_browser = self.client_class()
        response = stranger_browser.get(f'/editor/{document.uid}/')

        self.assertRedirects(response, '/')

    def test_deleted_document_shows_expired_message(self):
        self.upload(make_pdf())
        document = Document.objects.get()
        document.delete_with_files()

        response = self.client.get(f'/editor/{document.uid}/', follow=True)

        self.assertContains(response, 'Your document expired')

    def test_sign_only_by_me_shows_the_sign_button(self):
        self.upload(make_png())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertContains(response, 'onclick="ExportPDF()" disabled>Sign</button>')
        self.assertNotContains(response, 'Add placeholder')

    def test_multiple_signing_shows_placeholders_and_send(self):
        self.upload(make_png(), 'multi')
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertContains(response, 'Add placeholder')
        self.assertContains(response, 'Sign &amp; send')


class LeavePageTests(SignElectricTestCase):
    def test_leaving_marks_the_document_but_keeps_it(self):
        self.upload(make_pdf())

        response = self.client.post('/track-leave/')

        document = Document.objects.get()
        self.assertEqual(response.status_code, 204)
        self.assertIsNotNone(document.left_at)

    def test_only_post_is_allowed(self):
        response = self.client.get('/track-leave/')

        self.assertEqual(response.status_code, 405)


class HeartbeatTests(SignElectricTestCase):
    def test_heartbeat_clears_the_leave_mark(self):
        self.upload(make_pdf())
        self.client.post('/track-leave/')

        response = self.client.post('/heartbeat/')

        document = Document.objects.get()
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(document.left_at)

    def test_heartbeat_for_a_deleted_document_says_gone(self):
        self.upload(make_pdf())
        Document.objects.get().delete_with_files()

        response = self.client.post('/heartbeat/')

        self.assertEqual(response.status_code, 410)

    def test_only_post_is_allowed(self):
        response = self.client.get('/heartbeat/')

        self.assertEqual(response.status_code, 405)


class SendForSigningTests(SignElectricTestCase):
    def test_one_email_per_person(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])

        document = Document.objects.get()
        self.assertEqual(document.signers.count(), 2)
        self.assertEqual(document.placeholders.count(), 3)
        self.assertEqual(len(mail.outbox), 2)

    def test_email_shows_who_sent_it(self):
        self.send([placeholder('Ana', 'ana@example.com')])

        email = mail.outbox[0]
        self.assertIn('boss@example.com via SignElectric', email.from_email)
        self.assertEqual(email.reply_to, ['boss@example.com'])

    def test_invitation_is_html_with_the_button(self):
        self.send([placeholder('Ana', 'ana@example.com')])

        email = mail.outbox[0]
        self.assertEqual(email.content_subtype, 'html')
        self.assertIn('Open and sign', email.body)

    def test_document_is_marked_as_sent(self):
        self.send([placeholder('Ana', 'ana@example.com')])

        document = Document.objects.get()
        self.assertEqual(document.sender_email, 'boss@example.com')
        self.assertIsNotNone(document.sent_at)

    def test_wrong_sender_email_is_rejected(self):
        self.upload(make_png(), 'multi')
        placeholders = json.dumps([placeholder('Ana', 'ana@example.com')])

        response = self.client.post('/send/', {
            'sender_email': 'not an email',
            'placeholders': placeholders,
            'page_1': make_jpeg(),
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_sending_without_placeholders_is_rejected(self):
        response = self.send([])

        result = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(result['error'], 'Add at least one placeholder before sending.')

    def test_wrong_signer_email_is_rejected(self):
        response = self.send([placeholder('Ana', 'ana-at-example')])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_page_image_is_rejected(self):
        self.upload(make_png(), 'multi')
        placeholders = json.dumps([placeholder('Ana', 'ana@example.com')])

        response = self.client.post('/send/', {
            'sender_email': 'boss@example.com',
            'placeholders': placeholders,
        })

        result = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(result['error'], 'Page 1 is missing.')

    def test_sign_only_by_me_document_cannot_be_sent(self):
        self.upload(make_png(), 'solo')
        placeholders = json.dumps([placeholder('Ana', 'ana@example.com')])

        response = self.client.post('/send/', {
            'sender_email': 'boss@example.com',
            'placeholders': placeholders,
            'page_1': make_jpeg(),
        })

        self.assertEqual(response.status_code, 400)

    def test_only_post_is_allowed(self):
        response = self.client.get('/send/')

        self.assertEqual(response.status_code, 405)


class SignDocumentTests(SignElectricTestCase):
    def test_opening_the_link_shows_the_document_but_does_not_sign(self):
        self.send([placeholder('Ana', 'ana@example.com')])
        ana = Signer.objects.get()

        ana_browser = self.client_class()
        response = ana_browser.get(f'/sign/{ana.token}/')

        ana.refresh_from_db()
        self.assertContains(response, 'Sign')
        self.assertIsNone(ana.signed_at)

    def test_signing_sends_the_pdf_to_sender_and_signer(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        ana = Signer.objects.get(email='ana@example.com')
        mail.outbox = []

        ana_browser = self.client_class()
        ana_browser.post(f'/sign/{ana.token}/')

        email = mail.outbox[0]
        self.assertEqual(email.to, ['boss@example.com', 'ana@example.com'])
        self.assertEqual(email.attachments[0].mimetype, 'application/pdf')
        self.assertEqual(email.content_subtype, 'html')
        self.assertIn('Still waiting for', email.body)
        self.assertIn('Dan (dan@example.com)', email.body)

    def test_document_is_deleted_when_everyone_signed(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        ana = Signer.objects.get(email='ana@example.com')
        dan = Signer.objects.get(email='dan@example.com')

        ana_browser = self.client_class()
        ana_browser.post(f'/sign/{ana.token}/')
        self.assertEqual(Document.objects.count(), 1)

        folder = Document.objects.get().folder_path
        dan_browser = self.client_class()
        dan_browser.post(f'/sign/{dan.token}/')
        self.assertEqual(Document.objects.count(), 0)
        self.assertFalse(os.path.exists(folder))

    def test_signing_twice_says_already_signed(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        ana = Signer.objects.get(email='ana@example.com')
        ana_browser = self.client_class()
        ana_browser.post(f'/sign/{ana.token}/')

        response = ana_browser.get(f'/sign/{ana.token}/')

        self.assertContains(response, 'You already signed')

    def test_unknown_link_says_no_longer_active(self):
        random_token = uuid.uuid4()
        response = self.client.get(f'/sign/{random_token}/')

        self.assertContains(response, 'This link is no longer active')
