from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
	import psycopg
	from psycopg.rows import dict_row
except ImportError:
	psycopg = None
	dict_row = None


AUTH_DB_DIR = Path(os.environ.get("AUTH_DB_DIR", "output/auth")).resolve()
AUTH_DB_PATH = AUTH_DB_DIR / "users.sqlite3"
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DATABASE_URL") or ""
AUTH_JWT_SECRET = os.environ.get("AUTH_JWT_SECRET", "lex-local-auth-secret")
ACCESS_TOKEN_TTL_SECONDS = int(os.environ.get("AUTH_ACCESS_TOKEN_TTL_SECONDS", str(2 * 24 * 60 * 60)))
REFRESH_TOKEN_TTL_SECONDS = int(os.environ.get("AUTH_REFRESH_TOKEN_TTL_SECONDS", str(30 * 24 * 60 * 60)))
PASSWORD_ITERATIONS = int(os.environ.get("AUTH_PASSWORD_ITERATIONS", "120000"))

ROLE_SEEDS = [
	{"name": "Administrateur", "description": "Accès complet à la plateforme."},
	{"name": "Utilisateur", "description": "Utilisateur standard de la plateforme."},
	{"name": "Responsable Juridique", "description": "Responsable des analyses juridiques."},
]

ADMIN_SEED = {
	"username": "admin",
	"email": "admin@lex.local",
	"firstname": "Admin",
	"lastname": "System",
	"password": "Admin@12345!",
	"role": "Administrateur",
	"is_blocked": 0,
	"must_change_password": 0,
}

USER_SEEDS = [
	{
		"username": "user",
		"email": "user@lex.local",
		"firstname": "User",
		"lastname": "Test",
		"password": "User@12345!",
		"role": "Utilisateur",
		"is_blocked": 0,
		"must_change_password": 0,
	},
	{
		"username": "legal",
		"email": "legal@lex.local",
		"firstname": "Legal",
		"lastname": "Manager",
		"password": "Legal@12345!",
		"role": "Responsable Juridique",
		"is_blocked": 0,
		"must_change_password": 0,
	},
]


class AuthError(ValueError):
	pass


def _use_postgres() -> bool:
	return bool(DATABASE_URL.strip()) and psycopg is not None


def _pg_connect():
	if not _use_postgres():
		raise RuntimeError("PostgreSQL is not configured")
	return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _pg_now() -> datetime:
	return datetime.now(timezone.utc)


def _ensure_auth_pg_schema(connection) -> None:
	connection.execute(
		"""
		CREATE TABLE IF NOT EXISTS roles (
		  id bigserial PRIMARY KEY,
		  name text NOT NULL UNIQUE,
		  description text NOT NULL DEFAULT ''
		)
		"""
	)
	connection.execute(
		"""
		CREATE TABLE IF NOT EXISTS users (
		  id bigserial PRIMARY KEY,
		  username text NOT NULL UNIQUE,
		  email text NOT NULL UNIQUE,
		  firstname text NOT NULL,
		  lastname text NOT NULL,
		  password_salt text NOT NULL,
		  password_hash text NOT NULL,
		  role_id bigint NOT NULL REFERENCES roles(id) ON UPDATE CASCADE,
		  is_blocked boolean NOT NULL DEFAULT false,
		  must_change_password boolean NOT NULL DEFAULT false,
		  created_at timestamptz NOT NULL DEFAULT now(),
		  updated_at timestamptz NOT NULL DEFAULT now()
		)
		"""
	)
	for role in ROLE_SEEDS:
		connection.execute(
			"INSERT INTO roles (name, description) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
			(role["name"], role["description"]),
		)
	admin_exists = connection.execute(
		"SELECT 1 FROM users WHERE username = %s OR email = %s",
		(ADMIN_SEED["username"], ADMIN_SEED["email"]),
	).fetchone()
	if admin_exists is None:
		_seed_user_pg(connection, ADMIN_SEED)

	for seed_user in USER_SEEDS:
		_seed_user_pg(connection, seed_user)


