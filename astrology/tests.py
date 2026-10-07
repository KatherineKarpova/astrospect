import hashlib
import secrets
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from astrology.chatbot import SOURCES, build_chart_context, summarize_placement
from astrology.forms import BirthChartForm
from astrology.models import (
    BirthChart,
    BirthChartSummary,
    ChatMessage,
    House,
    Planet,
    PlanetSignDignity,
    PlanetaryRulership,
    SavedChart,
    User,
    ZodiacSign,
)
from astrology.services import (
    BirthplaceLookupError,
    calculate_birth_chart,
    calculate_daily_sign_changes,
    generate_chart_svg,
    resolve_birthplace,
)


class PlacementSummaryViewTests(TestCase):
    def setUp(self):
        session = self.client.session
        session["chart_context"] = {
            "zodiac": "tropical",
            "house_system": "whole_sign",
            "birth_time_known": True,
            "planets": [
                {
                    "name": "Sun",
                    "sign": "Virgo",
                    "degree": 4.2,
                    "house": 1,
                    "house_sign": "Virgo",
                    "house_topic": "self, body, and approach",
                    "essential_dignity": [],
                }
            ],
            "angles": [],
        }
        session.save()

    def test_returns_ai_summary_for_chart_placement(self):
        result = {
            "summary": "A plain-language traditional interpretation.",
            "sources": [
                {
                    "id": "BPHS",
                    "title": "Brihat Parashara Hora Shastra",
                    "url": "https://example.test/source",
                }
            ],
        }
        with patch("astrology.views.summarize_placement", return_value=result):
            response = self.client.post(
                "/placement-summary/",
                {"placement": "sun"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)

    def test_reopening_placement_reuses_cached_summary(self):
        result = {
            "summary": "Moon in Gemini connects thought with home and roots.",
            "sources": [],
        }
        with patch("astrology.views.summarize_placement", return_value=result) as generate:
            first = self.client.post("/placement-summary/", {"placement": "sun"})
            second = self.client.post("/placement-summary/", {"placement": "sun"})

        self.assertEqual(first.json(), result)
        self.assertEqual(second.json(), result)
        generate.assert_called_once()

    def test_saved_chart_link_loads_chart_without_prior_browser_session(self):
        token = secrets.token_urlsafe(32)
        chart = SavedChart.objects.create(
            token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            chart_context=self.client.session["chart_context"],
            chart_svg="<svg>chart</svg>",
            has_birth_time=True,
        )

        response = self.client.get(f"/saved/{token}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sun in Virgo")
        self.assertNotContains(response, "No special sign condition")
        self.assertEqual(self.client.session["saved_chart_id"], chart.pk)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(
            response["X-Robots-Tag"],
            "noindex, nofollow, noarchive",
        )

    def test_saved_chart_summary_is_cached_in_database(self):
        token = secrets.token_urlsafe(32)
        chart = SavedChart.objects.create(
            token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            chart_context=self.client.session["chart_context"],
            chart_svg="<svg>chart</svg>",
            has_birth_time=True,
        )
        session = self.client.session
        session["saved_chart_id"] = chart.pk
        session.save()
        result = {"summary": "Stable saved summary.", "sources": []}

        with patch("astrology.views.summarize_placement", return_value=result) as generate:
            first = self.client.post("/placement-summary/", {"placement": "sun"})
            second = self.client.post("/placement-summary/", {"placement": "sun"})

        self.assertEqual(first.json(), result)
        self.assertEqual(second.json(), result)
        generate.assert_called_once()
        chart.refresh_from_db()
        self.assertEqual(chart.placement_summaries["sun"], result)

    def test_generated_summary_is_saved_as_json_for_anonymous_user(self):
        result = {"summary": "Persisted summary.", "sources": []}
        with patch("astrology.views.summarize_placement", return_value=result):
            response = self.client.post(
                "/placement-summary/",
                {"placement": "sun"},
            )

        self.assertEqual(response.status_code, 200)
        visitor = User.objects.get(session_key=self.client.session.session_key)
        summary = BirthChartSummary.objects.get(user=visitor, placement_key="sun")
        self.assertEqual(summary.summary_data, result)
        self.assertIsNone(summary.birth_chart_id)

    def test_private_link_can_delete_its_saved_chart(self):
        token = secrets.token_urlsafe(32)
        chart = SavedChart.objects.create(
            token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            chart_context=self.client.session["chart_context"],
            chart_svg="<svg>chart</svg>",
            has_birth_time=True,
        )

        response = self.client.post(f"/saved/{token}/delete/")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SavedChart.objects.filter(pk=chart.pk).exists())

    def test_rejects_placement_not_in_session_chart(self):
        with patch("astrology.views.summarize_placement") as summarize:
            response = self.client.post(
                "/placement-summary/",
                {"placement": "pluto"},
            )

        self.assertEqual(response.status_code, 400)
        summarize.assert_not_called()

    def test_requires_post(self):
        response = self.client.get("/placement-summary/")

        self.assertEqual(response.status_code, 405)


