import { useEffect, useState } from 'react';
import { channelDocument, channelRequest, channelRows, channelText } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function ChannelMaterialPicker({ persona, disabled, onSelect }: {
  readonly persona: string; readonly disabled: boolean; readonly onSelect: (material: ChannelDocument | null) => void;
}) {
  const [records, setRecords] = useState<readonly ChannelDocument[]>([]);
  const [id, setId] = useState('');
  const [reload, setReload] = useState(0);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const { busy, error, perform } = useChannelJob();
  useEffect(() => {
    void perform(async (signal) => channelDocument(await channelRequest(`/api/content/material/records?${new URLSearchParams({ persona, title: filter, page: String(page), page_size: '30', status: 'success' })}`, { signal })),
      (document) => { setRecords(channelRows(document)); setTotal(Number(document.total) || 0); setId(''); });
  }, [persona, filter, page, reload, perform]);
  return <details><summary>使用内容草稿</summary>
    <form onSubmit={(event) => { event.preventDefault(); setFilter(search); setPage(1); }}><label className="field-label" htmlFor="publish-material-search">按标题搜索草稿</label>
      <input id="publish-material-search" className="field" value={search} disabled={disabled || busy} onChange={(event) => setSearch(event.target.value)} /><button className="btn btn-sm" disabled={disabled || busy}>搜索草稿</button></form>
    <label className="field-label" htmlFor="publish-material">当前画像的草稿</label>
    <select id="publish-material" className="field" disabled={disabled || busy} value={id} onChange={(event) => setId(event.target.value)}>
      <option value="">选择草稿</option>{records.map((record) => <option key={channelText(record.id)} value={channelText(record.id)}>{channelText(record.title) || '未命名草稿'}</option>)}
    </select>
    <button className="btn btn-sm" disabled={disabled || busy || !id} onClick={() => void perform(async (signal) => channelDocument(await channelRequest(
      `/api/content/material/records/${encodeURIComponent(id)}?${new URLSearchParams({ persona })}`, { signal },
    )), onSelect)}>载入草稿内容</button>
    <button className="btn btn-sm" disabled={disabled || busy} onClick={() => setReload((value) => value + 1)}>刷新草稿</button>
    <button className="btn btn-sm" disabled={disabled || busy || page === 1} onClick={() => setPage((value) => value - 1)}>上一页草稿</button>
    <button className="btn btn-sm" disabled={disabled || busy || page * 30 >= total} onClick={() => setPage((value) => value + 1)}>下一页草稿</button>
    <button className="btn btn-sm" disabled={disabled || busy} onClick={() => { setId(''); onSelect(null); }}>解除草稿关联</button>
    <p className="pv-hint">已确认发布后，系统自动为关联草稿和选用媒体登记使用次数。</p>
    {error && <p role="alert">{error}</p>}
  </details>;
}
