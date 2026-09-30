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
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const pid = claimFor?.player_id;
    if (!pid || !leagues.length) { setMatrix(null); return; }
    let alive = true;
    setChecking(true);
    api.availabilityMatrix(leagues.map((l) => l.league_id), [pid])
      .then((m) => {
        if (!alive) return;
        setMatrix(m.taken ?? {});
        setPendingMap(m.pending ?? {});
      })
      .catch(() => { if (alive) setMatrix({}); })
      .finally(() => { if (alive) setChecking(false); });
    return () => { alive = false; };
  }, [claimFor?.player_id, leagues]);

  /** Ya es tuyo en esa liga (taken y no es un claim propio pendiente). */
  function ownedIn(lid: string, pid: string): boolean {
    const t = matrix?.[lid] ?? [];
    const p = pendingMap?.[lid] ?? [];
    return t.includes(pid) && !p.includes(pid);
  }
  function pendingIn(lid: string, pid: string): boolean {
    return (pendingMap?.[lid] ?? []).includes(pid);
  }
  /** Ligas donde el claim todavía tiene sentido. */
  function viable(lid: string, pid: string): boolean {
    if (checking || matrix === null) return true;   // sin dato: no se cierra la puerta
    return !ownedIn(lid, pid);
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
              const vivas = leagues.filter((l) => viable(l.league_id, pid));
              return (
                <>
                  {vivas.length > 1 && (
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
                    const ok = viable(l.league_id, pid);
                    const pend = pendingIn(l.league_id, pid);
                    return (
                      <div key={l.league_id} className="row">
                        <span className="grow">
                          {l.name} <small className="muted">{l.mode}</small>
                          {!ok && <small className="muted"> — ya es tuyo</small>}
                          {ok && pend && <small className="muted"> — claim pendiente</small>}
                        </span>
                        <button className="primary" disabled={!ok}
                          title={ok ? '' : 'ya está en tu roster de esa liga'}
                          onClick={() => {
                            if (!ok) return;
                            onClaim(pid, claimFor.name ?? pid, l);
                            setClaimFor(null);
                          }}>
                          {ok ? 'elegir' : 'ya tuyo'}
                        </button>
                      </div>
                    );
                  })}
                  {vivas.length === 0 && !checking && (
                    <div className="row">
                      <small className="muted">
                        ya lo tenés en todas tus ligas: no hay claim que hacer.
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
