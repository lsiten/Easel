import { useEffect, useState } from 'react';
import { channelDocument, channelRequest, channelRows, channelText, channelUrl } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelScope } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ChannelTaskControl } from './ChannelTaskControl';
import { ChannelRecordDetail } from './ChannelRecordDetail';

export function ChannelTasks({ scope, platform }: { readonly scope: ChannelScope; readonly platform: string }) {
  const [records, setRecords] = useState<readonly ChannelDocument[]>([]);
  const [flows, setFlows] = useState<readonly ChannelDocument[]>([]);
  const [summary, setSummary] = useState<ChannelDocument | null>(null);
  const [total, setTotal] = useState(0);
  const [flowTotal, setFlowTotal] = useState(0);
  const [flowOffset, setFlowOffset] = useState(0);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState('');
  const [reload, setReload] = useState(0);
  const [flowId, setFlowId] = useState('');
  const [taskId, setTaskId] = useState('');
  const [task, setTask] = useState<ChannelDocument | null>(null);
  const [record, setRecord] = useState<ChannelDocument | null>(null);
  const { busy, error, perform } = useChannelJob();
  const query = new URLSearchParams({ ...scope, platform, status, offset: String(offset), limit: '30' }).toString();
  useEffect(() => {
    void perform(async (signal) => {
      const [history, plans, totals] = await Promise.all([
        channelRequest(`/api/publish-records?${query}`, { signal }),
        channelRequest(`/api/publish-flows?${new URLSearchParams({ persona: scope.persona, limit: '20', offset: String(flowOffset) })}`, { signal }),
        channelRequest(`/api/publish-records/summary?${new URLSearchParams({ persona: scope.persona })}`, { signal }),
      ]);
      return { history: channelDocument(history), plans: channelDocument(plans), totals: channelDocument(totals) };
    }, ({ history, plans, totals }) => { setRecords(channelRows(history)); setTotal(Number(history.total) || 0); setFlows(channelRows(plans)); setFlowTotal(Number(plans.total) || 0); setSummary(totals); });
  }, [scope.persona, query, flowOffset, reload, perform]);
  function openTask(id: string) {
    void perform(async (signal) => channelDocument(await channelRequest(`/api/publish-flows/tasks/${encodeURIComponent(id)}/detail?${new URLSearchParams({ persona: scope.persona })}`, { signal })),
      (value) => { setTask(value); setRecord(null); });
  }
  const tasks = flows.flatMap((flow) => channelRows(flow.tasks)).filter((item) => item.platform === platform && (!scope.account_uid || item.account_uid === scope.account_uid));
  return <section className="panel">
    <h2 className="panel-title">发布计划</h2>
    <button className="btn btn-sm" disabled={busy} onClick={() => setReload((value) => value + 1)}>刷新计划与记录</button>
    {!busy && !tasks.length && <p className="pv-hint">当前账号暂无发布计划。在发布标签选择定时时间并审核后创建。</p>}
    {tasks.map((item) => <article key={channelText(item.id)} style={{ paddingBlock: 'var(--space-3, 12px)', overflowWrap: 'anywhere' }}>
      <strong>{channelText(item.state)}</strong> · {new Date(Number(item.publish_at) * 1000).toLocaleString()}<p className="pv-hint">任务 {channelText(item.id)}</p>
      <button className="btn btn-sm" disabled={busy} onClick={() => openTask(channelText(item.id))}>查看及管理计划</button>
    </article>)}
    {flowTotal > 20 && <div className="subnav-tabs"><button className="btn btn-sm" disabled={busy || flowOffset === 0} onClick={() => setFlowOffset((value) => Math.max(0, value - 20))}>上一页流程</button>
      <button className="btn btn-sm" disabled={busy || flowOffset + 20 >= flowTotal} onClick={() => setFlowOffset((value) => value + 20)}>下一页流程</button></div>}
    <details><summary>按流程或任务 ID 查找</summary>
      <form onSubmit={(event) => { event.preventDefault(); void perform(async (signal) => channelDocument(await channelRequest(
        `/api/publish-flows/${encodeURIComponent(flowId)}?${new URLSearchParams({ persona: scope.persona })}`, { signal },
      )), (flow) => setFlows([flow])); }}>
        <label className="field-label" htmlFor="lookup-flow">流程 ID</label><input id="lookup-flow" className="field" value={flowId} onChange={(event) => setFlowId(event.target.value)} />
        <button className="btn btn-sm" disabled={busy || !flowId.trim()}>查找流程</button>
      </form>
      <form onSubmit={(event) => { event.preventDefault(); openTask(taskId); }}><label className="field-label" htmlFor="lookup-task">任务 ID</label>
        <input id="lookup-task" className="field" value={taskId} onChange={(event) => setTaskId(event.target.value)} /><button className="btn btn-sm" disabled={busy || !taskId.trim()}>查找任务</button></form>
    </details>
    {task && <ChannelTaskControl key={JSON.stringify([task.id, task.state, task.publish_at])} persona={scope.persona} task={task}
      onChanged={(value) => { setTask(value); setReload((previous) => previous + 1); }} />}
    <h2 className="panel-title" style={{ marginTop: 'var(--space-5, 20px)' }}>发布记录</h2>
    {summary && <details><summary>当前画像每日发布统计，共 {channelText(summary.total)} 次</summary><ul style={{ paddingLeft: 'var(--space-4, 16px)' }}>
      {channelRows(summary.daily).map((day) => <li key={channelText(day.date)}>{channelText(day.date)} · {channelText(day.count)} 次</li>)}
    </ul></details>}
    <label className="field-label" htmlFor="history-state">状态</label><select id="history-state" className="field" value={status} onChange={(event) => { setStatus(event.target.value); setOffset(0); }}>
      {[['', '全部状态'], ['queued,running,submitted', '排队与处理中'], ['published', '已发布'], ['failed', '失败'], ['unknown', '待核验'], ['cancelled', '已取消']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
    <p className="pv-hint">共 {total} 条记录</p>
    {records.map((item) => <article key={channelText(item.run_id)} style={{ paddingBlock: 'var(--space-3, 12px)', borderBottom: '1px solid var(--border)', overflowWrap: 'anywhere' }}>
      <strong>{channelText(item.title) || channelText(item.run_id)}</strong><p>{channelText(item.state)}</p>
      {channelUrl(item.permalink) && <p><a href={channelUrl(item.permalink)} target="_blank" rel="noreferrer">查看作品</a></p>}
      <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelRequest(
        `/api/publish-records/${encodeURIComponent(channelText(item.run_id))}?${new URLSearchParams({ persona: scope.persona })}`, { signal },
      )), (value) => { setRecord(value); setTask(null); })}>查看及核验记录</button>
    </article>)}
    {!busy && !records.length && <p className="pv-hint">当前筛选条件下暂无发布记录。</p>}
    <div className="subnav-tabs"><button className="btn btn-sm" disabled={busy || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - 30))}>上一页</button>
      <button className="btn btn-sm" disabled={busy || offset + 30 >= total} onClick={() => setOffset((value) => value + 30)}>下一页</button></div>
    {record && <ChannelRecordDetail key={channelText(record.run_id)} persona={scope.persona} record={record} onChanged={(value) => { setRecord(value); setReload((previous) => previous + 1); }} />}
    {error && <p role="alert">{error}</p>}
  </section>;
}
