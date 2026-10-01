const BASE = '';

/** Una sola puerta de salida para los errores: la UI habla español y nunca
 *  muestra lo que dijo Sleeper, ni una URL, ni un `Exception:`. El backend
 *  manda `code`; lo que no reconocemos cae en una frase genérica con el
 *  código para que el usuario pueda citarlo al pedir ayuda.
 *
 *  Sin esto, `String(e)` imprimía el prefijo "Error: " en cada mensaje y el
 *  texto crudo del upstream aparecía de golpe en pantalla. */
const POR_CODIGO: Record<string, string> = {
  'sleeper-read': 'Sleeper no respondió. Revisa tu internet e inténtalo otra vez.',
  'sleeper-write': 'Sleeper no aceptó el movimiento. Espera un momento e inténtalo otra vez.',
  'proyecciones': 'No hay proyecciones para esta semana.',
  'token': 'Tu sesión de Sleeper no sirvió. Vuelve a conectarla.',
  'local': 'Algo falló en la app. Cierra y vuelve a abrir Waivers.',
  'fantasypros': 'No pude leer FantasyPros. Inténtalo más tarde.',
  'interno': 'Algo falló de nuestro lado. Cierra y vuelve a abrir la app.',
};

/** Red de seguridad: si algo se cuela sin código, se corta. */
function sinFiltrar(raw: string): string {
  const limpio = raw
    .replace(/^\s*(Error|TypeError|NetworkError|HttpError):\s*/i, '')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\bException\b.*$/is, '')
    .trim();
  return limpio.length > 0 && limpio.length <= 160
    ? limpio
    : 'Algo salió mal en la app. Inténtalo otra vez.';
}

export function textoDeError(e: unknown, code?: string | null): string {
  if (code && POR_CODIGO[code]) return POR_CODIGO[code];
  const raw = e instanceof Error ? e.message : String(e);
  // `Failed to fetch` / `NetworkError` = el motor local no respondió.
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return 'No pude hablar con la app. Revisa que siga abierta.';
  }
  return sinFiltrar(raw);
}

/** El código que el backend adjuntó al error (si lo mandó). */
export function codigoDe(e: unknown): string | null {
  return (e as { code?: string | null } | null)?.code ?? null;
}

/** Los comandos de Tauri no pasan por `req()`: sus rechazos son texto crudo
 *  de Rust (paths, nombres de comando, errores de ACL). Se sanean igual. */
/** ¿Estamos dentro de la app de escritorio (Tauri) o en un navegador
 *  normal? La UI se sirve en 127.0.0.1:3001, así que el mismo build corre
 *  de las dos formas. Los comandos de Tauri (abrir carpeta, abrir Chrome)
 *  solo existen en escritorio; en navegador hay que ofrecer lo que sí
 *  funciona en vez de un botón que solo puede fallar. */
export function esTauri(): boolean {
  return typeof window !== 'undefined'
    && '__TAURI_INTERNALS__' in window;
}

async function invokeSeguro<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    return await invoke<T>(cmd, args);
  } catch (e: unknown) {
    throw new Error(textoDeError(e));
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const detail = body?.detail ?? {};
    const code = detail?.code;
    throw Object.assign(
      new Error(textoDeError(detail?.error ?? body?.error ?? `HTTP ${r.status}`, code)),
      { code: code ?? null, dev: detail?.dev ?? null },
    );
  }
  return r.json() as Promise<T>;
}

export interface ReorderResult {
  transaction_id: string;
  priority: number;
  ok: boolean;
  error?: string | null;
  code?: string | null;
  dev?: string | null;
}

export interface Claim {
  id: number;
  player_id: string;
  league_id: string;
  week: number;
  league_bid: number;
  drop_player_id: string;
  status: string;
  transaction_id?: string | null;
  name?: string;
  drop_name?: string;
}

