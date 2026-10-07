import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q


class User(models.Model):
    # a UUID avoids exposing guessable visitor ids in links or page data.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # uniqueness supports fast session lookups; null permits an identity to
    # survive an expired session through its separate browser-token digest.
    session_key = models.CharField(max_length=40, unique=True, null=True, blank=True)
    browser_token_digest = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"

    def __str__(self):
        return str(self.pk)


class ZodiacSign(models.Model):
    # position is the zodiac's stable circular order, not database insertion
    # order, so chart calculations can use constant-time modular arithmetic.
    name = models.CharField(max_length=20, unique=True)
    slug = models.SlugField(max_length=20, unique=True)
    position = models.PositiveSmallIntegerField(unique=True)
    element = models.CharField(max_length=10)
    modality = models.CharField(max_length=10)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return self.name


class Planet(models.Model):
    name = models.CharField(max_length=20, unique=True)
    symbol = models.CharField(max_length=4, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name


class PlanetaryRulership(models.Model):
    planet = models.ForeignKey(
        Planet,
        on_delete=models.CASCADE,
        related_name="rulerships",
    )
    sign = models.ForeignKey(
        ZodiacSign,
        on_delete=models.CASCADE,
        related_name="rulers",
    )

    class Meta:
        # a sign has one classical ruler, while a planet may rule two signs.
        constraints = [
            models.UniqueConstraint(
                fields=["planet", "sign"],
                name="unique_planetary_rulership",
            ),
            models.UniqueConstraint(
                fields=["sign"],
                name="unique_zodiac_sign_ruler",
            ),
        ]


class PlanetSignDignity(models.Model):
    class Condition(models.TextChoices):
        EXALTED = "exalted", "Exalted"
        DIGNIFIED = "dignified", "Dignified"
        NEUTRAL = "neutral", "Neutral"
        UNDIGNIFIED = "undignified", "Undignified"
        DEBILITATED = "debilitated", "Debilitated"

    planet = models.ForeignKey(
        Planet,
        on_delete=models.CASCADE,
        related_name="sign_conditions",
    )
    sign = models.ForeignKey(
        ZodiacSign,
        on_delete=models.CASCADE,
        related_name="planet_conditions",
    )
    condition = models.CharField(max_length=12, choices=Condition.choices)

    class Meta:
        # one row per planet-sign pair makes a condition lookup unambiguous.
        constraints = [
            models.UniqueConstraint(
                fields=["planet", "sign"],
                name="unique_planet_sign_dignity",
            ),
        ]


class House(models.Model):
    # short JSON arrays keep life-area labels structured for display while
    # allowing future translations or multiple descriptors without schema churn.
    number = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=30, unique=True)
    representations = models.JSONField(default=list)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return f"{self.name}: {', '.join(self.representations)}"


class BirthChart(models.Model):
    class HouseSystem(models.TextChoices):
        WHOLE_SIGN = "whole_sign", "Whole sign"
        PLACIDUS = "placidus", "Placidus"

    class ZodiacSystem(models.TextChoices):
        TROPICAL = "tropical", "Tropical"
        SIDEREAL = "sidereal", "Sidereal"

    class Ayanamsa(models.TextChoices):
        LAHIRI = "lahiri", "Lahiri/Chitrapaksha"

    # the source form remains queryable as JSON while parsed columns below
    # keep common birthplace and coordinate filters simple and indexable.
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="birth_charts",
    )
    form_data = models.JSONField()
    chart_context = models.JSONField()
    placements = models.JSONField(null=True, blank=True)
    birthplace = models.CharField(max_length=300, null=True, blank=True)
    location_id = models.CharField(max_length=1000, null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    birth_timezone = models.CharField(max_length=100, null=True, blank=True)
    has_birth_time = models.BooleanField(default=False)
    house_system = models.CharField(
        max_length=12,
        choices=HouseSystem.choices,
        default=HouseSystem.WHOLE_SIGN,
    )
    zodiac_system = models.CharField(
        max_length=8,
        choices=ZodiacSystem.choices,
        default=ZodiacSystem.TROPICAL,
    )
    ayanamsa = models.CharField(
        max_length=20,
        choices=Ayanamsa.choices,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # postgresql's GIN index accelerates containment searches in form data;
        # it costs some insert work but avoids full-table scans as charts grow.
        indexes = [
            GinIndex(fields=["form_data"], name="birthchart_form_data_gin"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Birth chart {self.pk} for {self.user_id}"


class BirthChartSummary(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="chart_summaries",
    )
    birth_chart = models.ForeignKey(
        BirthChart,
        on_delete=models.CASCADE,
        related_name="summaries",
        null=True,
        blank=True,
    )
    placement_key = models.CharField(max_length=40)
    summary_data = models.JSONField()
    version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # chart-scoped uniqueness permits one current cache entry per
        # placement, while null-chart legacy summaries can still be retained.
        constraints = [
            models.UniqueConstraint(
                fields=["birth_chart", "placement_key"],
                condition=Q(birth_chart__isnull=False),
                name="unique_chart_placement_summary",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "placement_key"]),
        ]


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    birth_chart = models.ForeignKey(
        BirthChart,
        on_delete=models.SET_NULL,
        related_name="chat_messages",
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=9, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # this compound index supports newest-message history reads by visitor
        # and chart without sorting the visitor's entire conversation archive.
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["user", "birth_chart", "created_at"]),
        ]


class SavedChart(models.Model):
    token_digest = models.CharField(max_length=64, unique=True)
    chart_context = models.JSONField()
    chart_svg = models.TextField()
    has_birth_time = models.BooleanField(default=False)
    placement_summaries = models.JSONField(default=dict)
    placement_summary_version = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
