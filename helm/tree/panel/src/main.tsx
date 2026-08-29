import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { Login } from './Login'
import './styles/tokens.css'

// /login — не клиентский маршрут SPA, а точка приземления редиректа из
// /auth/telegram/callback (ТЗ §10.5.6-§10.5.7): Caddy отдаёт index.html на
// любой путь, поэтому серверу достаточно перенаправить сюда с ?step=,
// не зная ничего о фронтенде.
const isLogin = window.location.pathname === '/login'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isLogin ? <Login /> : <App />}
  </StrictMode>,
)