export const api = {
  health: () => req<{ ok: boolean; version?: string; frontend?: boolean }>('/health'),
  setup: () => req<SetupState>('/setup'),
  stageExtension: () => req<{ ok: boolean; path: string }>('/setup/stage-extension', { method: 'POST' }),
  /** `stale` = Sleeper no respondió y esto es el último snapshot local. */
  leagues: () => req<{ leagues: import('./types').League[]; stale: boolean }>('/leagues'),
  league: (id: string) => req<import('./types').League>('/leagues/' + id),
  freshness: () => req<Freshness>('/freshness'),
  verify: (league_id?: string) =>
    req<VerifyReport>('/verify', { method: 'POST', body: JSON.stringify({ league_id: league_id ?? null }) }),
  /** `stale` = Sleeper no respondió y esto es lo último que se leyó
   *  (`age_s`: qué tan viejo). */
  clears: (id: string, fresh = false, past = false) =>
    req<{ clears: Record<string, number>; stale: boolean; age_s: number | null }>(
      '/waiver-clears/' + id +
      (fresh || past ? `?fresh=${fresh ? 1 : 0}&past=${past ? 1 : 0}` : '')),
  roster: (id: string) => req<{ roster_id: number; players: { player_id: string; name: string; pos?: string; team?: string }[]; taxi: string[]; reserve: string[] }>('/leagues/' + id + '/roster'),
  /** Agentes libres con filtros. Antes lo hacía un fetch a mano en el panel
   *  y se saltaba el saneo de errores: todo pasa por `req()`.
   *
   *  `fresh` ignora el cache de 30s de la señal de resolución de waivers.
   *  La PRIMERA carga de Mis Ligas lo manda para que el W/+ salga bien desde
   *  el primer render; después va false y el cache evita un poke por fila. */
  freeAgents: (league_id: string, q: string, pos: string, limit: number, sort: string, fresh = false) =>
    req<import('./components/FreeAgentsPanel').FA[]>(
      `/free-agents/${league_id}?q=${encodeURIComponent(q)}&pos=${pos}` +
      `&limit=${limit}&sort=${sort}${fresh ? '&fresh=1' : ''}`),
  /** Add LIBRE de agente libre: al instante, sin pending que cancelar. */
  pickup: (league_id: string, player_id: string, drop_player_id = '') =>
    req<{ ok: boolean; id: number; transaction_id: string | null }>(
      '/free-agents/pickup',
      { method: 'POST', body: JSON.stringify({ league_id, player_id, drop_player_id }) }),
  availability: (league_id: string, player_id: string) =>
    req<{ taken: boolean }>('/availability/' + league_id + '/' + player_id),
  /** `mine` = está en TU roster (o claim tuyo abierto). `taken` sin estar en
   *  `mine` = lo tiene OTRO manager: no es agregable, pero no es tuyo.
   *  `failed` = ligas que Sleeper no devolvió: no se puede afirmar nada. */
  availabilityMatrix: (league_ids: string[], player_ids: string[]) =>
    req<{
      taken: Record<string, string[]>;
      mine?: Record<string, string[]>;
      pending: Record<string, string[]>;
      failed?: string[];
    }>(
      '/availability/matrix', { method: 'POST', body: JSON.stringify({ league_ids, player_ids }) }),
  /** Aviso local: claims mutaron ( Hub/App -> FpWirePanel ). */
  notifyClaimsChanged: (league_id: string, player_id?: string) => {
    window.dispatchEvent(new CustomEvent('fp:claims-changed', { detail: { league_id, player_id: player_id ?? '' } }));
  },
  pending: (id: string, fresh = false) => req<import('./types').PendingTx[]>('/pending/' + id + (fresh ? '?fresh=1' : '')),
  search: (q: string) => req('/search?q=' + encodeURIComponent(q)),
  watchlist: () => req<import('./types').WatchItem[]>('/watchlist'),
  watchAdd: (b: object) => req('/watchlist', { method: 'POST', body: JSON.stringify(b) }),
  watchReorder: (ids: string[]) =>
    req('/watchlist/reorder', { method: 'PATCH', body: JSON.stringify({ ordered_ids: ids }) }),
  /** Reordenar pendings. `error` es frase segura; `dev` es el cuerpo crudo
   *  de la mutación y no se pinta (queda para support). */
  pendingReorder: (league_id: string, ordered_ids: string[], leg = 1) =>
    req<{ results: ReorderResult[] }>('/pending/reorder', {
      method: 'POST',
      body: JSON.stringify({ league_id, ordered_ids, leg }),
    }),
  watchDel: (pid: string) => req('/watchlist/' + pid, { method: 'DELETE' }),
  claims: (week: number) => req<Claim[]>('/claims?week=' + week),
  claimsByStatus: (status: string) => req<Claim[]>('/claims?status=' + status),
  claimsReady: (league_id: string) =>
    req<Claim[]>('/claims?status=ready&league_id=' + league_id),
  quick: (b: { league_id: string; player_id: string; drop_player_id: string; bid: number; week: number }) =>
    req<{ ok: boolean; id: number }>('/claims/quick', { method: 'POST', body: JSON.stringify(b) }),
  sendOne: (id: number) => req<{ ok: boolean; transaction_id: string | null }>('/claims/' + id + '/send', { method: 'POST' }),
  sendMany: (ids: number[]) =>
    req<{ results: { id: number; ok: boolean; transaction_id: string | null; error: string | null }[] }>(
      '/claims/send-many', { method: 'POST', body: JSON.stringify({ ids }) }),
  cancelPending: (league_id: string, transaction_id: string, leg = 1) =>
    req<{ ok: boolean }>('/pending/cancel', { method: 'POST', body: JSON.stringify({ league_id, transaction_id, leg }) }),
  updateBid: (league_id: string, transaction_id: string, leg: number, bid: number) =>
    req<{ ok: boolean }>('/pending/update', { method: 'POST', body: JSON.stringify({ league_id, transaction_id, leg, bid }) }),
  fpWire: () => req<FpWire>('/fp-wire'),
  fpImport: () =>
    req<{ week: number; scoring: string; imported: number; matched: number; unmatched: string[] }>(
      '/fp-wire/import', { method: 'POST' }),
  digest: () => req<Digest>('/digest'),
  history: () => req<History>('/history'),
  authStatus: () => req<{ jwt: Freshness['jwt'] }>('/auth/status'),
  authSave: (b: { jwt: string; session: string; user_id?: string }) =>
    req<{ ok: boolean; jwt: Freshness['jwt'] }>('/auth/save', { method: 'POST', body: JSON.stringify(b) }),
};

