import io
import json

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
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


class SignElectricTests(TestCase):
    def tearDown(self):
        for document in Document.objects.all():
            document.delete_with_files()

    def test_pdf_upload_creates_pages(self):
        self.client.post('/', {'file': make_pdf()})

        document = Document.objects.get()
        self.assertEqual(document.filename, 'contract')
        self.assertEqual(document.pages.count(), 3)

    def test_image_upload_creates_one_page(self):
        self.client.post('/', {'file': make_png()})

        document = Document.objects.get()
        self.assertEqual(document.pages.count(), 1)

    def test_txt_file_is_rejected(self):
        text_file = SimpleUploadedFile('notes.txt', b'hello')
        self.client.post('/', {'file': text_file})

        self.assertEqual(Document.objects.count(), 0)

    def test_fake_pdf_is_rejected(self):
        fake_pdf = SimpleUploadedFile('fake.pdf', b'not a pdf')
        self.client.post('/', {'file': fake_pdf})

        self.assertEqual(Document.objects.count(), 0)

    def test_owner_can_open_the_editor(self):
        self.client.post('/', {'file': make_pdf()})
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')
        self.assertEqual(response.status_code, 200)

    def test_stranger_cannot_open_the_editor(self):
        self.client.post('/', {'file': make_pdf()})
        document = Document.objects.get()

        stranger = self.client_class()
        response = stranger.get(f'/editor/{document.uid}/')
        self.assertEqual(response.status_code, 302)

    def test_leaving_marks_but_does_not_delete(self):
        self.client.post('/', {'file': make_pdf()})
        self.client.post('/track-leave/')

        document = Document.objects.get()
        self.assertIsNotNone(document.left_at)

    def test_heartbeat_clears_the_leave_mark(self):
        self.client.post('/', {'file': make_pdf()})
        self.client.post('/track-leave/')
        self.client.post('/heartbeat/')

        document = Document.objects.get()
        self.assertIsNone(document.left_at)


def make_jpeg():
    image = Image.new('RGB', (1250, 1667), 'white')

    buffer = io.BytesIO()
    image.save(buffer, 'JPEG')
    return SimpleUploadedFile('page.jpg', buffer.getvalue())


def placeholder(name, email):
    return {
        'name': name,
        'email': email,
        'font_key': 'MrDeHaviland',
        'color': '#1e3a8a',
        'page_number': 1,
        'left': 100,
        'top': 100,
        'width': 300,
        'height': 60,
        'font_size': 40,
    }


class MultipleSigningTests(TestCase):
    def tearDown(self):
        for document in Document.objects.all():
            document.delete_with_files()

    def send_to(self, placeholders):
        self.client.post('/', {'file': make_png(), 'mode': 'multi'})
        return self.client.post('/send/', {
            'sender_email': 'boss@example.com',
            'placeholders': json.dumps(placeholders),
            'page_1': make_jpeg(),
        })

    def test_one_email_per_person(self):
        self.send_to([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])

        document = Document.objects.get()
        self.assertEqual(document.signers.count(), 2)
        self.assertEqual(document.placeholders.count(), 3)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('boss@example.com via SignElectric', mail.outbox[0].from_email)

    def test_signing_sends_the_pdf_to_sender_and_signer(self):
        self.send_to([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        ana = Signer.objects.get(email='ana@example.com')
        mail.outbox = []

        self.client_class().post(f'/sign/{ana.token}/')

        email = mail.outbox[0]
        self.assertEqual(email.to, ['boss@example.com', 'ana@example.com'])
        self.assertEqual(email.attachments[0].mimetype, 'application/pdf')

    def test_document_is_deleted_when_everyone_signed(self):
        self.send_to([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        ana = Signer.objects.get(email='ana@example.com')
        dan = Signer.objects.get(email='dan@example.com')

        self.client_class().post(f'/sign/{ana.token}/')
        self.assertEqual(Document.objects.count(), 1)

        self.client_class().post(f'/sign/{dan.token}/')
        self.assertEqual(Document.objects.count(), 0)

    def test_opening_the_link_does_not_sign(self):
        self.send_to([placeholder('Ana', 'ana@example.com')])
        ana = Signer.objects.get()

        self.client_class().get(f'/sign/{ana.token}/')

        ana.refresh_from_db()
        self.assertIsNone(ana.signed_at)

    def test_signing_works_with_a_normal_font(self):
        courier_placeholder = placeholder('Ana', 'ana@example.com')
        courier_placeholder['font_key'] = 'CourierNew'
        self.send_to([courier_placeholder])
        ana = Signer.objects.get()
        mail.outbox = []

        self.client_class().post(f'/sign/{ana.token}/')

        self.assertEqual(mail.outbox[0].attachments[0].mimetype, 'application/pdf')
