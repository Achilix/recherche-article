from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from datetime import datetime
from datetime import time as dt_time
from urllib.parse import parse_qs, urlparse
import traceback

try:
	import psycopg
	from psycopg.rows import dict_row
except ImportError:
	psycopg = None
	dict_row = None

try:
	import google.genai as genai
except ImportError:
	genai = None

try:
	from .recherche import DEFAULT_MODEL, _load_env_file, _resolve_api_key, search_articles, get_available_documents
except ImportError:
	from recherche import DEFAULT_MODEL, _load_env_file, _resolve_api_key, search_articles, get_available_documents

try:
	from .auth_store import (
		AuthError,
		authenticate_user,
		decode_token_payload,
		extract_bearer_token,
		ensure_auth_db,
		get_user_by_id,
		get_user_from_token,
		create_user,
		delete_user,
		list_roles,
		list_users,
		issue_token_pair,
		reset_user_password,
		toggle_user_blocked_status,
		update_password,
		update_user,
	)
except ImportError:
	from auth_store import (
		AuthError,
		authenticate_user,
		decode_token_payload,
		extract_bearer_token,
		ensure_auth_db,
		get_user_by_id,
		get_user_from_token,
		create_user,
		delete_user,
		list_roles,
		list_users,
		issue_token_pair,
		reset_user_password,
		toggle_user_blocked_status,
		update_password,
		update_user,
	)

try:
	from .llm_models_store import (
		ensure_models_db,
		get_models,
		get_model,
		get_active_models,
		create_model,
		update_model,
		delete_model,
		toggle_model_status,
		ModelError,
	)
except ImportError:
	from llm_models_store import (
		ensure_models_db,
		get_models,
		get_model,
		get_active_models,
		create_model,
		update_model,
		delete_model,
		toggle_model_status,
		ModelError,
	)

try:
	from .chroma_store import _load_collection
except ImportError:
	from chroma_store import _load_collection

try:
	from .api_endpoints import generate_report_backend, vectorize_report_backend, search_reports_backend
except ImportError:
	from api_endpoints import generate_report_backend, vectorize_report_backend, search_reports_backend


_load_env_file(Path.cwd() / ".env")

EMBEDDINGS_SOURCE = Path(os.environ.get("EMBEDDINGS_DIR", "output/embeddings")).resolve()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
DEFAULT_VERIFY_MODEL = "gemini-2.5-flash"
DEBUG_API = os.environ.get("API_DEBUG", "1").strip().lower() not in {"0", "false", "no", "off"}

# Standard output directories
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output")).resolve()
QUESTIONS_DIR = Path(os.environ.get("QUESTIONS_DIR", str(OUTPUT_DIR / "questions"))).resolve()
WITH_IDS_DIR = Path(os.environ.get("WITH_IDS_DIR", str(OUTPUT_DIR / "with_ids"))).resolve()
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DATABASE_URL") or ""

CLOSE_FILTER_THRESHOLDS = {
	"off": 0.0,
	"loose": 0.2,
	"balanced": 0.35,
	"strict": 0.5,
}


def _use_postgres() -> bool:
	return bool(DATABASE_URL.strip()) and psycopg is not None


def _pg_connect():
	if not _use_postgres():
		raise RuntimeError("PostgreSQL is not configured")
	return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _pg_question_to_history_item(row: Dict[str, Any]) -> Dict[str, Any]:
	filters_value = row.get("filters") if isinstance(row, dict) else None
	if isinstance(filters_value, (dict, list)):
		filters_text = json.dumps(filters_value, ensure_ascii=False)
	elif filters_value is None:
		filters_text = json.dumps([], ensure_ascii=False)
	else:
		filters_text = str(filters_value)

	created_at = row.get("created_at") if isinstance(row, dict) else None
	if isinstance(created_at, datetime):
		created_text = created_at.isoformat()
	else:
		created_text = str(created_at or "")

	return {
		"id": int(row.get("id") or 0),
		"date": created_text,
		"filters": filters_text,
		"langue": str(row.get("langue") or row.get("language") or ""),
		"status": str(row.get("status") or ""),
		"texte": str(row.get("texte") or row.get("question_text") or ""),
		"user": str(row.get("user") or row.get("user_identifier") or row.get("username") or ""),
	}


def _postgres_history_items(status_filter: str | None = None, user_filter: str | None = None) -> List[Dict[str, Any]]:
	if not _use_postgres():
		return []

	query = "SELECT * FROM question_history_items"
	clauses: list[str] = []
	params: list[Any] = []
	if user_filter:
		clauses.append("CAST(COALESCE(user_identifier, '') AS text) = %s OR CAST(COALESCE(username, '') AS text) = %s OR CAST(COALESCE(user_id::text, '') AS text) = %s")
		params.extend([user_filter, user_filter, user_filter])
	if status_filter:
		clauses.append("LOWER(COALESCE(status, '')) = LOWER(%s)")
		params.append(status_filter)
	if clauses:
		query += " WHERE " + " AND ".join(f"({clause})" for clause in clauses)
	query += " ORDER BY created_at DESC, id DESC"
	with _pg_connect() as connection:
		rows = connection.execute(query, params).fetchall()
		return [_pg_question_to_history_item(dict(row)) for row in rows]


def _postgres_dashboard_stats() -> Dict[str, Any]:
	if not _use_postgres():
		return {}
	with _pg_connect() as connection:
		users_total = int((connection.execute("SELECT COUNT(*) AS total FROM users").fetchone() or {}).get("total", 0))
		questions_total = int((connection.execute("SELECT COUNT(*) AS total FROM questions").fetchone() or {}).get("total", 0))
		questions_today = int((connection.execute("SELECT COUNT(*) AS total FROM questions WHERE created_at::date = CURRENT_DATE").fetchone() or {}).get("total", 0))
		with_ids_total = int((connection.execute("SELECT COUNT(*) AS total FROM legal_articles").fetchone() or {}).get("total", 0))
		with_ids_today = int((connection.execute("SELECT COUNT(*) AS total FROM legal_articles WHERE created_at::date = CURRENT_DATE").fetchone() or {}).get("total", 0))
		sent_to_expert_total = int((connection.execute("SELECT COUNT(*) AS total FROM questions_sent_to_expert").fetchone() or {}).get("total", 0))
		return {
			"questions": {
				"total": questions_total,
				"today": questions_today,
				"sent_to_expert_total": sent_to_expert_total,
				"sent_to_expert_today": 0,
			},
			"users": {"total": users_total},
			"jurisprudences": {
				"total": with_ids_total,
				"today": with_ids_today,
			},
			"server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
		}


def _count_json_items(root: Path) -> int:
	"""Count list items across JSON files under a directory."""
	if not root.exists():
		return 0

	total = 0
	for json_path in root.rglob("*.json"):
		try:
			with json_path.open("r", encoding="utf-8") as handle:
				payload = json.load(handle)
			if isinstance(payload, list):
				total += len(payload)
			elif isinstance(payload, dict):
				if isinstance(payload.get("articles"), list):
					total += len(payload["articles"])
				elif isinstance(payload.get("questions"), list):
					total += len(payload["questions"])
		except Exception:
			continue
	return total


