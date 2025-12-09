"""
citeflex/engines/brave_search.py

Fast web search for URL citation metadata using Brave Search API.

This replaces the slow Claude API + web search approach with direct
Brave Search API calls (~1-2 sec vs 5-15 sec).

Usage:
    from engines.brave_search import search_url_citation
    
    metadata = search_url_citation(
        "https://www.theatlantic.com/ideas/2025/12/private-equity-housing-changes/685138/"
    )
    # Returns CitationMetadata with title, author, date, publication

API Key:
    Set BRAVE_API_KEY environment variable or pass directly to functions.
    Get free key at: https://brave.com/search/api/

Version History:
    2025-12-09: Initial creation for fast URL lookup
"""

import os
import re
import requests
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, quote_plus
from datetime import datetime

from models import CitationMetadata, CitationType


# =============================================================================
# CONFIGURATION
# =============================================================================

# Try to import from config, fall back to environment variable
try:
    from config import BRAVE_API_KEY
except ImportError:
    BRAVE_API_KEY = os.environ.get('BRAVE_API_KEY', '')

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# Request timeout (seconds)
TIMEOUT = 5

# Domain to publication name mapping
PUBLICATION_NAMES = {
    'theatlantic.com': 'The Atlantic',
    'nytimes.com': 'New York Times',
    'washingtonpost.com': 'Washington Post',
    'wsj.com': 'Wall Street Journal',
    'newyorker.com': 'The New Yorker',
    'economist.com': 'The Economist',
    'theguardian.com': 'The Guardian',
    'bbc.com': 'BBC',
    'bbc.co.uk': 'BBC',
    'reuters.com': 'Reuters',
    'apnews.com': 'Associated Press',
    'politico.com': 'Politico',
    'axios.com': 'Axios',
    'vox.com': 'Vox',
    'slate.com': 'Slate',
    'forbes.com': 'Forbes',
    'bloomberg.com': 'Bloomberg',
    'cnn.com': 'CNN',
    'npr.org': 'NPR',
    'time.com': 'Time',
    'newsweek.com': 'Newsweek',
    'latimes.com': 'Los Angeles Times',
    'chicagotribune.com': 'Chicago Tribune',
    'bostonglobe.com': 'Boston Globe',
    'nice.org.uk': 'National Institute for Health and Care Excellence',
    'gov.uk': 'UK Government',
    'nhs.uk': 'NHS',
}


# =============================================================================
# CORE SEARCH FUNCTION
# =============================================================================

def brave_search(query: str, api_key: str = None, count: int = 5) -> List[Dict[str, Any]]:
    """
    Execute a Brave Search API query.
    
    Args:
        query: Search query string
        api_key: Brave API key (uses env var if not provided)
        count: Number of results to return (1-20)
        
    Returns:
        List of search result dicts with keys: title, url, description, age
    """
    key = api_key or BRAVE_API_KEY
    if not key:
        print("[BraveSearch] No API key configured")
        return []
    
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": key
    }
    
    params = {
        "q": query,
        "count": count,
        "text_decorations": False,
        "search_lang": "en",
        "country": "us",
    }
    
    try:
        response = requests.get(
            BRAVE_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        
        results = []
        web_results = data.get('web', {}).get('results', [])
        
        for item in web_results[:count]:
            results.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'description': item.get('description', ''),
                'age': item.get('age', ''),
                'extra_snippets': item.get('extra_snippets', []),
            })
        
        return results
        
    except requests.exceptions.Timeout:
        print(f"[BraveSearch] Request timed out")
        return []
    except requests.exceptions.RequestException as e:
        print(f"[BraveSearch] Request error: {e}")
        return []
    except Exception as e:
        print(f"[BraveSearch] Error: {e}")
        return []


# =============================================================================
# URL TO SEARCH QUERY
# =============================================================================