def ensure_auth_db() -> None:
	if _use_postgres():
		with _pg_connect() as connection:
			_ensure_auth_pg_schema(connection)
			connection.commit()
		return

	AUTH_DB_DIR.mkdir(parents=True, exist_ok=True)
	with sqlite3.connect(AUTH_DB_PATH) as connection:
		connection.execute("PRAGMA foreign_keys = ON")
		connection.executescript(
			"""
			CREATE TABLE IF NOT EXISTS roles (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				name TEXT NOT NULL UNIQUE,
				description TEXT NOT NULL DEFAULT ''
			);

			CREATE TABLE IF NOT EXISTS users (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				username TEXT NOT NULL UNIQUE,
				email TEXT NOT NULL UNIQUE,
				firstname TEXT NOT NULL,
				lastname TEXT NOT NULL,
				password_salt TEXT NOT NULL,
				password_hash TEXT NOT NULL,
				role_id INTEGER NOT NULL,
				is_blocked INTEGER NOT NULL DEFAULT 0,
				must_change_password INTEGER NOT NULL DEFAULT 0,
				created_at INTEGER NOT NULL,
				updated_at INTEGER NOT NULL,
				FOREIGN KEY (role_id) REFERENCES roles (id)
			);
			"""
		)

		for role in ROLE_SEEDS:
			connection.execute(
				"INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?)",
				(role["name"], role["description"]),
			)

		cursor = connection.execute("SELECT id FROM users WHERE username = ? OR email = ?", (ADMIN_SEED["username"], ADMIN_SEED["email"]))
		if cursor.fetchone() is None:
			_seed_user_sqlite(connection, ADMIN_SEED)

		for seed_user in USER_SEEDS:
			_seed_user_sqlite(connection, seed_user)

		connection.commit()


def authenticate_user(identifier: str, password: str) -> Dict[str, Any]:
	if _use_postgres():
		ensure_auth_db()
		identifier_value = (identifier or "").strip()
		if not identifier_value:
			raise AuthError("Username or email is required")
		if not password:
			raise AuthError("Password is required")
		with _pg_connect() as connection:
			row = connection.execute(
				"""
				SELECT
					u.id, u.username, u.email, u.firstname, u.lastname,
					u.password_salt, u.password_hash, u.is_blocked, u.must_change_password,
					r.id AS role_id, r.name AS role_name, r.description AS role_description
				FROM users u
				JOIN roles r ON r.id = u.role_id
				WHERE LOWER(u.username) = LOWER(%s) OR LOWER(u.email) = LOWER(%s)
				""",
				(identifier_value, identifier_value),
			).fetchone()
			if row is None:
				raise AuthError("Invalid username or password")
			if bool(row["is_blocked"]):
				raise AuthError("This account is blocked")
			if not _verify_password(password, row["password_salt"], row["password_hash"]):
				raise AuthError("Invalid username or password")
			return _row_to_user(row)

	ensure_auth_db()
	identifier_value = (identifier or "").strip()
	if not identifier_value:
		raise AuthError("Username or email is required")
	if not password:
		raise AuthError("Password is required")

	with _connect() as connection:
		row = connection.execute(
			"""
			SELECT
				u.id, u.username, u.email, u.firstname, u.lastname,
				u.password_salt, u.password_hash, u.is_blocked, u.must_change_password,
				r.id AS role_id, r.name AS role_name, r.description AS role_description
			FROM users u
			JOIN roles r ON r.id = u.role_id
			WHERE LOWER(u.username) = LOWER(?) OR LOWER(u.email) = LOWER(?)
			""",
			(identifier_value, identifier_value),
		).fetchone()

		if row is None:
			raise AuthError("Invalid username or password")
		if int(row["is_blocked"]) == 1:
			raise AuthError("This account is blocked")
		if not _verify_password(password, row["password_salt"], row["password_hash"]):
			raise AuthError("Invalid username or password")

		return _row_to_user(row)


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
	if _use_postgres():
		ensure_auth_db()
		with _pg_connect() as connection:
			row = connection.execute(
				"""
				SELECT
					u.id, u.username, u.email, u.firstname, u.lastname,
					u.is_blocked, u.must_change_password,
					r.id AS role_id, r.name AS role_name, r.description AS role_description
				FROM users u
				JOIN roles r ON r.id = u.role_id
				WHERE u.id = %s
				""",
				(user_id,),
			).fetchone()
			if row is None:
				return None
			return _row_to_user(row)

	ensure_auth_db()
	with _connect() as connection:
		row = connection.execute(
			"""
			SELECT
				u.id, u.username, u.email, u.firstname, u.lastname,
				u.is_blocked, u.must_change_password,
				r.id AS role_id, r.name AS role_name, r.description AS role_description
			FROM users u
			JOIN roles r ON r.id = u.role_id
			WHERE u.id = ?
			""",
			(user_id,),
		).fetchone()

		if row is None:
			return None
		return _row_to_user(row)


