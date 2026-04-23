/**
 * Workspace helpers for admin client-side scripts.
 *
 * Every "Add to Workspace" button across the admin pages needs the same
 * behavior: find or create an active workspace for the current user, POST
 * the item(s), and recover gracefully if the cached workspace ID has gone
 * stale (deleted from another tab or the workspace page). Keeping the
 * logic in one module prevents the subtle divergences that were in
 * search/index, search/detail, and bookmarks before.
 */

const LS_KEY = 'active_workspace_id';

export async function ensureWorkspace(): Promise<string> {
  const cached = localStorage.getItem(LS_KEY);
  if (cached) return cached;
  const res = await fetch('/api/workspaces', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'My Workspace' }),
  });
  if (!res.ok) throw new Error('workspace-create-failed');
  const data = await res.json();
  localStorage.setItem(LS_KEY, data.id);
  return data.id as string;
}

export interface AddToWorkspaceResult {
  added: number;
  already_present: number;
}

/**
 * POST items to the active workspace. If the cached workspace was deleted
 * on the server (404), clear the cache and retry once with a fresh one —
 * so users never get a "No active workspace" error from stale storage.
 * Returns null on unrecoverable failure; caller shows a toast.
 */
export async function addItemsToWorkspace(
  mediaItemIds: string[],
): Promise<AddToWorkspaceResult | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    let wsId: string;
    try {
      wsId = await ensureWorkspace();
    } catch {
      return null;
    }
    const res = await fetch(`/api/workspaces/${wsId}/items`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_item_ids: mediaItemIds }),
    });
    if (res.ok) return res.json();
    if (res.status === 404 && attempt === 0) {
      localStorage.removeItem(LS_KEY);
      continue;
    }
    return null;
  }
  return null;
}
