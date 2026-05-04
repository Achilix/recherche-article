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
from typing import Any, Dict, Optional, Tuple


AUTH_DB_DIR = Path(os.environ.get("AUTH_DB_DIR", "output/auth")).resolve()
AUTH_DB_PATH = AUTH_DB_DIR / "users.sqlite3"
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


class AuthError(ValueError):
	pass


def ensure_auth_db() -> None:
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
			role_row = connection.execute("SELECT id FROM roles WHERE name = ?", (ADMIN_SEED["role"],)).fetchone()
			if role_row is None:
				raise RuntimeError("Unable to seed admin role")
			salt_hex, hash_hex = _hash_password(ADMIN_SEED["password"])
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
					ADMIN_SEED["username"],
					ADMIN_SEED["email"],
					ADMIN_SEED["firstname"],
					ADMIN_SEED["lastname"],
					salt_hex,
					hash_hex,
					int(role_row[0]),
					ADMIN_SEED["is_blocked"],
					ADMIN_SEED["must_change_password"],
					now,
					now,
				),
			)

		connection.commit()


def authenticate_user(identifier: str, password: str) -> Dict[str, Any]:
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


def get_user_from_token(token: str) -> Optional[Dict[str, Any]]:
	payload = decode_token_payload(token)
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
	ensure_auth_db()
	with _connect() as connection:
		rows = connection.execute("SELECT id, name, description FROM roles ORDER BY id").fetchall()
		return [
			{"id": int(row["id"]), "name": row["name"], "description": row["description"]}
			for row in rows
		]


def _connect() -> sqlite3.Connection:
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


def _row_to_user(row: sqlite3.Row) -> Dict[str, Any]:
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


def decode_token_payload(token: str) -> Optional[Dict[str, Any]]:
	if not token:
		return None
	parts = token.split(".")
	if len(parts) < 2:
		return None
	try:
		payload_segment = parts[1]
		padding = "=" * (-len(payload_segment) % 4)
		payload_bytes = base64.urlsafe_b64decode(f"{payload_segment}{padding}")
		return json.loads(payload_bytes.decode("utf-8"))
	except Exception:
		return None


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