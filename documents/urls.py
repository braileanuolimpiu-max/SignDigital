from django.urls import path

from . import views

urlpatterns = [
    path('', views.homepage, name='homepage'),
    path('editor/<uuid:uid>/', views.signature_workspace, name='signature_workspace'),
    path('heartbeat/', views.heartbeat, name='heartbeat'),
    path('track-leave/', views.user_left_page, name='track_leave'),
    path('autoplace/', views.autoplace, name='autoplace'),
    path('send/', views.send_for_signing, name='send_for_signing'),
    path('sign/<uuid:token>/', views.sign_document, name='sign_document'),
]
