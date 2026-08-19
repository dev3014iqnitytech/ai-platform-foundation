"""
Content Filter — Azure AI Content Safety integration.
Screens both inputs and LLM outputs for harmful content categories.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from structlog import get_logger

logger = get_logger(__name__)


class ContentCategory(str, Enum):
    HATE = "Hate"
    SELF_HARM = "SelfHarm"
    SEXUAL = "Sexual"
    VIOLENCE = "Violence"


@dataclass
class ContentFilterResult:
    is_safe: bool
    categories: dict[str, int]   # category → severity (0–6)
    blocked_categories: list[str]
    filtered_text: str | None = None

    def to_dict(self) -> dict:
        return {
            "is_safe": self.is_safe,
            "categories": self.categories,
            "blocked_categories": self.blocked_categories,
        }


class ContentFilter:
    """
    Azure AI Content Safety wrapper.
    Falls back to a permissive pass-through when the service is unavailable
    (e.g., local development without credentials) to avoid blocking dev workflows.
    """

    SEVERITY_THRESHOLD = 4  # Block severity >= 4 (medium-high)

    def __init__(self, endpoint: str | None = None, api_key: str | None = None):
        self.endpoint = endpoint
        self.api_key = api_key
        self._client = None
        self._available = False
        self._try_init()

    def _try_init(self) -> None:
        if not self.endpoint or not self.api_key:
            logger.warning("content_filter_unavailable", reason="Missing endpoint/key — passthrough mode")
            return
        try:
            from azure.ai.contentsafety import ContentSafetyClient
            from azure.core.credentials import AzureKeyCredential
            self._client = ContentSafetyClient(
                endpoint=self.endpoint,
                credential=AzureKeyCredential(self.api_key),
            )
            self._available = True
            logger.info("content_filter_initialized", endpoint=self.endpoint)
        except ImportError:
            logger.warning("content_filter_unavailable", reason="azure-ai-contentsafety not installed")
        except Exception as e:
            logger.warning("content_filter_init_failed", error=str(e))

    async def check_text(self, text: str) -> ContentFilterResult:
        """Screen text for harmful content. Returns safe=True if service is unavailable."""
        if not self._available or not self._client:
            return ContentFilterResult(
                is_safe=True,
                categories={},
                blocked_categories=[],
                filtered_text=text,
            )

        try:
            from azure.ai.contentsafety.models import AnalyzeTextOptions
            request = AnalyzeTextOptions(text=text[:10_000])  # API limit
            response = self._client.analyze_text(request)

            categories: dict[str, int] = {}
            blocked: list[str] = []

            for item in response.categories_analysis:
                severity = item.severity or 0
                categories[item.category] = severity
                if severity >= self.SEVERITY_THRESHOLD:
                    blocked.append(item.category)

            is_safe = len(blocked) == 0

            if not is_safe:
                logger.warning(
                    "content_blocked",
                    blocked_categories=blocked,
                    text_preview=text[:100],
                )

            return ContentFilterResult(
                is_safe=is_safe,
                categories=categories,
                blocked_categories=blocked,
                filtered_text=text if is_safe else None,
            )

        except Exception as e:
            logger.error("content_filter_check_failed", error=str(e))
            # Fail-open: don't block on service errors
            return ContentFilterResult(
                is_safe=True,
                categories={},
                blocked_categories=[],
                filtered_text=text,
            )

    async def screen_test_cases(self, test_cases: list[dict]) -> list[dict]:
        """Filter a batch of test cases, removing any with unsafe content."""
        safe_cases = []
        for tc in test_cases:
            combined = f"{tc.get('title', '')} {tc.get('description', '')} {tc.get('gherkin_text', '')}"
            result = await self.check_text(combined)
            if result.is_safe:
                safe_cases.append(tc)
            else:
                logger.warning("test_case_filtered", title=tc.get("title", ""), blocked=result.blocked_categories)
        return safe_cases


# Module-level singleton
_filter: ContentFilter | None = None


def get_content_filter() -> ContentFilter:
    global _filter
    if _filter is None:
        try:
            from app.core.config import settings
            _filter = ContentFilter(
                endpoint=getattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", None),
                api_key=getattr(settings, "AZURE_CONTENT_SAFETY_KEY", None),
            )
        except Exception:
            _filter = ContentFilter()
    return _filter