class TraditionalChartContextTests(TestCase):
    def test_birth_time_without_location_is_used_as_utc_planetary_time(self):
        with patch("astrology.services.AstrologicalSubject") as subject_factory:
            calculate_birth_chart(
                {
                    "name": "Test",
                    "birth_date": date(1997, 9, 22),
                    "birth_time": time(13, 5),
                    "has_birth_location": False,
                },
                "UTC",
            )

        kwargs = subject_factory.call_args.kwargs
        self.assertEqual((kwargs["hour"], kwargs["minute"]), (13, 5))
        self.assertEqual(kwargs["tz_str"], "UTC")
        self.assertEqual((kwargs["lat"], kwargs["lng"]), (0, 0))

    def test_ascendant_sets_the_whole_sign_house_sequence(self):
        chart = SimpleNamespace(
            ascendant=SimpleNamespace(sign_num=8, position=12.5),
            descendant=SimpleNamespace(sign_num=2, position=12.5),
            medium_coeli=SimpleNamespace(sign_num=5, position=12.5),
            imum_coeli=SimpleNamespace(sign_num=11, position=12.5),
            sun=SimpleNamespace(sign_num=8, position=12.5, retrograde=False),
            moon=SimpleNamespace(sign_num=1, position=12.5, retrograde=False),
            mercury=SimpleNamespace(sign_num=5, position=12.5, retrograde=False),
            venus=SimpleNamespace(sign_num=7, position=12.5, retrograde=False),
            mars=SimpleNamespace(sign_num=9, position=12.5, retrograde=False),
            jupiter=SimpleNamespace(sign_num=3, position=12.5, retrograde=False),
            saturn=SimpleNamespace(sign_num=0, position=12.5, retrograde=False),
        )

        context = build_chart_context(chart, True)

        self.assertEqual(context["ascendant"]["sign"], "Sagittarius")
        self.assertEqual(context["whole_sign_houses"][0]["sign"], "Sagittarius")
        self.assertEqual(context["whole_sign_houses"][1]["sign"], "Capricorn")
        self.assertEqual(context["whole_sign_houses"][-1]["sign"], "Scorpio")

    def test_unknown_birthplace_suppresses_houses_and_records_sign_changes(self):
        chart = SimpleNamespace(
            ascendant=SimpleNamespace(sign_num=8, position=12.5),
            sun=SimpleNamespace(sign_num=5, position=12.5, retrograde=False),
            moon=SimpleNamespace(sign_num=2, position=12.5, retrograde=False),
            mercury=SimpleNamespace(sign_num=5, position=12.5, retrograde=False),
            venus=SimpleNamespace(sign_num=7, position=12.5, retrograde=False),
            mars=SimpleNamespace(sign_num=9, position=12.5, retrograde=False),
            jupiter=SimpleNamespace(sign_num=3, position=12.5, retrograde=False),
            saturn=SimpleNamespace(sign_num=0, position=12.5, retrograde=False),
        )
        context = build_chart_context(
            chart,
            has_birth_time=False,
            has_birth_location=False,
            daily_sign_changes={"Moon": ["Gemini", "Cancer"]},
        )
        moon = next(planet for planet in context["planets"] if planet["name"] == "Moon")

        self.assertEqual(context["angles"], [])
        self.assertEqual(context["whole_sign_houses"], [])
        self.assertIsNone(moon["house"])
        self.assertEqual(moon["possible_sign_change"], ["Gemini", "Cancer"])

    def test_entered_time_without_birthplace_is_recorded_but_has_no_houses(self):
        chart = SimpleNamespace(**{
            name.lower(): SimpleNamespace(sign_num=5, position=12.5, retrograde=False)
            for name in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn")
        })
        context = build_chart_context(
            chart,
            has_birth_time=True,
            has_birth_location=False,
        )
        self.assertTrue(context["birth_time_known"])
        self.assertEqual(context["birth_time_basis"], "UTC approximation without birthplace")
        self.assertFalse(context["house_data_available"])
        self.assertEqual(context["angles"], [])
        self.assertTrue(all(planet["house"] is None for planet in context["planets"]))

    def test_daily_sign_change_detector_checks_all_traditional_planets(self):
        with patch("astrology.services.AstrologicalSubject") as subject_factory:
            subject_factory.side_effect = [
                SimpleNamespace(**{
                    name.lower(): SimpleNamespace(sign_num=0)
                    for name in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn")
                }),
                SimpleNamespace(**{
                    name.lower(): SimpleNamespace(
                        sign_num=1 if name == "Moon" else 0
                    )
                    for name in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn")
                }),
            ]
            changes = calculate_daily_sign_changes({
                "birth_date": date(1997, 9, 22),
            })

        self.assertEqual(changes, {"Moon": ["Aries", "Taurus"]})

    def test_calculates_sign_dignity_separately_from_house_strength(self):
        chart = SimpleNamespace(
            ascendant=SimpleNamespace(sign_num=0, position=12.5),
            descendant=SimpleNamespace(sign_num=6, position=12.5),
            medium_coeli=SimpleNamespace(sign_num=9, position=12.5),
            imum_coeli=SimpleNamespace(sign_num=3, position=12.5),
            sun=SimpleNamespace(sign_num=0, position=12.5, retrograde=False),
            moon=SimpleNamespace(sign_num=1, position=12.5, retrograde=False),
            mercury=SimpleNamespace(sign_num=5, position=12.5, retrograde=False),
            venus=SimpleNamespace(sign_num=7, position=12.5, retrograde=False),
            mars=SimpleNamespace(sign_num=9, position=12.5, retrograde=False),
            jupiter=SimpleNamespace(sign_num=3, position=12.5, retrograde=False),
            saturn=SimpleNamespace(sign_num=0, position=12.5, retrograde=False),
        )

        context = build_chart_context(chart, True)
        planets = {planet["name"]: planet for planet in context["planets"]}

        self.assertEqual(planets["Sun"]["essential_dignity"], ["exalted"])
        self.assertEqual(planets["Sun"]["house"], 1)
        self.assertEqual(planets["Sun"]["house_strength"], "angular")
        self.assertEqual(
            planets["Mercury"]["essential_dignity"],
            ["own sign", "exalted"],
        )
        self.assertEqual(planets["Saturn"]["essential_dignity"], ["debilitated"])

    def test_only_approved_jyotisha_source_is_available_to_summaries(self):
        self.assertEqual([source["id"] for source in SOURCES], ["BPHS"])

    def test_planet_summary_receives_planet_sign_house_topic_and_condition(self):
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(
                content='Moon in Gemini brings "quick" reflection into close partnerships.'
            ))
        ]
        placement = {
            "name": "Moon",
            "sign": "Gemini",
            "house": 7,
            "house_sign": "Gemini",
            "house_topic": "partnerships and agreements",
            "essential_dignity": [],
            "house_strength": "non-angular",
        }

        with patch("astrology.chatbot.OpenAI", return_value=client):
            result = summarize_placement(
                {"zodiac": "tropical", "house_system": "whole_sign"},
                placement,
            )

        supplied_facts = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn('"Moon"', supplied_facts)
        self.assertIn('"Gemini"', supplied_facts)
        self.assertIn('"house": 7', supplied_facts)
        self.assertIn("partnerships and agreements", supplied_facts)
        self.assertNotIn('"house_sign"', supplied_facts)
        self.assertIn('"essential_dignity": []', supplied_facts)
        self.assertEqual(
            result["summary"],
            'Moon in Gemini brings "quick" reflection into close partnerships.',
        )
        self.assertNotIn("sources", result)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("response_format", kwargs)
        self.assertIn(
            "ONLY placement being explained is Moon in Gemini",
            kwargs["messages"][0]["content"],
        )
        self.assertIn("NOT the Ascendant", kwargs["messages"][0]["content"])
        self.assertNotIn('"ascendant"', supplied_facts)

    def test_ascendant_summary_receives_lagna_and_house_sequence_instructions(self):
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(
                content="Sagittarius rising can suggest an open and exploratory approach."
            ))
        ]
        context = {
            "zodiac": "tropical",
            "house_system": "whole_sign",
            "ascendant": {"sign": "Sagittarius"},
            "whole_sign_houses": [
                {"house": 1, "sign": "Sagittarius"},
                {"house": 2, "sign": "Capricorn"},
            ],
        }

        with patch("astrology.chatbot.OpenAI", return_value=client):
            result = summarize_placement(context, {
                "name": "Ascendant",
                "sign": "Sagittarius",
            })

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("first impression", messages[0]["content"])
        self.assertIn("sets the sign", messages[0]["content"])
        self.assertIn('"Sagittarius"', messages[1]["content"])
        self.assertIn("Sagittarius", result["summary"])
        self.assertIn("Ascendant", result["summary"])

    def test_non_ascendant_summary_falls_back_when_model_explains_ascendant(self):
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(
                content="Your Ascendant shapes how you approach relationships."
            ))
        ]
        with patch("astrology.chatbot.OpenAI", return_value=client):
            result = summarize_placement(
                {"zodiac": "tropical", "house_system": "whole_sign"},
                {
                    "name": "Venus",
                    "sign": "Taurus",
                    "house": 7,
                    "house_topic": "partnerships and agreements",
                },
            )
        self.assertIn("Venus in Taurus", result["summary"])
        self.assertIn(
            "whole-sign house 7: partnerships and agreements",
            result["summary"],
        )
        self.assertNotIn("(Gemini)", result["summary"])
        self.assertNotIn("Ascendant", result["summary"])

    def test_house_labels_follow_the_wheel_and_colors_follow_elements(self):
        svg = (
            "<svg viewBox='40 40 500 500'><title>Chart</title>"
            "<!-- Colors --><style>:root { "
            "--kerykeion-chart-color-zodiac-bg-0: #ff7200; "
            "--kerykeion-chart-color-zodiac-icon-0: #ff7200; "
            "--kerykeion-chart-color-houses-radix-line: #ff0000; "
            "--kerykeion-chart-color-zodiac-radix-ring-0: #ff0000; "
            "--kerykeion-chart-color-zodiac-transit-ring-0: #ff0000; "
            "}</style><g kr:node='HouseNumber'><text "
            "style='fill: #000000; font-size: 14px'><tspan "
            "x='51.54' y='292.69'>1</tspan></text></g>"
            "<g kr:node='ChartPoint' kr:house='Fifth_House' "
            "kr:sign='Ari' kr:slug='Saturn'></g></svg>"
        )
        with patch("astrology.services.KerykeionChartSVG") as chart_svg:
            chart_svg.return_value.makeWheelOnlyTemplate.return_value = svg
            result = generate_chart_svg(object(), show_houses=True)

        self.assertIn("viewBox='40 40 500 500'", result)
        self.assertIn("x='51.54' y='292.69'>1</tspan>", result)
        self.assertNotIn("1st House", result)
        self.assertNotIn("Whole-sign house labels", result)
        self.assertNotIn("textPath", result)
        self.assertIn("--kerykeion-chart-color-zodiac-bg-0: #e7b493", result)
        self.assertIn("--kerykeion-chart-color-zodiac-icon-0: #a35428", result)
        self.assertIn("--kerykeion-chart-color-houses-radix-line: #57544d", result)
        self.assertIn("--kerykeion-chart-color-zodiac-radix-ring-0: #57544d", result)
        self.assertIn("--kerykeion-chart-color-zodiac-transit-ring-0: #57544d", result)
        self.assertIn("--kerykeion-chart-color-saturn: #a35428", result)


