# Legal Semantic Search (Python API + Angular Frontend)

## Overview

An intelligent legal research assistant that processes legal PDFs from French legislation, extracts articles into structured records, generates semantic embeddings, and provides advanced semantic search capabilities through a Python API and Angular frontend.

This system allows legal professionals and researchers to perform intelligent semantic searches across multiple French legal codes and legislation, returning contextually relevant articles rather than simple keyword matches.

**Key Features:**

- 📄 PDF extraction and article parsing from French legal documents
- 🔍 Semantic search powered by AI embeddings (Google Gemini)
- 💾 Persistent vector storage using ChromaDB
- 🎯 Intelligent question generation for legal articles
- 🔐 User authentication and session management
- 🚀 REST API backend with Angular frontend
- 📊 Support for multiple legal codes (Commercial, Labor, Criminal Procedure, etc.)

## Project Layout

### Backend (Python)

| File                                                   | Purpose                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------ |
| [src/app.py](src/app.py)                               | Extract legal articles from PDFs into JSON/CSV format        |
| [src/add_ids.py](src/add_ids.py)                       | Add stable, persistent IDs to articles for tracking          |
| [src/semantic_chunk.py](src/semantic_chunk.py)         | Semantic chunking pipeline for context-aware splitting       |
| [src/embed.py](src/embed.py)                           | Generate embeddings using Google Gemini and sync to ChromaDB |
| [src/embed_missing.py](src/embed_missing.py)           | Backfill missing embeddings and sync ChromaDB                |
| [src/generate_questions.py](src/generate_questions.py) | AI-powered question generation for articles                  |
| [src/chroma_store.py](src/chroma_store.py)             | ChromaDB persistence layer and helper functions              |
| [src/recherche.py](src/recherche.py)                   | CLI semantic search utility over ChromaDB                    |
| [src/api.py](src/api.py)                               | FastAPI backend providing REST endpoints for frontend        |
| [src/auth_store.py](src/auth_store.py)                 | User authentication and session management                   |
| [src/llm_models_store.py](src/llm_models_store.py)     | LLM model configuration and management                       |

### Frontend (Angular)

| Directory                                                                | Purpose                                   |
| ------------------------------------------------------------------------ | ----------------------------------------- |
| [App-assistante-juridique-ia/](App-assistante-juridique-ia/)             | Angular 19 single-page application        |
| [App-assistante-juridique-ia/src/](App-assistante-juridique-ia/src/app/) | Component structure and application logic |

### Data Storage

| Directory                                | Contents                              |
| ---------------------------------------- | ------------------------------------- |
| [output/embeddings/](output/embeddings/) | Generated embedding JSON files        |
| [output/extracted/](output/extracted/)   | Extracted articles in JSON/CSV format |
| [output/questions/](output/questions/)   | Generated questions for each article  |
| [output/chromadb/](output/chromadb/)     | Persistent ChromaDB vector store      |
| [output/auth/](output/auth/)             | User authentication database (SQLite) |
| [output/models/](output/models/)         | LLM models configuration              |
| [pdfs/](pdfs/)                           | Source PDF files to process           |

## Requirements

### System Requirements

- **Python**: 3.10 or higher
- **Node.js**: 20+ (for Angular frontend)
- **Operating System**: Windows, macOS, or Linux
- **API Key**: Google Gemini API key for embeddings and AI features

### Python Dependencies

The project uses the following key libraries:

- `chromadb` - Vector database for semantic search
- `pandas` - Data manipulation and analysis
- `pymupdf` - PDF extraction and parsing
- `fastapi` - REST API framework
- `google-generativeai` - Google Gemini API integration
- `numpy` - Numerical computing

### Quick Start

1. **Install Python dependencies** from project root:

```bash
pip install -r requirements.txt
```

2. **Install Node.js dependencies** (for Angular frontend):

```bash
cd App-assistante-juridique-ia
npm install
```

3. **Set up environment variables** (see Configuration section below)

## Configuration

### API Keys and Authentication

Create a `.env` file in the repository root with your configuration:

```env
# Required: Google Gemini API Key
# Get from: https://makersuite.google.com/app/apikey
GOOGLE_API_KEY=your_key_here

# Alternative names (any one works)
GEMINI_API_KEY=your_key_here
```

**Note**: The Google Gemini API is used for:

