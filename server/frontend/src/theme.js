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
        // Aura reads the accent from primary.400, which the gamut-fitted sweep
        // puts on Safety Orange itself - the shade the brand guidelines reserve
        // for dark backgrounds. Nothing to override.
        surface: {
          0: '#ffffff',
          ...semantic('charcoal')
        }
      },
      light: {
        // Safety Orange is a dark-background color; the guidelines name Orange
        // Deep for buttons, links and text on light ones, and forbid the plain
        // accent as text there at all. Reading one rung darker than Aura's
        // default lands on that shade, and is also what carries the accent past
        // WCAG AA against a white label - a rung the accent cannot clear at any
        // saturation, since contrast is set by lightness alone.
        primary: {
          color: '{primary.600}',
          hoverColor: '{primary.700}',
          activeColor: '{primary.800}'
        },
        surface: {
          0: '#ffffff',
          ...semantic('offwhite')
        }
      }
    }
  }
})
