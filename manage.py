"""Management CLI for a-u.supply. Runs inside the Docker container.

Usage (from host):
    ssh dokku run au-supply python3 manage.py create-user <email> <password> <name> [role]
    ssh dokku run au-supply python3 manage.py list-users
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

    elif cmd == "list-users":
        list_users()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
