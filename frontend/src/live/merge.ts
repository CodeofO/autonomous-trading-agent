import type { AuditEvent, ChatMsg } from "../api/types";

/*
 * 실시간 목록 합치기 — 이미 정렬된 목록에 끼워 넣는다 (메시지마다 전체를 다시 정렬하지 않는다).
 * 바뀐 것이 없으면 원래 배열을 그대로 돌려줘서 화면이 다시 그려지지 않는다.
 */

/** 채팅은 오래된 것부터(id 오름차순), 최근 800개까지 */
export const MAX_MESSAGES = 800;
/** 감사 로그는 최신 것부터(id 내림차순), 최근 300개까지 */
export const MAX_AUDIT = 300;

/** id 순서를 지키며 하나를 넣는다. 같은 id 가 있으면 새 것으로 바꾼다 */
function insertById<T extends { id: number }>(list: T[], item: T, asc: boolean, max: number): T[] {
  const before = (a: number, b: number) => (asc ? a < b : a > b);
  const n = list.length;
  // 대부분은 끝(오름차순: 가장 새것, 내림차순: 앞)에 붙는다
  if (asc ? n === 0 || before(list[n - 1].id, item.id) : n === 0 || before(item.id, list[0].id)) {
    const out = asc ? [...list, item] : [item, ...list];
    return out.length > max ? (asc ? out.slice(out.length - max) : out.slice(0, max)) : out;
  }
  // 이진 탐색으로 자리를 찾는다
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (before(list[mid].id, item.id)) lo = mid + 1;
    else hi = mid;
  }
  if (lo < n && list[lo].id === item.id) {
    if (list[lo] === item) return list;
    const out = list.slice();
    out[lo] = item;
    return out;
  }
  const out = list.slice();
  out.splice(lo, 0, item);
  return out.length > max ? (asc ? out.slice(out.length - max) : out.slice(0, max)) : out;
}

/**
 * 정렬된 두 목록을 한 번 훑어 합친다 (같은 id 는 keep 쪽을 남긴다).
 * keep 은 이미 화면에 있는 목록 — 그 원소들은 같은 객체를 그대로 써서 메모된 줄이 다시 그려지지 않는다
 */
function mergeById<T extends { id: number }>(keep: T[], incoming: T[], asc: boolean, max: number): T[] {
  if (incoming.length === 0) return keep;
  const sorted = isSorted(incoming, asc)
    ? incoming
    : [...incoming].sort((a, b) => (asc ? a.id - b.id : b.id - a.id));
  const out: T[] = [];
  let i = 0;
  let j = 0;
  let added = false;
  while (i < keep.length || j < sorted.length) {
    const a = keep[i];
    const b = sorted[j];
    if (b === undefined || (a !== undefined && (asc ? a.id < b.id : a.id > b.id))) {
      out.push(a);
      i++;
    } else if (a === undefined || (asc ? b.id < a.id : b.id > a.id)) {
      if (out.length === 0 || out[out.length - 1].id !== b.id) {
        out.push(b);
        added = true;
      }
      j++;
    } else {
      out.push(a); // 같은 id — 이미 있는 것을 남긴다
      i++;
      j++;
    }
  }
  if (!added) return keep;
  return out.length > max ? (asc ? out.slice(out.length - max) : out.slice(0, max)) : out;
}

function isSorted<T extends { id: number }>(list: T[], asc: boolean): boolean {
  for (let k = 1; k < list.length; k++) {
    if (asc ? list[k - 1].id > list[k].id : list[k - 1].id < list[k].id) return false;
  }
  return true;
}

export const insertMessage = (list: ChatMsg[], msg: ChatMsg) => insertById(list, msg, true, MAX_MESSAGES);
export const mergeMessages = (keep: ChatMsg[], fetched: ChatMsg[]) =>
  mergeById(keep, fetched, true, MAX_MESSAGES);

export const insertAudit = (list: AuditEvent[], ev: AuditEvent) => insertById(list, ev, false, MAX_AUDIT);
export const mergeAudit = (keep: AuditEvent[], fetched: AuditEvent[]) =>
  mergeById(keep, fetched, false, MAX_AUDIT);
