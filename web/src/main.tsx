import { StrictMode, Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

/** Antes, una excepción al pintar dejaba la ventana EN BLANCO y el único
 *  rastro estaba en la consola: el usuario veía una app muerta sin saber qué
 *  pasó. Acá se le dice qué pasó y cómo salir. */
class CodoError extends Component<{ children: ReactNode }, { msg: string | null }> {
  state = { msg: null as string | null }

  static getDerivedStateFromError(e: unknown) {
    return { msg: e instanceof Error ? e.message : 'error desconocido' }
  }

  componentDidCatch(e: unknown, info: ErrorInfo) {
    console.error('[waivers] fallo al pintar', e, info.componentStack)
  }

  render() {
    if (this.state.msg === null) return this.props.children
    return (
      <div style={{ padding: 40, maxWidth: 520, margin: '0 auto', color: '#e8ecf4' }}>
        <h2 style={{ color: '#ff4d5e', fontSize: 18 }}>La app se atoró</h2>
        <p style={{ color: '#8b93a7', fontSize: 14, lineHeight: 1.7 }}>
          Algo falló al pintar. Tu sesión y tus ligas están bien guardadas.
        </p>
        <p style={{ color: '#8b93a7', fontSize: 13 }}>Cierra Waivers y vuelve a abrirlo.</p>
        <button
          className="primary"
          style={{ marginTop: 12 }}
          onClick={() => location.reload()}
        >
          recargar
        </button>
      </div>
    )
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CodoError>
      <App />
    </CodoError>
  </StrictMode>,
)
