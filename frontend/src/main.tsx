import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@fontsource/manrope/600.css'
import '@fontsource/manrope/700.css'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import './styles.css'
import AuthGate from './auth/AuthGate'

// Keep saved links from the retired theme, including job, query and hash.
if (/^\/v2(?:\/|$)/.test(location.pathname)) {
  const canonical = new URL(location.href)
  canonical.pathname = canonical.pathname.replace(/^\/v2(?=\/|$)/, '') || '/'
  history.replaceState(history.state, '', canonical)
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false }, mutations: { retry: false } } })
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><QueryClientProvider client={client}><AuthGate /></QueryClientProvider></React.StrictMode>,
)
