"""
citeflex/document_processor.py

Word document processing using direct XML manipulation.

Ported from the monolithic citation_manager.py to preserve:
- Proper endnote/footnote reference elements
- Italic formatting via <i> tags
- Clickable hyperlinks for URLs

This approach extracts the docx as a zip, manipulates the XML directly,
and repackages it - giving full control over Word's internal structure.

Version History:
    2025-12-05 12:53: Enhanced IBID_PATTERN to recognize "Id." (Bluebook) and "pp." prefixes
                      Switched from router to unified_router import
    2025-12-05 13:15: Verified ibid detection passes 13/13 tests including Id. at X patterns
    2025-12-09: Refactored to two-phase processing:
                Phase 1: Parallel API lookups (preserves 10x speed)
                Phase 2: Sequential ibid/short form logic (fixes history tracking)
"""

import os
import re
import html
import zipfile
import tempfile
import shutil
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from io import BytesIO

from models import normalize_doi


# =============================================================================
# IBID DETECTION AND HANDLING
# =============================================================================

# Pattern to match ibid variations
# Matches: ibid, ibid., Ibid, Ibid., IBID, IBID., ibidem, etc.
# Optionally followed by comma/period and page number
# Updated: 2025-12-05 - Added "Id." (Bluebook) and "pp." support
IBID_PATTERN = re.compile(
    r'^(?:ibid\.?|ibidem\.?|id\.?)(?:\s*(?:at\s+|[,.]?\s*)?(?:pp?\.?\s*)?(\d+[\-–]?\d*)?)?\.?$',
    re.IGNORECASE
)


def is_ibid(text: str) -> bool:
    """
    Check if the text is an ibid reference.
    
    Recognizes variations:
    - ibid / ibid. / Ibid / Ibid. / IBID
    - ibidem
    - Id. / id. (Bluebook short form)
    - ibid, 45 / ibid., 45 / ibid. 123-125
    - Id. at 45 / id. at 789
    - ibid., pp. 12-15
    
    Updated: 2025-12-05 - Added Id. and pp. support
    
    Args:
        text: The citation text to check
        
    Returns:
        True if this is an ibid reference
    """
    if not text:
        return False
    
    cleaned = text.strip()
    return IBID_PATTERN.match(cleaned) is not None


def extract_ibid_page(text: str) -> Optional[str]:
    """
    Extract page number from an ibid reference.
    
    Examples:
    - "ibid, 45" → "45"
    - "ibid., 123-125" → "123-125"
    - "Id. at 789" → "789"
    - "ibid., pp. 12-15" → "12-15"
    - "ibid." → None
    - "ibid" → None
    
    Updated: 2025-12-05 - Added Id. at X and pp. support
    
    Args:
        text: The ibid text
        
    Returns:
        Page number string if present, None otherwise
    """
    if not text:
        return None
    
    cleaned = text.strip()
    match = IBID_PATTERN.match(cleaned)
    
    if match and match.group(1):
        return match.group(1).strip()
    
    return None


def normalize_url(url: str) -> str:
    """
    Normalize a URL for comparison purposes.
    
    Removes trailing slashes, converts to lowercase, strips whitespace,
    and removes common tracking parameters to ensure matching URLs
    are recognized as the same source.
    
    Args:
        url: The URL to normalize
        
    Returns:
        Normalized URL string
    """
    if not url:
        return ""
    
    # Strip whitespace and convert to lowercase
    normalized = url.strip().lower()
    
    # Remove trailing slashes
    normalized = normalized.rstrip('/')
    
    # Remove common tracking parameters (utm_, etc.)
    # Simple approach: remove everything after ? for comparison
    # This may be too aggressive for some URLs, but works for most cases
    if '?' in normalized:
        base_url = normalized.split('?')[0]
        # Keep the base URL without query params for comparison
        normalized = base_url
    
    return normalized


def urls_match(url1: Optional[str], url2: Optional[str]) -> bool:
    """
    Check if two URLs refer to the same source.
    
    Uses normalized comparison to handle minor variations like
    trailing slashes, case differences, and tracking parameters.
    
    Args:
        url1: First URL
        url2: Second URL
        
    Returns:
        True if both URLs are non-empty and match after normalization
    """
    if not url1 or not url2:
        return False
    
    return normalize_url(url1) == normalize_url(url2)


# =============================================================================
# SOURCE MATCHING FOR SHORT FORM DETECTION
# =============================================================================

def generate_source_key(metadata: Any) -> Optional[str]:
    """
    Generate a unique key to identify a source for short form matching.
    
    Two citations with the same source key refer to the same work.
    
    Priority order for matching:
    1. DOI (most reliable) - NORMALIZED for consistent comparison
    2. URL (for web sources)
    3. Case name + citation (for legal)
    4. Title + first author (for books/articles)
    
    Args:
        metadata: CitationMetadata object
        
    Returns:
        String key for source matching, or None if no key can be generated
    """
    if not metadata:
        return None
    
    # Priority 1: DOI (normalized for consistent matching)
    doi = getattr(metadata, 'doi', None)
    if doi:
        # Use normalized DOI for consistent matching
        normalized = normalize_doi(doi)
        if normalized:
            return f"doi:{normalized}"
    
    # Priority 2: URL (normalized)
    url = getattr(metadata, 'url', None)
    if url:
        return f"url:{normalize_url(url)}"
    
    # Priority 3: Legal case (case name + citation)
    case_name = getattr(metadata, 'case_name', None)
    citation = getattr(metadata, 'citation', None)
    if case_name and citation:
        return f"legal:{case_name.lower().strip()}|{citation.lower().strip()}"
    
    # Priority 4: Title + first author
    title = getattr(metadata, 'title', None)
    authors = getattr(metadata, 'authors', None)
    if title:
        key = f"title:{title.lower().strip()}"
        if authors and len(authors) > 0:
            key += f"|author:{authors[0].lower().strip()}"
        return key
    
    # Priority 5: Just case name for legal without citation
    if case_name:
        return f"case:{case_name.lower().strip()}"
    
    return None


