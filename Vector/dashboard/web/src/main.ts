import { createApp } from 'vue'

import App from './App.vue'
import { connect } from './lib/store'
import router from './router'
import './styles/base.css'

connect()

createApp(App).use(router).mount('#app')
