<template>
  <div
    v-if="showTokenRing"
    ref="ringRef"
    class="context-ring-wrapper"
    @click.stop="toggleTokenPopup"
  >
    <svg class="context-ring" viewBox="0 0 36 36">
      <circle
        class="context-ring-bg"
        cx="18"
        cy="18"
        r="15.9"
        fill="none"
        stroke="#e5e7eb"
        stroke-width="3"
      />
      <circle
        class="context-ring-fill"
        cx="18"
        cy="18"
        r="15.9"
        fill="none"
        :stroke="contextColor"
        stroke-width="3"
        stroke-linecap="round"
        :stroke-dasharray="`${contextPercent} ${100 - contextPercent}`"
        transform="rotate(-90 18 18)"
      />
    </svg>
    <span class="context-ring-text">{{ contextPercent }}%</span>
    <Teleport to="body">
      <div
        v-if="showTokenPopup"
        class="token-popup"
        :style="popupStyle"
        @click.stop
      >
        <div class="token-popup-title">会话信息</div>
        <div class="token-popup-kpis">
          <div class="token-popup-kpi">
            <div class="token-popup-kpi-label">会话耗时</div>
            <div class="token-popup-kpi-value">{{ formattedDuration }}</div>
          </div>
          <div class="token-popup-kpi">
            <div class="token-popup-kpi-label">迭代次数</div>
            <div class="token-popup-kpi-value">{{ iterationCount }}</div>
          </div>
        </div>
        <div class="token-popup-context-card">
          <div class="token-popup-context-head">
            <span class="token-popup-label">
              本轮上下文{{ sessionUsage.context_estimated ? "（估算）" : "" }}
            </span>
            <span
              class="token-popup-context-badge"
              :style="{ color: contextColor, background: `color-mix(in srgb, ${contextColor} 14%, transparent)` }"
            >
              {{ contextPercent }}%
            </span>
          </div>
          <div class="token-popup-context-nums">
            <span class="token-popup-context-main">{{ formatTokens(sessionUsage.context_tokens) }}</span>
            <span class="token-popup-context-total"> / {{ formatTokens(sessionUsage.context_length) }}</span>
          </div>
          <div class="token-popup-bar">
            <div class="token-popup-bar-inner">
              <div
                class="token-popup-bar-fill"
                :style="{ width: contextPercent + '%', background: contextColor }"
              ></div>
            </div>
          </div>
        </div>
        <div class="token-popup-legend">
          <div class="token-popup-row">
            <span class="token-popup-label-wrap">
              <span class="token-dot dot-input"></span>
              <span class="token-popup-label">输入 Token</span>
            </span>
            <span class="token-popup-num">{{ formatTokens(sessionUsage.input_tokens) }}</span>
          </div>
          <div class="token-popup-row">
            <span class="token-popup-label-wrap">
              <span class="token-dot dot-output"></span>
              <span class="token-popup-label">输出 Token</span>
            </span>
            <span class="token-popup-num">{{ formatTokens(sessionUsage.output_tokens) }}</span>
          </div>
          <div class="token-popup-row">
            <span class="token-popup-label-wrap">
              <span class="token-dot dot-reasoning"></span>
              <span class="token-popup-label">思考 Token</span>
            </span>
            <span class="token-popup-num">{{ formatTokens(sessionUsage.reasoning_tokens) }}</span>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useContextDuration, useContextRing } from "../composables/useContextUsage.js";

const props = defineProps({
  sessionId: { type: String, default: null },
  sessionUsage: {
    type: Object,
    default: () => ({
      input_tokens: 0,
      output_tokens: 0,
      reasoning_tokens: 0,
      context_tokens: 0,
      context_length: 0,
    }),
  },
  sessionDuration: { type: Number, default: 0 },
  iterationCount: { type: Number, default: 0 },
});

const showTokenPopup = ref(false);
const ringRef = ref(null);
const formattedDuration = useContextDuration(props, showTokenPopup);
const { showTokenRing, contextPercent, contextColor } = useContextRing(props);

const popupStyle = computed(() => {
  if (!ringRef.value) return {};
  const rect = ringRef.value.getBoundingClientRect();
  return {
    position: "fixed",
    bottom: `${window.innerHeight - rect.top + 10}px`,
    right: `${window.innerWidth - rect.right - 8}px`,
  };
});

function toggleTokenPopup() {
  showTokenPopup.value = !showTokenPopup.value;
}

function closeTokenPopup() {
  if (showTokenPopup.value) {
    showTokenPopup.value = false;
  }
}

function formatTokens(n) {
  if (!n) return "0";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return n.toString();
}

onMounted(() => {
  document.addEventListener("click", closeTokenPopup);
});

onUnmounted(() => {
  document.removeEventListener("click", closeTokenPopup);
});
</script>
