import { channelDocument } from './channelsApi';
export const CREATION_MODES = [
  ['image', '图片生成'], ['edit', '图片编辑'], ['video', '视频生成'], ['aideo', '智能视频'],
  ['style', '视频风格转换'], ['recap', '短剧解说'], ['direct-edit', '视频轨道编辑'], ['character', 'Sora 角色'], ['remix', '视频重混'],
] as const;
export type CreationMode = typeof CREATION_MODES[number][0];
export type CreationValues = Readonly<Record<string, string>>;
const lines = (value = '') => value.split('\n').map((line) => line.trim()).filter(Boolean);
export function creationRequest(mode: CreationMode, values: CreationValues, scope: { readonly persona: string; readonly key: string }) {
  const base = { persona: scope.persona, request_key: scope.key };
  const prompt = values.prompt || '';
  switch (mode) {
    case 'image': case 'edit': return { path: `/api/ai/image/${mode === 'edit' ? 'edit' : 'generate'}/async`, detail: '/api/ai/image/task/', payload: {
      ...base, prompt, model: values.model, channel: values.channel || '', operation: mode === 'edit' ? 'edit' : 'generate',
      size: values.size || '1024x1024', imageSize: values.imageSize || '', aspectRatio: values.ratio || '',
      ...(mode === 'edit' ? { image: lines(values.images), mask: values.mask || '' } : { n: Number(values.quantity || 1), quality: values.quality || '', style: values.style || '' }),
    } };
    case 'video': return { path: '/api/ai/video/generations', detail: '/api/ai/video/generations/', payload: {
      ...base, prompt, model: values.model, mode: values.mode || '', resolution: values.resolution || '', ratio: values.ratio || '',
      ...(values.duration ? { duration: Number(values.duration) } : {}), images: lines(values.images), videos: lines(values.videos), audios: lines(values.audios),
      image_tail: values.imageTail || '', groupId: values.groupId || '',
    } };
    case 'aideo': return { path: '/api/ai/aideo/tasks', detail: '/api/ai/aideo/tasks/', payload: { ...base, multiInputs: lines(values.inputs), spaceName: values.space || '',
      ...(values.skill ? { skillType: values.skill, skillParams: channelDocument(JSON.parse(values.params || '{}')) } : { prompt }) } };
    case 'style': return { path: '/api/ai/aideo/style-transfer/tasks', detail: '/api/ai/aideo/style-transfer/tasks/', payload: { ...base, videoInput: values.video, style: values.style || null, resolution: values.resolution || '720p' } };
    case 'recap': return { path: '/api/ai/aideo/drama-recap/tasks', detail: '/api/ai/aideo/drama-recap/tasks/', payload: { ...base, vids: lines(values.inputs), recapText: prompt || null,
      recapStyle: values.style || null, recapTextSpeed: Number(values.speed || 1.2), pauseTime: Number(values.pause || 120),
      ...(values.voice ? { speakerConfig: { appId: values.appId || '', cluster: values.cluster || '', voiceType: values.voice } } : {}) } };
    case 'direct-edit': return { path: '/api/ai/aideo/video-edit/tasks', detail: '/api/ai/aideo/video-edit/tasks/', payload: { ...base,
      Canvas: { Width: Number(values.width || 1920), Height: Number(values.height || 1080) }, SpaceName: values.space || '',
      Track: values.track ? JSON.parse(values.track) : [[{ Type: 'video', Source: values.video, SourceTime: [Number(values.start || 0), Number(values.end || 10)], TargetTime: [0, Number(values.end || 10) - Number(values.start || 0)] }]],
    } };
    case 'character': return { path: '/api/ai/openai/characters', detail: '/api/ai/openai/characters/', payload: { ...base, prompt, timestamps: `${values.start || 0},${values.end || 3}`, ...(values.taskId ? { taskId: values.taskId } : { videoUrl: values.video }) } };
    case 'remix': return { path: `/api/ai/openai/videos/${encodeURIComponent(values.taskId || '')}/remix`, detail: '/api/ai/openai/videos/', payload: { ...base, prompt } };
    default: { const unreachable: never = mode; throw new Error(`未知模式 ${unreachable}`); }
  }
}

export type CreationReceipt = { readonly taskId: string };
export function creationReceipt(mode: CreationMode, value: unknown): CreationReceipt {
  const receipt = channelDocument(value);
  let identifier: unknown;
  switch (mode) {
    case 'aideo': case 'style': case 'recap': case 'direct-edit':
      identifier = receipt.taskId;
      break;
    case 'image': case 'edit': case 'video': case 'character': case 'remix':
      identifier = receipt.id;
      break;
    default: { const unreachable: never = mode; throw new Error(`未知模式 ${unreachable}`); }
  }
  if (typeof identifier !== 'string' || !identifier.trim()) throw new Error('服务未返回有效任务编号');
  return { taskId: identifier };
}
