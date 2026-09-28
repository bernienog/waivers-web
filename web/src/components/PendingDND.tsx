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
import { Avatar } from './PlayerBits';
import type { PendingTx } from '../types';

export interface NamedTx extends PendingTx {
  addsNames: string[];
  dropsNames: string[];
  bid?: number | null;
}

function txMap(t: PendingTx): Record<string, { first_name?: string; last_name?: string }> {
  return (t as { player_map?: Record<string, { first_name?: string; last_name?: string }> }).player_map ?? {};
}

function pidName(
  pm: Record<string, { first_name?: string; last_name?: string }>,
  pid: string,
): string {
  const p = pm[pid];
  return p ? `${p.first_name ?? ''} ${p.last_name ?? ''}`.trim() : pid;
}

function Row({ tx, busy, onEditBid, onDrop }: {
  tx: NamedTx;
  busy: boolean;
  onEditBid: (tx: PendingTx) => void;
  onDrop: (tx: PendingTx) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition } =
    useSortable({ id: tx.transaction_id });
  const pm = txMap(tx);
  return (
    <div ref={setNodeRef} className="row" style={{ marginLeft: 16, transform: CSS.Transform.toString(transform), transition }}
      title={`tid ${tx.transaction_id}`}>
      <span {...attributes} {...listeners} style={{ cursor: 'grab' }}>⠿</span>
      <span className="stack">
        <span className="inline">
          {Object.keys(tx.adds ?? {}).map((pid) => (
            <span key={`a-${pid}`} className="inline">
              <Avatar pid={pid} name={pidName(pm, pid)} />
              <span className="add">+{pidName(pm, pid)}</span>
            </span>
          ))}
        </span>
        <span className="inline sub">
          {Object.keys(tx.drops ?? {}).map((pid) => (
            <span key={`d-${pid}`} className="inline">
              <Avatar pid={pid} name={pidName(pm, pid)} />
              <span>−{pidName(pm, pid)}</span>
            </span>
          ))}
        </span>
      </span>
      {tx.bid === undefined || tx.bid === null ? (
        <span className="badge claim">claim</span>
      ) : (
        <button className="badge faab" style={{ cursor: 'pointer', border: 0 }}
          title="editar bid" onClick={() => onEditBid(tx)}>${tx.bid} faab ✎</button>
      )}
      <button className="danger" disabled={busy} onClick={() => onDrop(tx)}>
        {busy ? 'dropping…' : 'drop waiver'}
      </button>
    </div>
  );
}

async function reorderApi(leagueId: string, ids: string[], leg = 1) {
  // Antes: fetch a mano que metía `JSON.stringify(r)` al Error. Ahora pasa
  // por api.pendingReorder(), que sanea lo que se muestra.
  const r = await api.pendingReorder(leagueId, ids, leg);
  return r.results ?? [];
}

export default function PendingDND({ leagueId, leg, txs, dropping, onEditBid, onDrop, onMsg }: {
  leagueId: string;
  leg: number;
  txs: NamedTx[];
  /** Tids en vuelo (`lid:tid`): el drop se deshabilita hasta reconciliar. */
  dropping?: Record<string, boolean>;
  onEditBid: (tx: PendingTx) => void;
  onDrop: (tx: PendingTx) => void;
  onMsg: (m: string) => void;
}) {
  const [items, setItems] = useState<NamedTx[] | null>(null); // null = SSOT servidor
  const live = items ?? txs;
  const dirty = items !== null;
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{
    ok: boolean; text: string; want: string; got: string;
  } | null>(null);
  const sensors = useSensors(useSensor(PointerSensor));

  // Resync SOLO si el contenido cambió (tids): antes cualquier poll de
  // fondo (nuevo array, mismos tids) borraba un drag en curso en silencio,
  // sin mensaje y sin tocar Sleeper.
  const idsKey = txs.map((x) => x.transaction_id).join(',');
  useEffect(() => { setItems(null); setResult(null); }, [idsKey]); // eslint-disable-line

  function onDragEnd(e: DragEndEvent) {
    if (sending) return; // en vuelo: no mover lo que ya se está enviando
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    setResult(null); // nuevo drag, nuevo veredicto
    const from = live.findIndex((x) => x.transaction_id === active.id);
    const to = live.findIndex((x) => x.transaction_id === over.id);
    setItems(arrayMove(live, from, to)); // solo local, sin enviar
  }

  /** Envía el orden + verifica contra Sleeper (ground truth, no promesas).
   * El backend ya reventó el cache de pendings: el re-pull es live. */
  async function send() {
    if (!items || sending) return;
    setSending(true);
    setResult(null);
    const want = items.map((x) => x.transaction_id);
    const nameMap = new Map<string, string>();
    [...txs, ...items].forEach((x) =>
      nameMap.set(x.transaction_id, x.addsNames.join('+') || x.transaction_id.slice(-4)));
    const nm = (tid: string) => nameMap.get(tid) ?? tid.slice(-4);
    try {
      const results = await reorderApi(leagueId, want, leg);
      const byId = new Map(results.map((r) => [r.transaction_id, r]));
      const failed = want.filter((tid) => !byId.get(tid)?.ok);
      if (failed.length) {
        const f0 = byId.get(failed[0]);
        // `error` es la frase segura del backend. `raw` (cuerpo crudo de la
        // mutación) queda solo para support y no se pinta.
        const detail = `falló ${nm(failed[0])}: ${f0?.error ?? 'sin detalle'}`;
        setResult({ ok: false, text: `reorder parcial (${failed.length}) — ${detail}`, want: '', got: '' });
        onMsg(`reorder parcial: ${detail}`);
      } else {
        // Todas 200: confirmar que Sleeper quedó en el orden pedido.
        let got: string[] = [];
        try {
          got = (await api.pending(leagueId)).map((t) => t.transaction_id);
        } catch { /* conserva comparación vacía */ }
        const match = got.length > 0 && got.join(',') === want.join(',');
        setResult({
          ok: match,
          text: match ? 'orden confirmado en Sleeper ✓'
            : 'Sleeper no refleja el orden (revisa)',
          want: want.map(nm).join(' → '),
          got: got.length ? got.map(nm).join(' → ') : '(sin re-pull)',
        });
        onMsg(match ? 'orden enviado ✓' : 'orden enviado pero Sleeper dice otro orden');
      }
      setItems(null);
    } catch (err: unknown) {
      const text = String(err);
      setResult({ ok: false, text, want: '', got: '' });
      onMsg(text);
      setItems(null); // revertir al SSOT
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      {dirty && (
        <div className="row">
          <span className="badge claim grow" style={{ textAlign: 'center' }}>orden local sin enviar</span>
          <button className="primary" disabled={sending} onClick={() => void send()}>
            {sending ? <><span className="spinner" />enviando…</> : 'enviar orden'}
          </button>
          <button disabled={sending} onClick={() => setItems(null)}>revertir</button>
        </div>
      )}
      {result && (
        <div className="row">
          <span className={result.ok ? 'badge claim grow' : 'badge bad grow'}
            style={{ textAlign: 'center' }}>{result.text}</span>
          {result.want && (
            <small className="muted">pediste {result.want} · Sleeper {result.got}</small>
          )}
          <button onClick={() => setResult(null)}>x</button>
        </div>
      )}
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={live.map((x) => x.transaction_id)} strategy={verticalListSortingStrategy}>
          {live.map((x) => <Row key={x.transaction_id} tx={x}
            busy={!!dropping?.[leagueId + ':' + x.transaction_id]}
            onEditBid={onEditBid} onDrop={onDrop} />)}
        </SortableContext>
      </DndContext>
    </>
  );
}
