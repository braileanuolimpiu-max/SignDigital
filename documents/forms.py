import os

from django import forms
from django.conf import settings

from .models import Document

FILE_SIGNATURES = {
    '.pdf': b'%PDF',
    '.docx': b'PK',
    '.doc': b'\xd0\xcf\x11\xe0',
    '.png': b'\x89PNG',
    '.jpg': b'\xff\xd8\xff',
    '.jpeg': b'\xff\xd8\xff',
}


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['file']
        widgets = {
            'file': forms.FileInput(attrs={'accept': '.pdf,.doc,.docx,.png,.jpg,.jpeg'}),
        }

    def clean_file(self):
        uploaded = self.cleaned_data['file']

        if uploaded.size > settings.MAX_UPLOAD_SIZE:
            raise forms.ValidationError('The file is too big. The limit is 20 MB.')

        name, extension = os.path.splitext(uploaded.name)
        extension = extension.lower()
        if extension not in FILE_SIGNATURES:
            raise forms.ValidationError('This file type is not allowed.')

        signature = FILE_SIGNATURES[extension]
        first_bytes = uploaded.read(len(signature))
        uploaded.seek(0)
        if first_bytes != signature:
            raise forms.ValidationError('The file content does not match its type.')

        return uploaded
