#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "Usage: $0 <email> <password> <name> [role]"
  echo "  role defaults to 'member' (options: member, admin)"
  exit 1
fi

EMAIL="$1"
PASSWORD="$2"
NAME="$3"
ROLE="${4:-member}"

echo "from auth import hash_password
from models import SessionLocal, User
db = SessionLocal()
if db.query(User).filter(User.email == '$EMAIL').first():
    print('ERROR: $EMAIL already exists')
    exit(1)
user = User(email='$EMAIL', name='$NAME', password_hash=hash_password('$PASSWORD'), role='$ROLE')
db.add(user)
db.commit()
print(f'Created: {user.name} ({user.email}) role={user.role}')
db.close()" | ssh dokku enter au-supply web python3 -
