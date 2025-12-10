"""
citeflex/author_date_extractor.py

Generic extractor for author-date citation styles (APA, Harvard, Chicago Author-Date, etc.)

Extracts in-text citations like:
    (Smith, 2020)
    (Smith & Jones, 2020)
    (Smith et al., 2020)
    (Smith, 2020; Jones, 2021)
    (Smith, 2020, p. 45)
    Smith (2020) argues...

Returns unique (author, year) pairs for lookup.

Created: 2025-12-10
"""

import re
from typing import List, Tuple, Set, Optional, NamedTuple
from dataclasses import dataclass


@dataclass
class AuthorYearCitation:
    """A single extracted author-year citation."""
    author: str          # Primary author surname (e.g., "Smith", "Bandura")
    year: str            # Publication year (e.g., "2020", "n.d.")
    is_et_al: bool = False  # True if "et al." was present
    second_author: Optional[str] = None  # For two-author citations
    page: Optional[str] = None  # Page number if present
    raw_text: str = ""   # Original matched text
    
    def search_key(self) -> Tuple[str, str]:
        """Return (author, year) tuple for deduplication and search."""
        return (self.author.lower().strip(), self.year.strip())
    
    def __hash__(self):
        return hash(self.search_key())
    
    def __eq__(self, other):
        if isinstance(other, AuthorYearCitation):
            return self.search_key() == other.search_key()
        return False


