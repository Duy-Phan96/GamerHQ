"""Public HTTPS link policy shared by managed buttons and curated deals.

Validation only: never fetches URLs or rewrites affiliate/referral parameters.
"""
import re
from urllib.parse import parse_qsl, urlsplit

from services.server_service import ServerMessageError


def validate_url(url):
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname.encode('idna').decode() if parsed.hostname else ''
        valid_host = bool(re.fullmatch(r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', hostname))
        valid = (len(url) <= 512 and parsed.scheme == 'https' and parsed.hostname and '.' in parsed.hostname
                 and valid_host and not parsed.username and not parsed.password and not re.search(r'[\s\\<>]', url))
        parsed.port
        sensitive = re.compile(r'token|secret|password|credential|authorization|api.?key|signature', re.I)
        if not valid or any(sensitive.search(key) for key, _ in parse_qsl(parsed.query) + parse_qsl(parsed.fragment)):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise ServerMessageError('Use a valid public HTTPS URL (maximum 512 characters), without credentials or secret query parameters.') from None
