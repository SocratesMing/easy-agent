/*
 * 对话内可视化的沙箱渲染器（/agent/viz/frame.js）。
 *
 * 运行在外层页面创建的 sandbox="allow-scripts" iframe 里（不同源：拿不到外层的 DOM、token、
 * localStorage），只通过 postMessage 收发消息：
 *   外层 → 本页：{source: 'ea-visual-host', type: 'render', kind, text, theme}
 *                {source: 'ea-visual-host', type: 'theme', theme}
 *                {source: 'ea-visual-host', type: 'export'}
 *   本页 → 外层：ready{kinds} / size{height} / rendered / error{message} / unsupported{kind}
 *                export{format, dataUrl} / prompt{text} / link{url}
 * kind：echarts（JSON 格式的 ECharts option）、mermaid（Mermaid 源码）、widget（HTML 交互组件）。
 * ECharts / Mermaid 由同目录的 vendor/ 托管，用到时才加载；CSP 禁止一切网络请求。
 */
(function () {
  'use strict'

  var HOST = 'ea-visual-host'
  var FRAME = 'ea-visual-frame'
  var html = document.documentElement
  var ENABLED = html.getAttribute('data-enabled') === '1'
  var WIDGETS = ENABLED && html.getAttribute('data-widgets') === '1'
  var KINDS = ENABLED ? (WIDGETS ? ['echarts', 'mermaid', 'widget'] : ['echarts', 'mermaid']) : []
  var LIBS = { echarts: 'vendor/echarts.min.js', mermaid: 'vendor/mermaid.min.js' }

  var root = document.getElementById('root')
  var state = { kind: '', text: '', theme: 'light', chart: null, svg: '' }
  var loading = {}
  var mermaidSeq = 0

  function post(message) {
    message.source = FRAME
    try {
      window.parent.postMessage(message, '*')
    } catch (e) { /* 外层已卸载 */ }
  }

  function loadScript(src) {
    if (!loading[src]) {
      loading[src] = new Promise(function (resolve, reject) {
        var script = document.createElement('script')
        script.src = src
        script.onload = function () { resolve() }
        script.onerror = function () {
          delete loading[src]
          reject(new Error('图表库加载失败：' + src))
        }
        document.head.appendChild(script)
      })
    }
    return loading[src]
  }

  // ── 高度：内容一变就回报，外层据此设置 iframe 高度（框架内不滚动） ──
  var lastHeight = -1
  var sizeQueued = false
  function postSize() {
    var height = Math.ceil(root.getBoundingClientRect().height)
    if (height !== lastHeight) {
      lastHeight = height
      post({ type: 'size', height: height })
    }
  }
  function reportSize() {
    if (sizeQueued) return
    sizeQueued = true
    requestAnimationFrame(function () {
      sizeQueued = false
      postSize()
    })
  }
  if (typeof ResizeObserver === 'function') new ResizeObserver(reportSize).observe(root)
  window.addEventListener('resize', function () {
    if (state.chart) state.chart.resize()
    reportSize()
  })

  function applyTheme(theme) {
    state.theme = theme === 'dark' ? 'dark' : 'light'
    html.setAttribute('data-theme', state.theme)
  }

  function disposeChart() {
    if (state.chart) {
      try { state.chart.dispose() } catch (e) { /* 已释放 */ }
      state.chart = null
    }
    state.svg = ''
  }

  function showError(err) {
    var message = (err && err.message) || String(err || '渲染失败')
    disposeChart()
    root.innerHTML = ''
    var box = document.createElement('div')
    box.className = 'ea-error'
    box.textContent = message
    root.appendChild(box)
    reportSize()
    post({ type: 'error', message: message })
  }

  // ── ECharts：JSON option，或 {height, option} 包一层指定高度 ──
  function parseChartSpec(text) {
    var spec = JSON.parse(String(text || ''))
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
      throw new Error('图表配置必须是一个 JSON 对象')
    }
    var wrapped = spec.option && typeof spec.option === 'object' && !spec.series
    var option = wrapped ? spec.option : spec
    var height = typeof spec.height === 'number' ? spec.height : 0
    return { option: option, height: height }
  }

  function chartHeight(spec) {
    if (spec.height) return Math.max(160, Math.min(800, spec.height))
    var series = [].concat(spec.option.series || [])
    var pieOnly = series.length > 0 && series.every(function (s) { return s && s.type === 'pie' })
    return pieOnly ? 300 : 340
  }

  function renderEcharts(text) {
    var spec = parseChartSpec(text)
    return loadScript(LIBS.echarts).then(function () {
      root.innerHTML = ''
      var el = document.createElement('div')
      el.className = 'ea-chart'
      el.style.height = chartHeight(spec) + 'px'
      root.appendChild(el)
      var chart = window.echarts.init(el, state.theme === 'dark' ? 'dark' : null)
      var option = Object.assign({ backgroundColor: 'transparent' }, spec.option)
      chart.setOption(option)
      state.chart = chart
    })
  }

  // ── Mermaid：strict 安全级别（不执行图里的点击回调、标签按纯文本处理） ──
  function renderMermaid(text) {
    return loadScript(LIBS.mermaid).then(function () {
      var mermaid = window.mermaid
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: state.theme === 'dark' ? 'dark' : 'default',
        fontFamily: getComputedStyle(document.body).fontFamily,
      })
      mermaidSeq += 1
      var id = 'ea-mermaid-' + mermaidSeq
      return mermaid.render(id, String(text || '')).then(function (result) {
        root.innerHTML = ''
        var box = document.createElement('div')
        box.className = 'ea-mermaid'
        box.innerHTML = result.svg
        root.appendChild(box)
        state.svg = result.svg
      }, function (err) {
        // 语法错误时 mermaid 会在 body 里留下渲染用的临时节点，清掉再报错
        var stray = document.getElementById('d' + id)
        if (stray && stray.parentNode) stray.parentNode.removeChild(stray)
        throw err
      })
    })
  }

  // ── HTML 交互组件：脚本按出现顺序执行；常见图表库的外链改成本地托管的版本 ──
  function localLibrary(src) {
    if (/echarts(\.min)?\.js(\?|#|$)/i.test(src)) return LIBS.echarts
    if (/mermaid(\.min)?\.js(\?|#|$)/i.test(src)) return LIBS.mermaid
    return ''
  }

  function runScript(original, host) {
    return new Promise(function (resolve) {
      var src = original.getAttribute('src')
      if (src) {
        var local = localLibrary(src)
        // 其他外链脚本 CSP 也会拦，直接跳过，免得报一堆加载错误
        if (!local) return resolve()
        loadScript(local).then(resolve, resolve)
        return
      }
      var script = document.createElement('script')
      for (var i = 0; i < original.attributes.length; i++) {
        var attr = original.attributes[i]
        script.setAttribute(attr.name, attr.value)
      }
      script.text = original.textContent || ''
      host.appendChild(script)
      resolve()
    })
  }

  function renderWidget(text) {
    var source = String(text || '')
    var preload = /\becharts\b/.test(source) ? loadScript(LIBS.echarts) : Promise.resolve()
    return preload.then(function () {
      root.innerHTML = ''
      var box = document.createElement('div')
      box.className = 'ea-widget'
      var template = document.createElement('template')
      template.innerHTML = source
      var scripts = Array.prototype.slice.call(template.content.querySelectorAll('script'))
      scripts.forEach(function (s) { s.parentNode.removeChild(s) })
      box.appendChild(template.content)
      root.appendChild(box)
      return scripts.reduce(function (chain, original) {
        return chain.then(function () { return runScript(original, box) })
      }, Promise.resolve())
    })
  }

  // 组件里可用的两个全局函数：把文字交给对话（外层决定填进输入框还是直接发送）、打开外链
  window.sendPrompt = function (text) {
    post({ type: 'prompt', text: String(text == null ? '' : text) })
  }
  window.openLink = function (url) {
    post({ type: 'link', url: String(url || '') })
  }
  document.addEventListener('click', function (event) {
    var target = event.target
    var link = target && target.closest ? target.closest('a[href]') : null
    if (!link) return
    event.preventDefault()
    var href = link.getAttribute('href') || ''
    if (/^https?:\/\//i.test(href)) window.openLink(href)
  })
  window.addEventListener('error', function (event) {
    if (state.kind === 'widget') post({ type: 'widget-error', message: String(event.message || '脚本错误') })
  })

  function render(kind, text) {
    state.kind = kind
    state.text = text
    disposeChart()
    if (KINDS.indexOf(kind) === -1) {
      post({ type: 'unsupported', kind: kind })
      return
    }
    Promise.resolve()
      .then(function () {
        if (kind === 'echarts') return renderEcharts(text)
        if (kind === 'mermaid') return renderMermaid(text)
        return renderWidget(text)
      })
      .then(function () {
        // 先同步报一次高度再报完成：外层收到 rendered 时 iframe 已是内容高度，不会闪一下最小高度
        postSize()
        post({ type: 'rendered', kind: kind })
      }, showError)
  }

  function exportImage() {
    if (state.kind === 'echarts' && state.chart) {
      var background = state.theme === 'dark' ? '#1a1a1a' : '#ffffff'
      post({ type: 'export', format: 'png', dataUrl: state.chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: background }) })
      return
    }
    if (state.kind === 'mermaid' && state.svg) {
      post({ type: 'export', format: 'svg', dataUrl: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(state.svg) })
      return
    }
    post({ type: 'export', format: '', dataUrl: '' })
  }

  window.addEventListener('message', function (event) {
    if (event.source !== window.parent) return
    var data = event.data
    if (!data || data.source !== HOST) return
    if (data.type === 'render') {
      applyTheme(data.theme)
      render(String(data.kind || ''), data.text)
    } else if (data.type === 'theme') {
      var previous = state.theme
      applyTheme(data.theme)
      // 图表、流程图按新主题重画；交互组件只换 CSS 变量，不重跑脚本（保留用户调过的状态）
      if (state.theme !== previous && (state.kind === 'echarts' || state.kind === 'mermaid')) render(state.kind, state.text)
    } else if (data.type === 'export') {
      exportImage()
    }
  })

  post({ type: 'ready', kinds: KINDS })
})()