def list_users(filters: Optional[Dict[str, Any]] = None) -> list[Dict[str, Any]]:
	if _use_postgres():
		ensure_auth_db()
		filters = filters or {}
		query_clauses: list[str] = []
		query_params: list[Any] = []

		role_filter = str(filters.get("role") or "").strip()
		if role_filter:
			query_clauses.append("LOWER(r.name) = LOWER(%s)")
			query_params.append(role_filter)

		search_term = str(filters.get("query") or filters.get("search") or "").strip()
		if search_term:
			like_value = f"%{search_term.lower()}%"
			query_clauses.append("(LOWER(u.username) LIKE %s OR LOWER(u.email) LIKE %s OR LOWER(u.firstname) LIKE %s OR LOWER(u.lastname) LIKE %s)")
			query_params.extend([like_value, like_value, like_value, like_value])

		for field in ("username", "email", "firstname", "lastname"):
			value = str(filters.get(field) or "").strip()
			if value:
				query_clauses.append(f"LOWER(u.{field}) LIKE %s")
				query_params.append(f"%{value.lower()}%")

		for field in ("is_blocked", "must_change_password"):
			if field in filters and filters[field] not in (None, ""):
				query_clauses.append(f"u.{field} = %s")
				query_params.append(_to_bool(filters[field]))

		sql = """
			SELECT
				u.id, u.username, u.email, u.firstname, u.lastname,
				u.is_blocked, u.must_change_password,
				r.id AS role_id, r.name AS role_name, r.description AS role_description
			FROM users u
			JOIN roles r ON r.id = u.role_id
		"""
		if query_clauses:
			sql += " WHERE " + " AND ".join(query_clauses)
		sql += " ORDER BY u.id ASC"

		with _pg_connect() as connection:
			rows = connection.execute(sql, query_params).fetchall()
			return [_row_to_user(row) for row in rows]

	ensure_auth_db()
	filters = filters or {}
	query_clauses: list[str] = []
	query_params: list[Any] = []

	role_filter = str(filters.get("role") or "").strip()
	if role_filter:
		query_clauses.append("LOWER(r.name) = LOWER(?)")
		query_params.append(role_filter)

	search_term = str(filters.get("query") or filters.get("search") or "").strip()
	if search_term:
		like_value = f"%{search_term.lower()}%"
		query_clauses.append("(LOWER(u.username) LIKE ? OR LOWER(u.email) LIKE ? OR LOWER(u.firstname) LIKE ? OR LOWER(u.lastname) LIKE ?)")
		query_params.extend([like_value, like_value, like_value, like_value])

	for field in ("username", "email", "firstname", "lastname"):
		value = str(filters.get(field) or "").strip()
		if value:
			query_clauses.append(f"LOWER(u.{field}) LIKE ?")
			query_params.append(f"%{value.lower()}%")

	for field in ("is_blocked", "must_change_password"):
		if field in filters and filters[field] not in (None, ""):
			query_clauses.append(f"u.{field} = ?")
			query_params.append(1 if _to_bool(filters[field]) else 0)

	sql = """
		SELECT
			u.id, u.username, u.email, u.firstname, u.lastname,
			u.is_blocked, u.must_change_password,
			r.id AS role_id, r.name AS role_name, r.description AS role_description
		FROM users u
		JOIN roles r ON r.id = u.role_id
	"""
	if query_clauses:
		sql += " WHERE " + " AND ".join(query_clauses)
	sql += " ORDER BY u.id ASC"

	with _connect() as connection:
		rows = connection.execute(sql, query_params).fetchall()
		return [_row_to_user(row) for row in rows]


