# Citate Genie 2.0

**Intelligent Citation Processing with International URL Support**

Citate Genie transforms hand-typed citations into properly formatted academic citations (Chicago, APA, MLA) by automatically fetching authoritative metadata from multiple sources.

## 🚀 What's New in 2.0

- **URL Routing System**: Intelligent classification and routing for URLs
- **5 Specialized Engines**: arXiv, Wikipedia, YouTube, Newspaper, Government
- **International Support**: US, UK, Canada, Australia, New Zealand, Ireland, EU
- **166 Newspaper Domains**: NYT, Guardian, Globe and Mail, SMH, and more
- **269 Government Agencies**: FDA, NHS, Health Canada, CSIRO, EC, and more
- **73 Legal Sites**: CanLII, BAILII, AustLII, EUR-Lex, and more

## 📁 Project Structure

```
citate_genie/
├── app.py                  # Flask web application
├── config.py               # Domain lists, API keys, settings
├── models.py               # CitationMetadata, CitationType
├── detectors.py            # Citation type detection
├── extractors.py           # Text extraction utilities
├── document_processor.py   # Word document processing
├── claude_router.py        # Claude AI integration
├── url_router.py           # URL routing orchestrator
│
├── engines/
│   ├── url_router.py       # URL classification & routing
│   ├── generic_url_engine.py   # HTML metadata scraping
│   ├── arxiv_engine.py     # arXiv API integration
│   ├── wikipedia_engine.py # MediaWiki API integration
│   ├── youtube_engine.py   # YouTube/Vimeo oEmbed
│   ├── academic.py         # Crossref, OpenAlex, PubMed
│   ├── google_scholar.py   # Google Scholar scraping
│   ├── google_cse.py       # Google Books, Custom Search
│   ├── books.py            # Book metadata engines
│   ├── superlegal.py       # Legal citation handling
│   ├── doi.py              # DOI extraction & resolution
│   └── famous_papers.py    # Cached famous paper metadata
│
├── formatters/
│   ├── chicago.py          # Chicago Manual of Style
│   ├── apa.py              # APA 7th Edition
│   ├── mla.py              # MLA 9th Edition
│   └── legal.py            # Legal citation formatting
│
├── templates/
│   └── index.html          # Web interface
│
├── requirements.txt        # Python dependencies
├── Procfile               # Railway/Heroku deployment
└── railway.json           # Railway configuration
```

## 🔧 Installation

```bash
# Clone the repository
git clone https://github.com/caplane/Citate-Genie-2.0.git
cd Citate-Genie-2.0

# Create virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run locally
python app.py
```

## 🌐 URL Routing System

The new URL routing system automatically detects and processes:

| URL Type | Detection | Engine |
|----------|-----------|--------|
| DOI | `doi.org/`, `/doi/` in path | CrossrefEngine |
| arXiv | `arxiv.org/abs/` | ArxivEngine |
| PubMed | `pubmed.ncbi.nlm.nih.gov/` | PubMedEngine |
| Wikipedia | `wikipedia.org/wiki/` | WikipediaEngine |
| YouTube | `youtube.com`, `youtu.be` | YouTubeEngine |
| JSTOR | `jstor.org/stable/` | (identifier extracted) |
| SSRN | `ssrn.com/abstract=` | (identifier extracted) |
| Newspapers | 166 domains | NewspaperEngine |
| Government | 269 domains | GovernmentEngine |
| Legal | 73 domains | LegalEngine |

## 🌍 International Coverage

### Government Domains
- **United States**: `.gov` (60+ agencies)
- **United Kingdom**: `.gov.uk`, `nhs.uk`, `parliament.uk`
- **Canada**: `.gc.ca`, `.canada.ca`, 13 provincial patterns
- **Australia**: `.gov.au`, 8 state patterns, `csiro.au`
- **New Zealand**: `.govt.nz`, `parliament.nz`
- **Ireland**: `.gov.ie`, `oireachtas.ie`
- **European Union**: `.europa.eu` (22 institutions)

### Newspapers
- **US**: NYT, WaPo, WSJ, Atlantic, New Yorker, CNN (50+ outlets)
- **UK**: Guardian, BBC, Telegraph, FT, Economist (24 outlets)
- **Canada**: Globe and Mail, Toronto Star, CBC (21 outlets)
- **Australia**: SMH, The Australian, ABC (20 outlets)
- **New Zealand**: NZ Herald, Stuff, RNZ (13 outlets)
- **Ireland**: Irish Times, Independent, RTÉ (10 outlets)

## 🧪 Testing

```bash
# Run the stress test suite
python stress_test.py
```

## 📦 Deployment (Railway)

1. Connect your GitHub repository to Railway
2. Railway will auto-detect the Procfile
3. Set environment variables in Railway dashboard:
   - `ANTHROPIC_API_KEY` (for Claude integration)
   - `GOOGLE_API_KEY` (for Google Books/CSE)

## 📄 License

MIT License

## 👨‍💻 Author

Eric Caplan - Wesleyan University

---

*Built with the assistance of Claude (Anthropic)*
