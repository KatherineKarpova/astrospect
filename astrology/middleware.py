import hashlib
import re
import secrets

from django.conf import settings

from .models import User


BROWSER_COOKIE_NAME = "astrology_browser"
BROWSER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
BROWSER_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class AnonymousUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # creating the session early lets every browser visit have a stable,
        # server-side identity before the view persists any chart or chat data.
        if request.session.session_key is None:
            request.session.create()
        session_key = request.session.session_key

        browser_token = request.COOKIES.get(BROWSER_COOKIE_NAME, "")
        # only a token with the expected shape is hashed and looked up; storing
        # the raw browser token would turn a database leak into reusable access.
        if BROWSER_TOKEN_PATTERN.fullmatch(browser_token):
            browser_digest = hashlib.sha256(
                browser_token.encode("ascii")
            ).hexdigest()
            visitor = User.objects.filter(
                browser_token_digest=browser_digest
            ).first()
        else:
            browser_digest = None
            visitor = None

        if visitor is None:
            visitor = User.objects.filter(session_key=session_key).first()

        new_browser_token = None
        if visitor is None:
            # random per-browser tokens let cleared sessions reconnect without
            # collecting account credentials or personally identifying data.
            new_browser_token = secrets.token_urlsafe(32)
            browser_digest = hashlib.sha256(
                new_browser_token.encode("ascii")
            ).hexdigest()
            visitor = User.objects.create(
                session_key=session_key,
                browser_token_digest=browser_digest,
            )
        else:
            if browser_digest is None:
                new_browser_token = secrets.token_urlsafe(32)
                browser_digest = hashlib.sha256(
                    new_browser_token.encode("ascii")
                ).hexdigest()
            User.objects.filter(session_key=session_key).exclude(
                pk=visitor.pk
            ).update(session_key=None)
            # a unique session key represents the browser's latest session,
            # while the durable browser token keeps its anonymous record stable.
            if (
                visitor.session_key != session_key
                or visitor.browser_token_digest != browser_digest
            ):
                visitor.session_key = session_key
                visitor.browser_token_digest = browser_digest
                visitor.save(update_fields=[
                    "session_key",
                    "browser_token_digest",
                    "updated_at",
                ])

        request.visitor = visitor
        response = self.get_response(request)
        if new_browser_token:
            response.set_cookie(
                BROWSER_COOKIE_NAME,
                new_browser_token,
                max_age=BROWSER_COOKIE_MAX_AGE,
                httponly=True,
                secure=settings.SESSION_COOKIE_SECURE,
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
        return response
