import { useState } from 'react';
import { AgentTasks } from './AgentTasks';
import { AiHistory } from './AiHistory';
import { AiCreation } from './AiCreation';
import { AiDrafts } from './AiDrafts';
import { AiMemory } from './AiMemory';
import { AiConfiguration } from './AiConfiguration';
import '../../styles/ai-workspace.css';

const TABS = [['tasks', '对话任务'], ['create', '生成内容'], ['logs', '生成记录'], ['assets', '生成素材'], ['drafts', '批量草稿'], ['memory', '内容记忆'], ['config', '模型与配置']] as const;
type Tab = typeof TABS[number][0];
export function AiWorkspace({ persona }: { readonly persona: string }) {
  const [tab, setTab] = useState<Tab>('tasks');
  function content() {
    if (!persona && !['tasks', 'logs', 'config'].includes(tab)) return <p>请在侧栏选择画像，再使用此功能。通用模式的对话可在「对话任务」查看。</p>;
    switch (tab) {
      case 'tasks': return <AgentTasks persona={persona} />;
      case 'create': return <AiCreation persona={persona} />;
      case 'logs': return <AiHistory persona={persona} />;
      case 'assets': return <AiHistory persona={persona} assets />;
      case 'drafts': return <AiDrafts persona={persona} />;
      case 'memory': return <AiMemory persona={persona} />;
      case 'config': return <AiConfiguration />;
      default: { const unreachable: never = tab; return unreachable; }
    }
  }
  return <div className="accounts-page ai-workspace"><h1 className="page-title">AI 工作区</h1><p>当前画像：{persona || '通用模式'}</p>
    <nav className="subnav-tabs" aria-label="AI 工作区">{TABS.map(([id, label]) => <button className={`subnav-tab ${tab === id ? 'active' : ''}`} aria-pressed={tab === id} key={id} onClick={() => setTab(id)}>{label}</button>)}</nav>
    <div key={`${persona}:${tab}`} className="ai-content">{content()}</div>
    <details className="ai-operator"><summary>运行状态</summary><a href="./metrics" target="_blank" rel="noreferrer">HTTP 与队列运行指标</a></details>
  </div>;
}
