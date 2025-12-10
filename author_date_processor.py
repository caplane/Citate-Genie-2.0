"""
citeflex/author_date_processor.py

Document processor for author-date citation styles.

Workflow:
1. Extract body text from Word document
2. Find all (Author, Year) citations
3. Search for each unique citation
4. Generate formatted References section
5. Append/replace References in document

Supports styles:
- APA (7th ed.)
- Harvard
- Chicago Author-Date
- ASA (Sociology)
- AAA (Anthropology)
- Turabian Author-Date

Created: 2025-12-10
"""

import os
import re
import zipfile
import tempfile
import shutil
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from io import BytesIO

from models import CitationMetadata, CitationType
from author_date_extractor import (
    AuthorDateExtractor,
    AuthorYearCitation,
    extract_body_text_from_docx,
    extract_references_section
)
from author_date_engine import AuthorDateEngine, get_engine


@dataclass
class ReferenceEntry:
    """A single reference entry with metadata and formatting."""
    citation: AuthorYearCitation  # Original in-text citation
    metadata: Optional[CitationMetadata]  # Looked-up metadata
    formatted: str  # Formatted reference string
    found: bool  # Whether lookup succeeded
    confidence: float = 0.0
    error: Optional[str] = None


@dataclass
class ProcessingResult:
    """Result of processing a document for author-date citations."""
    citations_found: int
    citations_resolved: int
    citations_failed: int
    references: List[ReferenceEntry]
    reference_list_text: str  # Formatted reference list
    style: str
    errors: List[str] = field(default_factory=list)


