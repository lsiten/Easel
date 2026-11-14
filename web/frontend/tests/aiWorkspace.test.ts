import { afterEach, expect, test } from 'bun:test';
import { creationReceipt, creationRequest } from '../src/lib/aiCreation';
import { agentSubmit, aiPath, aiRequest, mediaUrl, readPage } from '../src/lib/aiWorkspaceApi';

import nativeReceipts from './fixtures/creation-receipts.json';

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
afterEach(() => { globalThis.fetch = originalFetch; Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow }); });
function browser() { Object.defineProperty(globalThis, 'window', { configurable: true, value: { location: { pathname: '/easel/index.html' } } }); }

test('transport scopes persona, preserves reverse proxy base and propagates cancellation', async () => {
  browser();
  const controller = new AbortController();
  let captured = '';
  let capturedSignal: AbortSignal | null | undefined;
  globalThis.fetch = async (input, init) => { captured = String(input); capturedSignal = init?.signal; return Response.json({ items: [], total: 0 }); };
  const result = await aiRequest(aiPath('/api/ai/logs', '测试 / owner', { page: '2' }), controller.signal);
  expect(captured).toBe('/easel/api/ai/logs?persona=%E6%B5%8B%E8%AF%95+%2F+owner&page=2');
  expect(capturedSignal).toBe(controller.signal);
  expect(readPage(result)).toEqual({ items: [], total: 0 });
});

test('agent submission consumes split init event once and cancels only the response reader', async () => {
  browser();
  let calls = 0;
  let cancelled = false;
  const encoder = new TextEncoder();
  globalThis.fetch = async () => { calls++; return new Response(new ReadableStream({
    start(controller) { controller.enqueue(encoder.encode('data: {"type":"in')); controller.enqueue(encoder.encode('it","taskId":"original"}\n\n')); },
    cancel() { cancelled = true; },
  })); };
  expect(await agentSubmit('/api/ai/agent/tasks?persona=alice', new AbortController().signal, { taskId: 'original', prompt: 'continue' })).toBe('original');
  expect(calls).toBe(1); expect(cancelled).toBe(true);
});

test('provider validation failures reach the user rather than becoming success', async () => {
  browser();
  globalThis.fetch = async () => Response.json({ detail: 'config_revision_conflict' }, { status: 409 });
  await expect(aiRequest('/api/ai/config', new AbortController().signal, {}, 'PUT')).rejects.toThrow('config_revision_conflict');
});

test('image edit uses native Gemini fields and retains references while generation excludes them', () => {
  const edit = creationRequest('edit', { model: 'gemini-image', channel: 'gemini', prompt: 'edit', images: 'https://example.com/a.png\nhttps://example.com/b.png', ratio: '9:16', imageSize: '2K' }, { persona: 'alice', key: 'stable' });
  expect(edit.path).toBe('/api/ai/image/edit/async');
  expect(edit.payload).toMatchObject({ operation: 'edit', image: ['https://example.com/a.png', 'https://example.com/b.png'], aspectRatio: '9:16', imageSize: '2K', request_key: 'stable' });
  expect(creationRequest('image', { model: 'image', prompt: 'create', quantity: '2' }, { persona: 'alice', key: 'stable' }).payload).toMatchObject({ n: 2, operation: 'generate' });
});

test.each(['video', 'aideo', 'style', 'recap', 'direct-edit', 'character', 'remix'] as const)('workflow %s yields its actual mounted route and scoped payload', (mode) => {
  const result = creationRequest(mode, { model: 'model', prompt: 'Create', inputs: 'video-id', video: 'https://example.com/video.mp4', taskId: 'task', images: 'https://example.com/img.png', end: '3' }, { persona: 'alice', key: 'request' });
  const paths = { video: '/api/ai/video/generations', aideo: '/api/ai/aideo/tasks', style: '/api/ai/aideo/style-transfer/tasks', recap: '/api/ai/aideo/drama-recap/tasks', 'direct-edit': '/api/ai/aideo/video-edit/tasks', character: '/api/ai/openai/characters', remix: '/api/ai/openai/videos/task/remix' };
  expect(result.path).toBe(paths[mode]);
  expect(result.payload).toMatchObject({ persona: 'alice', request_key: 'request' });
});

