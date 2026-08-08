import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { MemberOut } from '@/types/models'
import { useApiStore } from './api'

const STORAGE_KEY = 'bookshelf_selected_member_id'

export const useMembersStore = defineStore('members', () => {
  const members = ref<MemberOut[]>([])
  const selectedId = ref<number | null>(null)
  const api = useApiStore()

  const selectedMember = computed(() =>
    members.value.find((m) => m.id === selectedId.value) || null,
  )

  // 修复 BUG：in-flight 去重，避免 main.ts 和 App.vue 同时触发两次请求
  let _loadPromise: Promise<void> | null = null

  async function load() {
    if (_loadPromise) return _loadPromise
    _loadPromise = (async () => {
      try {
        const data = await api.get<{ items: MemberOut[]; total: number }>('/members')
        members.value = data.items
        // 恢复 localStorage 中的选择，或默认选第一个
        const saved = localStorage.getItem(STORAGE_KEY)
        const savedId = saved ? parseInt(saved, 10) : null
        if (savedId && members.value.some((m) => m.id === savedId)) {
          selectedId.value = savedId
        } else if (members.value.length > 0) {
          selectedId.value = members.value[0].id
        }
      } finally {
        _loadPromise = null
      }
    })()
    return _loadPromise
  }

  function select(id: number) {
    selectedId.value = id
    localStorage.setItem(STORAGE_KEY, String(id))
  }

  return { members, selectedId, selectedMember, load, select }
})
