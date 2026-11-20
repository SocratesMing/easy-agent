<template>
  <div
    v-if="showTokenRing"
    ref="ringRef"
    class="context-ring-wrapper"
    @click.stop="toggleTokenPopup"
  >
    <span class="context-ring-inner">
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
    </span>
    <span class="context-ring-text">{{ contextPercent }}%</span>
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
  </div>
</template>

<script>
import contextUsageMixin from "../mixins/contextUsageMixin.js";

export default {
  name: "TokenUsageRing",
  mixins: [contextUsageMixin],
  props: {
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
  },
  data() {
    return {
      showTokenPopup: false,
      liveDuration: 0,
      durationTimer: null,
    };
  },
  watch: {
    shouldCountLive(on) {
      if (on) this.startLiveDuration();
      else this.stopLiveDuration();
    },
  },
  mounted() {
    document.addEventListener("click", this.closeTokenPopup);
  },
  beforeDestroy() {
    document.removeEventListener("click", this.closeTokenPopup);
    this.stopLiveDuration();
  },
  methods: {
    startLiveDuration() {
      this.stopLiveDuration();
      this.liveDuration = 0;
      this.durationTimer = setInterval(() => {
        this.liveDuration += 1;
      }, 1000);
    },
    stopLiveDuration() {
      if (this.durationTimer) {
        clearInterval(this.durationTimer);
        this.durationTimer = null;
      }
    },
    toggleTokenPopup() {
      this.showTokenPopup = !this.showTokenPopup;
    },
    closeTokenPopup() {
      if (this.showTokenPopup) {
        this.showTokenPopup = false;
      }
    },
    formatTokens(n) {
      if (!n) return "0";
      if (n >= 1000) return (n / 1000).toFixed(1) + "k";
      return n.toString();
    },
  },
};
</script>

<style scoped>
.context-ring-wrapper {
  position: relative;
  width: 36px;
  height: 36px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.context-ring-inner {
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.2s;
}

.context-ring-wrapper:hover .context-ring-inner {
  transform: scale(1.1);
}

.context-ring {
  width: 36px;
  height: 36px;
}

.context-ring-bg {
  stroke: var(--border-color);
}

.context-ring-text {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 9px;
  font-weight: 600;
  color: var(--text-secondary);
  line-height: 1;
  pointer-events: none;
}

.token-popup {
  position: fixed;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 14px 16px;
  min-width: 200px;
  z-index: 9999;
  cursor: default;
}

.token-popup-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.token-popup-section {
  margin-bottom: 2px;
}

.token-popup-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
}

.token-popup-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.token-popup-value {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.token-popup-value.input {
  color: #4f46e5;
}

.token-popup-value.output {
  color: #0e7490;
}

.token-popup-value.reasoning {
  color: #7e22ce;
}

.token-popup-value.duration-value {
  color: #4f46e5;
  font-weight: 600;
}

.token-popup-context-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 2px 0 4px;
}

.token-popup-context-value {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.token-popup-context-percent {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.token-popup-divider {
  height: 1px;
  background: var(--bg-tertiary);
  margin: 6px 0;
}

.token-popup-bar {
  margin-top: 4px;
}

.token-popup-bar-inner {
  height: 5px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--bg-tertiary);
}

.token-popup-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease;
}
</style>

<style>
.easy-agent-root[data-theme="dark"] .context-ring-bg {
  stroke: #ffffff !important;
}

.easy-agent-root[data-theme="dark"] .context-ring-wrapper {
  border: 1px solid #ffffff !important;
  border-radius: 50% !important;
  box-sizing: border-box !important;
}
</style>
