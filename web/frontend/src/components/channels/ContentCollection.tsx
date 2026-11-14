import { useEffect, useState } from 'react';
import { channelDocument, channelRequest, channelRows, channelText, channelWrite, CHANNEL_NAMES } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ContentRecordEditor } from './ContentRecordEditor';
import { ContentMediaImport } from './ContentMediaImport';

export function ContentCollection({ persona, domain, groupId, groups }: {
  readonly persona: string; readonly domain: string; readonly groupId: string; readonly groups: readonly ChannelDocument[];
}) {
  const [rows, setRows] = useState<readonly ChannelDocument[]>([]);
  const [media, setMedia] = useState<readonly ChannelDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState('');
  const [title, setTitle] = useState('');
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [target, setTarget] = useState('');
  const [editor, setEditor] = useState<ChannelDocument | 'new' | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [reload, setReload] = useState(0);
  const [optimalPlatform, setOptimalPlatform] = useState('youtube');
  const [usageRun, setUsageRun] = useState('');
  const [notice, setNotice] = useState('');
  const { busy, error, perform } = useChannelJob();
  const query = new URLSearchParams({ persona, group_id: groupId, title: filter, page: String(page), page_size: '30' }).toString();
  const base = `/api/content/${domain}`;
  useEffect(() => {
    void perform(async (signal) => {
      const document = channelDocument(await channelRequest(`${base}/records?${query}`, { signal }));
      const mediaRows = domain === 'material' ? channelRows(await channelRequest(`/api/content/media/records?${new URLSearchParams({ persona, page_size: '200' })}`, { signal })) : [];
      return { document, mediaRows };
    }, ({ document, mediaRows }) => { setRows(channelRows(document)); setTotal(Number(document.total) || 0); setSelected([]); setMedia(mediaRows); });
  }, [persona, domain, query, base, reload, perform]);
  function refresh() { setEditor(null); setSelected([]); setDeleting(false); setReload((value) => value + 1); }
  return <div style={{ marginTop: 'var(--space-5, 20px)' }}>
    <form onSubmit={(event) => { event.preventDefault(); setFilter(title); setPage(1); }}>
      <label className="field-label" htmlFor="record-filter">搜索当前分组标题</label><input id="record-filter" className="field" value={title} onChange={(event) => setTitle(event.target.value)} />
      <button className="btn btn-sm" disabled={busy}>搜索</button><button type="button" className="btn btn-sm" disabled={busy} onClick={refresh}>刷新</button>
      <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => setEditor('new')}>{domain === 'material' ? '新建草稿' : '登记已有媒体'}</button>
    </form>
    {domain === 'media' && <ContentMediaImport persona={persona} groupId={groupId} onSaved={refresh} />}
    {editor && <ContentRecordEditor key={editor === 'new' ? 'new' : channelText(editor.id)} persona={persona} domain={domain} groupId={groupId}
      record={editor === 'new' ? null : editor} mediaRecords={media} onSaved={refresh} />}
    <p className="pv-hint" role="status">共 {total} 条，第 {page} 页</p>
    {!busy && !rows.length && <p>当前分组暂无内容。</p>}
    {rows.map((record) => <article key={channelText(record.id)} style={{ paddingBlock: 'var(--space-3, 12px)', borderBottom: '1px solid var(--border)' }}>
      <label className="field-label"><input type="checkbox" checked={selected.includes(channelText(record.id))} disabled={busy}
        onChange={(event) => setSelected((old) => event.target.checked ? [...old, channelText(record.id)] : old.filter((id) => id !== record.id))} />
        <strong>{channelText(record.title) || '未命名内容'}</strong></label>
      <p className="pv-hint">{channelText(record.kind)} · 已使用 {channelText(record.use_count)} 次{record.max_use_count ? ` / ${channelText(record.max_use_count)}` : ''} · {channelText(record.status)}</p>
      {!!record.description && <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{channelText(record.description).slice(0, 500)}</p>}
      {!!record.path && <p className="pv-hint" style={{ overflowWrap: 'anywhere' }}>{channelText(record.path)}</p>}
      <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => channelDocument(await channelRequest(
        `${base}/records/${encodeURIComponent(channelText(record.id))}?${new URLSearchParams({ persona })}`, { signal },
      )), setEditor)}>查看并编辑</button>
    </article>)}
    <div className="subnav-tabs" style={{ flexWrap: 'wrap', marginBlock: 'var(--space-3, 12px)' }}>
      <button className="btn btn-sm" disabled={busy || page === 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
      <button className="btn btn-sm" disabled={busy || page * 30 >= total} onClick={() => setPage((value) => value + 1)}>下一页</button>
    </div>
    {selected.length > 0 && <>
      <label className="field-label" htmlFor="transfer-group">已选 {selected.length} 条，转移到分组</label><select id="transfer-group" className="field" value={target} onChange={(event) => setTarget(event.target.value)}>
        <option value="">选择目标分组</option>{groups.filter((group) => group.id !== groupId).map((group) => <option key={channelText(group.id)} value={channelText(group.id)}>{channelText(group.name)}</option>)}
      </select>
      <div className="subnav-tabs" style={{ flexWrap: 'wrap' }}>{[['move', '移动'], ['copy', '复制']].map(([mode, label]) => <button key={mode} className="btn btn-sm" disabled={busy || !target} onClick={() => void perform(
        (signal) => channelWrite(`${base}/transfer?${new URLSearchParams({ persona })}`, { ids: selected, target_group_id: target, mode }, 'POST', signal), refresh,
      )}>{label}选中内容</button>)}
        <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => setDeleting(!deleting)}>删除选中内容</button>
      </div>
      {deleting && <div role="alert"><p>确认删除选中的 {selected.length} 条记录。草稿的关联媒体删除设置会同时生效。</p>
        <button className="btn btn-danger" disabled={busy} onClick={() => void perform(
          (signal) => channelWrite(`${base}/records/bulk?${new URLSearchParams({ persona })}`, { ids: selected }, 'DELETE', signal), refresh,
        )}>确认删除内容</button></div>}
      <details><summary>登记已完成的使用记录</summary><label className="field-label" htmlFor="usage-run">已完成发布的任务 ID，相同 ID 自动去重</label>
        <input id="usage-run" className="field" value={usageRun} onChange={(event) => setUsageRun(event.target.value)} />
        <button className="btn btn-sm" disabled={busy || !usageRun.trim()} onClick={() => void perform(
          (signal) => channelWrite(`${base}/usage?${new URLSearchParams({ persona })}`, { ids: selected, run_id: usageRun }, 'POST', signal),
          () => { setNotice('使用记录已登记'); refresh(); },
        )}>登记一次使用</button></details>
    </>}
    {domain === 'material' && <details><summary>选择可用素材</summary><label className="field-label" htmlFor="optimal-platform">发布平台</label>
      <select id="optimal-platform" className="field" value={optimalPlatform} onChange={(event) => setOptimalPlatform(event.target.value)}>
        {Object.entries(CHANNEL_NAMES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
      <button className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => {
        const result = await channelRequest(`${base}/optimal?${new URLSearchParams({ persona, group_id: groupId, platform: optimalPlatform })}`, { signal });
        return result === null ? null : channelDocument(result);
      }, (result) => { setEditor(result); setNotice(result ? '已按使用次数选择可用素材' : '暂无满足平台及使用次数限制的素材'); })}>按使用次数选择素材</button>
    </details>}
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
  </div>;
}
