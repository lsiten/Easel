import { useEffect, useRef, useState } from 'react';
import { channelDocument, channelRequest, channelRows, channelText, channelWrite, CHANNEL_NAMES } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { ContentCollection } from './ContentCollection';
import { IconClose } from '../icons';

export function ChannelContent({ persona }: { readonly persona: string }) {
  const [domain, setDomain] = useState('material');
  const domains = [['material', '草稿素材'], ['media', '媒体文件']] as const;
  return <section className="panel channel-content">
    <div className="subnav-tabs" role="tablist" aria-label="内容类型">
      {domains.map(([value, label], index) => <button key={value} type="button" role="tab"
        className={`subnav-tab ${domain === value ? 'active' : ''}`} aria-selected={domain === value}
        tabIndex={domain === value ? 0 : -1}
        onClick={() => setDomain(value)}
        onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? domains.length - 1
            : (index + (event.key === 'ArrowRight' ? 1 : -1) + domains.length) % domains.length;
          setDomain(domains[next][0]);
          event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
        }}>{label}</button>)}
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
  const editorOpener = useRef<HTMLElement | null>(null);
  const { busy, error, perform } = useChannelJob();
  const prefix = `/api/content/${domain}/groups`;
  const query = new URLSearchParams({ persona, page_size: '200' }).toString();
  useEffect(() => {
    void perform(async (signal) => channelRows(await channelRequest(`${prefix}?${query}`, { signal })), (items) => {
      setGroups(items); setGroupId((previous) => items.some((item) => item.id === previous) ? previous : channelText(items[0]?.id));
    });
  }, [prefix, query, reload, perform]);
  const selected = groups.find((group) => group.id === groupId);
  function closeEditor() {
    setEditing(false);
    window.setTimeout(() => editorOpener.current?.focus({ preventScroll: true }), 0);
  }
  function openEditor(mode: 'new' | 'edit', trigger: HTMLElement) {
    editorOpener.current = trigger;
    setDeleting(false);
    if (mode === 'new') {
      setEditId('');
      setName('');
      setDescription('');
      setPlatforms([]);
      setKind('');
    } else {
      setEditId(groupId);
      setName(channelText(selected?.name));
      setDescription(channelText(selected?.description));
      setPlatforms(Array.isArray(selected?.platforms) ? selected.platforms.filter((item): item is string => typeof item === 'string') : []);
      setKind(channelText(selected?.kind));
    }
    setEditing(true);
  }
  return <div className="channel-content-groups">
    <div className="channel-group-picker">
      <label className="field-label" htmlFor="content-group">当前分组</label>
      <select id="content-group" className="field" value={groupId} disabled={busy}
        onChange={(event) => { setGroupId(event.target.value); setEditing(false); setDeleting(false); }}>
        {groups.map((group) => <option key={channelText(group.id)} value={channelText(group.id)}>{channelText(group.name)}{group.is_default ? '（默认）' : ''}</option>)}
      </select>
      {selected && <p className="pv-hint channel-group-description">{channelText(selected.description) || '当前分组没有补充说明。'}</p>}
    </div>
    <div className="channel-toolbar">
      <div className="channel-toolbar-actions" role="group" aria-label="分组操作">
        <button type="button" className="btn btn-sm" disabled={busy}
          aria-expanded={editing && !editId}
          onClick={(event) => openEditor('new', event.currentTarget)}>新建分组</button>
        <button type="button" className="btn btn-sm" disabled={busy || !selected}
          aria-expanded={editing && Boolean(editId)}
          onClick={(event) => openEditor('edit', event.currentTarget)}>编辑分组</button>
        <button type="button" className="btn btn-sm" disabled={busy}
          onClick={() => setReload((value) => value + 1)}>刷新分组</button>
        <button type="button" className="btn btn-sm btn-danger" disabled={busy || !selected || selected.is_default === true}
          onClick={() => setDeleting((value) => !value)}>删除分组</button>
      </div>
    </div>
    {deleting && <div className="channel-delete-confirm" role="alert">
      <p>确认删除当前分组？其中的内容会移至默认分组。</p>
      <div className="channel-action-row">
        <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setDeleting(false)}>取消</button>
        <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => void perform(
          (signal) => channelRequest(`${prefix}/${encodeURIComponent(groupId)}?${query}`, { method: 'DELETE', signal }),
          () => { setDeleting(false); setReload((value) => value + 1); },
        )}>确认删除分组</button>
      </div>
    </div>}
    {editing && <GroupEditor key={JSON.stringify([name, description, kind, platforms])} persona={persona} domain={domain}
      initial={{ name, description, kind, platforms }} groupId={editId}
      onCancel={closeEditor}
      onSaved={() => { setReload((value) => value + 1); closeEditor(); }} />}
    {error && <p role="alert">{error}</p>}
    {groupId && <ContentCollection key={JSON.stringify([persona, domain, groupId])} persona={persona} domain={domain} groupId={groupId} groups={groups} />}
  </div>;
}

