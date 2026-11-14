import { useEffect, useState } from 'react';
import { CHANNEL_NAMES, channelRequest, channelRows, channelText, channelWrite } from '../lib/channelsApi';
import type { ChannelDocument } from '../lib/channelsApi';
import { useChannelJob } from './channels/useChannelJob';

export function ChannelAccountGroups({ persona }: { readonly persona: string }) {
  const [groups, setGroups] = useState<readonly ChannelDocument[]>([]);
  const [selected, setSelected] = useState('');
  const [editId, setEditId] = useState('');
  const [name, setName] = useState('');
  const [rank, setRank] = useState('0');
  const [member, setMember] = useState('');
  const [memberRank, setMemberRank] = useState('0');
  const [reviewed, setReviewed] = useState(false);
  const [reload, setReload] = useState(0);
  const { busy, error, perform } = useChannelJob();
  const base = '/api/channels/account-groups';
  useEffect(() => {
    void perform(async (signal) => channelRows(await channelRequest(`${base}?${new URLSearchParams({ persona })}`, { signal })), (items) => {
      setGroups(items); setSelected((previous) => items.some((item) => item.id === previous) ? previous : channelText(items[0]?.id));
    });
  }, [persona, reload, perform]);
  const group = groups.find((item) => item.id === selected);
  const members = groups.flatMap((item) => channelRows(item.accounts));
  const current = channelRows(group?.accounts);
  function refresh() { setReload((value) => value + 1); setReviewed(false); }
  return <section className="panel" aria-label="平台账号分组">
    <h2 className="panel-title">平台账号分组</h2>
    <label className="field-label" htmlFor="account-group">分组</label><select id="account-group" className="field" disabled={busy} value={selected} onChange={(event) => { setSelected(event.target.value); setReviewed(false); }}>
      {groups.map((item) => <option key={channelText(item.id)} value={channelText(item.id)}>{channelText(item.name)}</option>)}
    </select>
    <div className="subnav-tabs" style={{ flexWrap: 'wrap' }}><button className="btn btn-sm" disabled={busy} onClick={() => { setEditId(''); setName(''); setRank('0'); }}>新建分组</button>
      <button className="btn btn-sm" disabled={busy || !group} onClick={() => { setEditId(selected); setName(channelText(group?.name)); setRank(channelText(group?.rank) || '0'); }}>编辑当前分组</button>
      <button className="btn btn-sm" disabled={busy} onClick={refresh}>刷新分组</button></div>
    <form onSubmit={(event) => { event.preventDefault(); void perform(
      (signal) => channelWrite(`${base}${editId ? `/${encodeURIComponent(editId)}` : ''}`, { persona, data: { name, rank: Number(rank) } }, editId ? 'PATCH' : 'POST', signal),
      () => { setName(''); setEditId(''); refresh(); },
    ); }}>
      <label className="field-label" htmlFor="account-group-name">{editId ? '分组新名称' : '新分组名称'}</label><input id="account-group-name" className="field" value={name} required maxLength={200} disabled={busy} onChange={(event) => setName(event.target.value)} />
      <label className="field-label" htmlFor="account-group-rank">分组排序值，较小值在前</label><input id="account-group-rank" className="field" type="number" step="any" value={rank} required disabled={busy} onChange={(event) => setRank(event.target.value)} />
      <button className="btn btn-sm" disabled={busy || !name.trim() || !Number.isFinite(Number(rank))}>{editId ? '保存分组' : '创建分组'}</button>
    </form>
    <ul style={{ paddingLeft: 'var(--space-4, 16px)' }}>{current.map((item) => <li key={JSON.stringify([item.platform, item.account_uid])} style={{ overflowWrap: 'anywhere', paddingBlock: 'var(--space-2, 8px)' }}>
      {CHANNEL_NAMES[channelText(item.platform)] || channelText(item.platform)} · {channelText(item.account_uid)} · 排序 {channelText(item.rank)}
      <button className="btn btn-sm" disabled={busy} onClick={() => { setMember(JSON.stringify([item.platform, item.account_uid])); setMemberRank(channelText(item.rank)); }}>调整此账号</button>
    </li>)}</ul>
    <label className="field-label" htmlFor="account-group-member">账号</label><select id="account-group-member" className="field" disabled={busy} value={member} onChange={(event) => setMember(event.target.value)}>
      <option value="">选择已连接账号</option>{members.map((item) => <option key={JSON.stringify([item.platform, item.account_uid])} value={JSON.stringify([item.platform, item.account_uid])}>{CHANNEL_NAMES[channelText(item.platform)]} · {channelText(item.account_uid)}</option>)}
    </select>
    <button className="btn btn-sm" disabled={busy || !member || !selected} onClick={() => void perform(async (signal) => {
      const value: unknown = JSON.parse(member);
      if (!Array.isArray(value) || value.length !== 2 || value.some((part) => typeof part !== 'string')) throw new Error('账号选择无效');
      return channelWrite(`${base}/${encodeURIComponent(selected)}/accounts`, { persona, platform: value[0], account_uid: value[1] }, 'PATCH', signal);
    }, refresh)}>移入当前分组</button>
    <label className="field-label" htmlFor="account-member-rank">账号排序值，较小值在前</label><input id="account-member-rank" className="field" type="number" step="any" value={memberRank} disabled={busy} onChange={(event) => setMemberRank(event.target.value)} />
    <button className="btn btn-sm" disabled={busy || !member || !Number.isFinite(Number(memberRank))} onClick={() => void perform(async (signal) => {
      const item = current.find((account) => JSON.stringify([account.platform, account.account_uid]) === member);
      if (!item) throw new Error('请先将所选账号移入当前分组');
      return channelWrite(`${base}/${encodeURIComponent(selected)}/accounts/rank`, { persona, items: [{ platform: item.platform, account_uid: item.account_uid, rank: Number(memberRank) }] }, 'PATCH', signal);
    }, refresh)}>保存账号排序</button>
    {group && group.is_default !== true && <details><summary>删除当前分组</summary><label className="field-label"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> 确认删除分组，账号会移至默认分组</label>
      <button className="btn btn-danger" disabled={busy || !reviewed} onClick={() => void perform((signal) => channelWrite(base, { persona, ids: [selected] }, 'DELETE', signal), refresh)}>确认删除账号分组</button></details>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
