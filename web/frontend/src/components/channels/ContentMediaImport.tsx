import { useRef, useState } from 'react';
import { channelDocument, channelText, channelWrite, channelRequest } from '../../lib/channelsApi';
import { useChannelJob } from './useChannelJob';

export function ContentMediaImport({ persona, groupId, onSaved }: {
  readonly persona: string; readonly groupId: string; readonly onSaved: () => void;
}) {
  const [url, setUrl] = useState('');
  const [kind, setKind] = useState('image');
  const [title, setTitle] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState('');
  const requestId = useRef(crypto.randomUUID());
  const { busy, error, perform } = useChannelJob();
  return <details><summary>上传或导入媒体</summary>
    <label className="field-label" htmlFor="content-import-title">媒体标题</label><input id="content-import-title" className="field" value={title} onChange={(event) => { setTitle(event.target.value); requestId.current = crypto.randomUUID(); }} />
    <label className="field-label" htmlFor="content-upload-file">上传本地文件</label><input id="content-upload-file" type="file" accept="image/*,video/*,audio/*" className="field" onChange={(event) => setFile(event.target.files?.[0] || null)} />
    <button className="btn" disabled={busy || !file || !groupId} onClick={() => void perform(async (signal) => {
      if (!file) throw new Error('请选择媒体文件');
      const limits = channelDocument(await channelRequest('/api/upload/limits', { signal }));
      const maximum = typeof limits.max_mb === 'number' ? limits.max_mb * 1024 * 1024 : 50 * 1024 * 1024;
      if (file.size > maximum) throw new Error(`文件超过 ${maximum / 1024 / 1024} MB 上传上限`);
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
      const sha256 = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('');
      const grant = channelDocument(await channelWrite('/api/asset-uploads/sessions', { persona, session_id: 'channel-content',
        filename: file.name, size: file.size, sha256, target: 'media' }, 'POST', signal));
      const upload = channelText(grant.upload_url), confirm = channelText(grant.confirm_url);
      if (!upload.startsWith('/api/asset-uploads/') || !confirm.startsWith('/api/asset-uploads/')) throw new Error('上传会话地址无效');
      await channelRequest(upload, { method: 'PUT', body: file, signal });
      const receipt = channelDocument(await channelWrite(confirm, {}, 'POST', signal));
      const mediaKind = file.type.startsWith('video/') ? 'video' : file.type.startsWith('audio/') ? 'audio' : 'image';
      return channelWrite(`/api/content/media/records?${new URLSearchParams({ persona })}`, {
        group_id: groupId, kind: mediaKind, path: channelText(receipt.path), title: title || file.name,
      }, 'POST', signal);
    }, () => { setMessage('媒体已上传并登记到当前分组'); setFile(null); onSaved(); })}>上传并登记媒体</button>
    <label className="field-label" htmlFor="content-import-url">公开媒体链接</label><input id="content-import-url" type="url" className="field" value={url} onChange={(event) => { setUrl(event.target.value); requestId.current = crypto.randomUUID(); }} />
    <label className="field-label" htmlFor="content-import-kind">链接媒体类型</label><select id="content-import-kind" className="field" value={kind} onChange={(event) => { setKind(event.target.value); requestId.current = crypto.randomUUID(); }}>
      <option value="image">图片</option><option value="video">视频</option><option value="audio">音频</option></select>
    <button className="btn" disabled={busy || !url || !groupId} onClick={() => void perform(
      (signal) => channelWrite(`/api/content/media/import-url?${new URLSearchParams({ persona })}`, {
        request_id: requestId.current, group_id: groupId, url, kind, title,
      }, 'POST', signal), () => { setMessage('链接媒体已导入当前画像'); onSaved(); })}>导入链接媒体</button>
    {message && <p role="status">{message}</p>}{busy && <p role="status">正在校验并导入媒体…</p>}{error && <p role="alert">{error}</p>}
  </details>;
}
