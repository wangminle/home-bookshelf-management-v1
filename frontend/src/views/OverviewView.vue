<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useApiStore, coverUrl } from '@/stores/api'
import type { StatsOut, BookOut, BookListOut } from '@/types/models'

const api = useApiStore()
const stats = ref<StatsOut | null>(null)
const books = ref<BookOut[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)  // BUG-125：加载失败时展示错误状态 + 重试
const canvasRef = ref<HTMLCanvasElement | null>(null)
const generating = ref(false)
const imageReady = ref(false)
// 修复 BUG-107：生成 token——丢弃过期异步结果，避免新旧 generate 竞态
let genToken = 0

const CANVAS_W = 1080
const CANVAS_H = 1350
const COVER_COLS = 6
const COVER_ROWS = 4
const COVER_COUNT = COVER_COLS * COVER_ROWS

function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => resolve(null)
    img.src = src
  })
}

/** 读取 CSS 变量（修复 P2-1/P3-4：不硬编码颜色和字体） */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** 根据书名生成确定性色相（读取 --cover-sat/--cover-light 保持一致） */
function coverColor(title: string, sat: string, light: string): string {
  const hash = title.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  return `hsl(${hash % 360}, ${sat}, ${light})`
}

/** 用 Canvas 绘制概览图 */
async function generateImage() {
  // 修复 BUG-107：已生成中时不重复触发（按钮虽 disabled，函数仍可能被 onMounted 路径调用）
  if (generating.value) return
  const canvas = canvasRef.value
  if (!canvas || !stats.value) return
  // 修复 BUG-107：try/finally 保证异常时 generating 复位；token 丢弃过期结果
  const myToken = ++genToken
  generating.value = true
  imageReady.value = false
  try {
    const ctx = canvas.getContext('2d')!
    canvas.width = CANVAS_W
    canvas.height = CANVAS_H

    // 修复 P2-1/P3-4：从 CSS 变量读取颜色和字体，保持与 UI 一致
    const bgColor = cssVar('--bg') || '#f5f5f5'
    const textColor = cssVar('--text') || '#1a202c'
    const mutedColor = cssVar('--text-muted') || '#5a6878'
    const primaryColor = cssVar('--primary') || '#2c7a7b'
    const borderColor = cssVar('--border') || '#e2e8f0'
    const coverSat = cssVar('--cover-sat') || '62%'
    const coverLight = cssVar('--cover-light') || '36%'
    const fontFamily = getComputedStyle(document.body).fontFamily

    // 背景
    ctx.fillStyle = bgColor
    ctx.fillRect(0, 0, CANVAS_W, CANVAS_H)

    // 标题区
    ctx.fillStyle = textColor
    ctx.font = `bold 48px ${fontFamily}`
    ctx.textAlign = 'center'
    ctx.fillText('📚 我的家庭书架', CANVAS_W / 2, 80)

    ctx.font = `24px ${fontFamily}`
    ctx.fillStyle = mutedColor
    const today = new Date().toLocaleDateString('zh-CN')
    ctx.fillText(`截至 ${today}`, CANVAS_W / 2, 115)

    // 封面墙（6×4 网格）
    const margin = 60
    const gap = 12
    const gridW = CANVAS_W - margin * 2
    const coverW = (gridW - gap * (COVER_COLS - 1)) / COVER_COLS
    const coverH = coverW * 4 / 3 // 3:4 书封比例
    const gridH = coverH * COVER_ROWS + gap * (COVER_ROWS - 1)
    const gridTop = 150

    // 修复 P2-2：并行加载封面图片（原为顺序加载，24 张图串行等待）
    const urls = Array.from({ length: COVER_COUNT }, (_, i) => {
      const book = books.value[i]
      return book ? coverUrl(book.cover_path) : null
    })
    const coverImgs = await Promise.all(urls.map(url => url ? loadImage(url) : null))
    // 修复 BUG-107：异步等待后若已被新一次 generate 取代则中止
    if (myToken !== genToken) return

    for (let row = 0; row < COVER_ROWS; row++) {
      for (let col = 0; col < COVER_COLS; col++) {
        const idx = row * COVER_COLS + col
        const book = books.value[idx]
        const x = margin + col * (coverW + gap)
        const y = gridTop + row * (coverH + gap)

        if (book) {
          const img = coverImgs[idx]
          if (img) {
            // 绘制封面图
            ctx.drawImage(img, x, y, coverW, coverH)
          } else {
            // 占位色块 + 书名首字
            ctx.fillStyle = coverColor(book.title, coverSat, coverLight)
            ctx.fillRect(x, y, coverW, coverH)
            ctx.fillStyle = 'white'
            ctx.font = `bold 36px ${fontFamily}`
            ctx.textAlign = 'center'
            ctx.fillText(book.title.charAt(0), x + coverW / 2, y + coverH / 2 + 12)
          }
          // 圆角边框效果（简化为细边框）
          ctx.strokeStyle = borderColor
          ctx.lineWidth = 1
          ctx.strokeRect(x, y, coverW, coverH)
        } else {
          // 空格
          ctx.fillStyle = borderColor
          ctx.fillRect(x, y, coverW, coverH)
        }
      }
    }

    // 统计摘要区（封面墙下方）
    const summaryTop = gridTop + gridH + 40
    const s = stats.value

    ctx.fillStyle = primaryColor
    ctx.fillRect(margin, summaryTop, gridW, 2)

    ctx.textAlign = 'left'
    ctx.font = `bold 28px ${fontFamily}`
    ctx.fillStyle = textColor
    const colW = gridW / 4
    const metrics: [string, string][] = [
      ['藏书', `${s.total_books} 本`],
      ['在读', `${s.by_status.reading || 0} 本`],
      ['已读完', `${s.by_status.finished || 0} 本`],
      ['花费', `¥${s.total_spent.toFixed(0)}`],
    ]
    metrics.forEach(([label, value], i) => {
      const cx = margin + i * colW
      ctx.fillStyle = mutedColor
      ctx.font = `20px ${fontFamily}`
      ctx.fillText(label, cx, summaryTop + 40)
      ctx.fillStyle = primaryColor
      ctx.font = `bold 36px ${fontFamily}`
      ctx.fillText(value, cx, summaryTop + 80)
    })

    // 分类 TOP3
    ctx.fillStyle = textColor
    ctx.font = `bold 24px ${fontFamily}`
    ctx.fillText('分类分布', margin, summaryTop + 140)

    ctx.font = `20px ${fontFamily}`
    ctx.fillStyle = mutedColor
    const topCats = s.by_category.slice(0, 3)
    topCats.forEach((c, i) => {
      const barY = summaryTop + 170 + i * 36
      ctx.fillStyle = mutedColor
      ctx.font = `18px ${fontFamily}`
      ctx.fillText(c.category, margin, barY)
      // 条形
      const maxCount = topCats[0].count
      const barLen = (c.count / maxCount) * 200
      ctx.fillStyle = primaryColor
      ctx.fillRect(margin + 120, barY - 18, barLen, 24)
      ctx.fillStyle = textColor
      ctx.fillText(`${c.count}`, margin + 120 + barLen + 8, barY)
    })

    // 底部水印
    ctx.fillStyle = mutedColor
    ctx.font = `16px ${fontFamily}`
    ctx.textAlign = 'center'
    ctx.fillText('家庭图书管理系统 · 家庭书架', CANVAS_W / 2, CANVAS_H - 30)

    // 修复 BUG-107：仅在仍是最新一次生成时标记完成
    if (myToken === genToken) {
      imageReady.value = true
    }
  } catch {
    // 修复 BUG-107：绘制异常不复位 imageReady（保留上一次结果），仅复位 generating
  } finally {
    if (myToken === genToken) {
      generating.value = false
    }
  }
}

