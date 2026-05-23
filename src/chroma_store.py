from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

try:
	import chromadb
except ImportError as exc:
	raise RuntimeError("Missing chromadb. Install it with: pip install chromadb") from exc


DEFAULT_CHROMADB_DIR = Path(os.environ.get("CHROMADB_DIR", "output/chromadb")).resolve()
DEFAULT_COLLECTION_NAME = os.environ.get("CHROMADB_COLLECTION", "legal_articles")


def _iter_embedded_files(embedded_source: Path) -> List[Path]:
	if embedded_source.is_file():
		return [embedded_source]

	embedded_files = sorted(
		path
		for path in embedded_source.rglob("*_embedded.json")
		if path.is_file()
	)
	if embedded_files:
		return embedded_files

	return sorted(path for path in embedded_source.rglob("*.json") if path.is_file())


def _document_id_from_file(file_path: Path) -> str:
	document_id = file_path.stem
	if document_id.endswith("_embedded"):
		document_id = document_id[:-len("_embedded")]
	return document_id


def _normalize_article_embedding(article_embedding: Any) -> List[float]:
	if isinstance(article_embedding, dict) and "values" in article_embedding:
		article_embedding = article_embedding["values"]

	if isinstance(article_embedding, (list, tuple)) and len(article_embedding) > 0:
		first_elem = article_embedding[0]
		if isinstance(first_elem, (list, tuple)) and len(first_elem) == 2:
			if first_elem[0] == "values" and isinstance(first_elem[1], (list, tuple)):
				article_embedding = first_elem[1]

	if isinstance(article_embedding, (list, tuple)):
		return [float(value) for value in article_embedding]

	raise RuntimeError(f"Unexpected embedding type: {type(article_embedding)}")


def _load_articles_from_file(embedded_file: Path) -> List[Dict[str, Any]]:
	with embedded_file.open("r", encoding="utf-8") as handle:
		loaded_articles = json.load(handle)

	if not isinstance(loaded_articles, list):
		return []

	document_id = _document_id_from_file(embedded_file)
	articles: List[Dict[str, Any]] = []
	for index, item in enumerate(loaded_articles, 1):
		if not isinstance(item, dict):
			continue

		article_copy = dict(item)
		article_copy["_embedded_file"] = str(embedded_file)
		article_copy["_source_file"] = str(embedded_file)
		article_copy["_document_id"] = document_id
		article_copy["_row_index"] = index
		if not str(article_copy.get("document_name", "")).strip():
			article_copy["document_name"] = document_id
		articles.append(article_copy)

	return articles


def _iter_articles_from_source(embedded_source: Path) -> Iterable[Dict[str, Any]]:
	for embedded_file in _iter_embedded_files(embedded_source):
		for article in _load_articles_from_file(embedded_file):
			yield article


def _load_chroma_client(chromadb_dir: Path = DEFAULT_CHROMADB_DIR) -> chromadb.PersistentClient:
	chromadb_dir.mkdir(parents=True, exist_ok=True)
	return chromadb.PersistentClient(path=str(chromadb_dir))


def _load_collection(
	chromadb_dir: Path = DEFAULT_CHROMADB_DIR,
	collection_name: str = DEFAULT_COLLECTION_NAME,
):
	client = _load_chroma_client(chromadb_dir)
	return client.get_or_create_collection(
		name=collection_name,
		metadata={"hnsw:space": "cosine"},
	)


def _prepare_chroma_payload(article: Dict[str, Any]) -> Tuple[str, str, List[float], Dict[str, Any]]:
	embedding = _normalize_article_embedding(article.get("embedding"))
	document_id = str(article.get("_document_id") or article.get("document_name") or "unknown-document")
	document_name = str(article.get("document_name") or document_id)
	article_id = article.get("id")
	article_number = article.get("article_number") or article.get("article")
	source_file = str(article.get("_source_file") or article.get("_embedded_file") or "")
	row_index = article.get("_row_index")

	metadata: Dict[str, Any] = {
		"document_id": document_id,
		"document_name": document_name,
		"source_file": source_file,
	}
	if article_id is not None:
		metadata["article_id"] = str(article_id)
	if article_number is not None:
		metadata["article_number"] = str(article_number)
	if row_index is not None:
		metadata["row_index"] = str(row_index)

	clean_article = {
		key: value
		for key, value in article.items()
		if not key.startswith("_") and key != "embedding"
	}
	article_json = json.dumps(clean_article, ensure_ascii=False)
	entry_suffix = article_id if article_id is not None else row_index if row_index is not None else article_number
	if entry_suffix is None:
		entry_suffix = article.get("content", "")[:32]
	entry_id = f"{document_id}::{entry_suffix}"

	return entry_id, article_json, embedding, metadata


def _upsert_articles(collection: Any, articles: Sequence[Dict[str, Any]]) -> int:
	entries: List[Tuple[str, str, List[float], Dict[str, Any]]] = []
	for article in articles:
		if not article.get("embedding"):
			continue
		entries.append(_prepare_chroma_payload(article))

	if not entries:
		return 0

	ids = [entry_id for entry_id, _, _, _ in entries]
	documents = [document for _, document, _, _ in entries]
	embeddings = [embedding for _, _, embedding, _ in entries]
	metadatas = [metadata for _, _, _, metadata in entries]
	collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
	return len(entries)


