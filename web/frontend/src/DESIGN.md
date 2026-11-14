# Easel AI workspace

## 1. Product and journeys
Creators use the current persona to resume conversations, review generated media and create batch drafts. Operators edit provider configuration. Existing chat keeps attachments, references, questions and reconnect behavior.

## 2. Visual source
Reuse the existing ChannelsPage/ChannelWorkspace and project styles. No new brand or component dependency. Palette roles are --bg, --surface, --surface-2, --text, --text-secondary, --border, --accent-start, --red in styles/index.css and styles/theme.css. Both themes inherit these roles.

## 3. Type and spacing
Use inherited system typography, page-title, field-label, field, btn, panel, subnav-tabs. Existing spacing scale --space-2/3/4/6 corresponds to 8/12/16/24 CSS px. Workspace custom layout uses 1rem and 1.5rem equivalents when absent. Native controls keep 44px minimum touch targets. Body text wraps at 70ch. Grid columns collapse to one at narrow widths.

## 4. Layout
Layout: fixed-sidenav-shell + stack + intrinsic grid; existing Easel sidebar is fixed, AI workspace owns vertical scroll in bounded page-host. Tabs wrap, form fields use a responsive two-column grid, task rows precede selected detail. Spatial reference: existing ChannelWorkspace; no external template needed.

## 5. Components and brief
Primary action: submit the active creation form. Priorities: selected workspace, current status/results, secondary controls. Omit decorative metrics and promotion. Primitives: existing btn default/hover/focus/disabled, field with explicit label/error, panel, subnav-tab with aria-pressed, native details for advanced input, live status for request state. Pager has explicit disabled boundaries.
Required controls: tasks search/favorite/page/detail/messages/resume/rename/rating/share/abort/delete/questions; logs filters/page; owned image/video assets/page/preview; native image/edit, five video channels, Aideo/style/recap/edit/character forms; drafts mode/quantity/group/query/stats/detail; memory view/delete/regenerate; provider/model/queue configuration validate/save/apply with masked secrets.

## 6. State and access
Every request is cancellable on persona change/unmount. Errors appear inline with retry; empty states explain next action. Destructive actions require explicit click. Sharing includes expiry. All fields have labels, actions are keyboard reachable, no hover-only required action. Existing account/profile/browser alternatives stay available in sidebar.

## 7. Materials and motion
Use project surfaces and neutral borders. No new gradients, shadow, animations or decorative badges.

## 8. Verification ledger
Root owns real Ego before/after screenshots at 390 and 1440 and both themes. Frontend build/lint and API transport tests captured under .omo/evidence. No new tooling installation is authorized in this task.

## AI control accessibility correction
The AI-local `--ai-control-font-size` is 0.875rem (14px) on desktop and 0.9375rem (15px) below the existing 700px mobile breakpoint. Fields, labels, buttons and tabs use this token. Labels and placeholders use existing `--text` in both themes; placeholders and enabled tabs retain full opacity. This overrides the inherited small/low-contrast label and placeholder styling only inside `.ai-workspace`; global theme tokens remain unchanged.