def _resolve_role_id(connection: sqlite3.Connection, role_value: Any) -> int:
	if _use_postgres():
		role_text = str(role_value or "").strip()
		if not role_text:
			raise AuthError("Role is required")
		row = connection.execute(
			"SELECT id FROM roles WHERE LOWER(name) = LOWER(%s)",
			(role_text,),
		).fetchone()
		if row is None:
			raise AuthError(f"Unknown role: {role_text}")
		return int(row["id"])

	role_text = str(role_value or "").strip()
	if not role_text:
		raise AuthError("Role is required")

	row = connection.execute(
		"SELECT id FROM roles WHERE LOWER(name) = LOWER(?)",
		(role_text,),
	).fetchone()
	if row is None:
		raise AuthError(f"Unknown role: {role_text}")
	return int(row["id"])


def create_user(user_data: Dict[str, Any]) -> Dict[str, Any]:
	if _use_postgres():
		ensure_auth_db()
		username = str(user_data.get("username") or "").strip()
		email = str(user_data.get("email") or "").strip()
		firstname = str(user_data.get("firstname") or "").strip()
		lastname = str(user_data.get("lastname") or "").strip()
		password = str(user_data.get("password") or "")

		if not username:
			raise AuthError("Username is required")
		if not email:
			raise AuthError("Email is required")
		if not firstname:
			raise AuthError("Firstname is required")
		if not lastname:
			raise AuthError("Lastname is required")
		if not _is_strong_password(password):
			raise AuthError("The password must contain at least 8 characters, one uppercase letter, one lowercase letter, and one number")

		with _pg_connect() as connection:
			role_id = _resolve_role_id(connection, user_data.get("role"))
			now = _pg_now()
			salt_hex, hash_hex = _hash_password(password)
			is_blocked = bool(_to_bool(user_data.get("is_blocked")))
			must_change_password = bool(_to_bool(user_data.get("must_change_password")))
			cursor = connection.execute(
				"""
				INSERT INTO users (
					username, email, firstname, lastname,
					password_salt, password_hash, role_id,
					is_blocked, must_change_password, created_at, updated_at
				) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
				RETURNING id
				""",
				(
					username,
					email,
					firstname,
					lastname,
					salt_hex,
					hash_hex,
					role_id,
					is_blocked,
					must_change_password,
					now,
					now,
				),
			)
			connection.commit()
			new_user_id = int(cursor.fetchone()[0])
			return get_user_by_id(new_user_id) or {"id": new_user_id}

	ensure_auth_db()
	username = str(user_data.get("username") or "").strip()
	email = str(user_data.get("email") or "").strip()
	firstname = str(user_data.get("firstname") or "").strip()
	lastname = str(user_data.get("lastname") or "").strip()
	password = str(user_data.get("password") or "")

	if not username:
		raise AuthError("Username is required")
	if not email:
		raise AuthError("Email is required")
	if not firstname:
		raise AuthError("Firstname is required")
	if not lastname:
		raise AuthError("Lastname is required")
	if not _is_strong_password(password):
		raise AuthError("The password must contain at least 8 characters, one uppercase letter, one lowercase letter, and one number")

	with _connect() as connection:
		role_id = _resolve_role_id(connection, user_data.get("role"))
		now = int(time.time())
		salt_hex, hash_hex = _hash_password(password)
		is_blocked = 1 if _to_bool(user_data.get("is_blocked")) else 0
		must_change_password = 1 if _to_bool(user_data.get("must_change_password")) else 0
		cursor = connection.execute(
			"""
			INSERT INTO users (
				username, email, firstname, lastname,
				password_salt, password_hash, role_id,
				is_blocked, must_change_password, created_at, updated_at
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(
				username,
				email,
				firstname,
				lastname,
				salt_hex,
				hash_hex,
				role_id,
				is_blocked,
				must_change_password,
				now,
				now,
			),
		)
		connection.commit()
		return get_user_by_id(int(cursor.lastrowid)) or {"id": int(cursor.lastrowid)}


def update_user(user_id: int, user_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
	if _use_postgres():
		ensure_auth_db()
		fields: list[str] = []
		params: list[Any] = []

		for field in ("username", "email", "firstname", "lastname"):
			value = str(user_data.get(field) or "").strip()
			if value:
				fields.append(f"{field} = %s")
				params.append(value)

		password = str(user_data.get("password") or "")
		if password:
			if not _is_strong_password(password):
				raise AuthError("The password must contain at least 8 characters, one uppercase letter, one lowercase letter, and one number")
			salt_hex, hash_hex = _hash_password(password)
			fields.extend(["password_salt = %s", "password_hash = %s"])
			params.extend([salt_hex, hash_hex])

		with _pg_connect() as connection:
			if "role" in user_data and user_data.get("role") not in (None, ""):
				role_id = _resolve_role_id(connection, user_data.get("role"))
				fields.append("role_id = %s")
				params.append(role_id)

			if "is_blocked" in user_data and user_data.get("is_blocked") not in (None, ""):
				fields.append("is_blocked = %s")
				params.append(bool(_to_bool(user_data.get("is_blocked"))))

			if "must_change_password" in user_data and user_data.get("must_change_password") not in (None, ""):
				fields.append("must_change_password = %s")
				params.append(bool(_to_bool(user_data.get("must_change_password"))))

			if not fields:
				raise AuthError("No updatable user fields were provided")

			fields.append("updated_at = %s")
			params.append(_pg_now())
			params.append(user_id)
			connection.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = %s", params)
			connection.commit()
			return get_user_by_id(user_id)

	ensure_auth_db()
	fields: list[str] = []
	params: list[Any] = []

	for field in ("username", "email", "firstname", "lastname"):
		value = str(user_data.get(field) or "").strip()
		if value:
			fields.append(f"{field} = ?")
			params.append(value)

	password = str(user_data.get("password") or "")
	if password:
		if not _is_strong_password(password):
			raise AuthError("The password must contain at least 8 characters, one uppercase letter, one lowercase letter, and one number")
		salt_hex, hash_hex = _hash_password(password)
		fields.extend(["password_salt = ?", "password_hash = ?"])
		params.extend([salt_hex, hash_hex])

	with _connect() as connection:
		if "role" in user_data and user_data.get("role") not in (None, ""):
			role_id = _resolve_role_id(connection, user_data.get("role"))
			fields.append("role_id = ?")
			params.append(role_id)

		if "is_blocked" in user_data and user_data.get("is_blocked") not in (None, ""):
			fields.append("is_blocked = ?")
			params.append(1 if _to_bool(user_data.get("is_blocked")) else 0)

		if "must_change_password" in user_data and user_data.get("must_change_password") not in (None, ""):
			fields.append("must_change_password = ?")
			params.append(1 if _to_bool(user_data.get("must_change_password")) else 0)

		if not fields:
			raise AuthError("No updatable user fields were provided")

		fields.append("updated_at = ?")
		params.append(int(time.time()))
		params.append(user_id)
		connection.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)
		connection.commit()
		return get_user_by_id(user_id)


def delete_user(user_id: int) -> bool:
	if _use_postgres():
		ensure_auth_db()
		with _pg_connect() as connection:
			cursor = connection.execute("DELETE FROM users WHERE id = %s", (user_id,))
			connection.commit()
			return cursor.rowcount > 0

	ensure_auth_db()
	with _connect() as connection:
		cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
		connection.commit()
		return cursor.rowcount > 0


def toggle_user_blocked_status(user_id: int) -> Optional[Dict[str, Any]]:
	if _use_postgres():
		ensure_auth_db()
		with _pg_connect() as connection:
			row = connection.execute("SELECT is_blocked FROM users WHERE id = %s", (user_id,)).fetchone()
			if row is None:
				return None
			new_status = not bool(row["is_blocked"])
			connection.execute(
				"UPDATE users SET is_blocked = %s, updated_at = %s WHERE id = %s",
				(new_status, _pg_now(), user_id),
			)
			connection.commit()
			return get_user_by_id(user_id)

	ensure_auth_db()
	with _connect() as connection:
		row = connection.execute("SELECT is_blocked FROM users WHERE id = ?", (user_id,)).fetchone()
		if row is None:
			return None
		new_status = 0 if int(row["is_blocked"]) == 1 else 1
		connection.execute(
			"UPDATE users SET is_blocked = ?, updated_at = ? WHERE id = ?",
			(new_status, int(time.time()), user_id),
		)
		connection.commit()
		return get_user_by_id(user_id)


def reset_user_password(user_id: int) -> str:
	if _use_postgres():
		ensure_auth_db()
		generated_password = secrets.token_urlsafe(12) + "Aa1"
		while not _is_strong_password(generated_password):
			generated_password = secrets.token_urlsafe(12) + "Aa1"

		salt_hex, hash_hex = _hash_password(generated_password)
		with _pg_connect() as connection:
			cursor = connection.execute(
				"""
				UPDATE users
				SET password_salt = %s, password_hash = %s, must_change_password = %s, updated_at = %s
				WHERE id = %s
				""",
				(salt_hex, hash_hex, True, _pg_now(), user_id),
			)
			connection.commit()
			if cursor.rowcount == 0:
				raise AuthError("User not found")
		return generated_password

	ensure_auth_db()
	generated_password = secrets.token_urlsafe(12) + "Aa1"
	while not _is_strong_password(generated_password):
		generated_password = secrets.token_urlsafe(12) + "Aa1"

	salt_hex, hash_hex = _hash_password(generated_password)
	with _connect() as connection:
		cursor = connection.execute(
			"""
			UPDATE users
			SET password_salt = ?, password_hash = ?, must_change_password = 1, updated_at = ?
			WHERE id = ?
			""",
			(salt_hex, hash_hex, int(time.time()), user_id),
		)
		connection.commit()
		if cursor.rowcount == 0:
			raise AuthError("User not found")
	return generated_password


def get_user_from_token(token: str, expected_token_type: str = "access") -> Optional[Dict[str, Any]]:
	payload = decode_token_payload(token, expected_token_type=expected_token_type)
	if not payload:
		return None

	expiry = int(payload.get("exp") or 0)
	if expiry <= int(time.time()):
		return None

	user_id = payload.get("sub")
	if user_id is None:
		return None

	try:
		return get_user_by_id(int(user_id))
	except (TypeError, ValueError):
		return None


def issue_token_pair(user: Dict[str, Any]) -> Tuple[str, str]:
	return (
		_create_token(user, "access", ACCESS_TOKEN_TTL_SECONDS),
		_create_token(user, "refresh", REFRESH_TOKEN_TTL_SECONDS),
	)


def update_password(user_id: int, current_password: str, new_password: str) -> None:
	if _use_postgres():
		if not current_password:
			raise AuthError("Current password is required")
		if not _is_strong_password(new_password):
			raise AuthError("The new password must contain at least 8 characters, one uppercase letter, one lowercase letter, and one number")

		ensure_auth_db()
		with _pg_connect() as connection:
			row = connection.execute(
				"SELECT password_salt, password_hash FROM users WHERE id = %s",
				(user_id,),
			).fetchone()
			if row is None:
				raise AuthError("User not found")
			if not _verify_password(current_password, row["password_salt"], row["password_hash"]):
				raise AuthError("Current password is incorrect")

			salt_hex, hash_hex = _hash_password(new_password)
			connection.execute(
				"""
				UPDATE users
				SET password_salt = %s, password_hash = %s, must_change_password = %s, updated_at = %s
				WHERE id = %s
				""",
				(salt_hex, hash_hex, False, _pg_now(), user_id),
			)
			connection.commit()
		return

	if not current_password:
		raise AuthError("Current password is required")
	if not _is_strong_password(new_password):
		raise AuthError("The new password must contain at least 8 characters, one uppercase letter, one lowercase letter, and one number")

	ensure_auth_db()
	with _connect() as connection:
		row = connection.execute(
			"SELECT password_salt, password_hash FROM users WHERE id = ?",
			(user_id,),
		).fetchone()
		if row is None:
			raise AuthError("User not found")
		if not _verify_password(current_password, row["password_salt"], row["password_hash"]):
			raise AuthError("Current password is incorrect")

		salt_hex, hash_hex = _hash_password(new_password)
		connection.execute(
			"""
			UPDATE users
			SET password_salt = ?, password_hash = ?, must_change_password = 0, updated_at = ?
			WHERE id = ?
			""",
			(salt_hex, hash_hex, int(time.time()), user_id),
		)
		connection.commit()


def list_roles() -> list[Dict[str, Any]]:
	if _use_postgres():
		ensure_auth_db()
		with _pg_connect() as connection:
			rows = connection.execute("SELECT id, name, description FROM roles ORDER BY id").fetchall()
			return [
				{"id": int(row["id"]), "name": row["name"], "description": row["description"]}
				for row in rows
			]

	ensure_auth_db()
	with _connect() as connection:
		rows = connection.execute("SELECT id, name, description FROM roles ORDER BY id").fetchall()
		return [
			{"id": int(row["id"]), "name": row["name"], "description": row["description"]}
			for row in rows
		]


def _connect() -> sqlite3.Connection:
	if _use_postgres():
		raise RuntimeError("SQLite connection is not available when DATABASE_URL is configured")
	connection = sqlite3.connect(AUTH_DB_PATH)
	connection.row_factory = sqlite3.Row
	connection.execute("PRAGMA foreign_keys = ON")
	return connection


def _hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
	salt_bytes = salt or secrets.token_bytes(16)
	digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PASSWORD_ITERATIONS)
	return salt_bytes.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
	_, computed_hash = _hash_password(password, bytes.fromhex(salt_hex))
	return hmac.compare_digest(computed_hash, hash_hex)


def _is_strong_password(password: str) -> bool:
	if len(password) < 8:
		return False
	return any(char.islower() for char in password) and any(char.isupper() for char in password) and any(char.isdigit() for char in password)


def _to_bool(value: Any) -> bool:
	if isinstance(value, bool):
		return value
	if value is None:
		return False
	if isinstance(value, (int, float)):
		return value != 0
	return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _row_to_user(row: sqlite3.Row) -> Dict[str, Any]:
	if isinstance(row, dict):
		return {
			"id": int(row["id"]),
			"username": row["username"],
			"email": row["email"],
			"firstname": row["firstname"],
			"lastname": row["lastname"],
			"is_blocked": bool(row["is_blocked"]),
			"must_change_password": bool(row["must_change_password"]),
			"role": {
				"id": int(row["role_id"]),
				"name": row["role_name"],
				"description": row["role_description"],
			},
		}
	return {
		"id": int(row["id"]),
		"username": row["username"],
		"email": row["email"],
		"firstname": row["firstname"],
		"lastname": row["lastname"],
		"is_blocked": bool(int(row["is_blocked"])),
		"must_change_password": bool(int(row["must_change_password"])),
		"role": {
			"id": int(row["role_id"]),
			"name": row["role_name"],
			"description": row["role_description"],
		},
	}


def _seed_user_pg(connection, seed_user: Dict[str, Any]) -> None:
	cursor = connection.execute(
		"SELECT 1 FROM users WHERE username = %s OR email = %s",
		(seed_user["username"], seed_user["email"]),
	).fetchone()
	if cursor is not None:
		return

	role_row = connection.execute(
		"SELECT id FROM roles WHERE name = %s",
		(seed_user["role"],),
	).fetchone()
	if role_row is None:
		raise RuntimeError(f"Unable to seed role: {seed_user['role']}")

	salt_hex, hash_hex = _hash_password(seed_user["password"])
	now = _pg_now()
	connection.execute(
		"""
		INSERT INTO users (
			username, email, firstname, lastname,
			password_salt, password_hash, role_id,
			is_blocked, must_change_password, created_at, updated_at
		) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
		""",
		(
			seed_user["username"],
			seed_user["email"],
			seed_user["firstname"],
			seed_user["lastname"],
			salt_hex,
			hash_hex,
			int(role_row["id"]),
			bool(seed_user["is_blocked"]),
			bool(seed_user["must_change_password"]),
			now,
			now,
		),
	)


def _seed_user_sqlite(connection, seed_user: Dict[str, Any]) -> None:
	cursor = connection.execute(
		"SELECT 1 FROM users WHERE username = ? OR email = ?",
		(seed_user["username"], seed_user["email"]),
	).fetchone()
	if cursor is not None:
		return

	role_row = connection.execute(
		"SELECT id FROM roles WHERE name = ?",
		(seed_user["role"],),
	).fetchone()
	if role_row is None:
		raise RuntimeError(f"Unable to seed role: {seed_user['role']}")

	salt_hex, hash_hex = _hash_password(seed_user["password"])
	now = int(time.time())
	connection.execute(
		"""
		INSERT INTO users (
			username, email, firstname, lastname,
			password_salt, password_hash, role_id,
			is_blocked, must_change_password, created_at, updated_at
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			seed_user["username"],
			seed_user["email"],
			seed_user["firstname"],
			seed_user["lastname"],
			salt_hex,
			hash_hex,
			int(role_row[0]),
			int(bool(seed_user["is_blocked"])),
			int(bool(seed_user["must_change_password"])),
			now,
			now,
		),
	)


