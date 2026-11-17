from django.urls import path

from . import views

urlpatterns = [
    path('', views.homepage, name='homepage'),
    path('editor/<uuid:uid>/', views.signature_workspace, name='signature_workspace'),
    path('editor/<uuid:uid>/page/<int:number>/', views.editor_page_image, name='editor_page_image'),
    path('editor/<uuid:uid>/original/', views.editor_original, name='editor_original'),
    path('editor/<uuid:uid>/autoplace/', views.autoplace, name='autoplace'),
    path('editor/<uuid:uid>/send/', views.send_for_signing, name='send_for_signing'),
    path('editor/<uuid:uid>/export/', views.export_signed, name='export_signed'),
    path('sign/<uuid:token>/', views.sign_document, name='sign_document'),
    path('sign/<uuid:token>/page/<int:number>/', views.sign_page_image, name='sign_page_image'),
    path('documents/<uuid:uid>/', views.document_detail, name='document_detail'),
    path('documents/<uuid:uid>/pdf/', views.document_pdf, name='document_pdf'),
    path('documents/<uuid:uid>/remind/', views.send_reminder, name='send_reminder'),
    path('documents/<uuid:uid>/cancel/', views.cancel_document, name='cancel_document'),
    path('documents/<uuid:uid>/delete/', views.delete_document, name='delete_document'),
]
