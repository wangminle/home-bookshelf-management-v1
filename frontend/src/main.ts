import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { probeSession } from './stores/session'
import './assets/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// CHK-071：成员列表改为会话确认后再加载（App.vue 内）——匿名 /shared 不再触发
// 受保护的 /members 请求（401 会污染全局错误横幅）。
probeSession().catch(() => {})

app.mount('#app')
