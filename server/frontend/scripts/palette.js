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
  // surfaces
  charcoal: '#161718',
  offwhite: '#F5F5F2'
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
    const lch = [lightness, chroma, hue]
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
      chroma,
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

function getClosestShade(l) {
  return shades.reduce((prev, curr) =>
    Math.abs(curr.lightness - l) < Math.abs(prev.lightness - l) ? curr : prev
  )
}
