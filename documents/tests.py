import hashlib
import io
import json
import os
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image, ImageDraw
import pymupdf

from .autoplace import (
    find_lone_drawn_lines,
    find_lone_underlines,
    find_name_label_spots,
    find_signature_spots,
    read_words,
    to_page_pixels,
)
from .models import AuditEvent, Document, Page, Placeholder, Signer, document_upload_path
from .signing import add_overlay, color_of, from_address, sign_link, write_signature
from .views import is_valid_email


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


def make_overlay(width=400, height=300):
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle([0, 0, 40, 40], fill='red')

    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return SimpleUploadedFile('overlay.png', buffer.getvalue(), content_type='image/png')


def to_points(pixels):
    return pixels * 72 / 200


def red_area(pdf_page):
    pixmap = pdf_page.get_pixmap()
    xs = []
    ys = []
    for y in range(0, pixmap.height, 2):
        for x in range(0, pixmap.width, 2):
            red, green, blue = pixmap.pixel(x, y)
            if red > 200 and green < 100 and blue < 100:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def make_text_pdf(lines, stamp=None, font=None, drawn=None, rects=None):
    pdf = pymupdf.open()
    page = pdf.new_page()
    if drawn is not None:
        for x0, y0, x1, y1 in drawn:
            page.draw_line((x0, y0), (x1, y1))
    if rects is not None:
        for x0, y0, x1, y1 in rects:
            page.draw_rect(pymupdf.Rect(x0, y0, x1, y1))
    for x, y, text in lines:
        if font is None:
            page.insert_text((x, y), text)
        else:
            writer = pymupdf.TextWriter(page.rect)
            writer.append((x, y), text, font=font)
            writer.write_text(page)
    if stamp is not None:
        page.add_stamp_annot(pymupdf.Rect(stamp))

    data = pdf.tobytes()
    pdf.close()
    return data


def read_first_page(lines, stamp=None, font=None, drawn=None, rects=None):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'contract.pdf')
        with open(path, 'wb') as file:
            file.write(make_text_pdf(lines, stamp, font, drawn, rects))
        pages = read_words(path)
    return pages[0]


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
    def setUp(self):
        self.user = User.objects.create_user('boss', 'boss@example.com', 'a-long-password-123')
        self.client.force_login(self.user)

    def tearDown(self):
        for document in Document.objects.all():
            document.delete_with_files()

    def editor_url(self, action):
        document = Document.objects.last()
        if document is None:
            return f'/editor/{uuid.uuid4()}/{action}/'
        return f'/editor/{document.uid}/{action}/'

    def upload(self, file, mode='solo'):
        return self.client.post('/', {'file': file, 'mode': mode})

    def send(self, placeholders):
        self.upload(make_png(), 'multi')
        return self.client.post(self.editor_url('send'), {
            'placeholders': json.dumps(placeholders),
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(),
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

    def test_two_uploads_can_be_edited_at_the_same_time(self):
        self.upload(make_pdf())
        self.upload(make_png())

        for document in Document.objects.all():
            response = self.client.get(f'/editor/{document.uid}/')
            self.assertEqual(response.status_code, 200)


class CleanupTests(SignElectricTestCase):
    def setUp(self):
        super().setUp()
        self.upload(make_pdf())
        self.document = Document.objects.get()

    def make_old(self, hours):
        Document.objects.filter(id=self.document.id).update(
            created_at=timezone.now() - timedelta(hours=hours),
        )

    def test_draft_stays_for_a_day(self):
        self.make_old(23)

        self.client.get('/')

        self.assertEqual(Document.objects.count(), 1)

    def test_draft_is_deleted_after_a_day(self):
        self.make_old(25)

        self.client.get('/')

        self.assertEqual(Document.objects.count(), 0)
        self.assertFalse(os.path.exists(self.document.folder_path))

    def test_locked_folder_is_skipped_and_deleted_later(self):
        self.make_old(25)

        with patch('shutil.rmtree', side_effect=PermissionError):
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Document.objects.count(), 1)

        self.client.get('/')
        self.assertEqual(Document.objects.count(), 0)


class EditorTests(SignElectricTestCase):
    def test_owner_can_open_the_editor(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertEqual(response.status_code, 200)

    def test_stranger_is_sent_to_the_login_page(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        stranger_browser = self.client_class()
        response = stranger_browser.get(f'/editor/{document.uid}/')

        self.assertRedirects(response, f'/login/?next=/editor/{document.uid}/', fetch_redirect_response=False)

    def test_deleted_document_shows_expired_message(self):
        self.upload(make_pdf())
        document = Document.objects.get()
        document.delete_with_files()

        response = self.client.get(f'/editor/{document.uid}/', follow=True)

        self.assertContains(response, 'Your document expired')

    def test_editor_knows_my_full_name(self):
        self.user.first_name = 'Ion'
        self.user.last_name = 'Pop'
        self.user.save()
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertContains(response, '"my_name": "Ion Pop"')

    def test_editor_title_is_the_document_name(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertContains(response, '<title>Sign contract</title>', html=True)

    def test_editor_uses_the_username_without_a_name(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertContains(response, '"my_name": "boss"')

    def test_pdf_shows_the_auto_place_button(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertContains(response, 'id="AutoPlaceButton"')

    def test_image_has_no_auto_place_button(self):
        self.upload(make_png())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertNotContains(response, 'id="AutoPlaceButton"')

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

    def test_account_without_email_cannot_send(self):
        self.user.email = ''
        self.user.save()

        response = self.send([placeholder('Ana', 'ana@example.com')])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Your account has no valid email.')
        self.assertEqual(len(mail.outbox), 0)

    def test_sent_document_cannot_be_sent_or_changed_again(self):
        self.send([placeholder('Ana', 'ana@example.com')])
        mail.outbox = []

        response = self.client.post(self.editor_url('send'), {
            'placeholders': json.dumps([placeholder('Dan', 'dan@example.com')]),
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(),
        })
        self.assertEqual(response.status_code, 410)

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay()})
        self.assertEqual(response.status_code, 410)

        self.assertEqual(Signer.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_editor_of_a_sent_document_opens_its_page(self):
        self.send([placeholder('Ana', 'ana@example.com')])
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertRedirects(response, f'/documents/{document.uid}/')

    def test_sending_goes_to_the_dashboard(self):
        response = self.send([placeholder('Ana', 'ana@example.com')])

        self.assertEqual(response.json()['redirect'], '/')

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

        response = self.client.post(self.editor_url('send'), {
            'placeholders': placeholders,
        })

        result = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(result['error'], 'Page 1 is missing.')

    def test_missing_overlay_is_rejected(self):
        self.upload(make_png(), 'multi')
        placeholders = json.dumps([placeholder('Ana', 'ana@example.com')])

        response = self.client.post(self.editor_url('send'), {
            'placeholders': placeholders,
            'page_1': make_jpeg(),
        })

        result = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(result['error'], 'The signatures of page 1 are missing.')
        self.assertEqual(len(mail.outbox), 0)

    def test_sign_only_by_me_document_cannot_be_sent(self):
        self.upload(make_png(), 'solo')
        placeholders = json.dumps([placeholder('Ana', 'ana@example.com')])

        response = self.client.post(self.editor_url('send'), {
            'placeholders': placeholders,
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(),
        })

        self.assertEqual(response.status_code, 400)

    def test_only_post_is_allowed(self):
        response = self.client.get(self.editor_url('send'))

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

    def test_document_is_kept_and_finished_when_everyone_signed(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        ana = Signer.objects.get(email='ana@example.com')
        dan = Signer.objects.get(email='dan@example.com')

        ana_browser = self.client_class()
        ana_browser.post(f'/sign/{ana.token}/')
        self.assertEqual(Document.objects.count(), 1)

        self.assertIsNone(Document.objects.get().finished_at)

        dan_browser = self.client_class()
        dan_browser.post(f'/sign/{dan.token}/')
        document = Document.objects.get()
        self.assertIsNotNone(document.finished_at)
        self.assertEqual(document.status, 'signed')
        self.assertTrue(os.path.exists(document.folder_path))

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

    def test_signed_pdf_keeps_the_text_and_writes_the_name(self):
        pdf = make_text_pdf([(72, 100, 'Contract Semnatura')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf), 'multi')
        page = Document.objects.get().pages.get()
        self.client.post(self.editor_url('send'), {
            'placeholders': json.dumps([placeholder('Ana Pop', 'ana@example.com')]),
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(page.width, page.height),
        })
        ana = Signer.objects.get()
        mail.outbox = []

        self.client_class().post(f'/sign/{ana.token}/')

        signed = pymupdf.open(stream=mail.outbox[0].attachments[0].content, filetype='pdf')
        text = signed[0].get_text()
        self.assertIn('Contract Semnatura', text)
        self.assertIn('Ana Pop', text)
        self.assertEqual(len(signed[0].get_images()), 1)

    def test_unknown_link_says_no_longer_active(self):
        random_token = uuid.uuid4()
        response = self.client.get(f'/sign/{random_token}/')

        self.assertContains(response, 'This link is no longer active')


class ExportTests(SignElectricTestCase):
    def test_export_keeps_the_text_of_the_pdf(self):
        pdf = make_text_pdf([(72, 100, 'Contract Semnatura')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))
        page = Document.objects.get().pages.get()

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay(page.width, page.height)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        signed = pymupdf.open(stream=response.content, filetype='pdf')
        self.assertIn('Contract Semnatura', signed[0].get_text())
        self.assertEqual(len(signed[0].get_images()), 1)

    def test_export_puts_the_overlay_on_the_page(self):
        pdf = make_text_pdf([(72, 100, 'Contract')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))
        page = Document.objects.get().pages.get()

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay(page.width, page.height)})

        signed = pymupdf.open(stream=response.content, filetype='pdf')
        left, top, right, bottom = red_area(signed[0])
        self.assertLess(left, 5)
        self.assertLess(top, 5)
        self.assertAlmostEqual(right, to_points(40), delta=3)

    def test_export_of_every_page(self):
        self.upload(make_pdf())

        response = self.client.post(self.editor_url('export'), {
            'overlay_1': make_overlay(),
            'overlay_2': make_overlay(),
            'overlay_3': make_overlay(),
        })

        signed = pymupdf.open(stream=response.content, filetype='pdf')
        self.assertEqual(signed.page_count, 4)
        self.assertIn('Signing record', signed[3].get_text())

    def test_export_of_an_image(self):
        self.upload(make_png())
        page = Document.objects.get().pages.get()

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay(page.width, page.height)})

        signed = pymupdf.open(stream=response.content, filetype='pdf')
        self.assertEqual(signed.page_count, 2)
        self.assertAlmostEqual(signed[0].rect.width, to_points(page.width), delta=1)

    def test_export_without_overlay_is_rejected(self):
        self.upload(make_pdf())

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay()})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'The signatures of page 2 are missing.')

    def test_export_needs_a_png(self):
        self.upload(make_png())

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_jpeg()})

        self.assertEqual(response.status_code, 400)

    def test_export_without_document(self):
        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay()})

        self.assertEqual(response.status_code, 410)

    def test_export_only_post_is_allowed(self):
        response = self.client.get(self.editor_url('export'))

        self.assertEqual(response.status_code, 405)