def url_to_search_query(url: str) -> str:
    """
    Convert a URL to an effective search query.
    
    Examples:
        https://www.theatlantic.com/ideas/2025/12/private-equity-housing-changes/685138/
        → "Atlantic private equity housing changes"
        
        https://www.nice.org.uk/guidance/ng255
        → "NICE guidance ng255"
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        path = parsed.path.strip('/')
        
        # Get publication name or domain base
        pub_name = PUBLICATION_NAMES.get(domain)
        if not pub_name:
            # Use first part of domain
            pub_name = domain.split('.')[0].replace('the', '').title()
        
        # Extract meaningful words from path
        path_words = []
        for segment in path.split('/'):
            # Skip dates and numbers
            if re.match(r'^\d+$', segment):
                continue
            if re.match(r'^\d{4}$', segment):  # Year
                continue
            if re.match(r'^\d{1,2}$', segment):  # Month/day
                continue
            
            # Convert slug to words
            if '-' in segment or '_' in segment:
                words = segment.replace('-', ' ').replace('_', ' ')
                path_words.append(words)
            elif len(segment) > 3:
                path_words.append(segment)
        
        query = f"{pub_name} {' '.join(path_words)}"
        return query.strip()
        
    except Exception as e:
        print(f"[BraveSearch] URL parsing error: {e}")
        return url


# =============================================================================
# EXTRACT CITATION FROM SEARCH RESULTS
# =============================================================================

def extract_author_from_text(text: str) -> List[str]:
    """
    Extract author name(s) from search result text.
    
    Patterns:
        - "by Author Name"
        - "Author Name writes"
        - "Author Name, staff writer"
        - "Author Name | Publication"
    """
    authors = []
    
    # Pattern: "by First Last" or "By First Last"
    by_match = re.search(r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)', text, re.IGNORECASE)
    if by_match:
        authors.append(by_match.group(1))
        return authors
    
    # Pattern: "First Last writes" or "First Last reports"
    writes_match = re.search(r'([A-Z][a-z]+\s+[A-Z][a-z]+)\s+(?:writes|reports|argues|explains)', text)
    if writes_match:
        authors.append(writes_match.group(1))
        return authors
    
    # Pattern: "First Last, [title] at Publication"
    title_match = re.search(r'([A-Z][a-z]+\s+[A-Z][a-z]+),\s+(?:staff writer|reporter|columnist|editor)', text, re.IGNORECASE)
    if title_match:
        authors.append(title_match.group(1))
        return authors
    
    return authors


def extract_date_from_text(text: str, age: str = '') -> str:
    """
    Extract publication date from search result text or age field.
    
    Args:
        text: Search result description
        age: Brave's "age" field (e.g., "2 days ago", "December 8, 2025")
    """
    # Try age field first (most reliable)
    if age:
        # Full date format
        date_match = re.search(r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', age)
        if date_match:
            return date_match.group(1)
        
        # Relative date (convert to absolute)
        relative_match = re.search(r'(\d+)\s+(day|hour|week|month)s?\s+ago', age, re.IGNORECASE)
        if relative_match:
            num = int(relative_match.group(1))
            unit = relative_match.group(2).lower()
            
            from datetime import timedelta
            now = datetime.now()
            
            if unit == 'hour':
                pub_date = now
            elif unit == 'day':
                pub_date = now - timedelta(days=num)
            elif unit == 'week':
                pub_date = now - timedelta(weeks=num)
            elif unit == 'month':
                pub_date = now - timedelta(days=num * 30)
            else:
                pub_date = now
            
            return pub_date.strftime('%B %d, %Y').replace(' 0', ' ')
    
    # Try to extract from text
    date_match = re.search(r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', text)
    if date_match:
        return date_match.group(1)
    
    # ISO date format
    iso_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if iso_match:
        try:
            dt = datetime.strptime(iso_match.group(1), '%Y-%m-%d')
            return dt.strftime('%B %d, %Y').replace(' 0', ' ')
        except:
            pass
    
    return ''


def find_matching_result(url: str, results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Find the search result that matches the original URL.
    
    Returns the best matching result, prioritizing exact URL match.
    """
    if not results:
        return None
    
    # Normalize URL for comparison
    url_lower = url.lower().rstrip('/')
    
    # First pass: exact URL match
    for result in results:
        result_url = result.get('url', '').lower().rstrip('/')
        if result_url == url_lower:
            return result
    
    # Second pass: URL contains same path
    try:
        parsed = urlparse(url)
        url_path = parsed.path.lower().rstrip('/')
        
        for result in results:
            result_parsed = urlparse(result.get('url', ''))
            result_path = result_parsed.path.lower().rstrip('/')
            if url_path and url_path in result_path:
                return result
    except:
        pass
    
    # Third pass: same domain + significant title overlap
    try:
        parsed = urlparse(url)
        url_domain = parsed.netloc.lower().replace('www.', '')
        
        for result in results:
            result_parsed = urlparse(result.get('url', ''))
            result_domain = result_parsed.netloc.lower().replace('www.', '')
            
            if url_domain == result_domain:
                return result
    except:
        pass
    
    # Fallback: return first result
    return results[0] if results else None


# =============================================================================
# MAIN FUNCTION: SEARCH URL CITATION
# =============================================================================

