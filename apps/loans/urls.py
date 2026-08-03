from django.urls import path
from .views import (
    LoanRequestBatchDetailView,
    LoanRequestBatchListView,
    LoanRequestListView,
    LoanRequestUploadDetailView,
    LoanRequestUploadListCreateView,
    LoanUploadStatusView,
)

app_name = 'loans'

urlpatterns = [
    # Uploads
    path('uploads/',                                LoanRequestUploadListCreateView.as_view(), name='upload-list-create'),
    path('uploads/<uuid:pk>/',                      LoanRequestUploadDetailView.as_view(),     name='upload-detail'),
    path('uploads/<uuid:pk>/status/',               LoanUploadStatusView.as_view(),            name='upload-status'),
    path('uploads/<uuid:upload_id>/requests/',      LoanRequestListView.as_view(),             name='upload-requests'),

    # Batches
    path('batches/',                                LoanRequestBatchListView.as_view(),        name='batch-list'),
    path('batches/<uuid:pk>/',                      LoanRequestBatchDetailView.as_view(),      name='batch-detail'),
]