<script>
const TYPE_CLASS = {
  pdf: 'pdf',
  doc: 'word',
  docx: 'word',
  xls: 'excel',
  xlsx: 'excel',
  csv: 'excel',
  ppt: 'powerpoint',
  pptx: 'powerpoint',
  jpg: 'image',
  jpeg: 'image',
  png: 'image',
  gif: 'image',
  svg: 'image',
  webp: 'image',
  bmp: 'image',
  zip: 'archive',
  rar: 'archive',
  '7z': 'archive',
  tar: 'archive',
  gz: 'archive',
}

export default {
  name: 'FileIcon',
  props: {
    filename: {
      type: String,
      required: true,
    },
    size: {
      type: [Number, String],
      default: 48,
    },
  },
  computed: {
    extension() {
      const name = String(this.filename || '')
      const dot = name.lastIndexOf('.')
      return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
    },
    typeClass() {
      return `file-icon--${TYPE_CLASS[this.extension] || 'default'}`
    },
    label() {
      if (!this.extension) return 'FILE'
      return this.extension.slice(0, 4).toUpperCase()
    },
    iconSize() {
      return typeof this.size === 'number' ? `${this.size}px` : this.size
    },
  },
}
</script>

<template>
  <span
    class="file-icon"
    :class="typeClass"
    :style="{ width: iconSize, height: iconSize, fontSize: iconSize }"
    aria-hidden="true"
  >
    <svg viewBox="0 0 32 36" focusable="false">
      <path class="file-icon__paper" d="M5.5 1.5h13.2l7.8 7.8v24.2a1 1 0 0 1-1 1h-20a1 1 0 0 1-1-1v-31a1 1 0 0 1 1-1Z" />
      <path class="file-icon__fold" d="M18.5 1.8v7.7h7.7" />
    </svg>
    <span class="file-icon__label">{{ label }}</span>
  </span>
</template>

<style scoped>
.file-icon {
  --file-color: #64748b;
  position: relative;
  display: inline-block;
  flex: none;
  min-width: 0;
  line-height: 0;
  overflow: hidden;
  vertical-align: middle;
}

.file-icon svg {
  display: block;
  width: 100%;
  height: 100%;
}

.file-icon__paper {
  fill: #fff;
  stroke: #cbd5e1;
  stroke-width: 1.5;
}

.file-icon__fold {
  fill: #e8eef5;
  stroke: #cbd5e1;
  stroke-linejoin: round;
  stroke-width: 1.5;
}

.file-icon__label {
  position: absolute;
  left: 8%;
  right: 8%;
  bottom: 17%;
  min-height: 20%;
  padding: 1px 0;
  border-radius: 2px;
  background: var(--file-color);
  color: #fff;
  font-size: clamp(5px, .24em, 10px);
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: -.02em;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
}

.file-icon--pdf { --file-color: #e5484d; }
.file-icon--word { --file-color: #3978d6; }
.file-icon--excel { --file-color: #27966a; }
.file-icon--powerpoint { --file-color: #dd6b3d; }
.file-icon--image { --file-color: #8b5cf6; }
.file-icon--archive { --file-color: #d39b22; }
</style>