test('owned asset previews use existing media endpoint and reject script URLs', () => {
  expect(mediaUrl('alice/generated image.png')).toBe('/api/media/alice/generated%20image.png');
  expect(mediaUrl('javascript:alert(1)')).toBe('');
  expect(mediaUrl('../secret')).toBe('');
  expect(() => readPage({ items: [] })).toThrow();
});


test.each(['aideo', 'style', 'recap', 'direct-edit'] as const)('actual native %s response resolves the nonempty polling URL', async (mode) => {
  browser();
  const fixture = nativeReceipts[mode];
  const paths: string[] = [];
  globalThis.fetch = async (input) => { paths.push(String(input)); return Response.json(fixture); };
  const request = creationRequest(mode, { prompt: 'edit', inputs: 'vid://source', video: 'vid://source' }, { persona: 'alice', key: 'native' });
  const signal = new AbortController().signal;
  const receipt = creationReceipt(mode, await aiRequest(request.path, signal, request.payload));
  await aiRequest(aiPath(request.detail + encodeURIComponent(receipt.taskId), 'alice'), signal);
  expect(paths).toEqual(['/easel' + request.path, '/easel' + request.detail + fixture.taskId + '?persona=alice']);
  expect(receipt.taskId.length).toBeGreaterThan(0);
  expect(() => creationReceipt(mode, { id: 'wrong-shape' })).toThrow('任务编号');
});

test('ordinary generation receipt requires id rather than accepting a native taskId', () => {
  expect(creationReceipt('image', { id: 'image-job' })).toEqual({ taskId: 'image-job' });
  expect(() => creationReceipt('image', { taskId: 'wrong-shape' })).toThrow('任务编号');
});

// Draft request tests exercise the exact values submitted by the reusable form.
import { draftRequest } from '../src/lib/aiDraftRequest';
import type { DraftValues } from '../src/lib/aiDraftRequest';
import { GENERATION_TYPES, contentGroupName, jobState, modelChoices } from '../src/lib/aiPresentation';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { ModelOption } from '../src/components/ai/ModelOption';

const draftValues: DraftValues = { kind: 'video', prompt: 'create', captionPrompt: 'caption', model: 'video-model',
  plannerModel: 'planner', quantity: 2, imageCount: 3, groupId: 'selected-group', videoUrl: 'https://example.com/source.mp4',
  ratio: '9:16', imageSize: '2K', resolution: '1080p', duration: '8', images: 'https://example.com/a.png\nhttps://example.com/b.png',
  videos: 'https://example.com/ref.mp4', audios: 'https://example.com/ref.mp3', mediaOnly: true, memory: true, platforms: 'youtube, tiktok' };

test('batch video carries references, planner, output mode and configured media options through transport', async () => {
  browser();
  let body: unknown;
  globalThis.fetch = async (_input, init) => { body = JSON.parse(String(init?.body)); return Response.json({ taskIds: ['draft'] }); };
  const request = draftRequest(draftValues);
  await aiRequest('/api/ai/draft-generation' + request.path, new AbortController().signal, request.payload);
  expect(request.path).toBe('/v2');
  expect(body).toMatchObject({ plannerModel: 'planner', quantity: 2, groupId: 'selected-group', disableMemory: false,
    imageUrls: ['https://example.com/a.png', 'https://example.com/b.png'], videoUrls: ['https://example.com/ref.mp4'],
    audioUrls: ['https://example.com/ref.mp3'], draftType: 'video', aspectRatio: '9:16', resolution: '1080p', duration: 8, platforms: ['youtube', 'tiktok'] });
});

