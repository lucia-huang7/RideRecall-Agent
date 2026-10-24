<template>
  <main v-if="!identity" class="auth-shell">
    <section class="auth-card">
      <a class="mobile-brand" href="#">LLM-Agent-System</a>
      <p class="eyebrow">YOUR SUPPORT WORKSPACE</p>
      <h1>{{ booting ? 'Connecting securely' : 'Sign in to continue' }}</h1>
      <p v-if="booting">Checking the workspace configuration.</p>
      <form v-else-if="configuration?.auth_mode==='production'" @submit.prevent="signIn">
        <p>Use the access credential provided by your workspace administrator. Your permissions follow your account.</p>
        <label class="field-label" for="accessCredential">Access credential</label>
        <input id="accessCredential" v-model="token" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" required maxlength="256" :disabled="busy" />
        <p class="auth-note">Held in page memory only. Refreshing or signing out clears it.</p>
        <button class="primary" :disabled="busy || !token.trim()">{{ busy ? 'Signing in…' : 'Sign in' }}</button>
      </form>
      <button v-else-if="!booting" class="primary" @click="bootstrap">Retry connection</button>
      <p v-if="error" role="alert" class="auth-error">{{ error }}</p>
    </section>
  </main>
  <RideSupport v-else :identity="identity" @sign-out="signOut" />
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import RideSupport from './RideSupport.vue'
import { runtimeConfig, setCredential, clearCredential, roleHome, allowsRoute, sessionExpiredEvent } from './lib/auth.js'
import { request, resetRequests } from './lib/rideApi.js'

const booting=ref(true), busy=ref(false), error=ref(''), token=ref(''), configuration=ref(null), identity=ref(null)
async function bootstrap() {
  booting.value=true;error.value=''
  try {
    configuration.value=await runtimeConfig()
    if(configuration.value.auth_mode==='demo')identity.value={auth_mode:'demo',model_mode:configuration.value.model_mode || 'demo',subject:null,roles:['rider','specialist','operator']}
  } catch(e) { error.value=e.message }
  finally { booting.value=false }
}
async function signIn() {
  if(busy.value)return
  busy.value=true;error.value='';setCredential(token.value);resetRequests()
  try {
    const account=await request('/api/session')
    if(account.auth_mode!=='production')throw new Error('The workspace authentication mode changed. Reload before continuing.')
    identity.value=account;token.value=''
    const page=window.location.hash.split('/')[1] || 'trips'
    if(!allowsRoute(account.roles,page))window.location.hash=roleHome(account.roles)
  } catch(e) { clearCredential();error.value=e.message }
  finally { busy.value=false }
}
function signOut() { clearCredential();resetRequests();identity.value=null;token.value='';window.location.hash='#/trips' }
function sessionExpired() {
  if (!identity.value) return
  signOut();error.value='Your access expired or was revoked. Sign in again.'
}
onMounted(()=>{window.addEventListener(sessionExpiredEvent,sessionExpired);bootstrap()})
onUnmounted(()=>window.removeEventListener(sessionExpiredEvent,sessionExpired))
</script>
