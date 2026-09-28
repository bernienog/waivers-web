import { useEffect } from 'react';

/** Escape cierra el modal. Todos los modales lo usan: el botón ✕
 *  promises "cerrar (esc)" y tiene que ser verdad. */
export function useEsc(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
}
