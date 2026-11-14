# Easel AI workspace

## 1. Product and journeys
Creators use the current persona to resume conversations, review generated media and create batch drafts. Operators edit provider configuration. Existing chat keeps attachments, references, questions and reconnect behavior.

## 2. Visual source
Reuse the existing ChannelsPage/ChannelWorkspace and project styles. No new brand or component dependency. Palette roles are --bg, --surface, --surface-2, --text, --text-secondary, --border, --accent-start, --red in styles/index.css and styles/theme.css. Both themes inherit these roles.

## 3. Type and spacing
Use inherited system typography, page-title, field-label, field, btn, panel, subnav-tabs. Existing spacing scale --space-2/3/4/6 corresponds to 8/12/16/24 CSS px. Workspace custom layout uses 1rem and 1.5rem equivalents when absent. Native controls keep 44px minimum touch targets. Body text wraps at 70ch. Grid columns collapse to one at narrow widths.

## 4. Layout
Layout: fixed-sidenav-shell + stack + intrinsic grid; existing Easel sidebar is fixed, AI workspace owns vertical scroll in bounded page-host. Tabs wrap, form fields use a responsive two-column grid, task rows precede selected detail. Spatial reference: existing ChannelWorkspace; no external template needed.
Layout: creation composer - separate prompt, model, output settings and results without nesting scroll - content-limiter + intrinsic grid (https://github.com/changeroa/StyleGallery/blob/main/CATALOG.md); scroll owner: `.section-body-scroll`; anti-patterns: no independent nested form scroller, no full-width sparse card, no controls stranded below an empty canvas.
Layout: batch generation - group draft type, content, settings, references and task history in reading order - page stack + intrinsic controls (https://github.com/changeroa/StyleGallery/blob/main/CATALOG.md); scroll owner: `.section-body-scroll`; anti-patterns: no label wrapping inside a narrow equal-width column, no full-width submit bar detached from readiness, no repeated task card inside a framed list.
Layout: content memory - separate extraction settings from extracted records - page stack + record list (https://github.com/changeroa/StyleGallery/blob/main/CATALOG.md); scroll owner: `.section-body-scroll`; anti-patterns: no lone two-field row above an unexplained blank canvas, no destructive action without the record beside it.
Layout: video workspace - compare generated videos and expose the current Studio scope at a glance - page stack + intrinsic media-card grid (https://github.com/changeroa/StyleGallery/blob/main/CATALOG.md); scroll owner: `.section-body-scroll` or `.gallery-page`; anti-patterns: no single 4:3 card stretched across the desktop canvas, no hidden primary action, no unlabelled loading region.
Layout: video preview and Studio states - keep the chosen media object, its actions and playback in one bounded surface - linked detail + overlay exception (https://github.com/changeroa/StyleGallery/blob/main/CATALOG.md); scroll owner: the page; anti-patterns: no background page scroll while the preview dialog is open, no permanent empty iframe.
Layout: content drafts and media library - group selector and bounded actions, then current-group editor and record list - page stack + focused inline editor (https://github.com/changeroa/StyleGallery/blob/main/CATALOG.md); scroll owner: `.content-panel-scroll`; anti-patterns: no button rows glued to fields, no all-platform picker expanded before the primary form action.

## 5. Components and brief
Primary action: submit the active creation form. Priorities: selected workspace, current status/results, secondary controls. Omit decorative metrics and promotion. Primitives: existing btn default/hover/focus/disabled, field with explicit label/error, panel, subnav-tab with aria-pressed, native details for advanced input, live status for request state. Pager has explicit disabled boundaries.
Required controls: tasks search/favorite/page/detail/messages/resume/rename/rating/share/abort/delete/questions; logs filters/page; owned image/video assets/page/preview; native image/edit, five video channels, Aideo/style/recap/edit/character forms; drafts mode/quantity/group/query/stats/detail; memory view/delete/regenerate; provider credentials/models only in the six channel settings, with queue and non-provider runtime parameters in advanced configuration.

## 6. State and access
Every request is cancellable on persona change/unmount. Errors appear inline with retry; empty states explain next action. Destructive actions require explicit click. Sharing includes expiry. All fields have labels, actions are keyboard reachable, no hover-only required action. Existing account/profile/browser alternatives stay available in sidebar.

### Creation optimization brief

- **Primary action:** submit the active creation form, or open the current video project in Studio when no generation is pending.
- **Information priorities:** 1) the prompt or selected video, 2) the model and output constraints, 3) generated results and the next actionable state.
- **Left out on purpose:** decorative AI badges, speculative metrics, duplicate project summaries and permanent configuration blocks that do not affect the active mode.
- **Required content retained:** all nine creation modes; prompt; model; image size; aspect ratio; image count; quality; style; reference image and mask inputs; video mode, duration, resolution, references and tail frame; Aideo inputs, space, skill and advanced JSON; style transfer, recap, track edit, character and remix fields; video gallery preview, Studio entry, refresh and AI creation.
- **Batch and memory content retained:** draft type, group, target platforms, creation/caption prompts, video URL, planner and generator models, quantity, ratio, image count/size, video resolution/duration, references, memory toggle, task selection/query/progress/pagination; memory type, planner model, regenerate/refresh, extracted samples and delete.
- **Data shape:** one creation request is a form; many videos are a responsive media grid; the selected video is a bounded playback detail; Studio availability is a state row.

## 7. Materials and motion
Use project surfaces and neutral borders. No new gradients, shadow, animations or decorative badges.

Controls use the existing `--t-fast`/`--t` tokens. Press feedback is a same-frame tonal and 1px transform change; loading swaps text and spinner inside a fixed-size action slot. `prefers-reduced-motion` removes positional motion while retaining opacity and status changes. Project cards keep their media aspect ratio and never animate size under the pointer.

The generation and video workspaces share the centered `--content-wide` cap of 82.5rem and always stretch to the available width below that cap. Mode strips, page headings, project grids and Hypit panels share the same left and right edges; inline margins never cause a shrink-to-fit grid.

## 8. Verification ledger
Root owns real Ego before/after screenshots at 390 and 1440 and both themes. Frontend build/lint and API transport tests captured under .omo/evidence. No new tooling installation is authorized in this task.

## AI control accessibility correction
The AI-local `--ai-control-font-size` is 0.875rem (14px) on desktop and 0.9375rem (15px) below the existing 700px mobile breakpoint. Fields, labels, buttons and tabs use this token. Labels and placeholders use existing `--text` in both themes; placeholders and enabled tabs retain full opacity. This overrides the inherited small/low-contrast label and placeholder styling only inside `.ai-workspace`; global theme tokens remain unchanged.

### Creation reusable primitives

- `creation-mode-tab`: horizontal, keyboard-reachable mode selector; idle, hover, focus-visible, pressed and selected.
- `creation-mode-shell`: sticky segmented toolbar; label and mode rail share one row on desktop, collapse into two rows on narrow screens, and fade the scrollable rail edge when more modes are available.
- `creation-section`: tonal surface with a heading, supporting text and intrinsic field grid; loading, error and disabled child states.
- `creation-submit`: one primary action with fixed idle/loading geometry and inline status.
- `video-card`: 16:9 media frame, label, scoped Studio action and pointer/keyboard preview trigger; empty thumbnail and failed-media fallback.
- `video-preview-dialog`: labelled modal player with visible close, Escape dismissal and focus return.
- `studio-status-row`: ready, loading, unavailable and error states with an adjacent manual action.
- `batch-mode-tab`: roving-tabindex draft type selector with Arrow/Home/End keyboard movement.
- `batch-task-row`: checkbox, draft summary, state, group and progress action on one row.
- `memory-record`: extracted text with a same-row destructive action and sample count.
