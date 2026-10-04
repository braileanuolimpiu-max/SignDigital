import re

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.urls import reverse
from .models import Pdffile, convert_pdf_to_image, Image

class EditorPdfTest(TestCase):
    def setUp(self):
        self.url = reverse("track_leave")
        post_save.disconnect(convert_pdf_to_image, sender=Pdffile)

        self.pdf_file = Pdffile.objects.create(
            pdf=SimpleUploadedFile(
                name="test_document.pdf",
                content=b"%PDF-1.4 mock pdf data",
                content_type="application/pdf"
            ),
            coverpage=SimpleUploadedFile(
                name="test_cover.jpg",
                content=b"mock_image_bytes",
                content_type="image/jpeg"
            ),
            filename='Test',
            pagenumforcover=1
        )

    def tearDown(self):
        post_save.connect(convert_pdf_to_image, sender=Pdffile)
        self.pdf_file.pdf.delete(save=False)
        self.pdf_file.coverpage.delete(save=False)

    def test_pdf_directory(self):
        self.assertTrue(re.search(r'/media/pdfdirectory/Test.*\.pdf', self.pdf_file.pdf.url))
        self.assertTrue(re.search(r'/media/coverdirectory/Test.*\.jpg', self.pdf_file.coverpage.url))
    def test_pdf_left(self):
        session = self.client.session
        session["active_doc_id"] = self.pdf_file.id
        session["active_doc_type"] = "pdf"
        session.save()

        response = self.client.post(self.url, data={"action": "left_page"})

        self.assertEqual(response.status_code, 204)

        self.assertFalse(Image.objects.filter(id=self.pdf_file.id).exists())

        self.assertNotIn("active_doc_id", self.client.session)
        self.assertNotIn("active_doc_type", self.client.session)

class EditorImgTest(TestCase):
    def setUp(self):
        self.url = reverse("track_leave")
        self.img_file = Image.objects.create(
            name="test_image.png",
            file=SimpleUploadedFile(
                name="test_image.png",
                content=b"\x89PNG\r\n\x1a\n",
                content_type="image/png"
            )
        )
    def tearDown(self):
        self.img_file.file.delete(save=False)
    def test_img_directory(self):
        self.assertTrue(re.search(r'/media/images/test_image.*\.png', self.img_file.file.url))
    def test_img_left(self):
        session = self.client.session
        session["active_doc_id"] = self.img_file.id
        session["active_doc_type"] = "img"
        session.save()
        response = self.client.post(self.url, data={"action": "left_page"})

        self.assertEqual(response.status_code, 204)

        self.assertFalse(Image.objects.filter(id=self.img_file.id).exists())

        self.assertNotIn("active_doc_id", self.client.session)
        self.assertNotIn("active_doc_type", self.client.session)
