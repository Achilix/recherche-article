# Model Dispatch System Documentation

## Overview

The legal assistant now supports multi-provider LLM and embedding models. Users can switch between providers (Gemini, OpenAI, Azure OpenAI, Anthropic) and manage model configurations through the database.

## Architecture

### Model Resolution Flow

```
User Request (with model_name parameter)
    ↓
_find_model_by_name(model_name)
    ↓ (if found)
_call_llm_provider() / _call_embedding_provider()
    ↓ (if not found)
Fallback to Default (Gemini)
```

### Supported Providers

| Provider     | LLM Support | Embedding Support | Library        | Config Required                      |
| ------------ | ----------- | ----------------- | -------------- | ------------------------------------ |
| Gemini       | ✅          | ✅                | `google-genai` | `api_key`                            |
| OpenAI       | ✅          | ✅                | `openai`       | `api_key`                            |
| Azure OpenAI | ✅          | ✅                | `openai`       | `api_key`, `endpoint`, `api_version` |
| Anthropic    | ✅          | ❌                | `anthropic`    | `api_key`                            |

## Database Schema

Models are stored in `output/models/llm_models.sqlite3`:

```sql
CREATE TABLE IF NOT EXISTS llm_models (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL,  -- 'gemini', 'openai', 'azure-openai', 'anthropic'
    api_key TEXT,
    endpoint TEXT,           -- For Azure: https://xxx.openai.azure.com
    api_version TEXT,        -- For Azure: '2024-02-15-preview'
    temperature REAL DEFAULT 0.7,
    max_tokens INTEGER DEFAULT 4000,
    batch_size INTEGER DEFAULT 16,  -- For embeddings
    created_at TEXT,
    updated_at TEXT
);
```

## Adding Models to the Database

### Via Python (Runtime)

```python
from src.llm_models_store import add_model

# Add Gemini Model
add_model(
    name="gemini-2.5-flash",
    provider="gemini",
    api_key="AIzaSy...",
    temperature=0.7,
    max_tokens=4000
)

# Add OpenAI Model
add_model(
    name="gpt-4-turbo",
    provider="openai",
    api_key="sk-...",
    temperature=0.8,
    max_tokens=2000
)

# Add Azure OpenAI Model
add_model(
    name="gpt-4-azure-deployment",
    provider="azure-openai",
    api_key="azure-key-...",
    endpoint="https://company.openai.azure.com",
    api_version="2024-02-15-preview",
    temperature=0.7,
    max_tokens=4000
)

# Add Anthropic Model
add_model(
    name="claude-3-sonnet",
    provider="anthropic",
    api_key="sk-ant-...",
    temperature=0.7,
    max_tokens=4000
)
```

### Via CLI (Management Script)

```bash
python -m src.llm_models_store --add \
    --name "gemini-2.5-pro" \
    --provider "gemini" \
    --api-key "AIzaSy..." \
    --temperature 0.7 \
    --max-tokens 4000
```

### Via Environment Variables

Create a model from `.env` variables:

```bash
# .env
GEMINI_MODEL_NAME=gemini-2.5-flash
GEMINI_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...
GEMINI_TEMPERATURE=0.7
GEMINI_MAX_TOKENS=4000

OPENAI_MODEL_NAME=gpt-4-turbo
OPENAI_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_TEMPERATURE=0.8
OPENAI_MAX_TOKENS=2000
```

## Using Models in API Calls

### Report Generation

```typescript
// Angular Frontend (assistant.component.ts)
generateReport() {
    const payload = {
        articles: this.selectedArticles,
        title: this.reportTitle,
        model: "gpt-4-turbo",  // Specify which model to use
        api_key: null  // Leave null to use model's stored API key
    };

    this.http.post("/api/generate-report", payload).subscribe(
        (response) => { /* handle */ }
    );
}
```

### Python Backend

```python
from src.api_endpoints import generate_report_backend

articles = [...]
result = generate_report_backend(
    articles=articles,
    title="Legal Analysis",
    model="gpt-4-turbo",  # Model name from DB
    api_key=None  # Use model's stored key
)
```

### Report Vectorization

```python
from src.api_endpoints import vectorize_report_backend

result = vectorize_report_backend(
    report_id="rpt-123",
    title="Report Title",
    content="<div>Report HTML</div>",
    timestamp="2024-01-15T15:00:00",
    articles_count=5,
    model="text-embedding-3-small",  # OpenAI embeddings
    api_key=None
)
```

