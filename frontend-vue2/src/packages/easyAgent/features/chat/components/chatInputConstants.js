// ChatInput 布局尺寸、存储键与分页常量（从 ChatInput.vue 拆出，保持配置集中管理）。

export const MIRROR_BLEED = 6
export const SUBPANEL_WIDTH = 480
export const SUBPANEL_GAP = 6
export const SUBPANEL_MAX_HEIGHT = 560

export const DRAFT_KEY = 'easy_agent_input_draft'
export const PENDING_SKILL_KEY = 'easy_agent_pending_skill'
export const PENDING_EXPERT_EDIT_KEY = 'easy_agent_pending_expert_edit'

export const TEXTAREA_MAX_ROWS = 5
export const TEXTAREA_LINE_HEIGHT = 24
export const TEXTAREA_MAX_HEIGHT = TEXTAREA_MAX_ROWS * TEXTAREA_LINE_HEIGHT

export const PLUS_EXPERTS_PAGE_SIZE = 5
export const PUBLIC_EXPERTS_PAGE_SIZE = 6
export const PUBLIC_EXPERTS_MORE_STEP = 3
export const EXPERT_SEARCH_DEBOUNCE_MS = 300