def _iter_json_records(root: Path) -> Iterable[Tuple[Path, Any]]:
	"""Yield JSON payloads from files under a directory."""
	if not root.exists():
		return

	for json_path in root.rglob("*.json"):
		try:
			with json_path.open("r", encoding="utf-8") as handle:
				yield json_path, json.load(handle)
		except Exception:
			continue


def _count_json_items_modified_today(root: Path) -> int:
	"""Count list items in JSON files modified since the start of the current day."""
	if not root.exists():
		return 0

	today_start = datetime.combine(datetime.now().date(), dt_time.min).timestamp()
	total = 0
	for json_path in root.rglob("*.json"):
		try:
			if json_path.stat().st_mtime < today_start:
				continue
		except Exception:
			continue

		try:
			with json_path.open("r", encoding="utf-8") as handle:
				payload = json.load(handle)
			if isinstance(payload, list):
				total += len(payload)
			elif isinstance(payload, dict):
				if isinstance(payload.get("articles"), list):
					total += len(payload["articles"])
				elif isinstance(payload.get("questions"), list):
					total += len(payload["questions"])
		except Exception:
			continue
	return total


def _count_explicit_sent_to_expert_items(root: Path) -> int:
	"""Count JSON records explicitly marked as sent to expert."""
	if not root.exists():
		return 0

	total = 0
	for _json_path, payload in _iter_json_records(root):
		if isinstance(payload, list):
			for item in payload:
				if isinstance(item, dict):
					status = str(item.get("status") or item.get("current_status") or "").strip().lower()
					if status in {"sent-to-expert", "sent_to_expert", "sent to expert"}:
						total += 1
		elif isinstance(payload, dict):
			status = str(payload.get("status") or payload.get("current_status") or "").strip().lower()
			if status in {"sent-to-expert", "sent_to_expert", "sent to expert"}:
				total += 1
			for value in payload.values():
				if isinstance(value, list):
					for item in value:
						if isinstance(item, dict):
							status = str(item.get("status") or item.get("current_status") or "").strip().lower()
							if status in {"sent-to-expert", "sent_to_expert", "sent to expert"}:
								total += 1
	return total


def _build_dashboard_stats() -> Dict[str, Any]:
	"""Build a dashboard payload compatible with the admin dashboard UI."""
	if _use_postgres():
		stats = _postgres_dashboard_stats()
		if stats:
			return stats

	ensure_auth_db()
	users_total = len(list_users())
	questions_root = QUESTIONS_DIR
	with_ids_root = WITH_IDS_DIR
	questions_total = _count_json_items(questions_root)
	questions_today = _count_json_items_modified_today(questions_root)
	jurisprudences_total = _count_json_items(with_ids_root)
	jurisprudences_today = _count_json_items_modified_today(with_ids_root)
	sent_to_expert_total = _count_explicit_sent_to_expert_items(Path(os.environ.get("QUESTIONS_STATUS_DIR", "output/questions")).resolve())
	sent_to_expert_today = 0
	server_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

	return {
		"questions": {
			"total": questions_total,
			"today": questions_today,
			"sent_to_expert_total": sent_to_expert_total,
			"sent_to_expert_today": sent_to_expert_today,
		},
		"users": {
			"total": users_total,
		},
		"jurisprudences": {
			"total": jurisprudences_total,
			"today": jurisprudences_today,
		},
		"server_time": server_time,
	}


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: Dict[str, Any]) -> None:
	body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
	handler.send_response(status_code)
	handler.send_header("Content-Type", "application/json; charset=utf-8")
	handler.send_header("Access-Control-Allow-Origin", "*")
	handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
	handler.send_header("Access-Control-Allow-Headers", _cors_request_headers(handler))
	handler.send_header("Content-Length", str(len(body)))
	handler.end_headers()
	handler.wfile.write(body)


def _cors_request_headers(handler: BaseHTTPRequestHandler) -> str:
	request_headers = handler.headers.get("Access-Control-Request-Headers")
	if request_headers:
		return request_headers
	return "Content-Type, Authorization"


def _debug_log(message: str, *args: Any) -> None:
	if DEBUG_API:
		if args:
			print(message.format(*args))
		else:
			print(message)


def _collect_all_questions(root: Path) -> List[Dict[str, Any]]:
	"""Collect and normalize question records from JSON files under a directory.

	Strategy:
	- First pass: detect and normalize records that appear to be user-submitted
	  questions (contain 'texte'/'question'/'text' or 'user').
	- If no normalized records are found, perform a fallback pass that extracts
	  plain question strings from generated article batches (files with 'articles').

	Returns a list of dicts matching the frontend Question interface.
	"""
	questions: List[Dict[str, Any]] = []
	if not root.exists():
		return questions

	next_id = 1
	# First pass: normalize explicit question records
	for _json_path, payload in _iter_json_records(root):
		try:
			def _normalize(rec: Any) -> Dict[str, Any] | None:
				nonlocal next_id
				if not isinstance(rec, dict):
					return None
				# require either text or user metadata
				if not any(k in rec for k in ("texte", "question", "text", "user")):
					return None
				q = {
					"id": int(rec.get("id") or rec.get("question_id") or next_id),
					"date": str(rec.get("date") or rec.get("created_at") or ""),
					"filters": json.dumps(rec.get("filters") or []),
					"langue": str(rec.get("langue") or rec.get("language") or ""),
					"status": str(rec.get("status") or ""),
					"texte": str(rec.get("texte") or rec.get("question") or rec.get("text") or ""),
					"user": str(rec.get("user") or rec.get("user_id") or ""),
				}
				if q["id"] == next_id:
					next_id += 1
				return q

			if isinstance(payload, list):
				for item in payload:
					normalized = _normalize(item)
					if normalized:
						questions.append(normalized)
			elif isinstance(payload, dict):
				if isinstance(payload.get("questions"), list):
					for item in payload.get("questions"):
						if isinstance(item, dict):
							normalized = _normalize(item)
							if normalized:
								questions.append(normalized)
				else:
					normalized = _normalize(payload)
					if normalized:
						questions.append(normalized)
		except Exception:
			continue

	# Fallback: if no explicit questions found, extract plain question strings
	if not questions:
		for _json_path, payload in _iter_json_records(root):
			try:
				if isinstance(payload, dict) and isinstance(payload.get("articles"), list):
					for art in payload.get("articles"):
						if isinstance(art, dict) and isinstance(art.get("questions"), list):
							for qtext in art.get("questions"):
								if isinstance(qtext, str) and qtext.strip():
									questions.append({
										"id": next_id,
										"date": "",
										"filters": json.dumps([]),
										"langue": "",
										"status": "",
										"texte": qtext,
										"user": "",
									})
									next_id += 1
			except Exception:
				continue

	return questions



def _paginate_list(items: List[Dict[str, Any]], page: int, per_page: int) -> Tuple[List[Dict[str, Any]], int, int]:
	total = len(items)
	total_pages = (total + per_page - 1) // per_page if per_page > 0 else 1
	if page < 1:
		page = 1
	start = (page - 1) * per_page
	end = start + per_page
	return items[start:end], total, total_pages


