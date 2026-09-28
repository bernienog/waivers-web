import { useEffect, useState } from 'react';
import { api, codigoDe, textoDeError } from '../api';
import type { League } from '../types';

const PRESETS = [1, 5, 10, 25, 50, 100];

export default function BidModal({ league, names, tid, leg, current, onClose, onSent }: {
  league: League;
  names: string;
  tid: string;
  leg: number;
  current: number;
  onClose: () => void;
  onSent: (tid: string, bid: number) => void;
}) {
  const budget = league.budget_left ?? 0;
  const min = league.bid_min ?? 0;
  const [bid, setBid] = useState(current);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  function clamp(n: number) {
    if (Number.isNaN(n)) return min;
    return Math.max(min, Math.min(budget, Math.floor(n)));
  }

  function preset(pct: number) {
    setBid(clamp((budget * pct) / 100));
  }

  async function send() {
    setMsg('');
    try {
      const next = clamp(bid);
      await api.updateBid(league.league_id, tid, leg, next);
      onSent(tid, next);
      onClose();
    } catch (e: unknown) {
      setMsg(textoDeError(e, codigoDe(e)));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} title="cerrar (esc)">×</button>
        <h3>{names}</h3>
        <small className="muted">{league.name} · saldo {budget} · min {min}</small>
        <div className="row" style={{ justifyContent: 'center' }}>
          <button className="addbtn" onClick={() => setBid((b) => clamp(b - 1))}>−</button>
          <input className="bidinput" type="number" value={bid}
            onChange={(e) => setBid(clamp(Number(e.target.value)))} />
          <button className="addbtn" onClick={() => setBid((b) => clamp(b + 1))}>+</button>
        </div>
        <div className="tabs" style={{ justifyContent: 'center' }}>
          {PRESETS.map((p) => (
            <button key={p} className="btnlight" onClick={() => preset(p)}>
              {p === 100 ? 'MAX' : `${p}%`}
            </button>
          ))}
        </div>
        <small className="muted">presets sobre saldo ({budget}), piso min ({min})</small>
        <div className="row">
          <button className="primary grow" onClick={() => void send()}>
            send update ${clamp(bid)}
          </button>
        </div>
        {msg && <small className="muted">{msg}</small>}
      </div>
    </div>
  );
}