def rebuild_chromadb(
	embedded_source: Path,
	chromadb_dir: Path = DEFAULT_CHROMADB_DIR,
	collection_name: str = DEFAULT_COLLECTION_NAME,
) -> int:
	if not embedded_source.exists():
		raise FileNotFoundError(f"Embeddings source not found: {embedded_source}")

	client = _load_chroma_client(chromadb_dir)
	try:
		client.delete_collection(collection_name)
	except Exception:
		pass

	collection = client.get_or_create_collection(
		name=collection_name,
		metadata={"hnsw:space": "cosine"},
	)
	articles = list(_iter_articles_from_source(embedded_source))
	return _upsert_articles(collection, articles)


def sync_embedded_source_to_chromadb(
	embedded_source: Path,
	chromadb_dir: Path = DEFAULT_CHROMADB_DIR,
	collection_name: str = DEFAULT_COLLECTION_NAME,
) -> int:
	if not embedded_source.exists():
		raise FileNotFoundError(f"Embeddings source not found: {embedded_source}")

	if embedded_source.is_dir():
		return rebuild_chromadb(embedded_source, chromadb_dir=chromadb_dir, collection_name=collection_name)

	collection = _load_collection(chromadb_dir=chromadb_dir, collection_name=collection_name)
	articles = _load_articles_from_file(embedded_source)
	document_id = _document_id_from_file(embedded_source)
	try:
		collection.delete(where={"document_id": document_id})
	except Exception:
		pass
	return _upsert_articles(collection, articles)


def ensure_chromadb_index(
	embedded_source: Path,
	chromadb_dir: Path = DEFAULT_CHROMADB_DIR,
	collection_name: str = DEFAULT_COLLECTION_NAME,
) -> Any:
	collection = _load_collection(chromadb_dir=chromadb_dir, collection_name=collection_name)
	if collection.count() == 0:
		rebuild_chromadb(embedded_source, chromadb_dir=chromadb_dir, collection_name=collection_name)
		collection = _load_collection(chromadb_dir=chromadb_dir, collection_name=collection_name)
	return collection


def get_available_documents(
	embedded_source: Path,
	chromadb_dir: Path = DEFAULT_CHROMADB_DIR,
	collection_name: str = DEFAULT_COLLECTION_NAME,
) -> Dict[str, str]:
	documents: Dict[str, str] = {}
	for embedded_file in _iter_embedded_files(embedded_source):
		document_id = _document_id_from_file(embedded_file)
		if not document_id or document_id in documents:
			continue

		display_name = document_id
		try:
			with embedded_file.open("r", encoding="utf-8") as handle:
				loaded_articles = json.load(handle)
			if isinstance(loaded_articles, list):
				for item in loaded_articles:
					if isinstance(item, dict):
						item_name = str(item.get("document_name") or "").strip()
						if item_name:
							display_name = item_name
							break
		except Exception:
			pass

		documents[document_id] = display_name

	return dict(sorted(documents.items(), key=lambda item: item[1].lower()))


def search_articles(
	embedded_source: Path,
	query_embedding: List[float],
	top_k: int = 5,
	threshold: float = 0.0,
	document_filter: List[str] | None = None,
	chromadb_dir: Path = DEFAULT_CHROMADB_DIR,
	collection_name: str = DEFAULT_COLLECTION_NAME,
) -> List[Tuple[Dict[str, Any], float]]:
	collection = ensure_chromadb_index(
		embedded_source,
		chromadb_dir=chromadb_dir,
		collection_name=collection_name,
	)

	n_results = max(top_k * 5, top_k)
	try:
		collection_count = int(collection.count())
		n_results = min(max(collection_count, top_k), n_results)
	except Exception:
		pass

	where = None
	if document_filter:
		filtered_documents = [str(document_id).strip() for document_id in document_filter if str(document_id).strip()]
		if filtered_documents:
			where = {"document_id": {"$in": filtered_documents}}

	query_result = collection.query(
		query_embeddings=[query_embedding],
		n_results=n_results,
		where=where,
		include=["documents", "metadatas", "distances"],
	)

	documents = (query_result.get("documents") or [[]])[0]
	metadatas = (query_result.get("metadatas") or [[]])[0]
	distances = (query_result.get("distances") or [[]])[0]

	results: List[Tuple[Dict[str, Any], float]] = []
	for document, metadata, distance in zip(documents, metadatas, distances):
		if not isinstance(document, str):
			continue

		try:
			article = json.loads(document)
		except json.JSONDecodeError:
			continue

		if isinstance(metadata, dict):
			if metadata.get("source_file"):
				article["_embedded_file"] = str(metadata.get("source_file"))
			article["_document_id"] = str(metadata.get("document_id") or article.get("_document_id") or "")

		try:
			similarity = max(0.0, min(1.0, 1.0 - float(distance)))
		except (TypeError, ValueError):
			similarity = 0.0

		if similarity >= threshold:
			results.append((article, similarity))

	results.sort(key=lambda item: item[1], reverse=True)
	return results[:top_k]
