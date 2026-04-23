import { createApp } from 'vue';
import { createPinia } from 'pinia';
import PrimeVue from 'primevue/config';
import ToastService from 'primevue/toastservice';
import Aura from '@primeuix/themes/aura';

import App from './App.vue';
import './styles.css';
import 'primeicons/primeicons.css';

const app = createApp(App);

document.documentElement.classList.add('app-dark');

app.use(createPinia());
app.use(PrimeVue, {
  ripple: true,
  inputVariant: 'filled',
  theme: {
    preset: Aura,
    options: {
      prefix: 'p',
      darkModeSelector: '.app-dark',
      cssLayer: false,
    },
  },
});
app.use(ToastService);

app.mount('#app');
