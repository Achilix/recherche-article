import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
	import google.genai as genai
except ImportError as exc:
	raise RuntimeError("Missing dependencies. Install with: pip install google-genai chromadb") from exc

try:
	from .chroma_store import DEFAULT_CHROMADB_DIR, get_available_documents as get_documents_from_store, search_articles as search_articles_from_store
except ImportError:
	from chroma_store import DEFAULT_CHROMADB_DIR, get_available_documents as get_documents_from_store, search_articles as search_articles_from_store


DEFAULT_MODEL = "gemini-embedding-001"



	"""Load simple KEY=VALUE pairs from a local .env file into os.environ."""
	if not env_path.exists():
		return

	with env_path.open("r", encoding="utf-8") as handle:
		for raw_line in handle:
			line = raw_line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue

			key, value = line.split("=", 1)
			key = key.strip()
			value = value.strip().strip('"').strip("'")
			if key and key not in os.environ:
				os.environ[key] = value


def _resolve_api_key(cli_api_key: str | None) -> str:
	if cli_api_key:
		return cli_api_key

	return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""


def get_available_documents(embedded_source: Path) -> Dict[str, str]:
	return get_documents_from_store(embedded_source, chromadb_dir=DEFAULT_CHROMADB_DIR)


def embed_query(query: str, api_key: str, model: str = DEFAULT_MODEL) -> List[float]:
	if not query or not query.strip():
		raise ValueError("Query cannot be empty")

	client = genai.Client(api_key=api_key)

	try:
		response = client.models.embed_content(
			model=f"models/{model}" if not model.startswith("models/") else model,
			contents=query,
		)
		if response.embeddings and len(response.embeddings) > 0:
			embedding = response.embeddings[0]
			if hasattr(embedding, "values"):
				return list(embedding.values)
			if isinstance(embedding, (list, tuple)):
				return list(embedding)
			raise RuntimeError(f"Unexpected embedding type: {type(embedding)}")
		raise RuntimeError("No embedding returned")
	except Exception as exc:
		raise RuntimeError(f"Error embedding query: {exc}")


def search_articles(
	embedded_file: Path,
	query: str,
	api_key: str,
	model: str = DEFAULT_MODEL,
	top_k: int = 5,
	threshold: float = 0.0,
	document_filter: List[str] | None = None,
) -> List[Tuple[Dict[str, Any], float]]:
	query_embedding = embed_query(query, api_key, model)
	return search_articles_from_store(
		embedded_file,
		query_embedding,
		top_k=top_k,
		threshold=threshold,
		document_filter=document_filter,
		chromadb_dir=DEFAULT_CHROMADB_DIR,
	)


def format_result(article: Dict[str, Any], similarity: float, rank: int) -> str:
	article_num = article.get("article_number") or article.get("article", "Unknown")
	content = article.get("content", "")[:200]
	embedded_file = article.get("_embedded_file", "")
	pages = article.get("pages", "?")
	if isinstance(pages, dict):
		page_start = pages.get("start", "?")
		page_end = pages.get("end", "?")
		pages_str = f"{page_start}-{page_end}"
	else:
		pages_str = str(pages) if pages else "?"

	embedded_file_line = f"Embedded file: {embedded_file}\n" if embedded_file else ""
	content_line = f"Content: {content}...\n" if content else ""

	return f"""\
#{rank} - Similarity: {similarity:.4f} ({similarity*100:.2f}%)
Article: {article_num}
Page(s): {pages_str}
{embedded_file_line}{content_line}"""


def main() -> None:
	parser = argparse.ArgumentParser(description="Search embedded articles using ChromaDB")
	parser.add_argument(
		"embedded_file",
		type=Path,
		help="Path to an embedded JSON file or directory (used to seed the ChromaDB index)",
	)
	parser.add_argument("-q", "--query", default=None, help="Search query text (if not provided, will prompt interactively)")
	parser.add_argument("-k", "--top-k", type=int, default=5, help="Number of top results to return (default: 5)")
	parser.add_argument("-t", "--threshold", type=float, default=0.0, help="Minimum similarity score 0.0-1.0 (default: 0.0)")
	parser.add_argument("-a", "--api-key", default=None, help="Google API key (or set GOOGLE_API_KEY / GEMINI_API_KEY in .env)")
	parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Embedding model name (default: {DEFAULT_MODEL})")

	args = parser.parse_args()
	embedded_file = args.embedded_file.resolve()
	if not embedded_file.exists():
		print(f"Error: File not found: {embedded_file}", file=sys.stderr)
		sys.exit(1)

	_load_env_file(Path.cwd() / ".env")
	api_key = _resolve_api_key(args.api_key)
	if not api_key:
		print("Error: Google API key required. Use --api-key or set GOOGLE_API_KEY / GEMINI_API_KEY in .env", file=sys.stderr)
		sys.exit(1)

	query = args.query
	if not query:
		print("Enter your search query (or 'quit' to exit):")
		query = input("> ").strip()
		if query.lower() == "quit":
			sys.exit(0)

	if not query:
		print("Error: Empty query", file=sys.stderr)
		sys.exit(1)

	results = search_articles(
		embedded_file,
		query,
		api_key,
		args.model,
		args.top_k,
		args.threshold,
	)

	if not results:
		print(f"No results found with similarity >= {args.threshold}")
		return

	print(f"\nFound {len(results)} result(s):\n")
	for rank, (article, similarity) in enumerate(results, 1):
		print(format_result(article, similarity, rank))
		print("-" * 80)


if __name__ == "__main__":
	main()
			raise RuntimeError("No embedding returned")
