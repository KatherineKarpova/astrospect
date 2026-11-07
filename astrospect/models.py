from django.db import models


class SavedChart(models.Model):
    token_digest = models.CharField(max_length=64, unique=True)
    chart_context = models.JSONField()
    chart_svg = models.TextField()
    has_birth_time = models.BooleanField(default=False)
    placement_summaries = models.JSONField(default=dict)
    placement_summary_version = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