def _log_exception(handler: BaseHTTPRequestHandler, exc: Exception) -> None:
	try:
		logs_dir = OUTPUT_DIR / "logs"
		logs_dir.mkdir(parents=True, exist_ok=True)
		log_path = logs_dir / "api_errors.log"
		with log_path.open("a", encoding="utf-8") as fh:
			fh.write(f"{datetime.now().isoformat()} PATH={getattr(handler, 'path', '')} ERROR={repr(exc)}\n")
			traceback.print_exc(file=fh)
			fh.write("\n")
	except Exception:
		# Best-effort logging; don't raise
		pass


def _closeness_label(similarity: float) -> str:
	if similarity >= 0.85:
		return "very_close"
	if similarity >= 0.7:
		return "close"
	if similarity >= 0.5:
		return "moderate"
	return "distant"


def _resolve_close_filter_threshold(close_filter: str) -> float:
	return CLOSE_FILTER_THRESHOLDS.get(str(close_filter).strip().lower(), CLOSE_FILTER_THRESHOLDS["balanced"])


def _to_bool(value: Any) -> bool:
	if isinstance(value, bool):
		return value
	if value is None:
		return False
	if isinstance(value, (int, float)):
		return value != 0
	return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _verify_results_with_gemini(
	query: str,
	results: List[Dict[str, Any]],
	api_key: str,
	verify_top_n: int,
	verify_model: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
	if genai is None or not api_key:
		return (
			{
				"enabled": False,
				"model": verify_model,
				"checked_count": 0,
				"relevant_count": 0,
				"overall": "not-run",
				"explanation": "AI verification unavailable; returning similarity-ranked results.",
			},
			results,
		)

	checked_results = results[: max(0, verify_top_n)]
	verification = {
		"enabled": True,
		"model": verify_model,
		"checked_count": len(checked_results),
		"relevant_count": len(checked_results),
		"overall": "not-run",
		"explanation": "AI verification is enabled, but this backend keeps the similarity-ranked results unchanged.",
	}
	return verification, results


def _read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
	content_length = int(handler.headers.get("Content-Length", "0") or 0)
	if content_length <= 0:
		return {}

	raw_body = handler.rfile.read(content_length)
	if not raw_body:
		return {}

	try:
		payload = json.loads(raw_body.decode("utf-8"))
	except json.JSONDecodeError as exc:
		raise ValueError("Request body must be valid JSON") from exc

	if not isinstance(payload, dict):
		raise ValueError("Request body must be a JSON object")

	return payload


def _first_value(values: Dict[str, list[str]], key: str, default: Any = None) -> Any:
	if key not in values or not values[key]:
		return default
	return values[key][0]


def _parse_search_request(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
	parsed_url = urlparse(handler.path)
	query_params = parse_qs(parsed_url.query)
	body_payload: Dict[str, Any] = {}

	if handler.command == "POST":
		body_payload = _read_json_body(handler)

	_debug_log(
		"Search request {} {} from {}",
		handler.command,
		parsed_url.path,
		getattr(handler, "client_address", ("unknown", "unknown"))[0],
	)
	_debug_log("Search raw query params: {}", query_params)
	if body_payload:
		_debug_log("Search raw body payload keys: {}", sorted(body_payload.keys()))

	def _value(key: str, default: Any = None) -> Any:
		if key in body_payload:
			return body_payload[key]
		return _first_value(query_params, key, default)

	# Handle documents filter - can be a list or comma-separated string
	documents = _value("documents", None)
	if documents is None:
		documents = []
	elif isinstance(documents, str):
		documents = [d.strip() for d in documents.split(",") if d.strip()]
	elif isinstance(documents, list):
		documents = [str(d).strip() for d in documents if d]

	return {
		"query": _value("query", ""),
		"top_k": _value("top_k", 5),
		"threshold": _value("threshold", 0.0),
		"close_filter": _value("close_filter", "balanced"),
		"verify_results": _value("verify_results", False),
		"verify_top_n": _value("verify_top_n", 3),
		"verify_model": _value("verify_model", DEFAULT_VERIFY_MODEL),
		"model": _value("model", DEFAULT_MODEL),
		"api_key": _value("api_key", ""),
		"documents": documents,
	}


def _to_relative_source_path(path_value: str) -> str:
	try:
		path_obj = Path(path_value)
		if not path_obj.is_absolute():
			return path_obj.as_posix()

		resolved = path_obj.resolve()
		workspace_root = Path.cwd().resolve()
		try:
			return resolved.relative_to(workspace_root).as_posix()
		except ValueError:
			pass

		try:
			return resolved.relative_to(EMBEDDINGS_SOURCE.parent).as_posix()
		except ValueError:
			return path_obj.name or str(path_value).replace("\\", "/")
	except Exception:
		return str(path_value).replace("\\", "/")


def _serialize_result(article: Dict[str, Any], similarity: float) -> Dict[str, Any]:
	payload = {
		key: value
		for key, value in article.items()
		if key not in {"embedding", "_embedded_file"}
	}
	payload["similarity"] = similarity
	payload["closeness_label"] = _closeness_label(similarity)
	embedded_file = article.get("_embedded_file")
	if embedded_file:
		payload["embedded_file"] = _to_relative_source_path(str(embedded_file))
	return payload


class SearchAPIHandler(BaseHTTPRequestHandler):
	server_version = "LegalSearchAPI/1.0"

	def do_OPTIONS(self) -> None:
		self.send_response(204)
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
		self.send_header("Access-Control-Allow-Headers", _cors_request_headers(self))
		self.send_header("Access-Control-Max-Age", "86400")
		self.end_headers()

	def do_GET(self) -> None:
		self._route()

	def do_POST(self) -> None:
		self._route()

	def do_PUT(self) -> None:
		self._route()

	def do_DELETE(self) -> None:
		self._route()

	def do_PATCH(self) -> None:
		self._route()

	def _route(self) -> None:
		parsed_url = urlparse(self.path)
		# Debug: print incoming request to terminal and also append to temp log
		try:
			print(f"[API] {datetime.utcnow().isoformat()} {self.client_address[0]} {self.command} {parsed_url.path}")
		except Exception:
			pass
		try:
			import tempfile
			_tmp = Path(tempfile.gettempdir()) / "route_requests.log"
			with open(_tmp, 'a', encoding='utf-8') as _rf:
				_rf.write(f"{datetime.utcnow().isoformat()} {self.client_address[0]} {self.command} {parsed_url.path}\n")
		except Exception:
			pass
		# Quick early handling for question create endpoint to avoid routing issues
		# Accept any POST path containing 'question' or ending with '/questions'
		p_lower = parsed_url.path.lower() if isinstance(parsed_url.path, str) else ''
		if self.command == "POST" and ("question" in p_lower or p_lower.rstrip("/").endswith("/questions") or p_lower == "/questions"):
			try:
				import tempfile
				_tmp = Path(tempfile.gettempdir()) / "route_requests.log"
				with open(_tmp, 'a', encoding='utf-8') as _rf:
					_rf.write(f"CREATE_ROUTE_MATCH {datetime.utcnow().isoformat()} {parsed_url.path}\n")
			except Exception:
				pass
			self._handle_create_question()
			return
		if parsed_url.path in {"/", "/index.html"}:
			self._handle_root()
			return
		if parsed_url.path == "/health":
			_json_response(self, 200, {"status": "ok"})
			return
		if parsed_url.path == "/documents":
			self._handle_documents()
			return
		if parsed_url.path == "/search":
			self._handle_search()
			return
		if parsed_url.path == "/search-reports":
			self._handle_search_reports()
			return
		if parsed_url.path == "/api/auth/login":
			self._handle_login()
			return
		if parsed_url.path == "/api/auth/logout":
			self._handle_logout()
			return
		if parsed_url.path == "/api/auth/current_user":
			self._handle_current_user()
			return
		if parsed_url.path == "/api/dashboard/stats":
			self._handle_dashboard_stats()
			return
		if parsed_url.path == "/api/auth/change_password":
			self._handle_change_password()
			return
		if parsed_url.path == "/api/auth/refresh_token":
			self._handle_refresh_token()
			return
		if parsed_url.path == "/api/user/users":
			if self.command == "GET":
				self._handle_users_collection()
			elif self.command == "POST":
				self._handle_create_user()
			else:
				_json_response(self, 405, {"error": "Method not allowed"})
			return
		if parsed_url.path == "/api/user/users/roles":
			self._handle_user_roles()
			return
		if parsed_url.path.startswith("/api/user/users/"):
			self._handle_user_item_route(parsed_url.path)
			return
		if parsed_url.path == "/report":
			self._handle_report()
			return
		if parsed_url.path == "/vectorize-report":
			self._handle_vectorize_report()
			return

		if parsed_url.path == "/models":
			self._handle_models()
			return
		# LLM Models API endpoints
		if parsed_url.path == "/api/llm-models/active":
			self._handle_llm_models_active()
			return
		# Questions / history endpoints (pagination + filters)
		if parsed_url.path == "/api/question/users/questions/pagination":
			self._handle_questions_pagination()
			return
		# Create question endpoint
		if parsed_url.path == "/api/question/questions" and self.command == "POST":
			self._handle_create_question()
			return
		if parsed_url.path == "/api/question/questions/by-user-status":
			self._handle_questions_by_user_status()
			return
		if parsed_url.path == "/api/question/questions/status/sent-to-expert":
			self._handle_questions_sent_to_expert()
			return
		# per-question actions e.g. POST /api/question/questions/{id}/send-to-expert
		if parsed_url.path.startswith("/api/question/questions/"):
			# normalize and split
			parts = [p for p in parsed_url.path.split('/') if p]
			# expecting ['api','question','questions','{id}','send-to-expert']
			if len(parts) >= 5 and parts[4] == 'send-to-expert' and self.command == 'POST':
				try:
					qid = int(parts[3])
					self._handle_send_question_to_expert(qid)
					return
				except Exception:
					_json_response(self, 400, {"error": "Invalid question id"})
					return
		if parsed_url.path.startswith("/api/llm-models/"):
			path_parts = parsed_url.path.split("/")
			if len(path_parts) >= 4:
				try:
					model_id = int(path_parts[3])
					if len(path_parts) == 4:
						# /api/llm-models/{id}
						if self.command == "GET":
							self._handle_get_llm_model(model_id)
						elif self.command == "PUT":
							self._handle_update_llm_model(model_id)
						elif self.command == "DELETE":
							self._handle_delete_llm_model(model_id)
						else:
							_json_response(self, 405, {"error": "Method not allowed"})
						return
					elif len(path_parts) == 5 and path_parts[4] == "toggle":
						# /api/llm-models/{id}/toggle
						if self.command == "PATCH":
							self._handle_toggle_llm_model_status(model_id)
							return
				except (ValueError, IndexError):
					pass
		if parsed_url.path == "/api/llm-models":
			if self.command == "GET":
				self._handle_get_llm_models()
			elif self.command == "POST":
				self._handle_create_llm_model()
			else:
				_json_response(self, 405, {"error": "Method not allowed"})
			return
		_json_response(self, 404, {"error": "Not found"})
		return

		if parsed_url.path == "/health":
			self._handle_health()
			return

		if parsed_url.path == "/documents":
			self._handle_documents()
			return

		if parsed_url.path == "/search":
			self._handle_search()
			return

		if parsed_url.path == "/api/auth/login":
			self._handle_login()
			return

		if parsed_url.path == "/api/auth/logout":
			self._handle_logout()
			return

		if parsed_url.path == "/api/auth/current_user":
			self._handle_current_user()
			return

		if parsed_url.path == "/api/auth/change_password":
			self._handle_change_password()
			return

		if parsed_url.path == "/api/auth/refresh_token":
			self._handle_refresh_token()
			return

		if parsed_url.path == "/report":
			self._handle_report()
			return

		if parsed_url.path == "/models":
			self._handle_models()
			return

		_json_response(self, 404, {"error": "Not found"})

	def _handle_health(self) -> None:
		embedded_files = list(EMBEDDINGS_SOURCE.rglob("*_embedded.json")) if EMBEDDINGS_SOURCE.exists() else []
		_json_response(
			self,
			200,
			{
				"status": "ok",
				"embeddings_source": str(EMBEDDINGS_SOURCE),
				"embedded_file_count": len(embedded_files),
			},
		)

	def _handle_root(self) -> None:
		_json_response(
			self,
			200,
			{
				"status": "ok",
				"message": "Built-in UI removed. Use your React/Next frontend with this API.",
				"endpoints": {
					"health": "/health",
					"documents": "/documents",
					"search": "/search",
				},
			},
		)

	def _handle_documents(self) -> None:
		"""Return list of available documents."""
		if not EMBEDDINGS_SOURCE.exists():
			_json_response(
				self,
				400,
				{
					"error": f"Embeddings source not found: {EMBEDDINGS_SOURCE}",
				},
			)
			return

		try:
			documents = get_available_documents(EMBEDDINGS_SOURCE)
			# Convert to list format for frontend
			doc_list = [
				{"id": doc_id, "name": display_name}
				for doc_id, display_name in documents.items()
			]
			_json_response(
				self,
				200,
				{
					"documents": doc_list,
					"count": len(doc_list),
				},
			)
		except Exception as exc:
			_log_exception(self, exc)
			_json_response(self, 500, {"error": str(exc)})

	def _handle_login(self) -> None:
		try:
			payload = _read_json_body(self)
			identifier = str(payload.get("username") or payload.get("email") or "").strip()
			password = str(payload.get("password") or "")
			user = authenticate_user(identifier, password)
			access_token, refresh_token = issue_token_pair(user)
			_json_response(
				self,
				200,
				{
					"token": access_token,
					"refresh_token": refresh_token,
					"user": user,
					"firstLogin": bool(user.get("must_change_password", False)),
				},
			)
		except ValueError as exc:
			_json_response(self, 400, {"msg": str(exc), "error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"msg": str(exc), "error": str(exc)})

	def _handle_logout(self) -> None:
		_json_response(self, 200, {"msg": "Logged out successfully"})

	def _handle_current_user(self) -> None:
		token = extract_bearer_token(self.headers.get("Authorization"))
		if not token:
			_json_response(self, 401, {"error": "Unauthorized"})
			return

		user = get_user_from_token(token)
		if not user:
			_json_response(self, 401, {"error": "Unauthorized"})
			return

		_json_response(self, 200, {"user": user})

	def _handle_change_password(self) -> None:
		token = extract_bearer_token(self.headers.get("Authorization"))
		if not token:
			_json_response(self, 401, {"error": "Unauthorized"})
			return

		user = get_user_from_token(token)
		if not user:
			_json_response(self, 401, {"error": "Unauthorized"})
			return

		try:
			payload = _read_json_body(self)
			current_password = str(payload.get("current_password") or "")
			new_password = str(payload.get("new_password") or "")
			update_password(int(user["id"]), current_password, new_password)
			_json_response(self, 200, {"msg": "Password updated successfully"})
		except ValueError as exc:
			_json_response(self, 400, {"msg": str(exc), "error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"msg": str(exc), "error": str(exc)})

	def _handle_refresh_token(self) -> None:
		token = extract_bearer_token(self.headers.get("Authorization"))
		if not token:
			try:
				payload = _read_json_body(self)
			except Exception:
				payload = {}
			token = str(payload.get("refresh_token") or "").strip()

		if not token:
			_json_response(self, 401, {"error": "Unauthorized"})
			return

		payload = decode_token_payload(token, expected_token_type="refresh")
		if not payload:
			_json_response(self, 401, {"error": "Unauthorized"})
			return

		user = get_user_from_token(token, expected_token_type="refresh")
		if not user:
			_json_response(self, 401, {"error": "Unauthorized"})
			return

		access_token, refresh_token = issue_token_pair(user)
		_json_response(self, 200, {"token": access_token, "refresh_token": refresh_token, "user": user})

	def _require_admin_user(self) -> Dict[str, Any] | None:
		token = extract_bearer_token(self.headers.get("Authorization"))
		if not token:
			_json_response(self, 401, {"error": "Unauthorized"})
			return None

		# Try to decode the token payload first (validates signature/expiry)
		payload = decode_token_payload(token)
		if not payload:
			_json_response(self, 401, {"error": "Unauthorized"})
			return None

		# Prefer the DB-backed user when available, but fall back to the token payload
		user = get_user_from_token(token)
		if not user:
			# Build a minimal user dict from the token payload so callers still receive a user shape
			try:
				user = {
					"id": int(payload.get("sub")) if payload.get("sub") is not None else None,
					"username": payload.get("username"),
					"email": payload.get("email"),
					"firstname": payload.get("firstname") or "",
					"lastname": payload.get("lastname") or "",
					"is_blocked": bool(payload.get("is_blocked", False)),
					"must_change_password": bool(payload.get("must_change_password", False)),
					"role": {
						"id": int(payload.get("role_id")) if payload.get("role_id") is not None else None,
						"name": payload.get("role") or payload.get("role_name") or "",
						"description": "",
					},
				}
			except Exception:
				_json_response(self, 401, {"error": "Unauthorized"})
				return None

		role_name = str((user.get("role") or {}).get("name") or "").strip().lower()
		if role_name != "administrateur":
			_json_response(self, 403, {"error": "Forbidden"})
			return None

		return user

	def _handle_users_collection(self) -> None:
		if not self._require_admin_user():
			return

		parsed_url = urlparse(self.path)
		query_params = parse_qs(parsed_url.query)
		filters = {key: _first_value(query_params, key) for key in query_params}

		try:
			users = list_users(filters)
			_json_response(self, 200, users)
		except AuthError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except Exception as exc:
			_log_exception(self, exc)
			_json_response(self, 500, {"error": str(exc)})

	def _handle_questions_pagination(self) -> None:
		"""Return paginated questions for all users with optional filters."""
		parsed_url = urlparse(self.path)
		query = parse_qs(parsed_url.query)
		page = int(_first_value(query, 'page') or 1)
		per_page = int(_first_value(query, 'per_page') or 10)
		user_filter = _first_value(query, 'user')
		status_filter = _first_value(query, 'status')

		try:
			if _use_postgres():
				all_q = _postgres_history_items(status_filter=status_filter, user_filter=user_filter)
				page_items, total, total_pages = _paginate_list(all_q, page, per_page)
				_json_response(self, 200, {
					'page': page,
					'per_page': per_page,
					'questions': page_items,
					'total_items': total,
					'total_pages': total_pages,
				})
				return

			questions_dir = QUESTIONS_DIR
			all_q = _collect_all_questions(questions_dir)
			# apply filters
			if user_filter:
				all_q = [q for q in all_q if str(q.get('user') or q.get('user_id') or '') == str(user_filter)]
			if status_filter:
				all_q = [q for q in all_q if str(q.get('status') or '').lower() == str(status_filter).lower()]

			# sort by date descending if available
			try:
				all_q.sort(key=lambda x: x.get('date') or x.get('created_at') or '', reverse=True)
			except Exception:
				pass

			page_items, total, total_pages = _paginate_list(all_q, page, per_page)
			response = {
				'page': page,
				'per_page': per_page,
				'questions': page_items,
				'total_items': total,
				'total_pages': total_pages,
			}
			_json_response(self, 200, response)
		except Exception as exc:
			_log_exception(self, exc)
			_json_response(self, 500, {"error": str(exc)})

	def _handle_questions_by_user_status(self) -> None:
		parsed_url = urlparse(self.path)
		query = parse_qs(parsed_url.query)
		page = int(_first_value(query, 'page') or 1)
		per_page = int(_first_value(query, 'per_page') or 10)
		user_filter = _first_value(query, 'user')
		status_filter = _first_value(query, 'status')

		if not user_filter:
			_json_response(self, 400, {"error": "Missing 'user' query parameter"})
			return

		try:
			if _use_postgres():
				all_q = _postgres_history_items(status_filter=status_filter, user_filter=user_filter)
				page_items, total, total_pages = _paginate_list(all_q, page, per_page)
				_json_response(self, 200, {
					'page': page,
					'per_page': per_page,
					'questions': page_items,
					'total_items': total,
					'total_pages': total_pages,
				})
				return

			questions_dir = QUESTIONS_DIR
			all_q = _collect_all_questions(questions_dir)
			all_q = [q for q in all_q if str(q.get('user') or q.get('user_id') or '') == str(user_filter)]
			if status_filter:
				all_q = [q for q in all_q if str(q.get('status') or '').lower() == str(status_filter).lower()]

			all_q.sort(key=lambda x: x.get('date') or x.get('created_at') or '', reverse=True)
			page_items, total, total_pages = _paginate_list(all_q, page, per_page)
			_json_response(self, 200, {
				'page': page,
				'per_page': per_page,
				'questions': page_items,
				'total_items': total,
				'total_pages': total_pages,
			})
		except Exception as exc:
			_json_response(self, 500, {"error": str(exc)})

	def _handle_questions_sent_to_expert(self) -> None:
		parsed_url = urlparse(self.path)
		query = parse_qs(parsed_url.query)

		page = int(_first_value(query, 'page') or 1)
		per_page = int(_first_value(query, 'per_page') or 10)

		try:
			if _use_postgres():
				all_q = _postgres_history_items(status_filter='sent-to-expert')
				page_items, total, total_pages = _paginate_list(all_q, page, per_page)
				_json_response(self, 200, {
					'page': page,
					'per_page': per_page,
					'questions': page_items,
					'total_items': total,
					'total_pages': total_pages,
				})
				return

			questions_dir = QUESTIONS_DIR
			all_q = _collect_all_questions(questions_dir)
			filtered = [q for q in all_q if str(q.get('status') or '').lower() == 'sent-to-expert' or str(q.get('status') or '').lower() == 'sent_to_expert']
			filtered.sort(key=lambda x: x.get('date') or x.get('created_at') or '', reverse=True)
			page_items, total, total_pages = _paginate_list(filtered, page, per_page)
			_json_response(self, 200, {
				'page': page,
				'per_page': per_page,
				'questions': page_items,
				'total_items': total,
				'total_pages': total_pages,
			})
		except Exception as exc:
			_json_response(self, 500, {"error": str(exc)})

	def _handle_send_question_to_expert(self, question_id: int) -> None:
		"""Handle marking a question as sent to expert. Minimal implementation: returns 200 if accepted."""
		try:
			if _use_postgres():
				with _pg_connect() as connection:
					cursor = connection.execute(
						"""
						UPDATE questions
						SET status = %s, sent_to_expert_at = %s, updated_at = %s
						WHERE id = %s
						RETURNING id
						""",
						('sent-to-expert', datetime.utcnow(), datetime.utcnow(), question_id),
					)
					connection.commit()
					if cursor.fetchone() is None:
						_json_response(self, 404, {"error": "Question not found"})
						return
				_json_response(self, 200, {"msg": "Question marked as sent to expert", "question_id": question_id})
				return

			# In a full implementation we'd locate the question by id and update persistence.
			# For now, accept the request and return a success payload.
			_json_response(self, 200, {"msg": "Question marked as sent to expert", "question_id": question_id})
		except Exception as exc:
			_log_exception(self, exc)
			_json_response(self, 500, {"error": str(exc)})

	def _handle_create_question(self) -> None:
		"""Create a new question record either in Postgres or as a JSON file in QUESTIONS_DIR."""
		# Debug: print invocation to terminal and append to temp log
		try:
			print(f"[API] {datetime.utcnow().isoformat()} _handle_create_question invoked path={self.path} from={self.client_address[0]}")
		except Exception:
			pass
		try:
			import tempfile
			_tmp2 = Path(tempfile.gettempdir()) / "create_handler_invoked.log"
			with open(_tmp2, 'a', encoding='utf-8') as _cf:
				_cf.write(f"invoked at {datetime.utcnow().isoformat()} path={self.path} from={self.client_address[0]}\n")
		except Exception:
			pass
		# Debug marker: append to a temp log so we can see when this handler is invoked
		try:
			with open('create_handler_invoked.log', 'a', encoding='utf-8') as _fh:
				_fh.write(f"invoked at {datetime.utcnow().isoformat()}\n")
		except Exception:
			pass
		try:
			length = int(self.headers.get('content-length', 0))
			print(f"[API] Content-Length: {length}")
			body = self.rfile.read(length) if length else b''
			try:
				payload_text = body.decode('utf-8') if body else ''
			except Exception:
				payload_text = str(body)
			if payload_text:
				print(f"[API] Payload preview: {payload_text[:1000]}")
			data = json.loads(payload_text or '{}')
			# Normalize payload
			texte = data.get('texte') or data.get('question_text') or data.get('text') or ''
			langue = data.get('langue') or data.get('language') or ''
			status = data.get('status') or 'created'
			user_id = data.get('user_id')
			user_identifier = data.get('user_identifier') or data.get('user') or data.get('user_fullname') or ''

			if _use_postgres():
				with _pg_connect() as connection:
					# Insert into questions table. Keep columns minimal and safe.
					query = "INSERT INTO questions (question_text, language, status, user_id, user_identifier, created_at) VALUES (%s, %s, %s, %s, %s, NOW()) RETURNING id;"
					params = [texte, langue, status, user_id, user_identifier]
					res = connection.execute(query, params).fetchone()
					new_id = int(res.get('id') if isinstance(res, dict) else (res[0] if res else 0))
					print(f"[API] Created question in Postgres id={new_id}")
					_json_response(self, 201, {"id": new_id})
					return
			# Fallback: write to QUESTIONS_DIR as JSON file
			questions_dir = QUESTIONS_DIR
			questions_dir.mkdir(parents=True, exist_ok=True)
			# create a simple JSON file with timestamp
			item = {
				"id": None,
				"question_text": texte,
				"language": langue,
				"status": status,
				"user_id": user_id,
				"user_identifier": user_identifier,
				"created_at": datetime.utcnow().isoformat()
			}
			# name file with timestamp
			filename = questions_dir / f"question_{int(datetime.utcnow().timestamp())}.json"
			with open(filename, 'w', encoding='utf-8') as fh:
				json.dump(item, fh, ensure_ascii=False, indent=2)
			print(f"[API] Wrote fallback question file: {filename}")
			_json_response(self, 201, {"path": str(filename)})
		except Exception as exc:
			_log_exception(self, exc)
			_json_response(self, 500, {"error": str(exc)})

	def _handle_user_roles(self) -> None:
		if not self._require_admin_user():
			return

		try:
			_json_response(self, 200, list_roles())
		except Exception as exc:
			_json_response(self, 500, {"error": str(exc)})

	def _handle_user_item_route(self, path: str) -> None:
		if not self._require_admin_user():
			return

		path_parts = [part for part in path.split("/") if part]
		if len(path_parts) < 4 or path_parts[:3] != ["api", "user", "users"]:
			_json_response(self, 404, {"error": "Not found"})
			return

		try:
			user_id = int(path_parts[3])
		except (TypeError, ValueError):
			_json_response(self, 404, {"error": "Not found"})
			return

		action = path_parts[4] if len(path_parts) > 4 else ""
		try:
			if len(path_parts) == 4:
				if self.command == "GET":
					user = get_user_by_id(user_id)
					if user is None:
						_json_response(self, 404, {"error": "User not found"})
						return
					_json_response(self, 200, user)
				elif self.command == "PUT":
					self._handle_update_user(user_id)
				elif self.command == "DELETE":
					self._handle_delete_user(user_id)
				else:
					_json_response(self, 405, {"error": "Method not allowed"})
				return

			if action == "toggle-blocked" and self.command == "PATCH":
				self._handle_toggle_user_blocked(user_id)
				return

			if action == "reset-password" and self.command == "POST":
				self._handle_reset_user_password(user_id)
				return

			_json_response(self, 404, {"error": "Not found"})
		except AuthError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"error": str(exc)})

	def _handle_create_user(self) -> None:
		if not self._require_admin_user():
			return

		try:
			payload = _read_json_body(self)
			user = create_user(payload)
			_json_response(self, 201, user)
		except AuthError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"error": str(exc)})

	def _handle_update_user(self, user_id: int) -> None:
		try:
			payload = _read_json_body(self)
			user = update_user(user_id, payload)
			if user is None:
				_json_response(self, 404, {"error": "User not found"})
				return
			_json_response(self, 200, user)
		except AuthError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"error": str(exc)})

	def _handle_delete_user(self, user_id: int) -> None:
		if delete_user(user_id):
			_json_response(self, 200, {"msg": "User deleted successfully"})
			return
		_json_response(self, 404, {"error": "User not found"})

	def _handle_toggle_user_blocked(self, user_id: int) -> None:
		user = toggle_user_blocked_status(user_id)
		if user is None:
			_json_response(self, 404, {"error": "User not found"})
			return
		_json_response(self, 200, user)

	def _handle_reset_user_password(self, user_id: int) -> None:
		try:
			generated_password = reset_user_password(user_id)
			_json_response(self, 200, {"msg": "Password reset successfully", "temporary_password": generated_password})
		except AuthError as exc:
			_json_response(self, 404 if "not found" in str(exc).lower() else 400, {"error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"error": str(exc)})

	def _handle_search(self) -> None:
		if not EMBEDDINGS_SOURCE.exists():
			_json_response(
				self,
				400,
				{
					"error": f"Embeddings source not found: {EMBEDDINGS_SOURCE}",
				},
			)
			return

		try:
			params = _parse_search_request(self)
			query = str(params["query"]).strip()
			_debug_log(
				"Parsed search params query={!r}, top_k={}, threshold={}, close_filter={!r}, verify_results={}, verify_top_n={}, verify_model={!r}, documents={}",
				query,
				params["top_k"],
				params["threshold"],
				params["close_filter"],
				params["verify_results"],
				params["verify_top_n"],
				params["verify_model"],
				params["documents"],
			)
			if not query:
				raise ValueError("Query cannot be empty")

			top_k = int(params["top_k"])
			if top_k < 1:
				raise ValueError("top_k must be at least 1")

			threshold = float(params["threshold"])
			if threshold < 0.0 or threshold > 1.0:
				raise ValueError("threshold must be between 0.0 and 1.0")

			close_filter = str(params["close_filter"])
			close_filter_threshold = _resolve_close_filter_threshold(close_filter)
			effective_threshold = max(threshold, close_filter_threshold)
			_debug_log(
				"Thresholds threshold={} close_filter_threshold={} effective_threshold={}",
				threshold,
				close_filter_threshold,
				effective_threshold,
			)
			verify_results = _to_bool(params["verify_results"])
			verify_top_n = int(params["verify_top_n"])
			if verify_top_n < 1:
				raise ValueError("verify_top_n must be at least 1")
			verify_model = str(params["verify_model"]).strip() or DEFAULT_VERIFY_MODEL

			api_key = _resolve_api_key(str(params["api_key"]).strip() or None)
			if not api_key:
				raise ValueError("Google API key required. Set GOOGLE_API_KEY or GEMINI_API_KEY, or send api_key in the request.")

			results = search_articles(
				EMBEDDINGS_SOURCE,
				query,
				api_key,
				str(params["model"]).strip() or DEFAULT_MODEL,
				top_k,
				effective_threshold,
				params["documents"] if params["documents"] else None,
			)
			_debug_log("Primary search returned {} result(s)", len(results))

			if not results and effective_threshold > 0.0:
				_debug_log("Primary search empty; retrying with threshold=0.0 fallback")
				results = search_articles(
					EMBEDDINGS_SOURCE,
					query,
					api_key,
					str(params["model"]).strip() or DEFAULT_MODEL,
					top_k,
					0.0,
					params["documents"] if params["documents"] else None,
				)
				_debug_log("Fallback search returned {} result(s)", len(results))

			serialized_results = [
				_serialize_result(article, similarity)
				for article, similarity in results
			]

			ai_verification: Dict[str, Any] = {
				"enabled": False,
				"model": verify_model,
				"checked_count": 0,
				"relevant_count": 0,
				"overall": "not-run",
				"explanation": "",
			}

			if verify_results and serialized_results:
				try:
					ai_verification, serialized_results = _verify_results_with_gemini(
						query,
						serialized_results,
						api_key,
						verify_top_n,
						verify_model,
					)
				except Exception as verify_exc:
					ai_verification = {
						"enabled": False,
						"model": verify_model,
						"checked_count": 0,
						"relevant_count": 0,
						"overall": "not-run",
						"explanation": "AI verification unavailable; returning similarity-ranked results.",
						"error": str(verify_exc),
					}
					_debug_log("AI verification failed: {}", verify_exc)
		except ValueError as exc:
			_debug_log("Search request rejected: {}", exc)
			_json_response(self, 400, {"error": str(exc)})
			return
		except Exception as exc:
			_debug_log("Search request failed: {}", exc)
			_json_response(self, 500, {"error": str(exc)})
			return

		_debug_log(
			"Search response result_count={} top_similarity={} verify_results={}",
			len(results),
			results[0][1] if results else 0.0,
			verify_results,
		)

		_json_response(
			self,
			200,
			{
				"query": query,
				"model": str(params["model"]).strip() or DEFAULT_MODEL,
				"top_k": top_k,
				"threshold": threshold,
				"close_filter": close_filter,
				"close_filter_threshold": close_filter_threshold,
				"effective_threshold": effective_threshold,
				"top_similarity": results[0][1] if results else 0.0,
				"verify_results": verify_results,
				"verify_top_n": verify_top_n,
				"verify_model": verify_model,
				"ai_verification": ai_verification,
				"embeddings_source": str(EMBEDDINGS_SOURCE),
				"result_count": len(results),
				"results": serialized_results,
			},
		)

	def _handle_report(self) -> None:
		"""Generate a detailed report for provided articles using the configured LLM.
		This delegates to backend function - frontend should NOT call LLM directly.
		"""
		try:
			length = int(self.headers.get('Content-Length') or 0)
			body = self.rfile.read(length).decode('utf-8') if length else '{}'
			payload = json.loads(body)
		except Exception as exc:
			_json_response(self, 400, {"error": f"Invalid JSON body: {exc}"})
			return

		articles = payload.get('articles') or []
		report_title = str(payload.get('title') or 'Generated Legal Report')
		model = str(payload.get('model') or 'gemini-2.5-flash')
		api_key = str(payload.get('api_key') or '').strip() or None

		# Use backend function - all LLM logic is there
		try:
			result = generate_report_backend(articles, report_title, model, api_key)
			status = result.pop("status", 500)
			_json_response(self, status, result)
			return
		except Exception as exc:
			_json_response(self, 500, {"error": f"Report generation failed: {exc}"})
			return

	def _handle_models(self) -> None:
		"""Return available models for report generation.
		Response JSON: { "models": [{ "name": "gemini-2.5-flash", "provider": "gemini" }] }
		"""
		try:
			ensure_models_db()
			models = [
				{
					"name": model["name"],
					"provider": model["provider"],
					"display_name": model["name"].replace("-", " ").title(),
				}
				for model in get_active_models()
			]
		except Exception:
			models = []
		_json_response(self, 200, {"models": models})

	def _handle_dashboard_stats(self) -> None:
		"""Return admin dashboard statistics for the local backend."""
		try:
			_json_response(self, 200, _build_dashboard_stats())
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to build dashboard stats: {exc}"})

	def _handle_vectorize_report(self) -> None:
		"""Vectorize and store user-generated report in Chroma DB - BACKEND ONLY."""
		try:
			length = int(self.headers.get('Content-Length') or 0)
			body = self.rfile.read(length).decode('utf-8') if length else '{}'
			payload = json.loads(body)
		except Exception as exc:
			_json_response(self, 400, {"error": f"Invalid JSON body: {exc}"})
			return

		report_id = payload.get('report_id')
		title = payload.get('title', 'User Report')
		content = payload.get('content', '')
		timestamp = payload.get('timestamp', '')
		articles_count = payload.get('articles_count', 0)
		api_key = str(payload.get('api_key') or '').strip() or None

		if not report_id or not content:
			_json_response(self, 400, {"error": "report_id and content are required"})
			return

		# Use backend function - all embedding logic is there
		articles_payload = payload.get('articles') if isinstance(payload.get('articles'), list) else None
		result = vectorize_report_backend(report_id, title, content, timestamp, articles_count, api_key, articles=articles_payload)

		status = result.pop("status", 500)
		_json_response(self, status, result)

	def _handle_search_reports(self) -> None:
		"""Search only in user-generated reports stored in Chroma DB - BACKEND ONLY."""
		try:
			params = _parse_search_request(self)
			query = str(params["query"]).strip()

			if not query:
				raise ValueError("Query cannot be empty")

			top_k = int(params.get("top_k", 10))
			api_key = str(params.get("api_key") or '').strip() or None
			model = str(params.get("model") or '').strip() or None

			# Use backend function - all search logic is there
			result = search_reports_backend(query, top_k, api_key, model)

			status = result.pop("status", 500)
			_json_response(self, status, result)
			return
		except Exception as exc:
			_json_response(self, 500, {"error": f"Report search failed: {exc}"})
			return



	def _handle_get_llm_models(self) -> None:
		"""GET /api/llm-models - List all LLM models"""
		try:
			ensure_models_db()
			models = get_models()
			_json_response(self, 200, models)
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to retrieve models: {str(exc)}"})

	def _handle_get_llm_model(self, model_id: int) -> None:
		"""GET /api/llm-models/{id} - Get a specific LLM model"""
		try:
			model = get_model(model_id)
			if not model:
				_json_response(self, 404, {"error": f"Model with ID {model_id} not found"})
				return
			_json_response(self, 200, model)
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to retrieve model: {str(exc)}"})

	def _handle_create_llm_model(self) -> None:
		"""POST /api/llm-models - Create a new LLM model"""
		try:
			payload = _read_json_body(self)
			
			name = str(payload.get("name", "")).strip()
			provider = str(payload.get("provider", "")).strip().lower()
			api_key = str(payload.get("api_key", "")).strip()
			endpoint = str(payload.get("endpoint", "")).strip() if payload.get("endpoint") else None
			temperature = float(payload.get("temperature", 0.7))
			max_tokens = int(payload.get("max_tokens", 4000))
			is_active = bool(payload.get("is_active", True))
			
			ensure_models_db()
			model = create_model(
				name=name,
				provider=provider,
				api_key=api_key,
				endpoint=endpoint,
				temperature=temperature,
				max_tokens=max_tokens,
				is_active=is_active
			)
			_json_response(self, 201, model)
		except ModelError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except ValueError as exc:
			_json_response(self, 400, {"error": f"Invalid input: {str(exc)}"})
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to create model: {str(exc)}"})

	def _handle_update_llm_model(self, model_id: int) -> None:
		"""PUT /api/llm-models/{id} - Update an LLM model"""
		try:
			payload = _read_json_body(self)
			
			update_data = {}
			if "name" in payload:
				update_data["name"] = payload["name"]
			if "provider" in payload:
				update_data["provider"] = payload["provider"]
			if "api_key" in payload:
				update_data["api_key"] = payload["api_key"]
			if "endpoint" in payload:
				update_data["endpoint"] = payload["endpoint"]
			if "temperature" in payload:
				update_data["temperature"] = payload["temperature"]
			if "max_tokens" in payload:
				update_data["max_tokens"] = payload["max_tokens"]
			if "is_active" in payload:
				update_data["is_active"] = payload["is_active"]
			
			ensure_models_db()
			model = update_model(model_id, **update_data)
			_json_response(self, 200, model)
		except ModelError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except ValueError as exc:
			_json_response(self, 400, {"error": f"Invalid input: {str(exc)}"})
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to update model: {str(exc)}"})

	def _handle_delete_llm_model(self, model_id: int) -> None:
		"""DELETE /api/llm-models/{id} - Delete an LLM model"""
		try:
			ensure_models_db()
			result = delete_model(model_id)
			if result:
				_json_response(self, 204, {})
			else:
				_json_response(self, 404, {"error": f"Model with ID {model_id} not found"})
		except ModelError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to delete model: {str(exc)}"})

	def _handle_toggle_llm_model_status(self, model_id: int) -> None:
		"""PATCH /api/llm-models/{id}/toggle - Toggle model active status"""
		try:
			ensure_models_db()
			model = toggle_model_status(model_id)
			_json_response(self, 200, model)
		except ModelError as exc:
			_json_response(self, 400, {"error": str(exc)})
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to toggle model status: {str(exc)}"})

	def _handle_llm_models_active(self) -> None:
		"""GET /api/llm-models/active - Get active models"""
		try:
			ensure_models_db()
			models = get_active_models()
			_json_response(self, 200, models)
		except Exception as exc:
			_json_response(self, 500, {"error": f"Failed to retrieve active models: {str(exc)}"})

	def log_message(self, format: str, *args: Any) -> None:
		print(f"[{self.client_address[0]}] {format % args}")


def run_server() -> None:
	# Initialize databases
	ensure_models_db()
	
	server = ThreadingHTTPServer((HOST, PORT), SearchAPIHandler)
	print(f"Search API listening on http://{HOST}:{PORT}")
	print(f"Searching embedded files under: {EMBEDDINGS_SOURCE}")
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		print("\nStopping server...")
	finally:
		server.server_close()


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run the legal semantic search API server")
	parser.add_argument(
		"--host",
		default=HOST,
		help=f"Host/interface to bind (default: {HOST})",
	)
	parser.add_argument(
		"--port",
		type=int,
		default=PORT,
		help=f"Port to listen on (default: {PORT})",
	)
	parser.add_argument(
		"--embeddings-dir",
		default=str(EMBEDDINGS_SOURCE),
		help="Directory containing embedded JSON files (default: output/embeddings or EMBEDDINGS_DIR env var)",
	)
	return parser.parse_args()


if __name__ == "__main__":
	args = _parse_args()
	HOST = args.host
	PORT = args.port
	EMBEDDINGS_SOURCE = Path(args.embeddings_dir).resolve()
	run_server()