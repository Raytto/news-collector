import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import 'antd/dist/reset.css'

const siteIcon = (import.meta.env.FRONTEND_SITE_ICON as string | undefined) || 'data/agentduck.png'

function applyFavicon(href: string) {
  if (!href) return
  const head = document.head
  const existing = head.querySelector<HTMLLinkElement>('link[rel*="icon"]')
  const link = existing ?? document.createElement('link')
  link.rel = 'icon'
  link.href = href
  link.type = 'image/png'
  if (!existing) {
    head.appendChild(link)
  }
}

applyFavicon(siteIcon)

// Simple build tag so deployed assets get a fresh hash when rebuilt.
const BUILD_TAG = 'frontend-20251128-02'
console.info('[agentduck] build', BUILD_TAG)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