test('image-only and URL draft payloads exclude fields from other generation modes', () => {
  const image = draftRequest({ ...draftValues, kind: 'image-text' });
  expect(image.path).toBe('/image-text');
  expect(image.payload).toMatchObject({ imageModel: 'video-model', imageCount: 3, imageSize: '2K', draftType: 'image' });
  expect(image.payload).not.toHaveProperty('videoUrls');
  const url = draftRequest({ ...draftValues, kind: 'video-url', prompt: '' });
  expect(url.payload).toMatchObject({ quantity: 1, prompt: '', draftType: 'draft', videoUrl: 'https://example.com/source.mp4', plannerModel: 'planner' });
  expect(url.payload).not.toHaveProperty('imageSize');
  expect(draftRequest({ ...draftValues, mediaOnly: false }).payload.draftType).toBe('draft');
});

test('configured capability choices constrain rendered controls; unspecified options remain editable', () => {
  const model = { resolutions: ['1K', '2K'], supportedAspectRatios: ['9:16'], sizes: ['1024x1792'] };
  const control = renderToStaticMarkup(createElement(ModelOption, { label: '分辨率', choices: modelChoices(model, 'resolutions'), value: '', onChange() {} }));
  expect(control).toContain('<select'); expect(control).toContain('value="1K"'); expect(control).toContain('value="2K"'); expect(control).not.toContain('0.5K');
  expect(modelChoices(model, 'supportedAspectRatios', 'aspectRatios')).toEqual(['9:16']);
  const free = renderToStaticMarkup(createElement(ModelOption, { label: '分辨率', choices: modelChoices({}, 'resolutions'), value: '', onChange() {} }));
  expect(free).toContain('<input'); expect(free).not.toContain('<select');
});

test('presentation uses actual draft log type and translates native and durable states', () => {
  expect(GENERATION_TYPES.map(([value]) => value)).toEqual(['', 'agent', 'image', 'video', 'chat', 'aideo', 'draft-generation', 'character']);
  expect(jobState('running')).toBe('处理中'); expect(jobState('Completed')).toBe('已完成');
  expect(jobState('Submitted')).toBe('已提交'); expect(jobState('requires_action')).toBe('等待操作');
});


test('draft history resolves group names without exposing opaque IDs', () => {
  const groups = [{ id: 'b67b1ca98e9b48f9a1cbdc2e1fd335c7', name: '品牌视频' }];
  expect(contentGroupName(groups[0].id, groups)).toBe('品牌视频');
  expect(contentGroupName('', groups)).toBe('默认分组');
  expect(contentGroupName('removed-group', groups)).toBe('原分组不可用');
});

import settingsFixture from './fixtures/runtime-settings.json';
import { runtimeSettings, runtimeSettingsUpdate } from '../src/lib/aiRuntimeSettings';

test('runtime metadata and save preserve masked secrets and environment locks through transport', async () => {
  browser();
  const requests: { path: string; payload: unknown }[] = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ path: String(input), payload: init?.body ? JSON.parse(String(init.body)) : null });
    return Response.json(settingsFixture);
  };
  const signal = new AbortController().signal;
  const fields = runtimeSettings(await aiRequest('/api/generation-settings', signal));
  const secret = fields.find((field) => field.name === 'VOLCENGINE_SECRET_ACCESS_KEY');
  expect(secret).toMatchObject({ configured: true, secret: true, value: '', locked: false });
  expect(fields.find((field) => field.name === 'EASEL_DRAFT_ASR_MODEL')?.locked).toBe(true);
  expect(fields.some((field) => field.name === 'GEMINI_API_KEY')).toBe(false);
  const update = runtimeSettingsUpdate(fields, { VOLCENGINE_SECRET_ACCESS_KEY: '', VOLCENGINE_SPACE_NAME: 'new-space',
    EASEL_DRAFT_ASR_MODEL: 'attempted-locked-change', GEMINI_API_KEY: 'not-owned-by-this-form' });
  await aiRequest('/api/generation-settings', signal, update);
  expect(requests).toEqual([{ path: '/easel/api/generation-settings', payload: null },
    { path: '/easel/api/generation-settings', payload: { values: { VOLCENGINE_SPACE_NAME: 'new-space' } } }]);
});