- Generating embeddings for semantic search
- Generating AI-powered questions for articles
- Semantic chunking of long articles

### Optional Settings

Configure data storage locations via environment variables:

```env
# Source directory for embedded JSON files
EMBEDDINGS_DIR=output/embeddings

# ChromaDB persistent storage directory
CHROMADB_DIR=output/chromadb

# ChromaDB collection name for legal articles
CHROMADB_COLLECTION=legal_articles

# Frontend API base URL (see App-assistante-juridique-ia/.env.local.example)
REACT_APP_API_URL=http://localhost:8000
```

### Database Configuration

- **User Authentication**: SQLite database at `output/auth/users.sqlite3`
- **LLM Models**: SQLite database at `output/models/llm_models.sqlite3`
- **Vector Store**: ChromaDB at `output/chromadb/`

## Data Pipeline

The system follows a multi-stage pipeline to process legal documents:

### Stage 1: PDF Extraction

Extract articles from all PDFs in the `pdfs/` directory:

```bash
python src/app.py
```

**Output**: JSON and CSV files in `output/extracted/`
**What it does**:

- Parses PDF documents
- Extracts individual legal articles
- Preserves article structure and hierarchy
- Generates JSON and CSV formats for flexibility

**Supported Legal Documents**:

- Code du Travail (Labor Code)
- Code de Commerce (Commercial Code)
- Code de Procédure Pénale (Criminal Procedure Code)
- Dahir and Moroccan legislation
- BAM (Bank Al-Maghrib) regulations

### Stage 2: Add Article IDs

Add stable, persistent IDs to articles for tracking:

```bash
python src/add_ids.py output/extracted --output-dir output/with_ids
```

**Output**: Articles with unique IDs in `output/with_ids/`
**Why**: Ensures consistent article identification across updates

### Stage 3: Semantic Chunking (Optional)

For long articles, perform intelligent semantic chunking:

```bash
python src/semantic_chunk.py output/extracted/codedecommerce_articles.json
```

**When to use**: When articles are too long for effective embedding
**Output**: Chunked versions preserving semantic meaning

### Stage 4: Generate Embeddings

Create vector embeddings and sync to ChromaDB:

```bash
python src/embed.py output/extracted/codedecommerce_articles.json
```

**Output**:

- Embedded JSON files in `output/embeddings/`
- Synchronized ChromaDB index in `output/chromadb/`

**Process**:

1. Reads extracted articles
2. Generates embeddings using Google Gemini
3. Stores embeddings locally as JSON
4. Syncs to ChromaDB for semantic search

### Stage 5: Backfill Missing Embeddings (Optional)

Update only missing embeddings without reprocessing everything:

```bash
python src/embed_missing.py output/embeddings/codedecommerce_articles_embedded.json
```

**Use case**: When adding new articles or updating existing data

### Stage 6: Generate Questions

AI-powered question generation for learning and testing:

```bash
python src/generate_questions.py output/extracted/codedecommerce_articles.json
```

**Output**: Generated questions in `output/questions/`

### Stage 7: Search via CLI

Test semantic search from command line:

```bash
python src/recherche.py output/embeddings -q "obligation d'ouvrir un compte"
```

**Features**:

- Free-text semantic search
- Returns relevant articles with similarity scores
- Direct query testing without API

The first search automatically rebuilds [output/chromadb](output/chromadb) if it is empty.

## Run Backend API

From repository root:

```bash
python src/api.py
```

API endpoints:

- `GET /` returns JSON service info (no built-in UI)
- `GET /health` service + embeddings status
- `GET /search?...` query via URL
- `POST /search` query via JSON body

### Example Search Request

```json
{
  "query": "obligation d'ouvrir un compte",
  "top_k": 5,
  "threshold": 0.2,
  "close_filter": "balanced",
  "verify_results": false
}
```

`close_filter` values:

- `off`
- `loose`
- `balanced`
- `strict`

Effective filter rule:

$$
effective\_threshold = \max(threshold, close\_filter\_threshold)
$$

Notes:

- Search queries the persistent ChromaDB index; the only API call is the single Gemini embedding request for the query.
- Query embedding uses one Gemini embedding request per search.
- Optional AI verification (`verify_results=true`) uses Gemini `generate_content` and may hit quota.
- If verification fails (quota/rate limit), API still returns similarity-ranked results.

## Run Frontend

1. In one terminal (root):

```bash
python src/api.py
```

