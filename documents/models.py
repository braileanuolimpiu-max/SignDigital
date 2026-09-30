from django.db import models
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db.models.signals import post_save
from django.http import JsonResponse
from pdf2image import convert_from_path
from django.conf import settings
import os
from pypdf import PdfReader

COVER_PAGE_DIRECTORY = 'coverdirectory/'
PDF_DIRECTORY = 'pdfdirectory/'
COVER_PAGE_FORMAT = 'jpg'

# Create your models here.
class Image(models.Model):
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to ='images/')
    def __str__(self):
        return self.name


def set_pdf_file_name(instance, filename):
    return os.path.join(PDF_DIRECTORY, '{}.pdf'.format(instance.filename))

def set_cover_file_name(instance, filename):
    return os.path.join(COVER_PAGE_DIRECTORY, '{}.{}'.format(instance.filename, COVER_PAGE_FORMAT))

class Pdffile(models.Model):
    pdf = models.FileField(
        upload_to=set_pdf_file_name,
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        max_length = 500,
        )
    filename = models.CharField(max_length=50)
    pagenumforcover = models.IntegerField(validators=[MinValueValidator(1)])
    coverpage = models.FileField(upload_to=set_cover_file_name)

# def number_of_pages(instance):
#     pdf_field = instance.pdf_file
#
#     with pdf_field.open("rb") as f:
#         reader = PdfReader(f)
#         page_count = len(reader.pages)
#     return page_count
def convert_pdf_to_image(instance, created, **kwargs):
    if created:
        cover_page_dir = os.path.join(settings.MEDIA_ROOT, COVER_PAGE_DIRECTORY)

        if not os.path.exists(cover_page_dir):
            os.makedirs(cover_page_dir, exist_ok=True)

        poppler_bin_path = getattr(settings, 'POPPLER_PATH', None)

        pdf_filename, _ = os.path.splitext(os.path.basename(instance.pdf.name))

        convert_from_path(
            pdf_path=instance.pdf.path,
            dpi=300,
            first_page=instance.pagenumforcover,
            last_page=instance.pagenumforcover,
            fmt=COVER_PAGE_FORMAT,
            output_folder=cover_page_dir,
            output_file=pdf_filename,
            poppler_path=poppler_bin_path
        )
        generated_file = None
        for file in os.listdir(cover_page_dir):
            if file.startswith(pdf_filename) and file.lower().endswith(COVER_PAGE_FORMAT):
                generated_file = file
                break

        if generated_file:
            new_cover_page_path_relative = os.path.join(COVER_PAGE_DIRECTORY, generated_file).replace('\\', '/')
            Pdffile.objects.filter(pk=instance.pk).update(coverpage=new_cover_page_path_relative)
        else :
            data = {'error': 'Coverpage number not found'}
            return JsonResponse(data, status=404)

post_save.connect(convert_pdf_to_image, sender=Pdffile)