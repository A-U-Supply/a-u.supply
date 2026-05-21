#!/usr/bin/env python3
"""CLI for managing users and catalog maintenance. No public signup — use this to create/invite users.

Usage:
    python cli.py create-user --email admin@example.com --name "Admin" --password secret --role admin
    python cli.py list-users
    python cli.py delete-user --email user@example.com
    python cli.py transcode-catalog  # convert existing WAV/FLAC tracks to MP3 for web streaming
"""
import argparse
import sys
from pathlib import Path

from server.auth import hash_password
from server.models import Base, SessionLocal, Track, User, engine

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


def transcode_catalog(args):
    """Transcode all WAV/FLAC/AIFF catalog tracks to MP3 192kbps for web streaming."""
    from server.catalog import LOSSLESS_EXTENSIONS, MEDIA_DIR, _transcode_to_mp3

    db = SessionLocal()
    try:
        tracks = db.query(Track).filter(Track.web_audio_file_path.is_(None), Track.audio_file_path.isnot(None)).all()
        lossless = [t for t in tracks if Path(t.audio_file_path).suffix.lower() in LOSSLESS_EXTENSIONS]
        if not lossless:
            print("No lossless tracks to transcode.")
            return

        print(f"Transcoding {len(lossless)} track(s) to MP3 192kbps...")
        ok = 0
        for track in lossless:
            src = MEDIA_DIR / track.audio_file_path
            dest = src.with_suffix(".mp3")
            print(f"  [{track.id}] {track.audio_file_path} → {dest.name}", end=" ", flush=True)
            if not src.exists():
                print("SKIP (source missing)")
                continue
            if _transcode_to_mp3(str(src), str(dest)):
                track.web_audio_file_path = str(dest.relative_to(MEDIA_DIR))
                db.commit()
                print("OK")
                ok += 1
            else:
                print("FAIL")

        print(f"Done: {ok}/{len(lossless)} transcoded.")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="a-u.supply management CLI")
    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser("create-user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--role", default="member", choices=["admin", "member"])

    sub.add_parser("list-users")

    delete = sub.add_parser("delete-user")
    delete.add_argument("--email", required=True)

    sub.add_parser("transcode-catalog")

    args = parser.parse_args()
    if args.command == "create-user":
        create_user(args)
    elif args.command == "list-users":
        list_users(args)
    elif args.command == "delete-user":
        delete_user(args)
    elif args.command == "transcode-catalog":
        transcode_catalog(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
