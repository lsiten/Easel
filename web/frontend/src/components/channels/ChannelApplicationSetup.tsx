import { useEffect, useState } from 'react';
import { channelDocument, channelRequest, channelWrite } from '../../lib/channelsApi';
import type { ChannelDocument, ChannelPlatform } from '../../lib/channelsApi';
import type { ConfigureApplication } from './PlatformConnectionWorkspace';
import { useChannelJob } from './useChannelJob';

export function ChannelApplicationSetup({ persona, spec, onConfigureApplication, onSaved, optional }: {
  readonly persona: string; readonly spec: ChannelPlatform; readonly optional: boolean;
  readonly onConfigureApplication: ConfigureApplication; readonly onSaved: () => void;
}) {
  const [configuration, setConfiguration] = useState<ChannelDocument | null>(null);
  const [values, setValues] = useState<Readonly<Record<string, string>>>({});
  const [message, setMessage] = useState('');
  const { busy, error, perform } = useChannelJob();
  const endpoint = `/api/channel-applications/${encodeURIComponent(spec.platform)}`;
  useEffect(() => {
    void perform(async (signal) => channelDocument(await channelRequest(`${endpoint}?${new URLSearchParams({ persona })}`, { signal })), setConfiguration);
  }, [endpoint, persona, perform]);
  const fields = Array.isArray(configuration?.fields) ? configuration.fields.filter((value): value is string => typeof value === 'string') : [];
  const present = configuration?.present ? channelDocument(configuration.present) : {};
  const ready = configuration?.connected === true;
  const handoff = spec.account_binding === 'handoff';
  return <details className="panel" open={!optional && !spec.configured && !ready}>
    <summary>应用授权{optional ? '（可选）' : ''}{ready ? ' · 已连接' : spec.configured ? ' · 应用已配置' : ''}</summary>
    <p className="pv-hint">可使用浏览器接入或应用授权。已登录的浏览器账号可以直接使用。</p>
    <button className="btn btn-primary" disabled={busy} onClick={() => void perform(
      (signal) => onConfigureApplication(spec.platform, signal), () => setMessage('已在配置对话中启动 Ego 操作'),
    )}>一键配置应用授权</button>
    <p className="pv-hint">Agent 通过 Ego 操作开发者控制台。需要登录、验证码或平台审核时，会交给你处理后继续。</p>
    <details><summary>手动填写已有应用配置</summary>
      <form onSubmit={(event) => {
        event.preventDefault();
        void perform(async (signal) => channelDocument(await channelWrite(endpoint, { persona, values }, 'POST', signal)), (value) => {
          setConfiguration(value); setValues({}); setMessage(value.connected ? '应用授权已连接' : value.configured ? '应用配置已保存' : '配置已保存，请补齐其余字段'); onSaved();
        });
      }}>
        {fields.map((field) => <div key={field}>
          <label className="field-label" htmlFor={`application-${field}`}>{field}{present[field] === true ? ' · 已保存' : ''}</label>
          <input id={`application-${field}`} className="field" type={field.includes('SECRET') || field.includes('TOKEN') ? 'password' : 'text'}
            autoComplete="off" value={values[field] || ''} disabled={busy} onChange={(event) => setValues((previous) => ({ ...previous, [field]: event.target.value }))} />
        </div>)}
        <button className="btn" disabled={busy || !Object.values(values).some((value) => value.trim())}>保存应用配置</button>
      </form>
      <p className="pv-hint">空字段沿用已保存的配置。密钥以隐藏输入填写，保存结果显示字段状态。</p>
    </details>
    {configuration && <p className="pv-hint">{ready ? '账号授权已连接' : configuration.configured ? handoff ? '分享应用已配置，发布账号在平台 App 中选择' : '应用已配置，可继续平台授权或应用接续' : '应用配置待完成'}</p>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    {busy && <p role="status">正在准备应用配置…</p>}
  </details>;
}
