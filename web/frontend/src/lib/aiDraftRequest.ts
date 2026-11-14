export type DraftKind = 'image-text' | 'video' | 'video-url';
export type DraftValues = {
  readonly kind: DraftKind; readonly prompt: string; readonly captionPrompt: string; readonly model: string;
  readonly plannerModel: string; readonly quantity: number; readonly imageCount: number; readonly groupId: string;
  readonly videoUrl: string; readonly ratio: string; readonly imageSize: string; readonly resolution: string;
  readonly duration: string; readonly images: string; readonly videos: string; readonly audios: string;
  readonly mediaOnly: boolean; readonly memory: boolean; readonly platforms: string;
};
const references = (value: string) => value.split('\n').map((line) => line.trim()).filter(Boolean);
export function draftRequest(values: DraftValues) {
  const common = { prompt: values.prompt, captionPrompt: values.captionPrompt, quantity: values.quantity,
    groupId: values.groupId, plannerModel: values.plannerModel, disableMemory: !values.memory,
    ...(values.platforms.trim() ? { platforms: values.platforms.split(',').map((value) => value.trim()).filter(Boolean) } : {}) };
  const ratio = values.ratio ? { aspectRatio: values.ratio } : {};
  switch (values.kind) {
    case 'image-text': return { path: '/image-text', payload: { ...common, ...ratio, imageModel: values.model,
      imageCount: values.imageCount, imageSize: values.imageSize, imageUrls: references(values.images), draftType: values.mediaOnly ? 'image' : 'draft' } };
    case 'video': return { path: '/v2', payload: { ...common, ...ratio, model: values.model, resolution: values.resolution,
      ...(values.duration ? { duration: Number(values.duration) } : {}), imageUrls: references(values.images),
      videoUrls: references(values.videos), audioUrls: references(values.audios), draftType: values.mediaOnly ? 'video' : 'draft' } };
    case 'video-url': return { path: '/from-video-url', payload: { ...common, quantity: 1, videoUrl: values.videoUrl, draftType: 'draft' } };
    default: { const unreachable: never = values.kind; throw new Error(`未知草稿类型 ${unreachable}`); }
  }
}
