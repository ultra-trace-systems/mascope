export const num = {
  // 34.2146
  mz: new Intl.NumberFormat('en-US', {
    minimumIntegerDigits: 2,
    minimumFractionDigits: 4,
    maximumFractionDigits: 4
  }),
  mzError: new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }),
  // A correction in ppm, signed: -11.93, +20.40, and 0.00 for one that rounds
  // to nothing either way
  mzCorrection: new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: 'exceptZero'
  }),
  peakIntensity: new Intl.NumberFormat('en-US', {
    notation: 'scientific',
    minimumSignificantDigits: 4,
    maximumSignificantDigits: 4
  }),
  relativeAbundance: new Intl.NumberFormat('en-US', {
    style: 'percent',
    minimumFractionDigits: 3,
    maximumFractionDigits: 3
  }),
  relativeAbundanceError: new Intl.NumberFormat('en-US', {
    style: 'percent',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }),
  ticFraction: new Intl.NumberFormat('en-US', {
    style: 'percent',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })
}