def _create_token(user: Dict[str, Any], token_type: str, ttl_seconds: int) -> str:
	now = int(time.time())
	payload = {
		"sub": int(user["id"]),
		"username": user["username"],
		"email": user["email"],
		"firstname": user["firstname"],
		"lastname": user["lastname"],
		"role": user["role"]["name"],
		"role_id": int(user["role"]["id"]),
		"is_blocked": bool(user["is_blocked"]),
		"must_change_password": bool(user["must_change_password"]),
		"token_type": token_type,
		"iat": now,
		"exp": now + ttl_seconds,
		"jti": secrets.token_hex(16),
	}
	headers = {"alg": "HS256", "typ": "JWT"}
	encoded_header = _base64url_json(headers)
	encoded_payload = _base64url_json(payload)
	signature = _base64url_bytes(
		hmac.new(AUTH_JWT_SECRET.encode("utf-8"), f"{encoded_header}.{encoded_payload}".encode("utf-8"), hashlib.sha256).digest()
	)
	return f"{encoded_header}.{encoded_payload}.{signature}"


def _base64url_json(payload: Dict[str, Any]) -> str:
	return _base64url_bytes(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _base64url_bytes(value: bytes) -> str:
	return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_token_payload(token: str, expected_token_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
	if not token:
		return None
	parts = token.split(".")
	if len(parts) != 3:
		return None

	header_segment, payload_segment, signature_segment = parts
	try:
		header = _decode_base64url_json(header_segment)
		if not isinstance(header, dict):
			return None
		if header.get("alg") != "HS256" or header.get("typ") != "JWT":
			return None

		expected_signature = _base64url_bytes(
			hmac.new(
				AUTH_JWT_SECRET.encode("utf-8"),
				f"{header_segment}.{payload_segment}".encode("utf-8"),
				hashlib.sha256,
			).digest()
		)
		if not hmac.compare_digest(expected_signature, signature_segment):
			return None

		payload = _decode_base64url_json(payload_segment)
		if not isinstance(payload, dict):
			return None
		if expected_token_type and payload.get("token_type") != expected_token_type:
			return None
		expiry = int(payload.get("exp") or 0)
		if expiry <= int(time.time()):
			return None
		return payload
	except Exception:
		return None


def _decode_base64url_json(segment: str) -> Any:
	padding = "=" * (-len(segment) % 4)
	decoded = base64.urlsafe_b64decode(f"{segment}{padding}")
	return json.loads(decoded.decode("utf-8"))


def extract_bearer_token(authorization_header: str | None) -> Optional[str]:
	if not authorization_header:
		return None
	parts = authorization_header.split(" ", 1)
	if len(parts) != 2:
		return None
	scheme, token = parts[0].strip(), parts[1].strip()
	if scheme.lower() != "bearer" or not token:
		return None
	return token


ensure_auth_db()