def sources_match(metadata1: Any, metadata2: Any) -> bool:
    """
    Check if two citation metadata objects refer to the same source.
    
    Args:
        metadata1: First CitationMetadata
        metadata2: Second CitationMetadata
        
    Returns:
        True if both refer to the same work
    """
    key1 = generate_source_key(metadata1)
    key2 = generate_source_key(metadata2)
    
    if key1 is None or key2 is None:
        return False
    
    return key1 == key2


@dataclass
class ProcessedCitation:
    """Result of processing a single citation."""
    original: str
    formatted: str
    metadata: Any
    url: Optional[str]
    success: bool
    error: Optional[str] = None
    citation_form: str = "full"  # "full", "ibid", or "short"


@dataclass 
class CitationHistoryEntry:
    """Entry in the citation history for tracking previously cited sources."""
    metadata: Any
    formatted: str
    source_key: Optional[str]
    note_number: int


class CitationHistory:
    """
    Tracks all citations seen in a document for ibid and short form handling.
    
    Maintains:
    - Previous citation (for ibid detection)
    - All cited sources (for short form detection)
    """
    
    def __init__(self):
        self.previous: Optional[CitationHistoryEntry] = None
        self.all_sources: Dict[str, CitationHistoryEntry] = {}  # source_key -> first occurrence
        self.note_counter: int = 0
    
    def add(self, metadata: Any, formatted: str) -> None:
        """
        Add a citation to the history.
        
        Args:
            metadata: Citation metadata
            formatted: Formatted citation string
        """
        self.note_counter += 1
        source_key = generate_source_key(metadata)
        
        entry = CitationHistoryEntry(
            metadata=metadata,
            formatted=formatted,
            source_key=source_key,
            note_number=self.note_counter
        )
        
        # Update previous
        self.previous = entry
        
        # Add to all_sources if this is the first time we've seen this source
        if source_key and source_key not in self.all_sources:
            self.all_sources[source_key] = entry
    
    def is_same_as_previous(self, metadata: Any) -> bool:
        """
        Check if the given metadata matches the immediately previous citation.
        
        Args:
            metadata: Citation metadata to check
            
        Returns:
            True if this is the same source as the previous citation
        """
        if self.previous is None:
            return False
        
        return sources_match(metadata, self.previous.metadata)
    
    def has_been_cited_before(self, metadata: Any) -> bool:
        """
        Check if this source has been cited previously in the document.
        
        Args:
            metadata: Citation metadata to check
            
        Returns:
            True if this source has been cited before (not counting current)
        """
        source_key = generate_source_key(metadata)
        if source_key is None:
            return False
        
        return source_key in self.all_sources
    
    def get_previous_metadata(self) -> Optional[Any]:
        """Get the metadata of the previous citation."""
        if self.previous:
            return self.previous.metadata
        return None
    
    def get_previous_url(self) -> Optional[str]:
        """Get the URL of the previous citation."""
        if self.previous and self.previous.metadata:
            return getattr(self.previous.metadata, 'url', None)
        return None


