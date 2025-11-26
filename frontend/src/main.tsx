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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
