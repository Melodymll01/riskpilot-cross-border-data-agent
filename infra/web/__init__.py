"""WebSearchPort 适配器。"""

from infra.web.duckduckgo import DuckDuckGoAdapter
from infra.web.safe_http import SafeHttpClient, SafeHttpResponse, validate_public_url

__all__ = [
    "DuckDuckGoAdapter",
    "SafeHttpClient",
    "SafeHttpResponse",
    "validate_public_url",
]
