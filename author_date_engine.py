"""
citeflex/author_date_engine.py

Search engine for author-date citations.

Given an author surname and year, searches multiple databases to find
the full citation metadata.

Integrates with existing CiteFlex engines:
- Semantic Scholar (strong for psychology/social science)
- Crossref (comprehensive, has DOIs)
- OpenAlex (open access, broad coverage)
- Google Scholar via SERPAPI (fallback)

Created: 2025-12-10
"""

import re
import time
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

from models import CitationMetadata, CitationType


@dataclass
class SearchResult:
    """Result from author-year search with confidence score."""
    metadata: CitationMetadata
    confidence: float  # 0.0 to 1.0
    match_reason: str  # Why we think this is the right match
    
    def __lt__(self, other):
        return self.confidence < other.confidence


class AuthorDateEngine:
    """
    Searches for full citation metadata given author surname and year.
    
    Uses multiple search strategies:
    1. Semantic Scholar author:X year:Y query
    2. Crossref author + year filter
    3. OpenAlex author + year filter  
    4. Google Scholar "author year" query (fallback)
    
    Ranks results by confidence based on:
    - Author name match quality
    - Year exact match
    - Citation count (higher = more likely correct)
    - Has DOI (more reliable)
    """
    
    def __init__(self):
        # Lazy load engines to avoid circular imports
        self._crossref = None
        self._semantic_scholar = None
        self._openalex = None
        self._google_scholar = None
        
    def _get_crossref(self):
        if self._crossref is None:
            try:
                from engines.academic import CrossrefEngine
                self._crossref = CrossrefEngine()
            except ImportError:
                pass
        return self._crossref
    
    def _get_semantic_scholar(self):
        if self._semantic_scholar is None:
            try:
                from engines.academic import SemanticScholarEngine
                self._semantic_scholar = SemanticScholarEngine()
            except ImportError:
                pass
        return self._semantic_scholar
    
    def _get_openalex(self):
        if self._openalex is None:
            try:
                from engines.academic import OpenAlexEngine
                self._openalex = OpenAlexEngine()
            except ImportError:
                pass
        return self._openalex
    
    def _get_google_scholar(self):
        if self._google_scholar is None:
            try:
                from engines.google_scholar import GoogleScholarEngine
                self._google_scholar = GoogleScholarEngine()
            except ImportError:
                pass
        return self._google_scholar
    
    def search(
        self,
        author: str,
        year: str,
        second_author: Optional[str] = None,
        timeout: float = 5.0,  # Reduced from 10s for faster processing
        context: Optional[str] = None  # Document field/context for smarter matching
    ) -> Optional[CitationMetadata]:
        """
        Search for a citation by author and year.
        
        Args:
            author: Primary author surname (e.g., "Bandura")
            year: Publication year (e.g., "1977")
            second_author: Optional second author for two-author citations
            timeout: Maximum time to wait for all searches
            context: Optional context (e.g., "psychology") for smarter matching
            
        Returns:
            Best matching CitationMetadata, or None if not found
        """
        if year == "n.d.":
            # Can't search without a year effectively
            return None
        
        results: List[SearchResult] = []
        
        # Build search queries
        query_simple = f"{author} {year}"
        query_with_second = f"{author} {second_author} {year}" if second_author else None
        
        # Run searches in parallel
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            
            # Semantic Scholar
            ss = self._get_semantic_scholar()
            if ss:
                futures[executor.submit(
                    self._search_semantic_scholar, author, year, second_author
                )] = "semantic_scholar"
            
            # Crossref
            cr = self._get_crossref()
            if cr:
                futures[executor.submit(
                    self._search_crossref, author, year, second_author
                )] = "crossref"
            
            # OpenAlex
            oa = self._get_openalex()
            if oa:
                futures[executor.submit(
                    self._search_openalex, author, year, second_author
                )] = "openalex"
            
            # Google Scholar (if available)
            gs = self._get_google_scholar()
            if gs:
                futures[executor.submit(
                    self._search_google_scholar, author, year, second_author
                )] = "google_scholar"
            
            # Collect results
            for future in as_completed(futures, timeout=timeout):
                try:
                    result = future.result()
                    if result:
                        results.extend(result)
                except Exception as e:
                    source = futures.get(future, "unknown")
                    print(f"[AuthorDateEngine] {source} error: {e}")
        
        # Sort by confidence
        if results:
            results.sort(reverse=True)
            best = results[0]
            
            # If best result is high confidence, return it
            if best.confidence >= 0.6:
                best.metadata.raw_source = f"({author}, {year})"
                return best.metadata
            
            print(f"[AuthorDateEngine] Low confidence ({best.confidence:.2f}), trying Claude...")
        else:
            print(f"[AuthorDateEngine] No API results for {author} ({year}), trying Claude...")
        
        # Fallback: Use Claude for difficult/unfound citations
        claude_results = self._search_claude(author, year, second_author, context)
        if claude_results:
            results.extend(claude_results)
        
        if not results:
            return None
        
        # Sort by confidence (highest first)
        results.sort(reverse=True)
        
        # Return best match
        best = results[0]
        
        # Add search info to metadata
        best.metadata.raw_source = f"({author}, {year})"
        
        return best.metadata
    
    def _search_semantic_scholar(
        self,
        author: str,
        year: str,
        second_author: Optional[str]
    ) -> List[SearchResult]:
        """Search Semantic Scholar."""
        results = []
        ss = self._get_semantic_scholar()
        if not ss:
            return results
        
        try:
            # Semantic Scholar query format
            query = f"author:{author} year:{year}"
            metadata = ss.search(query)
            
            if metadata and metadata.title:
                confidence = self._calculate_confidence(metadata, author, year, second_author)
                results.append(SearchResult(
                    metadata=metadata,
                    confidence=confidence,
                    match_reason="Semantic Scholar author+year match"
                ))
        except Exception as e:
            print(f"[AuthorDateEngine] Semantic Scholar error: {e}")
        
        return results
    
    def _search_crossref(
        self,
        author: str,
        year: str,
        second_author: Optional[str]
    ) -> List[SearchResult]:
        """Search Crossref."""
        results = []
        cr = self._get_crossref()
        if not cr:
            return results
        
        try:
            # Crossref query - author name plus year
            query = f"{author} {year}"
            if second_author:
                query = f"{author} {second_author} {year}"
            
            metadata = cr.search(query)
            
            if metadata and metadata.title:
                # Verify year matches
                if metadata.year and metadata.year == year:
                    confidence = self._calculate_confidence(metadata, author, year, second_author)
                    # Boost confidence if has DOI
                    if metadata.doi:
                        confidence = min(1.0, confidence + 0.1)
                    results.append(SearchResult(
                        metadata=metadata,
                        confidence=confidence,
                        match_reason="Crossref author+year match"
                    ))
        except Exception as e:
            print(f"[AuthorDateEngine] Crossref error: {e}")
        
        return results
    
    def _search_openalex(
        self,
        author: str,
        year: str,
        second_author: Optional[str]
    ) -> List[SearchResult]:
        """Search OpenAlex."""
        results = []
        oa = self._get_openalex()
        if not oa:
            return results
        
        try:
            query = f"{author} {year}"
            if second_author:
                query = f"{author} {second_author} {year}"
            
            metadata = oa.search(query)
            
            if metadata and metadata.title:
                if metadata.year and metadata.year == year:
                    confidence = self._calculate_confidence(metadata, author, year, second_author)
                    results.append(SearchResult(
                        metadata=metadata,
                        confidence=confidence,
                        match_reason="OpenAlex author+year match"
                    ))
        except Exception as e:
            print(f"[AuthorDateEngine] OpenAlex error: {e}")
        
        return results
    
    def _search_google_scholar(
        self,
        author: str,
        year: str,
        second_author: Optional[str]
    ) -> List[SearchResult]:
        """Search Google Scholar via SERPAPI."""
        results = []
        gs = self._get_google_scholar()
        if not gs:
            return results
        
        try:
            query = f"author:{author} {year}"
            if second_author:
                query = f"author:{author} author:{second_author} {year}"
            
            metadata = gs.search(query)
            
            if metadata and metadata.title:
                confidence = self._calculate_confidence(metadata, author, year, second_author)
                # Google Scholar often lacks DOI, slight penalty
                if not metadata.doi:
                    confidence = max(0.0, confidence - 0.05)
                results.append(SearchResult(
                    metadata=metadata,
                    confidence=confidence,
                    match_reason="Google Scholar author+year match"
                ))
        except Exception as e:
            print(f"[AuthorDateEngine] Google Scholar error: {e}")
        
        return results
    
    def _search_claude(
        self,
        author: str,
        year: str,
        second_author: Optional[str],
        context: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Use Claude as intelligent fallback for difficult citations.
        
        Claude can:
        - Understand context (field of study)
        - Use its training knowledge to identify works
        - Provide structured citation data
        """
        results = []
        
        try:
            from claude_router import guess_citation
            
            # Build query with context
            if second_author:
                query = f"{author} & {second_author} ({year})"
            else:
                query = f"{author} ({year})"
            
            # Add context hint if provided
            if context:
                query = f"{query}\n\nContext: This citation appears in a document about {context}."
            
            print(f"[AuthorDateEngine] Trying Claude for: {author} ({year})")
            
            guess = guess_citation(query)
            
            if guess.get('confidence', 0) < 0.5:
                print(f"[AuthorDateEngine] Claude low confidence: {guess.get('confidence', 0)}")
                return results
            
            # Build CitationMetadata from Claude's guess
            from models import CitationType
            
            type_map = {
                'journal': CitationType.JOURNAL,
                'book': CitationType.BOOK,
                'newspaper': CitationType.NEWSPAPER,
                'medical': CitationType.MEDICAL,
            }
            
            metadata = CitationMetadata(
                citation_type=type_map.get(guess.get('type', 'journal'), CitationType.JOURNAL),
                title=guess.get('title', ''),
                authors=guess.get('authors', []),
                year=guess.get('year', year),
                journal=guess.get('journal', ''),
                volume=guess.get('volume', ''),
                issue=guess.get('issue', ''),
                pages=guess.get('pages', ''),
                publisher=guess.get('publisher', ''),
                doi=guess.get('doi', ''),
                source_engine="Claude AI"
            )
            
            # Verify author name appears in result
            author_lower = author.lower()
            authors_match = any(author_lower in a.lower() for a in metadata.authors) if metadata.authors else False
            
            if metadata.title and authors_match:
                confidence = guess.get('confidence', 0.7)
                # Claude results get a small boost since they're contextual
                results.append(SearchResult(
                    metadata=metadata,
                    confidence=min(0.95, confidence + 0.1),
                    match_reason="Claude AI contextual match"
                ))
                print(f"[AuthorDateEngine] Claude found: {metadata.title[:50]}...")
            else:
                print(f"[AuthorDateEngine] Claude result didn't match author: {metadata.authors}")
                
        except ImportError:
            print("[AuthorDateEngine] Claude router not available")
        except Exception as e:
            print(f"[AuthorDateEngine] Claude error: {e}")
        
        return results
    
    def _calculate_confidence(
        self,
        metadata: CitationMetadata,
        author: str,
        year: str,
        second_author: Optional[str]
    ) -> float:
        """
        Calculate confidence score for a match.
        
        Factors:
        - Author name appears in authors list
        - Year matches exactly
        - Second author matches (if provided)
        - Has DOI (more reliable)
        - Has complete metadata
        """
        confidence = 0.0
        
        # Year match (required)
        if metadata.year == year:
            confidence += 0.3
        elif metadata.year:
            # Close year (off by 1) - might be publication vs. online date
            try:
                if abs(int(metadata.year) - int(year)) <= 1:
                    confidence += 0.2
            except ValueError:
                pass
        
        # Author match
        author_lower = author.lower()
        if metadata.authors:
            authors_lower = [a.lower() for a in metadata.authors]
            
            # Check if author surname appears in any author name
            for a in authors_lower:
                if author_lower in a:
                    confidence += 0.3
                    break
            
            # Check second author if provided
            if second_author:
                second_lower = second_author.lower()
                for a in authors_lower:
                    if second_lower in a:
                        confidence += 0.15
                        break
        
        # DOI presence (reliable identifier)
        if metadata.doi:
            confidence += 0.15
        
        # Complete metadata bonus
        completeness = 0
        if metadata.title:
            completeness += 1
        if metadata.journal or metadata.publisher:
            completeness += 1
        if metadata.volume or metadata.pages:
            completeness += 1
        
        confidence += completeness * 0.05
        
        return min(1.0, confidence)
    
    def search_multiple(
        self,
        citations: List[Tuple[str, str, Optional[str]]],
        progress_callback=None
    ) -> Dict[Tuple[str, str], Optional[CitationMetadata]]:
        """
        Search for multiple citations.
        
        Args:
            citations: List of (author, year, second_author) tuples
            progress_callback: Optional callback(current, total) for progress
            
        Returns:
            Dict mapping (author, year) to CitationMetadata (or None if not found)
        """
        results = {}
        total = len(citations)
        
        for i, (author, year, second_author) in enumerate(citations):
            if progress_callback:
                progress_callback(i + 1, total)
            
            key = (author.lower(), year)
            
            try:
                metadata = self.search(author, year, second_author)
                results[key] = metadata
            except Exception as e:
                print(f"[AuthorDateEngine] Error searching {author}, {year}: {e}")
                results[key] = None
            
            # Small delay to avoid rate limiting
            if i < total - 1:
                time.sleep(0.5)
        
        return results


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_engine = None

def get_engine() -> AuthorDateEngine:
    """Get singleton engine instance."""
    global _engine
    if _engine is None:
        _engine = AuthorDateEngine()
    return _engine


def search_author_year(
    author: str,
    year: str,
    second_author: Optional[str] = None
) -> Optional[CitationMetadata]:
    """
    Convenience function to search by author and year.
    
    Args:
        author: Author surname
        year: Publication year
        second_author: Optional second author
        
    Returns:
        CitationMetadata if found, None otherwise
    """
    return get_engine().search(author, year, second_author)


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("AUTHOR-DATE ENGINE TEST")
    print("=" * 60)
    
    engine = AuthorDateEngine()
    
    # Test citations
    test_cases = [
        ("Bandura", "1977", None),
        ("Kahneman", "1979", "Tversky"),
        ("Diener", "2014", None),
        ("Seligman", "2011", None),
    ]
    
    for author, year, second in test_cases:
        print(f"\nSearching: {author}, {year}" + (f", {second}" if second else ""))
        print("-" * 40)
        
        result = engine.search(author, year, second)
        
        if result:
            print(f"  Title: {result.title[:60]}..." if len(result.title) > 60 else f"  Title: {result.title}")
            print(f"  Authors: {', '.join(result.authors[:3])}" + ("..." if len(result.authors) > 3 else ""))
            print(f"  Year: {result.year}")
            print(f"  Journal: {result.journal or 'N/A'}")
            print(f"  DOI: {result.doi or 'N/A'}")
            print(f"  Source: {result.source_engine}")
        else:
            print("  NOT FOUND")
