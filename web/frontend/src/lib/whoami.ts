// whoami 登录态：缓存 + 开页后台自愈校验。账号页与工作台「创作数据」卡片共用，
// 避免对同一平台在 TTL 内重复起 headless 浏览器。缓存落 localStorage，条目带 ts。
import { accountWhoami, type AccountWhoami } from './api';

const KEY = 'easel_whoami_v4';
export const WHOAMI_TTL_MS = 600_000; // 10 分钟，与后端 WHOAMI_TTL 对齐

/** 缓存/版本号按画像隔离：命名画像各自一份，通用/空画像沿用全局（向后兼容）。 */
function cacheKey(persona?: string): string {
  return persona ? `${KEY}__${encodeURIComponent(persona)}` : KEY;
}
function verKey(platform: string, persona?: string): string {
  return persona ? `${persona}::${platform}` : platform;
}

export type WhoamiEntry = AccountWhoami & { ts: number };
const versions: Record<string, number> = {};

export function getWhoamiCache(persona?: string): Record<string, WhoamiEntry> {
  try { return JSON.parse(localStorage.getItem(cacheKey(persona)) || '{}'); } catch { return {}; }
}

/** 写缓存；r 为 null 则删除该平台条目。附带 ts 供 TTL 判定（旧格式无 ts→视为过期）。按画像隔离。 */
export function setWhoamiCache(platform: string, r: AccountWhoami | null, persona?: string): void {
  versions[verKey(platform, persona)] = (versions[verKey(platform, persona)] || 0) + 1;
  try {
    const c = getWhoamiCache(persona);
    if (r) c[platform] = { ...r, ts: Date.now() };
    else delete c[platform];
    localStorage.setItem(cacheKey(persona), JSON.stringify(c));
  } catch { /* 配额 / 隐私模式：忽略 */ }
}

function isFresh(e: WhoamiEntry | undefined, ttlMs: number): boolean {
  const lifetime = e?.loggedIn && (!e.name || !e.avatar) ? Math.min(ttlMs, 60_000) : ttlMs;
  return !!e && typeof e.ts === 'number' && (Date.now() - e.ts) < lifetime;
}

interface VerifyOpts {
  ttlMs?: number;
  force?: boolean;
  persona?: string;
  onUpdate?: (platform: string, r: AccountWhoami) => void;
  alive?: () => boolean;
}

/**
 * 对缓存缺失/过期的平台在后台真校验（后端起 headless 浏览器，数秒/个），
 * 每得到一个结果就写缓存 + 回调 onUpdate。**并发 2 个**（比逐个串行快近一倍，又不至同时起太多
 * 浏览器打满资源）；每个平台仍独立、失败只跳过该平台，不影响其它，也不改任何登录/检测逻辑。
 * 调用方负责从 platforms 里剔除 bilibili（走 cookie 判定，无 whoami 浏览器路径也可，但更慢）。
 */
const WHOAMI_CONCURRENCY = 2;

export function verifyStale(platforms: string[], opts: VerifyOpts = {}): void {
  const persona = opts.persona;
  const ttlMs = opts.ttlMs ?? WHOAMI_TTL_MS;
  const cache = getWhoamiCache(persona);
  const todo = opts.force ? platforms : platforms.filter((p) => !isFresh(cache[p], ttlMs));
  if (!todo.length) return;
  let next = 0;
  const worker = async (): Promise<void> => {
    while (next < todo.length) {
      if (opts.alive && !opts.alive()) return;
      const p = todo[next++];
      const version = versions[verKey(p, persona)] || 0;
      try {
        const r = await accountWhoami(p, false, persona);
        if ((versions[verKey(p, persona)] || 0) !== version) continue;
        setWhoamiCache(p, r, persona);
        if (!opts.alive || opts.alive()) opts.onUpdate?.(p, r);
      } catch {
        // 校验失败（超时/网络）：保留既有缓存，跳过该平台
      }
    }
  };
  void Promise.all(
    Array.from({ length: Math.min(WHOAMI_CONCURRENCY, todo.length) }, () => worker()),
  );
}
