import React, { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { getMe, logout, type Me } from './api'

type AuthContextType = {
  user: Me | null
  setUser: (u: Me | null) => void
  loginVisible: boolean
  setLoginVisible: (v: boolean) => void
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Me | null>(null)
  const [loginVisible, setLoginVisible] = useState(false)

  useEffect(() => {
    // Try fetch current user on mount
    getMe()
      .then((u) => setUser(u))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const onAuthRequired = () => setLoginVisible(true)
    window.addEventListener('auth:required', onAuthRequired as EventListener)
    return () => window.removeEventListener('auth:required', onAuthRequired as EventListener)
  }, [])

  const signOut = async () => {
    try {
      await logout()
    } finally {
      setUser(null)
      setLoginVisible(true)
    }
  }

  const value = useMemo<AuthContextType>(
    () => ({ user, setUser, loginVisible, setLoginVisible, signOut }),
    [user, loginVisible]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>')
  return ctx
}

