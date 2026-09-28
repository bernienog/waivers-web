import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { api, fmtClears, fmtTime, type Claim, type Digest, type DigestRow } from '../api';
import type { League, PendingTx } from '../types';
import BidModal from './BidModal';
import { Avatar, PosChip } from './PlayerBits';
import PendingDND, { type NamedTx } from './PendingDND';
import WatchlistDND from './WatchlistDND';
import FpWirePanel from './FpWirePanel';
import LeagueClaimModal, { type CreatedClaim } from './LeagueClaimModal';

function txNames(t: PendingTx): { adds: string[]; drops: string[] } {
  const pm = (t as { player_map?: Record<string, { first_name?: string; last_name?: string }> }).player_map ?? {};
  const nm = (pid: string) => pidName(pm, pid);
  return {
    adds: Object.keys(t.adds ?? {}).map(nm),
    drops: Object.keys(t.drops ?? {}).map(nm),
  };
}

function pidName(
  pm: Record<string, { first_name?: string; last_name?: string }>,
  pid: string,
): string {
  const p = pm[pid];
  return p ? `${p.first_name ?? ''} ${p.last_name ?? ''}`.trim() : pid;
}

function txMap(t: PendingTx): Record<string, { first_name?: string; last_name?: string }> {
  return (t as { player_map?: Record<string, { first_name?: string; last_name?: string }> }).player_map ?? {};
}

/** Menor waiver_clears_at entre los adds (fecha exacta Sleeper, ms). */
function txClears(t: PendingTx, cl: Record<string, number>): number | null {
  let best: number | null = null;
  for (const pid of Object.keys(t.adds ?? {})) {
    const ms = cl[pid];
    if (ms && (best === null || ms < best)) best = ms;
  }
  return best;
}

/** Fila de resultado (anoche + historial): misma pieza en ambos. */
function ResultRow({ r }: { r: DigestRow }) {
  return (
    <div className="row">
      <PosChip pos={r.pos} />
      <Avatar pid={r.player_id} name={r.name} />
      <span className="grow">
        {r.resolution === 'won' ? (
          <span className="badge ok" title="ganado en Sleeper">W</span>
        ) : r.resolution === 'lost' ? (
          <span className="badge bad" title="perdido en Sleeper">L</span>
        ) : r.status === 'submitted' ? (
          <span className="badge claim" title="enviado · pendiente en Sleeper">…</span>
        ) : r.status === 'ready' ? (
          <span className="badge claim" title="listo · sin enviar">○</span>
        ) : (
          <span className="badge warn" title="fallido">F</span>
        )}{' '}
        <span className="add">+{r.name}</span>{' '}
        {r.drop_player_id ? (
          <small className="drop">−{r.drop_name || r.drop_player_id}</small>
        ) : (
          <small className="muted">sin drop</small>
        )}{' '}
                        {r.league_bid != null ? <small className="muted">${r.league_bid}</small> : null}{' '}
        {r.resolution === 'lost' && r.winner ? (
          <small className="muted" title={r.note ?? undefined}>→ {r.winner}</small>
        ) : null}
      </span>
      {(r.resolved_at ?? r.sent_at) ? (
        <small className="muted">
          {new Date((r.resolved_at ?? r.sent_at)!).toLocaleString(undefined, {
            day: '2-digit', month: '2-digit',
            hour: '2-digit', minute: '2-digit',
          })}
        </small>
      ) : null}
      {r.status === 'failed' && (r.note || r.error_text) ? (
        <small className="muted">{(r.note || r.error_text || '').slice(0, 80)}</small>
      ) : null}
    </div>
  );
}