class AuthorDateProcessor:
    """
    Processes Word documents with author-date citations.
    
    Extracts (Author, Year) citations from body text, looks up metadata,
    and generates a formatted References section.
    """
    
    # Supported author-date styles
    SUPPORTED_STYLES = [
        "APA (7th ed.)",
        "Harvard",
        "Chicago Author-Date",
        "ASA (Sociology)",
        "AAA (Anthropology)",
        "Turabian Author-Date",
    ]
    
    # Map style names to formatter
    STYLE_MAP = {
        "APA (7th ed.)": "apa",
        "Harvard": "harvard",
        "Chicago Author-Date": "chicago_author_date",
        "ASA (Sociology)": "asa",
        "AAA (Anthropology)": "aaa",
        "Turabian Author-Date": "turabian",
    }
    
    def __init__(self):
        self.extractor = AuthorDateExtractor()
        self.engine = get_engine()
        self._formatters = {}
    
    def _get_formatter(self, style: str):
        """Get formatter for the specified style."""
        if style not in self._formatters:
            style_key = self.STYLE_MAP.get(style, "apa")
            
            # Load the appropriate formatter based on style
            try:
                if style_key == "harvard":
                    from formatters.harvard import HarvardFormatter
                    self._formatters[style] = HarvardFormatter()
                elif style_key == "chicago_author_date":
                    from formatters.chicago_author_date import ChicagoAuthorDateFormatter
                    self._formatters[style] = ChicagoAuthorDateFormatter()
                else:
                    # Default to APA for apa, asa, aaa, turabian
                    from formatters.apa import APAFormatter
                    self._formatters[style] = APAFormatter()
            except ImportError as e:
                print(f"[Processor] Formatter import error: {e}")
                # Fallback to base formatter
                from formatters.base import get_formatter
                self._formatters[style] = get_formatter("APA")
        
        return self._formatters[style]
    
    def process_document(
        self,
        file_bytes: bytes,
        style: str = "APA (7th ed.)",
        progress_callback=None
    ) -> Tuple[bytes, ProcessingResult]:
        """
        Process a document to extract citations and generate references.
        
        Args:
            file_bytes: Word document as bytes
            style: Citation style to use
            progress_callback: Optional callback(status, current, total)
            
        Returns:
            Tuple of (processed_document_bytes, ProcessingResult)
        """
        errors = []
        
        # Step 1: Extract body text
        if progress_callback:
            progress_callback("Extracting text...", 0, 100)
        
        body_text = extract_body_text_from_docx(file_bytes)
        if not body_text:
            errors.append("Could not extract text from document")
            return file_bytes, ProcessingResult(
                citations_found=0,
                citations_resolved=0,
                citations_failed=0,
                references=[],
                reference_list_text="",
                style=style,
                errors=errors
            )
        
        # Step 2: Extract citations
        if progress_callback:
            progress_callback("Finding citations...", 10, 100)
        
        self.extractor.extract_from_text(body_text)
        unique_citations = self.extractor.get_unique_citations()
        
        if not unique_citations:
            errors.append("No author-date citations found in document")
            return file_bytes, ProcessingResult(
                citations_found=0,
                citations_resolved=0,
                citations_failed=0,
                references=[],
                reference_list_text="",
                style=style,
                errors=errors
            )
        
        # Step 3: Look up each citation
        references: List[ReferenceEntry] = []
        total = len(unique_citations)
        resolved = 0
        
        for i, citation in enumerate(unique_citations):
            if progress_callback:
                pct = 20 + int((i / total) * 60)  # 20-80% for lookups
                progress_callback(
                    f"Looking up: {citation.author}, {citation.year}",
                    pct, 100
                )
            
            try:
                metadata = self.engine.search(
                    citation.author,
                    citation.year,
                    citation.second_author
                )
                
                if metadata:
                    # Format the reference
                    formatter = self._get_formatter(style)
                    formatted = formatter.format(metadata)
                    
                    references.append(ReferenceEntry(
                        citation=citation,
                        metadata=metadata,
                        formatted=formatted,
                        found=True,
                        confidence=0.8  # Could get actual confidence from engine
                    ))
                    resolved += 1
                else:
                    # Not found - create placeholder
                    references.append(ReferenceEntry(
                        citation=citation,
                        metadata=None,
                        formatted=f"[NOT FOUND: {citation.author}, {citation.year}]",
                        found=False,
                        error="No matching publication found"
                    ))
            
            except Exception as e:
                references.append(ReferenceEntry(
                    citation=citation,
                    metadata=None,
                    formatted=f"[ERROR: {citation.author}, {citation.year}]",
                    found=False,
                    error=str(e)
                ))
        
        # Step 4: Sort references alphabetically by author
        if progress_callback:
            progress_callback("Sorting references...", 85, 100)
        
        references.sort(key=lambda r: (
            r.citation.author.lower(),
            r.citation.year,
            r.citation.second_author.lower() if r.citation.second_author else ""
        ))
        
        # Step 5: Generate reference list text
        if progress_callback:
            progress_callback("Generating reference list...", 90, 100)
        
        reference_list_text = self._generate_reference_list(references, style)
        
        # Step 6: Update document with references
        if progress_callback:
            progress_callback("Updating document...", 95, 100)
        
        processed_bytes = self._update_document_references(
            file_bytes,
            reference_list_text,
            style
        )
        
        if progress_callback:
            progress_callback("Complete!", 100, 100)
        
        return processed_bytes, ProcessingResult(
            citations_found=total,
            citations_resolved=resolved,
            citations_failed=total - resolved,
            references=references,
            reference_list_text=reference_list_text,
            style=style,
            errors=errors
        )
    
    def _generate_reference_list(
        self,
        references: List[ReferenceEntry],
        style: str
    ) -> str:
        """Generate formatted reference list text."""
        lines = []
        
        # Header based on style
        if style in ["APA (7th ed.)", "Harvard", "ASA (Sociology)"]:
            lines.append("References")
        elif style == "Chicago Author-Date":
            lines.append("References")
        elif style == "AAA (Anthropology)":
            lines.append("References Cited")
        elif style == "Turabian Author-Date":
            lines.append("Bibliography")
        else:
            lines.append("References")
        
        lines.append("")  # Blank line after header
        
        # Add each reference
        for ref in references:
            lines.append(ref.formatted)
            lines.append("")  # Blank line between entries (APA style)
        
        return "\n".join(lines)
    
    def _update_document_references(
        self,
        file_bytes: bytes,
        reference_list_text: str,
        style: str
    ) -> bytes:
        """
        Update the Word document with the new references section.
        
        If a References section exists, replace it.
        Otherwise, append to the end.
        """
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Extract docx
            with zipfile.ZipFile(BytesIO(file_bytes), 'r') as zf:
                zf.extractall(temp_dir)
            
            # Load document.xml
            doc_path = os.path.join(temp_dir, 'word', 'document.xml')
            
            NS = {
                'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                'xml': 'http://www.w3.org/XML/1998/namespace',
            }
            
            # Register namespaces
            for prefix, uri in NS.items():
                ET.register_namespace(prefix, uri)
            
            tree = ET.parse(doc_path)
            root = tree.getroot()
            
            # Find body element
            body = root.find('.//w:body', NS)
            if body is None:
                return file_bytes
            
            # Look for existing References section
            references_para = None
            references_start_idx = None
            
            paragraphs = body.findall('w:p', NS)
            for i, para in enumerate(paragraphs):
                para_text = ''.join(t.text or '' for t in para.findall('.//w:t', NS))
                if re.match(r'^References?\s*$|^Bibliography\s*$|^References Cited\s*$', 
                           para_text.strip(), re.IGNORECASE):
                    references_para = para
                    references_start_idx = i
                    break
            
            # Create new references paragraphs
            new_paragraphs = self._create_reference_paragraphs(reference_list_text, NS)
            
            if references_start_idx is not None:
                # Remove existing references section (from header to end)
                # Keep paragraphs before references
                for para in paragraphs[references_start_idx:]:
                    body.remove(para)
            
            # Find sectPr (section properties) - should be at end
            sect_pr = body.find('w:sectPr', NS)
            
            # Insert new paragraphs before sectPr
            insert_idx = len(list(body)) - 1 if sect_pr is not None else len(list(body))
            
            for i, para in enumerate(new_paragraphs):
                body.insert(insert_idx + i, para)
            
            # Write back
            tree.write(doc_path, encoding='UTF-8', xml_declaration=True)
            
            # Repackage
            output_buffer = BytesIO()
            with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root_dir, dirs, files in os.walk(temp_dir):
                    for file in files:
                        file_path = os.path.join(root_dir, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zf.write(file_path, arcname)
            
            output_buffer.seek(0)
            return output_buffer.read()
        
        except Exception as e:
            print(f"[AuthorDateProcessor] Error updating document: {e}")
            return file_bytes
        
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
    
    def _create_reference_paragraphs(
        self,
        reference_list_text: str,
        NS: Dict[str, str]
    ) -> List[ET.Element]:
        """Create Word XML paragraphs for the reference list."""
        paragraphs = []
        
        lines = reference_list_text.split('\n')
        
        for line in lines:
            para = ET.Element(f"{{{NS['w']}}}p")
            
            if line.strip():
                # Check if this is the header
                if re.match(r'^References?$|^Bibliography$|^References Cited$', 
                           line.strip(), re.IGNORECASE):
                    # Add heading style
                    pPr = ET.SubElement(para, f"{{{NS['w']}}}pPr")
                    pStyle = ET.SubElement(pPr, f"{{{NS['w']}}}pStyle")
                    pStyle.set(f"{{{NS['w']}}}val", "Heading1")
                
                # Create run with text
                run = ET.SubElement(para, f"{{{NS['w']}}}r")
                
                # Handle italics (marked with <i> tags in formatted output)
                parts = re.split(r'(<i>.*?</i>)', line)
                
                for part in parts:
                    if not part:
                        continue
                    
                    if part.startswith('<i>') and part.endswith('</i>'):
                        # Italic text
                        inner = part[3:-4]
                        italic_run = ET.SubElement(para, f"{{{NS['w']}}}r")
                        rPr = ET.SubElement(italic_run, f"{{{NS['w']}}}rPr")
                        ET.SubElement(rPr, f"{{{NS['w']}}}i")
                        t = ET.SubElement(italic_run, f"{{{NS['w']}}}t")
                        t.text = inner
                        t.set(f"{{{NS['xml']}}}space", "preserve")
                    else:
                        # Normal text
                        t = ET.SubElement(run, f"{{{NS['w']}}}t")
                        t.text = part
                        t.set(f"{{{NS['xml']}}}space", "preserve")
            
            paragraphs.append(para)
        
        return paragraphs
    
    def extract_citations_only(self, file_bytes: bytes) -> List[AuthorYearCitation]:
        """
        Extract citations without looking them up.
        
        Useful for preview/validation before full processing.
        """
        body_text = extract_body_text_from_docx(file_bytes)
        if not body_text:
            return []
        
        citations = self.extractor.extract_from_text(body_text)
        return self.extractor.get_unique_citations(citations)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def process_author_date_document(
    file_bytes: bytes,
    style: str = "APA (7th ed.)",
    progress_callback=None
) -> Tuple[bytes, ProcessingResult]:
    """
    Convenience function to process a document.
    
    Args:
        file_bytes: Word document as bytes
        style: Citation style
        progress_callback: Optional progress callback
        
    Returns:
        Tuple of (processed_document_bytes, ProcessingResult)
    """
    processor = AuthorDateProcessor()
    return processor.process_document(file_bytes, style, progress_callback)


def get_supported_styles() -> List[str]:
    """Return list of supported author-date styles."""
    return AuthorDateProcessor.SUPPORTED_STYLES.copy()


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("AUTHOR-DATE PROCESSOR TEST")
    print("=" * 60)
    
    # Create a test document text
    test_text = """
    According to Bandura (1977), self-efficacy plays a crucial role in behavior.
    This was further explored by Kahneman and Tversky (1979) in their work on 
    prospect theory.
    
    Recent research (Diener et al., 2014) has examined subjective well-being.
    The positive psychology movement (Seligman, 2011) has grown significantly.
    """
    
    print("\nTest text:")
    print(test_text)
    
    print("\n" + "=" * 60)
    print("Extracting citations...")
    
    extractor = AuthorDateExtractor()
    citations = extractor.extract_from_text(test_text)
    unique = extractor.get_unique_citations()
    
    print(f"\nFound {len(unique)} unique citations:")
    for c in unique:
        print(f"  - {c.author}, {c.year}")
    
    print("\n" + "=" * 60)
    print("Supported styles:")
    for style in get_supported_styles():
        print(f"  - {style}")
