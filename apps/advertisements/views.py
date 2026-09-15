from rest_framework import generics
from rest_framework.permissions import BasePermission, IsAuthenticated

from .models import Advertisement
from .serializers import AdvertisementSerializer


class IsSuperAdmin(BasePermission):
    message = "Only super admins can manage advertisements."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class AdvertisementListCreateView(generics.ListCreateAPIView):
    serializer_class = AdvertisementSerializer

    def get_queryset(self):
        queryset = Advertisement.objects.all()
        if self.request.user.is_superuser and self.request.query_params.get("manage") == "true":
            return queryset
        return queryset.filter(is_active=True, parent__isnull=True)

    def get_permissions(self):
        return [IsSuperAdmin()] if self.request.method == "POST" else [IsAuthenticated()]


class AdvertisementDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdvertisementSerializer

    def get_permissions(self):
        return [IsSuperAdmin()] if self.request.method in ("PUT", "PATCH", "DELETE") else [IsAuthenticated()]

    def get_queryset(self):
        queryset = Advertisement.objects.all()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(is_active=True)
