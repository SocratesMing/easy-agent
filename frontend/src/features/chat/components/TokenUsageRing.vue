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
        <div class="token-popup-section">
          <div class="token-popup-row">
            <span class="token-popup-label">会话耗时</span>
            <span class="token-popup-value duration-value">{{ formattedDuration }}</span>
          </div>
          <div class="token-popup-row">
            <span class="token-popup-label">迭代次数</span>
            <span class="token-popup-value">{{ iterationCount }}</span>
          </div>
        </div>
        <div class="token-popup-divider"></div>
        <div class="token-popup-section">
          <div class="token-popup-row">
            <span class="token-popup-label">
              本轮上下文占用{{ sessionUsage.context_estimated ? "（估算）" : "" }}
            </span>
          </div>
          <div class="token-popup-context-row">
            <span class="token-popup-context-value">
              {{ formatTokens(sessionUsage.context_tokens) }}/{{ formatTokens(sessionUsage.context_length) }}
            </span>
            <span class="token-popup-context-percent" :style="{ color: contextColor }">
              {{ contextPercent }}%
            </span>
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
        <div class="token-popup-divider"></div>
        <div class="token-popup-row">
          <span class="token-popup-label">输入 Token</span>
          <span class="token-popup-value input">{{ formatTokens(sessionUsage.input_tokens) }}</span>
        </div>
        <div class="token-popup-row">
          <span class="token-popup-label">输出 Token</span>
          <span class="token-popup-value output">{{ formatTokens(sessionUsage.output_tokens) }}</span>
        </div>
        <div class="token-popup-row">
          <span class="token-popup-label">思考 Token</span>
          <span class="token-popup-value reasoning">{{ formatTokens(sessionUsage.reasoning_tokens) }}</span>
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
