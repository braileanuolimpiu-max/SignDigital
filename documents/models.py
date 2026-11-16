import os
import shutil
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

DOCUMENTS_DIRECTORY = 'documents'
ALLOWED_EXTENSIONS = ['pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg']
DRAFTS_KEPT_FOR = timedelta(hours=24)


def document_upload_path(instance, filename):
    name, extension = os.path.splitext(filename)
    folder = os.path.join(DOCUMENTS_DIRECTORY, str(instance.uid))
    return os.path.join(folder, 'original' + extension.lower())


class Document(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='documents',
        null=True,
        blank=True,
    )
    file = models.FileField(
        upload_to=document_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_EXTENSIONS)],
        max_length=500,
    )
    filename = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    mode = models.CharField(max_length=10, default='solo')
    sender_email = models.EmailField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.filename

    @property
    def kind(self):
        name, extension = os.path.splitext(self.file.name)
        if extension == '.pdf':
            return 'pdf'
        if extension in ('.doc', '.docx'):
            return 'word'
        return 'image'

    @property
    def folder_path(self):
        return os.path.join(settings.MEDIA_ROOT, DOCUMENTS_DIRECTORY, str(self.uid))

    def delete_with_files(self):
        if os.path.isdir(self.folder_path):
            shutil.rmtree(self.folder_path)
        self.delete()

    def everyone_signed(self):
        waiting = self.signers.filter(signed_at__isnull=True)
        return not waiting.exists()

    def signer_count(self):
        return self.signers.count()

    def signed_count(self):
        return self.signers.filter(signed_at__isnull=False).count()

    @property
    def status(self):
        if self.finished_at is not None:
            return 'signed'
        if self.sent_at is not None:
            return 'waiting'
        return 'draft'


class Page(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='pages')
    number = models.PositiveIntegerField()
    image = models.FileField(max_length=500)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()

    class Meta:
        ordering = ['number']


class Signer(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='signers')
    name = models.CharField(max_length=100)
    email = models.EmailField()
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    signed_at = models.DateTimeField(null=True, blank=True)


class Placeholder(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='placeholders')
    signer = models.ForeignKey(Signer, on_delete=models.CASCADE, related_name='placeholders')
    page_number = models.PositiveIntegerField()
    left = models.FloatField()
    top = models.FloatField()
    width = models.FloatField()
    height = models.FloatField()
    font_size = models.FloatField()
    color = models.CharField(max_length=7)


def delete_if_possible(document):
    try:
        document.delete_with_files()
    except OSError:
        pass


def delete_old_drafts():
    too_old = timezone.now() - DRAFTS_KEPT_FOR
    old_drafts = Document.objects.filter(
        sent_at__isnull=True,
        finished_at__isnull=True,
        created_at__lt=too_old,
    )
    for document in old_drafts:
        delete_if_possible(document)
