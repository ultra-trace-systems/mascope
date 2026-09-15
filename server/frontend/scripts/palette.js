import * as fs from 'fs'
import convert from 'chroma-js'
import * as prettier from 'prettier'

// PARAMETERS

// output path
const jsonPath = './src/palette.json'

// input colors, from the Ultra Trace brand guidelines
const colors = {
  // primaries
  safetyorange: '#FF6700',
  safetyorangedim: '#FF6700',
  // surfaces
  charcoal: '#161718',
  offwhite: '#F5F5F2'
}

// Colors swept against the sRGB gamut rather than against their own chroma: a
// shade takes the most color the screen can hold at its lightness, scaled by
// the strength given here. Full strength is the brand guidelines' own
// construction - the four oranges they name (Tint, Safety Orange, Deep, Shadow)
// each sit exactly on the gamut boundary at their lightness, so a ramp built
// that way passes through the same family.
//
// The accent is swept twice. Light surfaces read the full-strength ramp. Dark
// surfaces read a damped one, because against near-black the accent is
// otherwise the most saturated orange the display can produce - carrying tabs,
// buttons, focus rings and every selected row at once, which is tiring to read
// against for long. Damping costs nothing measurable: lightness alone sets
// contrast, so a damped shade measures exactly what its full-strength twin
// does against the same background.
//
// Colors left out keep the seed's chroma the whole way up, which is what the
// near-neutral surfaces want.
const gamutFitted = {
  safetyorange: 1,
  safetyorangedim: 0.7
}

// shades
const shades = [
  { lightness: 95, shade: null },
  { lightness: 90, shade: null },
  { lightness: 80, shade: null },
  { lightness: 70, shade: null },
  { lightness: 60, shade: null },
  { lightness: 50, shade: null },
  { lightness: 40, shade: null },
  { lightness: 30, shade: null },
  { lightness: 20, shade: null },
  { lightness: 10, shade: null },
  { lightness: 5, shade: null }
]

// GENERATION PROCEDURE

const records = []

// iterate through the main colors
for (const [color, hexcode] of Object.entries(colors)) {
  const [l, chroma, hue] = convert(hexcode).lch()
  const mainLightness = getClosestShade(l).lightness
  if (Math.abs(l - mainLightness) > 1) {
    console.warn(
      `lightness of the main ${color} shade was significantly modified from ${l} to ${mainLightness}`
    )
  }
  // iterate through the shades
  for (const { lightness, shade } of shades) {
    // create the lch triplet
    const shadeChroma =
      color in gamutFitted ? gamutChroma(lightness, hue) * gamutFitted[color] : chroma
    const lch = [lightness, shadeChroma, hue]
    // compute color systems
    const rgb = convert
      .lch(...lch)
      .rgb()
      .join(', ')
    const hex = convert.lch(...lch).hex()
    // construct color record
    records.push({
      color,
      shade,
      hex,
      rgb,
      hue,
      chroma: shadeChroma,
      lightness
    })
  }
}

// OUTPUT

const json = JSON.stringify(records, null, 2)
prettier
  .format(json, { parser: 'json' })
  .then((formattedJson) =>
    fs.writeFile(jsonPath, formattedJson, (err) => err && console.error(err))
  )

// helpers

// The largest chroma that still fits inside sRGB at this lightness and hue,
// found by bisection. Sweeping a seed's own chroma across every shade only
// holds up near the seed's own lightness - further out the color falls outside
// the gamut and the conversion to rgb clips a channel, taking the lightness and
// the hue with it. That is why the light accent shades used to arrive as a
// vivid peach rather than a wash, and the dark ones as maroon rather than
// orange. Asking the gamut first keeps every shade on the lightness and hue it
// was given.
function gamutChroma(lightness, hue) {
  let low = 0
  let high = 150
  while (high - low > 0.01) {
    const middle = (low + high) / 2
    if (convert.lch(lightness, middle, hue).clipped()) high = middle
    else low = middle
  }
  return low
}

function getClosestShade(l) {
  return shades.reduce((prev, curr) =>
    Math.abs(curr.lightness - l) < Math.abs(prev.lightness - l) ? curr : prev
  )
}