class DashboardTests(SignElectricTestCase):
    def export_solo(self):
        self.upload(make_png())
        self.client.post(self.editor_url('export'), {'overlay_1': make_overlay()})
        return Document.objects.get()

    def test_dashboard_needs_login(self):
        self.client.logout()

        response = self.client.get('/')

        self.assertRedirects(response, '/login/?next=/')

    def test_empty_dashboard(self):
        response = self.client.get('/')

        self.assertContains(response, 'You have no signed or sent documents yet.')

    def test_draft_is_not_shown(self):
        self.upload(make_png())
        document = Document.objects.get()

        response = self.client.get('/')
        self.assertNotContains(response, document.filename)

        response = self.client.get(f'/documents/{document.uid}/')
        self.assertRedirects(response, '/')

    def test_exported_solo_document_is_kept_and_shown(self):
        document = self.export_solo()

        self.assertEqual(document.status, 'signed')
        response = self.client.get('/')
        self.assertContains(response, 'photo')
        self.assertContains(response, 'Signed by me')

    def test_signed_document_is_not_cleaned_up(self):
        document = self.export_solo()
        Document.objects.filter(id=document.id).update(created_at=timezone.now() - timedelta(days=3))

        self.client.get('/')

        self.assertEqual(Document.objects.count(), 1)

    def test_multiple_signing_shows_how_many_signed(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        response = self.client.get('/')
        self.assertContains(response, '0 of 2 signed')

        ana = Signer.objects.get(email='ana@example.com')
        self.client_class().post(f'/sign/{ana.token}/')

        response = self.client.get('/')
        self.assertContains(response, '1 of 2 signed')

        dan = Signer.objects.get(email='dan@example.com')
        self.client_class().post(f'/sign/{dan.token}/')

        response = self.client.get('/')
        self.assertContains(response, 'Signed by all 2')

    def test_detail_shows_who_signed(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        document = Document.objects.get()
        ana = Signer.objects.get(email='ana@example.com')
        self.client_class().post(f'/sign/{ana.token}/')

        response = self.client.get(f'/documents/{document.uid}/')

        self.assertContains(response, '1 of 2 people signed')
        self.assertContains(response, 'ana@example.com')
        self.assertContains(response, 'Waiting')

    def test_pdf_has_the_signatures_so_far(self):
        pdf = make_text_pdf([(72, 100, 'Contract Semnatura')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf), 'multi')
        page = Document.objects.get().pages.get()
        self.client.post(self.editor_url('send'), {
            'placeholders': json.dumps([
                placeholder('Ana Pop', 'ana@example.com'),
                placeholder('Dan Ionescu', 'dan@example.com'),
            ]),
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(page.width, page.height),
        })
        document = Document.objects.get()
        ana = Signer.objects.get(email='ana@example.com')
        self.client_class().post(f'/sign/{ana.token}/')

        response = self.client.get(f'/documents/{document.uid}/pdf/')

        self.assertEqual(response['Content-Type'], 'application/pdf')
        signed = pymupdf.open(stream=b''.join(response.streaming_content), filetype='pdf')
        text = signed[0].get_text()
        self.assertIn('Ana Pop', text)
        self.assertNotIn('Dan Ionescu', text)

    def test_pdf_can_be_downloaded(self):
        document = self.export_solo()

        response = self.client.get(f'/documents/{document.uid}/pdf/?download=1')

        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('photo.pdf', response['Content-Disposition'])

    def test_other_user_cannot_see_the_document(self):
        document = self.export_solo()
        other = User.objects.create_user('maria', 'maria@example.com', 'a-long-password-123')
        self.client.force_login(other)

        response = self.client.get('/')
        self.assertNotContains(response, 'photo')

        response = self.client.get(f'/documents/{document.uid}/')
        self.assertEqual(response.status_code, 404)

        response = self.client.get(f'/documents/{document.uid}/pdf/')
        self.assertEqual(response.status_code, 404)


class ReminderTests(SignElectricTestCase):
    def setUp(self):
        super().setUp()
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        self.document = Document.objects.get()
        ana = Signer.objects.get(email='ana@example.com')
        self.client_class().post(f'/sign/{ana.token}/')
        mail.outbox = []

    def remind(self):
        return self.client.post(f'/documents/{self.document.uid}/remind/', follow=True)

    def test_reminder_goes_only_to_people_who_did_not_sign(self):
        response = self.remind()

        self.assertContains(response, 'A reminder was sent to 1 person.')
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['dan@example.com'])
        self.assertIn('Reminder', email.subject)
        self.assertIn('still waiting for you to sign', email.body)
        dan = Signer.objects.get(email='dan@example.com')
        self.assertIn(str(dan.token), email.body)

    def test_detail_shows_the_reminder_button(self):
        response = self.client.get(f'/documents/{self.document.uid}/')

        self.assertContains(response, 'Send reminder')

    def test_only_one_reminder_per_hour(self):
        self.remind()
        mail.outbox = []

        response = self.remind()

        self.assertContains(response, 'You already sent a reminder in the last hour.')
        self.assertEqual(len(mail.outbox), 0)

    def test_reminder_can_be_sent_again_after_an_hour(self):
        self.remind()
        Document.objects.filter(id=self.document.id).update(
            reminded_at=timezone.now() - timedelta(minutes=61),
        )
        mail.outbox = []

        self.remind()

        self.assertEqual(len(mail.outbox), 1)

    def test_no_reminder_when_everyone_signed(self):
        dan = Signer.objects.get(email='dan@example.com')
        self.client_class().post(f'/sign/{dan.token}/')
        mail.outbox = []

        response = self.remind()

        self.assertContains(response, 'This document is not waiting for anyone to sign.')
        self.assertNotContains(response, 'Send reminder')
        self.assertEqual(len(mail.outbox), 0)

    def test_failed_email_shows_an_error(self):
        with patch('documents.views.send_reminders', side_effect=OSError):
            response = self.remind()

        self.assertContains(response, 'The emails could not be sent.')
        self.assertIsNone(Document.objects.get().reminded_at)

    def test_other_user_cannot_send_a_reminder(self):
        other = User.objects.create_user('maria', 'maria@example.com', 'a-long-password-123')
        self.client.force_login(other)

        response = self.client.post(f'/documents/{self.document.uid}/remind/')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_only_post_is_allowed(self):
        response = self.client.get(f'/documents/{self.document.uid}/remind/')

        self.assertEqual(response.status_code, 405)


class SentToMeTests(SignElectricTestCase):
    def setUp(self):
        super().setUp()
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        self.document = Document.objects.get()
        self.ana = User.objects.create_user('ana', 'Ana@Example.com', 'a-long-password-123')
        self.client.force_login(self.ana)

    def test_document_sent_before_the_account_is_shown(self):
        response = self.client.get('/?show=received')

        self.assertContains(response, 'photo')
        self.assertContains(response, 'From boss@example.com')
        self.assertContains(response, 'Waiting for you')
        self.assertContains(response, '<span class="count">1</span>', html=True)

    def test_my_documents_tab_does_not_show_it(self):
        response = self.client.get('/')

        self.assertContains(response, 'You have no signed or sent documents yet.')

    def test_signer_can_open_the_document_and_sign(self):
        signer = Signer.objects.get(email='ana@example.com')

        response = self.client.get(f'/documents/{self.document.uid}/')

        self.assertContains(response, 'Sent by boss@example.com')
        self.assertContains(response, f'/sign/{signer.token}/')
        self.assertNotContains(response, 'Send reminder')

    def test_after_signing_it_says_you_signed(self):
        signer = Signer.objects.get(email='ana@example.com')
        response = self.client.post(f'/sign/{signer.token}/')
        self.assertContains(response, 'Back to my documents')

        response = self.client.get('/?show=received')
        self.assertContains(response, 'You signed')
        self.assertContains(response, '1 of 2 signed')

        response = self.client.get(f'/documents/{self.document.uid}/')
        self.assertNotContains(response, 'Sign now')

    def test_signer_can_get_the_pdf(self):
        response = self.client.get(f'/documents/{self.document.uid}/pdf/')

        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_signer_cannot_send_a_reminder(self):
        mail.outbox = []

        response = self.client.post(f'/documents/{self.document.uid}/remind/')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_other_people_cannot_see_it(self):
        stranger = User.objects.create_user('maria', 'maria@example.com', 'a-long-password-123')
        self.client.force_login(stranger)

        response = self.client.get('/?show=received')
        self.assertContains(response, 'Nobody has sent you a document to sign yet.')

        response = self.client.get(f'/documents/{self.document.uid}/')
        self.assertEqual(response.status_code, 404)

        response = self.client.get(f'/documents/{self.document.uid}/pdf/')
        self.assertEqual(response.status_code, 404)

    def test_account_without_email_sees_nothing(self):
        nobody = User.objects.create_user('nobody', '', 'a-long-password-123')
        self.client.force_login(nobody)

        response = self.client.get('/?show=received')

        self.assertContains(response, 'Nobody has sent you a document to sign yet.')


class FileAccessTests(SignElectricTestCase):
    def test_owner_gets_the_page_image_and_the_original(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/page/1/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')

        response = self.client.get(f'/editor/{document.uid}/original/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('contract.pdf', response['Content-Disposition'])

    def test_editor_uses_the_protected_addresses(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/')

        self.assertContains(response, f'/editor/{document.uid}/page/1/')
        self.assertContains(response, f'/editor/{document.uid}/original/')
        self.assertNotContains(response, '/media/')

    def test_missing_page_is_not_found(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get(f'/editor/{document.uid}/page/99/')

        self.assertEqual(response.status_code, 404)

    def test_other_user_and_logged_out_get_nothing(self):
        self.upload(make_pdf())
        document = Document.objects.get()
        other = User.objects.create_user('maria', 'maria@example.com', 'a-long-password-123')
        self.client.force_login(other)

        response = self.client.get(f'/editor/{document.uid}/page/1/')
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f'/editor/{document.uid}/original/')
        self.assertEqual(response.status_code, 404)

        self.client.logout()
        response = self.client.get(f'/editor/{document.uid}/page/1/')
        self.assertEqual(response.status_code, 302)

    def test_media_folder_is_not_public(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        response = self.client.get('/media/documents/' + str(document.uid) + '/original.pdf')

        self.assertEqual(response.status_code, 404)

    def test_signer_gets_the_page_image_by_token(self):
        self.send([placeholder('Ana', 'ana@example.com')])
        signer = Signer.objects.get()

        response = self.client_class().get(f'/sign/{signer.token}/')
        self.assertContains(response, f'/sign/{signer.token}/page/1/')

        response = self.client_class().get(f'/sign/{signer.token}/page/1/')
        self.assertEqual(response.status_code, 200)

        response = self.client_class().get(f'/sign/{uuid.uuid4()}/page/1/')
        self.assertEqual(response.status_code, 404)


class CancelAndDeleteTests(SignElectricTestCase):
    def setUp(self):
        super().setUp()
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        self.document = Document.objects.get()
        self.ana = Signer.objects.get(email='ana@example.com')
        self.dan = Signer.objects.get(email='dan@example.com')
        self.client_class().post(f'/sign/{self.ana.token}/')

    def cancel(self):
        return self.client.post(f'/documents/{self.document.uid}/cancel/', follow=True)

    def test_owner_sees_cancel_and_delete(self):
        response = self.client.get(f'/documents/{self.document.uid}/')

        self.assertContains(response, 'Cancel signing')
        self.assertContains(response, 'Delete document')

    def test_cancel_stops_the_signing_links(self):
        response = self.cancel()

        self.assertContains(response, 'The document was cancelled.')
        self.assertContains(response, '1 of 2 people had signed.')
        self.assertNotContains(response, 'Send reminder')
        self.assertEqual(Document.objects.get().status, 'cancelled')

        response = self.client_class().get(f'/sign/{self.dan.token}/')
        self.assertContains(response, 'This document was cancelled')
        response = self.client_class().post(f'/sign/{self.dan.token}/')
        self.assertContains(response, 'This document was cancelled')
        self.assertIsNone(Signer.objects.get(email='dan@example.com').signed_at)

        response = self.client_class().get(f'/sign/{self.dan.token}/page/1/')
        self.assertEqual(response.status_code, 404)

    def test_cancelled_document_shows_in_the_dashboard(self):
        self.cancel()

        response = self.client.get('/')

        self.assertContains(response, '<span class="badge cancelled">Cancelled</span>', html=True)

    def test_cancelled_document_cannot_get_a_reminder(self):
        self.cancel()
        mail.outbox = []

        response = self.client.post(f'/documents/{self.document.uid}/remind/', follow=True)

        self.assertContains(response, 'This document is not waiting for anyone to sign.')
        self.assertEqual(len(mail.outbox), 0)

    def test_finished_document_cannot_be_cancelled(self):
        self.client_class().post(f'/sign/{self.dan.token}/')

        response = self.cancel()

        self.assertContains(response, 'Only a document that is waiting for signatures can be cancelled.')
        self.assertEqual(Document.objects.get().status, 'signed')

    def test_delete_removes_the_document_and_its_files(self):
        folder = self.document.folder_path

        response = self.client.post(f'/documents/{self.document.uid}/delete/', follow=True)

        self.assertRedirects(response, '/')
        self.assertContains(response, '&quot;photo&quot; was deleted.')
        self.assertEqual(Document.objects.count(), 0)
        self.assertFalse(os.path.exists(folder))

        response = self.client_class().get(f'/sign/{self.dan.token}/')
        self.assertContains(response, 'This link is no longer active')

    def test_locked_files_show_an_error(self):
        with patch('shutil.rmtree', side_effect=PermissionError):
            response = self.client.post(f'/documents/{self.document.uid}/delete/', follow=True)

        self.assertContains(response, 'could not be deleted right now')
        self.assertEqual(Document.objects.count(), 1)

    def test_signer_cannot_cancel_or_delete(self):
        ana_user = User.objects.create_user('ana', 'ana@example.com', 'a-long-password-123')
        self.client.force_login(ana_user)

        response = self.client.get(f'/documents/{self.document.uid}/')
        self.assertNotContains(response, 'Cancel signing')
        self.assertNotContains(response, 'Delete document')

        response = self.client.post(f'/documents/{self.document.uid}/cancel/')
        self.assertEqual(response.status_code, 404)
        response = self.client.post(f'/documents/{self.document.uid}/delete/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Document.objects.get().status, 'waiting')

    def test_signer_sees_cancelled_without_sign_now(self):
        self.cancel()
        dan_user = User.objects.create_user('dan', 'dan@example.com', 'a-long-password-123')
        self.client.force_login(dan_user)

        response = self.client.get('/?show=received')
        self.assertContains(response, '<span class="badge cancelled">Cancelled</span>', html=True)
        self.assertNotContains(response, '<span class="count">')

        response = self.client.get(f'/documents/{self.document.uid}/')
        self.assertNotContains(response, 'Sign now')

    def test_only_post_is_allowed(self):
        response = self.client.get(f'/documents/{self.document.uid}/cancel/')
        self.assertEqual(response.status_code, 405)
        response = self.client.get(f'/documents/{self.document.uid}/delete/')
        self.assertEqual(response.status_code, 405)


class DashboardFilterTests(SignElectricTestCase):
    def setUp(self):
        super().setUp()
        self.upload(SimpleUploadedFile('Rental agreement.pdf', make_text_pdf([(72, 100, 'Rent')])))
        self.client.post(self.editor_url('export'), {'overlay_1': make_overlay(600, 800)})
        self.send([placeholder('Ana', 'ana@example.com')])

    def test_search_by_name(self):
        response = self.client.get('/?search=rental')

        self.assertContains(response, '<div class="name">Rental agreement</div>', html=True)
        self.assertNotContains(response, '<div class="name">photo</div>', html=True)

    def test_filter_by_status(self):
        response = self.client.get('/?status=waiting')

        self.assertContains(response, '<div class="name">photo</div>', html=True)
        self.assertNotContains(response, '<div class="name">Rental agreement</div>', html=True)

    def test_nothing_found(self):
        response = self.client.get('/?search=invoice')

        self.assertContains(response, 'No documents match your search.')
        self.assertContains(response, 'Clear')

    def test_search_in_sent_to_me_by_sender(self):
        ana = User.objects.create_user('ana', 'ana@example.com', 'a-long-password-123')
        self.client.force_login(ana)

        response = self.client.get('/?show=received&search=boss')
        self.assertContains(response, '<div class="name">photo</div>', html=True)

        response = self.client.get('/?show=received&search=nobody')
        self.assertContains(response, 'No documents match your search.')


class RequireLoginTests(SignElectricTestCase):
    def setUp(self):
        super().setUp()
        self.upload(make_png(), 'multi')
        self.client.post(self.editor_url('send'), {
            'placeholders': json.dumps([placeholder('Ana', 'ana@example.com')]),
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(),
            'require_login': 'yes',
        })
        self.document = Document.objects.get()
        self.signer = Signer.objects.get()
        self.link = f'/sign/{self.signer.token}/'

    def test_option_is_saved_and_shown(self):
        self.assertTrue(self.document.require_login)
        self.assertIn('log in with ana@example.com', mail.outbox[0].body)

        response = self.client.get(f'/documents/{self.document.uid}/')
        self.assertContains(response, 'Signers must log in with their email to sign.')

    def test_without_the_option_anyone_with_the_link_can_sign(self):
        self.upload(make_png(), 'multi')
        self.client.post(self.editor_url('send'), {
            'placeholders': json.dumps([placeholder('Dan', 'dan@example.com')]),
            'page_1': make_jpeg(),
            'overlay_1': make_overlay(),
        })
        dan = Signer.objects.get(email='dan@example.com')

        self.assertFalse(dan.document.require_login)
        response = self.client_class().get(f'/sign/{dan.token}/')
        self.assertContains(response, 'asks you to sign')

    def test_logged_out_visitor_is_sent_to_log_in(self):
        response = self.client_class().get(self.link)

        self.assertRedirects(response, f'/login/?next={self.link}', fetch_redirect_response=False)
        response = self.client_class().post(self.link)
        self.assertIsNone(Signer.objects.get().signed_at)
        response = self.client_class().get(self.link + 'page/1/')
        self.assertEqual(response.status_code, 404)

    def test_other_account_cannot_sign(self):
        other = User.objects.create_user('maria', 'maria@example.com', 'a-long-password-123')
        self.client.force_login(other)

        response = self.client.post(self.link)

        self.assertContains(response, 'This document is for someone else')
        self.assertIsNone(Signer.objects.get().signed_at)

    def test_the_right_account_can_sign(self):
        ana = User.objects.create_user('ana', 'Ana@Example.com', 'a-long-password-123')
        self.client.force_login(ana)

        response = self.client.get(self.link)
        self.assertContains(response, 'asks you to sign')
        response = self.client.get(self.link + 'page/1/')
        self.assertEqual(response.status_code, 200)

        self.client.post(self.link)
        self.assertIsNotNone(Signer.objects.get().signed_at)


class AuditTrailTests(SignElectricTestCase):
    def record_text(self, pdf_bytes):
        signed = pymupdf.open(stream=pdf_bytes, filetype='pdf')
        return signed[signed.page_count - 1].get_text()

    def test_upload_saves_the_fingerprint_and_an_event(self):
        self.upload(make_pdf())
        document = Document.objects.get()

        with open(document.file.path, 'rb') as file:
            expected = hashlib.sha256(file.read()).hexdigest()
        self.assertEqual(document.original_hash, expected)
        self.assertEqual(document.events.get().text, 'Uploaded by boss (boss@example.com)')
        self.assertEqual(document.events.get().ip, '127.0.0.1')

    def test_solo_pdf_ends_with_the_signing_record(self):
        self.user.first_name = 'Ion'
        self.user.last_name = 'Ștefănescu'
        self.user.save()
        self.upload(make_png())

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay()})

        text = self.record_text(response.content)
        document = Document.objects.get()
        self.assertIn('Signing record', text)
        self.assertIn(str(document.uid), text)
        self.assertIn(document.original_hash, text)
        self.assertIn('Signed by Ion Ștefănescu (boss@example.com)', text)
        self.assertIn('127.0.0.1', text)

    def test_multiple_signing_history(self):
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        document = Document.objects.get()
        ana = Signer.objects.get(email='ana@example.com')
        dan = Signer.objects.get(email='dan@example.com')
        ana_browser = self.client_class()
        ana_browser.get(f'/sign/{ana.token}/')
        ana_browser.get(f'/sign/{ana.token}/')
        ana_browser.post(f'/sign/{ana.token}/')
        self.client.post(f'/documents/{document.uid}/remind/')
        self.client_class().post(f'/sign/{dan.token}/')

        texts = []
        for event in document.events.order_by('created_at', 'id'):
            texts.append(event.text)
        self.assertEqual(texts, [
            'Uploaded by boss (boss@example.com)',
            'Sent for signing by boss (boss@example.com) to Ana (ana@example.com), Dan (dan@example.com)',
            'Opened by Ana (ana@example.com)',
            'Signed by Ana (ana@example.com)',
            'Reminder sent to 1 person who did not sign yet',
            'Signed by Dan (dan@example.com)',
            'Signed by everyone',
        ])

        response = self.client.get(f'/documents/{document.uid}/pdf/')
        text = self.record_text(b''.join(response.streaming_content))
        self.assertIn('Signed by everyone', text)
        self.assertIn('ana@example.com', text)

    def test_emailed_copy_has_the_record(self):
        self.send([placeholder('Ana', 'ana@example.com')])
        ana = Signer.objects.get()
        mail.outbox = []

        self.client_class().post(f'/sign/{ana.token}/')

        text = self.record_text(mail.outbox[0].attachments[0].content)
        self.assertIn('Signed by Ana (ana@example.com)', text)

    def test_cancel_is_recorded(self):
        self.send([placeholder('Ana', 'ana@example.com')])
        document = Document.objects.get()

        self.client.post(f'/documents/{document.uid}/cancel/')

        self.assertTrue(document.events.filter(text='Cancelled by boss (boss@example.com)').exists())

    def test_failed_email_does_not_keep_the_signed_event(self):
        self.send([placeholder('Ana', 'ana@example.com')])
        ana = Signer.objects.get()

        with patch('documents.views.send_signed_copy', side_effect=OSError):
            self.client_class().post(f'/sign/{ana.token}/')

        document = Document.objects.get()
        self.assertFalse(document.events.filter(text__startswith='Signed by Ana').exists())

    def test_long_history_goes_on_more_pages(self):
        self.upload(make_png())
        document = Document.objects.get()
        for number in range(30):
            AuditEvent.objects.create(document=document, text=f'Event number {number}')

        response = self.client.post(self.editor_url('export'), {'overlay_1': make_overlay()})

        signed = pymupdf.open(stream=response.content, filetype='pdf')
        self.assertEqual(signed.page_count, 3)
        self.assertIn('Event number 29', signed[2].get_text())


class UnitTests(TestCase):
    def test_upload_path_uses_the_uid_and_a_lowercase_extension(self):
        document = Document()

        path = document_upload_path(document, 'Contract.DOCX')

        expected = os.path.join('documents', str(document.uid), 'original.docx')
        self.assertEqual(path, expected)

    def test_kind_of_a_pdf(self):
        document = Document(file='documents/abc/original.pdf')

        self.assertEqual(document.kind, 'pdf')

    def test_kind_of_a_word_file(self):
        document = Document(file='documents/abc/original.docx')

        self.assertEqual(document.kind, 'word')

    def test_kind_of_an_image(self):
        document = Document(file='documents/abc/original.png')

        self.assertEqual(document.kind, 'image')

    def test_valid_email(self):
        self.assertTrue(is_valid_email('ana@example.com'))

    def test_invalid_email(self):
        self.assertFalse(is_valid_email('ana-at-example'))

    def test_from_address(self):
        with self.settings(DEFAULT_FROM_EMAIL='site@example.com'):
            address = from_address('Ana Pop')

        self.assertEqual(address, 'Ana Pop via SignElectric <site@example.com>')

    def test_sign_link(self):
        signer = Signer()

        with self.settings(SITE_URL='http://example.com'):
            link = sign_link(signer)

        self.assertEqual(link, f'http://example.com/sign/{signer.token}/')

    def test_everyone_signed(self):
        document = Document.objects.create(filename='Contract', file='documents/abc/original.pdf')
        ana = Signer.objects.create(document=document, name='Ana', email='ana@example.com')
        dan = Signer.objects.create(document=document, name='Dan', email='dan@example.com')

        ana.signed_at = timezone.now()
        ana.save()
        self.assertFalse(document.everyone_signed())

        dan.signed_at = timezone.now()
        dan.save()
        self.assertTrue(document.everyone_signed())

    def test_color_of(self):
        self.assertEqual(color_of('#ff0000'), (1, 0, 0))

    def test_write_signature_writes_the_name_as_text(self):
        pdf = pymupdf.open()
        pdf_page = pdf.new_page(width=400, height=200)
        page = Page(width=400, height=200)
        signer = Signer(name='Ana Pop')
        box = Placeholder(signer=signer, left=50, top=50, width=300, height=100, font_size=40, color='#ff0000')

        write_signature(pdf_page, box, page)

        self.assertIn('Ana Pop', pdf_page.get_text())
        left, top, right, bottom = red_area(pdf_page)
        self.assertAlmostEqual((left + right) / 2, 200, delta=10)
        self.assertAlmostEqual((top + bottom) / 2, 100, delta=10)

    def test_write_signature_on_a_rotated_page(self):
        pdf = pymupdf.open()
        pdf_page = pdf.new_page(width=200, height=400)
        pdf_page.set_rotation(90)
        page = Page(width=400, height=200)
        signer = Signer(name='Ana Pop')
        box = Placeholder(signer=signer, left=50, top=50, width=300, height=100, font_size=40, color='#ff0000')

        write_signature(pdf_page, box, page)

        left, top, right, bottom = red_area(pdf_page)
        self.assertAlmostEqual((left + right) / 2, 200, delta=10)
        self.assertAlmostEqual((top + bottom) / 2, 100, delta=10)

    def test_overlay_covers_the_shown_page(self):
        for rotation in [0, 90, 180, 270]:
            pdf = pymupdf.open()
            pdf_page = pdf.new_page(width=300, height=400)
            pdf_page.set_rotation(rotation)
            shown = pdf_page.rect
            overlay = make_overlay(int(shown.width), int(shown.height))

            add_overlay(pdf_page, overlay.read())

            left, top, right, bottom = red_area(pdf_page)
            self.assertLess(left, 5)
            self.assertLess(top, 5)
            self.assertAlmostEqual(right, 40, delta=4)
            self.assertAlmostEqual(bottom, 40, delta=4)


class AutoPlaceTests(SignElectricTestCase):
    def test_spot_is_on_the_underline(self):
        page = read_first_page([(72, 600, 'Signature: ________')])

        spots = find_signature_spots(page)

        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]['underline']['text'], '________')
        self.assertEqual(spots[0]['box']['left'], spots[0]['underline']['left'])

    def test_name_before_the_keyword(self):
        page = read_first_page([(72, 600, 'Vadim signature: ________')])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Vadim')

    def test_keyword_without_underline_gets_the_space_after_it(self):
        page = read_first_page([(72, 600, 'Signature')])

        spots = find_signature_spots(page)

        keyword = spots[0]['keyword']
        self.assertIsNone(spots[0]['underline'])
        self.assertGreater(spots[0]['box']['left'], keyword['left'] + keyword['width'])

    def test_keyword_glued_to_the_underline(self):
        page = read_first_page([(72, 600, 'Semnatura__________')])

        spots = find_signature_spots(page)

        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]['underline']['text'], '__________')
        self.assertGreater(spots[0]['box']['left'], spots[0]['keyword']['left'])

    def test_two_signers_on_the_same_line(self):
        page = read_first_page([(72, 600, 'Ana signature: ______   Dan signature: ______')])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ana')
        self.assertEqual(spots[1]['name'], 'Dan')

    def test_keyword_inside_a_sentence_is_skipped(self):
        page = read_first_page([(72, 600, 'The copy is valid without signature and stamp.')])

        self.assertEqual(find_signature_spots(page), [])

    def test_filled_signature_field_is_skipped(self):
        page = read_first_page([(72, 600, 'Mobile Electronic Signature: 068888176')])

        self.assertEqual(find_signature_spots(page), [])

    def test_long_text_before_the_keyword_is_skipped(self):
        page = read_first_page([(72, 600, 'Please make sure to add your signature: ______')])

        self.assertEqual(find_signature_spots(page), [])

    def test_already_signed_spot_is_skipped(self):
        page = read_first_page([(72, 600, 'Signature')], stamp=(120, 585, 300, 640))

        self.assertEqual(find_signature_spots(page), [])

    def test_underline_without_keyword_is_not_a_spot(self):
        page = read_first_page([(72, 600, 'Date: ______')])

        self.assertEqual(find_signature_spots(page), [])
        self.assertEqual(find_lone_underlines(page), [])

    def test_signed_by_is_a_label(self):
        page = read_first_page([(72, 600, 'Signed by: ________')])

        spots = find_signature_spots(page)

        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]['underline']['text'], '________')

    def test_sign_here_is_a_label(self):
        page = read_first_page([(72, 600, 'Sign here')])

        self.assertEqual(len(find_signature_spots(page)), 1)

    def test_semnatura_olografa_is_one_label(self):
        page = read_first_page([(72, 600, 'Semnătura olografă: ________')], font=pymupdf.Font('cjk'))

        spots = find_signature_spots(page)

        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]['underline']['text'], '________')

    def test_russian_signature_label(self):
        page = read_first_page([(72, 600, 'Подпись: ________')], font=pymupdf.Font('cjk'))

        self.assertEqual(len(find_signature_spots(page)), 1)

    def test_date_alone_above_is_not_a_role(self):
        page = read_first_page([
            (72, 580, 'Data'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], '')

    def test_date_words_above_are_not_a_person(self):
        page = read_first_page([
            (72, 580, 'Data Semnarii'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], '')

    def test_underline_alone_on_its_line_is_a_lone_underline(self):
        page = read_first_page([
            (72, 600, '______________'),
            (72, 615, 'Ion Pop'),
        ])

        lone_underlines = find_lone_underlines(page)

        self.assertEqual(len(lone_underlines), 1)
        self.assertEqual(lone_underlines[0]['name'], '')
        self.assertEqual(lone_underlines[0]['box']['width'], lone_underlines[0]['underline']['width'])

    def test_two_lone_underlines_side_by_side(self):
        page = read_first_page([
            (72, 600, '______________'),
            (350, 600, '______________'),
        ])

        self.assertEqual(len(find_lone_underlines(page)), 2)

    def test_lines_to_write_on_are_not_lone_underlines(self):
        page = read_first_page([
            (72, 580, '__________________________'),
            (72, 600, '__________________________'),
            (72, 620, '__________________________'),
        ])

        self.assertEqual(find_lone_underlines(page), [])

    def test_underline_of_a_label_is_not_a_lone_underline(self):
        page = read_first_page([(72, 600, 'Semnatura: ________/Ion Pop')])

        self.assertEqual(find_lone_underlines(page), [])

    def test_label_with_a_drawn_line_after_it(self):
        page = read_first_page([(72, 600, 'Semnatura:')], drawn=[(140, 602, 300, 602)])

        spots = find_signature_spots(page)

        self.assertEqual(len(spots), 1)
        self.assertAlmostEqual(spots[0]['box']['left'], to_page_pixels(140), places=0)
        self.assertAlmostEqual(spots[0]['box']['width'], to_page_pixels(160), places=0)

    def test_drawn_line_in_many_pieces_is_one_line(self):
        pieces = []
        for x in range(140, 300, 4):
            pieces.append((x, 602, x + 4, 602))
        page = read_first_page([(72, 600, 'Semnatura:')], drawn=pieces)

        spots = find_signature_spots(page)

        self.assertAlmostEqual(spots[0]['box']['width'], to_page_pixels(160), places=0)

    def test_drawn_line_far_from_the_label_is_not_its_line(self):
        page = read_first_page([(72, 600, 'Semnatura:')], drawn=[(400, 602, 500, 602)])

        spots = find_signature_spots(page)

        self.assertNotAlmostEqual(spots[0]['box']['left'], to_page_pixels(400), places=0)

    def test_drawn_line_alone_is_a_lone_underline(self):
        page = read_first_page([(72, 615, 'Ion Pop')], drawn=[(72, 600, 250, 600)])

        lone_lines = find_lone_drawn_lines(page)

        self.assertEqual(len(lone_lines), 1)
        self.assertEqual(lone_lines[0]['name'], '')
        self.assertAlmostEqual(lone_lines[0]['box']['width'], to_page_pixels(178), places=0)

    def test_underlined_text_is_not_a_lone_underline(self):
        page = read_first_page([(72, 600, 'Important conditions')], drawn=[(72, 602, 200, 602)])

        self.assertEqual(find_lone_drawn_lines(page), [])

    def test_label_line_is_not_a_lone_underline(self):
        page = read_first_page([(72, 600, 'Data:')], drawn=[(110, 602, 250, 602)])

        self.assertEqual(find_lone_drawn_lines(page), [])

    def test_table_lines_are_not_lone_underlines(self):
        page = read_first_page([], drawn=[
            (72, 500, 300, 500),
            (72, 600, 300, 600),
            (72, 500, 72, 600),
            (300, 500, 300, 600),
        ])

        self.assertEqual(find_lone_drawn_lines(page), [])

    def test_drawn_lines_to_write_on_are_not_lone_underlines(self):
        page = read_first_page([], drawn=[
            (72, 580, 300, 580),
            (72, 600, 300, 600),
            (72, 620, 300, 620),
        ])

        self.assertEqual(find_lone_drawn_lines(page), [])

    def test_line_across_the_page_is_not_a_lone_underline(self):
        page = read_first_page([], drawn=[(40, 600, 560, 600)])

        self.assertEqual(find_lone_drawn_lines(page), [])

    def test_page_background_is_not_a_box(self):
        page = read_first_page([(72, 85, 'Signature:')], rects=[(0, 0, 595.5, 842.25)])

        box = find_signature_spots(page)[0]['box']

        self.assertLess(box['top'], to_page_pixels(85))
        self.assertGreater(box['left'], to_page_pixels(72))

    def test_page_border_is_not_a_box(self):
        page = read_first_page([(72, 85, 'Signature:')], rects=[(20, 20, 575, 822)])

        box = find_signature_spots(page)[0]['box']

        self.assertLess(box['top'], to_page_pixels(85))

    def test_empty_box_after_the_label(self):
        page = read_first_page([(72, 600, 'Signature:')], rects=[(140, 580, 300, 620)])

        box = find_signature_spots(page)[0]['box']

        self.assertAlmostEqual(box['left'], to_page_pixels(143), places=0)
        self.assertAlmostEqual(box['width'], to_page_pixels(154), places=0)
        self.assertAlmostEqual(box['top'] + box['height'], to_page_pixels(617), places=0)
        self.assertAlmostEqual(box['space_above'], to_page_pixels(34), places=0)

    def test_box_with_text_inside_is_not_a_signature_box(self):
        page = read_first_page(
            [(72, 600, 'Signature:'), (150, 600, 'Ion')],
            rects=[(140, 580, 300, 620)],
        )

        box = find_signature_spots(page)[0]['box']

        self.assertNotAlmostEqual(box['left'], to_page_pixels(143), places=0)

    def test_empty_box_under_the_label(self):
        page = read_first_page([(72, 600, 'Signature:')], rects=[(72, 610, 250, 660)])

        box = find_signature_spots(page)[0]['box']

        self.assertAlmostEqual(box['left'], to_page_pixels(75), places=0)
        self.assertAlmostEqual(box['top'] + box['height'], to_page_pixels(657), places=0)

    def test_room_under_the_label_in_a_table_cell(self):
        page = read_first_page(
            [
                (80, 520, 'Prestator'),
                (80, 540, 'Semnatura'),
                (300, 520, 'Beneficiar'),
                (300, 540, 'Semnatura'),
            ],
            drawn=[(286, 500, 286, 620)],
            rects=[(72, 500, 500, 620)],
        )

        spots = find_signature_spots(page)

        self.assertEqual(len(spots), 2)
        self.assertEqual(spots[0]['name'], 'Prestator')
        self.assertEqual(spots[1]['name'], 'Beneficiar')
        first_box = spots[0]['box']
        self.assertLess(first_box['left'] + first_box['width'], to_page_pixels(286))
        self.assertAlmostEqual(first_box['top'] + first_box['height'], to_page_pixels(617), places=0)

    def test_label_in_a_full_cell_stays_inside_the_cell(self):
        page = read_first_page([(80, 600, 'Semnatura')], rects=[(72, 580, 230, 610)])

        box = find_signature_spots(page)[0]['box']

        self.assertLess(box['left'] + box['width'], to_page_pixels(230))

    def test_sides_of_a_box_are_not_lone_underlines(self):
        page = read_first_page([], rects=[(72, 580, 250, 620)])

        self.assertEqual(find_lone_drawn_lines(page), [])

    def test_name_after_prin_on_the_line_above(self):
        page = read_first_page([
            (72, 580, 'SC MUSKET SRL prin Marius Mitrofan'),
            (72, 600, 'Semnatura'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Marius Mitrofan')

    def test_name_alone_on_a_line_above(self):
        page = read_first_page([
            (72, 560, 'SALARIATUL'),
            (72, 580, 'Corasevici Mihail'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Corasevici Mihail')

    def test_name_after_name_label_on_the_line_above(self):
        page = read_first_page([
            (72, 580, 'Name: Ana Pop'),
            (72, 600, 'Signature: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ana Pop')

    def test_job_title_after_a_dash_is_not_part_of_the_name(self):
        page = read_first_page([
            (72, 580, 'Valentin Scarevnea - Director'),
            (72, 600, 'Signature: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Valentin Scarevnea')

    def test_each_column_gets_its_own_name(self):
        page = read_first_page([
            (72, 580, 'Ana Pop'),
            (350, 580, 'Dan Rusu'),
            (72, 600, 'Semnatura______        Semnatura______'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ana Pop')
        self.assertEqual(spots[1]['name'], 'Dan Rusu')

    def test_company_is_used_when_there_is_no_person(self):
        page = read_first_page([
            (72, 560, 'ANGAJATORUL'),
            (72, 580, 'SRL "Sugar Software"'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Sugar Software')

    def test_role_is_used_when_there_is_no_person_or_company(self):
        page = read_first_page([
            (72, 580, 'BENEFICIAR'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Beneficiar')

    def test_person_wins_over_the_company(self):
        page = read_first_page([
            (72, 560, 'SRL Sugar Software'),
            (72, 580, 'Corasevici Mihail'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Corasevici Mihail')

    def test_lines_with_numbers_are_not_names(self):
        page = read_first_page([
            (72, 580, 'Cod fiscal: 1024600086865'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], '')

    def test_name_before_the_keyword_wins_over_the_line_above(self):
        page = read_first_page([
            (72, 580, 'Ana Pop'),
            (72, 600, 'Vadim signature: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Vadim')

    def test_space_above_stops_at_the_text_above(self):
        roomy = read_first_page([(72, 600, 'Signature: ________')])
        tight = read_first_page([
            (72, 587, 'Maria Ionescu'),
            (72, 600, 'Signature: ________'),
        ])

        roomy_space = find_signature_spots(roomy)[0]['box']['space_above']
        tight_space = find_signature_spots(tight)[0]['box']['space_above']

        self.assertLess(tight_space, roomy_space / 2)

    def test_empty_spot_stops_before_the_next_label(self):
        page = read_first_page([(72, 600, 'Semnatura        Semnatura')])

        spots = find_signature_spots(page)

        first_box = spots[0]['box']
        self.assertLessEqual(first_box['left'] + first_box['width'], spots[1]['keyword']['left'])

    def test_empty_spot_stops_at_the_page_margin(self):
        page = read_first_page([(480, 600, 'Signature')])

        spots = find_signature_spots(page)

        box = spots[0]['box']
        self.assertLess(box['left'] + box['width'], page['width'])

    def test_name_after_a_slash_glued_to_the_line(self):
        page = read_first_page([(72, 600, 'Semnatura: ________/Ion Popescu')])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ion Popescu')
        self.assertEqual(spots[0]['underline']['text'], '________')

    def test_glued_name_is_not_part_of_the_line(self):
        page = read_first_page([(72, 600, 'Semnatura: ________/Ion Popescu')])

        spots = find_signature_spots(page)

        glued_word = page['words'][1]
        self.assertLess(spots[0]['box']['width'], glued_word['width'])

    def test_name_between_slashes_after_the_line(self):
        page = read_first_page([(72, 600, 'Semnatura ________ /Ion Pop/')])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ion Pop')

    def test_name_with_colon_of_a_known_signer_is_a_spot(self):
        page = read_first_page([(72, 600, 'Ion Pop:')])

        spots = find_name_label_spots(page, ['Ion Pop'])

        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]['name'], 'Ion Pop')

    def test_heading_with_colon_is_not_a_spot(self):
        page = read_first_page([(72, 600, 'Invoice No:')])

        spots = find_name_label_spots(page, ['Ion Pop'])

        self.assertEqual(spots, [])

    def test_name_above_has_no_colon_or_comma(self):
        page = read_first_page([
            (72, 560, 'Ion Pop:'),
            (72, 580, 'Ana Rusu,'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ana Rusu')

    def test_name_before_the_keyword_has_no_comma(self):
        page = read_first_page([(72, 600, 'Vadim, signature: ________')])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Vadim')

    def test_name_label_above_the_keyword_is_found_once_more(self):
        page = read_first_page([
            (72, 580, 'Ion Pop:'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ion Pop')
        self.assertEqual(len(find_name_label_spots(page, ['Ion Pop'])), 1)

    def test_semnatura_with_romanian_letters(self):
        page = read_first_page([
            (72, 500, 'Semnătura: ________'),
            (72, 600, 'Semnătură: ________'),
        ], font=pymupdf.Font('cjk'))

        spots = find_signature_spots(page)

        self.assertEqual(len(spots), 2)

    def test_label_at_the_right_edge_gets_the_line_under_it(self):
        page = read_first_page([(540, 600, 'Signature')])

        spots = find_signature_spots(page)

        box = spots[0]['box']
        keyword = spots[0]['keyword']
        self.assertGreater(box['width'], 0)
        self.assertGreater(box['top'], keyword['top'] + keyword['height'])
        self.assertLessEqual(box['left'] + box['width'], page['width'])

    def test_close_labels_get_the_line_under_them(self):
        page = read_first_page([(72, 600, 'Semnatura Semnatura')])

        spots = find_signature_spots(page)

        first_box = spots[0]['box']
        self.assertGreater(first_box['width'], 0)
        self.assertLessEqual(first_box['left'] + first_box['width'], spots[1]['keyword']['left'])

    def test_another_label_above_is_not_a_role(self):
        page = read_first_page([
            (72, 580, 'Semnatura'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[1]['name'], '')

    def test_lowercase_word_above_is_not_a_role(self):
        page = read_first_page([
            (72, 580, 'aprobat'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], '')

    def test_numbering_before_the_keyword_is_not_a_name(self):
        page = read_first_page([
            (72, 580, 'Ana Pop'),
            (72, 600, '1. Signature: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ana Pop')

    def test_closest_line_above_wins_even_if_written_last(self):
        page = read_first_page([
            (72, 580, 'Ana Pop'),
            (72, 560, 'Dan Rusu'),
            (72, 600, 'Semnatura: ________'),
        ])

        spots = find_signature_spots(page)

        self.assertEqual(spots[0]['name'], 'Ana Pop')

    def test_autoplace_returns_the_lone_underlines(self):
        pdf = make_text_pdf([(72, 600, '______________')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf), 'multi')

        response = self.client.post(self.editor_url('autoplace'))

        self.assertEqual(response.json()['spots'], [])
        self.assertEqual(len(response.json()['lone_underlines']), 1)

    def test_autoplace_does_not_send_every_word(self):
        pdf = make_text_pdf([(72, 600, 'Vadim signature: ________')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        self.assertNotIn('pages', response.json())

    def test_autoplace_finds_the_name_label_of_a_signer(self):
        pdf = make_text_pdf([
            (72, 600, 'Semnatura: ________/Ion Pop'),
            (72, 650, 'Ion Pop:'),
        ])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        spots = response.json()['spots']
        self.assertEqual(len(spots), 2)
        self.assertEqual(spots[1]['name'], 'Ion Pop')

    def test_autoplace_finds_the_line_under_the_keyword_and_the_name_label_above(self):
        pdf = make_text_pdf([
            (72, 200, '_____________________________________________________________'),
            (72, 600, 'Braileanu: _________'),
            (72, 615, 'Signature:'),
            (72, 630, '__________'),
        ])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        spots = response.json()['spots']
        self.assertEqual(len(spots), 2)
        self.assertEqual(spots[0]['name'], '')
        self.assertGreater(spots[0]['box']['top'], spots[0]['keyword']['top'])
        self.assertEqual(spots[1]['name'], 'Braileanu')
        self.assertEqual(response.json()['lone_underlines'], [])

    def test_autoplace_finds_a_name_label_glued_to_its_line(self):
        pdf = make_text_pdf([
            (72, 600, 'Braileanu:_________'),
            (72, 630, 'Signature:'),
            (72, 645, '__________'),
        ])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        spots = response.json()['spots']
        self.assertEqual(len(spots), 2)
        self.assertEqual(spots[0]['name'], '')
        self.assertEqual(spots[1]['name'], 'Braileanu')
        self.assertEqual(spots[1]['underline']['text'], '_________')

    def test_autoplace_signs_a_name_before_a_line_and_skips_the_line_between(self):
        pdf = make_text_pdf([
            (72, 600, 'Signature:'),
            (72, 615, '__________'),
            (72, 658, '__________'),
            (72, 700, 'Gabriel ____________'),
        ])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        spots = response.json()['spots']
        self.assertEqual(len(spots), 2)
        self.assertEqual(spots[1]['name'], 'Gabriel')
        self.assertEqual(response.json()['lone_underlines'], [])

    def test_autoplace_signs_names_without_a_line_next_to_other_signers(self):
        pdf = make_text_pdf([
            (72, 400, 'Termeni:'),
            (72, 600, 'Signature: ________'),
            (72, 615, 'Mihalaci:'),
            (72, 630, 'Daniel:'),
        ])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        names = []
        for spot in response.json()['spots']:
            names.append(spot['name'])
        self.assertEqual(names, ['', 'Mihalaci', 'Daniel'])

    def test_autoplace_marks_a_signature_next_to_the_word(self):
        pdf = make_text_pdf([(72, 600, 'Signature:')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        box = response.json()['spots'][0]['box']
        self.assertTrue(box['next_to_text'])

    def test_autoplace_returns_the_spots_of_the_uploaded_pdf(self):
        pdf = make_text_pdf([(72, 600, 'Vadim signature: ________')])
        self.upload(SimpleUploadedFile('contract.pdf', pdf))

        response = self.client.post(self.editor_url('autoplace'))

        spots = response.json()['spots']
        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]['name'], 'Vadim')
        self.assertEqual(spots[0]['page_number'], 1)

    def test_autoplace_is_refused_for_an_image(self):
        self.upload(make_png())

        response = self.client.post(self.editor_url('autoplace'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Auto Place works only on PDF and Word files.')

    def test_autoplace_without_a_document(self):
        response = self.client.post(self.editor_url('autoplace'))

        self.assertEqual(response.status_code, 410)


class DeclineTests(SignElectricTestCase):
    def setUp(self):
        super().setUp()
        self.send([
            placeholder('Ana', 'ana@example.com'),
            placeholder('Dan', 'dan@example.com'),
        ])
        self.document = Document.objects.get()
        self.ana = Signer.objects.get(email='ana@example.com')
        self.dan = Signer.objects.get(email='dan@example.com')
        mail.outbox = []

    def decline(self, signer, reason=''):
        return self.client_class().post(f'/sign/{signer.token}/', {'action': 'decline', 'reason': reason})

    def test_sign_page_has_a_decline_button(self):
        response = self.client_class().get(f'/sign/{self.ana.token}/')

        self.assertContains(response, 'Decline to sign?')
        self.assertContains(response, 'name="reason"')

    def test_decline_stops_the_document(self):
        response = self.decline(self.ana, 'The price is wrong.')

        self.assertContains(response, 'You declined to sign')
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'declined')
        ana = Signer.objects.get(email='ana@example.com')
        self.assertIsNotNone(ana.declined_at)
        self.assertIsNone(ana.signed_at)
        self.assertEqual(ana.decline_reason, 'The price is wrong.')

    def test_sender_gets_an_email_with_the_reason(self):
        self.decline(self.ana, 'The price is wrong.')

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['boss@example.com'])
        self.assertEqual(email.reply_to, ['ana@example.com'])
        self.assertIn('Ana declined to sign', email.subject)
        self.assertIn('The price is wrong.', email.body)

    def test_decline_without_a_reason(self):
        self.decline(self.ana)

        self.assertEqual(Document.objects.get().status, 'declined')
        self.assertIn('They did not give a reason.', mail.outbox[0].body)

    def test_reason_is_cut_to_500_characters(self):
        self.decline(self.ana, 'x' * 800)

        self.assertEqual(len(Signer.objects.get(email='ana@example.com').decline_reason), 500)

    def test_nobody_can_sign_after_a_decline(self):
        self.decline(self.ana)

        response = self.client_class().get(f'/sign/{self.dan.token}/')
        self.assertContains(response, 'This document was declined')
        response = self.client_class().post(f'/sign/{self.dan.token}/')
        self.assertContains(response, 'This document was declined')
        self.assertIsNone(Signer.objects.get(email='dan@example.com').signed_at)
        response = self.client_class().get(f'/sign/{self.dan.token}/page/1/')
        self.assertEqual(response.status_code, 404)
        response = self.decline(self.ana)
        self.assertContains(response, 'This document was declined')

    def test_signed_person_cannot_decline(self):
        self.client_class().post(f'/sign/{self.ana.token}/')

        response = self.decline(self.ana)

        self.assertContains(response, 'You already signed')
        self.assertEqual(Document.objects.get().status, 'waiting')

    def test_owner_sees_the_decline(self):
        self.decline(self.ana, 'The price is wrong.')

        response = self.client.get('/')
        self.assertContains(response, '<span class="badge declined">Declined</span>', html=True)
        response = self.client.get('/?status=declined')
        self.assertContains(response, '<div class="name">photo</div>', html=True)

        response = self.client.get(f'/documents/{self.document.uid}/')
        self.assertContains(response, 'Declined on')
        self.assertContains(response, '"The price is wrong."')
        self.assertNotContains(response, 'Send reminder')
        self.assertNotContains(response, 'Cancel signing')

    def test_signer_with_account_sees_you_declined(self):
        User.objects.create_user('ana', 'ana@example.com', 'a-long-password-123')
        self.decline(self.ana)
        self.client.login(username='ana', password='a-long-password-123')

        response = self.client.get('/?show=received')

        self.assertContains(response, '<span class="badge declined">You declined</span>', html=True)

    def test_declined_document_cannot_get_a_reminder(self):
        self.decline(self.ana)
        mail.outbox = []

        response = self.client.post(f'/documents/{self.document.uid}/remind/', follow=True)

        self.assertContains(response, 'This document is not waiting for anyone to sign.')
        self.assertEqual(len(mail.outbox), 0)

    def test_decline_is_in_the_signing_record(self):
        self.decline(self.ana, 'The price is wrong.')

        event = AuditEvent.objects.filter(document=self.document).last()
        self.assertEqual(event.text, 'Declined by Ana (ana@example.com). Reason: The price is wrong.')

        response = self.client.get(f'/documents/{self.document.uid}/pdf/')
        pdf = pymupdf.open(stream=b''.join(response.streaming_content), filetype='pdf')
        text = pdf[-1].get_text()
        self.assertIn('Status: Declined on', text)
        self.assertIn('Declined by Ana', text)

    def test_decline_works_when_the_email_fails(self):
        with patch('documents.views.send_decline_notice', side_effect=OSError):
            response = self.decline(self.ana)

        self.assertContains(response, 'You declined to sign')
        self.assertEqual(Document.objects.get().status, 'declined')

    def test_decline_needs_login_when_required(self):
        self.document.require_login = True
        self.document.save()

        response = self.decline(self.ana)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Document.objects.get().status, 'waiting')