class AuthorDateExtractor:
    """
    Extracts author-date citations from document text.
    
    Supports patterns used by:
    - APA (7th ed.)
    - Harvard
    - Chicago Author-Date
    - ASA (Sociology)
    - AAA (Anthropology)
    - Turabian Author-Date
    """
    
    # Pattern components
    # Author name: Capitalized word, may include hyphenated names, apostrophes, Unicode
    # Simplified to be more reliable - complex surnames handled via pre-processing if needed
    AUTHOR_NAME = r"[A-Z\u00C0-\u024F][a-zA-Z\u00C0-\u024F'''\-]+"
    
    # Year: 4 digits, optional letter suffix, "n.d.", or "in press"
    YEAR = r"(?:\d{4}[a-z]?|n\.d\.|in\s+press)"
    
    # Page indicators - captures the page number
    PAGE = r"(?:pp?\.\s*(\d+(?:\s*[-–—]\s*\d+)?))"
    
    # "et al." variations
    ET_AL = r"et\s+al\.?"
    
    # Patterns for different citation formats
    PATTERNS = [
        # (Author et al., Year) or (Author et al., Year, p. 45)
        re.compile(
            rf'\(({AUTHOR_NAME})\s+{ET_AL},?\s*({YEAR})(?:,?\s*{PAGE})?\)',
            re.UNICODE
        ),
        
        # (Author & Author, Year) or (Author and Author, Year)
        re.compile(
            rf'\(({AUTHOR_NAME})\s*(?:&|and)\s*({AUTHOR_NAME}),?\s*({YEAR})(?:,?\s*{PAGE})?\)',
            re.UNICODE
        ),
        
        # (Author, Year) - simple single author - COMMA REQUIRED
        re.compile(
            rf'\(({AUTHOR_NAME})\s*,\s*({YEAR})(?:,?\s*{PAGE})?\)',
            re.UNICODE
        ),
        
        # Author (Year, p. X) - narrative citation with page
        re.compile(
            rf'({AUTHOR_NAME})\s+\(({YEAR}),\s*{PAGE}\)',
            re.UNICODE
        ),
        
        # Author (Year) - narrative citation simple
        re.compile(
            rf'({AUTHOR_NAME})\s+\(({YEAR})\)',
            re.UNICODE
        ),
        
        # Author et al. (Year) - narrative with et al.
        re.compile(
            rf'({AUTHOR_NAME})\s+{ET_AL}\s*\(({YEAR})\)',
            re.UNICODE
        ),
        
        # Author and Author (Year) - narrative two authors
        re.compile(
            rf'({AUTHOR_NAME})\s+(?:&|and)\s+({AUTHOR_NAME})\s*\(({YEAR})\)',
            re.UNICODE
        ),
        
        # [PATTERN 7] Author, Author, and Author (Year) - narrative 3+ authors
        # Captures: FirstAuthor, (MiddleAuthors,)* and LastAuthor (Year)
        # Group 1 = first author, Group 2 = year
        re.compile(
            rf'({AUTHOR_NAME})(?:\s*,\s*{AUTHOR_NAME})+\s*,?\s*and\s+{AUTHOR_NAME}\s*\(({YEAR})\)',
            re.UNICODE
        ),
        
        # [PATTERN 8] Author's (Year) - possessive narrative form
        # e.g., "Simonton's (1988) observation"
        # Note: Uses explicit Unicode for right single quote (\u2019) which Word documents use
        re.compile(
            rf"({AUTHOR_NAME})['\u2019]s\s*\(({YEAR})\)",
            re.UNICODE
        ),
        
        # [PATTERN 9] (Author, Author, & Author, Year) - parenthetical 3+ authors WITHOUT et al.
        # e.g., "(Griggs, Jackson, Christopher, & Marek, 1999)"
        re.compile(
            rf'\(({AUTHOR_NAME})(?:\s*,\s*{AUTHOR_NAME})+\s*,?\s*(?:&|and)\s*{AUTHOR_NAME}\s*,\s*({YEAR})(?:,?\s*{PAGE})?\)',
            re.UNICODE
        ),
        
        # [PATTERN 10] (Author, Year, Year, Year) - multi-year same author
        # e.g., "(Simonton, 1992, 2000, 2002)"
        # Captures author and all years
        re.compile(
            rf'\(({AUTHOR_NAME})\s*,\s*({YEAR}(?:\s*,\s*{YEAR})+)\)',
            re.UNICODE
        ),
    ]
    
    # Pattern for multiple citations in one parenthetical: (Smith, 2020; Jones, 2021)
    MULTI_CITATION = re.compile(
        r'\(([^)]+;\s*[^)]+)\)',
        re.UNICODE
    )
    
    # Simpler pattern for individual citations within multi-citation
    # Used after splitting by semicolon
    SIMPLE_AUTHOR = r"([A-Z\u00C0-\u024F][a-zA-Z\u00C0-\u024F'''\-]+(?:\s+et\s+al\.?)?(?:\s*(?:&|and)\s*[A-Z\u00C0-\u024F][a-zA-Z\u00C0-\u024F'''\-]+)?)"
    SIMPLE_YEAR = r"(\d{4}[a-z]?|n\.d\.|in\s+press)"
    SINGLE_IN_MULTI = re.compile(
        rf'{SIMPLE_AUTHOR}\s*,?\s*{SIMPLE_YEAR}',
        re.UNICODE
    )
    
    def __init__(self):
        self.citations: List[AuthorYearCitation] = []
    
    def extract_from_text(self, text: str) -> List[AuthorYearCitation]:
        """
        Extract all author-date citations from text.
        
        Args:
            text: Document body text
            
        Returns:
            List of AuthorYearCitation objects (may contain duplicates)
        """
        if not text:
            return []
        
        citations = []
        found_spans = set()  # Track character spans to avoid duplicates
        found_keys = set()   # Track (author, year) to avoid dupes in multi-citations
        
        def add_if_new(citation, start, end, use_span=True):
            """Add citation only if not already matched."""
            key = (citation.author.lower(), citation.year)
            
            if use_span:
                span_key = (start, end)
                # Check for overlapping spans
                for existing_start, existing_end in found_spans:
                    if start < existing_end and end > existing_start:
                        return False  # Overlapping
                found_spans.add(span_key)
            else:
                # For multi-citations, just check the key
                if key in found_keys:
                    return False
            
            found_keys.add(key)
            citations.append(citation)
            return True
        
        # First, handle multi-citations like (Smith, 2020; Jones, 2021)
        # Also handles complex citations like (Annin, Boring, & Watson, 1968; Endler, 1987)
        for match in self.MULTI_CITATION.finditer(text):
            inner = match.group(1)
            # Check if it actually contains multiple citations (has semicolon)
            if ';' in inner:
                # Mark this span as used to prevent single-citation patterns from re-matching
                found_spans.add((match.start(), match.end()))
                
                # Split by semicolon and process each citation segment
                segments = inner.split(';')
                for segment in segments:
                    segment = segment.strip()
                    if not segment:
                        continue
                    
                    # Returns a list (may have multiple for multi-year)
                    parsed_citations = self._parse_multi_author_segment(segment, match.group(0))
                    for citation in parsed_citations:
                        add_if_new(citation, match.start(), match.end(), use_span=False)
        
        # Pattern 6: Author and Author (Year) - narrative two authors (check FIRST to avoid overlap)
        for match in self.PATTERNS[6].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(3),
                is_et_al=False,
                second_author=match.group(2),
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 5: Author et al. (Year) - narrative (check before simple narrative)
        for match in self.PATTERNS[5].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(2),
                is_et_al=True,
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 0: (Author et al., Year)
        for match in self.PATTERNS[0].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(2),
                is_et_al=True,
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 1: (Author & Author, Year)
        for match in self.PATTERNS[1].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(3),
                is_et_al=False,
                second_author=match.group(2),
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 2: (Author, Year) - with REQUIRED comma
        for match in self.PATTERNS[2].finditer(text):
            raw = match.group(0)
            if 'et al' in raw.lower() or '&' in raw or ' and ' in raw.lower():
                continue
            page = match.group(3) if match.lastindex >= 3 else None
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(2),
                is_et_al=False,
                page=page,
                raw_text=raw
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 3: Author (Year, p. X) - narrative with page
        for match in self.PATTERNS[3].finditer(text):
            page = match.group(3) if match.lastindex >= 3 else None
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(2),
                is_et_al=False,
                page=page,
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 7: Author, Author, and Author (Year) - narrative 3+ authors
        # Must be checked BEFORE Pattern 4 to catch full multi-author citations
        for match in self.PATTERNS[7].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),  # First author
                year=match.group(2),
                is_et_al=True,  # 3+ authors = et al.
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 8: Author's (Year) - possessive narrative form
        for match in self.PATTERNS[8].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(2),
                is_et_al=False,
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 9: (Author, Author, & Author, Year) - parenthetical 3+ authors
        for match in self.PATTERNS[9].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),  # First author
                year=match.group(2),
                is_et_al=True,  # 3+ authors = et al.
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        # Pattern 10: (Author, Year, Year, Year) - multi-year same author
        for match in self.PATTERNS[10].finditer(text):
            author = match.group(1)
            years_str = match.group(2)
            # Extract all years from the comma-separated list
            years = re.findall(r'\d{4}[a-z]?|n\.d\.|in\s+press', years_str)
            for year in years:
                citation = AuthorYearCitation(
                    author=author,
                    year=year,
                    is_et_al=False,
                    raw_text=match.group(0)
                )
                add_if_new(citation, match.start(), match.end(), use_span=False)
        
        # Pattern 4: Author (Year) - simple narrative (check LAST)
        for match in self.PATTERNS[4].finditer(text):
            citation = AuthorYearCitation(
                author=match.group(1),
                year=match.group(2),
                is_et_al=False,
                raw_text=match.group(0)
            )
            add_if_new(citation, match.start(), match.end())
        
        self.citations = citations
        return citations
    
    def _parse_author_part(self, author_part: str, year: str, raw: str) -> Optional[AuthorYearCitation]:
        """Parse author portion of a citation."""
        author_part = author_part.strip()
        
        # Check for et al.
        is_et_al = bool(re.search(r'et\s+al\.?', author_part, re.IGNORECASE))
        if is_et_al:
            author_part = re.sub(r'\s*et\s+al\.?', '', author_part, flags=re.IGNORECASE)
        
        # Check for two authors
        second_author = None
        two_author_match = re.match(
            rf'({self.AUTHOR_NAME})\s*(?:&|and)\s*({self.AUTHOR_NAME})',
            author_part,
            re.IGNORECASE
        )
        if two_author_match:
            author = two_author_match.group(1)
            second_author = two_author_match.group(2)
        else:
            author = author_part.strip()
        
        if not author:
            return None
        
        return AuthorYearCitation(
            author=author,
            year=year,
            is_et_al=is_et_al,
            second_author=second_author,
            raw_text=raw
        )
    
    def _parse_multi_author_segment(self, segment: str, raw: str) -> List[AuthorYearCitation]:
        """
        Parse a citation segment from a multi-citation parenthetical.
        
        Handles formats like:
        - "Smith, 2020"
        - "Smith & Jones, 2020"  
        - "Annin, Boring, & Watson, 1968" → Annin et al. (1968)
        - "Smith et al., 2020"
        - "Simonton, 1992, 2000, 2002" → multiple citations (multi-year)
        - "see Smith, 2020" (strips leading "see")
        
        Key: Extract the FIRST author name, not the last.
        
        Args:
            segment: A single citation like "Annin, Boring, & Watson, 1968"
            raw: Full raw text of the parenthetical
            
        Returns:
            List of AuthorYearCitation (usually 1, but multiple for multi-year)
        """
        segment = segment.strip()
        
        # Remove leading words like "see", "e.g.", "cf."
        segment = re.sub(r'^(?:see|e\.g\.|cf\.?|also)\s+', '', segment, flags=re.IGNORECASE)
        # Also remove trailing noise like "for more recent reviews"
        segment = re.sub(r'\s+for\s+.*$', '', segment, flags=re.IGNORECASE)
        
        if not segment:
            return []
        
        # First, detect multi-year same-author pattern: "Author, Year, Year, Year"
        # Pattern: starts with author name, followed by comma-separated years only
        multi_year_match = re.match(
            rf'^({self.AUTHOR_NAME})\s*,\s*((?:\d{{4}}[a-z]?(?:\s*,\s*)?)+)\s*$',
            segment
        )
        if multi_year_match:
            author = multi_year_match.group(1)
            years_str = multi_year_match.group(2)
            years = re.findall(r'\d{4}[a-z]?', years_str)
            if len(years) > 1:
                # Multi-year citation - return multiple citations
                return [
                    AuthorYearCitation(
                        author=author,
                        year=year,
                        is_et_al=False,
                        raw_text=raw
                    )
                    for year in years
                ]
        
        # Extract year (look for 4-digit year or special patterns at end)
        year_match = re.search(r',?\s*(\d{4}[a-z]?|n\.d\.|in\s+press)\s*$', segment)
        if not year_match:
            return []
        
        year = year_match.group(1)
        # Remove year from segment to get author part
        author_part = segment[:year_match.start()].strip().rstrip(',').strip()
        
        if not author_part:
            return []
        
        # Check for explicit "et al."
        is_et_al = bool(re.search(r'et\s+al\.?', author_part, re.IGNORECASE))
        if is_et_al:
            # Extract first author before "et al."
            et_al_match = re.match(rf'({self.AUTHOR_NAME})\s+et\s+al\.?', author_part, re.IGNORECASE)
            if et_al_match:
                return [AuthorYearCitation(
                    author=et_al_match.group(1),
                    year=year,
                    is_et_al=True,
                    raw_text=raw
                )]
        
        # Check for "&" or "and" indicating multiple authors
        # Pattern: "Author1, Author2, & Author3" or "Author1 & Author2"
        if '&' in author_part or re.search(r'\band\b', author_part, re.IGNORECASE):
            # Count authors by looking for capitalized names
            # Split by comma and &/and
            parts = re.split(r'\s*(?:,|&|\band\b)\s*', author_part)
            # Filter to actual author names (capitalized)
            author_names = [p.strip() for p in parts if p.strip() and p.strip()[0].isupper()]
            
            if len(author_names) >= 1:
                first_author = author_names[0]
                
                if len(author_names) == 2:
                    # Two authors - capture both
                    return [AuthorYearCitation(
                        author=first_author,
                        year=year,
                        is_et_al=False,
                        second_author=author_names[1],
                        raw_text=raw
                    )]
                elif len(author_names) >= 3:
                    # Three or more authors - first author et al.
                    return [AuthorYearCitation(
                        author=first_author,
                        year=year,
                        is_et_al=True,
                        raw_text=raw
                    )]
                else:
                    # Single author (shouldn't happen with & but handle anyway)
                    return [AuthorYearCitation(
                        author=first_author,
                        year=year,
                        is_et_al=False,
                        raw_text=raw
                    )]
        
        # Simple single author: "Smith" (already stripped year)
        # Extract first capitalized word
        first_author_match = re.match(rf'({self.AUTHOR_NAME})', author_part)
        if first_author_match:
            return [AuthorYearCitation(
                author=first_author_match.group(1),
                year=year,
                is_et_al=False,
                raw_text=raw
            )]
        
        return []
    
    def get_unique_citations(self, citations: List[AuthorYearCitation] = None) -> List[AuthorYearCitation]:
        """
        Return deduplicated list of citations.
        
        Args:
            citations: Optional list of citations. If None, uses self.citations.
        
        Returns:
            List keeping one representative citation for each unique (author, year) pair.
        """
        source = citations if citations is not None else self.citations
        seen: Set[Tuple[str, str]] = set()
        unique = []
        
        for citation in source:
            key = citation.search_key()
            if key not in seen:
                seen.add(key)
                unique.append(citation)
        
        return unique
    
    def get_search_queries(self, citations: List[AuthorYearCitation] = None) -> List[Tuple[str, str, Optional[str]]]:
        """
        Return list of (author, year, second_author) tuples for searching.
        
        Args:
            citations: Optional list of citations. If None, uses self.citations.
        
        Returns:
            Tuples that can be passed to author_date_engine for lookup.
        """
        unique = self.get_unique_citations(citations)
        queries = []
        
        for citation in unique:
            queries.append((
                citation.author,
                citation.year,
                citation.second_author
            ))
        
        return queries
    
    def extract_citations_from_docx(self, file_bytes: bytes) -> List[AuthorYearCitation]:
        """
        Extract citations from a Word document.
        
        Args:
            file_bytes: The .docx file as bytes
            
        Returns:
            List of AuthorYearCitation objects
        """
        body_text = extract_body_text_from_docx(file_bytes)
        return self.extract_from_text(body_text)


