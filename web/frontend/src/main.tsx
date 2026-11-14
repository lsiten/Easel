import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { PublicAgentTask } from './components/ai/AgentTasks';
import './styles/ai-workspace.css';
import App from './App.tsx'
import ErrorBoundary from './components/ErrorBoundary.tsx'
import { ChannelHandoffGuest } from './components/ChannelHandoffGuest.tsx'

export const BUILD_ID = 'subnav-1';
// 打到控制台，便于确认浏览器加载的是最新前端（排查缓存旧包）
console.log(`%cEasel build: ${BUILD_ID}`, 'color:#8b5cf6;font-weight:bold');
const query = new URLSearchParams(window.location.search);
const guest = query.has('handoff') || query.has('token');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      {query.has('aiShare') ? <PublicAgentTask token={query.get('aiShare') || ''} /> : guest ? <ChannelHandoffGuest key={JSON.stringify([query.get('handoff'), query.get('token')])} templateId={query.get('handoff') || ''} token={query.get('token') || ''} /> : <App />}
    </ErrorBoundary>
  </StrictMode>,
)
