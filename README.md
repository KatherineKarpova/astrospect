# Astrological Introspection

This Django app calculates **tropical zodiac, whole-sign houses** and offers a
source-grounded chat guide using approved traditional Jyotisha texts only. It
intentionally does not mix in sidereal recalculations, nakshatras, or dashas.
Traditional Jyotisha texts do not establish that ancient authors prescribed
tropical astrology; the app applies Jyotisha concepts to tropical placements
as a clearly labeled modern adaptation.

On the chart page, select a planet or (when birthplace and birth time are
known) an angle to expand an AI-written plain-language summary. The approved
traditional source is listed once at the bottom of the chart page. In the
question box, Enter sends the message and Shift+Enter inserts a new line.

Birth time without a selected birthplace is used as a UTC approximation for
planetary positions only. Houses and angles require both a birthplace and a
birth time; without a birthplace, a sign-change notice flags planets whose
sign may be uncertain.

Planet summaries distinguish traditional Jyotisha sign conditions (own sign,
exaltation, or debilitation) from house placement. In traditional astrology,
the angular houses (1st, 4th, 7th, and 10th) indicate a separate kind of
prominence; a house does not itself change a planet's sign-based condition.

New charts are held in the current browser session and are not stored as
permanent chart records. Optionally select **Save my chart details in this
browser** to keep the birth details on that device and restore the chart later.
Generated placement summaries are cached in the same browser when this option
is enabled. This browser cache is not synced or encrypted; anyone using that
browser profile can restore it. Use **Clear saved chart from this browser** on
the chart page (or clear the site data in the browser) to remove it. Existing
private links created by an earlier version remain available.

## Hugging Face inference setup

The chat and placement summaries use the Hugging Face Inference Providers
router through its OpenAI-compatible API. Set these values in `.env`:

```text
HF_TOKEN=your_fine_grained_hugging_face_token
HF_MODEL=
HF_BASE_URL=https://router.huggingface.co/v1
```

Give the token permission to make calls to Inference Providers. Keep `HF_MODEL`
empty until you have selected a model and confirmed its cost; the app keeps
inference disabled while this setting is empty. The app does not send the token
to the browser.

Anthropic Claude models are not currently listed in Hugging Face's Inference
Providers catalog. A Hugging Face token by itself does not include free
inference credits: Hugging Face currently lists no included Inference Provider
credits for Free accounts. Check your account's billing/credits and the
selected model's provider availability before enabling live inference.

The server sends chart facts, the approved source catalog in
`shared/sources.json`, and bounded chat history to Hugging Face for inference.
It does not include the user's name or raw birth location in those prompts.

## Free HTTPS deployment

`render.yaml` describes a Render free web service. Render provides a free
`onrender.com` subdomain and managed HTTPS certificate automatically. Deploy
from the repository using Render Blueprint, then set `GEOAPIFY_API_KEY` and
`HF_TOKEN` in the dashboard if chat must work while hosted.

A custom registrable domain is not normally free. If you later buy one,
attach it in Render and use its automatic certificate, or put it behind
Cloudflare's free DNS/proxy. Do not upload certificate private keys to this
repository.