export interface DigestRow {
  id: number;
  league_id: string;
  player_id: string;
  name: string;
  pos?: string | null;
  team?: string | null;
  league_bid: number;
  drop_player_id: string;
  drop_name: string;
  status: string;
  resolution?: string | null;
  transaction_id?: string | null;
  sent_at?: number | null;
  resolved_at?: number | null;
  checked_at?: number | null;
  error_text?: string | null;
  note?: string | null;
  winner?: string | null;
}

export interface Digest {
  week: number;
  latest_week: number;
  sleeper_weeks: number[];
  unverified: number;
  rows: DigestRow[];
}

export interface History {
  weeks: { week: number; rows: DigestRow[] }[];
}

export interface FpRow {
  fp_id: number;
  name: string;
  pos: string;
  team: string;
  sleeper_id: string | null;
  rank_ecr: number;
  rank_ave: number | null;
  pos_rank: string;
  bye: number | null;
  owned_avg: number | null;
  opp: string;
  tag: string;
  note: string;
  week: number;
  fetched_at: number;
}

export interface FpWire {
  week: number;
  scoring: string;
  fetched_at: number | null;
  count: number;
  rows: FpRow[];
}

/** Fecha exacta de Sleeper (ms) -> "Thu 9/17 2:12 AM". Verbatim, sin estimar. */
export function fmtClears(ms: number | null | undefined): string {
  if (!ms) return '';
  return new Date(ms).toLocaleString(undefined, {
    weekday: 'short', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

/** Día en 3 letras (DOM/LUN/...). Para la celda W: la fecha completa
 * vive en el tooltip. El clear de waivers siempre cae dentro de la
 * semana, así que el día basta. */
export function fmtDay3(ms: number | null | undefined): string {
  if (!ms) return '';
  const d = ['DOM', 'LUN', 'MAR', 'MIE', 'JUE', 'VIE', 'SAB'];
  return d[new Date(ms).getDay()] ?? '';
}

/** Abre una carpeta nuestra en el Explorador. Es un comando de Rust (no el
 *  opener) porque `file:///` no lo resuelve el sistema de forma fiable. */
export async function openFolder(path: string): Promise<void> {
  return invokeSeguro<void>('open_folder', { path });
}

/** Abre chrome://extensions (o edge://extensions) en el navegador REAL
 *  del usuario. Es un comando de Rust, no el opener: `chrome://` no es un
 *  protocolo del sistema. Devuelve con qué navegador salió. */
export async function openExtensions(): Promise<string> {
  return invokeSeguro<string>('open_extensions_page');
}

/** Solo-hora corta para "upd" por liga. */
export function fmtTime(ms: number | null | undefined): string {
  if (!ms) return '';
  return new Date(ms).toLocaleTimeString(undefined, {
    hour: 'numeric', minute: '2-digit',
  });
}

export interface Freshness {
  jwt: { status: string; expires_at: number | null; hours_left?: number };
  claims: { submitted: number; ready: number; oldest_unverified: number | null; stale: boolean };
  clears_cache: Record<string, number>;
  db_mtime: number | null;
}

export interface SetupState {
  first_run: boolean;
  version: string;
  ext_ready: boolean;
  staged: boolean;
  /** Carpeta con los archivos sueltos de la extensión (NO se abre en el
   *  Explorador: ahí no hay nada que elegir). */
  stage_path: string;
  /** Carpeta PADRE: la que se abre para que "Waivers Connect" se vea como
   *  una carpeta y sea lo que se elige en "Cargar descomprimida". */
  picker_dir: string;
  /** Nombre de esa carpeta, para el texto ("elige la carpeta X"). */
  stage_name: string;
  jwt: { status: string; expires_at: number | null; hours_left?: number };
  data_dir: string;
  db: string;
  db_exists: boolean;
  players_cached: boolean;
  port: number;
  frozen: boolean;
}

export interface VerifyReport {  checked: number;
  won: number;
  lost: number;
  vanished: number;
  skipped: { league_id: string; reason: string }[];
  details: { id: number; what: string }[];
}
