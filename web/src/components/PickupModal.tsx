import { useEffect, useState } from 'react';
import { api } from '../api';
import { useEsc } from '../useEsc';
import { PlayerCard, Avatar, PosChip } from './PlayerBits';
import type { League } from '../types';

interface RosterPlayer { player_id: string; name: string; pos?: string }

/** Agregar un agente libre directo a tu roster (sin waivers).
 *
 * Qué es y qué NO es, en humano:
 *  - NO usa tu prioridad de waivers ni tu presupuesto.
 *  - Pasa de inmediato: no hay nada pendiente ni que cancelar después.
 *  - No se puede deshacer desde la app (se revierte agregando/dropeando
 *    en Sleeper).
 * Por eso va en dos pasos: primero eliges a quién sacas, luego confirmas.
 *
 * El otro camino es el botón W de la lista: ese pone el claim y espera
 * al cierre de waivers. */
export default function PickupModal({ league, pid, name, pos, team, onClose, onDone }: {
  league: League;
  pid: string;
  name: string;
  pos?: string;
  team?: string;
  onClose: () => void;
  onDone: (pid: string) => void;
}) {
  const [roster, setRoster] = useState<RosterPlayer[] | null>(null);
  const [drop, setDrop] = useState('');
  const [step, setStep] = useState<1 | 2>(1);
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  useEsc(onClose);

  const free = league.roster_space?.free ?? 0;
  const dropPlayer = (roster ?? []).find((p) => p.player_id === drop);

  useEffect(() => {
    void api.roster(league.league_id)
      .then((r) => setRoster(r.players ?? []))
      .catch((e: unknown) => setMsg(String(e)));
  }, [league.league_id]);

  /** Errores del backend traducidos: la UI habla como persona. */
  function friendly(raw: string): string {
    const m = raw.toLowerCase();
    if (m.includes('sin drop y sin slots')) return 'No te quedan espacios libres: elige a quién sacar de tu roster.';
    if (m.includes('ya tiene dueño')) return `${name} ya está en el roster de alguien en esta liga.`;
    if (m.includes('no esta en tu roster')) return 'Ese jugador ya no está en tu roster. Recarga la lista.';
    if (m.includes('no puedes dropear')) return 'No puedes sacar a la misma persona que quieres agregar.';
    if (m.includes('rechazado')) return 'Sleeper no aceptó el movimiento. Espera un momento e inténtalo otra vez.';
    if (m.includes('app cerrada') || m.includes('failed to fetch')) return 'No pude hablar con la app. Reinténtalo.';
    return raw;
  }

  async function send() {
    setBusy(true);
    setMsg('');
    try {
      const r = await api.pickup(league.league_id, pid, drop);
      setMsg(`Listo: ${name} ya está en tu roster${r.transaction_id ? ' ✓' : ''}.`);
      window.setTimeout(() => onDone(pid), 900);
    } catch (e: unknown) {
      setMsg(friendly(String(e)));
    } finally {
      setBusy(false);
    }
  }

  const canContinue = free > 0 || !!drop;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} title="cerrar (esc)">×</button>

        {step === 1 ? (
          <>
            <h3>agregar a {name}</h3>
            <PlayerCard pid={pid} name={name} pos={pos} team={team} />
            <div><small className="muted">
              Lo agregas directo a tu roster en <b>{league.name}</b>: no usa tu
              prioridad de waivers, no descuita presupuesto y <b>se aplica al
              instante</b>. No queda nada pendiente.
            </small></div>
            {free > 0 ? (
              <div><small className="muted">
                Tienes {free} espacio{free === 1 ? '' : 's'} libre{free === 1 ? '' : 's'}:
                puedes agregarlo sin sacar a nadie.
              </small></div>
            ) : (
              <div><small className="muted">
                No te quedan espacios libres, así que tienes que sacar a alguien
                de tu roster.
              </small></div>
            )}
            <div className="pickfield">
              <small className="muted">¿a quién sacas?</small>
              <select value={drop} onChange={(e) => setDrop(e.target.value)}>
                {free > 0 && <option value="">nadie (usar un espacio libre)</option>}
                {roster === null && <option value="">leyendo tu roster…</option>}
                {(roster ?? []).map((p) => (
                  <option key={p.player_id} value={p.player_id}>
                    {p.name}{p.pos ? ` · ${p.pos}` : ''}
                  </option>
                ))}
              </select>
            </div>
            {roster !== null && roster.length === 0 && free <= 0 && (
              <div><small className="muted">No veo tu roster; recarga la lista.</small></div>
            )}
            {msg && <div><small className="muted">{msg}</small></div>}
            <div className="btnrow">
              <button className="primary" onClick={() => setStep(2)}
                disabled={!canContinue}>siguiente</button>
              <button onClick={onClose}>cancelar</button>
            </div>
          </>
        ) : (
          <>
            <h3>confirma</h3>
            <PlayerCard pid={pid} name={name} pos={pos} team={team} />
            {dropPlayer && (
              <div className="pcard">
                <div className="top">
                  <Avatar pid={dropPlayer.player_id} name={dropPlayer.name} />
                  <span className="nm">sacas a {dropPlayer.name}</span>
                </div>
                <div className="bot">
                  {dropPlayer.pos ? <PosChip pos={dropPlayer.pos} /> : null}
                </div>
              </div>
            )}
            <div><small className="muted">
              {dropPlayer ? <>Sale <b>{dropPlayer.name}</b> y entra <b>{name}</b>.</>
                : <>Entra <b>{name}</b> usando un espacio libre.</>}
            </small></div>
            <div><small className="muted">
              Pasa de inmediato y <b>no se puede deshacer desde la app</b>. Si te
              arrepientes, lo cambias en Sleeper.
            </small></div>
            {msg && <div><small className="muted">{msg}</small></div>}
            <div className="btnrow">
              <button className="primary" onClick={() => void send()} disabled={busy}>
                {busy ? 'agregando…' : 'sí, agregar'}
              </button>
              <button onClick={() => setStep(1)} disabled={busy}>volver</button>
              <button onClick={onClose} disabled={busy}>cancelar</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
