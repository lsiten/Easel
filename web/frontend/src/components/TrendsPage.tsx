import { useState, useEffect, useCallback } from 'react';
import { fetchTrends, createIdea } from '../lib/api';
import type { TrendGroup, TrendPersonalization } from '../lib/api';
import { IconFire, IconRefresh, IconBookmark, IconCheck } from './icons';

interface TrendsPageProps {
  persona: string;
  onUseTopic: (title: string) => void;   // 一键做成内容 → 跳 chat
}

const ALL_PLATFORMS: { key: string; label: string }[] = [
  { key: 'weibo', label: '微博' },
  { key: 'douyin', label: '抖音' },
  { key: 'zhihu', label: '知乎' },
  { key: 'bilibili', label: 'B站' },
  { key: 'baidu', label: '百度' },
  { key: 'toutiao', label: '头条' },
];

const EMPTY_PERSONALIZATION: TrendPersonalization = { enabled: false, focusTerms: [], platforms: [] };

export default function TrendsPage({ persona, onUseTopic }: TrendsPageProps) {
  const [selected, setSelected] = useState<string[]>(['weibo', 'douyin', 'zhihu']);
  const [groups, setGroups] = useState<TrendGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [updated, setUpdated] = useState(0);
  const [personalization, setPersonalization] = useState<TrendPersonalization>(EMPTY_PERSONALIZATION);
  const [saved, setSaved] = useState<Set<string>>(new Set());

  const save = async (title: string, source: string) => {
    if (saved.has(title)) return;
    try {
      await createIdea({ title, source: `${source}热搜`, status: 'pending' });
      setSaved((prev) => new Set(prev).add(title));
    } catch { /* ignore */ }
  };

  const load = useCallback((pfs: string[]) => {
    if (pfs.length === 0) { setGroups([]); return; }
    setLoading(true);
    setError('');
    fetchTrends(pfs.join(','), 15, persona)
      .then((d) => { setGroups(d.trends); setUpdated(d.updated); setPersonalization(d.personalization); })
      .catch(() => setError('热点拉取失败——请确认已配置外网代理（EASEL_PROXY）。'))
      .finally(() => setLoading(false));
  }, [persona]);

  useEffect(() => { load(selected); }, [load, selected]);

  const toggle = (k: string) =>
    setSelected((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);

  return (
    <div className="page-scroll trends-page">
      <div className="page-head">
        <div>
          <h1 className="page-title"><IconFire size={22} /> 热点雷达</h1>
          <p className="page-subtitle">
            多平台实时热搜，挑值得蹭的选题，一键交给 AI 做成你的内容。
            {updated > 0 && <span style={{ color: 'var(--text-tertiary)' }}> · {new Date(updated * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 更新</span>}
          </p>
        </div>
        <button className="btn btn-sm" onClick={() => load(selected)} disabled={loading}>
          <IconRefresh size={14} /> {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <div className="trend-platforms">
        {ALL_PLATFORMS.map((p) => (
          <button key={p.key} className={`chip ${selected.includes(p.key) ? 'active' : ''}`}
            onClick={() => toggle(p.key)}>{p.label}</button>
        ))}
      </div>

      <div className={`trend-profile ${personalization.enabled ? 'active' : ''}`}>
        <div>
          <strong>{personalization.enabled ? `按画像「${persona}」排序` : '当前为通用热点排序'}</strong>
          <p>{personalization.enabled
            ? '综合画像兴趣、目标受众、主力平台和红线进行相关性排序。'
            : '选择画像后，会按该账号的内容方向和受众重新排序。'}</p>
        </div>
        {personalization.focusTerms.length > 0 && <div className="trend-profile-terms">
          {personalization.focusTerms.slice(0, 6).map((term) => <span key={term}>{term}</span>)}
        </div>}
      </div>

      {error && <div className="notice-error">{error}</div>}

      <div className="trend-grid">
        {groups.map((g) => {
          const matchedCount = g.items.filter((item) => item.matched).length;
          return <div key={g.platform} className="card trend-col">
            <div className="trend-col-head">
              <span>{g.label}<span className="trend-count">{g.items.length}</span></span>
              {g.personalized && <span className={`trend-match-count ${matchedCount ? 'has-match' : ''}`}>
                {matchedCount ? `${matchedCount} 条画像相关` : '暂无画像命中'}
              </span>}
            </div>
            <div className="trend-list">
              {g.items.length === 0 && !loading && (
                <div className="trend-empty">
                  {g.ok === false ? '该平台暂时取不到（数据源不可用）' : '暂无数据'}
                </div>
              )}
              {g.items.map((it, i) => (
                <div key={i} className="trend-item">
                  <span className={`trend-rank ${i < 3 ? 'top' : ''}`}>{i + 1}</span>
                  <div className="trend-main">
                    <a className="trend-title" href={it.url || undefined} target="_blank" rel="noreferrer"
                      title={it.title}>{it.title}</a>
                    <span className="trend-meta">
                      {it.hot && <span className="trend-hot">{it.hot}</span>}
                      {it.reasons?.map((reason) => <span key={reason} className="trend-reason">{reason}</span>)}
                    </span>
                  </div>
                  <button className="trend-save" title={saved.has(it.title) ? '已收藏到选题库' : '收藏到选题库'}
                    onClick={() => save(it.title, g.label)}>
                    {saved.has(it.title) ? <IconCheck size={14} /> : <IconBookmark size={14} />}
                  </button>
                  <button className="trend-use" title="做成内容"
                    onClick={() => onUseTopic(it.title)}>做内容</button>
                </div>
              ))}
            </div>
          </div>;
        })}
      </div>
    </div>
  );
}
