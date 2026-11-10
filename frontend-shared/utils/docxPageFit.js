// 保留 Word 的纸张尺寸和排版，只缩小超出预览区的页面。
export function fitDocxPages(container) {
  const wrapper = container.querySelector('.docx-preview-content-wrapper')
  if (!wrapper) return () => {}

  const pages = Array.from(wrapper.children)
    .filter(page => page.matches('section.docx-preview-content'))
    .map(page => {
      const frame = document.createElement('div')
      frame.className = 'docx-page-frame'
      page.before(frame)
      frame.appendChild(page)
      return { page, frame }
    })

  const fit = () => {
    const style = getComputedStyle(wrapper)
    const availableWidth = wrapper.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    // 隐藏标签页宽度为 0，重新显示后由 ResizeObserver 再适配。
    if (availableWidth <= 0) return

    for (const { page, frame } of pages) {
      const width = page.offsetWidth
      const height = page.offsetHeight
      if (!width || !height) continue
      const scale = Math.min(1, availableWidth / width)
      page.style.transform = `scale(${scale})`
      frame.style.width = `${width * scale}px`
      frame.style.height = `${height * scale}px`
    }
  }

  let pendingFrame = 0
  const observer = new ResizeObserver(() => {
    cancelAnimationFrame(pendingFrame)
    pendingFrame = requestAnimationFrame(fit)
  })
  observer.observe(container)
  // 图片或字体晚于正文加载时，保持页面高度及分页间距正确。
  for (const { page } of pages) observer.observe(page)
  fit()

  return () => {
    observer.disconnect()
    cancelAnimationFrame(pendingFrame)
  }
}
