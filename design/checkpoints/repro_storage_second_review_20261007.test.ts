/** 历史诊断：通过表示两条残口存在；使用 mock 网络，不访问真实数据库。 */

import {it,expect,vi,beforeEach} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import {createPinia,setActivePinia} from 'pinia'
import Provision from '@/components/CopyProvisionForm.vue'
import StorageView from '@/views/StorageView.vue'
import {sessionRole} from '@/stores/session'
import {useMembersStore} from '@/stores/members'
import {lastError} from '@/stores/api'
import {resetProvisionIdempotencyKeys} from '@/stores/storage'
const TS='2026-10-07T00:00:00Z'
beforeEach(()=>{sessionRole.value='owner';lastError.value='';resetProvisionIdempotencyKeys()})
it('残口探针：HTTP201回执JSON丢失后，同载荷重试换新键并重复补录',async()=>{
const accepted=new Set<string>();let created=0;const payloads:any[]=[];
vi.stubGlobal('fetch',vi.fn(async(_u,o)=>{const p=JSON.parse(String(o.body));payloads.push(p);if(!accepted.has(p.idempotency_key)){accepted.add(p.idempotency_key);created+=p.items[0].new_copies.count}return {ok:true,status:201,json:async()=>{throw new SyntaxError('JSON truncated')}}}));
const pinia=createPinia();setActivePinia(pinia);useMembersStore().members=[{id:3,name:'爸爸',role:'member',avatar_path:null,reading_streak_offset:0,created_at:TS,updated_at:TS}];
const w=mount(Provision,{props:{bookId:7},global:{plugins:[pinia],stubs:{PlacementTargetPicker:{template:'<button type="button" data-target @click="$emit(\'update:target\', {shelf_id:11,cell_id:null})">target</button>'}}}});
await w.get('[data-field="owner"]').setValue(3);await w.get('[data-target]').trigger('click');await w.get('form').trigger('submit');await flushPromises();await w.get('form').trigger('submit');await flushPromises();
expect(payloads).toHaveLength(2);expect(payloads[0].items).toEqual(payloads[1].items);expect(payloads[0].idempotency_key).not.toBe(payloads[1].idempotency_key);expect(created).toBe(2);console.log('201 JSON-loss: equal payload, distinct keys, modeled created=',created,w.text())
});
it('残口探针：409后GET刷新失败，仍显示已刷新且重试提交旧版本',async()=>{
let gets=0;const patches:any[]=[];vi.stubGlobal('fetch',vi.fn(async(u,o)=>{
const url=String(u);if(o?.method==='PATCH'){patches.push(JSON.parse(String(o.body)));return {ok:false,status:409,json:async()=>({detail:{code:'PLACEMENT_CHANGED',message:'冲突'}})}}
if(url.includes('/storage/rooms')){gets++;if(gets>1)throw new TypeError('refresh disconnected');return {ok:true,status:200,json:async()=>({ok:true,data:{items:[{id:1,code:'R',name:'房间',description:null,sort_order:0,version:1,archived_at:null,created_at:TS,updated_at:TS,stats:{}}],total:1}})}}
if(url.includes('/storage/shelves?'))return {ok:true,status:200,json:async()=>({ok:true,data:{items:[],total:0}})};throw Error(url)}));
const w=mount(StorageView,{global:{stubs:{RouterLink:{props:['to'],template:'<a><slot/></a>'}}}});await flushPromises();await w.get('[data-room="1"] button').trigger('click');await w.get('[data-field="name"]').setValue('草稿');await w.get('.storage-form').trigger('submit');await flushPromises();
expect(lastError.value).toContain('已为你刷新最新数据');expect((w.get('[data-field="name"]').element as HTMLInputElement).disabled).toBe(false);await w.get('.storage-form').trigger('submit');await flushPromises();expect(patches.map(p=>p.version)).toEqual([1,1]);console.log('409 refresh failure: latest message=',lastError.value,'patch versions=',patches.map(p=>p.version));
});
