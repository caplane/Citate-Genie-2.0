"""
citeflex/engines/__init__.py
Search engines package.

Updated: 2025-12-08 - Added URL routing and specialized URL engines
"""
from engines.base import SearchEngine, MultiAttemptEngine
from engines.academic import (
    CrossrefEngine,
    OpenAlexEngine,
    SemanticScholarEngine,
    PubMedEngine,
)
from engines.google_scholar import GoogleScholarEngine
from engines.google_cse import (
    GoogleCSEEngine,
    GoogleBooksEngine,
    OpenLibraryEngine,
)
from engines.doi import (
    extract_doi_from_url,
    is_academic_publisher_url,
    fetch_crossref_by_doi,
    ACADEMIC_PUBLISHER_DOMAINS,
)

# URL Routing and Extraction Engines (NEW)
from engines.url_router import (
    URLRouter,
    URLType,
    classify_url,
    route_url,
    get_url_type,
    extract_doi_from_url as url_extract_doi,
    extract_arxiv_id,
    extract_pmid_from_url,
    extract_wikipedia_title,
    extract_youtube_id,
    extract_jstor_id,
    extract_ssrn_id,
)
from engines.generic_url_engine import (
    GenericURLEngine,
    NewspaperEngine,
    GovernmentEngine,
)
from engines.arxiv_engine import ArxivEngine
from engines.wikipedia_engine import WikipediaEngine, WikipediaSearchEngine
from engines.youtube_engine import YouTubeEngine, VimeoEngine

__all__ = [
    # Base
    'SearchEngine',
    'MultiAttemptEngine',
    # Academic
    'CrossrefEngine',
    'OpenAlexEngine', 
    'SemanticScholarEngine',
    'PubMedEngine',
    # Google Scholar
    'GoogleScholarEngine',
    # Google/Books
    'GoogleCSEEngine',
    'GoogleBooksEngine',
    'OpenLibraryEngine',
    # DOI
    'extract_doi_from_url',
    'is_academic_publisher_url',
    'fetch_crossref_by_doi',
    'ACADEMIC_PUBLISHER_DOMAINS',
    # URL Routing (NEW)
    'URLRouter',
    'URLType',
    'classify_url',
    'route_url',
    'get_url_type',
    'extract_arxiv_id',
    'extract_pmid_from_url',
    'extract_wikipedia_title',
    'extract_youtube_id',
    'extract_jstor_id',
    'extract_ssrn_id',
    # URL Engines (NEW)
    'GenericURLEngine',
    'NewspaperEngine',
    'GovernmentEngine',
    'ArxivEngine',
    'WikipediaEngine',
    'WikipediaSearchEngine',
    'YouTubeEngine',
    'VimeoEngine',
]