function GroupEditor({ persona, domain, initial, groupId, onSaved, onCancel }: {
  readonly persona: string; readonly domain: string; readonly groupId: string; readonly onSaved: () => void; readonly onCancel: () => void;
  readonly initial: { readonly name: string; readonly description: string; readonly kind: string; readonly platforms: readonly string[] };
}) {
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [kind, setKind] = useState(initial.kind);
  const [platforms, setPlatforms] = useState(initial.platforms);
  const nameInput = useRef<HTMLInputElement>(null);
  const { busy, error, perform } = useChannelJob();
  useEffect(() => { nameInput.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || busy) return;
      event.preventDefault();
      onCancel();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [busy, onCancel]);
  return <section className="channel-editor-panel" aria-labelledby="group-editor-title">
    <header className="channel-editor-head">
      <div>
        <h2 id="group-editor-title">{groupId ? '编辑分组' : '新建分组'}</h2>
        <p>{groupId ? '修改分组名称、说明和适用平台。' : '为一组相关草稿或媒体建立独立分组。'}</p>
      </div>
      <button type="button" className="channel-editor-close" aria-label="关闭分组编辑" onClick={onCancel}>
        <IconClose size={16} />
      </button>
    </header>
    <form className="channel-editor-form" onSubmit={(event) => { event.preventDefault(); void perform(async (signal) => channelDocument(await channelWrite(
      `/api/content/${domain}/groups${groupId ? `/${encodeURIComponent(groupId)}` : ''}?${new URLSearchParams({ persona })}`,
      { name, description, kind: kind || null, platforms }, groupId ? 'PUT' : 'POST', signal,
    )), onSaved); }}>
      <div className="channel-form-grid">
        <label className="channel-form-field">
          <span className="field-label">名称</span>
          <input ref={nameInput} id="group-name" className="field" value={name} required maxLength={200} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="channel-form-field">
          <span className="field-label">内容类型</span>
          <select id="group-kind" className="field" value={kind} disabled={!!groupId} onChange={(event) => setKind(event.target.value)}>
            <option value="">全部类型</option>{[['image', '图片'], ['video', '视频'], ['audio', '音频'], ['article', '图文'], ['text', '文本']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          {groupId && <span className="ai-field-hint">内容类型创建后不可修改。</span>}
        </label>
        <label className="channel-form-field channel-form-field-wide">
          <span className="field-label">说明</span>
          <textarea id="group-description" className="field" value={description} onChange={(event) => setDescription(event.target.value)}
            placeholder="补充这个分组的用途或内容范围。" />
        </label>
      </div>
      <details className="channel-details-panel">
        <summary>
          <span>适用平台</span>
          <span className="channel-summary-meta">{platforms.length ? `已选择 ${platforms.length} 个` : '全部平台'}</span>
        </summary>
        <div className="channel-details-body">
          <fieldset className="channel-platform-fieldset channel-platform-fieldset-nested">
            <legend>选择适用平台</legend>
            <p className="channel-platform-help">不选择时对全部平台生效。</p>
            <div className="channel-platform-grid">
              {Object.entries(CHANNEL_NAMES).map(([id, label]) => <label key={id} className="channel-platform-option">
                <input type="checkbox" checked={platforms.includes(id)}
                  onChange={(event) => setPlatforms((old) => event.target.checked ? [...old, id] : old.filter((value) => value !== id))} />
                <span>{label}</span>
              </label>)}
            </div>
          </fieldset>
        </div>
      </details>
      <div className="channel-form-actions">
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>取消</button>
        <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>{busy ? '保存中…' : '保存分组'}</button>
      </div>
      {error && <p role="alert">{error}</p>}
    </form>
  </section>;
}
