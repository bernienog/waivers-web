import { useEffect, useRef, useState } from 'react';
import { api, fmtClears } from '../api';
import { Avatar, PosChip } from './PlayerBits';
import type { League } from '../types';

interface FA {
  player_id: string;
  name: string;
  pos?: string;
  team?: string;
  clears_at?: number | null;
  inj?: string | null;
  proj?: number | null;
  opp?: string | null;
}

interface DropOpt {
  player_id: string;
  name: string;
}

interface MbRow {
  league: League;
  taken: boolean | null; // null = verificando
  bid: number;
  drops: DropOpt[];
  drop: string;
  noDrop: boolean;
  free: number;
  status: 'idle' | 'ok' | 'error' | 'sending' | 'sent' | 'send-error';
  msg: string;
  claimId: number | null;
}

const MB_PCTS = [1, 5, 10, 25, 50];

/** Claim recién creado (quick ok): Hub lo pinta optimista al instante,
 * el refresh de fondo reconcilia por id. */
export interface CreatedClaim {
  id: number;
  player_id: string;
  name: string;
  bid: number;
  drop_player_id: string;
  drop_name: string;
  week: number;
  /** submit inmediato: el flujo manual envía al confirmar (el limbo
   * ready/enviar queda para el multipick). */
  status: 'ready' | 'submitted';
}

/** Claim multistep por liga: 1 add (FA) -> 2 drop + bid -> 3 confirmar.
 * Sin drop habilitado con slot libre real (roster_space.free > 0,
 * revalidado en backend con free_slots).
 * Multibid: % del saldo FAAB por liga -> matriz liga x drop -> quick
 * por liga (quedan ready para el batch con jitter).
 */