2. In another terminal:

```bash
cd frontend
npm install
npm run dev
```

3. Open:

```text
http://localhost:3000
```

## CLI Search

Search all embeddings in folder:

```bash
python src/recherche.py output/embeddings -q "obligation d'ouvrir un compte"
```

Search only one embedded file:

```bash
python src/recherche.py output/embeddings/4_ONC_Law_fr-FR_articles_embedded.json -q "obligations"
```

## Troubleshooting

- `Google API key required`: verify [\.env](.env) has a valid key.
- `Embeddings source not found`: check [output/embeddings](output/embeddings) or pass `--embeddings-dir`.
- `No module named chromadb`: install dependencies in the same Python environment used to run the API (`pip install -r requirements.txt`).
- `429 RESOURCE_EXHAUSTED`: disable `verify_results` or retry later.
- PowerShell `curl` alias issues: use `curl.exe`.
- API returns relative source file paths by design.

## System Architecture

### Component Overview

```
┌────────────────────────────────────────────────────────────────┐
│                  User Interface Layer                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │    Angular 19 Frontend (Port 4200)                       │  │
│  │  - Search Interface                                      │  │
│  │  - User Authentication & Dashboard                       │  │
│  │  - Article Display & Highlighting                        │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
                             ▲
                             │ HTTP/REST
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                  Application Layer                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │    FastAPI Backend (Port 8000)                           │  │
│  │  - REST API Endpoints                                    │  │
│  │  - Authentication & Authorization                        │  │
│  │  - Search Orchestration                                  │  │
│  │  - Vector Embedding Integration                          │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
       ▲              ▲              ▲              ▲
       │              │              │              │
       ▼              ▼              ▼              ▼
  ┌─────────┐  ┌─────────────┐ ┌──────────┐ ┌──────────┐
  │ChromaDB │  │ SQLite DBs  │ │JSON Files│ │ Gemini   │
  │(Vector  │  │- Auth       │ │(Embed-   │ │ API      │
  │  Store) │  │- Models     │ │ dings)   │ │(Embed-   │
  └─────────┘  └─────────────┘ └──────────┘ └──────────┘
```

### Data Flow Diagram

```
PDF Files in pdfs/
     │
     ▼
[src/app.py] ─── Extract Articles ─── JSON/CSV
     │                                   │
     │                                   ▼
     │                              [src/add_ids.py] ─── Add IDs
     │                                   │
     │                                   ▼
     │                              [src/semantic_chunk.py]
     │                              (Optional chunking)
     │                                   │
     └───────────────────────────────────┘
                   │
                   ▼
          [src/embed.py]
          Generate Embeddings
          (Google Gemini API)
                   │
      ┌────────────┴────────────┐
      ▼                         ▼
  JSON Files            ChromaDB Store
(output/embeddings)     (output/chromadb)
      │                         │
      │                         └─── Persistent Vector Index
      │
      ├─── [src/recherche.py] ─── CLI Search
      │
      ├─── [src/api.py] ─── FastAPI
      │         │
      │         └─── Angular Frontend
      │
      └─── [src/generate_questions.py] ─── Questions
           (Output to output/questions)
```

## Features Overview

### 🔍 Semantic Search

- **Vector-based matching**: Uses AI embeddings for semantic similarity
- **Context awareness**: Understands legal concepts beyond keyword matching
- **Relevance scoring**: Ranks results by semantic relevance
- **Similarity threshold**: Configurable thresholds for result filtering

**Supported Filter Strategies**:

- `off`: No filtering, return all results
- `loose`: Minimal filtering, broad results
- `balanced`: Default filtering, balanced precision/recall
- `strict`: Aggressive filtering, only top matches

### 📄 Document Processing

- **Multi-format support**: PDF extraction and parsing
- **Article structuring**: Preserves legal document hierarchy
- **Metadata extraction**: Captures article numbers, sections, subsections
- **Stable ID generation**: Consistent article identification

### 🤖 AI Capabilities

- **Embedding generation**: Using Google Gemini for semantic embeddings
- **Question generation**: AI-powered synthesis of assessment questions
- **Semantic chunking**: Intelligent splitting of long articles
- **Content verification**: Optional AI-powered result validation

### 👤 User Management

- **Authentication**: Secure user registration and login
- **Session management**: Persistent user sessions
- **Search history**: Track user queries and results
- **User preferences**: Customizable settings per user