def extract_author_date_citations(text: str) -> List[AuthorYearCitation]:
    """
    Convenience function to extract citations from text.
    
    Args:
        text: Document body text
        
    Returns:
        List of unique AuthorYearCitation objects
    """
    extractor = AuthorDateExtractor()
    extractor.extract_from_text(text)
    return extractor.get_unique_citations()


# =============================================================================
# WORD DOCUMENT EXTRACTION
# =============================================================================

def extract_body_text_from_docx(file_bytes: bytes) -> str:
    """
    Extract main body text from a Word document (excluding footnotes/endnotes).
    
    Args:
        file_bytes: The .docx file as bytes
        
    Returns:
        Plain text content of document body
    """
    import zipfile
    import xml.etree.ElementTree as ET
    from io import BytesIO
    
    NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    
    try:
        with zipfile.ZipFile(BytesIO(file_bytes), 'r') as zf:
            # Read main document
            if 'word/document.xml' not in zf.namelist():
                return ""
            
            with zf.open('word/document.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
            
            # Extract all text from paragraphs
            text_parts = []
            for para in root.findall('.//w:p', NS):
                para_text = []
                for t in para.findall('.//w:t', NS):
                    if t.text:
                        para_text.append(t.text)
                if para_text:
                    text_parts.append(''.join(para_text))
            
            return '\n'.join(text_parts)
    
    except Exception as e:
        print(f"[extract_body_text_from_docx] Error: {e}")
        return ""


def extract_references_section(text: str) -> Tuple[str, str]:
    """
    Split document into body and references section.
    
    Looks for common headers: "References", "Reference List", "Bibliography",
    "Works Cited", "References Cited"
    
    Args:
        text: Full document text
        
    Returns:
        Tuple of (body_text, references_text)
    """
    # Common reference section headers
    headers = [
        r'\n\s*References?\s*\n',
        r'\n\s*Reference\s+List\s*\n',
        r'\n\s*Bibliography\s*\n',
        r'\n\s*Works\s+Cited\s*\n',
        r'\n\s*References\s+Cited\s*\n',
        r'\n\s*Literature\s+Cited\s*\n',
    ]
    
    combined_pattern = '|'.join(f'({h})' for h in headers)
    
    match = re.search(combined_pattern, text, re.IGNORECASE)
    
    if match:
        split_point = match.start()
        return text[:split_point], text[split_point:]
    
    # No references section found
    return text, ""


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    # Test with sample text
    test_text = """
    According to Bandura (1977), self-efficacy plays a crucial role in behavior.
    This has been supported by subsequent research (Bandura, 1986, 1997).
    
    The seminal work by Kahneman and Tversky (1979) introduced prospect theory,
    which was later expanded (Kahneman & Tversky, 1984; Tversky & Kahneman, 1992).
    
    Recent studies (Smith et al., 2020; Jones & Williams, 2021) have shown that
    cognitive biases affect decision-making (Diener et al., 2014, p. 25).
    
    As noted by several researchers (Beck, 2011; Seligman, 2012; Csikszentmihalyi, 1990),
    positive psychology has gained significant traction.
    """
    
    print("=" * 60)
    print("AUTHOR-DATE CITATION EXTRACTOR TEST")
    print("=" * 60)
    
    extractor = AuthorDateExtractor()
    citations = extractor.extract_from_text(test_text)
    
    print(f"\nFound {len(citations)} total citations:")
    for c in citations:
        print(f"  - {c.author}, {c.year} (et al: {c.is_et_al}, second: {c.second_author})")
        print(f"    Raw: {c.raw_text}")
    
    print(f"\nUnique citations: {len(extractor.get_unique_citations())}")
    
    print("\nSearch queries:")
    for author, year, second in extractor.get_search_queries():
        print(f"  - Author: {author}, Year: {year}, Second: {second}")
