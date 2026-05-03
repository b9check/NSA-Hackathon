import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'


// ---- In-page error display so we don't need DevTools ----
function showError(title: string, msg: string) {
  let div = document.getElementById('critical-error')
  if (!div) {
    div = document.createElement('div')
    div.id = 'critical-error'
    div.style.cssText = [
      'position:fixed', 'top:0', 'left:0', 'right:0',
      'background:#3a0d12', 'color:#ffb4b4',
      'padding:14px 18px', 'font:12px ui-monospace,monospace',
      'z-index:99999', 'white-space:pre-wrap', 'max-height:60vh',
      'overflow:auto', 'border-bottom:2px solid #ff4d5e',
    ].join(';')
    document.body.appendChild(div)
  }
  div.textContent = `${title}\n\n${msg}`
}

window.addEventListener('error', (e) => {
  const err = (e as ErrorEvent).error
  showError(
    'JS ERROR',
    `${err?.message ?? (e as ErrorEvent).message ?? '(unknown)'}\n${err?.stack ?? ''}`,
  )
})

window.addEventListener('unhandledrejection', (e) => {
  const r: any = (e as PromiseRejectionEvent).reason
  showError(
    'UNHANDLED PROMISE REJECTION',
    `${r?.message ?? r ?? '(unknown)'}\n${r?.stack ?? ''}`,
  )
})


// ---- React error boundary ----
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    showError('REACT RENDER ERROR', `${error.message}\n\n${error.stack}\n\nComponent stack:${info.componentStack}`)
  }
  render() {
    if (this.state.error) {
      return null // showError already painted the box; let it stand alone
    }
    return this.props.children
  }
}


ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
