import { useEffect, useRef, useState } from 'react';
import { api, fmtTime, type FpRow } from '../api';
import { Avatar, PosChip } from './PlayerBits';
import type { League } from '../types';

const FANTASY_POS = ['QB', 'RB', 'WR', 'TE', 'K', 'DEF'];
const TABS = ['ALL', ...FANTASY_POS];

/** Matriz taken cacheada en módulo: StrictMode remonta en dev y el
 * segundo montaje reutiliza sin flashear skeletons ni refetchear. */
const MATRIX_TTL = 120_000;
let matrixCache: {
  key: string; at: number;
  data: { taken: Record<string, string[]>; pending: Record<string, string[]> };
} | null = null;

/** El botón de importar no se explica solo: nadie sabe qué es el "wire" ni
 *  de dónde sale. Este `?` lo dice en un párrafo, sin secciones. */
function PopoverHelp() {
  const [open, setOpen] = useState(false);
  return (
    <span className="helpwrap">
      <button className="helpon" onClick={() => setOpen((v) => !v)}
        aria-label="qué es esto" title="qué es esto">?</button>
      {open && (
        <span className="helpbox" onClick={(e) => e.stopPropagation()}>
          Busca y copia la Waiver Tierlist semanal disponible públicamente en
          FantasyPros. La dirección nunca cambia, así que siempre se trae la
          más reciente que haya.
        </span>
      )}
    </span>
  );
}