class WordDocumentProcessor:
    """
    Processes Word documents to read and write endnotes/footnotes.
    Preserves the main document body while allowing citation fixes.
    
    Uses direct XML manipulation for precise control over Word's structure.
    """
    
    NS = {
        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'xml': 'http://www.w3.org/XML/1998/namespace',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    }
    
    def __init__(self, file_path_or_buffer):
        """
        Initialize with a file path or file-like object (BytesIO).
        """
        self.temp_dir = tempfile.mkdtemp()
        self.original_path = None
        
        # Handle both file paths and file-like objects
        if hasattr(file_path_or_buffer, 'read'):
            # It's a file-like object (e.g., from upload)
            with zipfile.ZipFile(file_path_or_buffer, 'r') as z:
                z.extractall(self.temp_dir)
        else:
            # It's a file path
            self.original_path = file_path_or_buffer
            with zipfile.ZipFile(file_path_or_buffer, 'r') as z:
                z.extractall(self.temp_dir)
    
    def get_endnotes(self) -> List[Dict[str, str]]:
        """
        Extract all endnotes from the document.
        
        Returns:
            List of dicts: [{'id': '1', 'text': 'citation text'}, ...]
        """
        endnotes_path = os.path.join(self.temp_dir, 'word', 'endnotes.xml')
        if not os.path.exists(endnotes_path):
            return []
        
        try:
            tree = ET.parse(endnotes_path)
            root = tree.getroot()
            notes = []
            
            for endnote in root.findall('.//w:endnote', self.NS):
                note_id = endnote.get(f"{{{self.NS['w']}}}id")
                
                # Skip system endnotes (id 0 and -1)
                try:
                    if int(note_id) < 1:
                        continue
                except (ValueError, TypeError):
                    continue
                
                # Extract all text from this endnote
                text_parts = []
                for t in endnote.findall('.//w:t', self.NS):
                    if t.text:
                        text_parts.append(t.text)
                
                full_text = "".join(text_parts).strip()
                if full_text:
                    notes.append({'id': note_id, 'text': full_text})
            
            return notes
            
        except Exception as e:
            print(f"[WordDocumentProcessor] Error reading endnotes: {e}")
            return []
    
    def get_footnotes(self) -> List[Dict[str, str]]:
        """
        Extract all footnotes from the document.
        
        Returns:
            List of dicts: [{'id': '1', 'text': 'citation text'}, ...]
        """
        footnotes_path = os.path.join(self.temp_dir, 'word', 'footnotes.xml')
        if not os.path.exists(footnotes_path):
            return []
        
        try:
            tree = ET.parse(footnotes_path)
            root = tree.getroot()
            notes = []
            
            for footnote in root.findall('.//w:footnote', self.NS):
                note_id = footnote.get(f"{{{self.NS['w']}}}id")
                
                # Skip system footnotes (id 0 and -1)
                try:
                    if int(note_id) < 1:
                        continue
                except (ValueError, TypeError):
                    continue
                
                # Extract all text
                text_parts = []
                for t in footnote.findall('.//w:t', self.NS):
                    if t.text:
                        text_parts.append(t.text)
                
                full_text = "".join(text_parts).strip()
                if full_text:
                    notes.append({'id': note_id, 'text': full_text})
            
            return notes
            
        except Exception as e:
            print(f"[WordDocumentProcessor] Error reading footnotes: {e}")
            return []
    
    def write_endnote(self, note_id: str, new_content: str) -> bool:
        """
        Replace an endnote's content with new formatted citation.
        Handles <i> tags for italics using regex (no BeautifulSoup needed).
        PRESERVES the endnoteRef element for proper numbering and linking.
        
        Args:
            note_id: The endnote ID to update
            new_content: New citation text (may contain <i> tags for italics)
            
        Returns:
            bool: True if successful
        """
        endnotes_path = os.path.join(self.temp_dir, 'word', 'endnotes.xml')
        if not os.path.exists(endnotes_path):
            return False
        
        try:
            # Register namespace to preserve it
            ET.register_namespace('w', self.NS['w'])
            ET.register_namespace('xml', self.NS['xml'])
            
            tree = ET.parse(endnotes_path)
            root = tree.getroot()
            
            # Find the target endnote
            target = None
            for endnote in root.findall('.//w:endnote', self.NS):
                if endnote.get(f"{{{self.NS['w']}}}id") == str(note_id):
                    target = endnote
                    break
            
            if target is None:
                return False
            
            # Find or create paragraph
            para = target.find('.//w:p', self.NS)
            if para is None:
                para = ET.SubElement(target, f"{{{self.NS['w']}}}p")
            else:
                # FIXED: Preserve paragraph properties AND endnoteRef run
                preserved_pPr = None
                preserved_endnoteRef_run = None
                
                for child in list(para):
                    tag = child.tag.replace(f"{{{self.NS['w']}}}", "")
                    
                    # Preserve paragraph properties
                    if tag == 'pPr':
                        preserved_pPr = child
                        continue
                    
                    # Check if this run contains endnoteRef
                    if tag == 'r':
                        endnote_ref = child.find(f".//{{{self.NS['w']}}}endnoteRef")
                        if endnote_ref is not None:
                            preserved_endnoteRef_run = child
                            continue
                    
                    # Remove all other children
                    para.remove(child)
                
                # If no endnoteRef run was found, create one
                if preserved_endnoteRef_run is None:
                    ref_run = ET.Element(f"{{{self.NS['w']}}}r")
                    rPr = ET.SubElement(ref_run, f"{{{self.NS['w']}}}rPr")
                    rStyle = ET.SubElement(rPr, f"{{{self.NS['w']}}}rStyle")
                    rStyle.set(f"{{{self.NS['w']}}}val", "EndnoteReference")
                    ET.SubElement(ref_run, f"{{{self.NS['w']}}}endnoteRef")
                    
                    # Insert after pPr if it exists, otherwise at beginning
                    if preserved_pPr is not None:
                        idx = list(para).index(preserved_pPr) + 1
                        para.insert(idx, ref_run)
                    else:
                        para.insert(0, ref_run)
            
            # Parse content using regex to handle <i> tags (no BeautifulSoup)
            parts = re.split(r'(<i>.*?</i>)', html.unescape(new_content))
            
            for part in parts:
                if not part:
                    continue
                    
                run = ET.SubElement(para, f"{{{self.NS['w']}}}r")
                
                # Check if this is italic text
                italic_match = re.match(r'<i>(.*?)</i>', part)
                if italic_match:
                    rPr = ET.SubElement(run, f"{{{self.NS['w']}}}rPr")
                    ET.SubElement(rPr, f"{{{self.NS['w']}}}i")
                    text_content = italic_match.group(1)
                else:
                    text_content = part
                
                t = ET.SubElement(run, f"{{{self.NS['w']}}}t")
                t.text = text_content
                t.set(f"{{{self.NS['xml']}}}space", "preserve")
            
            tree.write(endnotes_path, encoding='UTF-8', xml_declaration=True)
            return True
            
        except Exception as e:
            print(f"[WordDocumentProcessor] Error writing endnote: {e}")
            return False
    
    def write_footnote(self, note_id: str, new_content: str) -> bool:
        """
        Replace a footnote's content with new formatted citation.
        Handles <i> tags for italics using regex (no BeautifulSoup needed).
        PRESERVES the footnoteRef element for proper numbering and linking.
        """
        footnotes_path = os.path.join(self.temp_dir, 'word', 'footnotes.xml')
        if not os.path.exists(footnotes_path):
            return False
        
        try:
            ET.register_namespace('w', self.NS['w'])
            ET.register_namespace('xml', self.NS['xml'])
            
            tree = ET.parse(footnotes_path)
            root = tree.getroot()
            
            target = None
            for footnote in root.findall('.//w:footnote', self.NS):
                if footnote.get(f"{{{self.NS['w']}}}id") == str(note_id):
                    target = footnote
                    break
            
            if target is None:
                return False
            
            para = target.find('.//w:p', self.NS)
            if para is None:
                para = ET.SubElement(target, f"{{{self.NS['w']}}}p")
            else:
                # FIXED: Preserve paragraph properties AND footnoteRef run
                preserved_pPr = None
                preserved_footnoteRef_run = None
                
                for child in list(para):
                    tag = child.tag.replace(f"{{{self.NS['w']}}}", "")
                    
                    # Preserve paragraph properties
                    if tag == 'pPr':
                        preserved_pPr = child
                        continue
                    
                    # Check if this run contains footnoteRef
                    if tag == 'r':
                        footnote_ref = child.find(f".//{{{self.NS['w']}}}footnoteRef")
                        if footnote_ref is not None:
                            preserved_footnoteRef_run = child
                            continue
                    
                    # Remove all other children
                    para.remove(child)
                
                # If no footnoteRef run was found, create one
                if preserved_footnoteRef_run is None:
                    ref_run = ET.Element(f"{{{self.NS['w']}}}r")
                    rPr = ET.SubElement(ref_run, f"{{{self.NS['w']}}}rPr")
                    rStyle = ET.SubElement(rPr, f"{{{self.NS['w']}}}rStyle")
                    rStyle.set(f"{{{self.NS['w']}}}val", "FootnoteReference")
                    ET.SubElement(ref_run, f"{{{self.NS['w']}}}footnoteRef")
                    
                    # Insert after pPr if it exists, otherwise at beginning
                    if preserved_pPr is not None:
                        idx = list(para).index(preserved_pPr) + 1
                        para.insert(idx, ref_run)
                    else:
                        para.insert(0, ref_run)
            
            # Parse content using regex to handle <i> tags
            parts = re.split(r'(<i>.*?</i>)', html.unescape(new_content))
            
            for part in parts:
                if not part:
                    continue
                    
                run = ET.SubElement(para, f"{{{self.NS['w']}}}r")
                
                # Check if this is italic text
                italic_match = re.match(r'<i>(.*?)</i>', part)
                if italic_match:
                    rPr = ET.SubElement(run, f"{{{self.NS['w']}}}rPr")
                    ET.SubElement(rPr, f"{{{self.NS['w']}}}i")
                    text_content = italic_match.group(1)
                else:
                    text_content = part
                
                t = ET.SubElement(run, f"{{{self.NS['w']}}}t")
                t.text = text_content
                t.set(f"{{{self.NS['xml']}}}space", "preserve")
            
            tree.write(footnotes_path, encoding='UTF-8', xml_declaration=True)
            return True
            
        except Exception as e:
            print(f"[WordDocumentProcessor] Error writing footnote: {e}")
            return False
    
    def save_to_buffer(self) -> BytesIO:
        """
        Save the modified document to a BytesIO buffer.
        
        Returns:
            BytesIO buffer containing the .docx file
        """
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, self.temp_dir)
                    zipf.write(file_path, arcname)
        buffer.seek(0)
        return buffer
    
    def save_as(self, output_path: str) -> None:
        """
        Save the modified document to a new file.
        
        Args:
            output_path: Path for the output .docx file
        """
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, self.temp_dir)
                    zipf.write(file_path, arcname)
    
    def cleanup(self) -> None:
        """Remove temporary files."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.cleanup()
        except:
            pass



# =============================================================================
# LINK ACTIVATOR - FIXED VERSION
# =============================================================================
# Uses proper XML parsing (ElementTree) and relationship-based hyperlinks
# instead of regex string replacement that was creating malformed XML.
# =============================================================================

class RelsManager:
    """
    Manages the .rels file for a Word document part.
    
    Handles reading, modifying, and writing relationship files
    that map rIds to external hyperlinks.
    """
    
    RELS_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
    HYPERLINK_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    
    def __init__(self, rels_path: str):
        """
        Initialize the RelsManager.
        
        Args:
            rels_path: Path to the .rels file
        """
        self.rels_path = rels_path
        self.relationships: Dict[str, dict] = {}  # rId -> {type, target, mode}
        self.next_id = 1
        self.url_to_rid: Dict[str, str] = {}  # URL -> existing rId
        
        self._load()
    
    def _load(self):
        """Load existing relationships from file."""
        if not os.path.exists(self.rels_path):
            # Create directory if needed
            os.makedirs(os.path.dirname(self.rels_path), exist_ok=True)
            return
        
        try:
            ET.register_namespace('', self.RELS_NS)
            tree = ET.parse(self.rels_path)
            root = tree.getroot()
            
            for rel in root.findall(f'{{{self.RELS_NS}}}Relationship'):
                r_id = rel.get('Id', '')
                rel_type = rel.get('Type', '')
                target = rel.get('Target', '')
                target_mode = rel.get('TargetMode', '')
                
                self.relationships[r_id] = {
                    'type': rel_type,
                    'target': target,
                    'mode': target_mode
                }
                
                # Track existing hyperlink URLs
                if rel_type == self.HYPERLINK_TYPE:
                    self.url_to_rid[target] = r_id
                
                # Track highest rId number
                if r_id.startswith('rId'):
                    try:
                        num = int(r_id[3:])
                        if num >= self.next_id:
                            self.next_id = num + 1
                    except ValueError:
                        pass
                        
        except ET.ParseError as e:
            print(f"[RelsManager] Error parsing {self.rels_path}: {e}")
    
    def add_hyperlink(self, url: str) -> str:
        """
        Add a hyperlink relationship and return its rId.
        
        If the URL already exists, returns the existing rId.
        
        Args:
            url: The URL to link to
            
        Returns:
            The rId for this hyperlink
        """
        # Check if URL already has a relationship
        if url in self.url_to_rid:
            return self.url_to_rid[url]
        
        # Create new relationship
        r_id = f'rId{self.next_id}'
        self.next_id += 1
        
        self.relationships[r_id] = {
            'type': self.HYPERLINK_TYPE,
            'target': url,
            'mode': 'External'
        }
        
        self.url_to_rid[url] = r_id
        
        return r_id
    
    def save(self):
        """Save relationships to file."""
        ET.register_namespace('', self.RELS_NS)
        
        root = ET.Element(f'{{{self.RELS_NS}}}Relationships')
        
        # Sort by rId for consistent output
        for r_id in sorted(self.relationships.keys(), key=lambda x: (len(x), x)):
            rel_data = self.relationships[r_id]
            
            rel = ET.SubElement(root, f'{{{self.RELS_NS}}}Relationship')
            rel.set('Id', r_id)
            rel.set('Type', rel_data['type'])
            rel.set('Target', rel_data['target'])
            if rel_data.get('mode'):
                rel.set('TargetMode', rel_data['mode'])
        
        # Write to file
        tree = ET.ElementTree(root)
        tree.write(self.rels_path, encoding='UTF-8', xml_declaration=True)


class LinkActivator:
    """
    Post-processing module that converts plain text URLs in Word documents
    into clickable hyperlinks using relationship-based approach.
    
    This is the native Word hyperlink format that:
    - Creates <w:hyperlink r:id="rIdX"> elements
    - Manages word/_rels/*.xml.rels files
    - Produces valid XML that Word opens without repair
    
    FIXED: 2025-12-09 - Replaced regex-based approach with proper ElementTree
    XML parsing to prevent malformed XML that caused Word repair dialogs.
    """
    
    # Namespaces used in Word documents
    NS = {
        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    }
    
    # URL pattern
    URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
    
    @classmethod
    def process(cls, docx_buffer: BytesIO) -> BytesIO:
        """
        Process a .docx file to make all URLs clickable.
        
        Args:
            docx_buffer: BytesIO containing the input .docx file
            
        Returns:
            BytesIO containing the processed .docx file with clickable URLs
        """
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Extract docx to temp directory
            docx_buffer.seek(0)
            with zipfile.ZipFile(docx_buffer, 'r') as zf:
                zf.extractall(temp_dir)
            
            # Process each relevant XML file with its corresponding .rels file
            target_files = [
                ('word/document.xml', 'word/_rels/document.xml.rels'),
                ('word/endnotes.xml', 'word/_rels/endnotes.xml.rels'),
                ('word/footnotes.xml', 'word/_rels/footnotes.xml.rels'),
            ]
            
            for xml_file, rels_file in target_files:
                xml_path = os.path.join(temp_dir, xml_file)
                rels_path = os.path.join(temp_dir, rels_file)
                
                if os.path.exists(xml_path):
                    cls._process_xml_file(xml_path, rels_path)
            
            # Repackage as docx
            output_buffer = BytesIO()
            with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zf.write(file_path, arcname)
            
            output_buffer.seek(0)
            return output_buffer
            
        except Exception as e:
            print(f"[LinkActivator] Error: {e}")
            import traceback
            traceback.print_exc()
            docx_buffer.seek(0)
            return docx_buffer
            
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
    
    @classmethod
    def _process_xml_file(cls, xml_path: str, rels_path: str):
        """
        Process a single XML file to convert URLs to hyperlinks.
        
        Args:
            xml_path: Path to the XML file (document.xml, endnotes.xml, etc.)
            rels_path: Path to the corresponding .rels file
        """
        # Register namespaces to preserve them in output
        for prefix, uri in cls.NS.items():
            ET.register_namespace(prefix, uri)
        
        # Also register common namespaces found in Word docs
        ET.register_namespace('mc', 'http://schemas.openxmlformats.org/markup-compatibility/2006')
        ET.register_namespace('w14', 'http://schemas.microsoft.com/office/word/2010/wordml')
        ET.register_namespace('w15', 'http://schemas.microsoft.com/office/word/2012/wordml')
        ET.register_namespace('wps', 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape')
        ET.register_namespace('wpg', 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup')
        ET.register_namespace('wpc', 'http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas')
        ET.register_namespace('wp', 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing')
        ET.register_namespace('a', 'http://schemas.openxmlformats.org/drawingml/2006/main')
        
        # Parse the XML file
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        # Load or create relationships
        rels_manager = RelsManager(rels_path)
        
        # Track URLs we've already processed to avoid duplicates
        processed_urls: Dict[str, str] = {}  # url -> rId
        
        # Find all text elements
        w_ns = cls.NS['w']
        
        # Process all paragraphs
        for para in root.iter(f'{{{w_ns}}}p'):
            cls._process_paragraph(para, rels_manager, processed_urls)
        
        # Save the modified XML
        tree.write(xml_path, encoding='UTF-8', xml_declaration=True)
        
        # Save the relationships file
        rels_manager.save()
    
    @classmethod
    def _process_paragraph(cls, para: ET.Element, rels_manager: 'RelsManager', 
                          processed_urls: Dict[str, str]):
        """
        Process a paragraph to convert URLs to hyperlinks.
        
        Args:
            para: The paragraph element
            rels_manager: Manager for the .rels file
            processed_urls: Cache of URL -> rId mappings
        """
        w_ns = cls.NS['w']
        r_ns = cls.NS['r']
        
        # Get list of direct children - we'll iterate through them
        # We need to work on a copy because we'll be modifying the paragraph
        children = list(para)
        
        for child in children:
            # Only process runs that are direct children (not inside hyperlinks)
            if child.tag != f'{{{w_ns}}}r':
                continue
            
            # Skip if this run is inside a hyperlink (shouldn't happen for direct children, but check)
            if cls._is_inside_hyperlink(child, para):
                continue
            
            # Find text element
            t_elem = child.find(f'{{{w_ns}}}t')
            if t_elem is None or not t_elem.text:
                continue
            
            text = t_elem.text
            
            # Find URLs in the text
            matches = list(cls.URL_PATTERN.finditer(text))
            if not matches:
                continue
            
            # Get run properties to preserve formatting
            rPr = child.find(f'{{{w_ns}}}rPr')
            rPr_copy = None
            if rPr is not None:
                rPr_copy = cls._copy_element(rPr)
            
            # Find position of this run in paragraph
            try:
                run_index = list(para).index(child)
            except ValueError:
                continue
            
            # Build new elements to replace this run
            new_elements = []
            last_end = 0
            
            for match in matches:
                url = match.group(0)
                start, end = match.start(), match.end()
                
                # Clean URL (remove trailing punctuation)
                clean_url = url.rstrip('.,;:)]\'"')
                trailing_punct = url[len(clean_url):]
                
                # Text before this URL
                if start > last_end:
                    text_before = text[last_end:start]
                    before_run = cls._create_run(text_before, rPr_copy, w_ns)
                    new_elements.append(before_run)
                
                # Get or create relationship ID for this URL
                if clean_url in processed_urls:
                    r_id = processed_urls[clean_url]
                else:
                    r_id = rels_manager.add_hyperlink(clean_url)
                    processed_urls[clean_url] = r_id
                
                # Create hyperlink element
                hyperlink = ET.Element(f'{{{w_ns}}}hyperlink')
                hyperlink.set(f'{{{r_ns}}}id', r_id)
                hyperlink.set(f'{{{w_ns}}}history', '1')
                
                # Create run inside hyperlink with URL text
                link_run = cls._create_hyperlink_run(clean_url, rPr_copy, w_ns)
                hyperlink.append(link_run)
                new_elements.append(hyperlink)
                
                # Include trailing punctuation in the "after" text
                last_end = end - len(trailing_punct)
            
            # Text after last URL
            if last_end < len(text):
                text_after = text[last_end:]
                after_run = cls._create_run(text_after, rPr_copy, w_ns)
                new_elements.append(after_run)
            
            # Replace original run with new elements
            para.remove(child)
            for i, elem in enumerate(new_elements):
                para.insert(run_index + i, elem)
    
    @classmethod
    def _create_run(cls, text: str, rPr_template: Optional[ET.Element], w_ns: str) -> ET.Element:
        """Create a run element with text."""
        run = ET.Element(f'{{{w_ns}}}r')
        
        if rPr_template is not None:
            run.append(cls._copy_element(rPr_template))
        
        t = ET.SubElement(run, f'{{{w_ns}}}t')
        t.text = text
        # Preserve whitespace
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        
        return run
    
    @classmethod
    def _create_hyperlink_run(cls, url: str, rPr_template: Optional[ET.Element], w_ns: str) -> ET.Element:
        """Create a run element for inside a hyperlink (with blue/underline styling)."""
        run = ET.Element(f'{{{w_ns}}}r')
        
        # Create run properties
        rPr = ET.SubElement(run, f'{{{w_ns}}}rPr')
        
        # Copy existing properties if present
        if rPr_template is not None:
            for child in rPr_template:
                # Skip color and underline - we'll add our own
                tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if tag not in ('color', 'u'):
                    rPr.append(cls._copy_element(child))
        
        # Add hyperlink styling (blue, underlined)
        color = ET.SubElement(rPr, f'{{{w_ns}}}color')
        color.set(f'{{{w_ns}}}val', '0000FF')
        
        underline = ET.SubElement(rPr, f'{{{w_ns}}}u')
        underline.set(f'{{{w_ns}}}val', 'single')
        
        # Add the URL text
        t = ET.SubElement(run, f'{{{w_ns}}}t')
        t.text = url
        
        return run
    
    @classmethod
    def _is_inside_hyperlink(cls, run: ET.Element, para: ET.Element) -> bool:
        """Check if a run is inside an existing hyperlink element."""
        w_ns = cls.NS['w']
        
        # Check if any hyperlink in paragraph contains this run
        for elem in para:
            if elem.tag == f'{{{w_ns}}}hyperlink':
                for child in elem.iter():
                    if child is run:
                        return True
        
        return False
    
    @classmethod
    def _copy_element(cls, elem: ET.Element) -> ET.Element:
        """Deep copy an element."""
        new_elem = ET.Element(elem.tag, elem.attrib)
        new_elem.text = elem.text
        new_elem.tail = elem.tail
        for child in elem:
            new_elem.append(cls._copy_element(child))
        return new_elem

def process_document(
    file_bytes: bytes,
    style: str = "Chicago Manual of Style",
    add_links: bool = True
) -> tuple:
    """
    Process all citations in a Word document.
    
    Handles citation forms:
    1. Full citation - first time a source is cited
    2. Ibid - same source as immediately preceding citation
    3. Short form - source has been cited before, but not immediately preceding
    
    Also handles:
    - Explicit ibid references (user typed "ibid" or "ibid., 45")
    - Repetitive URLs (same URL as previous note → ibid)
    
    Args:
        file_bytes: The document as bytes
        style: Citation style to use
        add_links: Whether to make URLs clickable
        
    Returns:
        Tuple of (processed_document_bytes, results_list)
    """
    # Import here to avoid circular imports
    from unified_router import get_citation
    from formatters.base import BaseFormatter, get_formatter
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    
    # Per-note timeout to prevent indefinite hanging
    NOTE_TIMEOUT = 8  # seconds per note
    
    results = []
    
    # Initialize citation history for ibid and short form tracking
    history = CitationHistory()
    
    # Get the formatter for short form citations
    formatter = get_formatter(style)
    
    # Load document
    processor = WordDocumentProcessor(BytesIO(file_bytes))
    
    # Get all endnotes and footnotes
    endnotes = processor.get_endnotes()
    footnotes = processor.get_footnotes()
    
    # Helper to call get_citation (parallel executor handles overall timing)
    def get_citation_with_timeout(text: str, style: str, timeout: int = NOTE_TIMEOUT):
        """Call get_citation - parallel processing handles concurrency."""
        try:
            return get_citation(text, style)
        except Exception as e:
            print(f"[process_document] Error in get_citation: {e}")
            return None, None
    
    def fetch_metadata_for_note(note: Dict[str, str], note_type: str) -> Dict[str, Any]:
        """
        Phase 1: Fetch metadata for a single note (parallelizable).
        
        Returns a dict with all info needed for Phase 2 processing.
        Does NOT access citation history (that's Phase 2).
        """
        note_id = note['id']
        original_text = note['text']
        
        # Check if this is an explicit ibid - no API call needed
        if is_ibid(original_text):
            return {
                'note_id': note_id,
                'note_type': note_type,
                'original_text': original_text,
                'is_explicit_ibid': True,
                'ibid_page': extract_ibid_page(original_text),
                'metadata': None,
                'formatted': None,
                'error': None
            }
        
        # Call API to get metadata
        try:
            metadata, full_formatted = get_citation_with_timeout(original_text, style)
            
            # Extract URL if available
            current_url = None
            if metadata:
                current_url = getattr(metadata, 'url', None)
            if not current_url and original_text.strip().startswith('http'):
                current_url = original_text.strip()
            
            return {
                'note_id': note_id,
                'note_type': note_type,
                'original_text': original_text,
                'is_explicit_ibid': False,
                'ibid_page': None,
                'metadata': metadata,
                'formatted': full_formatted,
                'current_url': current_url,
                'error': None if metadata else "No metadata found"
            }
        except Exception as e:
            return {
                'note_id': note_id,
                'note_type': note_type,
                'original_text': original_text,
                'is_explicit_ibid': False,
                'ibid_page': None,
                'metadata': None,
                'formatted': None,
                'current_url': None,
                'error': str(e)
            }
    
    # =========================================================================
    # TWO-PHASE PROCESSING
    # Phase 1: Parallel API lookups (fast - 10 workers)
    # Phase 2: Sequential ibid/short form detection (requires order)
    # =========================================================================
    
    # Combine all notes with their types, maintaining document order
    all_notes = [(note, 'endnote') for note in endnotes]
    all_notes += [(note, 'footnote') for note in footnotes]
    
    total_notes = len(all_notes)
    print(f"[process_document] Processing {len(endnotes)} endnotes, {len(footnotes)} footnotes ({total_notes} total)")
    
    # --- PHASE 1: Parallel metadata fetching ---
    print(f"[process_document] Phase 1: Fetching metadata in parallel...")
    PARALLEL_WORKERS = 10
    
    def fetch_wrapper(args):
        note, note_type = args
        print(f"[process_document] Fetching: {note.get('text', '')[:40]}...")
        return fetch_metadata_for_note(note, note_type)
    
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        fetched_data = list(executor.map(fetch_wrapper, all_notes))
    
    print(f"[process_document] Phase 1 complete: {len(fetched_data)} notes fetched")
    
    # --- PHASE 2: Sequential citation form determination ---
    print(f"[process_document] Phase 2: Applying ibid/short form logic sequentially...")
    results = []
    
    for idx, data in enumerate(fetched_data):
        note_id = data['note_id']
        note_type = data['note_type']
        original_text = data['original_text']
        
        try:
            # Case 1: Explicit ibid reference (user typed "ibid")
            if data['is_explicit_ibid']:
                previous_metadata = history.get_previous_metadata()
                
                if previous_metadata is None:
                    print(f"[process_document] Warning: ibid in {note_type} {note_id} but no previous citation")
                    results.append(ProcessedCitation(
                        original=original_text,
                        formatted=original_text,
                        metadata=None,
                        url=None,
                        success=False,
                        error="ibid reference but no previous citation found",
                        citation_form="ibid"
                    ))
                    continue
                
                formatted = BaseFormatter.format_ibid(data['ibid_page'])
                
                if note_type == 'endnote':
                    processor.write_endnote(note_id, formatted)
                else:
                    processor.write_footnote(note_id, formatted)
                
                results.append(ProcessedCitation(
                    original=original_text,
                    formatted=formatted,
                    metadata=previous_metadata,
                    url=history.get_previous_url(),
                    success=True,
                    citation_form="ibid"
                ))
                continue
            
            # Case 2: API lookup failed
            metadata = data['metadata']
            full_formatted = data['formatted']
            current_url = data.get('current_url')
            
            if not metadata or not full_formatted:
                results.append(ProcessedCitation(
                    original=original_text,
                    formatted=original_text,
                    metadata=None,
                    url=None,
                    success=False,
                    error=data.get('error', "No metadata found"),
                    citation_form="full"
                ))
                continue
            
            # Case 3: Check if same URL as previous → ibid
            previous_url = history.get_previous_url()
            if current_url and previous_url and urls_match(current_url, previous_url):
                formatted = BaseFormatter.format_ibid()
                
                if note_type == 'endnote':
                    processor.write_endnote(note_id, formatted)
                else:
                    processor.write_footnote(note_id, formatted)
                
                results.append(ProcessedCitation(
                    original=original_text,
                    formatted=formatted,
                    metadata=history.get_previous_metadata(),
                    url=current_url,
                    success=True,
                    citation_form="ibid"
                ))
                continue
            
            # Case 4: Check if same source as previous → ibid
            if history.is_same_as_previous(metadata):
                formatted = BaseFormatter.format_ibid()
                
                if note_type == 'endnote':
                    processor.write_endnote(note_id, formatted)
                else:
                    processor.write_footnote(note_id, formatted)
                
                results.append(ProcessedCitation(
                    original=original_text,
                    formatted=formatted,
                    metadata=metadata,
                    url=current_url,
                    success=True,
                    citation_form="ibid"
                ))
                # Update history with current source
                history.add(metadata, formatted)
                continue
            
            # Case 5: Check if previously cited → short form
            if history.has_been_cited_before(metadata):
                formatted = formatter.format_short(metadata)
                
                if note_type == 'endnote':
                    processor.write_endnote(note_id, formatted)
                else:
                    processor.write_footnote(note_id, formatted)
                
                history.add(metadata, formatted)
                
                results.append(ProcessedCitation(
                    original=original_text,
                    formatted=formatted,
                    metadata=metadata,
                    url=current_url,
                    success=True,
                    citation_form="short"
                ))
                continue
            
            # Case 6: New source → full citation
            if note_type == 'endnote':
                processor.write_endnote(note_id, full_formatted)
            else:
                processor.write_footnote(note_id, full_formatted)
            
            history.add(metadata, full_formatted)
            
            results.append(ProcessedCitation(
                original=original_text,
                formatted=full_formatted,
                metadata=metadata,
                url=current_url,
                success=True,
                citation_form="full"
            ))
            
        except Exception as e:
            print(f"[process_document] Error processing {note_type} {note_id}: {e}")
            results.append(ProcessedCitation(
                original=original_text,
                formatted=original_text,
                metadata=None,
                url=None,
                success=False,
                error=str(e),
                citation_form="full"
            ))
    
    print(f"[process_document] Phase 2 complete: {len(results)} notes processed")
    
    # Save to buffer
    doc_buffer = processor.save_to_buffer()
    
    # Make URLs clickable if requested
    if add_links:
        doc_buffer = LinkActivator.process(doc_buffer)
    
    # Cleanup
    processor.cleanup()
    
    return doc_buffer.read(), results


# =============================================================================
# UPDATE SINGLE NOTE (for Workbench UI)
# Added: 2025-12-05 13:40
# =============================================================================

def update_document_note(doc_bytes: bytes, note_id: int, new_html: str) -> bytes:
    """
    Update a single endnote/footnote in a processed document.
    
    This function is used by the Workbench UI to update individual notes
    after manual editing.
    
    Args:
        doc_bytes: The current processed document as bytes
        note_id: The 1-based note ID to update
        new_html: The new HTML content for the note
        
    Returns:
        Updated document as bytes
    """
    import io
    import zipfile
    import tempfile
    import shutil
    import re
    
    try:
        # Extract the docx
        temp_dir = tempfile.mkdtemp()
        
        with zipfile.ZipFile(io.BytesIO(doc_bytes), 'r') as zf:
            zf.extractall(temp_dir)
        
        # Find and update the endnote
        endnotes_path = os.path.join(temp_dir, 'word', 'endnotes.xml')
        footnotes_path = os.path.join(temp_dir, 'word', 'footnotes.xml')
        
        updated = False
        
        for xml_path, note_tag in [(endnotes_path, 'w:endnote'), (footnotes_path, 'w:footnote')]:
            if not os.path.exists(xml_path):
                continue
            
            # Determine note type for styling
            note_type = 'footnote' if 'footnote' in xml_path else 'endnote'
                
            with open(xml_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find the note with matching ID
            # Pattern: <w:endnote w:id="N">...</w:endnote>
            pattern = rf'(<{note_tag}\s+[^>]*w:id="{note_id}"[^>]*>)(.*?)(</{note_tag}>)'
            
            def replace_note_content(match):
                open_tag = match.group(1)
                close_tag = match.group(3)
                
                # Convert HTML to Word XML with proper style
                word_xml = html_to_word_xml(new_html, note_type)
                
                return f"{open_tag}{word_xml}{close_tag}"
            
            new_content, count = re.subn(pattern, replace_note_content, content, flags=re.DOTALL)
            
            if count > 0:
                with open(xml_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                updated = True
                break
        
        # Repackage the docx
        output_buffer = io.BytesIO()
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zf.write(file_path, arcname)
        
        # Cleanup
        shutil.rmtree(temp_dir)
        
        output_buffer.seek(0)
        
        # Activate any URLs as clickable hyperlinks (use internal LinkActivator)
        output_buffer = LinkActivator.process(output_buffer)
        
        return output_buffer.read()
        
    except Exception as e:
        print(f"[update_document_note] Error: {e}")
        # Return original if update fails
        return doc_bytes


def html_to_word_xml(html: str, note_type: str = 'endnote') -> str:
    """
    Convert simple HTML to Word XML for endnote/footnote content.
    
    Handles:
    - <i>text</i> → italic runs
    - Plain text → normal runs
    - Applies proper paragraph style (EndnoteText/FootnoteText)
    - Includes endnoteRef/footnoteRef for superscript number
    
    Updated: 2025-12-06 - Added endnoteRef/footnoteRef to preserve superscript numbers
    """
    import re
    import html as html_module
    
    # Unescape HTML entities
    text = html_module.unescape(html)
    
    # Determine paragraph style and reference element based on note type
    if note_type == 'footnote':
        style_name = 'FootnoteText'
        ref_style = 'FootnoteReference'
        ref_element = '<w:footnoteRef/>'
    else:
        style_name = 'EndnoteText'
        ref_style = 'EndnoteReference'
        ref_element = '<w:endnoteRef/>'
    
    # Build Word XML paragraph
    runs = []
    
    # First run: the superscript reference number
    runs.append(f'<w:r><w:rPr><w:rStyle w:val="{ref_style}"/></w:rPr>{ref_element}</w:r>')
    
    # Add a space after the number
    runs.append('<w:r><w:t xml:space="preserve"> </w:t></w:r>')
    
    # Split by italic tags
    parts = re.split(r'(<i>.*?</i>)', text)
    
    for part in parts:
        if not part:
            continue
            
        if part.startswith('<i>') and part.endswith('</i>'):
            # Italic text
            inner = part[3:-4]
            inner_escaped = inner.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            runs.append(f'<w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve">{inner_escaped}</w:t></w:r>')
        else:
            # Normal text
            escaped = part.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            runs.append(f'<w:r><w:t xml:space="preserve">{escaped}</w:t></w:r>')
    
    # Wrap in paragraph WITH style
    return f'<w:p><w:pPr><w:pStyle w:val="{style_name}"/></w:pPr>{"".join(runs)}</w:p>'
