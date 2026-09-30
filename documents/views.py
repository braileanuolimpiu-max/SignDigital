from django.shortcuts import render, redirect, get_object_or_404
from .forms import PdffileForm, ImageForm, HomeForm
from .models import Pdffile, Image
from django.http import  HttpResponse
from django.views.decorators.csrf import csrf_exempt
from pypdf import PdfReader
from django.contrib import messages

def delete_active_document(request):
    doc_id = request.session.get('active_doc_id')
    doc_type = request.session.get('active_doc_type')

    if doc_id and doc_type:
        try:
            if doc_type == 'pdf':
                document = Pdffile.objects.get(id=doc_id)
                if document.pdf:
                    document.pdf.delete(save=False)
                if document.coverpage:
                    document.coverpage.delete(save=False)
                document.delete()
            elif doc_type == 'img':
                document = Image.objects.get(id=doc_id)
                if document.file:
                    document.file.delete(save=False)
                document.delete()
        except (Pdffile.DoesNotExist, Image.DoesNotExist):
            pass

    request.session.pop('active_doc_id', None)
    request.session.pop('active_doc_type', None)
    request.session.pop('pending_delete', None)


def homepage(request):
    if request.session.pop('pending_delete', False):
        delete_active_document(request)

    form = HomeForm()
    if request.method == 'POST':
        form = HomeForm(request.POST, request.FILES)
        if request.POST.get("save_pdf"):
            return redirect('uploadpdf')
        if request.POST.get("save_img"):
            return redirect('uploadimg')
    return render(request, "homepage.html", {'form': form})

def uploadimg(request):
    form = ImageForm()

    if request.method == 'POST':
        form = ImageForm(request.POST, request.FILES)
        if form.is_valid():
            doc_saved = form.save()
            request.session['active_doc_id'] = doc_saved.id
            request.session['active_doc_type'] = 'img'
            return redirect('signature_workspace', doc_id=doc_saved.id)

    return render(request, "uploadimg.html", {'form': form})
def uploadpdf(request):
    form = PdffileForm()

    if request.method == 'POST':
        form = PdffileForm(request.POST, request.FILES)
        if form.is_valid():
            doc_saved = form.save()
            request.session['active_doc_id'] = doc_saved.id
            request.session['active_doc_type'] = 'pdf'
            return redirect('signature_workspace', doc_id=doc_saved.id)

    return render(request, "uploadpdf.html", {'form': form})


def signature_workspace(request, doc_id):
    if doc_id != request.session.get('active_doc_id'):
        return redirect('homepage')

    request.session.pop('pending_delete', None)

    if request.session.get('active_doc_type') == 'pdf':
        document = get_object_or_404(Pdffile, id=doc_id)
        pdf_field = document.pdf
        with pdf_field.open("rb") as f:
            reader = PdfReader(f)
            page_count = len(reader.pages)
        if document.pagenumforcover > page_count:
            document.pdf.delete(save=False)
            document.coverpage.delete(save=False)
            document.delete()
            messages.info(request, 'The cover page is out of range')
            return redirect('homepage')
        context = {
            'background_url': document.coverpage.url,
            'filename': document.filename,
            'original_url': document.pdf.url,
        }
    else:
        document = get_object_or_404(Image, id=doc_id)
        context = {
            'background_url': document.file.url,
            'filename': document.name,
            'original_url': document.file.url,
        }
    return render(request, 'editor.html', context)

@csrf_exempt
def user_left_page(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'left_page':
            if request.session.get('active_doc_id'):
                request.session['pending_delete'] = True
                return HttpResponse(status=204)

            return redirect('homepage')

    return HttpResponse(status=400)