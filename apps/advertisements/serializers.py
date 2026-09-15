from rest_framework import serializers

from .models import Advertisement


class AdvertisementSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    subposts = serializers.SerializerMethodField()

    class Meta:
        model = Advertisement
        fields = (
            "id", "parent", "title", "description", "image", "image_url", "starts_at",
            "location", "price_label", "is_active", "date_created", "date_modified", "subposts",
        )
        read_only_fields = ("id", "image_url", "date_created", "date_modified", "subposts")

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url

    def validate_parent(self, value):
        if self.instance and value and value.pk == self.instance.pk:
            raise serializers.ValidationError("A post cannot be its own parent.")
        if value and value.parent_id:
            raise serializers.ValidationError("Subposts can only link to a top-level post.")
        return value

    def get_subposts(self, obj):
        posts = obj.subposts.filter(is_active=True).order_by("starts_at", "-date_created")
        return AdvertisementSerializer(posts, many=True, context=self.context).data