test('runtime secret replacement is explicit and invalid rates cannot be submitted', () => {
  const fields = runtimeSettings(settingsFixture);
  expect(runtimeSettingsUpdate(fields, { VOLCENGINE_SECRET_ACCESS_KEY: 'replacement' })).toEqual({ values: { VOLCENGINE_SECRET_ACCESS_KEY: 'replacement' } });
  expect(runtimeSettingsUpdate(fields, { VOLCENGINE_SPACE_NAME: 'fixture-space' })).toEqual({ values: {} });
  expect(() => runtimeSettingsUpdate(fields, { EASEL_DRAFT_MODEL_RATES: '[]' })).toThrow('JSON 对象');
  const rates = '{"video-model":[{"amount":0.01,"currency":"USD","unit":"second"}]}';
  expect(runtimeSettingsUpdate(fields, { EASEL_DRAFT_MODEL_RATES: rates })).toEqual({ values: { EASEL_DRAFT_MODEL_RATES: rates } });
});

import { AgentTaskDetail } from '../src/components/ai/AgentTaskDetail';

const historicalQuestion = { uuid: 'old:2', type: 'question', data: JSON.stringify({ id: 'old-question', questions: [{ questionId: 'approval', question: 'Earlier approval?', options: [] }] }) };
const currentQuestion = { uuid: 'current:4', type: 'question', data: JSON.stringify({ id: 'current-question', questions: [{ questionId: 'approval', question: 'Current approval?', options: [] }] }) };
function taskQuestionMarkup(status: string, messages: readonly unknown[]) {
  return renderToStaticMarkup(createElement(AgentTaskDetail, { persona: 'alice', task: { id: 'task', title: 'Question task', status, messages }, onChanged() {}, onDeleted() {} }));
}

test.each(['completed', 'aborted', 'error', 'requires_action'])('reopened %s task renders durable questions read-only', (status) => {
  const markup = taskQuestionMarkup(status, [{ uuid: 'old:user', type: 'user', data: 'ask' }, historicalQuestion]);
  expect(markup).toContain('Earlier approval?'); expect(markup).toContain('历史问题（只读）');
  expect(markup).not.toContain('提交回答'); expect(markup).not.toContain('list="options-approval"');
});

test('only latest question of the running current turn offers submit', () => {
  const messages = [{ uuid: 'old:user', type: 'user', data: 'ask' }, historicalQuestion,
    { uuid: 'current:user', type: 'user', data: 'ask again' },
    { ...currentQuestion, uuid: 'current:2' }, currentQuestion];
  const markup = taskQuestionMarkup('running', messages);
  expect(markup.match(/提交回答/g)?.length).toBe(1);
  expect(markup.match(/历史问题（只读）/g)?.length).toBe(2);
  expect(taskQuestionMarkup('running', messages.slice(0, 3))).not.toContain('提交回答');
  expect(taskQuestionMarkup('running', [...messages.slice(0, 3), historicalQuestion])).not.toContain('提交回答');
});

test('runtime output after a question closes its submit control without waiting for task completion', () => {
  const messages = [{ uuid: 'current:user', type: 'user', data: 'ask' }, currentQuestion];
  expect(taskQuestionMarkup('running', messages)).toContain('提交回答');
  expect(taskQuestionMarkup('running', [...messages, { uuid: 'current:5', type: 'token', data: 'answer received' }])).not.toContain('提交回答');
});

import { rows as aiRows } from '../src/lib/aiWorkspaceApi';

