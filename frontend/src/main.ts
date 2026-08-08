import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useMembersStore } from './stores/members'
import './assets/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 启动时加载成员列表；失败时 backendOffline 由 api.ts 设置，App.vue 会显示连接提示
const members = useMembersStore()
members.load().catch(() => {})

app.mount('#app')
