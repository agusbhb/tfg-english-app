import { createRouter, createWebHistory } from 'vue-router'
import { auth } from './auth.js'
import { sessionGuard } from './sessionGuard.js'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import WelcomeView from './views/WelcomeView.vue'
import PracticeView from './views/PracticeView.vue'
import TestView from './views/TestView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/practicar',
      name: 'practice',
      component: PracticeView,
      meta: { private: true },
    },
    {
      path: '/tests/:id',
      name: 'test',
      component: TestView,
      meta: { private: true },
    },
    { path: '/', redirect: '/login' },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { guest: true },
      title: 'Iniciar sesión',
    },
    {
      path: '/register',
      name: 'register',
      component: RegisterView,
      meta: { guest: true },
      title: 'Crear cuenta',
    },
    {
      path: '/bienvenida',
      name: 'welcome',
      component: WelcomeView,
      meta: { private: true },
      title: 'Bienvenida',
    },
    { path: '/:pathMatch(.*)*', redirect: '/login' },
  ],
})
router.beforeEach(sessionGuard(auth))
