"""Runnable package 5 composition using package 1's public extension point.

uvicorn app.documents.application:create_app --factory --workers 1
Matvey can instead pass router to his own create_app(feature_routers={5: router}).
"""

from app.common.config import Settings
from app.main import create_app as create_backend
from .router import router


def create_app(settings: Settings | None = None, *, extractor=None, rebuilder=None):
    return create_backend(settings, feature_routers={5: router},
                          document_extractor=extractor, case_rebuilder=rebuilder)
