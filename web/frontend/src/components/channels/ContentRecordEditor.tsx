import { useState } from 'react';
import { channelJson, channelRows, channelText, channelWrite } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { OwnedMediaPicker } from './OwnedMediaPicker';

export function ContentRecordEditor({ persona, domain, groupId, record, mediaRecords, onSaved }: {
  readonly persona: string; readonly domain: string; readonly groupId: string; readonly record: ChannelDocument | null;
  readonly mediaRecords: readonly ChannelDocument[]; readonly onSaved: () => void;
}) {
  const [title, setTitle] = useState(channelText(record?.title));
  const [description, setDescription] = useState(channelText(record?.description));
  const [kind, setKind] = useState(channelText(record?.kind) || (domain === 'material' ? 'article' : 'image'));
  const [path, setPath] = useState(channelText(record?.path));
  const [topics, setTopics] = useState(Array.isArray(record?.topics) ? record.topics.join(', ') : '');
  const [platforms, setPlatforms] = useState(Array.isArray(record?.account_types) ? record.account_types.join(', ') : '');
  const [maximum, setMaximum] = useState(channelText(record?.max_use_count));
  const [options, setOptions] = useState(JSON.stringify(record?.options || {}, null, 2));
  const [chosen, setChosen] = useState<readonly string[]>(channelRows(record?.media).map((item) => channelText(item.media_id)).filter(Boolean));
  const [autoDelete, setAutoDelete] = useState(record?.auto_delete_media === true);
  const [status, setStatus] = useState(channelText(record?.status) || 'success');
  const { busy, error, perform } = useChannelJob();
  const split = (text: string) => text.split(',').map((value) => value.trim()).filter(Boolean);
  return <form onSubmit={(event) => {
    event.preventDefault();
    void perform(async (signal) => {
      const preserved = channelRows(record?.media).filter((item) => !item.media_id || !mediaRecords.some((available) => available.id === item.media_id));
      const media = [...preserved, ...mediaRecords.filter((item) => chosen.includes(channelText(item.id))).map((item) => ({
        media_id: channelText(item.id), path: channelText(item.path), kind: channelText(item.kind),
        thumbnail_path: channelText(item.thumbnail_path),
      }))];
      const payload = { group_id: groupId, title, description, kind, path: domain === 'media' ? path : '',
        media, topics: split(topics), account_types: split(platforms), options: channelJson(options),
        max_use_count: maximum ? Number(maximum) : null, auto_delete_media: autoDelete,
        thumbnail_path: channelText(record?.thumbnail_path), material_id: channelText(record?.material_id),
        status };
      const suffix = record ? `/records/${encodeURIComponent(channelText(record.id))}` : '/records';
      return channelWrite(`/api/content/${domain}${suffix}?${new URLSearchParams({ persona })}`, payload, record ? 'PUT' : 'POST', signal);
    }, onSaved);
  }}>
    <h3 className="panel-title">{record ? '编辑记录' : domain === 'material' ? '新建草稿' : '登记已有媒体'}</h3>
    <label className="field-label" htmlFor="content-title">标题</label><input id="content-title" className="field" value={title} onChange={(event) => setTitle(event.target.value)} required={domain === 'material'} />
    <label className="field-label" htmlFor="content-description">正文或说明</label><textarea id="content-description" className="field" value={description} onChange={(event) => setDescription(event.target.value)} />
    <label className="field-label" htmlFor="content-kind">类型</label><select id="content-kind" className="field" value={kind} onChange={(event) => { setKind(event.target.value); if (domain === 'media') setPath(''); }}>
      {(domain === 'material' ? [['article', '图文'], ['video', '视频'], ['text', '文本']] : [['image', '图片'], ['video', '视频'], ['audio', '音频']])
        .map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
    {domain === 'media' ? <><label className="field-label">内容库媒体</label><OwnedMediaPicker persona={persona} kind={kind} value={path} onChange={setPath} disabled={busy} /></>
      : <fieldset style={{ border: 0, padding: 0 }}><legend className="field-label">关联媒体</legend>
        {!mediaRecords.length && <p className="pv-hint">先到媒体分组上传或登记文件，再关联到草稿。</p>}
        {mediaRecords.map((item) => <label key={channelText(item.id)} className="field-label"><input type="checkbox" checked={chosen.includes(channelText(item.id))}
          onChange={(event) => setChosen((old) => event.target.checked ? [...old, channelText(item.id)] : old.filter((id) => id !== item.id))} />
          {channelText(item.title || item.path)}</label>)}</fieldset>}
    <label className="field-label" htmlFor="content-topics">话题，用英文逗号分隔</label><input id="content-topics" className="field" value={topics} onChange={(event) => setTopics(event.target.value)} />
    {domain === 'material' && <>
      <label className="field-label" htmlFor="content-status">素材状态</label><select id="content-status" className="field" value={status} onChange={(event) => setStatus(event.target.value)}>
        <option value="success">可用</option><option value="draft">待完善</option><option value="error">异常</option></select>
      <label className="field-label" htmlFor="content-platforms">适用平台 ID，用英文逗号分隔，留空适用全部</label><input id="content-platforms" className="field" value={platforms} onChange={(event) => setPlatforms(event.target.value)} />
      <label className="field-label" htmlFor="content-max">最大使用次数，可选</label><input id="content-max" type="number" min="1" className="field" value={maximum} onChange={(event) => setMaximum(event.target.value)} />
      <label className="field-label"><input type="checkbox" checked={autoDelete} onChange={(event) => setAutoDelete(event.target.checked)} /> 删除此草稿时同步移除关联媒体记录</label>
    </>}
    <details><summary>其他内容选项</summary><label className="field-label" htmlFor="content-options">选项 JSON</label>
      <textarea id="content-options" className="field" value={options} onChange={(event) => setOptions(event.target.value)} /></details>
    <button className="btn btn-primary" disabled={busy || !groupId || (domain === 'media' && !path)} type="submit">{busy ? '保存中…' : '保存记录'}</button>
    <button className="btn" type="button" disabled={busy} onClick={onSaved}>关闭编辑</button>
    {error && <p role="alert">{error}</p>}
  </form>;
}