test('a late image catalog cannot replace video choices after the workflow changes', async () => {
  browser();
  const pending: { path: string; complete: (response: Response) => void }[] = [];
  globalThis.fetch = (input) => new Promise<Response>((complete) => pending.push({ path: String(input), complete }));
  const image = new AbortController();
  const video = new AbortController();
  const imageResult = loadAiQuery('/api/ai/models/image/generation?persona=alice', image.signal, aiRows);
  image.abort();
  const videoResult = loadAiQuery('/api/ai/models/video/generation?persona=alice', video.signal, aiRows);
  expect(pending.map((request) => request.path)).toEqual(['/easel/api/ai/models/image/generation?persona=alice', '/easel/api/ai/models/video/generation?persona=alice']);
  pending[1].complete(Response.json([{ name: 'qa-video', channel: 'openai' }]));
  expect(await videoResult).toEqual([{ name: 'qa-video', channel: 'openai' }]);
  // Intentionally emulate a transport that still resolves after cancellation.
  pending[0].complete(Response.json([{ name: 'qa-openai-image' }, { name: 'qa-gemini' }]));
  expect(await imageResult).toBeNull();
});

import { loadAiQuery } from '../src/lib/aiQuery';
import { document as aiDocument } from '../src/lib/aiWorkspaceApi';

test.each([
  ['/api/ai/logs?type=image', '/api/ai/logs?type=video'],
  ['/api/ai/agent/tasks?keyword=old', '/api/ai/agent/tasks?keyword=new'],
  ['/api/ai/agent/tasks/old', '/api/ai/agent/tasks/new'],
  ['/api/ai/agent/tasks/running', '/api/ai/agent/tasks/completed'],
])('superseded AI query %s cannot overwrite %s', async (older, newer) => {
  browser();
  const pending: ((value: Response) => void)[] = [];
  globalThis.fetch = () => new Promise<Response>((complete) => pending.push(complete));
  const first = new AbortController(); const second = new AbortController();
  let visible: unknown;
  const oldRequest = loadAiQuery(older, first.signal, aiDocument).then((value) => { if (value !== null) visible = value; });
  first.abort();
  const newRequest = loadAiQuery(newer, second.signal, aiDocument).then((value) => { if (value !== null) visible = value; });
  pending[1](Response.json({ id: 'new', status: 'completed', items: [{ id: 'new' }], total: 1 }));
  await newRequest;
  pending[0](Response.json({ id: 'old', status: 'running', items: [{ id: 'old' }], total: 1 }));
  await oldRequest;
  expect(visible).toMatchObject({ id: 'new', status: 'completed', items: [{ id: 'new' }] });
  expect(second.signal.aborted).toBe(false);
});

import genericVideoResult from './fixtures/generic-video-result.json';
import { Result } from '../src/components/ai/AiPrimitives';

test('actual mounted generic video response renders playable media and open-file link', () => {
  const markup = renderToStaticMarkup(createElement(Result, { value: genericVideoResult }));
  expect(markup).toContain(`<video src="${genericVideoResult.videoUrl}" controls=""`);
  expect(markup).toContain(`<a href="${genericVideoResult.videoUrl}"`);
  expect(markup).toContain('打开原文件');
});

test('native workflow outputUrl and existing draft/image results retain previews without duplicates', () => {
  const url = '/api/media/project/generated.mp4';
  const native = renderToStaticMarkup(createElement(Result, { value: { ...nativeReceipts['direct-edit'], state: 'succeeded', outputUrl: url, assets: [{ path: 'project/generated.mp4', kind: 'video' }] } }));
  expect(native.match(/<video /g)?.length).toBe(1);
  const workflow = renderToStaticMarkup(createElement(Result, { value: { ...nativeReceipts.style, state: 'succeeded', outputUrl: url } }));
  expect(workflow).toContain(`<video src="${url}"`);
  const draft = renderToStaticMarkup(createElement(Result, { value: { state: 'succeeded', response: { title: 'Draft title', imageUrls: ['/api/media/project/image.png'], videoUrl: url } } }));
  expect(draft).toContain('Draft title'); expect(draft).toContain('<img src="/api/media/project/image.png"'); expect(draft).toContain(`<video src="${url}"`);
  const unsafe = renderToStaticMarkup(createElement(Result, { value: { state: 'succeeded', videoUrl: 'javascript:alert(1)' } }));
  expect(unsafe).not.toContain('<video'); expect(unsafe).not.toContain('<a href="javascript:');
});

