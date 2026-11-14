import { useEffect, useState } from 'react';
import { channelDocument, channelRequest, channelRows, channelText, channelWrite, CHANNEL_NAMES } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ContentCollection } from './ContentCollection';

export function ChannelContent({ persona }: { readonly persona: string }) {
  const [domain, setDomain] = useState('material');
  return <section className="panel">
    <div className="subnav-tabs" role="group" aria-label="内容类型">
      {[['material', '草稿素材'], ['media', '媒体文件']].map(([value, label]) => <button key={value}
        className={`subnav-tab ${domain === value ? 'active' : ''}`} aria-pressed={domain === value} onClick={() => setDomain(value)}>{label}</button>)}
    </div>
    <ContentGroups key={JSON.stringify([persona, domain])} persona={persona} domain={domain} />
  </section>;
}

function ContentGroups({ persona, domain }: { readonly persona: string; readonly domain: string }) {
  const [groups, setGroups] = useState<readonly ChannelDocument[]>([]);
  const [groupId, setGroupId] = useState('');
  const [editing, setEditing] = useState(false);
  const [editId, setEditId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [platforms, setPlatforms] = useState<readonly string[]>([]);
  const [kind, setKind] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [reload, setReload] = useState(0);
  const { busy, error, perform } = useChannelJob();
  const prefix = `/api/content/${domain}/groups`;
  const query = new URLSearchParams({ persona, page_size: '200' }).toString();
  useEffect(() => {
    void perform(async (signal) => channelRows(await channelRequest(`${prefix}?${query}`, { signal })), (items) => {
      setGroups(items); setGroupId((previous) => items.some((item) => item.id === previous) ? previous : channelText(items[0]?.id));
    });
  }, [prefix, query, reload, perform]);
  const selected = groups.find((group) => group.id === groupId);
  return <>
    <label className="field-label" htmlFor="content-group">分组</label>
    <select id="content-group" className="field" value={groupId} disabled={busy} onChange={(event) => { setGroupId(event.target.value); setEditing(false); setDeleting(false); }}>
      {groups.map((group) => <option key={channelText(group.id)} value={channelText(group.id)}>{channelText(group.name)}{group.is_default ? '（默认）' : ''}</option>)}
    </select>
    {selected && <p className="pv-hint">{channelText(selected.description)}</p>}
    <div className="subnav-tabs" style={{ flexWrap: 'wrap' }}>
      <button className="btn btn-sm" disabled={busy} onClick={() => { setEditId(''); setName(''); setDescription(''); setPlatforms([]); setKind(''); setEditing(true); }}>新建分组</button>
      <button className="btn btn-sm" disabled={busy || !selected} onClick={() => {
        setEditId(groupId); setName(channelText(selected?.name)); setDescription(channelText(selected?.description));
        setPlatforms(Array.isArray(selected?.platforms) ? selected.platforms.filter((item): item is string => typeof item === 'string') : []);
        setKind(channelText(selected?.kind)); setEditing(true);
      }}>编辑分组</button>
      <button className="btn btn-sm" disabled={busy} onClick={() => setReload((value) => value + 1)}>刷新分组</button>
      <button className="btn btn-sm btn-danger" disabled={busy || !selected || selected.is_default === true} onClick={() => setDeleting(!deleting)}>删除分组</button>
    </div>
    {deleting && <div role="alert"><p>确认删除当前分组，其中内容将移至默认分组。</p><button className="btn btn-danger" disabled={busy} onClick={() => void perform(
      (signal) => channelRequest(`${prefix}/${encodeURIComponent(groupId)}?${query}`, { method: 'DELETE', signal }),
      () => { setDeleting(false); setReload((value) => value + 1); },
    )}>确认删除分组</button></div>}
    {editing && <GroupEditor key={JSON.stringify([name, description, kind, platforms])} persona={persona} domain={domain}
      initial={{ name, description, kind, platforms }} groupId={editId}
      onSaved={() => { setEditing(false); setReload((value) => value + 1); }} />}
    {error && <p role="alert">{error}</p>}
    {groupId && <ContentCollection key={JSON.stringify([persona, domain, groupId])} persona={persona} domain={domain} groupId={groupId} groups={groups} />}
  </>;
}

function GroupEditor({ persona, domain, initial, groupId, onSaved }: {
  readonly persona: string; readonly domain: string; readonly groupId: string; readonly onSaved: () => void;
  readonly initial: { readonly name: string; readonly description: string; readonly kind: string; readonly platforms: readonly string[] };
}) {
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [kind, setKind] = useState(initial.kind);
  const [platforms, setPlatforms] = useState(initial.platforms);
  const { busy, error, perform } = useChannelJob();
  return <form onSubmit={(event) => { event.preventDefault(); void perform(async (signal) => channelDocument(await channelWrite(
    `/api/content/${domain}/groups${groupId ? `/${encodeURIComponent(groupId)}` : ''}?${new URLSearchParams({ persona })}`,
    { name, description, kind: kind || null, platforms }, groupId ? 'PUT' : 'POST', signal,
  )), onSaved); }}>
    <h3 className="panel-title">{groupId ? '编辑分组' : '新建分组'}</h3>
    <label className="field-label" htmlFor="group-name">名称</label><input id="group-name" className="field" value={name} required maxLength={200} onChange={(event) => setName(event.target.value)} />
    <label className="field-label" htmlFor="group-description">说明</label><textarea id="group-description" className="field" value={description} onChange={(event) => setDescription(event.target.value)} />
    <label className="field-label" htmlFor="group-kind">内容类型，创建后固定</label><select id="group-kind" className="field" value={kind} disabled={!!groupId} onChange={(event) => setKind(event.target.value)}>
      <option value="">全部类型</option>{[['image', '图片'], ['video', '视频'], ['audio', '音频'], ['article', '图文'], ['text', '文本']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
    <fieldset style={{ border: 0, padding: 0 }}><legend className="field-label">适用平台，留空适用全部</legend>
      {Object.entries(CHANNEL_NAMES).map(([id, label]) => <label key={id} className="field-label"><input type="checkbox" checked={platforms.includes(id)} onChange={(event) => setPlatforms((old) => event.target.checked ? [...old, id] : old.filter((value) => value !== id))} /> {label}</label>)}
    </fieldset>
    <button className="btn btn-primary" disabled={busy || !name.trim()} type="submit">保存分组</button><button type="button" className="btn" disabled={busy} onClick={onSaved}>关闭</button>
    {error && <p role="alert">{error}</p>}
  </form>;
}