export default function FpWirePanel({ leagues, onClaim, onMulti }: {
  leagues: League[];
  onClaim: (pid: string, name: string, league: League) => void;
  onMulti: (pid: string, name: string) => void;
}) {
  const [rows, setRows] = useState<FpRow[]>([]);
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [week, setWeek] = useState(0);
  const [scoring, setScoring] = useState('');
  const [claimFor, setClaimFor] = useState<FpRow | null>(null);
  const [detail, setDetail] = useState<FpRow | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [matrix, setMatrix] = useState<Record<string, string[]> | null>(null);
  const [pendingMap, setPendingMap] = useState<Record<string, string[]> | null>(null);
  const loadedOnce = useRef(false);
  const [tab, setTab] = useState('ALL');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [wireLoading, setWireLoading] = useState(true);
  // Escape cierra el modal abierto (detalle o claim), no los dos.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (claimFor) setClaimFor(null); else setDetail(null);
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [claimFor]);

  async function reloadWire() {
    try {
      const w = await api.fpWire();
      setRows(w.rows);
      setWeek(w.week);
      setScoring(w.scoring ?? '');
      setFetchedAt(w.fetched_at);
    } catch (e: unknown) {
      setMsg(String(e));
    } finally {
      setWireLoading(false);
    }
  }

  async function reloadWatch() {
    try {
      const wl = await api.watchlist();
      setWatched(new Set(wl.map((x) => x.player_id)));
    } catch { /* conserva anterior */ }
  }

  async function reload() {
    await reloadWire();
    await reloadWatch();
  }

  useEffect(() => {
    void reload();
    // Cambió el watchlist, no el wire: no re-bajar el ECR.
    const h = () => void reloadWatch();
    window.addEventListener('watchlist:changed', h);
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setClaimFor(null); setDetail(null); }
    };
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('watchlist:changed', h);
      window.removeEventListener('keydown', esc);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Matriz taken: corre cuando hay filas + ligas (ligas llegan async).
   * Un call por liga, no por fila. Liga ausente = desconocida (el
   * modal/backend gatea igual). Cache de módulo contra el doble
   * montaje de StrictMode. Refreshes de fondo no flashean: solo la
   * primera carga muestra skeletons. */
  function applyMatrix(taken: Record<string, string[]>, pending: Record<string, string[]>) {
    setMatrix(taken);
    setPendingMap(pending);
    loadedOnce.current = true;
  }

  useEffect(() => {
    const pids = rows.map((r) => r.sleeper_id).filter((x): x is string => !!x);
    if (!leagues.length || !pids.length) return;
    const key = leagues.map((l) => l.league_id).join(',') + '#' + pids.join(',');
    if (matrixCache && matrixCache.key === key && Date.now() - matrixCache.at < MATRIX_TTL) {
      applyMatrix(matrixCache.data.taken, matrixCache.data.pending);
      return;
    }
    let alive = true;
    if (!loadedOnce.current) setMatrix(null);
    api.availabilityMatrix(leagues.map((l) => l.league_id), pids)
      .then((m) => {
        matrixCache = { key, at: Date.now(), data: { taken: m.taken, pending: m.pending ?? {} } };
        if (alive) applyMatrix(m.taken, m.pending ?? {});
      })
      .catch(() => { if (alive && !loadedOnce.current) { setMatrix({}); setPendingMap({}); } });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagues, rows]);

  /** Marca optimista + refetch de fondo al crear claims (Hub/App).
   * El servidor ya une claims abiertos, así que el refetch converge
   * sin revertir la marca. Debounce 500ms: un multibid avisaba N veces
   * (una por liga) y cada aviso era una matriz completa. */
  const ccTimer = useRef<number | null>(null);
  useEffect(() => {
    const refetch = () => {
      const pids = rows.map((r) => r.sleeper_id).filter((x): x is string => !!x);
      if (!leagues.length || !pids.length) return;
      const key = leagues.map((l) => l.league_id).join(',') + '#' + pids.join(',');
      api.availabilityMatrix(leagues.map((l) => l.league_id), pids)
        .then((m) => {
          matrixCache = { key, at: Date.now(), data: { taken: m.taken, pending: m.pending ?? {} } };
          applyMatrix(m.taken, m.pending ?? {});
        })
        .catch(() => { /* conserva marca optimista */ });
    };
    const h = (e: Event) => {
      const d = (e as CustomEvent<{ league_id: string; player_id: string }>).detail;
      if (!d) return;
      matrixCache = null; // el próximo refetch es live
      if (d.player_id) {
        const mark = (m: Record<string, string[]> | null) => {
          const next = { ...(m ?? {}) };
          const cur = next[d.league_id] ?? [];
          if (!cur.includes(d.player_id)) next[d.league_id] = [...cur, d.player_id];
          return next;
        };
        setMatrix((m) => mark(m));
        setPendingMap((m) => mark(m));
        loadedOnce.current = true;
      }
      if (ccTimer.current !== null) window.clearTimeout(ccTimer.current);
      ccTimer.current = window.setTimeout(refetch, 500);
    };
    window.addEventListener('fp:claims-changed', h);
    return () => {
      window.removeEventListener('fp:claims-changed', h);
      if (ccTimer.current !== null) window.clearTimeout(ccTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagues, rows]);

  /** Dueño real (taken menos pendientes propios) en TODAS las ligas
   * conocidas -> no hay + que clicear. */
  function ownedIn(lid: string, pid: string): boolean {
    const t = matrix?.[lid] ?? [];
    const p = pendingMap?.[lid] ?? [];
    return t.includes(pid) && !p.includes(pid);
  }

  function pendingIn(lid: string, pid: string): boolean {
    return (pendingMap?.[lid] ?? []).includes(pid);
  }

  function ownedEverywhere(pid: string): boolean {
    if (!leagues.length || matrix === null) return false;
    return leagues.every((l) => ownedIn(l.league_id, pid));
  }

  /** Claim abierto propio en cada liga libre conocida -> waiver pendiente
   * (tercer estado: informa, no esconde, bloquea el duplicado). */
  function pendingCover(pid: string): boolean {
    if (!leagues.length || matrix === null) return false;
    const open = leagues.filter((l) => matrix?.[l.league_id] !== undefined && !ownedIn(l.league_id, pid));
    return open.length > 0 && open.every((l) => pendingIn(l.league_id, pid));
  }

  function takenEverywhere(pid: string): boolean {
    return ownedEverywhere(pid);
  }

  async function doImport() {
    setBusy(true);
    setMsg('');
    try {
      const r = await api.fpImport();
      setMsg(`sem ${r.week}: ${r.imported} jugadores, ${r.matched} cruzados` +
        (r.unmatched.length ? ` — sin match: ${r.unmatched.join(', ')}` : ''));
      await Promise.all([reloadWire(), reloadWatch()]);
    } catch (e: unknown) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toWatch(r: FpRow) {
    if (!r.sleeper_id) return;
    await api.watchAdd({ player_id: r.sleeper_id, base_bid: 0, notes: 'fp #' + r.rank_ecr });
    window.dispatchEvent(new Event('watchlist:changed'));
  }

  /** Mismo filtro que el watchlist: IDP fuera + tab. */
  function visible(): FpRow[] {
    return rows.filter((x) => {
      const p = (x.pos ?? '').toUpperCase();
      if (p && !FANTASY_POS.includes(p)) return false;
      if (tab !== 'ALL' && p !== tab) return false;
      return true;
    });
  }

  const vis = visible();
  const matrixLoading = rows.length > 0 && matrix === null;

  return (
    <div className="panel">
      <h3>FantasyPros wire{scoring ? ` · ${scoring}` : ''}{week ? ` · sem ${week}` : ''}</h3>
      {/* Sin la caja de `.row`: un solo botón centrado no necesita un
          marco con borde y fondo alrededor. */}
      <div className="wire-cta">
        <button className="primary" disabled={busy} onClick={() => void doImport()}>
          {busy ? 'importando…' : 'import FantasyPros waivers'}
        </button>
        <PopoverHelp />
      </div>
      {fetchedAt ? (
        <div style={{ textAlign: 'center' }}>
          <small className="muted">última actualización {fmtTime(fetchedAt)}</small>
        </div>
      ) : null}
      {(busy || matrixLoading) && <div className="loadbar"><div /></div>}
      {msg && <div><small className="muted">{msg}</small></div>}
      <div className="tabs" style={{ marginTop: 8 }}>
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'active' : ''}
            onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="fprow head">
        <small className="muted c">#</small>
        <small className="muted">jugador</small>
        <small className="muted c">pos</small>
        <small className="muted c">bye</small>
        <small className="muted c">own</small>
        <small className="muted c">$</small>
        <small className="muted btn" title="watchlist">watchlist</small>
        <small className="muted btn" title="claim">+</small>
      </div>
      {vis.map((r) => (
        <div key={r.fp_id} className="fprow" onClick={() => setDetail(r)} style={{ cursor: 'pointer' }}>
          <small className="muted c">#{r.rank_ecr}</small>
          <span className="nm">
            <Avatar pid={r.sleeper_id ?? String(r.fp_id)} name={r.name} />
            <span>{r.name} <small className="muted">({r.team})</small></span>
          </span>
          <small className="muted c">{r.pos_rank || '—'}</small>
          <small className="muted c">{r.bye != null ? r.bye : '—'}</small>
          <small className="muted c">{r.owned_avg != null ? `${r.owned_avg}%` : '—'}</small>
          <small className="muted c">{r.tag || '—'}</small>
          <span className="btn">
            {!r.sleeper_id ? (
              <small className="muted" title="Sin match en Sleeper">—</small>
            ) : matrix === null ? (
              <div className="skel" style={{ width: 90, height: 16 }} />
            ) : takenEverywhere(r.sleeper_id) ? (
              <small className="muted" title="Con dueño en todas tus ligas">—</small>
            ) : (
              <button className="watchlink"
                title={watched.has(r.sleeper_id) ? 'En watchlist' : 'Al watchlist'}
                onClick={(e) => { e.stopPropagation(); void toWatch(r); }}>
                {watched.has(r.sleeper_id) ? 'en watchlist ✓' : 'agregar a watchlist'}
              </button>
            )}
          </span>
          <span className="btn">
            {!r.sleeper_id ? (
              <small className="muted" title="Sin match en Sleeper">—</small>
            ) : matrix === null ? (
              <div className="skel" style={{ width: 34, height: 34, borderRadius: 8 }} />
            ) : takenEverywhere(r.sleeper_id) ? (
              <small className="muted" title="Con dueño en todas tus ligas">No disponible</small>
            ) : pendingCover(r.sleeper_id) ? (
              <small className="muted" title="Ya tienes claim abierto en cada liga libre">waiver pendiente</small>
            ) : (
              <button className="addbtn" title="claim" onClick={(e) => { e.stopPropagation(); setClaimFor(r); }}>
                +
              </button>
            )}
          </span>
        </div>
      ))}
      {detail && (
        <div className="modal-backdrop" onClick={() => setDetail(null)}>
          <div className="modal fp-detail" onClick={(e) => e.stopPropagation()}>
            <button className="modal-x" onClick={() => setDetail(null)} title="cerrar (esc)">×</button>
            <div className="row" style={{ alignItems: 'flex-start' }}>
              <img
                className="avatar avatar-lg" alt={detail.name}
                src={`https://sleepercdn.com/content/nfl/players/${detail.sleeper_id ?? detail.fp_id}.jpg`}
                onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
              />
              <span className="grow">
                <strong>#{detail.rank_ecr} {detail.name}</strong>{' '}
                <small className="muted">({detail.team})</small>{' '}
                <PosChip pos={detail.pos} />
                <br />
                <small className="muted">
                  {[detail.pos_rank, detail.bye != null ? `bye ${detail.bye}` : '',
                    detail.owned_avg != null ? `${detail.owned_avg}% own` : '',
                    detail.opp, detail.tag].filter(Boolean).join(' · ')}
                </small>
              </span>
            </div>
            {detail.note.split(/\n\s*\n/).map((p, i) => (
              <p key={i}><small>{p.trim()}</small></p>
            ))}
          </div>
        </div>
      )}
      {claimFor?.sleeper_id && (
        <div className="modal-backdrop" onClick={() => setClaimFor(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-x" onClick={() => setClaimFor(null)} title="cerrar (esc)">×</button>
            <h3>
              <Avatar pid={claimFor.sleeper_id ?? String(claimFor.fp_id)} name={claimFor.name} />{' '}
              +{claimFor.name} <small className="muted">#{claimFor.rank_ecr}</small>{' '}
              <PosChip pos={claimFor.pos} />
            </h3>
            <div><small className="muted">elige liga:</small></div>
            {(() => {
              const pid = claimFor.sleeper_id as string;
              const freeCount = leagues.filter((l) =>
                matrix?.[l.league_id] !== undefined && !ownedIn(l.league_id, pid)).length;
              return (
                <>
                  {freeCount >= 2 && (
                    <div className="row">
                      <button className="multibtn grow" onClick={() => {
                        onMulti(pid, claimFor.name);
                        setClaimFor(null);
                      }}>
                        ⚡ multibid en {freeCount} ligas
                      </button>
                    </div>
                  )}
                </>
              );
            })()}
            {leagues.map((l) => {
              const pid = claimFor.sleeper_id as string;
              const isOwned = ownedIn(l.league_id, pid);
              const isPending = pendingIn(l.league_id, pid);
              return (
                <div key={l.league_id} className="row">
                  <span className="grow">{l.name} <small className="muted">{l.mode}</small></span>
                  {isOwned ? (
                    <small className="muted">No disponible</small>
                  ) : isPending ? (
                    <small className="muted">waiver pendiente</small>
                  ) : (
                    <button className="primary" onClick={() => {
                      onClaim(pid, claimFor.name, l);
                      setClaimFor(null);
                    }}>
                      elegir
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {wireLoading ? (
        <>
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="fprow">
              <div className="skel" style={{ height: 14, width: 20, justifySelf: 'center' }} />
              <span className="nm">
                <div className="skel circle" style={{ width: 30, height: 30 }} />
                <div className="skel" style={{ flex: 1, height: 16 }} />
              </span>
              <div className="skel" style={{ height: 14 }} />
              <div className="skel" style={{ height: 14 }} />
              <div className="skel" style={{ height: 14 }} />
              <div className="skel" style={{ height: 14 }} />
              <div className="skel" style={{ height: 16, width: 90, justifySelf: 'center' }} />
              <div className="skel" style={{ width: 34, height: 34, justifySelf: 'center' }} />
            </div>
          ))}
        </>
      ) : rows.length === 0
        ? <div className="empty">
            <span>Vacío — importa el wire con el botón.</span>
          </div>
        : vis.length === 0 && <div className="empty">Nada en este tab.</div>}
    </div>
  );
}