import { publicShareError } from '../src/lib/aiPresentation';
import { Feedback } from '../src/components/ai/AiPrimitives';

test('default logs transport omits optional empty filters but retains the general persona scope', async () => {
  browser();
  const paths: string[] = [];
  globalThis.fetch = async (input) => { paths.push(String(input)); return Response.json({ items: [], total: 0 }); };
  const filters = { page: '1', page_size: '20', type: '', status: '', model: '', channel: '' };
  await aiRequest(aiPath('/api/ai/logs', 'alice', filters), new AbortController().signal);
  await aiRequest(aiPath('/api/ai/logs', '', filters), new AbortController().signal);
  await aiRequest(aiPath('/api/ai/logs', 'alice', { ...filters, status: 'success', type: 'draft-generation' }), new AbortController().signal);
  expect(paths).toEqual(['/easel/api/ai/logs?persona=alice&page=1&page_size=20', '/easel/api/ai/logs?persona=&page=1&page_size=20',
    '/easel/api/ai/logs?persona=alice&page=1&page_size=20&type=draft-generation&status=success']);
});

test('unavailable public share renders contextual Chinese feedback without internal error codes', async () => {
  browser();
  globalThis.fetch = async () => Response.json({ detail: 'agent_task_not_found' }, { status: 404 });
  let error = '';
  try { await aiRequest('/api/ai/agent/tasks/shared/expired', new AbortController().signal); }
  catch (failure: unknown) { if (failure instanceof Error) error = failure.message; else throw failure; }
  const markup = renderToStaticMarkup(createElement(Feedback, { busy: false, error: publicShareError(error) }));
  expect(markup).toContain('链接可能已过期或被删除'); expect(markup).not.toContain('agent_task_not_found');
  expect(publicShareError('network down')).toBe('暂时无法加载分享，请检查网络后重试。');
});

test.each([
  ['/api/ai/draft-generation/memory?persona=alice&contentType=image-text', '/api/ai/draft-generation/memory?persona=alice&contentType=video'],
  ['/api/ai/draft-generation?persona=alice&page=1', '/api/ai/draft-generation?persona=alice&page=2'],
])('memory/draft transport discards superseded %s in favor of %s', async (older, newer) => {
  browser();
  const pending: ((value: Response) => void)[] = [];
  globalThis.fetch = () => new Promise<Response>((complete) => pending.push(complete));
  const oldScope = new AbortController(); const currentScope = new AbortController();
  const oldResult = loadAiQuery(older, oldScope.signal, aiDocument);
  oldScope.abort();
  const currentResult = loadAiQuery(newer, currentScope.signal, aiDocument);
  pending[1](Response.json({ items: [{ id: 'current' }], total: 1 }));
  expect(await currentResult).toEqual({ items: [{ id: 'current' }], total: 1 });
  pending[0](Response.json({ items: [{ id: 'older' }], total: 1 }));
  expect(await oldResult).toBeNull();
});

import { AiHistory } from '../src/components/ai/AiHistory';

test('log records have a visible level-two section before their level-three rows', () => {
  const logs = renderToStaticMarkup(createElement(AiHistory, { persona: 'alice' }));
  expect(logs).toContain('<h2>生成记录</h2><div class="ai-stack">');
  const assets = renderToStaticMarkup(createElement(AiHistory, { persona: 'alice', assets: true }));
  expect(assets).not.toContain('<h2>生成记录</h2>');
});
