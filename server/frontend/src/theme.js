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
    ['safetyorange', 'safetyorangedim', 'charcoal', 'offwhite'].map((color) => [
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
        // Aura reads the accent from primary.400, which at full strength is
        // Safety Orange itself - the shade the guidelines reserve for dark
        // backgrounds, and the most saturated orange the display can make at
        // that lightness. Over a whole interface on near-black that is tiring,
        // so dark reads the damped sweep of the same hue instead. It cannot
        // simply read a darker rung: this one token is both the button fill and
        // the accent as text on the ground, and a rung down takes the text to
        // 3.8:1. Damping leaves contrast where it was.
        primary: {
          color: '{safetyorangedim.600}',
          hoverColor: '{safetyorangedim.700}',
          activeColor: '{safetyorangedim.800}'
        },
        // These carry Aura's own mix ratios; only the color they mix changes.
        highlight: {
          background: 'color-mix(in srgb, {safetyorangedim.600}, transparent 84%)',
          focusBackground: 'color-mix(in srgb, {safetyorangedim.600}, transparent 76%)'
        },
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