### 📊 Data Management

- **ChromaDB integration**: Vector database for scalable search
- **Persistent storage**: Survives application restarts
- **Batch processing**: Efficient handling of multiple documents
- **Version control**: ID-based tracking of article versions

## Supported Legal Documents

### French Legal Codes

| Code  | Full Name                          | Status | Coverage                           |
| ----- | ---------------------------------- | ------ | ---------------------------------- |
| CC    | Code de Commerce (Commercial Code) | ✅     | Commercial transactions, contracts |
| CT    | Code du Travail (Labor Code)       | ✅     | Employment, labor relations        |
| CPP   | Code de Procédure Pénale           | ✅     | Criminal procedure, investigations |
| 4-ONC | 4-ONC Law (French)                 | ✅     | Specialized legal framework        |
| Dahir | Dahir 1-00-175 / Loi 15-95         | ✅     | Moroccan legislation               |
| BAM   | Bank Al-Maghrib Regulations        | ✅     | Banking and finance regulations    |

**Total Coverage**: 1000+ legal articles with embedded vectors

## Quick Reference

### Common Commands

```bash
# Full pipeline
python src/app.py
python src/add_ids.py output/extracted --output-dir output/with_ids
python src/embed.py output/extracted/codedecommerce_articles.json

# API server
python src/api.py

# Frontend
cd App-assistante-juridique-ia && npm start

# CLI search
python src/recherche.py output/embeddings -q "your query"

# Generate questions
python src/generate_questions.py output/extracted/codedecommerce_articles.json
```

### Environment Setup

```bash
# Clone repo
git clone <repo-url>
cd project

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Unix

# Install dependencies
pip install -r requirements.txt
cd App-assistante-juridique-ia && npm install

# Configure
# Create .env with GOOGLE_API_KEY=...
# Create App-assistante-juridique-ia/.env.local with API URL
```

## Development & Deployment

### Local Development

1. **Backend development**:

   ```bash
   python src/api.py
   ```

   - Uvicorn auto-reload on code changes
   - Access docs at `http://localhost:8000/docs`

2. **Frontend development**:
   ```bash
   cd App-assistante-juridique-ia
   npm start
   ```

   - Angular CLI dev server with live reload
   - Connects to local API

### Production Deployment

1. **Build frontend**:

   ```bash
   cd App-assistante-juridique-ia
   npm run build
   ```

   - Output: `dist/` folder

2. **Configure API**:
   - Set production environment variables
   - Enable CORS for frontend domain
   - Configure database paths

3. **Deploy**:
   - Use container (Docker recommended)
   - Or traditional server deployment
   - Ensure ChromaDB persistence across restarts

## Performance Characteristics

### Indexing

- **Time per article**: ~2-3 seconds (with Google Gemini API)
- **Memory per 1000 articles**: ~500MB (ChromaDB + embeddings)
- **Batch processing**: 50+ articles in parallel recommended

### Search

- **Query embedding**: ~1 second (first call), <100ms cached
- **ChromaDB search**: <50ms for 1000 articles
- **Result return**: <100ms average
- **Total latency**: ~1-2 seconds per query

### Scalability

- **Articles**: Tested up to 10,000+
- **Concurrent users**: Depends on server resources
- **Storage**: ~1MB per 100 embedded articles

## Known Limitations & Future Work

### Current Limitations

- ⚠️ French language focus (extensible to other languages)
- ⚠️ Single vector model (can add multi-model support)
- ⚠️ No advanced filtering (e.g., by article type, date range)
- ⚠️ Basic authentication (can enhance with OAuth)

### Planned Features

- 🗓️ Multi-language document support
- 🗓️ Advanced search filters and faceting
- 🗓️ Document versioning and change tracking
- 🗓️ Integration with external legal databases
- 🗓️ Mobile application
- 🗓️ Custom LLM fine-tuning

## Support & Contributing

### Issues & Bugs

Report issues with:

- System information (OS, Python version)
- Steps to reproduce
- Error messages or logs
- Expected vs actual behavior

### Contributing

Contributions welcome in:

- Bug fixes
- Performance optimizations
- New legal code support
- UI/UX enhancements
- Documentation improvements

### Questions?

- Check troubleshooting section first
- Review existing issues
- Document problems with detailed context

## License

Proprietary and Confidential. All rights reserved.
