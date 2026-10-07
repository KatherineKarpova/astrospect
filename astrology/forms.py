from datetime import date
from django import forms 
from django.utils import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# validate the data from the submitted birth chart generation form
class BirthChartForm(forms.Form):
    HOUSE_SYSTEM_CHOICES = (
        ("whole_sign", "Whole sign"),
        ("placidus", "Placidus"),
    )
    ZODIAC_SYSTEM_CHOICES = (
        ("tropical", "Tropical"),
        ("sidereal", "Sidereal (Lahiri/Chitrapaksha)"),
    )

    name = forms.CharField(max_length=100, required=False)

    birth_month = forms.IntegerField(min_value=1, max_value=12)
    birth_day = forms.IntegerField(min_value=1, max_value=31)
    birth_year = forms.IntegerField(min_value=1900)

    birth_time = forms.TimeField(
        required=False,
        input_formats=['%H:%M', '%H:%M:%S'],
    )

    birthplace = forms.CharField(max_length=300, required=False)
    location_id = forms.CharField(max_length=1000, required=False)

    latitude = forms.FloatField(min_value=-90, max_value=90, required=False)
    longitude = forms.FloatField(min_value=-180, max_value=180, required=False)

    birth_timezone = forms.CharField(max_length=100, required=False)
    house_system = forms.ChoiceField(
        choices=HOUSE_SYSTEM_CHOICES,
        initial="whole_sign",
        required=False,
    )
    zodiac_system = forms.ChoiceField(
        choices=ZODIAC_SYSTEM_CHOICES,
        initial="tropical",
        required=False,
    )

    # check if time zone is in a recognized zone
    def clean_birth_timezone(self):
        timezone_name = self.cleaned_data.get("birth_timezone", "")
        if not timezone_name:
            return ""

        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise forms.ValidationError("Select a location with a valid time zone.")

        return timezone_name

    def clean(self):
        cleaned_data = super().clean()

        year = cleaned_data.get("birth_year")
        month = cleaned_data.get("birth_month")
        day = cleaned_data.get("birth_day")

        # only check the full date if all three fields passed validation.
        if year is not None and month is not None and day is not None:
            try:
                birth_date = date(year, month, day)
            except ValueError:
                self.add_error(
                    "birth_day",
                    "Enter a valid date of birth.",
                )
            else:
                if birth_date > timezone.localdate():
                    self.add_error(
                        "birth_year",
                        "Date of birth cannot be in the future.",
                    )
                else:
                    cleaned_data["birth_date"] = birth_date

        location_fields = (
            "location_id",
            "latitude",
            "longitude",
            "birth_timezone",
        )
        has_any_location_metadata = any(
            cleaned_data.get(name) not in (None, "")
            for name in location_fields
        )
        has_complete_location = bool(cleaned_data.get("birthplace")) and all(
            cleaned_data.get(name) not in (None, "")
            for name in location_fields
        )
        needs_location_lookup = (
            bool(cleaned_data.get("birthplace"))
            and not has_any_location_metadata
        )
        if has_any_location_metadata and not has_complete_location:
            self.add_error(
                None,
                "We could not verify the selected birthplace. Choose a suggestion or clear the birthplace field.",
            )
        cleaned_data["has_birth_location"] = has_complete_location
        cleaned_data["needs_location_lookup"] = needs_location_lookup
        cleaned_data["house_system"] = (
            cleaned_data.get("house_system") or "whole_sign"
        )
        cleaned_data["zodiac_system"] = (
            cleaned_data.get("zodiac_system") or "tropical"
        )
        # keeping the ayanamsa fixed avoids silently mixing sidereal standards;
        # tropical charts do not use an ayanamsa.
        cleaned_data["ayanamsa"] = (
            "lahiri"
            if cleaned_data.get("zodiac_system") == "sidereal"
            else None
        )

        return cleaned_data