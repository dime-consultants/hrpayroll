import re


def normalize_phone(raw: str) -> str:
    """Normalise to E.164 Kenya format (254XXXXXXXXX)."""
    if not raw:
        return ''
    digits = re.sub(r'\D', '', str(raw))
    if digits.startswith('0') and len(digits) == 10:
        digits = '254' + digits[1:]
    elif digits.startswith('7') and len(digits) == 9:
        digits = '254' + digits
    elif digits.startswith('254') and len(digits) == 12:
        pass
    else:
        return ''
    return digits


def validate_phone(phone: str) -> bool:
    return bool(re.fullmatch(r'254[0-9]{9}', phone))
