import { useEffect, useRef, useState } from 'react';
import { api, codigoDe, fmtClears, fmtDay3, textoDeError } from '../api';
import { Avatar } from './PlayerBits';

/** Fila de agente libre. La comparten la lista y el modal (para la
 *  tarjeta del jugador). */
export interface FA {
  player_id: string;
  name: string;
  pos?: string;
  team?: string;
  clears_at?: number | null;
  /** Cuándo resuelve (solo si Sleeper nos da la fecha: en waivers). */
  resolves_at?: number | null;
  /** Ritmo de waivers de la liga (diario/semanal + días de clear). */
  cadence?: string;
  inj?: string | null;
  proj?: number | null;
  opp?: string | null;
  /** Columnas crudas de la semana (espejo de la tabla de Sleeper). */
  st?: { att?: number | null; cmp?: number | null; ryd?: number | null;
        rtd?: number | null; rec?: number | null; reyd?: number | null;
        reydtd?: number | null } | null;
}

const TABS = ['ALL', 'QB', 'RB', 'WR', 'TE', 'K', 'DEF'];

/** Los DST no tienen headshot en Sleeper (su player_id es el del equipo),
 * así que la celda lleva marcador de abreviación en vez de foto. */
function hasAv(p: { player_id: string; pos?: string }): boolean {
  if (!p.player_id) return false;
  if ((p.pos || '').toUpperCase() === 'DEF') return false;
  return true;
}

/** Las tabs siguen las posiciones que ROSTERIZA la liga (regla de la
 * liga, no un set fijo): en una liga sin K no aparece K. */function tabsFor(positions?: string[] | null): string[] {
  if (!positions || !positions.length) return TABS;
  const rpos = positions.map((p) => String(p).toUpperCase());
  const out = ['ALL'];
  const slots: [string, string][] = [['QB', 'QB'], ['RB', 'RB'], ['WR', 'WR'],
    ['TE', 'TE'], ['K', 'K']];
  for (const [slot, tab] of slots) {
    if (rpos.includes(slot)) out.push(tab);
  }
  if (rpos.includes('DEF') || rpos.includes('DST')) out.push('DEF');
  return out;
}

