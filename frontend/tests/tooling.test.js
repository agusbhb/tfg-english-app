// @vitest-environment happy-dom
import { expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import { h } from 'vue'

test('Vue Test Utils conserva el formateo HTML con sus dependencias parcheadas', () => {
  const wrapper = mount({
    render: () =>
      h('section', { class: 'trial' }, [h('p', 'Contenido de prueba')]),
  })
  expect(wrapper.html()).toBe(
    '<section class="trial">\n  <p>Contenido de prueba</p>\n</section>',
  )
  wrapper.unmount()
})
