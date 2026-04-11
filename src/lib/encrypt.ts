import { createCipheriv, randomBytes, pbkdf2Sync } from 'node:crypto';

const SALT = Buffer.from('au-supply-personnel-2026', 'utf8');
const ITERATIONS = 100_000;
const KEY_LEN = 32;
const ALGORITHM = 'aes-256-gcm';

export function encrypt(plaintext: string, passphrase: string): string {
  const key = pbkdf2Sync(passphrase, SALT, ITERATIONS, KEY_LEN, 'sha256');
  const iv = randomBytes(12);
  const cipher = createCipheriv(ALGORITHM, key, iv);

  const encrypted = Buffer.concat([
    cipher.update(plaintext, 'utf8'),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();

  return Buffer.concat([iv, tag, encrypted]).toString('base64');
}
