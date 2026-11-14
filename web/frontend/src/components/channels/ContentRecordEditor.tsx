import { useEffect, useRef, useState } from 'react';
import { channelJson, channelRows, channelText, channelWrite } from '../../lib/channelsApi';
import type { ChannelDocument } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';
import { OwnedMediaPicker } from './OwnedMediaPicker';
import { IconClose } from '../icons';

export function ContentRecordEditor({ persona, domain, groupId, record, mediaRecords, onSaved, onCancel }: {
  readonly persona: string; readonly domain: string; readonly groupId: string; readonly record: ChannelDocument | null;
  readonly mediaRecords: readonly ChannelDocument[]; readonly onSaved: () => void; readonly onCancel: () => void;
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
  const titleInput = useRef<HTMLInputElement>(null);
  const { busy, error, perform } = useChannelJob();
  const split = (text: string) => text.split(',').map((value) => value.trim()).filter(Boolean);
  useEffect(() => { titleInput.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || busy) return;
      event.preventDefault();
      onCancel();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [busy, onCancel]);
  const editorTitle = record ? '编辑内容' : domain === 'material' ? '新建草稿' : '登记已有媒体';
  return <section className="channel-editor-panel" aria-labelledby="record-editor-title">
    <header className="channel-editor-head">
      <div>
        <h2 id="record-editor-title">{editorTitle}</h2>
        <p>{domain === 'material' ? '填写草稿信息，并按需关联媒体文件。' : '登记当前画像里已经生成或上传的媒体文件。'}</p>
      </div>
      <button type="button" className="channel-editor-close" aria-label="关闭内容编辑" disabled={busy} onClick={onCancel}>
        <IconClose size={16} />
      </button>
    </header>
    <form className="channel-editor-form" aria-busy={busy} onSubmit={(event) => {
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
      <div className="channel-form-grid">
        <label className="channel-form-field">
          <span className="field-label">标题</span>
          <input ref={titleInput} id="content-title" className="field" value={title} onChange={(event) => setTitle(event.target.value)} required={domain === 'material'} />
        </label>
        <label className="channel-form-field">
          <span className="field-label">类型</span>
          <select id="content-kind" className="field" value={kind} onChange={(event) => { setKind(event.target.value); if (domain === 'media') setPath(''); }}>
            {(domain === 'material' ? [['article', '图文'], ['video', '视频'], ['text', '文本']] : [['image', '图片'], ['video', '视频'], ['audio', '音频']])
              .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="channel-form-field channel-form-field-wide">
          <span className="field-label">正文或说明</span>
          <textarea id="content-description" className="field" value={description} onChange={(event) => setDescription(event.target.value)}
            placeholder={domain === 'material' ? '填写草稿正文、使用说明或内容摘要。' : '补充这个媒体文件的用途或说明。'} />
        </label>
      </div>

      {domain === 'media' ? <div className="channel-form-field">
        <span className="field-label">素材源媒体</span>
        <OwnedMediaPicker persona={persona} kind={kind} value={path} onChange={setPath} disabled={busy} />
      </div> : <details className="channel-details-panel">
        <summary>
          <span>关联媒体</span>
          <span className="channel-summary-meta">{chosen.length ? `已选 ${chosen.length} 个` : `${mediaRecords.length} 个可用`}</span>
        </summary>
        <div className="channel-details-body">
          <fieldset className="channel-platform-fieldset channel-platform-fieldset-nested">
            <legend>选择关联媒体</legend>
            <p className="channel-platform-help">可选择一个或多个媒体文件与这条草稿一起使用。</p>
            {!mediaRecords.length && <p className="pv-hint">媒体分组中没有可用文件，请先上传或登记媒体。</p>}
            <div className="channel-platform-grid">
              {mediaRecords.map((item) => <label key={channelText(item.id)} className="channel-platform-option">
                <input type="checkbox" checked={chosen.includes(channelText(item.id))}
                  onChange={(event) => setChosen((old) => event.target.checked ? [...old, channelText(item.id)] : old.filter((id) => id !== item.id))} />
                <span>{channelText(item.title || item.path)}</span>
              </label>)}
            </div>
          </fieldset>
        </div>
      </details>}

      <div className="channel-form-grid">
        <label className="channel-form-field channel-form-field-wide">
          <span className="field-label">话题</span>
          <input id="content-topics" className="field" value={topics} onChange={(event) => setTopics(event.target.value)} placeholder="用英文逗号分隔" />
        </label>
        {domain === 'material' && <>
          <label className="channel-form-field">
            <span className="field-label">素材状态</span>
            <select id="content-status" className="field" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="success">可用</option><option value="draft">待完善</option><option value="error">异常</option>
            </select>
          </label>
          <label className="channel-form-field">
            <span className="field-label">最大使用次数</span>
            <input id="content-max" type="number" min="1" className="field" value={maximum} onChange={(event) => setMaximum(event.target.value)} placeholder="留空表示不限制" />
          </label>
          <label className="channel-form-field channel-form-field-wide">
            <span className="field-label">适用平台 ID</span>
            <input id="content-platforms" className="field" value={platforms} onChange={(event) => setPlatforms(event.target.value)}
              placeholder="用英文逗号分隔，留空适用全部" />
          </label>
          <label className="channel-platform-option channel-form-field-wide">
            <input type="checkbox" checked={autoDelete} onChange={(event) => setAutoDelete(event.target.checked)} />
            <span>删除此草稿时同步移除关联媒体记录</span>
          </label>
        </>}
      </div>

      <details className="channel-details-panel">
        <summary>其他内容选项</summary>
        <div className="channel-details-body">
          <label className="channel-form-field" htmlFor="content-options">
            <span className="field-label">选项 JSON</span>
            <textarea id="content-options" className="field" value={options} onChange={(event) => setOptions(event.target.value)} />
          </label>
        </div>
      </details>
      <div className="channel-form-actions">
        <button className="btn" type="button" disabled={busy} onClick={onCancel}>取消</button>
        <button className="btn btn-primary" disabled={busy || !groupId || (domain === 'media' && !path)} type="submit">{busy ? '保存中…' : '保存记录'}</button>
      </div>
      {error && <p role="alert">{error}</p>}
    </form>
  </section>;
}
