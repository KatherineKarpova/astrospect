from django import template


register = template.Library()


SIGN_NAMES = {
    'Ari': 'Aries',
    'Tau': 'Taurus',
    'Gem': 'Gemini',
    'Can': 'Cancer',
    'Leo': 'Leo',
    'Vir': 'Virgo',
    'Lib': 'Libra',
    'Sco': 'Scorpio',
    'Sag': 'Sagittarius',
    'Cap': 'Capricorn',
    'Aqu': 'Aquarius',
    'Pis': 'Pisces',
}


@register.filter
# expand the abbreviated zodiac sign used in chart data.
def full_sign(sign):
    return SIGN_NAMES.get(sign, sign)