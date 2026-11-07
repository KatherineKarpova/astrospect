from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SavedChart",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "token_digest",
                    models.CharField(max_length=64, unique=True),
                ),
                ("chart_context", models.JSONField()),
                ("chart_svg", models.TextField()),
                ("has_birth_time", models.BooleanField(default=False)),
                ("placement_summaries", models.JSONField(default=dict)),
                ("placement_summary_version", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
