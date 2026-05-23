"""
Clean backend endpoints for the legal assistant.
All business logic is here - the frontend just calls these endpoints.
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List
from io import BytesIO
from datetime import datetime

try:
	import google.genai as genai
except ImportError:
	genai = None

try:
	import psycopg
except Exception:
	psycopg = None
try:
	from reportlab.lib.pagesizes import A4
	from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
	from reportlab.lib.units import inch
	from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether
	from reportlab.lib import colors
	from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
except ImportError:
	print("WARNING: reportlab not installed. PDF generation will fail.")

try:
	from .recherche import DEFAULT_MODEL, _resolve_api_key, search_articles, _load_env_file
except ImportError:
	from recherche import DEFAULT_MODEL, _resolve_api_key, search_articles, _load_env_file

try:
	from .embed import embed_texts
except ImportError:
	from embed import embed_texts

try:
	from .chroma_store import _load_collection
except ImportError:
	from chroma_store import _load_collection

_load_env_file(Path.cwd() / ".env")
EMBEDDINGS_SOURCE = Path(os.environ.get("EMBEDDINGS_DIR", "output/embeddings")).resolve()
DEFAULT_VERIFY_MODEL = "gemini-2.5-flash"
MAX_REPORT_ARTICLES = int(os.environ.get("REPORT_MAX_ARTICLES", "5"))
MAX_REPORT_CHARS_PER_ARTICLE = int(os.environ.get("REPORT_MAX_CHARS_PER_ARTICLE", "1200"))
REPORT_STORAGE_WAIT_SECONDS = float(os.environ.get("REPORT_STORAGE_WAIT_SECONDS", "0.25"))


try:
	from .llm_models_store import get_models
except ImportError:
	from llm_models_store import get_models

try:
	import openai
except Exception:
	openai = None

try:
	import anthropic
except Exception:
	anthropic = None


def _find_model_by_name(model_name: str):
	"""Find a model config by name (case-insensitive)."""
	if not model_name:
		return None
	models = get_models()
	for m in models:
		if str(m.get('name', '')).lower() == str(model_name).lower():
			return m
	# Also allow provider-prefixed names like 'openai:gpt-4'
	for m in models:
		if str(m.get('name', '')).lower().endswith(str(model_name).lower()):
			return m
	return None


def _call_llm_provider(model_config: dict, prompt: str, override_api_key: str = None) -> str:
	"""Dispatch to provider-specific LLM calls. Returns text output or raises Exception."""
	provider = (model_config.get('provider') or '').lower()
	if provider == 'azure':
		provider = 'azure-openai'
	api_key = override_api_key or model_config.get('api_key') or os.environ.get('GOOGLE_API_KEY') or os.environ.get('OPENAI_API_KEY')
	model_name = str(model_config.get('name') or '')

	if provider == 'gemini':
		if genai is None:
			raise Exception('google-genai library is required for Gemini provider')
		client = genai.Client(api_key=api_key)
		response = client.models.generate_content(
			model=f"models/{model_name}" if not model_name.startswith('models/') else model_name,
			contents=prompt,
		)
		return getattr(response, 'text', '') or ''
	elif provider == 'openai':
		if openai is None:
			raise Exception('openai package is required for OpenAI provider')
		# Use chat completion style
		openai.api_key = api_key
		resp = openai.ChatCompletion.create(
			model=model_name,
			messages=[{"role": "user", "content": prompt}],
			temperature=float(model_config.get('temperature', 0.7)),
			max_tokens=int(model_config.get('max_tokens', 4000)),
		)
		# Extract text
		if isinstance(resp, dict):
			choices = resp.get('choices')
			if choices and len(choices) > 0:
				return choices[0].get('message', {}).get('content', '') or choices[0].get('text', '') or ''
		# For other OpenAI client shapes
		return getattr(resp, 'choices', [None])[0].get('message', {}).get('content', '') if getattr(resp, 'choices', None) else ''
	elif provider == 'azure-openai':
		if openai is None:
			raise Exception('openai package is required for Azure OpenAI provider')
		# Azure OpenAI uses deployment name as model_name
		azure_endpoint = model_config.get('endpoint') or os.environ.get('AZURE_OPENAI_ENDPOINT')
		if not azure_endpoint:
			raise Exception('Azure OpenAI endpoint required. Set in model config or AZURE_OPENAI_ENDPOINT env')
		openai.api_type = 'azure'
		openai.api_key = api_key
		openai.api_base = azure_endpoint
		openai.api_version = model_config.get('api_version', '2024-02-15-preview')
		resp = openai.ChatCompletion.create(
			engine=model_name,
			messages=[{"role": "user", "content": prompt}],
			temperature=float(model_config.get('temperature', 0.7)),
			max_tokens=int(model_config.get('max_tokens', 4000)),
		)
		if isinstance(resp, dict):
			choices = resp.get('choices')
			if choices and len(choices) > 0:
				return choices[0].get('message', {}).get('content', '') or choices[0].get('text', '') or ''
		return getattr(resp, 'choices', [None])[0].get('message', {}).get('content', '') if getattr(resp, 'choices', None) else ''
	elif provider == 'anthropic':
		if anthropic is None:
			raise Exception('anthropic package is required for Anthropic provider')
		client = anthropic.Anthropic(api_key=api_key)
		resp = client.messages.create(
			model=model_name,
			max_tokens=int(model_config.get('max_tokens', 4000)),
			messages=[{"role": "user", "content": prompt}],
		)
		if hasattr(resp, 'content') and len(resp.content) > 0:
			return resp.content[0].text or ''
		return ''
	else:
		raise Exception(f'Provider "{provider}" not implemented')


def _call_embedding_provider(model_config: dict, texts: List[str], override_api_key: str = None) -> List[List[float]]:
	"""Dispatch to provider-specific embedding calls. Returns list of embedding vectors."""
	provider = (model_config.get('provider') or '').lower()
	api_key = override_api_key or model_config.get('api_key') or os.environ.get('GOOGLE_API_KEY') or os.environ.get('OPENAI_API_KEY')
	model_name = str(model_config.get('name') or '')
	embedding_model_name = model_name if 'embed' in model_name.lower() else DEFAULT_MODEL

	if not api_key:
		raise Exception('API key required for embeddings')

	if provider == 'gemini':
		if genai is None:
			raise Exception('google-genai library is required for Gemini provider')
		client = genai.Client(api_key=api_key)
		model_ref = f"models/{embedding_model_name}" if not embedding_model_name.startswith('models/') else embedding_model_name
		
		result = [[] for _ in texts]
		non_empty_items = [(idx, str(text or '')) for idx, text in enumerate(texts) if str(text or '').strip()]
		batch_size = int(model_config.get('batch_size', 16))
		
		for start in range(0, len(non_empty_items), batch_size):
			batch_items = non_empty_items[start:start + batch_size]
			batch_texts = [text for _, text in batch_items]
			try:
				resp = client.models.embed_content(model=model_ref, contents=batch_texts)
				if not resp.embeddings:
					raise Exception('No embeddings returned')
				for (orig_idx, _), emb in zip(batch_items, resp.embeddings):
					if hasattr(emb, 'values'):
						result[orig_idx] = list(emb.values)
					elif isinstance(emb, (list, tuple)):
						result[orig_idx] = list(emb)
					else:
						raise Exception(f'Unexpected embedding type: {type(emb)}')
			except Exception as e:
				raise Exception(f'Gemini embedding error: {e}')
		return result
	
	elif provider == 'openai':
		if openai is None:
			raise Exception('openai package is required for OpenAI provider')
		openai.api_key = api_key
		
		result = [[] for _ in texts]
		non_empty_items = [(idx, str(text or '')) for idx, text in enumerate(texts) if str(text or '').strip()]
		batch_size = int(model_config.get('batch_size', 16))
		
		for start in range(0, len(non_empty_items), batch_size):
			batch_items = non_empty_items[start:start + batch_size]
			batch_texts = [text for _, text in batch_items]
			try:
				resp = openai.Embedding.create(model=model_name, input=batch_texts)
				if isinstance(resp, dict):
					data = resp.get('data', [])
					for item in data:
						idx_in_batch = item.get('index', 0)
						orig_idx = batch_items[idx_in_batch][0] if idx_in_batch < len(batch_items) else 0
						result[orig_idx] = item.get('embedding', [])
				else:
					for item in resp.data:
						idx_in_batch = item.index
						orig_idx = batch_items[idx_in_batch][0] if idx_in_batch < len(batch_items) else 0
						result[orig_idx] = item.embedding
			except Exception as e:
				raise Exception(f'OpenAI embedding error: {e}')
		return result
	
	elif provider == 'azure-openai':
		if openai is None:
			raise Exception('openai package is required for Azure OpenAI provider')
		azure_endpoint = model_config.get('endpoint') or os.environ.get('AZURE_OPENAI_ENDPOINT')
		if not azure_endpoint:
			raise Exception('Azure OpenAI endpoint required')
		openai.api_type = 'azure'
		openai.api_key = api_key
		openai.api_base = azure_endpoint
		openai.api_version = model_config.get('api_version', '2024-02-15-preview')
		
		result = [[] for _ in texts]
		non_empty_items = [(idx, str(text or '')) for idx, text in enumerate(texts) if str(text or '').strip()]
		batch_size = int(model_config.get('batch_size', 16))
		
		for start in range(0, len(non_empty_items), batch_size):
			batch_items = non_empty_items[start:start + batch_size]
			batch_texts = [text for _, text in batch_items]
			try:
				resp = openai.Embedding.create(engine=model_name, input=batch_texts)
				if isinstance(resp, dict):
					data = resp.get('data', [])
					for item in data:
						idx_in_batch = item.get('index', 0)
						orig_idx = batch_items[idx_in_batch][0] if idx_in_batch < len(batch_items) else 0
						result[orig_idx] = item.get('embedding', [])
			except Exception as e:
				raise Exception(f'Azure OpenAI embedding error: {e}')
		return result
	
	elif provider == 'anthropic':
		if anthropic is None:
			raise Exception('anthropic package is required for Anthropic provider')
		raise Exception('Anthropic provider does not support embeddings. Use Gemini or OpenAI.')
	else:
		raise Exception(f'Provider "{provider}" not implemented for embeddings')




def generate_report_backend(
	articles: List[Dict[str, str]],
	title: str,
	model: str,
	api_key: str = None
) -> Dict[str, Any]:
	"""
	Generate legal report from articles - BACKEND ONLY.
	All LLM interaction happens here, not in frontend.
	"""
	# Build article summaries for LLM
	candidates = []
	for idx, a in enumerate(articles[:MAX_REPORT_ARTICLES]):
		candidates.append({
			"index": idx,
			"article_number": a.get('article_number', ''),
			"document_name": a.get('document_name', ''),
			"content": str(a.get('content', ''))[:MAX_REPORT_CHARS_PER_ARTICLE],
		})

	truncated_articles = max(0, len(articles) - len(candidates))

	prompt = (
		"Vous êtes un juriste expert. Rédigez un rapport juridique en français, clair et très structuré, à partir des passages fournis. "
		"Le rapport doit commencer par une réponse directe à la question de l'utilisateur, puis présenter chaque passage avec une sous-section dédiée. "
		"Pour chaque passage, utilisez exactement cette structure: un titre <h2> avec le document ou l'article, un court paragraphe 'Réponse', un paragraphe 'Portée juridique', une liste <ul> 'Exemple concret' et une liste <ul> 'Points d'attention'. "
		"Évitez les répétitions, restez concis, et gardez le même style pour tous les passages. Retournez UNIQUEMENT du HTML dans un seul <div>...</div> avec des balises sémantiques (h2, h3, p, ul, li).\n\n"
		f"Report title: {title}\n\nPassages:\n{json.dumps(candidates, ensure_ascii=False)}"
	)
	if truncated_articles > 0:
		prompt += f"\n\nNote: {truncated_articles} additional passage(s) were omitted to keep the report responsive."

	# If a model name is provided, try to resolve model config from DB
	try:
		if model:
			model_conf = _find_model_by_name(model)
		else:
			model_conf = None

		if model_conf:
			# Use model-specific API key if present, else api_key param, else env
			resolved_api_key = api_key or model_conf.get('api_key') or os.environ.get('GOOGLE_API_KEY') or os.environ.get('OPENAI_API_KEY')
			response_text = _call_llm_provider(model_conf, prompt, override_api_key=resolved_api_key)
		else:
			# Fallback: try to use google genai with provided api_key or env
			if genai is None:
				return {"error": "google-genai library is required for default report generation", "status": 500}
			api_key_resolved = _resolve_api_key(api_key or os.environ.get("GOOGLE_API_KEY") or "")
			if not api_key_resolved:
				return {"error": "Google API key required", "status": 400}
			client = genai.Client(api_key=api_key_resolved)
			response = client.models.generate_content(
				model=f"models/{model}" if model and not model.startswith("models/") else (model or DEFAULT_VERIFY_MODEL),
				contents=prompt,
			)
			response_text = getattr(response, 'text', '') or ''

		# Extract clean HTML
		report_html = response_text
		start = report_html.find('<div')
		end = report_html.rfind('</div>')
		if start != -1 and end != -1 and end > start:
			report_html = report_html[start:end+6]

		report_id = f"rpt-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
		storage_result_container: Dict[str, Any] = {}

		def _store_report() -> None:
			storage_result_container["result"] = vectorize_report_backend(
					report_id=report_id,
					title=title,
					content=report_html,
					timestamp=datetime.utcnow().isoformat(),
					articles_count=len(articles),
					api_key=api_key,
					model=model,
					articles=candidates,
				)

		storage_thread = threading.Thread(target=_store_report, daemon=True)
		storage_thread.start()
		storage_thread.join(REPORT_STORAGE_WAIT_SECONDS)
		storage_result = storage_result_container.get("result")
		if storage_result is None:
			storage_result = {
				"status": 202,
				"message": "Report storage scheduled in background",
			}

		return {
			"status": 200,
			"report_html": report_html,
			"title": title,
			"articles_count": len(articles),
			"report_id": report_id,
			"storage": storage_result,
		}
	except Exception as exc:
		return {"error": f"Report generation failed: {str(exc)}", "status": 500}


def vectorize_report_backend(
	report_id: str,
	title: str,
	content: str,
	timestamp: str,
	articles_count: int,
	api_key: str = None,
	model: str = None,
	articles: List[Dict[str, str]] | None = None,
) -> Dict[str, Any]:
	"""
	Vectorize and store report in Chroma DB - BACKEND ONLY.
	All embedding happens here, not in frontend.
	"""
	try:
		# Resolve model config if model name provided
		if model:
			model_conf = _find_model_by_name(model)
			if model_conf and model_conf.get('provider') and model_conf.get('provider').lower() in ['gemini', 'openai', 'azure-openai']:
				# Use model dispatch
				resolved_api_key = api_key or model_conf.get('api_key') or os.environ.get('GOOGLE_API_KEY') or os.environ.get('OPENAI_API_KEY')
				embeddings = _call_embedding_provider(model_conf, [content], override_api_key=resolved_api_key)
			else:
				# Fallback to Gemini
				api_key = _resolve_api_key(api_key or os.environ.get("GOOGLE_API_KEY") or "")
				if not api_key:
					return {"error": "API key required for vectorization", "status": 400}
				embeddings = embed_texts([content], api_key, model=model or "gemini-embedding-001")
		else:
			# No model specified, use Gemini
			api_key = _resolve_api_key(api_key or os.environ.get("GOOGLE_API_KEY") or "")
			if not api_key:
				return {"error": "API key required for vectorization", "status": 400}
			embeddings = embed_texts([content], api_key)
		
		if not embeddings or len(embeddings) == 0:
			return {"error": "Failed to generate embedding", "status": 500}

		# Store in Chroma
		collection = _load_collection()
		metadata = {
			"document_id": report_id,
			"document_name": title,
			"type": "user_report",
			"timestamp": timestamp,
			"articles_count": str(articles_count),
			"source_file": "user_generated"
		}

		collection.upsert(
			ids=[report_id],
			documents=[content],
			embeddings=[embeddings[0]],
			metadatas=[metadata]
		)

		# Also persist a record in Postgres if configured
		try:
			db_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DATABASE_URL") or ""
			if db_url and psycopg is not None:
				with psycopg.connect(db_url) as conn:
					with conn.cursor() as cur:
						cur.execute(
							"""
							INSERT INTO reports (report_uid, title, prompt, content_html, model_name, articles_count, vectorized_at, metadata)
							VALUES (%s, %s, %s, %s, %s, %s, now(), %s)
							ON CONFLICT (report_uid) DO UPDATE SET
							  title = EXCLUDED.title,
							  content_html = EXCLUDED.content_html,
							  model_name = EXCLUDED.model_name,
							  articles_count = EXCLUDED.articles_count,
							  vectorized_at = now(),
							  metadata = EXCLUDED.metadata,
							  updated_at = now()
							RETURNING id
							""",
							(report_id, title, "", content, model or "", int(articles_count or 0), json.dumps({"stored_in": "chroma"})),
						)
						row = cur.fetchone()
						report_db_id = row[0] if row else None
						if articles and report_db_id:
							for art in articles:
								try:
									cur.execute(
										"INSERT INTO report_articles (report_id, article_number, document_name, content, relevance, pages, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s)",
										(
											report_db_id,
											art.get("article_number", ""),
											art.get("document_name", ""),
											art.get("content", ""),
											int(art.get("relevance", 0) or 0),
											art.get("pages", ""),
											json.dumps(art.get("metadata", {})),
										),
									)
								except Exception:
									# ignore per-article failures
									pass
					# commit handled by context manager

		except Exception:
			# best-effort Postgres persistence; ignore failures
			pass

		return {
			"status": 200,
			"report_id": report_id,
			"message": f"Report '{title}' vectorized and stored successfully"
		}
	except Exception as exc:
		return {"error": f"Vectorization failed: {str(exc)}", "status": 500}


def search_reports_backend(query: str, top_k: int = 10, api_key: str = None, model: str = None) -> Dict[str, Any]:
	"""
	Search user reports in Chroma DB - BACKEND ONLY.
	All search logic happens here, not in frontend.
	"""
	try:
		# Resolve model config if model name provided
		if model:
			model_conf = _find_model_by_name(model)
			if model_conf and model_conf.get('provider') and model_conf.get('provider').lower() in ['gemini', 'openai', 'azure-openai']:
				# Use model dispatch
				resolved_api_key = api_key or model_conf.get('api_key') or os.environ.get('GOOGLE_API_KEY') or os.environ.get('OPENAI_API_KEY')
				query_embedding = _call_embedding_provider(model_conf, [query], override_api_key=resolved_api_key)[0]
			else:
				# Fallback to Gemini
				api_key = _resolve_api_key(api_key or os.environ.get("GOOGLE_API_KEY") or "")
				if not api_key:
					return {"error": "API key required", "status": 400}
				try:
					from .embed import embed_query as embed_query_func
				except ImportError:
					from embed import embed_query as embed_query_func
				query_embedding = embed_query_func(query, api_key, model=model or "gemini-embedding-001")
		else:
			# No model specified, use Gemini
			api_key = _resolve_api_key(api_key or os.environ.get("GOOGLE_API_KEY") or "")
			if not api_key:
				return {"error": "API key required", "status": 400}
			try:
				from .embed import embed_query as embed_query_func
			except ImportError:
				from embed import embed_query as embed_query_func
			query_embedding = embed_query_func(query, api_key)

		# Query Chroma DB for user reports only
		collection = _load_collection()
		query_result = collection.query(
			query_embeddings=[query_embedding],
			n_results=top_k,
			where={"type": {"$eq": "user_report"}},
			include=["documents", "metadatas", "distances"]
		)

		documents = (query_result.get("documents") or [[]])[0]
		metadatas = (query_result.get("metadatas") or [[]])[0]
		distances = (query_result.get("distances") or [[]])[0]
		ids = (query_result.get("ids") or [[]])[0]

		results = []
		for doc_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
			try:
				similarity = max(0.0, min(1.0, 1.0 - float(distance)))
				results.append({
					"report_id": doc_id,
					"title": metadata.get("document_name", "Untitled Report") if isinstance(metadata, dict) else "Untitled Report",
					"content": document,
					"similarity": similarity,
					"metadata": metadata if isinstance(metadata, dict) else {}
				})
			except (TypeError, ValueError):
				pass

		results.sort(key=lambda x: x["similarity"], reverse=True)

		return {
			"status": 200,
			"results": results,
			"count": len(results)
		}
	except Exception as exc:
		return {"error": f"Report search failed: {str(exc)}", "status": 500}


# PDF generation moved client-side (Angular) or handled separately; backend PDF function removed.


def _strip_html_tags(html_text: str) -> str:
	"""Strip HTML tags from text."""
	# Remove script and style tags
	html_text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
	html_text = re.sub(r'<style[^>]*>.*?</style>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
	
	# Remove HTML tags
	html_text = re.sub(r'<[^>]+>', '', html_text)
	
	# Decode HTML entities
	import html as html_module
	html_text = html_module.unescape(html_text)
	
	return html_text.strip()
