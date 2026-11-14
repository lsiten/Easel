import { jobState } from '../../lib/aiPresentation';
import type { ReactNode } from 'react';
import { mediaUrl, text, rows, document } from '../../lib/aiWorkspaceApi';
import type { Document } from '../../lib/aiWorkspaceApi';

export function Field({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return <label className="ai-field"><span className="field-label">{label}</span>{children}</label>;
}
export function Feedback({ busy, error }: { readonly busy: boolean; readonly error: string }) {
  return <>{busy && <p role="status">正在处理…</p>}{error && <p role="alert">{error}</p>}</>;
}
export function Pager({ page, total, onPage }: { readonly page: number; readonly total: number; readonly onPage: (page: number) => void }) {
  return <div className="ai-actions"><button className="btn" disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</button>
    <span>第 {page} 页 · {total} 条</span><button className="btn" disabled={page * 20 >= total} onClick={() => onPage(page + 1)}>下一页</button></div>;
}
export function Result({ value }: { readonly value: Document }) {
  const assets = rows(value.assets);
  const response = value.response && typeof value.response === 'object' && !Array.isArray(value.response) ? document(value.response) : {};
  const urls = Array.isArray(response.imageUrls) ? response.imageUrls : [];
  const video = mediaUrl(value.videoUrl) || mediaUrl(value.outputUrl) || mediaUrl(response.videoUrl);
  const single = value.asset && typeof value.asset === 'object' ? rows([value.asset]) : [];
  const attached = [...assets, ...single];
  const separateVideo = video && !attached.some((asset) => mediaUrl(asset.url || asset.path) === video);
  return <div className="ai-result"><p role="status">{jobState(value.state || value.status)} {text(value.error || value.errorMessage)}</p>
    {text(response.title) && <h3>{text(response.title)}</h3>}{text(response.description) && <p className="ai-prose">{text(response.description)}</p>}
    {urls.map((url, index) => <Asset key={index} asset={{ url, kind: 'image' }} />)}{separateVideo && <Asset asset={{ url: video, kind: 'video' }} />}
    {attached.map((asset, index) => <Asset key={text(asset.id) || index} asset={asset} />)}
    <details><summary>任务详情</summary><pre>{JSON.stringify(value, null, 2)}</pre></details></div>;
}
export function Asset({ asset }: { readonly asset: Document }) {
  const url = mediaUrl(asset.url || asset.path);
  const video = text(asset.kind || asset.type || asset.mime_type).includes('video') || /\.(mp4|webm)(\?|$)/i.test(url);
  return url ? <figure>{video ? <video src={url} controls preload="metadata" /> : <img src={url} alt={text(asset.name || asset.filename) || '生成图片'} loading="lazy" />}
    <figcaption><a href={url} target="_blank" rel="noreferrer">打开原文件</a></figcaption></figure> : <p>媒体地址不可用</p>;
}
