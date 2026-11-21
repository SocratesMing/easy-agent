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
  border-radius: 14px;
  box-shadow:
    0 12px 32px -4px rgba(15, 23, 42, 0.14),
    0 2px 6px rgba(15, 23, 42, 0.06);
  padding: 14px 16px;
  width: 248px;
  box-sizing: border-box;
  z-index: 9999;
  cursor: default;
}

.token-popup-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.token-popup-kpis {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.token-popup-kpi {
  padding: 8px 10px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--bg-tertiary) 75%, transparent);
}

.token-popup-kpi-label {
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 2px;
}

.token-popup-kpi-value {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
}

.token-popup-context-card {
  margin-top: 10px;
  padding: 9px 10px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--bg-tertiary) 50%, transparent);
  border: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
}

.token-popup-context-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.token-popup-context-badge {
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  padding: 1px 7px;
  border-radius: 999px;
  line-height: 1.4;
}

.token-popup-context-nums {
  margin-top: 4px;
  display: flex;
  align-items: baseline;
  font-variant-numeric: tabular-nums;
}

.token-popup-context-main {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.token-popup-context-total {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-secondary);
  white-space: pre;
}

.token-popup-bar {
  margin-top: 7px;
}

.token-popup-bar-inner {
  height: 6px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--bg-tertiary);
}

.token-popup-bar-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.4s ease;
}

.token-popup-legend {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid color-mix(in srgb, var(--border-color) 75%, transparent);
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.token-popup-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1px 0;
}

.token-popup-label-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.token-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
}

.token-dot.dot-input {
  background: #6366f1;
}

.token-dot.dot-output {
  background: #06b6d4;
}

.token-dot.dot-reasoning {
  background: #a855f7;
}

.token-popup-label {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.token-popup-num {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
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
