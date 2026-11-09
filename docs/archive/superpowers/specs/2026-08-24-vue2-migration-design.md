# Vue 2 Full Migration Design

## Goal

Migrate the complete Easy Agent frontend from Vue 3 to a standard Vue 2 application while preserving all existing user-facing capabilities: authentication, sessions, chat streaming, files, workspace, skill center, scheduled tasks, settings, user profile, and user management.

## Confirmed Stack

- Vue `2.7.x`
- Vue CLI `5.x` with Webpack
- Element UI `2.15.x`
- Axios for all HTTP requests
- Existing Tailwind CSS and custom chat/workspace styling

## Architecture

### Application Bootstrap

Replace the Vue 3 `createApp` entry point with a Vue 2 root instance. Install Element UI globally and mount the existing `App.vue` root component after migration. Keep runtime frontend configuration, environment-specific API addresses, and application title behavior.

### Component Model

Convert all 27 Vue SFCs from `<script setup>` and Vue 3 Composition API to Vue 2 Options API:

- Props become `props` declarations.
- Emits become declared Vue 2 event names.
- Reactive refs and computed values become `data`, `computed`, and `watch`.
- Lifecycle functions become `created`, `mounted`, `beforeDestroy`, and `destroyed`.
- Template refs become string refs accessed through `this.$refs`.
- Vue 3-only template syntax and components are replaced with Vue 2 equivalents.
- Common controls, modals, dropdowns, messages, forms, and tables use Element UI.

Complex chat and workspace layouts remain custom components because their interaction requirements are specialized. Element UI is used for standard controls and feedback rather than forcing the entire layout into a generic admin template.

### Unified Axios Layer

Create a shared request module under `frontend/src/api/request.js`. It will provide:

- One Axios instance with `API_BASE_URL`
- Authorization header injection
- User-activity event dispatch
- 401 handling and auth-expired event dispatch
- Backend error normalization
- Consistent JSON body handling
- Optional per-request error suppression for best-effort calls

All existing API modules will use this instance instead of direct `fetch`. Streaming endpoints remain fetch-based where the current frontend relies on streamed response bodies.

### UI Strategy

Element UI supplies dialogs, form controls, selects, dropdowns, tables, messages, notifications, loading states, and confirmation flows. Existing brand identity, light/dark theme, chat bubbles, markdown rendering, file previews, and workspace panels retain their custom styling. Component behavior and screen information architecture remain unchanged.

### File and Document Previews

Vue 3-specific preview packages will be replaced with Vue 2-compatible wrappers. Preview logic that is framework-neutral, such as PDF.js, Excel parsing, document conversion, and Monaco editor, remains in isolated components. Vue 2 adapters own lifecycle, DOM mounting, and cleanup.

## Non-Goals

- No backend API redesign.
- No product redesign or navigation restructuring.
- No removal of existing features.
- No attempt to run Vue 3 components through compatibility shims.

## Migration Order

1. Build system and dependency baseline.
2. Axios request layer and API module conversion.
3. Authentication and root application shell.
4. Session list, chat, chat input, and message rendering.
5. Files, workspace, generated files, and preview components.
6. Settings, model selection, MCP, skills, and scheduled tasks.
7. User profile, user management, and administrative flows.
8. Polish, theme compatibility, runtime configuration, and production build.

## Verification

- `npm run build` must pass for the migrated Vue CLI project.
- Environment-specific build modes must continue resolving `.env.dev`, `.env.test`, `.env.prod`, and `.env.win`.
- Every migrated API module must use the unified Axios wrapper.
- Login, session creation, chat send, file upload/download, settings load, and user management endpoints must remain unchanged.
- Manual browser checks must cover light/dark themes and desktop viewport behavior.

## Risks and Mitigations

- **Large rewrite surface:** migrate in functional groups and build after each group.
- **Vue 3-only dependencies:** isolate preview and editor integrations behind Vue 2 adapters.
- **Streamed chat responses:** keep fetch streaming where Axios does not replace it, but centralize headers and error handling.
- **Webpack environment differences:** preserve the existing platform mode scripts and validate all four environment files.
