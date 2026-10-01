import { useEffect, useState } from 'react';
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  verticalListSortingStrategy,
  useSortable,
  arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { api } from '../api';
import { useEsc } from '../useEsc';
import { Avatar, PosChip } from './PlayerBits';
import type { WatchItem } from '../types';
import type { League } from '../types';

const FANTASY_POS = ['QB', 'RB', 'WR', 'TE', 'K', 'DEF'];
const TABS = ['ALL', ...FANTASY_POS];

function Row({ item, onDel, onClaim }: {
  item: WatchItem;
  onDel: (pid: string) => void;
  onClaim: (item: WatchItem) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition } =
    useSortable({ id: item.player_id });
  return (
    <div ref={setNodeRef} className="row"
      style={{ transform: CSS.Transform.toString(transform), transition }}>
      <span {...attributes} {...listeners} style={{ cursor: 'grab' }}>⠿</span>
      <PosChip pos={item.pos} />
      <Avatar pid={item.player_id} name={item.name ?? item.player_id} />
      <span className="grow">{item.name ?? item.player_id}</span>
      <small className="muted">base ${item.base_bid}</small>
      <button className="addbtn" title="claim" onClick={() => onClaim(item)}>+</button>
      <button className="danger" onClick={() => onDel(item.player_id)}>x</button>
    </div>
  );
}

