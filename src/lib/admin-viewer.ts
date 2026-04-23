/**
 * Admin-page helpers for opening the image viewer with the right
 * action callbacks wired up for each surface.
 *
 * Pages differ in two things: the shape of their row data, and which
 * actions apply (e.g. midden/slop can "index"; search can't).
 * This module keeps those differences to a minimum of boilerplate.
 */

import { addItemsToWorkspace } from './workspace';
import {
  openImageViewer,
  type ViewerActions,
  type ViewerItem,
} from './image-viewer';

export { openImageViewer, type ViewerItem } from './image-viewer';

function bmToggle(id: string): Promise<boolean> {
  const bm = (window as any).__bookmarks;
  if (!bm) return Promise.resolve(false);
  return bm.toggle('media_item', id).then((on: any) => !!on);
}

async function bmIsBookmarked(id: string, allIds: string[]): Promise<boolean> {
  const bm = (window as any).__bookmarks;
  if (!bm) return false;
  const set = await bm.check('media_item', allIds);
  return set.has(id);
}

async function addToWorkspaceWithToast(id: string): Promise<void> {
  const data = await addItemsToWorkspace([id]);
  const toast = (window as any).toast;
  if (data) {
    toast?.success(
      data.already_present > 0 ? 'Already in workspace' : 'Added to workspace',
    );
  } else {
    toast?.error('Failed to add to workspace');
  }
}

/**
 * Build a viewer item for a MediaItem-backed row (search hits, workspace
 * items, bookmarks). Accepts a subset of fields off whatever row shape
 * the page has.
 */
export function mediaItemToViewerItem(row: {
  id: string;
  filename?: string;
  media_type?: string;
  dominant_colors?: string[];
  image_meta?: { width?: number; height?: number };
  width?: number;
  height?: number;
}): ViewerItem {
  const kind =
    row.media_type === 'video'
      ? 'video'
      : row.media_type === 'audio'
        ? 'audio'
        : 'image';
  return {
    id: row.id,
    kind: 'media_item',
    media_kind: kind,
    large_url: `/api/media/${row.id}/thumbnail?size=lg`,
    thumbnail_url: `/api/media/${row.id}/thumbnail?size=sm`,
    download_url: `/api/media/${row.id}/file`,
    stream_url: `/api/media/${row.id}/file`,
    filename: row.filename,
    width: row.image_meta?.width ?? row.width,
    height: row.image_meta?.height ?? row.height,
    dominant_colors: row.dominant_colors,
    media_type: row.media_type,
  };
}

/**
 * Default action set for MediaItem-backed pages: bookmark, workspace,
 * details. Caller can override / extend as needed.
 */
export function mediaItemDefaultActions(allIds: string[]): ViewerActions {
  return {
    onBookmark: (item) => bmToggle(item.id),
    isBookmarked: (item) => bmIsBookmarked(item.id, allIds),
    onAddToWorkspace: (item) => addToWorkspaceWithToast(item.id),
    onDetails: (item) => {
      window.location.href = `/admin/search/detail?id=${item.id}`;
    },
  };
}

/**
 * Build viewer items for JobOutput-backed rows (midden, slop,
 * jobs/detail). JobOutputs have a job_id and a `/download` URL; video
 * and audio stream from the same URL.
 */
export function jobOutputToViewerItem(row: {
  id: string;
  job_id: string;
  filename?: string;
  media_type?: string;
  indexed?: boolean;
  media_item_id?: string;
}): ViewerItem {
  const rawType = (row.media_type || '').toLowerCase();
  const kind =
    rawType === 'video' || rawType.startsWith('video/')
      ? 'video'
      : rawType === 'audio' || rawType.startsWith('audio/')
        ? 'audio'
        : 'image';
  // Indexed outputs get a MediaItem — its /thumbnail endpoint has the
  // lg variant. Unindexed ones fall back to the job-output thumbnail
  // endpoint which generates on first hit.
  const base =
    row.indexed && row.media_item_id
      ? `/api/media/${row.media_item_id}`
      : `/api/jobs/${row.job_id}/outputs/${row.id}`;
  const downloadUrl =
    row.indexed && row.media_item_id
      ? `/api/media/${row.media_item_id}/file`
      : `/api/jobs/${row.job_id}/outputs/${row.id}/download`;
  return {
    id: row.id,
    kind: 'job_output',
    media_kind: kind,
    large_url: `${base}/thumbnail?size=lg`,
    thumbnail_url: `${base}/thumbnail?size=sm`,
    download_url: downloadUrl,
    stream_url: downloadUrl,
    job_id: row.job_id,
    indexed: !!row.indexed,
    filename: row.filename,
    media_type: row.media_type,
  };
}