/** 导出图片 */
function downloadImage() {
  const canvas = canvasRef.value
  if (!canvas) return
  canvas.toBlob((blob) => {
    if (!blob) return
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '家庭书架概览图.png'
    a.click()
    URL.revokeObjectURL(url)
  }, 'image/png')
}

/** 分享（Web Share API，不支持时降级为下载） */
async function shareImage() {
  const canvas = canvasRef.value
  if (!canvas) return
  canvas.toBlob(async (blob) => {
    if (!blob) return
    const file = new File([blob], '家庭书架概览图.png', { type: 'image/png' })
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: '我的家庭书架' })
      } catch {
        // 用户取消分享，静默
      }
    } else {
      // 不支持分享，降级下载
      downloadImage()
    }
  }, 'image/png')
}

async function loadData() {
  loading.value = true
  loadError.value = null
  try {
    const [s, b] = await Promise.all([
      api.get<StatsOut>('/stats'),
      api.get<BookListOut>(`/books?limit=${COVER_COUNT}&offset=0`),
    ])
    stats.value = s
    books.value = b.items
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/** BUG-125：重试——重新加载数据，成功后重新生成画布与下载按钮 */
async function retry() {
  await loadData()
  if (loadError.value) return
  // 等 v-else 渲染（canvas ref 注册）后再生成
  await nextTick()
  // BUG-125：部分浏览器 nextTick 后 canvas 仍未就绪，追加 rAF 等待渲染帧
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
  await generateImage()
}

onMounted(async () => {
  await loadData()
  // BUG-125：加载失败时不尝试生成图片
  if (loadError.value) return
  // 修复 BUG-107：等 loading=false 触发的 v-else 渲染（canvas ref 注册）后再生成
  await nextTick()
  // BUG-125：追加 rAF 等待渲染帧，确保 canvas 在所有浏览器中就绪
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
  await generateImage()
})
</script>

<template>
  <div>
    <h1 class="page-title">📷 书架概览图</h1>

    <div v-if="loading" class="loading" role="status" aria-live="polite">加载中...</div>

    <!-- BUG-125：加载失败时展示错误状态 + 重试（成功后重新生成画布） -->
    <div v-else-if="loadError" class="error-state">
      <p class="error-state-msg">{{ loadError }}</p>
      <button class="btn" @click="retry">重试</button>
    </div>

    <div v-else>
      <div class="overview-actions">
        <span class="sr-only" role="status" aria-live="polite">{{ generating ? '正在生成概览图' : (imageReady ? '概览图已生成' : '') }}</span>
        <button class="btn" :disabled="generating" @click="generateImage">
          {{ generating ? '生成中...' : '🔄 重新生成' }}
        </button>
        <button class="btn" :disabled="!imageReady" @click="downloadImage">⬇ 下载图片</button>
        <button class="btn" :disabled="!imageReady" @click="shareImage">📤 分享</button>
      </div>

      <div class="canvas-container">
        <canvas ref="canvasRef" class="overview-canvas" role="img" :aria-label="`家庭书架概览图，共 ${stats?.total_books || 0} 本藏书`"></canvas>
      </div>

      <p v-if="!imageReady && !generating" class="overview-hint">
        点击"重新生成"创建概览图
      </p>
    </div>
  </div>
</template>

<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.page-title {
  margin-bottom: 20px;
  font-family: var(--font-serif);
}

.overview-actions {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.canvas-container {
  background: var(--card-bg);
  border-radius: var(--radius);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-md);
  padding: 16px;
  display: flex;
  justify-content: center;
  overflow-x: auto;
}

.overview-canvas {
  max-width: 100%;
  height: auto;
  border-radius: var(--radius-sm);
}

.overview-hint {
  color: var(--text-muted);
  text-align: center;
  margin-top: 16px;
}

/* BUG-125：加载失败状态 */
.error-state {
  text-align: center;
  padding: 48px 24px;
}
.error-state-msg {
  color: var(--error-text);
  margin-bottom: 16px;
}
</style>