export default function WatchlistDND({ leagues, onClaim, onMulti }: {
  leagues: League[];
  onClaim: (pid: string, name: string, league: League) => void;
  onMulti: (pid: string, name: string, leagues: League[]) => void;
}) {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [claimFor, setClaimFor] = useState<WatchItem | null>(null);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<{ player_id: string; name: string; pos: string; team: string }[]>([]);
  const [err, setErr] = useState('');
  const sensors = useSensors(useSensor(PointerSensor));
  useEsc(() => setClaimFor(null));

  // Disponibilidad del jugador en TODAS las ligas, leída ANTES de elegir
  // liga. Antes había que cliquear una liga para enterarte de que el
  // jugador ya era tuyo ahí, y eso gastaba el click del modal entero.
  // Misma idea que el panel de FantasyPros: una matriz para todas las
  // ligas, no un call por liga.
  const [matrix, setMatrix] = useState<Record<string, string[]> | null>(null);
  const [pendingMap, setPendingMap] = useState<Record<string, string[]>>({});
  /** Ligas que el backend no pudo leer (falla de Sleeper). */
  const [failed, setFailed] = useState<string[]>([]);
  /** De qué jugador es la matriz que hay en pantalla. */
  const [matrixFor, setMatrixFor] = useState<string>('');

  // `checking` se DERIVA, no se setea en el effect. Antes era un flag que
  // el effect ponía en true: en el primer render del modal ya era false,
  // así que las 4 ligas se veían clicables y un segundo después se
  // corregían. Eso es la carrera que se veía.
  // Ahora es "la matriz que tengo no es de este jugador" -> desconocido.
  const checking = !!claimFor && matrixFor !== claimFor.player_id;

  useEffect(() => {
    const pid = claimFor?.player_id;
    if (!pid || !leagues.length) { setMatrix(null); setMatrixFor(''); return; }
    let alive = true;
    api.availabilityMatrix(leagues.map((l) => l.league_id), [pid])
      .then((m) => {
        if (!alive) return;
        const t = m.taken ?? {};
        setMatrix(t);
        setPendingMap(m.pending ?? {});
        const ids = leagues.map((l) => l.league_id);
        // Una liga ausente de `taken` es una liga que no se pudo leer: sin
        // esto el front la tomaba por "no lo tenes".
        setFailed(ids.filter((id) => !(id in t)));
        setMatrixFor(pid);
      })
      .catch(() => {
        if (!alive) return;
        setMatrix({});
        setFailed(leagues.map((l) => l.league_id));
        setMatrixFor(pid);
      });
    return () => { alive = false; };
  }, [claimFor?.player_id, leagues]);

  /** Ya es tuyo en esa liga (rostered y no es un claim propio pendiente). */
  function ownedIn(lid: string, pid: string): boolean {
    const t = matrix?.[lid];
    if (!t) return false;          // liga ausente = no medido, NO es "no lo tenes"
    const p = pendingMap?.[lid] ?? [];
    return t.includes(pid) && !p.includes(pid);
  }
  function pendingIn(lid: string, pid: string): boolean {
    return (pendingMap?.[lid] ?? []).includes(pid);
  }
  /** No se pudo leer esta liga: no se afirma nada, ni libre ni tomado. */
  function unknownIn(lid: string): boolean {
    return failed.includes(lid);
  }

  type Verdict = 'libre' | 'ya-tuyo' | 'pendiente' | 'desconocido';
  function verdictOf(lid: string, pid: string): Verdict {
    if (checking) return 'desconocido';
    if (unknownIn(lid)) return 'desconocido';
    if (ownedIn(lid, pid)) return 'ya-tuyo';
    if (pendingIn(lid, pid)) return 'pendiente';
    return 'libre';
  }
  /** El claim solo tiene sentido si no es un deadlock conocido. */
  function viable(lid: string, pid: string): boolean {
    return verdictOf(lid, pid) !== 'ya-tuyo';
  }

  async function reload() {
    try {
      setItems(await api.watchlist());
      setErr('');
    } catch (e: unknown) {
      setErr(String(e));
    }
  }

  useEffect(() => {
    void reload();
    const h = () => void reload();
    window.addEventListener('watchlist:changed', h);
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setClaimFor(null); };
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('watchlist:changed', h);
      window.removeEventListener('keydown', esc);
    };
  }, []);

  async function onSearch(v: string) {
    setQ(v);
    if (v.trim().length < 3) { setHits([]); return; }
    try {
      setHits(await api.search(v) as typeof hits);
    } catch { /* noop */ }
  }

  async function add(pid: string) {
    await api.watchAdd({ player_id: pid, base_bid: 0, notes: '' });
    setQ(''); setHits([]);
    await reload();
  }

  async function del(pid: string) {
    await api.watchDel(pid);
    await reload();
  }

  async function onDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    // Índices sobre la lista visible (respeta tab), aplicados al array real.
    // Sin dedupe: 1 fila = 1 player_id, el orden persiste tal cual.
    const vis = visible();
    const fromPid = String(active.id);
    const toPid = String(over.id);
    const from = items.findIndex((x) => x.player_id === fromPid);
    const to = items.findIndex((x) => x.player_id === toPid);
    if (from < 0 || to < 0 || !vis.some((x) => x.player_id === fromPid)) return;
    const next = arrayMove(items, from, to);
    setItems(next); // optimista
    try {
      await api.watchReorder(next.map((x) => x.player_id));
    } catch (e: unknown) {
      setErr(String(e));
      await reload();
    }
  }

  const [tab, setTab] = useState('ALL');

  /** Filtro IDP (como FA) + tab. Pos desconocida se conserva. */
  function visible(): WatchItem[] {
    return items.filter((x) => {
      const p = (x.pos ?? '').toUpperCase();
      if (p && !FANTASY_POS.includes(p)) return false;
      if (tab !== 'ALL' && p !== tab) return false;
      return true;
    });
  }

  return (
    <div className="panel">
      <h3>Watchlist</h3>
      {err && <span className="badge bad">{err}</span>}
      <div className="row">
        <input className="grow" placeholder="Buscar add (min 3 letras)…"
          value={q} onChange={(e) => void onSearch(e.target.value)} />
      </div>
      {hits.map((h) => (
        <div key={h.player_id} className="row">
          <span className="grow">{h.name} <small className="muted">{h.pos} {h.team}</small></span>
          <button className="primary" onClick={() => void add(h.player_id)}>+ watch</button>
        </div>
      ))}
      <div className="tabs" style={{ marginTop: 8 }}>
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'active' : ''}
            onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={(e) => void onDragEnd(e)}>
        <SortableContext items={visible().map((x) => x.player_id)} strategy={verticalListSortingStrategy}>
          {visible().map((x) => <Row key={x.player_id} item={x} onDel={(pid) => void del(pid)} onClaim={(it) => setClaimFor(it)} />)}
        </SortableContext>
      </DndContext>
      {visible().length === 0 && (
        <div className="empty">
          <span>Vacía — busca arriba y agrega.</span>
          <span className="hint">Tu lista de pendientes: los jugadores que
            quieres tener en mente cuando estén en waivers.</span>
        </div>
      )}
      {claimFor && (
        <div className="modal-backdrop" onClick={() => setClaimFor(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-x" onClick={() => setClaimFor(null)} title="cerrar (esc)">×</button>
            <h3>
              <Avatar pid={claimFor.player_id} name={claimFor.name ?? claimFor.player_id} />{' '}
              +{claimFor.name ?? claimFor.player_id} <PosChip pos={claimFor.pos} />
            </h3>
            <div><small className="muted">elige liga:</small></div>
            {checking && <div className="row"><small className="muted">viendo dónde ya está rosterizado…</small></div>}
            {(() => {
              const pid = claimFor.player_id;
              // Mientras carga NO se cuentan ligas: si no, el multibid
              // aparece con todas y se va solo con las que sí.
              const vivas = checking ? [] : leagues.filter((l) => viable(l.league_id, pid));
              return (
                <>
                  {!checking && vivas.length > 1 && (
                    <div className="row">
                      <button className="multibtn grow" onClick={() => {
                        onMulti(pid, claimFor.name ?? pid, vivas);
                        setClaimFor(null);
                      }}>
                        ⚡ multibid en {vivas.length} liga{vivas.length === 1 ? '' : 's'}
                      </button>
                    </div>
                  )}
                  {leagues.map((l) => {
                    const v = verdictOf(l.league_id, pid);
                    const ok = v !== 'ya-tuyo';
                    const ETIQUETA: Record<Verdict, string> = {
                      libre: 'elegir',
                      'ya-tuyo': 'ya tuyo',
                      pendiente: 'pendiente',
                      desconocido: checking ? '…' : 'sin dato',
                    };
                    return (
                      <div key={l.league_id} className="row">
                        <span className="grow">
                          {l.name} <small className="muted">{l.mode}</small>
                          {v === 'ya-tuyo' && <small className="muted"> — ya es tuyo</small>}
                          {v === 'pendiente' && <small className="muted"> — claim pendiente</small>}
                          {v === 'desconocido' && !checking && (
                            <small className="muted"> — no pude leer esta liga</small>
                          )}
                        </span>
                        {/* Mientras carga el botón NO dice "elegir": si no,
                            el primer render ofrece los 4 como clicables y
                            el estado real aparece segundos después. */}
                        <button className="primary"
                          disabled={!ok || v === 'desconocido'}
                          title={v === 'ya-tuyo'
                            ? 'ya está en tu roster de esa liga'
                            : v === 'desconocido'
                              ? 'Sleeper no respondió para esta liga'
                              : ''}
                          onClick={() => {
                            if (!ok || v === 'desconocido') return;
                            onClaim(pid, claimFor.name ?? pid, l);
                            setClaimFor(null);
                          }}>
                          {ETIQUETA[v]}
                        </button>
                      </div>
                    );
                  })}
                  {!checking && vivas.length === 0 && (
                    <div className="row">
                      <small className="muted">
                        ya lo tenés en todas tus ligas: no hay claim que hacer.
                      </small>
                    </div>
                  )}
                  {!checking && vivas.length === 0 && failed.length > 0 && (
                    <div className="row">
                      <small className="muted">
                        (puede faltar alguna: {failed.length} liga(s) no se pudieron leer)
                      </small>
                    </div>
                  )}
                </>
              );
            })()}
          </div>
        </div>
      )}
    </div>
  );
}
