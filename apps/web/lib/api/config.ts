/**
 * Base URL of the API, inlined at build time (see `.env.example`). The web app and the API must be same-site for the
 * session cookie to be sent (`localhost` for both locally; `app.example.com` and `api.example.com` in production),
 * so the default uses `localhost`, not `127.0.0.1`.
 */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8765').replace(/\/+$/, '')
