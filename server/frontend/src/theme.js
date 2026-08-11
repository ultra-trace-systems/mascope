import Aura from '@primevue/themes/aura'
import { definePreset } from '@primevue/themes'

import palette from '@/palette.json'

const semantic = (color) =>
  Object.fromEntries(
    [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95].map((lightness) => [
      Number(`${100 - lightness}0`),
      `{${color}.${lightness}0}`
    ])
  )

export default definePreset(Aura, {
  primitive: Object.fromEntries(
    ['safetyorange', 'charcoal', 'offwhite'].map((color) => [
      color,
      Object.fromEntries(
        palette
          .filter((swatch) => swatch.color == color)
          .map(({ lightness, hex }) => [`${lightness}0`, hex])
      )
    ])
  ),
  semantic: {
    primary: semantic('safetyorange'),
    colorScheme: {
      dark: {
        surface: {
          0: '#ffffff',
          ...semantic('charcoal')
        }
      },
      light: {
        surface: {
          0: '#ffffff',
          ...semantic('offwhite')
        }
      }
    }
  }
})
