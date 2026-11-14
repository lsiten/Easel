import { useEffect, useRef, useState } from 'react';
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
  const editorOpener = useRef<HTMLElement | null>(null);
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
  function openEditor(trigger?: HTMLElement) {
    editorOpener.current = trigger ?? null;
    setEditor('new');
  }
  function closeEditor() {
    setEditor(null);
    window.setTimeout(() => editorOpener.current?.focus({ preventScroll: true }), 0);
  }
  const createLabel = domain === 'material' ? '新建草稿' : '登记已有媒体';
  return <section className="channel-collection" aria-labelledby="collection-heading">
    <header className="channel-collection-head">
      <div>
        <h2 id="collection-heading">当前分组内容</h2>
        <p>{domain === 'material' ? '管理可用于发布的草稿文案和关联素材。' : '管理当前分组中的图片、视频和音频文件。'}</p>
      </div>
      <button type="button" className="btn btn-primary" disabled={busy || !groupId} onClick={(event) => openEditor(event.currentTarget)}>{createLabel}</button>
    </header>
    <form className="channel-search-form" onSubmit={(event) => { event.preventDefault(); setFilter(title); setPage(1); }}>
      <label className="channel-search-field" htmlFor="record-filter">
        <span className="field-label">搜索当前分组标题</span>
        <span className="channel-search-control">
          <input id="record-filter" className="field" value={title} placeholder="输入标题关键词"
            onChange={(event) => setTitle(event.target.value)} />
          <button type="submit" className="btn btn-sm" disabled={busy}>搜索</button>
        </span>
      </label>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={refresh}>刷新</button>
    </form>
    {domain === 'media' && <ContentMediaImport persona={persona} groupId={groupId} onSaved={refresh} />}
    {editor && <ContentRecordEditor key={editor === 'new' ? 'new' : channelText(editor.id)} persona={persona} domain={domain} groupId={groupId}
      record={editor === 'new' ? null : editor} mediaRecords={media} onSaved={refresh} onCancel={closeEditor} />}
    <div className="channel-collection-status">
      <span role="status">共 {total} 条，第 {page} 页</span>
      <div className="channel-pagination">
        <button type="button" className="btn btn-sm" disabled={busy || page === 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
        <button type="button" className="btn btn-sm" disabled={busy || page * 30 >= total} onClick={() => setPage((value) => value + 1)}>下一页</button>
      </div>
    </div>
    {!busy && !rows.length && !editor && <div className="channel-empty-state">
      <div>
        <strong>{domain === 'material' ? '当前分组还没有草稿' : '当前分组还没有媒体文件'}</strong>
        <p>{domain === 'material' ? '新建一条草稿，或从其他分组移动已有内容。' : '登记已有媒体，或先上传本地文件。'}</p>
      </div>
      <button type="button" className="btn btn-sm" onClick={(event) => openEditor(event.currentTarget)}>{createLabel}</button>
    </div>}
    {!!rows.length && <div className="channel-record-list">
      {rows.map((record) => <article key={channelText(record.id)} className="channel-record">
        <label className="channel-record-title">
          <input type="checkbox" checked={selected.includes(channelText(record.id))} disabled={busy}
            onChange={(event) => setSelected((old) => event.target.checked ? [...old, channelText(record.id)] : old.filter((id) => id !== record.id))} />
          <strong>{channelText(record.title) || '未命名内容'}</strong>
        </label>
        <p className="pv-hint">{channelText(record.kind)} · 已使用 {channelText(record.use_count)} 次{record.max_use_count ? ` / ${channelText(record.max_use_count)}` : ''} · {channelText(record.status)}</p>
        {!!record.description && <p>{channelText(record.description).slice(0, 500)}</p>}
        {!!record.path && <p className="pv-hint">{channelText(record.path)}</p>}
        <button type="button" className="btn btn-sm channel-record-actions" disabled={busy}
          onClick={(event) => {
            const opener = event.currentTarget;
            void perform(async (signal) => channelDocument(await channelRequest(
              `${base}/records/${encodeURIComponent(channelText(record.id))}?${new URLSearchParams({ persona })}`, { signal },
            )), (value) => { editorOpener.current = opener; setEditor(value); });
          }}>编辑</button>
      </article>)}
    </div>}
    {selected.length > 0 && <section className="channel-bulk-actions" aria-label="批量操作">
      <div className="channel-details-body">
        <label className="channel-form-field" htmlFor="transfer-group">
          <span className="field-label">已选 {selected.length} 条，转移到分组</span>
          <select id="transfer-group" className="field" value={target} onChange={(event) => setTarget(event.target.value)}>
            <option value="">选择目标分组</option>{groups.filter((group) => group.id !== groupId).map((group) => <option key={channelText(group.id)} value={channelText(group.id)}>{channelText(group.name)}</option>)}
          </select>
        </label>
        <div className="channel-action-row">
          {[['move', '移动'], ['copy', '复制']].map(([mode, label]) => <button key={mode} type="button" className="btn btn-sm" disabled={busy || !target} onClick={() => void perform(
            (signal) => channelWrite(`${base}/transfer?${new URLSearchParams({ persona })}`, { ids: selected, target_group_id: target, mode }, 'POST', signal), refresh,
          )}>{label}选中内容</button>)}
          <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => setDeleting((value) => !value)}>删除选中内容</button>
        </div>
        {deleting && <div className="channel-delete-confirm" role="alert">
          <p>确认删除选中的 {selected.length} 条记录？关联媒体的删除设置会同时生效。</p>
          <div className="channel-action-row">
            <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setDeleting(false)}>取消</button>
            <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => void perform(
              (signal) => channelWrite(`${base}/records/bulk?${new URLSearchParams({ persona })}`, { ids: selected }, 'DELETE', signal), refresh,
            )}>确认删除内容</button>
          </div>
        </div>}
      </div>
      <details className="channel-details-panel">
        <summary>登记已完成的使用记录</summary>
        <div className="channel-details-body">
          <label className="channel-form-field" htmlFor="usage-run">
            <span className="field-label">已完成发布的任务 ID</span>
            <input id="usage-run" className="field" value={usageRun} onChange={(event) => setUsageRun(event.target.value)} />
            <span className="ai-field-hint">相同 ID 会自动去重。</span>
          </label>
          <div className="channel-action-row">
            <button type="button" className="btn btn-sm" disabled={busy || !usageRun.trim()} onClick={() => void perform(
              (signal) => channelWrite(`${base}/usage?${new URLSearchParams({ persona })}`, { ids: selected, run_id: usageRun }, 'POST', signal),
              () => { setNotice('使用记录已登记'); refresh(); },
            )}>登记一次使用</button>
          </div>
        </div>
      </details>
    </section>}
    {domain === 'material' && <details className="channel-details-panel">
      <summary>按平台选择可用素材</summary>
      <div className="channel-details-body">
        <label className="channel-form-field" htmlFor="optimal-platform">
          <span className="field-label">发布平台</span>
          <select id="optimal-platform" className="field" value={optimalPlatform} onChange={(event) => setOptimalPlatform(event.target.value)}>
            {Object.entries(CHANNEL_NAMES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        <div className="channel-action-row">
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void perform(async (signal) => {
            const result = await channelRequest(`${base}/optimal?${new URLSearchParams({ persona, group_id: groupId, platform: optimalPlatform })}`, { signal });
            return result === null ? null : channelDocument(result);
          }, (result) => { setEditor(result); setNotice(result ? '已按使用次数选择可用素材' : '暂无满足平台及使用次数限制的素材'); })}>按使用次数选择素材</button>
        </div>
      </div>
    </details>}
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
