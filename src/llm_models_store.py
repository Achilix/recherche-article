"""
LLM Models storage and management.
"""

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

# Database configuration
MODELS_DB_DIR = Path(os.environ.get("MODELS_DB_DIR", "output/models")).resolve()
MODELS_DB_PATH = MODELS_DB_DIR / "llm_models.sqlite3"


class ModelError(ValueError):
	pass


def ensure_models_db() -> None:
	"""Ensure the models database exists and is initialized with schema."""
	MODELS_DB_DIR.mkdir(parents=True, exist_ok=True)
	
	with sqlite3.connect(MODELS_DB_PATH) as connection:
		connection.execute("PRAGMA foreign_keys = ON")
		connection.executescript("""
			CREATE TABLE IF NOT EXISTS llm_models (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				name TEXT NOT NULL UNIQUE,
				provider TEXT NOT NULL,
				api_key TEXT NOT NULL,
				endpoint TEXT,
				temperature REAL DEFAULT 0.7,
				max_tokens INTEGER DEFAULT 4000,
				is_active INTEGER DEFAULT 1,
				created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
				updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
			);
		""")
		connection.commit()


def get_models() -> List[Dict[str, Any]]:
	"""Retrieve all LLM models from database."""
	ensure_models_db()
	
	with sqlite3.connect(MODELS_DB_PATH) as connection:
		connection.row_factory = sqlite3.Row
		cursor = connection.execute("SELECT * FROM llm_models ORDER BY created_at DESC")
		models = [dict(row) for row in cursor.fetchall()]
	
	return models


def get_model(model_id: int) -> Optional[Dict[str, Any]]:
	"""Retrieve a specific LLM model by ID."""
	ensure_models_db()
	
	with sqlite3.connect(MODELS_DB_PATH) as connection:
		connection.row_factory = sqlite3.Row
		cursor = connection.execute("SELECT * FROM llm_models WHERE id = ?", (model_id,))
		row = cursor.fetchone()
	
	return dict(row) if row else None


def get_active_models() -> List[Dict[str, str]]:
	"""Retrieve all active models (name and provider only)."""
	ensure_models_db()
	
	with sqlite3.connect(MODELS_DB_PATH) as connection:
		connection.row_factory = sqlite3.Row
		cursor = connection.execute(
			"SELECT name, provider FROM llm_models WHERE is_active = 1 ORDER BY name"
		)
		models = [dict(row) for row in cursor.fetchall()]
	
	return models


def create_model(name: str, provider: str, api_key: str, endpoint: Optional[str] = None,
				 temperature: float = 0.7, max_tokens: int = 4000, is_active: bool = True) -> Dict[str, Any]:
	"""Create a new LLM model."""
	ensure_models_db()
	
	# Validate required fields
	if not name or not name.strip():
		raise ModelError("Model name is required")
	if not provider or not provider.strip():
		raise ModelError("Provider is required")
	if not api_key or not api_key.strip():
		raise ModelError("API key is required")
	
	name = name.strip()
	provider = provider.strip().lower()
	
	# Validate provider
	valid_providers = ["openai", "gemini", "anthropic", "azure"]
	if provider not in valid_providers:
		raise ModelError(f"Provider must be one of: {', '.join(valid_providers)}")
	
	# Validate temperature
	if not 0 <= temperature <= 2:
		raise ModelError("Temperature must be between 0 and 2")
	
	# Validate max_tokens
	if max_tokens < 1:
		raise ModelError("Max tokens must be at least 1")
	
	try:
		with sqlite3.connect(MODELS_DB_PATH) as connection:
			cursor = connection.execute(
				"""
				INSERT INTO llm_models (name, provider, api_key, endpoint, temperature, max_tokens, is_active)
				VALUES (?, ?, ?, ?, ?, ?, ?)
				""",
				(name, provider, api_key, endpoint, temperature, max_tokens, 1 if is_active else 0)
			)
			connection.commit()
			model_id = cursor.lastrowid
	except sqlite3.IntegrityError as e:
		if "UNIQUE constraint failed" in str(e):
			raise ModelError(f"A model with name '{name}' already exists")
		raise ModelError(f"Database error: {str(e)}")
	
	model = get_model(model_id)
	if not model:
		raise ModelError("Failed to create model")
	
	return model