class ChartPagePlacementTests(TestCase):
    def test_anonymous_browser_identity_survives_a_new_session(self):
        first_response = self.client.get("/")
        browser_cookie = first_response.cookies["astrology_browser"].value
        visitor = User.objects.get(
            session_key=self.client.session.session_key,
        )

        self.client.cookies.pop("sessionid")
        self.client.get("/")

        self.assertEqual(User.objects.count(), 1)
        visitor.refresh_from_db()
        self.assertEqual(visitor.browser_token_digest, hashlib.sha256(
            browser_cookie.encode("ascii")
        ).hexdigest())
        self.assertEqual(visitor.session_key, self.client.session.session_key)

    def test_zodiac_reference_data_is_seeded(self):
        self.assertEqual(ZodiacSign.objects.count(), 12)
        self.assertEqual(Planet.objects.count(), 7)
        self.assertEqual(PlanetaryRulership.objects.count(), 12)
        self.assertEqual(PlanetSignDignity.objects.count(), 84)
        self.assertEqual(House.objects.count(), 12)
        self.assertEqual(
            House.objects.get(number=7).representations,
            ["partnerships", "agreements"],
        )
        self.assertEqual(
            PlanetSignDignity.objects.get(
                planet__name="Sun",
                sign__name="Aries",
            ).condition,
            PlanetSignDignity.Condition.EXALTED,
        )
        self.assertEqual(
            PlanetSignDignity.objects.get(
                planet__name="Sun",
                sign__name="Aquarius",
            ).condition,
            PlanetSignDignity.Condition.UNDIGNIFIED,
        )

    def test_chat_messages_are_persisted_for_anonymous_user(self):
        session = self.client.session
        session["chart_context"] = {
            "zodiac": "tropical",
            "house_system": "whole_sign",
            "planets": [],
            "angles": [],
        }
        session.save()

        histories = []

        def answer(_chart, _question, history, _level, _tone):
            histories.append(list(history))
            return {"answer": "A chart-based response."}

        with patch("astrology.views.ask_chart_guide", side_effect=answer):
            self.assertEqual(
                self.client.post("/chat/", {"question": "What stands out?"}).status_code,
                200,
            )
            self.client.post("/chat/", {"question": "And next?"})

        visitor = User.objects.get(session_key=self.client.session.session_key)
        messages = list(
            ChatMessage.objects.filter(user=visitor).order_by("created_at", "pk")
        )
        self.assertEqual(
            [(message.role, message.content) for message in messages],
            [
                (ChatMessage.Role.USER, "What stands out?"),
                (ChatMessage.Role.ASSISTANT, "A chart-based response."),
                (ChatMessage.Role.USER, "And next?"),
                (ChatMessage.Role.ASSISTANT, "A chart-based response."),
            ],
        )
        self.assertEqual(
            histories[1],
            [
                {"role": "user", "content": "What stands out?"},
                {"role": "assistant", "content": "A chart-based response."},
            ],
        )

    def test_birthplace_is_optional_but_partial_selection_is_explained(self):
        form = BirthChartForm(data={
            "birth_month": "9",
            "birth_day": "22",
            "birth_year": "1997",
            "birthplace": "",
            "location_id": "",
            "latitude": "",
            "longitude": "",
            "birth_timezone": "",
            "birth_time": "",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["has_birth_location"])

        partial_form = BirthChartForm(data={
            "birth_month": "9",
            "birth_day": "22",
            "birth_year": "1997",
            "birthplace": "New York",
            "location_id": "some-location-id",
            "latitude": "",
            "longitude": "",
            "birth_timezone": "",
            "birth_time": "",
        })
        self.assertFalse(partial_form.is_valid())
        self.assertIn(
            "Choose a suggestion",
            str(partial_form.non_field_errors()),
        )

    def test_birth_date_fields_render_in_a_single_horizontal_row(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="date-fields"')
        self.assertContains(response, 'class="date-field"', count=3)

    def test_chat_interface_has_distinct_message_bubble_layout(self):
        context = {"birth_location_known": False, "planets": [], "angles": []}
        with (
            patch("astrology.views.calculate_daily_sign_changes", return_value={}),
            patch("astrology.views.calculate_birth_chart", return_value=object()),
            patch("astrology.views.build_chart_context", return_value=context),
            patch("astrology.views.generate_chart_svg", return_value="<svg></svg>"),
        ):
            response = self.client.post(
                "/chart/",
                {
                    "birth_month": "9",
                    "birth_day": "22",
                    "birth_year": "1997",
                    "birthplace": "",
                    "location_id": "",
                    "latitude": "",
                    "longitude": "",
                    "birth_timezone": "",
                    "birth_time": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="log"')
        self.assertContains(response, "chat-bubble-${role}")
        self.assertContains(response, 'appendBubble("user", question)')

    def test_birthplace_text_without_suggestion_is_valid_for_server_lookup(self):
        form = BirthChartForm(data={
            "birth_month": "9",
            "birth_day": "22",
            "birth_year": "1997",
            "birthplace": "Miami, Florida",
            "location_id": "",
            "latitude": "",
            "longitude": "",
            "birth_timezone": "",
            "birth_time": "13:05",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["needs_location_lookup"])

    @patch("astrology.services.urlopen")
    def test_server_birthplace_lookup_resolves_coordinates_and_timezone(self, open_url):
        response = MagicMock()
        response.read.return_value = (
            b'{"results":[{"formatted":"Miami, Florida, United States",'
            b'"place_id":"miami-id","lat":25.77,"lon":-80.19,'
            b'"timezone":{"name":"America/New_York"}}]}'
        )
        open_url.return_value.__enter__.return_value = response
        with patch("astrology.services.settings.GEOAPIFY_API_KEY", "test-key"):
            result = resolve_birthplace("Miami, Florida")
        self.assertEqual(result["location_id"], "miami-id")
        self.assertEqual(result["birth_timezone"], "America/New_York")
        self.assertEqual(result["latitude"], 25.77)

    @patch("astrology.views.resolve_birthplace")
    @patch("astrology.views.generate_chart_svg", return_value="<svg></svg>")
    @patch("astrology.views.build_chart_context", return_value={
        "birth_location_known": True, "planets": [], "angles": [],
    })
    @patch("astrology.views.calculate_birth_chart", return_value=object())
    def test_text_autofilled_birthplace_is_resolved_without_suggestion_selection(
        self,
        calculate_chart,
        build_context,
        render_svg,
        resolve,
    ):
        resolve.return_value = {
            "birthplace": "Miami, Florida, United States",
            "location_id": "miami-id",
            "latitude": 25.77,
            "longitude": -80.19,
            "birth_timezone": "America/New_York",
            "has_birth_location": True,
        }
        response = self.client.post("/chart/", {
            "birth_month": "9",
            "birth_day": "22",
            "birth_year": "1997",
            "birthplace": "Miami, Florida",
            "location_id": "",
            "latitude": "",
            "longitude": "",
            "birth_timezone": "",
            "birth_time": "13:05",
        })
        self.assertEqual(response.status_code, 200)
        resolve.assert_called_once_with("Miami, Florida")
        self.assertTrue(build_context.call_args.args[2])
        self.assertEqual(
            calculate_chart.call_args.args[0]["birth_timezone"],
            "America/New_York",
        )

    def test_chart_without_birthplace_uses_entered_time_as_utc_and_omits_houses(self):
        context = {
            "birth_location_known": False,
            "planets": [],
            "angles": [],
            "whole_sign_houses": [],
            "daily_sign_changes": {"Moon": ["Gemini", "Cancer"]},
        }
        post_data = {
            "birth_month": "9",
            "birth_day": "22",
            "birth_year": "1997",
            "birthplace": "",
            "location_id": "",
            "latitude": "",
            "longitude": "",
            "birth_timezone": "",
            "birth_time": "13:05",
        }

        with (
            patch("astrology.views.calculate_daily_sign_changes", return_value={"Moon": ["Gemini", "Cancer"]}) as sign_changes,
            patch("astrology.views.calculate_birth_chart", return_value=object()) as calculate_chart,
            patch("astrology.views.build_chart_context", return_value=context) as build_context,
            patch("astrology.views.generate_chart_svg", return_value="<svg></svg>") as render_svg,
        ):
            response = self.client.post("/chart/", post_data)

        self.assertEqual(response.status_code, 200)
        sign_changes.assert_called_once()
        calculate_chart.assert_called_once()
        self.assertEqual(calculate_chart.call_args.args[1], "UTC")
        self.assertFalse(calculate_chart.call_args.args[0]["has_birth_location"])
        self.assertEqual(
            calculate_chart.call_args.args[0]["birth_time"],
            time(13, 5),
        )
        build_context.assert_called_once_with(
            calculate_chart.return_value,
            True,
            False,
            {"Moon": ["Gemini", "Cancer"]},
        )
        render_svg.assert_called_once_with(
            calculate_chart.return_value,
            show_houses=False,
        )
        self.assertNotIn("saved_chart_id", self.client.session)
        self.assertEqual(
            self.client.session["chart_context"]["daily_sign_changes"]["Moon"],
            ["Gemini", "Cancer"],
        )
        saved_chart = BirthChart.objects.get(user__session_key=self.client.session.session_key)
        self.assertEqual(saved_chart.form_data["birth_time"], "13:05:00")
        self.assertIsNone(saved_chart.form_data["birthplace"])
        self.assertIsNone(saved_chart.birthplace)
        self.assertIsNone(saved_chart.latitude)
        self.assertIsNone(saved_chart.placements)
        self.assertTrue(build_context.call_args.args[1])
        self.assertContains(response, "Birth time received: 13:05")
        self.assertContains(response, "UTC approximation")
        self.assertNotContains(response, "House not calculated because no birthplace")

    def test_invalid_chart_post_redisplays_form_with_validation_errors(self):
        response = self.client.post(
            "/chart/",
            {
                "birth_month": "2",
                "birth_day": "30",
                "birth_year": "1997",
                "birthplace": "Miami, Florida",
                "location_id": "test-location",
                "latitude": "25.774",
                "longitude": "-80.193",
                "birth_timezone": "America/New_York",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertContains(response, 'role="alert"')

    @patch("astrology.views.generate_chart_svg", return_value="<svg>1st House</svg>")
    @patch(
        "astrology.views.build_chart_context",
        return_value={
            "planets": [
                {
                    "name": "Sun",
                    "sign": "Virgo",
                    "degree": 4.2,
                    "house": 10,
                    "house_sign": "Capricorn",
                    "house_topic": "work, responsibility, and public role",
                    "essential_dignity": ["exalted"],
                    "house_strength": "angular",
                }
            ],
            "angles": [],
        },
    )
    @patch("astrology.views.calculate_birth_chart", return_value=object())
    def test_chart_page_renders_sign_house_and_dignity(
        self,
        calculate_chart,
        build_context,
        render_svg,
    ):
        response = self.client.post(
            "/chart/",
            {
                "name": "Test",
                "birth_month": "9",
                "birth_day": "22",
                "birth_year": "1997",
                "birthplace": "Miami, Florida",
                "location_id": "test-location",
                "latitude": "25.774",
                "longitude": "-80.193",
                "birth_timezone": "America/New_York",
                "birth_time": "13:05",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sun in Virgo")
        self.assertContains(response, "House 10")
        self.assertContains(response, "Exalted")
        self.assertContains(response, "1st House")
        self.assertContains(response, "Birth time received: 13:05")
        calculate_chart.assert_called_once()
        build_context.assert_called_once()
        render_svg.assert_called_once()
        self.assertNotIn("saved_chart_id", self.client.session)
        self.assertContains(response, "Traditional source:")
        self.assertNotContains(response, "Copy private link")

    @patch("astrology.views.generate_chart_svg", return_value="<svg>chart</svg>")
    @patch(
        "astrology.views.build_chart_context",
        return_value={"planets": [], "angles": [], "birth_location_known": False},
    )
    @patch("astrology.views.calculate_birth_chart", return_value=object())
    def test_browser_cache_is_opt_in_and_chart_is_not_stored_in_saved_chart_table(
        self,
        calculate_chart,
        build_context,
        render_svg,
    ):
        response = self.client.post(
            "/chart/",
            {
                "birth_month": "9",
                "birth_day": "22",
                "birth_year": "1997",
                "birth_time": "",
                "save_to_browser": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["browser_cache_enabled"])
        self.assertFalse(SavedChart.objects.exists())
        self.assertContains(response, "Saved in this browser")

    def test_planet_house_note_is_not_repeated_on_each_placement(self):
        from astrology.views import chart_display_context

        context = chart_display_context(
            {
                "birth_location_known": False,
                "birth_time_known": False,
                "planets": [{
                    "name": "Sun",
                    "sign": "Virgo",
                    "degree": 4.2,
                    "house": None,
                    "house_sign": None,
                    "house_topic": None,
                    "essential_dignity": [],
                }],
                "angles": [],
            },
            "<svg></svg>",
            has_birth_time=False,
        )
        self.assertEqual(context["planet_placements"][0]["house_brief"], "")

    def test_chart_form_explains_opt_in_browser_cache(self):
        response = self.client.get("/")
        self.assertContains(response, "Save my chart details in this browser")
        self.assertContains(response, "Restore chart saved in this browser")
