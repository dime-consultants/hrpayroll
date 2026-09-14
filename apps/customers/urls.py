from django.urls import path
from .views import (
    CustomerRegistrationDetailView,
    CustomerRegistrationListCreateView,
    CustomerRegistrationStatusView,
    CustomerRegistrationSyncStatusView,
)

app_name = 'customers'

urlpatterns = [
    path('registrations/',                       CustomerRegistrationListCreateView.as_view(),   name='registration-list-create'),
    path('registrations/<uuid:pk>/',              CustomerRegistrationDetailView.as_view(),        name='registration-detail'),
    path('registrations/<uuid:pk>/status/',       CustomerRegistrationStatusView.as_view(),        name='registration-status'),
    path('registrations/<uuid:pk>/sync-status/',  CustomerRegistrationSyncStatusView.as_view(),    name='registration-sync-status'),
]