def update_model(model_id: int, **kwargs) -> Dict[str, Any]:
	"""Update an existing LLM model."""
	ensure_models_db()
	
	# Get existing model
	model = get_model(model_id)
	if not model:
		raise ModelError(f"Model with ID {model_id} not found")
	
	# Build update query dynamically
	allowed_fields = ["name", "provider", "api_key", "endpoint", "temperature", "max_tokens", "is_active"]
	update_fields = {}
	
	for field in allowed_fields:
		if field in kwargs and kwargs[field] is not None:
			value = kwargs[field]
			
			# Validation
			if field == "name":
				if not str(value).strip():
					raise ModelError("Model name cannot be empty")
				value = str(value).strip()
			elif field == "provider":
				provider = str(value).strip().lower()
				valid_providers = ["openai", "gemini", "anthropic", "azure"]
				if provider not in valid_providers:
					raise ModelError(f"Provider must be one of: {', '.join(valid_providers)}")
				value = provider
			elif field == "api_key":
				if not str(value).strip():
					raise ModelError("API key cannot be empty")
				value = str(value).strip()
			elif field == "endpoint":
				value = str(value).strip() if value else None
			elif field == "temperature":
				value = float(value)
				if not 0 <= value <= 2:
					raise ModelError("Temperature must be between 0 and 2")
			elif field == "max_tokens":
				value = int(value)
				if value < 1:
					raise ModelError("Max tokens must be at least 1")
			elif field == "is_active":
				value = 1 if value else 0
			
			update_fields[field] = value
	
	if not update_fields:
		return model
	
	# Add updated_at timestamp
	update_fields["updated_at"] = datetime.now().isoformat()
	
	try:
		with sqlite3.connect(MODELS_DB_PATH) as connection:
			set_clause = ", ".join([f"{key} = ?" for key in update_fields.keys()])
			values = list(update_fields.values()) + [model_id]
			
			connection.execute(
				f"UPDATE llm_models SET {set_clause} WHERE id = ?",
				values
			)
			connection.commit()
	except sqlite3.IntegrityError as e:
		if "UNIQUE constraint failed" in str(e):
			raise ModelError(f"A model with that name already exists")
		raise ModelError(f"Database error: {str(e)}")
	
	model = get_model(model_id)
	if not model:
		raise ModelError("Failed to update model")
	
	return model


def delete_model(model_id: int) -> bool:
	"""Delete an LLM model."""
	ensure_models_db()
	
	model = get_model(model_id)
	if not model:
		raise ModelError(f"Model with ID {model_id} not found")
	
	with sqlite3.connect(MODELS_DB_PATH) as connection:
		cursor = connection.execute("DELETE FROM llm_models WHERE id = ?", (model_id,))
		connection.commit()
	
	return cursor.rowcount > 0


def toggle_model_status(model_id: int) -> Dict[str, Any]:
	"""Toggle the active status of a model."""
	ensure_models_db()
	
	model = get_model(model_id)
	if not model:
		raise ModelError(f"Model with ID {model_id} not found")
	
	new_status = 1 - model["is_active"]  # Toggle between 0 and 1
	
	with sqlite3.connect(MODELS_DB_PATH) as connection:
		connection.execute(
			"UPDATE llm_models SET is_active = ?, updated_at = ? WHERE id = ?",
			(new_status, datetime.now().isoformat(), model_id)
		)
		connection.commit()
	
	model = get_model(model_id)
	if not model:
		raise ModelError("Failed to toggle model status")
	
	return model
