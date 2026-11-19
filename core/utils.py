import secrets
from django.core.cache import cache
from django.core.exceptions import ValidationError


def generate_idempotency_token():
    return f"idem_{secrets.token_hex(12)}"


def claim_idempotency_token(token: str, scope: str, ttl: int = 600) -> bool:
    """
    Returns True the first time a token is claimed within `scope`,
    and False on any duplicate submission.
    """
    if not token or len(token) > 80:
        return False
    cache_key = f"idem:{scope}:{token}"
    # cache.add is atomic: returns True only if key did not exist
    return cache.add(cache_key, '1', timeout=ttl)


def validate_uploaded_image(file_obj):
    if not file_obj:
        return
    max_bytes = 2 * 1024 * 1024
    if file_obj.size > max_bytes:
        raise ValidationError('Image file size must not exceed 2 MB.')
    valid_types = {'image/jpeg', 'image/png', 'image/webp'}
    content_type = getattr(file_obj, 'content_type', '')
    if content_type and content_type not in valid_types:
        raise ValidationError('Only JPEG, PNG, or WebP images are permitted.')
