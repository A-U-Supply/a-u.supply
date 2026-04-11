#!/usr/bin/env python3
"""CLI for managing users. No public signup — use this to create/invite users.

Usage:
    python cli.py create-user --email admin@example.com --name "Admin" --password secret --role admin
    python cli.py list-users
    python cli.py delete-user --email user@example.com
"""
import argparse
import sys
from pathlib import Path

from auth import hash_password
from models import Base, SessionLocal, User, engine

Path("data").mkdir(exist_ok=True)
Base.metadata.create_all(bind=engine)


def create_user(args):
    db = SessionLocal()
    if db.query(User).filter(User.email == args.email).first():
        print(f"Error: {args.email} already exists")
        sys.exit(1)
    if args.role not in ("admin", "member"):
        print("Error: role must be admin or member")
        sys.exit(1)
    user = User(
        email=args.email,
        name=args.name,
        password_hash=hash_password(args.password),
        role=args.role,
    )
    db.add(user)
    db.commit()
    print(f"Created {args.role}: {args.name} <{args.email}>")
    db.close()


def list_users(args):
    db = SessionLocal()
    users = db.query(User).order_by(User.created_at).all()
    if not users:
        print("No users")
        return
    for u in users:
        print(f"  [{u.role:6}] {u.name} <{u.email}>  (id={u.id})")
    db.close()


def delete_user(args):
    db = SessionLocal()
    user = db.query(User).filter(User.email == args.email).first()
    if not user:
        print(f"Error: {args.email} not found")
        sys.exit(1)
    db.delete(user)
    db.commit()
    print(f"Deleted: {user.name} <{user.email}>")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="a-u.supply user management")
    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser("create-user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--role", default="member", choices=["admin", "member"])

    sub.add_parser("list-users")

    delete = sub.add_parser("delete-user")
    delete.add_argument("--email", required=True)

    args = parser.parse_args()
    if args.command == "create-user":
        create_user(args)
    elif args.command == "list-users":
        list_users(args)
    elif args.command == "delete-user":
        delete_user(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
