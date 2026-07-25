"""Pure HTTP authority checks for public, loopback-only demo surfaces."""
import ipaddress
from urllib.parse import urlsplit


def loopback_authority(value):
    if not isinstance(value,str) or not value or len(value)>260 or any(c.isspace() for c in value):return False
    try:
        parsed=urlsplit('//'+value)
        if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:return False
        if parsed.port is not None and parsed.port<1:return False
        host=parsed.hostname
        if not host or '%' in host:return False
        if host.casefold()=='localhost':return True
        return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


def loopback_origin(value):
    """An absent Origin is normal for CLI clients and many same-origin GETs."""
    if value is None:return True
    if not isinstance(value,str) or len(value)>280:return False
    try:
        parsed=urlsplit(value)
        return parsed.scheme in ('http','https') and not (parsed.path or parsed.query or parsed.fragment) and loopback_authority(parsed.netloc)
    except ValueError:return False
