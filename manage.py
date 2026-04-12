"""Management CLI for a-u.supply. Runs inside the Docker container.

Usage (from host):
    ssh dokku run au-supply .venv/bin/python manage.py create-user <email> <password> <name> [role]
    ssh dokku run au-supply .venv/bin/python manage.py list-users
    ssh dokku run au-supply .venv/bin/python manage.py make-apikey <email> <label> <scope>
    ssh dokku run au-supply .venv/bin/python manage.py revoke-apikey <key-prefix>
"""

import sys

from auth import hash_password
from models import SessionLocal, User


def create_user(email: str, password: str, name: str, role: str = "member"):
    db = SessionLocal()
    if db.query(User).filter(User.email == email).first():
        print(f"ERROR: {email} already exists")
        db.close()
        sys.exit(1)
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    print(f"Created: {user.name} ({user.email}) role={user.role} id={user.id}")
    db.close()


def set_role(email: str, role: str):
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        print(f"ERROR: {email} not found")
        db.close()
        sys.exit(1)
    if role not in ("admin", "member"):
        print("ERROR: role must be admin or member")
        db.close()
        sys.exit(1)
    user.role = role
    db.commit()
    print(f"{user.name} ({user.email}) is now {user.role}")
    db.close()


def make_apikey(email: str, label: str, scope: str):
    from auth import generate_api_key, hash_api_key
    from models import ApiKey

    if scope not in ("read", "write", "admin"):
        print("ERROR: scope must be read, write, or admin")
        sys.exit(1)
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        print(f"ERROR: {email} not found")
        db.close()
        sys.exit(1)
    raw_key = "au_" + generate_api_key()
    ak = ApiKey(
        user_id=user.id,
        key_hash=hash_api_key(raw_key),
        key_prefix=raw_key[:11],
        label=label,
        scope=scope,
    )
    db.add(ak)
    db.commit()
    print(raw_key)
    db.close()


def revoke_apikey(prefix: str):
    from datetime import datetime, timezone
    from models import ApiKey

    db = SessionLocal()
    key = db.query(ApiKey).filter(
        ApiKey.key_prefix == prefix,
        ApiKey.revoked_at.is_(None),
    ).first()
    if not key:
        print(f"ERROR: no active key with prefix {prefix}")
        db.close()
        sys.exit(1)
    key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    print(f"Revoked: {key.label} ({key.key_prefix})")
    db.close()


def list_users():
    db = SessionLocal()
    users = db.query(User).all()
    for u in users:
        print(f"{u.id} | {u.email} | {u.name} | {u.role}")
    db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "create-user":
        if len(sys.argv) < 5:
            print("Usage: manage.py create-user <email> <password> <name> [role]")
            sys.exit(1)
        role = sys.argv[5] if len(sys.argv) > 5 else "member"
        create_user(sys.argv[2], sys.argv[3], sys.argv[4], role)

    elif cmd == "set-role":
        if len(sys.argv) < 4:
            print("Usage: manage.py set-role <email> <role>")
            sys.exit(1)
        set_role(sys.argv[2], sys.argv[3])

    elif cmd == "list-users":
        list_users()

    elif cmd == "make-apikey":
        if len(sys.argv) < 5:
            print("Usage: manage.py make-apikey <email> <label> <scope>")
            print("  scope: read, write, or admin")
            sys.exit(1)
        make_apikey(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "revoke-apikey":
        if len(sys.argv) < 3:
            print("Usage: manage.py revoke-apikey <key-prefix>")
            sys.exit(1)
        revoke_apikey(sys.argv[2])

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
