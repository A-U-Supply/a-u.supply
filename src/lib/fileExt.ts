/**
 * Lowercase file extension from a filename ("logicx" from "song.logicx"),
 * or "" when there isn't a usable one.
 */
export function fileExt(name?: string | null): string {
  const m = /\.([a-z0-9]{1,10})$/i.exec(name || '');
  return m ? m[1].toLowerCase() : '';
}