export default function FreeAgentsPanel({ leagueId, tick, active, onAdd, onPickup, positions }: {
  leagueId: string;
  tick: number;
  active: boolean;
  onAdd: (pid: string, name: string) => void;
  onPickup: (p: FA) => void;
  positions?: string[] | null;
}) {
  const tabs = tabsFor(positions);
  const [q, setQ] = useState('');
  const [pos, setPos] = useState('ALL');
  const [sort, setSort] = useState('proj');
  const [list, setList] = useState<FA[]>([]);
  const [faErr, setFaErr] = useState('');
  const [loading, setLoading] = useState(false);
  const [watched, setWatched] = useState<Set<string>>(new Set());
  // Debounce de búsqueda (antes: un fetch por tecla) + guarda de
  // carrera. El timer vive en ref para cancelarlo al desmontar.
  const searchTimer = useRef<number | null>(null);
  const searchSeq = useRef(0);

  async function load(query = q, p = pos, s = sort, seq = searchSeq.current) {
    setLoading(true);
    try {
      const r = await api.freeAgents(leagueId, query, p === 'ALL' ? '' : p, 20, s);
      if (searchSeq.current !== seq) return;
      setFaErr('');
      setList(r);
    } catch (e: unknown) {
      if (searchSeq.current !== seq) return;
      setFaErr(s === 'proj'
        ? `sin proyecciones: ${textoDeError(e, codigoDe(e))} — cambia a Rank.`
        : textoDeError(e, codigoDe(e)));
      setList([]);
    } finally {
      if (searchSeq.current === seq) setLoading(false);
    }
  }

  function queueLoad(query: string, p: string, s = sort) {
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
    const seq = ++searchSeq.current;
    searchTimer.current = window.setTimeout(() => { void load(query, p, s, seq); }, 250);
  }

  async function loadWatched() {
    try {
      const ws = await api.watchlist();
      setWatched(new Set(ws.map((w) => w.player_id)));
    } catch { /* noop */ }
  }

  async function toggleWatch(p: FA) {
    try {
      if (watched.has(p.player_id)) {
        await api.watchDel(p.player_id);
      } else {
        await api.watchAdd({ player_id: p.player_id });
      }
      window.dispatchEvent(new Event('watchlist:changed'));
      await loadWatched();
    } catch { /* noop */ }
  }

  // Reset de búsqueda solo al cambiar de liga (antes el tick global
  // borraba lo que estabas escribiendo). Gateado: oculto no fetchea;
  // al volver, el tick coalescado recarga.
  useEffect(() => { setQ(''); setPos('ALL'); }, [leagueId]); // eslint-disable-line
  useEffect(() => {
    if (!active) return;
    searchSeq.current += 1;
    void load('', 'ALL', sort, searchSeq.current);
    void loadWatched();
    return () => {
      if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
    };
  }, [leagueId, tick, active]); // eslint-disable-line

  return (
    <div className="panel">
      <h3>Free agents (top 20 por rank Sleeper)</h3>
      <input className="grow" style={{ width: '100%' }} placeholder="Buscar FA…"
        value={q} onChange={(e) => { setQ(e.target.value); queueLoad(e.target.value, pos); }} />
      <div className="tabs" style={{ marginTop: 8 }}>
        {tabs.map((t) => (
          <button key={t} className={pos === t ? 'active' : ''}
            onClick={() => { setPos(t); searchSeq.current += 1; void load(q, t, sort, searchSeq.current); }}>{t}</button>
        ))}
      </div>
      <div className="tabs">
        {(['proj', 'rank'] as const).map((s) => (
          <button key={s} className={sort === s ? 'active' : ''}
            onClick={() => { setSort(s); searchSeq.current += 1; void load(q, pos, s, searchSeq.current); }}>
            {s === 'proj' ? 'Proy ↓' : 'Rank'}
          </button>
        ))}
      </div>
      {loading && <div className="loadbar"><div /></div>}
      {faErr && <div><small className="muted">⚠ {faErr}</small></div>}
      {sort === 'proj' && !faErr && list.length > 0 && !list.some((p) => p.proj != null) && (
        <div><small className="muted">filas sin proyección — van al fondo por rank.</small></div>
      )}
      {/* Encabezado: mismo grid que las filas, así las columnas caen
          unas sobre otras. */}
      <div className="fa-grid fa-head">
        <span />
        <span className="name" />
        <span className="c" title="puntos proyectados con el scoring de tu liga">pts</span>
        <span className="c">att</span>
        <span className="c">rush</span>
        <span className="c">td</span>
        <span className="c">rec</span>
        <span className="c">yds</span>
        <span className="c">td</span>
        <span className="c">cmp</span>
        <span />
        <span />
      </div>
      {loading && list.length === 0 ? (
        // Skeletons con la misma animación del resto (shimmer) + loadbar.
        <>
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="fa-grid fa-row">
              <div className="skel circle" style={{ width: 22, height: 22 }} />
              <div><div className="skel" style={{ height: 11, width: '62%' }} />
                <div className="skel" style={{ height: 9, width: '42%', marginTop: 3 }} /></div>
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ height: 13 }} />
              <div className="skel" style={{ width: 34, height: 22 }} />
            </div>
          ))}
        </>
      ) : (
        list.map((p) => {
          // En waivers en ESTA liga -> no se puede agregar: se reclama.
          // El estado se marca con el filo de la fila, sin repetir texto.
          const onWaivers = p.clears_at != null;
          const s = p.st;
          const has = p.proj != null && !!s;
          return (
        <div key={p.player_id} className={`fa-grid fa-row${onWaivers ? ' onw' : ''}`}>
          {/* La celda del avatar SIEMPRE existe, aunque no haya foto: los
              DST no tienen headshot y Avatar devuelve null; sin esta
             .span, esa columna desaparece y TODO se corre una casilla
              (los nombres se veían corridos). El hueco también se
              respeta, con un marcador del equipo. */}
          <span className="fa-av">
            <Avatar pid={p.player_id} name={p.name} />
            {!hasAv(p) && (
              <span className="fa-av-ph"
                title={p.pos === 'DEF' ? 'defensa de equipo' : 'sin foto'}>
                {(p.team || p.pos || '?').slice(0, 2).toUpperCase()}
              </span>
            )}
          </span>
          {/* Nombre y pos/equipo en dos líneas (como Sleeper): el nombre
              nunca compite con el rival ni con las columnas. */}
          <div className="fa-nm">
            <div className="fa-name">
              {p.name}
              {p.inj ? <span className="badge bad" title="lesionado"> {p.inj}</span> : null}
            </div>
            <div className="fa-sub">
              {p.pos} · {p.team}{p.opp ? ` vs ${p.opp}` : ''}
            </div>
          </div>
          <span className="fa-pts">{has ? p.proj!.toFixed(1) : '·'}</span>
          <span className="fa-c">{has ? (s!.att ?? '·') : '·'}</span>
          <span className="fa-c">{has ? (s!.ryd ?? '·') : '·'}</span>
          <span className="fa-c">{has ? (s!.rtd ?? '·') : '·'}</span>
          <span className="fa-c">{has ? (s!.rec ?? '·') : '·'}</span>
          <span className="fa-c">{has ? (s!.reyd ?? '·') : '·'}</span>
          <span className="fa-c">{has ? (s!.reydtd ?? '·') : '·'}</span>
          <span className="fa-c">{has ? (s!.cmp ?? '·') : '·'}</span>
          {/* Mismos botones sobrios que el resto de la app. */}
          <button className={`fa-act star${watched.has(p.player_id) ? ' on' : ''}`}
            title={watched.has(p.player_id) ? 'Quitar de watchlist' : 'Agregar a watchlist'}
            onClick={() => void toggleWatch(p)}>
            {watched.has(p.player_id) ? '★' : '☆'}
          </button>
          {/* UNA sola acción por fila, como en Sleeper. Nunca las dos:
              - está en waivers -> W (no se puede agregar: se reclama)
              - libre           -> + (se agrega ya, al instante) */}
          {onWaivers ? (
            <span className="fa-cell">
              <button className="fa-act claim" onClick={() => onAdd(p.player_id, p.name)}
                title={`Poner claim: sale de waivers ${fmtClears(p.clears_at!)}`}>W</button>
              <span className="d">{fmtDay3(p.clears_at!)}</span>
            </span>
          ) : (
            <button className="fa-act add" title="Agregar ahora a tu roster"
              onClick={() => onPickup(p)}>+</button>
          )}
        </div>
          );
        })
      )}
      {!loading && list.length === 0 && <small className="muted">Sin resultados.</small>}
    </div>
  );
}
