'use client'

import useSWR, { useSWRConfig } from 'swr'
import { fetchMe, logout, type AuthState } from '@/lib/auth/session'

const KEY = 'auth/me'

/** The signed-in user (or a visitor). `loading` is true only until the first answer. */
export function useAuth() {
  const { data, isLoading } = useSWR<AuthState>(KEY, fetchMe, { revalidateOnFocus: true, shouldRetryOnError: false })
  const { mutate } = useSWRConfig()

  async function signOut() {
    await logout()
    // Everything cached belongs to the previous user.
    await mutate(() => true, undefined, { revalidate: false })
    await mutate(KEY, { authenticated: false, user: null, loginEnabled: data?.loginEnabled ?? true }, { revalidate: false })
  }

  return {
    loading: isLoading,
    authenticated: data?.authenticated ?? false,
    user: data?.user ?? null,
    loginEnabled: data?.loginEnabled ?? true,
    signOut,
  }
}