### Report Search

```python
from src.api_endpoints import search_reports_backend

results = search_reports_backend(
    query="employment law",
    top_k=10,
    model="gemini-embedding-001",  # Gemini embeddings
    api_key=None
)
```

## Fallback Behavior

If a requested model is **not found** in the database:

1. **For LLM calls**: Falls back to default Gemini model from `DEFAULT_VERIFY_MODEL`
2. **For embeddings**: Falls back to Gemini embedding model via `embed.py`
3. **Error handling**: Graceful degradation with appropriate error messages

```python
# If model not found, these fallback:
result = generate_report_backend(
    articles=articles,
    title="Title",
    model="unknown-model-xyz",  # Not in DB
    api_key=None
)
# Result: Uses DEFAULT_VERIFY_MODEL (gemini-2.5-flash)

result = vectorize_report_backend(
    report_id="rpt-123",
    title="Report",
    content="Content",
    timestamp="2024-01-15T15:00:00",
    articles_count=1,
    model="unknown-model-xyz",  # Not in DB
    api_key=None
)
# Result: Falls back to Gemini embedding via embed_texts()
```

## Provider-Specific Configuration

### Gemini (Google AI)

**Required:**

- `api_key`: Google API key

**Optional:**

- `temperature`: 0.0-2.0 (default: 0.7)
- `max_tokens`: 1-100000 (default: 4000)
- `batch_size`: Embedding batch size (default: 16)

**Model Examples:**

- `gemini-2.5-flash`: Fast model
- `gemini-2.5-pro`: More capable
- `gemini-embedding-001`: Embedding model

**Docs:** https://ai.google.dev/

---

### OpenAI

**Required:**

- `api_key`: OpenAI API key
- `name`: Deployment/model name

**Optional:**

- `temperature`: 0.0-2.0 (default: 0.7)
- `max_tokens`: 1-4096 (default: 4000)
- `batch_size`: Embedding batch size (default: 16)

**LLM Model Examples:**

- `gpt-4-turbo`
- `gpt-4`
- `gpt-3.5-turbo`

**Embedding Model Examples:**

- `text-embedding-3-small` (768 dims)
- `text-embedding-3-large` (3072 dims)

**Docs:** https://platform.openai.com/docs/

---

### Azure OpenAI

**Required:**

- `api_key`: Azure OpenAI API key
- `endpoint`: `https://<resource>.openai.azure.com`
- `api_version`: `2024-02-15-preview` (or later)
- `name`: Deployment name

**Optional:**

- `temperature`: 0.0-2.0 (default: 0.7)
- `max_tokens`: 1-4096 (default: 4000)

**Setup Example:**

```python
add_model(
    name="gpt-4-deployment",  # Your Azure deployment name
    provider="azure-openai",
    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
    endpoint="https://mycompany.openai.azure.com",
    api_version="2024-02-15-preview",
    temperature=0.7,
    max_tokens=4000
)
```

**Environment Setup:**

```bash
export AZURE_OPENAI_API_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://resource.openai.azure.com"
```

**Docs:** https://learn.microsoft.com/azure/ai-services/openai/

---

### Anthropic (Claude)

**Required:**

- `api_key`: Anthropic API key
- `name`: Claude model version

**Optional:**

- `temperature`: 0.0-1.0 (default: 0.7)
- `max_tokens`: 1-200000 (default: 4000)

**Model Examples:**

- `claude-3-sonnet-20240229`
- `claude-3-opus-20240229`
- `claude-3-haiku-20240307`

**Important:** Anthropic does **NOT** support embeddings. Use Gemini or OpenAI for embeddings.

**Docs:** https://docs.anthropic.com/

```python
# LLM only:
add_model(
    name="claude-3-sonnet",
    provider="anthropic",
    api_key="sk-ant-...",
    temperature=0.7,
    max_tokens=4000
)

# For embeddings, add a separate Gemini/OpenAI model:
add_model(
    name="gemini-embedding-001",
    provider="gemini",
    api_key="AIzaSy...",
    batch_size=16
)
```

## Testing

### Run Unit Tests

```bash
pytest src/test_model_dispatch.py -v
```

Tests cover:

- Model lookups
- Provider dispatch (Gemini, OpenAI, Azure, Anthropic)
- Embedding dispatch
- Error handling
- Fallback behavior