export default function Hub({ leagues, tick, leagueTick, active, onOpenLeague, onLeagueRefresh }: {
  leagues: League[];
  /** Tick global: se mueve con el ↻ de todo. */
  tick: number;
  /** Tick por liga: se mueve solo para la liga refrescada. */
  leagueTick: Record<string, number>;
  /** Con el Hub escondido (pestaña Mis Ligas) no se re-tira nada. */
  active: boolean;
  onOpenLeague: (leagueId: string) => void;
  onLeagueRefresh: (leagueId: string, opts?: { verify?: boolean }) => Promise<void>;
}) {
  const [pend, setPend] = useState<Record<string, PendingTx[]>>({});
  const [clears, setClears] = useState<Record<string, Record<string, number>>>({});
  /** Ligas cuyo clear map se está sirviendo de cache: id -> antigüedad (s). */
  const [staleClears, setStaleClears] = useState<Record<string, number>>({});
  const [ready, setReady] = useState<Record<string, Claim[]>>({});
  const [confirmSend, setConfirmSend] = useState<number | null>(null);
  const [loaded, setLoaded] = useState<Record<string, boolean>>({});
  const [refreshing, setRefreshing] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState('');
  /** Tids en vuelo/muertos-recientes (`lid:tid`): el botón drop se
   * deshabilita hasta reconciliar, así un dato rancio jamás ofrece
   * cancelar dos veces lo que Sleeper ya dropeó. Ref = guarda síncrona
   * anti-dobletap; state = deshabilitar el botón. */
  const droppingRef = useRef<Set<string>>(new Set());
  const [dropping, setDropping] = useState<Record<string, boolean>>({});
  function markDropping(leagueId: string, tid: string, on: boolean) {
    const key = leagueId + ':' + tid;
    if (on) droppingRef.current.add(key);
    else droppingRef.current.delete(key);
    setDropping((m) => {
      const n = { ...m };
      if (on) n[key] = true;
      else delete n[key];
      return n;
    });
  }
  const [editing, setEditing] = useState<{ league: League; tx: PendingTx } | null>(null);
  const [adding, setAdding] = useState<League | null>(null);
  const [claimDraft, setClaimDraft] = useState<{ league: League; pid: string; name: string; multi?: boolean } | null>(null);
  const [toast, setToast] = useState('');
  /** Reconciliación morning-after: una vez por montaje, solo si hay
   * submitted locales (el freshness es local: cero llamadas si no hay
   * nada que resolver). Sin esto, un slate procesado amanecía fantasma. */
  const reconciled = useRef(false);
  const [reconciling, setReconciling] = useState(false);
  const [digest, setDigest] = useState<Digest | null>(null);

  /** Resueltos de la liga, más recientes primero. */
  function resolvedFor(lid: string) {
    return (digest?.rows ?? [])
      .filter((r) => r.league_id === lid &&
        (r.status === 'resolved' || r.status === 'failed'))
      .sort((a, b) => (b.resolved_at ?? 0) - (a.resolved_at ?? 0));
  }

  function flash(t: string) {
    setToast(t);
    setTimeout(() => setToast(''), 3500);
  }

  /** Las 3 slices de UNA liga en paralelo (una ola, no cascada:
   * antes pending→clears→ready eran 3 awaits secuenciales). */
  async function fetchSlices(lid: string, light: boolean, fresh = false) {
    const [p, c, r] = await Promise.all([
      api.pending(lid, fresh).catch(() => null),
      api.clears(lid, !light).catch(() => null),
      api.claimsReady(lid).catch(() => null),
    ]);
    if (p !== null) setPend((m) => ({ ...m, [lid]: p }));
    if (c !== null) {
      setClears((m) => ({ ...m, [lid]: c.clears }));
      if (c.stale) setStaleClears((m) => ({ ...m, [lid]: c.age_s ?? 0 }));
      else setStaleClears((m) => { const n = { ...m }; delete n[lid]; return n; });
    }
    if (r !== null) setReady((m) => ({ ...m, [lid]: r }));
  }

  /** Refresh de las slices de las ligas indicadas (3 requests por liga, en
   *  paralelo entre ligas). */
  async function loadPending(lids: string[]) {
    await Promise.all(lids.map(async (id) => {
      await fetchSlices(id, true); // light: clears cacheados
      setLoaded((m) => ({ ...m, [id]: true }));
    }));
  }

  function loadDigest() {
    return api.digest().then(setDigest).catch(() => { /* conserva anterior */ });
  }

  useEffect(() => {
    if (reconciled.current) return;
    reconciled.current = true;
    void (async () => {
      try {
        const s = await api.claimsByStatus('submitted');
        if (s.length) {
          setReconciling(true);
          await api.verify().catch(() => null);
        }
      } catch { /* offline: se muestra rancio, el ↻ reconcilia */ }
      finally { setReconciling(false); }
      await loadDigest();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Ola de TODAS las ligas: solo cuando cambia el tick global (el ↻ de
  // todo) o la lista de ligas. Antes también salía con cada refresh de una
  // liga, y eso costaba 3 requests por liga sin motivo.
  useEffect(() => {
    if (!active) return;
    void loadPending(leagues.map((l) => l.league_id));
    void loadDigest();
    // Sin reset de loaded: refrescar conserva las filas visibles (sin flicker);
    // las ligas nuevas muestran skeletons hasta su primera carga.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagues, tick, active]);

  // Refresh de UNA liga. Dos caminos, y hay que separarlos:
  //  - refreshOne/refreshMany (dentro del Hub) YA traen sus slices.
  //  - App.refreshLeague desde el dashboard solo mueve leagueTick, y ahí
  //    sí hay que reaccionar.
  // Antes el efecto refundía `Object.keys(leagueTick)`: cada refresh de
  // una liga volvía a pedir TODAS las tocadas alguna vez, y además la liga
  // que el Hub acababa de traer se pedía dos veces.
  const leagueTickRef = useRef(leagueTick);
  leagueTickRef.current = leagueTick;
  const vistoLeagueTick = useRef<Record<string, number>>({});
  const enCurso = useRef<Set<string>>(new Set());
  const refreshTick = JSON.stringify(leagueTick);

  useEffect(() => {
    // Oculto: no se avanza el registro, así al volver se pone al día de
    // lo que se refrescó mientras el Hub estaba escondido (y solo una vez).
    if (!active) return;
    const changed = Object.keys(leagueTick).filter(
      (id) => (leagueTick[id] ?? 0) > (vistoLeagueTick.current[id] ?? 0)
        && !enCurso.current.has(id),
    );
    for (const id of Object.keys(leagueTick)) {
      if (!enCurso.current.has(id)) vistoLeagueTick.current[id] = leagueTick[id] ?? 0;
    }
    if (changed.length) void loadPending(changed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTick, active]);

  /** Refresh de UNA liga. Lecturas independientes en paralelo (una ola,
   * no cascada). light=true (tras crear/enviar): sin verify (nada nuevo
   * submitted) y clears cacheados (el badge no amerita el dump completo). */
  async function refreshOne(lid: string, light = false, fresh = false) {
    enCurso.current.add(lid);
    setRefreshing((m) => ({ ...m, [lid]: true }));
    try {
      await onLeagueRefresh(lid, light ? { verify: false } : undefined);
      await fetchSlices(lid, light, fresh);
    } finally {
      enCurso.current.delete(lid);
      // El efecto no debe pescar esta liga como "pendiente".
      vistoLeagueTick.current[lid] = leagueTickRef.current[lid] ?? 0;
      setRefreshing((m) => ({ ...m, [lid]: false }));
    }
  }

  /** Post-batch (multibid): N ligas, UNA ola. Antes: N×refreshOne +
   * el tick de App disparaba otro loadPending completo encima. */
  async function refreshMany(lids: string[], light = true) {
    const ids = [...new Set(lids)];
    if (!ids.length) return;
    ids.forEach((id) => enCurso.current.add(id));
    setRefreshing((m) => {
      const n = { ...m };
      ids.forEach((id) => { n[id] = true; });
      return n;
    });
    try {
      await Promise.all(ids.map((id) =>
        onLeagueRefresh(id, light ? { verify: false } : undefined).catch(() => undefined)));
      await Promise.all(ids.map((id) => fetchSlices(id, light)));
    } finally {
      ids.forEach((id) => {
        enCurso.current.delete(id);
        vistoLeagueTick.current[id] = leagueTickRef.current[id] ?? 0;
      });
      setRefreshing((m) => {
        const n = { ...m };
        ids.forEach((id) => { n[id] = false; });
        return n;
      });
    }
  }

  /** Inserta el claim recién creado al instante (datos que ya tenemos);
   * el refresh de fondo reconcilia por id. Solo ready: lo submitted ya
   * viaja a pending y el refresh lo trae. */
  function prependReady(lid: string, c: CreatedClaim) {
    if (c.status !== 'ready') return;
    setReady((m) => ({
      ...m,
      [lid]: [...(m[lid] ?? []), {
        id: c.id, player_id: c.player_id, league_id: lid, week: c.week,
        league_bid: c.bid, drop_player_id: c.drop_player_id, status: 'ready',
        name: c.name, drop_name: c.drop_name,
      }],
    }));
  }
  /** Writer por tap explícito: envía UN claim listo (con confirmación
   * previa en UI). NO probar en vivo sin aprobación. */
  async function doSend(id: number, lid: string) {
    setConfirmSend(null);
    setMsg('');
    try {
      const r = await api.sendOne(id);
      setMsg(r.ok ? 'enviado a Sleeper' : 'Sleeper no lo aceptó');
      // La matriz lo marcaba pendiente al instante: avisar una vez para
      // que converja sin refresh manual.
      if (r.ok) api.notifyClaimsChanged(lid);
    } catch (e: unknown) {
      setMsg(String(e));
    }
    await refreshOne(lid, true);
  }

  /** Parche optimista del bid (BidModal): la fila muestra el nuevo $ al
   * instante; el refresh de fondo reconcilia. */
  function patchPendBid(lid: string, tid: string, bid: number) {
    setPend((m) => ({
      ...m,
      [lid]: (m[lid] ?? []).map((t) => (t.transaction_id === tid
        ? { ...t, settings: { ...((t.settings as Record<string, number> | undefined) ?? {}), waiver_bid: bid } }
        : t)),
    }));
  }

  async function dropClaim(leagueId: string, tid: string, leg = 1) {
    // Writer: cancela pending. Solo con aprobación.
    // Doble-guardia: si ya está en vuelo (o el refresh aún no confirma
    // la muerte), el segundo tap se ignora — jamás doble-cancel.
    const key = leagueId + ':' + tid;
    if (droppingRef.current.has(key)) return;
    markDropping(leagueId, tid, true);
    // Optimista: la fila desaparece al instante; el fondo reconcilia.
    setMsg('');
    setPend((m) => ({
      ...m,
      [leagueId]: (m[leagueId] ?? []).filter((t) => t.transaction_id !== tid),
    }));
    try {
      const r = await api.cancelPending(leagueId, tid, leg);
      setMsg(r.ok ? 'claim cancelado' : 'cancel FALLÓ');
      // El backend marca la fila local failed: reventar matriz para que
      // el waiver pendiente desaparezca sin refresh manual.
      if (r.ok) api.notifyClaimsChanged(leagueId);
    } catch (e: unknown) {
      setMsg(String(e));
    }
    await refreshOne(leagueId, true);
    // Recién ahora se rehabilita el botón: si la fila sigue viva (el
    // cancel falló de verdad), se puede reintentar; si murió, ya no está.
    markDropping(leagueId, tid, false);
  }

  /** Filas pendientes con nombres + bid, memoizadas por liga. Antes se
   * reconstruían en cada render (nuevo array) y el resync de PendingDND
   * borraba un drag en curso ante cualquier rerender del Hub. */
  const namedByLeague = useMemo(() => {
    const m: Record<string, { named: NamedTx[]; earliest: number | null }> = {};
    for (const l of leagues) {
      const txs = pend[l.league_id] ?? [];
      const cl = clears[l.league_id] ?? {};
      // Orden SIEMPRE el de Sleeper (ground truth): en FAAB el proceso
      // va por bid, pero la lista debe espejar a Sleeper — reordenar por
      // bid rompía el espejo (caso Penix/Wilson). El drag manda encima.
      const named: NamedTx[] = txs.map((t) => {
        const n = txNames(t);
        return {
          ...t,
          addsNames: n.adds,
          dropsNames: n.drops,
          bid: (t.settings as Record<string, number> | null)?.waiver_bid ?? null,
        };
      });
      m[l.league_id] = {
        named,
        earliest: txs.reduce<number | null>((b, t) => {
          const c = txClears(t, cl);
          return c !== null && (b === null || c < b) ? c : b;
        }, null),
      };
    }
    return m;
  }, [leagues, pend, clears]);

  return (
    <>
      <div>
        <div className="hub-top">
          <div className="cap-scroll"><FpWirePanel
            leagues={leagues}
            onClaim={(pid, name, league) => setClaimDraft({ league, pid, name })}
            onMulti={(pid, name) => setClaimDraft({ league: leagues[0], pid, name, multi: true })}
          /></div>
          <div className="cap-scroll"><WatchlistDND
            leagues={leagues}
            onClaim={(pid, name, league) => setClaimDraft({ league, pid, name })}
            onMulti={(pid, name) => setClaimDraft({ league: leagues[0], pid, name, multi: true })}
          /></div>
        </div>

        <div className="panel">
          <h3>Waivers</h3>
          {reconciling && <div><small className="muted">conciliando con Sleeper…</small></div>}
          {(leagues.some((l) => !loaded[l.league_id]) || Object.values(refreshing).some(Boolean)) && (
            <div className="loadbar-inline"><div /></div>
          )}
          {msg && <div><small className="muted">{msg}</small></div>}
        {leagues.map((l) => {
          const txs = pend[l.league_id] ?? [];
          const cl = clears[l.league_id] ?? {};
          const { named, earliest } = namedByLeague[l.league_id]
            ?? { named: [], earliest: null };
          const readies = l.mode === 'faab'
            ? [...(ready[l.league_id] ?? [])].sort((a, b) => (b.league_bid ?? 0) - (a.league_bid ?? 0))
            : (ready[l.league_id] ?? []);
          const done = resolvedFor(l.league_id);
          const actionable = txs.length > 0 || readies.length > 0;
          return (
            <Fragment key={l.league_id}>
            <details open={!!loaded[l.league_id] && actionable}>
              <summary className="lsum">
                <button
                  className="linklike"
                  title="Abrir en dashboard"
                  onClick={(e) => { e.preventDefault(); onOpenLeague(l.league_id); }}>
                  <strong>{l.name}</strong>
                </button>{' '}
                <span className="badge">{txs.length} waiting</span>{' '}
                {done.length > 0 && (
                  <small className="muted"> · {done.length} anoche</small>
                )}{' '}
                {l.mode === 'claim' && l.waiver_priority != null && (
                  <span className="badge claim">prio #{l.waiver_priority}</span>
                )}{' '}
                {earliest !== null && (
                  <small className="muted">clears {fmtClears(earliest)}</small>
                )}{' '}
                {/* Clears servidos de cache: la fecha puede estar vieja y
                    alguien podría decidir un claim con ella. */}
                {staleClears[l.league_id] != null && (
                  <span className="badge warn"
                    title={`Sleeper no respondió: esta fecha puede estar vieja (cache de ${Math.round(staleClears[l.league_id] / 60)} min)`}>
                    clears: caché
                  </span>
                )}{' '}
                {l.fetched_at ? (
                  <small className="muted">última actualización {fmtTime(l.fetched_at)}</small>
                ) : null}
                <span className="spacer" />
                <button
                  className="addbtn"
                  title="Nuevo claim en esta liga"
                  onClick={(e) => { e.preventDefault(); setAdding(l); }}>
                  +
                </button>
                <button
                  className="iconbtn"
                  title="Refresh esta liga (live, sin cache)"
                  disabled={!!refreshing[l.league_id]}
                  onClick={(e) => { e.preventDefault(); void refreshOne(l.league_id, false, true); }}>
                  {refreshing[l.league_id] ? '…' : '↻'}
                </button>
                <span className="chev" aria-hidden="true" />
              </summary>
              {!loaded[l.league_id] ? (
                <>
                  {[0, 1].map((i) => (
                    <div key={i} className="row" style={{ marginLeft: 16 }}>
                      <div className="skel circle" style={{ width: 30, height: 30 }} />
                      <span className="stack" style={{ flex: 1 }}>
                        <div className="skel" style={{ height: 14, width: '60%', marginBottom: 6 }} />
                        <div className="skel" style={{ height: 12, width: '40%' }} />
                      </span>
                      <div className="skel" style={{ width: 52, height: 20 }} />
                      <div className="skel" style={{ width: 70, height: 30 }} />
                    </div>
                  ))}
                </>
              ) : l.mode === 'claim' && txs.length > 1 ? (
                <>
                  <small className="muted">arrastra para reordenar priority</small>
                  <PendingDND
                    leagueId={l.league_id}
                    leg={l.resolve?.leg ?? 1}
                    txs={named}
                    dropping={dropping}
                    onEditBid={(t) => setEditing({ league: l, tx: t })}
                    onDrop={(t) => void dropClaim(l.league_id, t.transaction_id, t.leg ?? 1)}
                    onMsg={setMsg}
                  />
                </>
              ) : (
                named.map((t) => {
                  const c = txClears(t, cl);
                  const pm = txMap(t);
                  return (
                    <div key={t.transaction_id} className="row" style={{ marginLeft: 16 }}
                      title={`tid ${t.transaction_id}`}>
                      <span className="stack">
                        <span className="inline">
                          {Object.keys(t.adds ?? {}).map((pid) => (
                            <span key={`a-${pid}`} className="inline">
                              <Avatar pid={pid} name={pidName(pm, pid)} />
                              <span className="add">+{pidName(pm, pid)}</span>
                            </span>
                          ))}
                        </span>
                        <span className="inline sub">
                          {Object.keys(t.drops ?? {}).map((pid) => (
                            <span key={`d-${pid}`} className="inline">
                              <Avatar pid={pid} name={pidName(pm, pid)} />
                              <span>−{pidName(pm, pid)}</span>
                            </span>
                          ))}
                        </span>
                      </span>
                      {c !== null && <small className="muted">{fmtClears(c)}</small>}
                      {l.mode === 'faab' ? (
                        <button className="badge faab" style={{ cursor: 'pointer', border: 0 }}
                          title="editar bid"
                          onClick={() => setEditing({ league: l, tx: t })}>
                          ${t.bid ?? 0} faab ✎
                        </button>
                      ) : t.bid === undefined || t.bid === null ? (
                        <span className="badge claim">claim</span>
                      ) : (
                        <button className="badge faab" style={{ cursor: 'pointer', border: 0 }}
                          title="editar bid"
                          onClick={() => setEditing({ league: l, tx: t })}>
                          ${t.bid} faab ✎
                        </button>
                      )}
                      <button className="danger"
                        disabled={!!dropping[l.league_id + ':' + t.transaction_id]}
                        onClick={() => void dropClaim(l.league_id, t.transaction_id, t.leg ?? 1)}>
                        {dropping[l.league_id + ':' + t.transaction_id] ? 'dropping…' : 'drop waiver'}
                      </button>
                    </div>
                  );
                })
              )}
              {txs.length === 0 && !readies.length && !done.length && (
                <div className="emptystate"><small className="muted">Sin nada.</small></div>
              )}
              {readies.map((c) => (
              <div key={c.id} className="row" style={{ marginLeft: 16 }}>
                <Avatar pid={c.player_id} name={c.name ?? c.player_id} />
                <span className="grow">
                  <span className="add">+{c.name ?? c.player_id}</span>{' '}
                  {c.drop_player_id
                    ? <small className="drop">−{c.drop_name || c.drop_player_id}</small>
                    : <small className="muted">sin drop</small>}{' '}
                  {l.mode === 'faab' ? <small className="muted">${c.league_bid ?? 0}</small>
                    : (c.league_bid ? <small className="muted">${c.league_bid}</small> : null)}
                </span>
                {confirmSend === c.id ? (
                  <>
                    <button className="danger" onClick={() => void doSend(c.id, l.league_id)}>
                      confirmar envío
                    </button>
                    <button onClick={() => setConfirmSend(null)}>x</button>
                  </>
                ) : (
                  <button className="primary" onClick={() => setConfirmSend(c.id)}>
                    enviar
                  </button>
                )}
              </div>
            ))}
              {done.length > 0 && (
                <div><small className="muted">— anoche —</small></div>
              )}
              {done.length > 0 && (
                <div className="anoche-scroll">
                  {done.map((r) => <ResultRow key={r.id} r={r} />)}
                </div>
              )}
            </details>
            </Fragment>
          );
            })}
        </div>
      </div>
      {adding && (
        <LeagueClaimModal
          league={adding}
          leagues={leagues}
          onClose={() => setAdding(null)}
          onSent={(created) => { const l = adding; if (created) prependReady(l.league_id, created); api.notifyClaimsChanged(l.league_id, created?.player_id); setAdding(null); flash(created?.status === 'submitted' ? 'claim enviado ✓' : 'claim creado ✓'); void refreshOne(l.league_id, true); }}
          onMultiSent={(ids) => { flash('multibid enviado ✓'); void refreshMany(ids, true); }}
        />
      )}
      {claimDraft && (
        <LeagueClaimModal
          league={claimDraft.league}
          leagues={leagues}
          preselectedAdd={claimDraft.pid}
          preselectedName={claimDraft.name}
          startMulti={claimDraft.multi}
          onClose={() => setClaimDraft(null)}
          onSent={(created) => { const d = claimDraft; if (created) prependReady(d.league.league_id, created); api.notifyClaimsChanged(d.league.league_id, created?.player_id ?? d.pid); setClaimDraft(null); flash(created?.status === 'submitted' ? 'claim enviado ✓' : 'claim creado ✓'); void refreshOne(d.league.league_id, true); }}
          onMultiSent={(ids) => { flash('multibid enviado ✓'); void refreshMany(ids, true); }}
        />
      )}
      {toast && <div className="toast">{toast}</div>}
      {editing && (() => {
        const n = txNames(editing.tx);
        const lg = editing.league;
        const cur = (editing.tx.settings as Record<string, number> | null)?.waiver_bid ?? 0;
        return (
          <BidModal
            league={editing.league}
            names={`+${n.adds.join(', ')} / −${n.drops.join(', ')}`}
            tid={editing.tx.transaction_id}
            leg={editing.tx.leg ?? 1}
            current={cur}
            onClose={() => setEditing(null)}
            onSent={(tid, bid) => { patchPendBid(lg.league_id, tid, bid); setEditing(null); void refreshOne(lg.league_id, true); }}
          />
        );
      })()}
    </>
  );
}
