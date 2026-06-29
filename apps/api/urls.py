from django.urls import path
from .views import (
    HRTokenObtainView, HRTokenRefreshView,
    DashboardView,
    OrganizationDetailView,
    HRUserListCreateView, HRUserDetailView,
    PayrollUploadListCreateView, PayrollUploadDetailView, UploadApprovalCallbackView,
    SalaryDeductionListView, SalaryDeductionDetailView,
    RepaymentBatchListView, RepaymentBatchDetailView, RepaymentBatchApproveView,
    RepaymentRecordListView,
    HealthCheckView,
)

urlpatterns = [
    path('auth/login/',          HRTokenObtainView.as_view(),           name='token_obtain'),
    path('auth/refresh/',        HRTokenRefreshView.as_view(),          name='token_refresh'),
    path('health/',              HealthCheckView.as_view(),              name='health'),
    path('dashboard/',           DashboardView.as_view(),                name='dashboard'),
    path('organization/',        OrganizationDetailView.as_view(),       name='organization_detail'),
    path('users/',               HRUserListCreateView.as_view(),         name='hr_user_list_create'),
    path('users/<uuid:pk>/',     HRUserDetailView.as_view(),             name='hr_user_detail'),
    path('uploads/',             PayrollUploadListCreateView.as_view(),  name='upload_list_create'),
    path('uploads/<uuid:pk>/',   PayrollUploadDetailView.as_view(),      name='upload_detail'),
    path('uploads/<uuid:pk>/approve/', UploadApprovalCallbackView.as_view(), name='upload_approve_callback'),
    path('deductions/',          SalaryDeductionListView.as_view(),      name='deduction_list'),
    path('deductions/<uuid:pk>/',SalaryDeductionDetailView.as_view(),    name='deduction_detail'),
    path('batches/',             RepaymentBatchListView.as_view(),       name='batch_list'),
    path('batches/<uuid:pk>/',   RepaymentBatchDetailView.as_view(),     name='batch_detail'),
    path('batches/<uuid:pk>/approve/', RepaymentBatchApproveView.as_view(), name='batch_approve'),
    path('records/',             RepaymentRecordListView.as_view(),      name='record_list'),
]
