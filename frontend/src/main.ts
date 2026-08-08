import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useMembersStore } from './stores/members'
import './assets/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 启动时加载成员列表
const members = useMembersStore()
members.load().catch(() => {
  // 后端未启动时静默处理，页面内会显示连接提示
})

app.mount('#app')