export default function LeagueClaimModal({ league, leagues, preselectedAdd, preselectedName, startMulti, onClose, onSent, onMultiSent }: {
  league: League;
  leagues?: League[];
  preselectedAdd?: string;
  preselectedName?: string;
  /** Entrar directo en multibid (desde el league-picker). */
  startMulti?: boolean;
  onClose: () => void;
  onSent: (created?: CreatedClaim) => void;
  onMultiSent?: (ids: string[]) => void;
}) {
  const [step, setStep] = useState(preselectedAdd ? 2 : 1);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<FA[]>([]);
  const [add, setAdd] = useState(preselectedAdd ?? '');
  const [addName, setAddName] = useState(preselectedName ?? '');
  const [drops, setDrops] = useState<DropOpt[]>([]);
  const [lockedCount, setLockedCount] = useState(0);
  const [drop, setDrop] = useState('');
  const [noDrop, setNoDrop] = useState(false);
  const [bid, setBid] = useState(league.bid_min ?? 0);
  const [msg, setMsg] = useState('');
  const [avail, setAvail] = useState<'checking' | 'free' | 'taken' | 'unknown'>('unknown');
  const [clearsMap, setClearsMap] = useState<Record<string, number> | null>(null);
  /** El map vino de cache (Sleeper no respondió): la fecha puede estar vieja. */
  const [clearsStale, setClearsStale] = useState(false);
  const isFaab = league.mode === 'faab';
  // Sin drop solo con slot libre real (backend lo revalida con free_slots).
  const canNoDrop = (league.roster_space?.free ?? 0) > 0;

  // Lock informativo: waiver_clears_at se vuelve rancio (rollover no lo
  // reescribe: caso Raheim). Solo se muestra fecha futura; NUNCA bloquea.
  // Único gate duro: rostered (backend). Sleeper valida al enviar.
  const addClears = add ? clearsMap?.[add] : undefined;
  const isLocked = addClears != null && addClears > Date.now();

  /** Ritmo de waivers de la liga, leído de sus settings (diario vs
   * semanal + días de clear). Sin fecha inventada: solo el texto. */
  const cadence = (() => {
    const r = league.resolve;
    if (!r) return '';
    if (r.daily_waivers) {
      return r.waiver_clear_days
        ? 'esta liga corre waivers todos los días: tu claim resuelve al día siguiente.'
        : 'esta liga corre waivers todos los días: tu claim resuelve el mismo día.';
    }
    return 'esta liga corre waivers una vez por semana: el claim espera al cierre.';
  })();

  useEffect(() => {
    api.roster(league.league_id)
      .then((r) => {
        // Taxi/IR no son dropeables via claim (Sleeper los rechaza):
        // fuera del picker antes de que muerdan.
        const locked = new Set([...(r.taxi ?? []), ...(r.reserve ?? [])]);
        setDrops(r.players
          .filter((p) => !locked.has(p.player_id))
          .map((p) => ({ player_id: p.player_id, name: p.name })));
        setLockedCount(r.players.filter((p) => locked.has(p.player_id)).length);
      })
      .catch(() => { setDrops([]); setLockedCount(0); });
    api.clears(league.league_id, false, true)
      .then((r) => { setClearsMap(r.clears); setClearsStale(r.stale); })
      .catch(() => { setClearsMap({}); setClearsStale(false); });
  }, [league.league_id]);

  /** Disponibilidad del add en ESTA liga: Boston en Dynasty = taken.
   * checking/taken bloquean el confirm; unknown (fallo del check) no
   * bloquea — el backend revalida rostered al enviar. */
  useEffect(() => {
    if (!add) { setAvail('unknown'); return; }
    setAvail('checking');
    api.availability(league.league_id, add)
      .then((r) => setAvail(r.taken ? 'taken' : 'free'))
      .catch(() => setAvail('unknown'));
  }, [league.league_id, add]);

  const availBlocked = !!add && (avail === 'checking' || avail === 'taken');

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  // Búsqueda con debounce (antes: un fetch por tecla) + guarda de
  // carrera (una respuesta lenta no pisa a una más nueva).
  const searchTimer = useRef<number | null>(null);
  const searchSeq = useRef(0);
  useEffect(() => () => {
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
  }, []);

  async function search(v: string) {
    setQ(v);
    if (v.trim().length < 3) { setHits([]); return; }
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
    const seq = ++searchSeq.current;
    searchTimer.current = window.setTimeout(() => {
      void (async () => {
        try {
          const r = await fetch(
            `/api/free-agents/${league.league_id}?q=${encodeURIComponent(v)}&limit=10&sort=rank`,
          ).then((x) => x.json());
          if (searchSeq.current === seq) setHits(Array.isArray(r) ? r : []);
        } catch {
          if (searchSeq.current === seq) setHits([]);
        }
      })();
    }, 250);
  }

  function pick(h: FA) {
    setAdd(h.player_id);
    setAddName(h.name);
    setQ('');
    setHits([]);
    setNoDrop(false);
    setStep(2);
  }

  const dropName = drops.find((d) => d.player_id === drop)?.name ?? drop;
  const [sending, setSending] = useState(false);
  // ---- multibid ----
  const [multi, setMulti] = useState(startMulti ?? false);
  const [mbPct, setMbPct] = useState<number | null>(10);
  const [mbCustom, setMbCustom] = useState('');
  const [mbRows, setMbRows] = useState<Record<string, MbRow> | null>(null);
  const [mbBusy, setMbBusy] = useState(false);
  const [mbDone, setMbDone] = useState(false);

  async function send() {
    if (sending) return;
    setSending(true);
    setMsg('');
    try {
      const r = await api.quick({
        league_id: league.league_id, player_id: add,
        drop_player_id: drop, bid, week: 2,
      });
      const made = {
        id: r.id, player_id: add, name: addName || add, bid,
        drop_player_id: drop, drop_name: dropName, week: 2,
      };
      // Flujo manual: enviar al confirmar, sin limbo. Si el send falla,
      // el claim queda ready y se reintenta con enviar (Hub).
      const s = await api.sendOne(r.id);
      if (!s.ok) {
        setMsg('creado pero envío FALLÓ (reintenta con enviar)');
        onSent({ ...made, status: 'ready' });
        return;
      }
      onSent({ ...made, status: 'submitted' });
    } catch (e: unknown) {
      setMsg(String(e));
      setSending(false);
    }
  }

  // ---------- multibid ----------

  /** % efectivo: preset o custom. */
  function mbPctEff(): number {
    if (mbCustom.trim() !== '') {
      const v = Number(mbCustom);
      return Number.isFinite(v) && v > 0 ? v : 0;
    }
    return mbPct ?? 0;
  }

  function mbBidFor(l: League, pct: number): number {
    if (l.mode !== 'faab') return 0;
    const left = l.budget_left ?? 0;
    return Math.max(l.bid_min ?? 0, Math.floor(left * pct / 100));
  }

  /** Arma la matriz: availability por liga + rosters en paralelo. */
  async function startMatrix() {
    const ls = (leagues ?? []).filter((l) => l.league_id);
    if (!ls.length || !add) return;
    const pct = mbPctEff();
    setMbBusy(true);
    setMbDone(false);
    const rows: Record<string, MbRow> = {};
    for (const l of ls) {
      rows[l.league_id] = {
        league: l, taken: null, bid: mbBidFor(l, pct),
        drops: [], drop: '', noDrop: (l.roster_space?.free ?? 0) > 0,
        free: l.roster_space?.free ?? 0, status: 'idle', msg: '',
        claimId: null,
      };
    }
    setMbRows(rows);
    try {
      const [mx, ...rosters] = await Promise.all([
        api.availabilityMatrix(ls.map((l) => l.league_id), [add]),
        ...ls.map((l) => api.roster(l.league_id).catch(() => null)),
      ]);
      setMbRows((prev) => {
        if (!prev) return prev;
        const next = { ...prev };
        ls.forEach((l, i) => {
          const r = rosters[i];
          const locked = new Set([...(r?.taxi ?? []), ...(r?.reserve ?? [])]);
          const drops = (r?.players ?? [])
            .filter((p) => !locked.has(p.player_id))
            .map((p) => ({ player_id: p.player_id, name: p.name }));
          next[l.league_id] = {
            ...next[l.league_id],
            taken: (mx.taken[l.league_id] ?? []).includes(add),
            drops,
          };
        });
        return next;
      });
    } catch {
      // Sin matriz: no bloquear (el backend revalida al crear).
      setMbRows((prev) => {
        if (!prev) return prev;
        const next = { ...prev };
        for (const k of Object.keys(next)) next[k] = { ...next[k], taken: false };
        return next;
      });
    } finally {
      setMbBusy(false);
    }
  }

  function setMbRow(lid: string, patch: Partial<MbRow>) {
    setMbRows((prev) => (prev ? { ...prev, [lid]: { ...prev[lid], ...patch } } : prev));
  }

  /** Multibid de un tiro: quick por liga + send-many con jitter.
   * Sin limbo ready en UI: las filas van a sent ✓ o error con reintento.
   * Secuencial local (orden predecible, fallos legibles), luego un batch
   * server-side. Todo-sent → autocierra (el toast lo pone el Hub). */
  async function sendMultibid() {
    if (!mbRows || mbBusy) return;
    setMbBusy(true);
    const lids = Object.keys(mbRows);
    const scope = lids.filter((lid) => mbRows[lid].taken === false);
    // Filas ya enviadas antes de este tap + las que enviemos ahora.
    const sentNow = new Set(
      scope.filter((lid) => mbRows[lid].status === 'sent'));
    const created: { lid: string; id: number }[] = [];
    for (const lid of lids) {
      const r = mbRows[lid];
      // Solo ligas verificadas libres: null = aún chequeando, jamás disparar.
      if (r.taken !== false || r.status === 'sent') continue;
      if (!r.drop && !r.noDrop) {
        setMbRow(lid, { status: 'error', msg: 'elige drop o sin drop' });
        continue;
      }
      try {
        const c = await api.quick({
          league_id: lid, player_id: add,
          drop_player_id: r.noDrop ? '' : r.drop, bid: r.bid, week: 2,
        });
        setMbRow(lid, { status: 'ok', msg: 'creado, enviando…', claimId: c.id });
        created.push({ lid, id: c.id });
      } catch (e: unknown) {
        setMbRow(lid, { status: 'error', msg: String(e) });
      }
    }
    if (created.length) {
      try {
        const res = await api.sendMany(created.map((c) => c.id));
        const byId = new Map(res.results.map((x) => [x.id, x]));
        setMbRows((prev) => {
          if (!prev) return prev;
          const next = { ...prev };
          for (const lid of Object.keys(next)) {
            const row = next[lid];
            const hit = row.claimId != null ? byId.get(row.claimId) : undefined;
            if (!hit) continue;
            next[lid] = hit.ok
              ? { ...row, status: 'sent', msg: `enviado tid=${hit.transaction_id}` }
              : { ...row, status: 'send-error', msg: hit.error ?? 'envío FALLÓ' };
          }
          return next;
        });
        const sentLids = created
          .filter((c) => byId.get(c.id)?.ok)
          .map((c) => c.lid);
        sentLids.forEach((lid) => sentNow.add(lid));
        // Un solo aviso: el panel refetchea la matriz completa igual.
        if (sentLids.length) api.notifyClaimsChanged(sentLids[0], add);
        if (sentLids.length) onMultiSent?.(sentLids);
      } catch (e: unknown) {
        setMsg(String(e));
        setMbRows((prev) => {
          if (!prev) return prev;
          const next = { ...prev };
          for (const lid of Object.keys(next)) {
            if (next[lid].status === 'ok') next[lid] = { ...next[lid], status: 'send-error', msg: 'batch FALLÓ (reintenta individual)' };
          }
          return next;
        });
      }
    }
    setMbBusy(false);
    // Todo-sent (incluye filas ya enviadas antes del tap) → autocierra.
    // Cualquier fallo → queda abierto con reintento por fila.
    const allSent = scope.length > 0 && scope.every((lid) => sentNow.has(lid));
    if (allSent) {
      onClose();
    } else {
      setMbDone(true);
    }
  }

  /** Envía UN claim creado (botón individual). */
  async function sendRow(lid: string) {
    const r = mbRows?.[lid];
    if (!r || r.claimId == null || mbBusy) return;
    setMbRow(lid, { status: 'sending', msg: 'enviando…' });
    try {
      const s = await api.sendOne(r.claimId);
      if (s.ok) {
        setMbRow(lid, { status: 'sent', msg: `enviado tid=${s.transaction_id}` });
        api.notifyClaimsChanged(lid, add);
        onMultiSent?.([lid]);
      } else {
        setMbRow(lid, { status: 'send-error', msg: 'envío FALLÓ (queda ready)' });
      }
    } catch (e: unknown) {
      setMbRow(lid, { status: 'send-error', msg: String(e) });
    }
  }

  const mbCount = mbRows
    ? Object.values(mbRows).filter((r) => r.taken === false && (r.drop || r.noDrop)).length
    : 0;
  const mbFree = mbRows
    ? Object.values(mbRows).filter((r) => r.taken === false).length
    : 0;
  const mbChecked = !!mbRows && Object.values(mbRows).every((r) => r.taken !== null);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} title="cerrar (esc)">×</button>
        <h3>{multi ? `Multibid — +${addName || add}` : `Nuevo claim — ${league.name}`}</h3>
        {add && (leagues ?? []).length > 1 && !multi && (
          <div className="row">
            <button className="multibtn grow" onClick={() => { setMulti(true); setMbRows(null); setMbDone(false); }}>
              ⚡ multibid en {leagues!.length} ligas
            </button>
          </div>
        )}
        {multi ? (
          <>
            <small className="muted">
              multibid · +{addName || add} · {mbPctEff()}% del saldo
            </small>
            {!mbRows ? (
              <>
                <div><small className="muted">% del saldo FAAB por liga:</small></div>
                <div className="tabs">
                  {MB_PCTS.map((p) => (
                    <button key={p} className={mbPct === p && mbCustom.trim() === '' ? 'active' : ''}
                      onClick={() => { setMbPct(p); setMbCustom(''); }}>{p}%</button>
                  ))}
                  <input className="bidinput" type="number" placeholder="custom" value={mbCustom}
                    onChange={(e) => setMbCustom(e.target.value)}
                    onBlur={() => setMbCustom((v) => {
                      // % entero 0-100 sobre el SALDO actual (no el max):
                      // floor(pct × saldo) nunca supera lo que queda.
                      const n = Math.floor(Number(v));
                      if (v.trim() === '' || !Number.isFinite(n)) return '';
                      return String(Math.min(100, Math.max(0, n)));
                    })} style={{ width: 76, fontSize: 12, color: 'var(--muted)' }} />
                </div>
                <div className="row">
                  <button className="primary grow" disabled={!mbPctEff() || mbBusy} onClick={() => void startMatrix()}>
                    continuar
                  </button>
                  <button onClick={() => setMulti(false)}>← simple</button>
                </div>
              </>
            ) : (
              <>
                {Object.values(mbRows).filter((r) => r.taken !== true).map((r) => (
                  <div key={r.league.league_id} className="row">
                    <span className="grow">
                      <strong>{r.league.name}</strong>{' '}
                      <small className="muted">{r.league.mode}</small>
                      <br />
                      {r.taken === null ? (
                        <div className="skel" style={{ height: 14, width: 120 }} />
                      ) : r.league.mode === 'faab' ? (
                        <small className="muted">
                          $<input className="bidinput" type="number" value={r.bid} style={{ width: 64 }}
                            onChange={(e) => {
                              const n = Math.floor(Number(e.target.value));
                              setMbRow(r.league.league_id, {
                                bid: Number.isFinite(n) ? Math.max(0, n) : 0,
                                status: 'idle', msg: '',
                              });
                            }} />
                          {' '}de ${r.league.budget_left ?? '?'}
                        </small>
                      ) : (
                        <small className="muted">claim sin bid</small>
                      )}
                      {r.status === 'ok' && <small className="muted"> · {r.msg}</small>}
                      {r.status === 'error' && <span className="badge bad">{r.msg}</span>}
                      {r.status === 'sending' && (
                        <small className="muted"> · <span className="spinner" />enviando…</small>
                      )}
                      {r.status === 'sent' && <small className="muted"> · {r.msg}</small>}
                      {r.status === 'send-error' && <span className="badge bad">{r.msg}</span>}
                    </span>
                    {r.status === 'send-error' && r.claimId != null && (
                      <button className="primary" disabled={mbBusy} title="reintentar envío"
                        onClick={() => void sendRow(r.league.league_id)}>
                        reintentar
                      </button>
                    )}
                    {r.taken === false && (r.status === 'idle' || r.status === 'error') && (
                      <select value={r.noDrop ? '' : r.drop}
                        onChange={(e) => {
                          const v = e.target.value;
                          setMbRow(r.league.league_id, v === '__free__'
                            ? { drop: '', noDrop: true, status: 'idle', msg: '' }
                            : { drop: v, noDrop: false, status: 'idle', msg: '' });
                        }}>
                        <option value="">drop…</option>
                        {r.free > 0 && <option value="__free__">sin drop (slot libre)</option>}
                        {r.drops.map((d) => <option key={d.player_id} value={d.player_id}>{d.name}</option>)}
                      </select>
                    )}
                    {['idle', 'error', 'send-error'].includes(r.status) && (
                      <button className="danger" title="quitar liga"
                        onClick={() => setMbRows((prev) => {
                          if (!prev) return prev;
                          const next = { ...prev };
                          delete next[r.league.league_id];
                          return next;
                        })}>
                        x
                      </button>
                    )}
                  </div>
                ))}
                <div className="row">
                  {!mbDone ? (
                    <button className="primary grow" disabled={!mbChecked || mbFree < 2 || !mbCount || mbBusy} onClick={() => void sendMultibid()}>
                      {mbBusy ? <><span className="spinner" />enviando…</> : `enviar ${mbCount} claim${mbCount === 1 ? '' : 's'}`}
                    </button>
                  ) : (
                    <button className="primary grow" disabled={mbBusy} onClick={onClose}>
                      cerrar
                    </button>
                  )}
                  {!mbDone && <button onClick={() => setMbRows(null)}>← %</button>}
                </div>
                {mbChecked && mbFree < 2 && !mbDone && (
                  <div><small className="muted">
                    solo {mbFree} liga{mbFree === 1 ? '' : 's'} libre{mbFree === 1 ? '' : 's'} — multibid necesita 2+.
                  </small></div>
                )}
              </>
            )}
          </>
        ) : (
        <>
        <small className="muted">
          paso {step}/3 · {league.mode} · saldo {league.budget_left ?? 'n/a'} · min {league.bid_min}
        </small>
        {add && avail === 'checking' && (
          <div className="skel" style={{ height: 22, marginTop: 8 }} />
        )}
        {add && avail === 'taken' && (
          <div><span className="badge bad">ya tiene dueño en esta liga</span></div>
        )}

        {step === 1 && (
          <>
            <div className="row">
              <input className="grow" placeholder="Buscar add (FA, min 3 letras)…" value={addName || q}
                onChange={(e) => { setAdd(''); setAddName(''); void search(e.target.value); }} />
            </div>
            {hits.map((h) => (
              <div key={h.player_id} className="row">
                <PosChip pos={h.pos} />
                <Avatar pid={h.player_id} name={h.name} />
                <span className="grow">{h.name}{' '}
                  <small className="muted">{h.pos} {h.team}{h.opp ? ` vs ${h.opp}` : ''}</small></span>
                {h.proj != null ? <span className="badge faab">{h.proj.toFixed(1)}</span> : null}
                {h.inj ? <span className="badge bad">{h.inj}</span> : null}
                {h.clears_at ? <small className="muted">W {fmtClears(h.clears_at)}</small> : null}
                <button className="primary" onClick={() => pick(h)}>elegir</button>
              </div>
            ))}
          </>
        )}

        {step === 2 && (
          <>
            <div className="row"><Avatar pid={add} name={addName || add} /><span className="grow add">+{addName || add}</span>
              <button onClick={() => setStep(1)}>cambiar</button></div>
            {(league.roster_space?.free ?? 0) > 0 ? (
              <div><small className="muted">
                tienes {league.roster_space!.free} slot(s) libre(s) — el drop es opcional.
              </small></div>
            ) : null}
            {!league.roster_space ? (
              <div><span className="badge claim">slots desconocidos</span>{' '}
                <small className="muted">reinicia el backend para ver slots libres.</small></div>
            ) : null}
            {isLocked && addClears ? (
              <div>
                <small className="muted">W — clears {fmtClears(addClears)}</small>
                {clearsStale && <> <span className="badge warn"
                  title="Sleeper no respondió: esta fecha puede estar vieja">caché</span></>}
              </div>
            ) : null}
            {cadence ? (
              <div><small className="muted">{cadence}</small></div>
            ) : null}
            <div className="row">
              {drop ? <Avatar pid={drop} name={dropName} /> : null}
              <select className="grow" value={drop} onChange={(e) => { setDrop(e.target.value); setNoDrop(false); }}>
                <option value="">drop…</option>
                {drops.map((d) => <option key={d.player_id} value={d.player_id}>{d.name}</option>)}
              </select>
              {isFaab && <input className="bidinput" type="number" value={bid}
                onChange={(e) => setBid(Number(e.target.value))}
                onBlur={(e) => {
                  const v = Math.floor(Number(e.target.value));
                  setBid(Number.isFinite(v) ? Math.max(league.bid_min ?? 0, v) : (league.bid_min ?? 0));
                }} />}
              {isFaab && <small className="muted">mín ${league.bid_min ?? 0}</small>}
            </div>
            {lockedCount > 0 ? (
              <div><small className="muted">
                {lockedCount} en taxi/IR ocultos (Sleeper rechaza dropearlos).
              </small></div>
            ) : null}
            {canNoDrop && !noDrop ? (
              <div className="row">
                <button className="grow" onClick={() => { setNoDrop(true); setDrop(''); }}>
                  sin drop ({league.roster_space!.free} slot(s) libre(s))
                </button>
              </div>
            ) : null}
            {noDrop ? (
              <div><small className="muted">sin drop — entra directo al slot libre.</small></div>
            ) : null}
            <div className="row">
              <button className="primary grow" disabled={!drop && !noDrop} onClick={() => {
                setBid((b) => (isFaab ? Math.max(league.bid_min ?? 0, Math.floor(Number(b) || 0)) : b));
                setStep(3);
              }}>
                continuar
              </button>
            </div>
            {(!drop && !noDrop) ? (
              <div><small className="muted">
                {league.roster_space
                  ? ((league.roster_space.free ?? 0) > 0
                    ? 'elige un drop o usa sin drop.'
                    : 'sin slots libres: elige un drop.')
                  : 'elige un drop (slots desconocidos: reinicia el backend).'}
              </small></div>
            ) : null}
          </>
        )}

        {step === 3 && (
          <>
            <div className="row">
              <span className="grow">
                <Avatar pid={add} name={addName || add} />
                <span className="add">+{addName || add}</span>{' '}
                {drop ? (
                  <>
                    <Avatar pid={drop} name={dropName} />
                    <small className="drop">−{dropName}</small>
                  </>
                ) : (
                  <small className="muted">sin drop</small>
                )}{' '}
                {isFaab && <small className="muted">${bid}</small>}
              </span>
            </div>
            {isLocked && addClears ? (
              <div><small className="muted">W — clears {fmtClears(addClears)}</small></div>
            ) : null}
            <div className="row">
              <button className="primary grow" disabled={!add || (!drop && !noDrop) || availBlocked || sending} onClick={() => void send()}>
                {sending ? <><span className="spinner" />enviando…</> : 'confirmar claim'}
              </button>
              <button className="iconbtn" title="atrás" onClick={() => setStep(2)}>←</button>
            </div>
          </>
        )}
        </>
        )}

        {msg && <small className="muted">{msg}</small>}
      </div>
    </div>
  );
}