### Run Integration Tests

```bash
pytest src/test_model_endpoints.py -v
```

Tests cover:

- Full report generation workflow
- Vectorization with different models
- Search with model selection
- Error handling and edge cases
- Model persistence

### Manual Testing

```python
# Quick integration test
from src.api_endpoints import generate_report_backend

# Add test model first
from src.llm_models_store import add_model
add_model(
    name="test-gemini",
    provider="gemini",
    api_key=os.environ.get("GOOGLE_API_KEY"),
    temperature=0.5
)

# Test report generation
articles = [{"article_number": "1", "document_name": "Test", "content": "Content"}]
result = generate_report_backend(articles, "Test Report", model="test-gemini")
print("Status:", result.get("status"))
print("Report HTML:", result.get("report_html")[:100])
```

## Troubleshooting

### Model Not Found

```python
from src.llm_models_store import get_models
models = get_models()
for m in models:
    print(f"{m['name']} ({m['provider']})")
```

### API Key Issues

```
Error: "API key required"
→ Check that model config has api_key or env var is set
```

### Provider Library Missing

```
Error: "openai package is required"
→ Run: pip install openai
```

### Embedding Provider Mismatch

```
Error: "Anthropic provider does not support embeddings"
→ Use Gemini or OpenAI for embeddings, Anthropic only for LLM
```

### Azure Configuration

```
Error: "Azure OpenAI endpoint required"
→ Ensure model has endpoint and api_version fields
```

## Performance Tips

### Batch Size Optimization

For embeddings, adjust `batch_size` based on provider:

- **Gemini**: 16-32 (default: 16)
- **OpenAI**: 10-20 (default: 16, capped at 20)
- **Azure**: Same as OpenAI

```python
add_model(
    name="gemini-fast-embedding",
    provider="gemini",
    api_key="AIzaSy...",
    batch_size=32  # Larger batches = faster but more API cost
)
```

### Model Selection for Different Use Cases

**Speed Priority:**

- LLM: `gpt-3.5-turbo` (OpenAI) or `gemini-2.5-flash` (Gemini)
- Embeddings: `text-embedding-3-small` (OpenAI)

**Quality Priority:**

- LLM: `gpt-4-turbo` (OpenAI) or `claude-3-sonnet` (Anthropic)
- Embeddings: `text-embedding-3-large` (OpenAI)

**Cost Efficiency:**

- LLM: `gemini-2.5-flash` (Gemini - free tier available)
- Embeddings: `gemini-embedding-001` (Gemini - free tier available)

## Future Extensions

### Adding New Providers

To add a new provider (e.g., Cohere, Replicate):

1. **Update `_call_llm_provider()` in `api_endpoints.py`:**

```python
elif provider == 'cohere':
    if cohere is None:
        raise Exception('cohere package required')
    client = cohere.Client(api_key=api_key)
    resp = client.generate(prompt=prompt, model=model_name)
    return resp.generations[0].text
```

2. **Update `_call_embedding_provider()` in `api_endpoints.py`:**

```python
elif provider == 'cohere':
    if cohere is None:
        raise Exception('cohere package required')
    client = cohere.Client(api_key=api_key)
    embeddings = client.embed(texts=texts, model=model_name)
    return embeddings.embeddings
```

3. **Add provider import at top of `api_endpoints.py`**
4. **Add tests to `test_model_dispatch.py`**
5. **Update this documentation**

### Multi-Model Routing

Future: Route different request types to different models:

- Fast requests → faster model
- Long requests → more capable model
- Cost-sensitive → budget model

## API Reference

### \_find_model_by_name(model_name: str) → dict | None

Find model config by name (case-insensitive).

### \_call_llm_provider(model_config: dict, prompt: str, override_api_key: str = None) → str

Dispatch LLM call to provider. Returns generated text.

### \_call_embedding_provider(model_config: dict, texts: List[str], override_api_key: str = None) → List[List[float]]

Dispatch embedding call to provider. Returns list of embedding vectors.

### generate_report_backend(articles, title, model, api_key=None) → dict

Generate report using specified model. Handles both LLM and vectorization.

### vectorize_report_backend(report_id, title, content, timestamp, articles_count, api_key=None, model=None) → dict

Vectorize and store report using specified embedding model.

### search_reports_backend(query, top_k=10, api_key=None, model=None) → dict

Search reports using specified embedding model.
