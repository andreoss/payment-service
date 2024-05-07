import hmac

from fastapi import Header, HTTPException, status

from app.config import settings


async def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    # Constant-time comparison so a failed attempt cannot leak, via response
    # timing, how many leading characters of the key were correct.
    if x_api_key is None or not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
