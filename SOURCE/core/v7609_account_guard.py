"""0311 identity checks. Never store an account number or token on disk."""
import re


def base_account(value):
    text = str(value or "").strip().replace("-", "")
    return text[:8] if re.fullmatch(r"[0-9]{10}", text) else ""


def allowed_account(value):
    return bool(base_account(value) and base_account(value).endswith("0311"))


def selected_account_matches(texts, verified_account):
    """Only explicit, unmasked selected account numbers; no title/coordinate guess."""
    expected = base_account(verified_account)
    if not expected or not allowed_account(verified_account):
        return False
    seen = set()
    for text in texts:
        for match in re.finditer(r"(?<![0-9])([0-9]{4}-?[0-9]{4}(?:-?[0-9]{2})?)(?![0-9])", str(text)):
            digits = match.group(1).replace("-", "")
            seen.add(digits[:8])
    return seen == {expected}
