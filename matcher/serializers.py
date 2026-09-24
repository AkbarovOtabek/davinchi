from rest_framework import serializers

from .models import ActionLog, BotConfig, GeneratedMessage, Profile


class BotConfigSerializer(serializers.ModelSerializer):
    is_paused = serializers.BooleanField(read_only=True)

    class Meta:
        model = BotConfig
        exclude = ["id"]


class GeneratedMessageSerializer(serializers.ModelSerializer):
    profile_title = serializers.CharField(source="profile.__str__", read_only=True)

    class Meta:
        model = GeneratedMessage
        fields = [
            "id", "profile", "profile_title", "text", "language", "strategy", "model",
            "prompt_tokens", "completion_tokens", "status", "error", "sent_at", "created_at",
        ]
        read_only_fields = fields


class ProfileSerializer(serializers.ModelSerializer):
    has_description = serializers.BooleanField(read_only=True)
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = [
            "id", "chat_id", "message_id", "name", "age", "city", "about", "raw_caption",
            "language", "photos", "status", "skip_reason", "has_description",
            "created_at", "last_message",
        ]
        read_only_fields = fields

    def get_last_message(self, obj):
        message = obj.messages.first()
        return GeneratedMessageSerializer(message).data if message else None


class ActionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionLog
        fields = ["id", "level", "event", "detail", "profile", "created_at"]
        read_only_fields = fields


class GenerateRequestSerializer(serializers.Serializer):
    """Ручная генерация: либо raw_caption, либо разобранные поля."""

    raw_caption = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True, default="")
    age = serializers.IntegerField(required=False, allow_null=True, min_value=14, max_value=99)
    city = serializers.CharField(required=False, allow_blank=True, default="")
    about = serializers.CharField(required=False, allow_blank=True, default="")
    tone = serializers.ChoiceField(choices=BotConfig.TONE_CHOICES, required=False)
    max_chars = serializers.IntegerField(required=False, min_value=60, max_value=900)
    use_vision = serializers.BooleanField(required=False)
    save = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if not attrs.get("raw_caption") and not any(
            [attrs.get("name"), attrs.get("age"), attrs.get("city"), attrs.get("about")]
        ):
            raise serializers.ValidationError(
                "Передайте raw_caption или хотя бы одно из полей name/age/city/about."
            )
        return attrs


class ParseRequestSerializer(serializers.Serializer):
    caption = serializers.CharField()


class PauseRequestSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=0, max_value=60 * 48, default=60)