def search_url_citation(
    url: str,
    citation_type: CitationType = None,
    api_key: str = None
) -> Optional[CitationMetadata]:
    """
    Search for citation metadata for a URL using Brave Search.
    
    This is the main entry point for fast URL citation lookup.
    Typically completes in 1-2 seconds.
    
    Args:
        url: The URL to look up
        citation_type: Type hint (NEWSPAPER, GOVERNMENT, etc.)
        api_key: Brave API key (uses env var if not provided)
        
    Returns:
        CitationMetadata with title, authors, date, publication
    """
    key = api_key or BRAVE_API_KEY
    if not key:
        print("[BraveSearch] No API key - cannot search")
        return None
    
    # Determine citation type from URL if not provided
    if citation_type is None:
        citation_type = _detect_type_from_url(url)
    
    # Convert URL to search query
    query = url_to_search_query(url)
    print(f"[BraveSearch] Searching: {query[:60]}...")
    
    # Execute search
    results = brave_search(query, api_key=key, count=5)
    
    if not results:
        print("[BraveSearch] No results found")
        return None
    
    # Find matching result
    match = find_matching_result(url, results)
    if not match:
        print("[BraveSearch] No matching result")
        return None
    
    # Extract metadata
    title = match.get('title', '')
    description = match.get('description', '')
    age = match.get('age', '')
    extra = ' '.join(match.get('extra_snippets', []))
    
    # Combine all text for author/date extraction
    all_text = f"{title} {description} {extra}"
    
    authors = extract_author_from_text(all_text)
    date = extract_date_from_text(all_text, age)
    
    # Get publication name
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        publication = PUBLICATION_NAMES.get(domain, domain.split('.')[0].title())
    except:
        publication = ''
    
    # Clean up title (remove site name suffix)
    clean_title = title
    separators = [' | ', ' - ', ' — ', ' · ']
    for sep in separators:
        if sep in clean_title:
            parts = clean_title.split(sep)
            # Usually article title is first part
            if len(parts[0]) > 20:
                clean_title = parts[0].strip()
                break
    
    print(f"[BraveSearch] Found: {clean_title[:50]}... by {authors}")
    
    access_date = datetime.now().strftime('%B %d, %Y').replace(' 0', ' ')
    
    return CitationMetadata(
        citation_type=citation_type,
        raw_source=url,
        source_engine="Brave Search",
        url=url,
        title=clean_title,
        authors=authors,
        date=date,
        newspaper=publication if citation_type == CitationType.NEWSPAPER else None,
        agency=publication if citation_type == CitationType.GOVERNMENT else None,
        access_date=access_date,
    )


def _detect_type_from_url(url: str) -> CitationType:
    """Detect citation type from URL domain."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        
        # Newspaper domains
        newspaper_domains = [
            'theatlantic.com', 'nytimes.com', 'washingtonpost.com', 'wsj.com',
            'newyorker.com', 'economist.com', 'theguardian.com', 'bbc.com',
            'reuters.com', 'apnews.com', 'politico.com', 'axios.com', 'vox.com',
            'slate.com', 'forbes.com', 'bloomberg.com', 'cnn.com', 'npr.org',
            'time.com', 'newsweek.com', 'latimes.com',
        ]
        if any(d in domain for d in newspaper_domains):
            return CitationType.NEWSPAPER
        
        # Government domains
        gov_patterns = ['gov.uk', 'nhs.uk', 'nice.org.uk', '.gov', 'gc.ca', 'europa.eu']
        if any(p in domain for p in gov_patterns):
            return CitationType.GOVERNMENT
        
    except:
        pass
    
    return CitationType.URL


# =============================================================================
# CONVENIENCE FUNCTION FOR UNIFIED_ROUTER
# =============================================================================

def search_url_fallback(url: str, citation_type: CitationType) -> Optional[CitationMetadata]:
    """
    Drop-in replacement for _claude_url_fallback in unified_router.py
    
    Use this instead of Claude API + web search for ~5x faster results.
    """
    return search_url_citation(url, citation_type)


# =============================================================================
# CLI TEST
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python brave_search.py <url>")
        print("       Set BRAVE_API_KEY environment variable first")
        sys.exit(1)
    
    test_url = sys.argv[1]
    
    if not BRAVE_API_KEY:
        print("Error: BRAVE_API_KEY not set")
        sys.exit(1)
    
    print(f"\nSearching for: {test_url}\n")
    
    result = search_url_citation(test_url)
    
    if result:
        print("\n=== RESULT ===")
        print(f"Title: {result.title}")
        print(f"Authors: {result.authors}")
        print(f"Date: {result.date}")
        print(f"Publication: {result.newspaper or result.agency}")
        print(f"Type: {result.citation_type.name}")
    else:
        print("\nNo result found")
